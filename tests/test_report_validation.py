"""Publication and evidence guards; isolated SQLite and fake model responses only."""
import copy
import json
from datetime import datetime, timezone

import pytest

from virt_report import coverage, db, server
from virt_report.config import Config, MailingListSource, Sources
from virt_report.processing.threads import rebuild_threads
from virt_report.summarize import evidence, prompts, report


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "validation.db")
    yield connection
    connection.close()


def mail(conn, key, at="2026-10-11T00:00:00Z", *, subject="[PATCH v14 0/2] migration/cpr: vsock",
         parent=None, body="A proposed change", author="A <a@example.org>"):
    db.upsert_item(conn, {"source": "ml", "project": "qemu-devel", "native_id": key,
                         "message_id": key, "in_reply_to": parent, "thread_root": parent,
                         "created_at": at, "updated_at": at, "author": author,
                         "subject": subject, "kind": "patch" if not parent else "discussion",
                         "url": f"https://example.org/{key}", "body_excerpt": body, "raw_json": {}})
    rebuild_threads(conn)


def payload(ref="T001", **fields):
    return {"headline": "本期迁移讨论", "overview": [], "sections": [{"key": "qemu", "items": [
        {"ref": ref, "title": "迁移评审", "summary": "本期继续讨论设备迁移。",
         "impact": "合入后有望改善可靠性。", "status": "评审中", **fields},
    ]}]}


class Provider:
    last_finish_reason = "stop"
    last_usage = {"prompt_tokens": 100, "completion_tokens": 10}
    last_reasoning_chars = 0
    call_history = []

    def __init__(self, *replies):
        self.replies = replies
        self.requests = []

    def complete(self, text, **kwargs):
        index = min(len(self.requests), len(self.replies) - 1)
        self.requests.append({"text": text, **kwargs})
        return json.dumps(self.replies[index])


def generate(conn, monkeypatch, provider, config=None, **options):
    monkeypatch.setattr(report.llm_provider, "get_provider", lambda _: provider)
    return report.generate(conn, config or Config(), "daily", "2026-10-11", **options)


@pytest.mark.parametrize("publish_fallback", [False, True])
def test_invalid_refs_cannot_publish_or_overwrite_good_report(conn, monkeypatch, publish_fallback):
    mail(conn, "root")
    old = {"fallback": False, "headline": "old valid report"}
    db.save_report(conn, "daily", "2026-10-11", old, "Asia/Shanghai", model="test")
    provider = Provider(payload("T999"))
    result = generate(conn, monkeypatch, provider, publish_fallback=publish_fallback)
    assert result["generation_status"] == "failed_validation"
    assert not result["published"]
    assert result["validation_error"] == "no_valid_evidence_items"
    assert json.loads(db.get_report(conn, "daily", "2026-10-11")["content_json"]) == old
    assert len(provider.requests) == 2
    assert result["llm_usage"]["prompt_tokens"] == 200


def test_one_correction_retry_keeps_budget_and_publishes_valid_items(conn, monkeypatch):
    mail(conn, "root")
    provider = Provider(payload("T999"), payload())
    result = generate(conn, monkeypatch, provider, publish_fallback=False)
    assert result["published"] and not result["fallback"]
    assert result["generation_status"] == "ready"
    assert result["validation_error"] == ""
    assert provider.requests[0]["max_tokens"] == provider.requests[1]["max_tokens"]
    assert "no_valid_evidence_items" in provider.requests[1]["text"]


@pytest.mark.parametrize("bad", [
    {"sections": "not a list"}, {"sections": [], "headline": {}},
    payload(ref=["T001"]), payload(summary={"text": "bad type"}), payload(summary=""),
])
def test_invalid_structure_does_not_become_success(conn, monkeypatch, bad):
    mail(conn, "root")
    result = generate(conn, monkeypatch, Provider(bad), publish_fallback=False)
    assert not result["published"] and result["generation_status"] == "failed_validation"
    assert db.get_report(conn, "daily", "2026-10-11") is None


