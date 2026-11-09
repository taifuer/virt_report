"""Protocol collection stays intact while daily selection has a narrower scope."""
from copy import deepcopy
import json
import re

import pytest

from virt_report import coverage, db
from virt_report.config import Config, MailingListSource, Sources
from virt_report.processing.threads import rebuild_threads
from virt_report.render import render
from virt_report.report_projects import VIRTIO_SOURCES
from virt_report.summarize import periods, prompts, report


class Provider:
    last_finish_reason = "stop"
    last_usage = {}
    call_history = []

    def __init__(self, *, omit_protocol=False):
        self.requests = []
        self.omit_protocol = omit_protocol

    def complete(self, text, **kwargs):
        self.requests.append((text, kwargs))
        items = [{"ref": ref, "title": f"{project} 队列评审", "summary": "讨论队列访问语义。",
                  "impact": "需核对兼容性。", "status": "评审中"}
                 for ref, project in re.findall(r"\[(T\d+)\] project=(\S+)", text)
                 if not (self.omit_protocol and project in VIRTIO_SOURCES)]
        # Deliberately put everything in QEMU and return an unwanted overview;
        # publication must still use trusted source attribution and UI policy.
        return json.dumps({"headline": "队列机制评审", "overview": [
            {"project": "Virtio", "summary": "规范评审有进展。"}],
            "sections": [{"key": "qemu", "name": "QEMU", "items": items}]})


def insert_mail(conn, project, key=None):
    key = key or project
    at = "2026-11-05T06:00:00Z"
    db.upsert_item(conn, {
        "source": "ml", "project": project, "native_id": key, "message_id": key,
        "in_reply_to": None, "thread_root": key, "author": "Editor",
        "subject": f"[RFC] {project} virtio queue semantics", "kind": "rfc",
        "created_at": at, "updated_at": at, "url": f"https://example.test/{key}",
        "body_excerpt": "This proposal discusses shared queue access and compatibility.",
        "raw_json": {},
    })


def configuration():
    return Config(sources=Sources(mailing_lists=[
        MailingListSource(name, "lore_git", "https://example.test/", required=True)
        for name in ("qemu-devel", "kvm", "vhost-user", *VIRTIO_SOURCES)
    ]))


@pytest.mark.parametrize("period,key", [("daily", "2026-11-05"), ("weekly", "2026-W45"),
                                      ("monthly", "2026-11")])
def test_period_policy_scopes_evidence_stats_and_coverage_without_changing_collection(
        tmp_path, monkeypatch, period, key):
    cfg, provider = configuration(), Provider()
    before = deepcopy(cfg)
    monkeypatch.setattr(report.llm_provider, "get_provider", lambda _: provider)
    with db.connect(tmp_path / "period.db") as conn:
        for source in cfg.sources.mailing_lists:
            insert_mail(conn, source.name)
        rebuild_threads(conn)
        result = report.generate(conn, cfg, period, key, publish_fallback=False)
        expected = {source.name for source in cfg.sources.mailing_lists}
        if period == "daily":
            expected -= set(VIRTIO_SOURCES)
        assert result["published"] and result["generation_status"] == "ready"
        assert {card["project"] for card in result["top_threads"]} == expected
        assert set(result["stats"]["by_project"]) == expected
        assert result["stats"]["total_threads"] == result["stats"]["total_items"] == len(expected)
        assert {source["project"] for source in result["data_coverage"]["sources"]} == expected
        assert not any(row["project"] == "Virtio" for row in result["overview"])
        assert ("virtio" in {s["key"] for s in result["sections"]}) == (period != "daily")
        assert {"qemu", "kvm"} <= {s["key"] for s in result["sections"] if s["items"]}
        assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 5
        text, kwargs = provider.requests[0]
        for source in VIRTIO_SOURCES:
            assert (f"project={source}" in text) == (period != "daily")
        assert '"project": "Virtio"' not in kwargs["system"]
    assert cfg == before


