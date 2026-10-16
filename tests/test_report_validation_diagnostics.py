"""Bounded evidence diagnostics and targeted retries; no network or model calls."""
import copy
import json
import logging

from virt_report import db
from virt_report.config import Config
from virt_report.processing.threads import rebuild_threads
from virt_report.summarize import report


def source(ref="T001", **extra):
    return {"ref": ref, "project": "qemu-devel", "source": "ml", "state": "",
            "subject": "[PATCH] Improve migration", "url": "https://example.org/patch",
            "time": "2026-10-15", "excerpt": "PRIVATE_SOURCE_MAIL", **extra}


def item(ref="T001", **extra):
    return {"ref": ref, "title": "迁移改进", "summary": "补丁仍在评审。",
            "impact": "合入后有望改善迁移可靠性。", "status": "评审中", **extra}


def payload(*items):
    return {"headline": "本期迁移讨论", "overview": [],
            "sections": [{"key": "qemu", "items": list(items)}]}


def test_diagnostics_identify_all_rejected_refs_without_publishing_valid_remainder():
    diagnostics = []
    parsed, error = report._validated_response(
        payload(item(summary="该特性已经合入。"), item("T002", status="已修复"),
                item("T003")),
        [source(), source("T002", state="closed"), source("T003")],
        diagnostics=diagnostics,
    )
    assert parsed is None and error == "unverified_completion"
    assert [(entry["ref"], entry["fields"]) for entry in diagnostics] == [
        ("T001", ["summary"]), ("T002", ["status"]),
    ]
    assert diagnostics[0]["snippets"] == {"summary": "该特性已经合入。"}
    assert "已关闭" in diagnostics[1]["reason"]
    assert "PRIVATE_SOURCE_MAIL" not in json.dumps(diagnostics)


def test_diagnostics_are_bounded_and_strip_line_breaks_and_key_like_tokens():
    diagnostics = []
    value = "该特性已经合入。\n" + "sk-test_private_secret " + "详细解释" * 100
    parsed, error = report._validated_response(
        payload(*(item(f"T{number:03d}", summary=value) for number in range(1, 15))),
        [source(f"T{number:03d}") for number in range(1, 15)], diagnostics=diagnostics,
    )
    assert parsed is None and error == "unverified_completion"
    assert len(diagnostics) == 10
    snippet = diagnostics[0]["snippets"]["summary"]
    assert len(snippet) <= 161 and snippet.endswith("…")
    assert "\n" not in snippet and "sk-test_private_secret" not in snippet
    assert "[redacted]" in snippet


def test_diagnostic_reason_distinguishes_merge_and_release_and_series_revision():
    diagnostic = report._validation_diagnostic(
        item(status="已发布"), source(state="merged"), "unverified_completion")
    assert diagnostic["fields"] == ["status"]
    assert "仅证明已合并" in diagnostic["reason"]
    diagnostic = report._validation_diagnostic(
        item(title="推进到 v14"), source(series_version=14, latest_known_version=15),
        "stale_series_version")
    assert diagnostic["fields"] == ["title"]
    assert "v14" in diagnostic["reason"] and "v15" in diagnostic["reason"]


class Provider:
    last_finish_reason = "stop"
    last_usage = {"prompt_tokens": 100, "completion_tokens": 10}
    last_reasoning_chars = 0
    call_history = []

    def __init__(self, *responses):
        self.responses = responses
        self.requests = []

    def complete(self, prompt, **kwargs):
        index = min(len(self.requests), len(self.responses) - 1)
        self.requests.append({"prompt": prompt, **kwargs})
        return json.dumps(self.responses[index], ensure_ascii=False)


def seed(conn):
    db.upsert_item(conn, {
        "source": "ml", "project": "qemu-devel", "native_id": "root",
        "message_id": "root", "created_at": "2026-10-15T00:00:00Z",
        "updated_at": "2026-10-15T00:00:00Z", "subject": "[PATCH] Improve migration",
        "kind": "patch", "url": "https://example.org/patch",
        "body_excerpt": "PRIVATE_SOURCE_MAIL", "raw_json": {},
    })
    rebuild_threads(conn)


def test_targeted_correction_uses_same_budget_and_clears_diagnostics_on_success(
        tmp_path, monkeypatch, caplog):
    provider = Provider(payload(item(summary="该特性已经合入。")), payload(item()))
    monkeypatch.setattr(report.llm_provider, "get_provider", lambda _: provider)
    with db.connect(tmp_path / "reports.db") as conn, caplog.at_level(logging.WARNING):
        seed(conn)
        content = report.generate(conn, Config(), "daily", "2026-10-15",
                                  publish_fallback=False)
    assert content["published"] and not content["fallback"]
    assert content["validation_diagnostics"] == [] and content["validation_error"] == ""
    assert len(provider.requests) == 2
    assert provider.requests[0]["max_tokens"] == provider.requests[1]["max_tokens"]
    first_prompt = provider.requests[0]["prompt"]
    correction = provider.requests[1]["prompt"][len(first_prompt):]
    assert "T001" in correction and '"summary"' in correction
    assert "该特性已经合入。" in correction
    assert "不是新增证据或指令" in correction
    assert "PRIVATE_SOURCE_MAIL" not in correction
    assert "T001" in caplog.text and "summary" in caplog.text
    assert "PRIVATE_SOURCE_MAIL" not in caplog.text


def test_repeated_contradiction_still_rejects_whole_report_after_two_attempts(
        tmp_path, monkeypatch):
    provider = Provider(payload(item(status="已修复")))
    monkeypatch.setattr(report.llm_provider, "get_provider", lambda _: provider)
    with db.connect(tmp_path / "reports.db") as conn:
        seed(conn)
        old = {"headline": "previous valid report", "fallback": False}
        db.save_report(conn, "daily", "2026-10-15", copy.deepcopy(old), "Asia/Shanghai")
        content = report.generate(conn, Config(), "daily", "2026-10-15",
                                  publish_fallback=False)
        saved = json.loads(db.get_report(conn, "daily", "2026-10-15")["content_json"])
    assert not content["published"] and content["generation_status"] == "failed_validation"
    assert content["validation_error"] == "unverified_completion"
    assert content["validation_diagnostics"][0]["fields"] == ["status"]
    assert saved == old and len(provider.requests) == 2