def test_mixed_invalid_and_duplicate_refs_keep_the_valid_content(conn, monkeypatch):
    mail(conn, "root")
    value = payload()
    value["sections"][0]["items"] += [copy.deepcopy(value["sections"][0]["items"][0]),
                                     payload("T999")["sections"][0]["items"][0]]
    result = generate(conn, monkeypatch, Provider(value), publish_fallback=False)
    assert result["published"]
    assert sum(len(section["items"]) for section in result["sections"]) == 1
    assert result["evidence_validation"]["discarded_item_count"] == 1


@pytest.mark.parametrize("item,source,error", [
    ({"summary": "该特性已经合入。"}, {}, "unverified_completion"),
    ({"impact": "用户可直接使用。"}, {"state": "opened"}, "unconditional_availability"),
    ({"impact": "合入后用户可直接使用。"}, {}, None),
    ({"status": "已合并"}, {"state": "merged"}, None),
    ({"status": "已修复"}, {"state": "closed"}, "unverified_completion"),
    ({"summary": "已正式发布。"}, {"state": "merged"}, "unverified_completion"),
    ({"impact": "用户可直接使用。"}, {"state": "merged"}, "unconditional_availability"),
    ({"summary": "关闭不代表已合并，也不能确认已修复。"}, {"state": "closed"}, None),
    ({"title": "推进到 v14"}, {"series_version": 14, "latest_known_version": 15}, "stale_series_version"),
    ({"summary": "v14 有新评审，最新为 v15。"}, {"series_version": 14, "latest_known_version": 15}, None),
    ({"summary": "作者已发布 v15，本期继续评审 v14。"}, {}, None),
    ({"summary": "补丁修复已合入代码中的回归，当前仍在评审。"}, {}, None),
    ({"summary": "该问题影响已发布版本，修复尚未合入。"}, {}, None),
    ({"summary": "最新讨论围绕 v14，期末已知最新系列为 v15。"},
     {"series_version": 14, "latest_known_version": 15}, None),
    ({"summary": "用户不能直接使用，也不可以立即部署。"}, {}, None),
    ({"summary": "最新版本为 v14。"}, {"series_version": 14, "latest_known_version": 15},
     "stale_series_version"),
])
def test_evidence_consistency_examples(item, source, error):
    assert evidence.consistency_error(item, source) == error


def test_period_end_controls_versions_and_review_context(conn):
    mail(conn, "old", "2026-10-07T00:00:00Z")
    mail(conn, "v15", "2026-10-08T00:00:00Z", subject="[PATCH v15 0/2] migration/cpr: vsock")
    mail(conn, "reply", parent="old", subject="Re: [PATCH v14 0/2] migration/cpr: vsock")
    start, end = "2026-10-10T16:00:00Z", "2026-10-11T16:00:00Z"
    before = report._build_threads_data(conn, db.get_top_threads(conn, start, end), 600, None, end)
    assert before[0]["series_version"] == 14
    assert before[0]["latest_known_version"] == 15
    assert before[0]["latest_version_url"] == "https://example.org/v15"
    mail(conn, "future", "2026-10-11T16:02:00Z", parent="old", body="Reviewed-by: Future")
    mail(conn, "v16", "2026-10-12T00:00:00Z", subject="[PATCH v16 0/2] migration/cpr: vsock")
    after = report._build_threads_data(conn, db.get_top_threads(conn, start, end), 600, None, end)
    assert after == before
    assert "active_version=v14 latest_known_version=v15" in prompts.build_prompt("daily", "2026-10-11", after)
    assert "Future" not in json.dumps(after)


def test_no_activity_is_explicit_and_requires_complete_coverage(conn, monkeypatch):
    monkeypatch.setattr(report.llm_provider, "get_provider", lambda _: None)
    config = Config(sources=Sources(mailing_lists=[MailingListSource(
        name="qemu-devel", type="mbox", url="https://example.org")]))
    unknown = report.generate(conn, config, "daily", "2026-10-11", publish_fallback=False)
    assert unknown["generation_status"] == "data_unavailable" and not unknown["published"]
    coverage.start_batch(conn, "complete", datetime(2026, 10, 10, tzinfo=timezone.utc),
                         datetime(2026, 10, 12, tzinfo=timezone.utc))
    coverage.record_scan(conn, key="complete", source="ml", project="qemu-devel",
                         start="2026-10-10T00:00:00Z", end="2026-10-12T00:00:00Z",
                         started_at="2026-10-12T00:00:00Z", status="complete", page_limit=8)
    result = report.generate(conn, config, "daily", "2026-10-11", publish_fallback=False)
    assert result["generation_status"] == "no_activity" and result["published"]
    assert not result["fallback"] and result["model"] == "none" and result["llm_attempts"] == 0


