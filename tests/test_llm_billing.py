"""Model migration and cost snapshots; all HTTP/LLM responses are mocked."""
import copy
import json
import runpy
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests

from virt_report import db, metrics
from virt_report.config import Config, LLMConfig, Storage, load_config
from virt_report.render import render
from virt_report.summarize import billing, llm_provider, report

USAGE = {"prompt_tokens": 1000, "prompt_cache_hit_tokens": 200,
         "prompt_cache_miss_tokens": 800, "completion_tokens": 500}


@pytest.fixture(autouse=True)
def fixed_request_clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 10, 16, 15, tzinfo=timezone.utc).astimezone(tz)
    monkeypatch.setattr(llm_provider, "datetime", Clock)


def call(at="2026-09-10T00:15:00+08:00", model="deepseek-flash"):
    return {"requested_model": model, "response_model": "deepseek-v4.1-flash",
            "started_at": at, "usage": dict(USAGE), "status": "success"}


@pytest.mark.parametrize("at,tier", [
    ("2026-09-10T00:15:00+08:00", "off_peak"),
    ("2026-09-10T08:59:59+08:00", "off_peak"),
    ("2026-09-10T09:00:00+08:00", "peak"),
    ("2026-09-10T11:59:59+08:00", "peak"),
    ("2026-09-10T12:00:00+08:00", "off_peak"),
    ("2026-09-10T14:00:00+08:00", "peak"),
    ("2026-09-10T18:00:00+08:00", "off_peak"),
    ("2026-09-12T10:00:00+08:00", "off_peak"),
    ("2026-09-10T01:00:00Z", "peak"),
])
def test_request_time_selects_peak_tariff(at, tier):
    result = billing.snapshot_call(call(at), LLMConfig())
    assert result["pricing_tier"] == tier
    assert result["estimated_cost_cny"] == pytest.approx(0.002804 * (2 if tier == "peak" else 1))


@pytest.mark.parametrize("model", sorted(billing.FLASH_ALIASES))
def test_legacy_alias_new_requests_use_flash_schedule(model):
    result = billing.snapshot_call(call(model=model), LLMConfig())
    assert result["rates"]["output"] == 4
    assert result["requested_model"] == model
    assert result["response_model"] == "deepseek-v4.1-flash"
    old = billing.snapshot_call(call("2026-09-09T23:59:59+08:00", model), LLMConfig())
    assert old["pricing_tier"] == "legacy"
    if model == "deepseek-v4-flash":
        assert old["rates"]["output"] == 2


def test_unknown_model_missing_usage_and_invalid_time_are_not_free():
    config = LLMConfig()
    assert billing.snapshot_call(call(model="unknown"), config)["estimated_cost_cny"] is None
    assert billing.snapshot_call({**call(), "usage": {}}, config)["estimated_cost_cny"] is None
    assert billing.snapshot_call({**call(), "usage": {"total_tokens": 1500}}, config)["estimated_cost_cny"] is None
    for at in ("invalid", "2026-09-10T10:00:00"):
        assert billing.snapshot_call(call(at), config)["estimated_cost_cny"] is None
    assert billing.estimate(USAGE, {"output": 4})["estimated_cost_cny"] is None
    assert billing.estimate(USAGE, {"cache_hit": 0, "cache_miss": 0, "output": float("nan")})["estimated_cost_cny"] is None


def test_explicit_zero_cache_miss_is_not_overwritten():
    totals = billing.usage_totals({"prompt_tokens": 100, "prompt_cache_miss_tokens": 0})
    assert totals["cache_miss_tokens"] == 0


def test_both_config_defaults_and_yaml_select_new_model_and_same_tariffs():
    config = load_config()
    defaults = LLMConfig()
    assert {config.llm.daily_model, config.llm.weekly_model, config.llm.monthly_model} == {"deepseek-flash"}
    assert config.llm.pricing_schedule_cny == defaults.pricing_schedule_cny
    assert config.llm.pricing_cny == defaults.pricing_cny