def test_protocol_only_activity_does_not_block_a_verified_empty_daily(tmp_path, monkeypatch):
    cfg = Config(sources=Sources(mailing_lists=[
        MailingListSource(name, "lore_git", "https://example.test/", required=True)
        for name in ("qemu-devel", *VIRTIO_SOURCES)
    ]))
    provider = Provider()
    monkeypatch.setattr(report.llm_provider, "get_provider", lambda _: provider)
    with db.connect(tmp_path / "idle.db") as conn:
        for source in VIRTIO_SOURCES:
            insert_mail(conn, source)
        rebuild_threads(conn)
        start, end = periods.window("daily", "2026-11-05", cfg.timezone)
        start, end = coverage.utc_iso(start), coverage.utc_iso(end)
        coverage.ensure_schema(conn)
        coverage.record_scan(conn, key="qemu", source="ml", project="qemu-devel",
                             start=start, end=end, started_at=end, status="complete", page_limit=1)
        # Maintenance still reports protocol coverage gaps; daily eligibility does not.
        assert coverage.report_coverage(conn, cfg, start, end)["wait_recommended"]
        result = report.generate(conn, cfg, "daily", "2026-11-05", publish_fallback=False)
        assert result["published"] and result["generation_status"] == "no_activity"
        assert result["model"] == "none" and not provider.requests
        assert result["stats"]["total_items"] == 0
        assert result["data_coverage"]["status"] == "complete"
        assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 2


@pytest.mark.parametrize("period,key", [("weekly", "2026-W45"), ("monthly", "2026-11")])
def test_protocol_candidates_do_not_force_an_empty_or_low_value_section(
        tmp_path, monkeypatch, period, key):
    provider = Provider(omit_protocol=True)
    monkeypatch.setattr(report.llm_provider, "get_provider", lambda _: provider)
    with db.connect(tmp_path / "selection.db") as conn:
        for name in ("qemu-devel", "virtio-comment"):
            insert_mail(conn, name)
        rebuild_threads(conn)
        result = report.generate(conn, configuration(), period, key, publish_fallback=False)
        assert result["published"]
        assert any(card["project"] == "virtio-comment" for card in result["top_threads"])
        assert not any(section["key"] == "virtio" for section in result["sections"])
        html = render.render_report_html(configuration(), result)
        assert 'id="virtio"' not in html and 'href="#virtio"' not in html
        assert "本期未观察到显著动态" not in html


def test_prompt_keeps_period_evidence_and_asks_for_consolidated_protocol_progress():
    cards = [{"project": "virtio-comment", "ref": "T001"}]
    daily = prompts.build_prompt("daily", "2026-11-05", cards)
    assert "候选证据：0 张" in daily and "[T001]" not in daily
    monthly = prompts.system_prompt("monthly", "2026-11", cards)
    assert '"overview": []' in monthly
    assert "没有入选条目就省略整个 Virtio section" in monthly
    assert "月报合并同一提案的跨周往返及版本迭代" in monthly
    assert "依据本周期原始证据整理" in monthly


@pytest.mark.parametrize("period", ["weekly", "monthly"])
def test_protocol_only_fallback_has_body_without_a_false_empty_overview(period):
    cards = [{"project": "virtio-comment", "ref": "T001", "subject": "Queue proposal",
              "source": "ml", "time": "2026-11-05", "url": "https://example.test/queue"}]
    overview, sections = report._fallback(cards, period, "example", None)
    assert overview == []
    assert [section["key"] for section in sections] == ["virtio"]
    assert sections[0]["items"][0]["url"] == cards[0]["url"]


def test_about_consolidates_actual_sources_without_a_protocol_directory():
    html = render.render_about_html(Config())
    sources = html.split('id="data-sources"', 1)[1].split('</section>', 1)[0]
    for name in (*VIRTIO_SOURCES, "vhost-user"):
        assert html.count(f'href="https://lore.kernel.org/{name}/"') == 1
        assert f'href="https://lore.kernel.org/{name}/"' in sources
    assert sources.count("周报 / 月报") == 2
    assert "协议与后端补充来源" not in html
    assert "DPDK/SPDK 目前不接入" not in html
    assert 'href="versions.html#virtio-specifications"' not in html