def test_fallback_never_overwrites_existing_good_report(conn, monkeypatch):
    old = {"headline": "keep me", "fallback": False}
    db.save_report(conn, "daily", "2026-10-11", old, "Asia/Shanghai")
    result = generate(conn, monkeypatch, None, publish_fallback=True)
    assert result["fallback"] and not result["published"]
    assert json.loads(db.get_report(conn, "daily", "2026-10-11")["content_json"]) == old


def test_gitlab_opened_status_uses_trusted_canonical_label():
    source = {"ref": "T001", "project": "libvirt", "subject": "Fix issue",
              "state": "opened", "url": "https://example.org/issue", "time": "2026-10-11"}
    _, sections = report._sanitize([], payload(status="评审中")["sections"], [source])
    assert next(section for section in sections if section["items"])["items"][0]["status"] == "开放"


def test_coverage_wait_is_not_presented_as_ai_running(conn):
    db.set_report_generation_state(
        conn, "daily", "2026-10-11", status="retry_wait", attempt=1,
        error="coverage_wait",
    )
    state = server._generation_state_view(
        db.get_report_generation_state(conn, "daily", "2026-10-11")
    )
    assert state["title"] == "等待数据补齐"
    assert "来源数据尚未齐全" in state["description"]


def test_review_timestamps_keep_older_ack_separate_from_current_activity(conn):
    mail(conn, "root", "2026-10-05T00:00:00Z")
    mail(conn, "ack", "2026-10-07T00:00:00Z", parent="root", body="Acked-by: Reviewer")
    mail(conn, "today", parent="root", body="Discussing an unresolved detail")
    start, end = "2026-10-10T16:00:00Z", "2026-10-11T16:00:00Z"
    cards = report._build_threads_data(conn, db.get_top_threads(conn, start, end), 600, None, end)
    assert cards[0]["review_at"] == "2026-10-07T00:00:00Z"
    assert cards[0]["latest_at"] == "2026-10-11T00:00:00Z"
    assert "review=2026-10-07T00:00:00Z" in prompts.build_prompt("daily", "2026-10-11", cards)


def test_stats_count_known_creation_without_using_future_gitlab_state(conn):
    db.upsert_item(conn, {
        "source": "gitlab", "project": "qemu", "native_id": "issue:1",
        "thread_root": "issue:1", "subject": "An issue", "kind": "issue",
        "created_at": "2026-10-11T00:00:00Z", "updated_at": "2026-10-12T00:00:00Z",
        "url": "https://example.org/issue/1", "raw_json": {"state": "closed"},
    })
    stats = report._stats(conn, "2026-10-10T16:00:00Z", "2026-10-11T16:00:00Z")
    assert stats["gl_issues_opened"] == 1
    assert stats["total_items"] == stats["total_threads"] == 1


def test_stats_active_merged_mrs_are_not_limited_to_new_merges(conn):
    db.upsert_item(conn, {
        "source": "gitlab", "project": "libvirt", "native_id": "mr:1",
        "thread_root": "mr:1", "subject": "Merged change follow-up", "kind": "mr",
        "created_at": "2026-10-01T00:00:00Z", "updated_at": "2026-10-11T00:00:00Z",
        "url": "https://example.org/mr/1",
        "raw_json": {"state": "merged", "merged": True, "merged_at": "2026-10-02T00:00:00Z"},
    })
    stats = report._stats(conn, "2026-10-10T16:00:00Z", "2026-10-11T16:00:00Z")
    assert stats["gl_mrs_merged"] == 1  # UI labels this as 活跃已合并 MR.