def test_stored_costs_survive_price_changes_and_sum_cross_boundary_retries(tmp_path):
    config = Config()
    calls = [billing.snapshot_call(call(at), config.llm) for at in (
        "2026-09-10T08:59:59+08:00", "2026-09-10T09:00:01+08:00")]
    calls.append(billing.snapshot_call({**call(), "usage": {}}, config.llm))
    frozen = copy.deepcopy(calls)
    with db.connect(tmp_path / "reports.db") as conn:
        content = {"llm_calls": calls, "llm_requested_model": "deepseek-flash",
                   "llm_response_models": ["deepseek-v4.1-flash"]}
        db.save_report(conn, "daily", "2026-09-09", content, "Asia/Shanghai", model="deepseek-flash")
        config.llm.pricing_schedule_cny["deepseek-flash"]["peak"]["output"] = 999
        values = metrics.build_metrics(conn, config)
        result = values["reports"][0]
        assert result["estimated_cost_cny"] == pytest.approx(0.008412)
        assert result["unpriced_calls"] == 1
        assert result["pricing_basis"] == "snapshot"
        assert values["models"]["deepseek-flash"]["calls"] == 3
        assert values["models"]["deepseek-flash"]["total_tokens"] == 3000
        assert calls == frozen
        page = render.render_metrics_html(config, values)
        assert "部分未计价" in page and "另有 1 次请求未计价" in page
        assert "接口返回：deepseek-v4.1-flash" in page


def test_legacy_report_estimate_and_json_are_unchanged(tmp_path):
    with db.connect(tmp_path / "legacy.db") as conn:
        db.save_report(conn, "daily", "2026-09-08", {"llm_usage": USAGE},
                       "Asia/Shanghai", model="deepseek-v4-flash")
        before = dict(db.get_report(conn, "daily", "2026-09-08"))
        result = metrics.build_metrics(conn, Config())["reports"][0]
        assert result["estimated_cost_cny"] == pytest.approx(0.001804)
        assert result["pricing_basis"] == "legacy"
        assert dict(db.get_report(conn, "daily", "2026-09-08")) == before


def response(content='{"overview": []}', model="deepseek-v4.1-flash", finish="stop"):
    value = requests.Response()
    value.status_code = 200
    value._content = json.dumps({"id": "response-test", "model": model, "usage": USAGE,
                                 "choices": [{"finish_reason": finish, "message": {
                                     "content": content, "reasoning_content": "private-reasoning",
                                 }}]}).encode()
    return value


def test_provider_records_models_and_usage_without_prompts_secrets_or_reasoning(monkeypatch):
    bodies = []

    def post(url, **kwargs):
        bodies.append(kwargs["json"])
        assert url == "https://api.deepseek.com/chat/completions"
        return response()

    monkeypatch.setattr(llm_provider.requests, "post", post)
    provider = llm_provider.OpenAICompatibleProvider("https://api.deepseek.com", "secret", "deepseek-flash")
    provider.complete("private-evidence json", thinking="enabled", reasoning_effort="high", json_mode=True)
    assert bodies[0]["model"] == "deepseek-flash"
    assert bodies[0]["thinking"] == {"type": "enabled"}
    assert "temperature" not in bodies[0]
    record = provider.call_history[0]
    assert record["response_model"] == "deepseek-v4.1-flash"
    assert record["usage"] == USAGE
    assert record["started_at"] <= record["finished_at"]
    assert not any(text in json.dumps(record) for text in ("secret", "private-evidence", "private-reasoning"))


def test_provider_retries_and_clears_stale_usage(monkeypatch):
    replies = iter([response(), requests.Timeout("timeout"), response(model=None)])

    def post(*args, **kwargs):
        reply = next(replies)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(llm_provider.requests, "post", post)
    monkeypatch.setattr(llm_provider.time, "sleep", lambda _: None)
    provider = llm_provider.OpenAICompatibleProvider("https://api.deepseek.com", "secret", "deepseek-flash")
    provider.complete("json")
    with pytest.raises(requests.Timeout):
        provider.complete("json", retries=1)
    assert provider.last_usage == {} and provider.last_finish_reason is None
    provider.complete("json")
    assert len(provider.call_history) == 3
    assert provider.call_history[1]["usage"] == {}
    assert provider.call_history[2]["response_model"] is None


def test_http_retry_records_each_attempt_and_unknown_usage(monkeypatch):
    retry = requests.Response()
    retry.status_code = 429
    replies = iter([retry, response()])
    monkeypatch.setattr(llm_provider.requests, "post", lambda *args, **kwargs: next(replies))
    monkeypatch.setattr(llm_provider.time, "sleep", lambda _: None)
    provider = llm_provider.OpenAICompatibleProvider("https://api.deepseek.com", "secret", "deepseek-flash")
    provider.complete("json")
    snapshots = [billing.snapshot_call(c, LLMConfig()) for c in provider.call_history]
    assert snapshots[0]["http_status"] == 429 and snapshots[0]["estimated_cost_cny"] is None
    assert snapshots[1]["status"] == "success"
    assert billing.summarize_calls(snapshots)["unpriced_calls"] == 1


def test_unknown_model_is_displayed_as_unpriced(tmp_path):
    with db.connect(tmp_path / "unknown.db") as conn:
        db.save_report(conn, "daily", "2026-09-09", {"llm_usage": USAGE, "llm_attempts": 2},
                       "Asia/Shanghai", model="unknown")
        values = metrics.build_metrics(conn, Config())
        assert values["models"]["unknown"]["unpriced_calls"] == 2
        assert values["reports"][0]["estimated_cost_cny"] is None
        assert "未计价" in render.render_metrics_html(Config(), values)


def test_live_check_harness_isolates_original_reports_with_mocked_api(tmp_path, monkeypatch):
    from virt_report import config as config_module

    config = Config(storage=Storage(db_path=tmp_path / "source.db"))
    monkeypatch.setenv(config.llm.api_key_env, "dummy-test-only")
    monkeypatch.setattr(config_module, "load_config", lambda _: config)
    evidence = {"ref": "T001", "project": "qemu", "subject": "migration fix",
                "time": "2026-09-09", "url": "https://example.com/evidence", "source": "mailing_list",
                "kind": "patch", "msg_count": 2, "participants": 2}
    monkeypatch.setattr(report, "_build_threads_data", lambda *args: [evidence])
    payload = {"overview": [{"project": "QEMU", "summary": "迁移改进仍在讨论中。"}],
               "sections": [{"key": "qemu", "items": [{"ref": "T001", "summary": "迁移改进。"}]}]}
    monkeypatch.setattr(llm_provider.requests, "post", lambda *args, **kwargs: response(json.dumps(payload)))
    with closing(db.connect(config.db_path)) as conn:
        for period, key in (("daily", "2026-09-09"), ("weekly", "2026-W36"), ("monthly", "2026-08")):
            db.save_report(conn, period, key, {"headline": "original", "fallback": False},
                           "Asia/Shanghai", model="deepseek-v4-flash")
    monkeypatch.setattr(sys, "argv", ["check_model_reports.py", "--live"])
    runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/check_model_reports.py"), run_name="__main__")
    output = next((tmp_path / "model-checks").iterdir())
    summary = json.loads((output / "summary.json").read_text())
    assert summary["source_reports_unchanged"] is True
    assert {result["period"] for result in summary["results"]} == {"daily", "weekly"}
    assert all(result["valid"] for result in summary["results"])
    assert (output / "daily/2026-09-09.html").is_file()
    assert not (output / "monthly").exists()


@pytest.mark.parametrize("period,key", [("daily", "2026-09-09"), ("weekly", "2026-W36"), ("monthly", "2026-08")])
def test_report_saves_usage_and_tariff_for_truncated_and_successful_response(tmp_path, monkeypatch, period, key):
    config = Config()
    evidence = {"ref": "T001", "project": "qemu", "subject": "migration fix",
                "time": "2026-09-09", "url": "https://example.com/evidence", "source": "mailing_list",
                "kind": "patch", "msg_count": 2, "participants": 2,
                "excerpt": "migration fix under review"}
    monkeypatch.setattr(report, "_build_threads_data", lambda *args: [evidence])
    data = {"overview": [{"project": "QEMU", "summary": "迁移改进仍在讨论中。"}],
            "sections": [{"key": "qemu", "items": [{"ref": "T001", "summary": "修正迁移状态处理。"}]}]}
    replies = iter([response('{"overview":', finish="length"), response(json.dumps(data))])
    monkeypatch.setattr(llm_provider.requests, "post", lambda *args, **kwargs: next(replies))
    provider = llm_provider.OpenAICompatibleProvider("https://api.deepseek.com", "secret", "deepseek-flash")
    monkeypatch.setattr(llm_provider, "get_provider", lambda _: provider)
    with db.connect(tmp_path / "reports.db") as conn:
        result = report.generate(conn, config, period, key, publish_fallback=False)
        assert not result["fallback"]
        assert len(result["llm_calls"]) == 2
        assert result["llm_response_models"] == ["deepseek-v4.1-flash"]
        assert result["llm_usage"]["completion_tokens"] == 1000
        assert all(c["rates"]["output"] in (4, 8) for c in result["llm_calls"])
        assert result["sections"][0]["items"][0]["url"] == evidence["url"]
        stored = json.loads(db.get_report(conn, period, key)["content_json"])
        assert stored["llm_calls"] == result["llm_calls"]
