"""virt-report 基础逻辑测试 (不调 LLM/网络)。覆盖周期窗口、线程折叠、URL/时间解析、sanitize、分类。"""
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from virt_report import db, maintenance
from virt_report.collectors import base, hyperkitty, mbox
from virt_report.config import Config, MailingListSource, Sources
from virt_report.processing import architecture, category, classify, threads, topics
from virt_report.render import render as html_render
from virt_report import server as web_server
from virt_report import scheduler
from virt_report.summarize import periods, report


# ---------- periods ----------
def test_daily_window():
    s, e = periods.window("daily", "2026-07-12", "Asia/Shanghai")
    # 2026-07-12 00:00 CST = 2026-07-11 16:00 UTC; 次日 00:00 CST = 2026-07-12 16:00 UTC
    assert s.strftime("%Y-%m-%d %H:%M") == "2026-07-11 16:00"
    assert e.strftime("%Y-%m-%d %H:%M") == "2026-07-12 16:00"


def test_weekly_window():
    s, e = periods.window("weekly", "2026-W27", "Asia/Shanghai")
    assert s.strftime("%Y-%m-%d") == "2026-06-28"  # W27 周一 = 6/29 CST = 6/28 16:00 UTC
    assert (e - s).days == 7


def test_monthly_window():
    s, e = periods.window("monthly", "2026-06", "Asia/Shanghai")
    assert s.strftime("%Y-%m-%d") == "2026-05-31"  # 6/1 00:00 CST = 5/31 16:00 UTC
    assert e.strftime("%Y-%m-%d") == "2026-06-30"  # 7/1 00:00 CST = 6/30 16:00 UTC


def test_period_key_for():
    d = datetime(2026, 7, 12, 15, 0, tzinfo=timezone.utc)
    assert periods.period_key_for("daily", d) == "2026-07-12"
    assert periods.period_key_for("weekly", d) == "2026-W28"
    assert periods.period_key_for("monthly", d) == "2026-07"


def test_label():
    assert periods.label("daily", "2026-07-12") == "2026-07-12"
    assert "第 28 周" in periods.label("weekly", "2026-W28")
    assert "7 月" in periods.label("monthly", "2026-07")


# ---------- base: 时间解析/规范化 ----------
def test_parse_dt_iso():
    assert base.parse_dt("2026-07-12T04:27:40Z").strftime("%Y-%m-%d %H:%M") == "2026-07-12 04:27"
    assert base.parse_dt(None) is None


def test_parse_dt_rfc822():
    # mail-archive RSS 用 RFC822; 修复前会返回 None -> 误标今天
    dt = base.parse_dt("Sun, 12 Jul 2026 07:42:05 GMT")
    assert dt is not None
    assert dt.strftime("%Y-%m-%d") == "2026-07-12"


def test_norm_utc_iso():
    assert base.norm_utc_iso("2026-07-12T10:30:00.123Z") == "2026-07-12T10:30:00Z"
    assert base.norm_utc_iso("2026-07-12T10:30:00Z") == "2026-07-12T10:30:00Z"
    assert base.norm_utc_iso(None) is None


# ---------- mbox: _strip_mid / URL ----------
def test_strip_mid_normal():
    assert mbox._strip_mid("<abc@example.com>") == "abc@example.com"


def test_strip_mid_empty():
    # 畸形 Message-ID 不应崩 (曾 IndexError)
    assert mbox._strip_mid("<>") is None
    assert mbox._strip_mid("   ") is None
    assert mbox._strip_mid(None) is None


def test_mbox_download_appends_verified_range(tmp_path, monkeypatch):
    dest = tmp_path / "month.mbox"
    original = b"old-mail\n" * 20
    addition = b"new-mail\n"
    dest.write_bytes(original)

    class Response:
        status_code = 206
        content = original + addition
        headers = {"Content-Range": f"bytes 0-{len(content)-1}/{len(content)}"}

    monkeypatch.setattr(base, "http_get", lambda *_args, **_kwargs: Response())
    assert mbox._download("https://example.test", "2026-07", dest) == (True, True)
    assert dest.read_bytes() == original + addition


def test_mbox_download_falls_back_when_prefix_changed(tmp_path, monkeypatch):
    dest = tmp_path / "month.mbox"
    dest.write_bytes(b"old-cache")

    class RangeResponse:
        status_code = 206
        content = b"different"
        headers = {"Content-Range": "bytes 0-8/9"}

    class FullResponse:
        status_code = 200
        content = b"replacement"
        headers = {}

    responses = iter((RangeResponse(), FullResponse()))
    monkeypatch.setattr(base, "http_get", lambda *_args, **_kwargs: next(responses))
    assert mbox._download("https://example.test", "2026-07", dest) == (True, True)
    assert dest.read_bytes() == b"replacement"


# ---------- classify ----------
def test_classify_subject():
    assert base.classify_subject("[PATCH 0/2] foo") == "patch"
    assert base.classify_subject("[Stable-10.0.12 1/75] x") == "patch"
    assert base.classify_subject("[RFC v2] bar") == "rfc"
    assert base.classify_subject("Re: question") == "discussion"


def test_extract_topic():
    assert classify.extract_topic("[PATCH v3 5/9] hw/audio/virtio-sound: fix") == "hw/audio"
    assert classify.extract_topic("[Stable-10.0.12 38/75] hw/nvme: foo") == "hw/nvme"
    assert classify.extract_topic("Re: [PATCH] target/arm: x") == "target/arm"


def test_detect_architectures_from_subsystem_and_feature_names():
    assert architecture.detect_architectures(["[PATCH] target/i386: TDX fix"]) == ["x86"]
    assert architecture.detect_architectures(["KVM: arm64: update GICv4"]) == ["ARM"]
    assert architecture.detect_architectures(["target/riscv: vector update"]) == ["RISC-V"]
    assert architecture.detect_architectures(["target/hexagon: update HVX"]) == ["Hexagon"]
    assert architecture.detect_architectures(["loongarch64 user-only"]) == ["LoongArch"]
    assert architecture.detect_architectures(["harmless refactor"]) == []


def test_focus_architecture_priority():
    assert architecture.focus_priority(["ARM"]) == 0
    assert architecture.focus_priority(["x86", "RISC-V"]) == 0
    assert architecture.focus_priority(["RISC-V"]) == 1


def test_change_category_is_conservative():
    assert category.classify_change("security", "anything") == "bug"
    assert category.classify_change("patch", "[PATCH] Fix buffer overflow") == "bug"
    assert category.classify_change("patch", "[PATCH] Add arm64 support") == "feature"
    assert category.classify_change("rfc", "[RFC] Discuss migration ABI") == "other"
    assert category.category_label("feature") == "功能"


def test_operation_topic_classification_can_overlap():
    item = {"title": "Improve live migration performance with zero-copy"}
    assert topics.classify_item(item) == ["migration", "performance"]
    assert topics.classify_item({"title": "KVM: support memory hotplug"}) == ["hotplug"]
    assert topics.classify_item({"title": "QEMU runtime live update"}) == ["live-upgrade"]


# ---------- report: _sanitize (防 LLM 输出缺字段) ----------
def test_sanitize_missing_items_key():
    # section 缺 items 键: 旧版会崩 (dict.items 方法); 现应补 []
    ov, secs = report._sanitize(
        [{"project": "QEMU", "summary": "x"}],
        [{"key": "features", "name": "新功能"},  # 无 items
         {"key": "bugfixes", "name": "Bug", "items": [{"title": "t", "url": "u"}]}])
    assert secs[0]["items"] == []
    assert secs[1]["items"][0]["title"] == "t"
    assert secs[1]["items"][0]["tag"] == ""


def test_sanitize_non_list_items():
    ov, secs = report._sanitize([], [{"key": "q", "name": "Q", "items": "not a list"}])
    assert secs[0]["items"] == []


def test_sanitize_overview():
    ov, _ = report._sanitize([{"project": "QEMU", "summary": "s"}, "bad", {}], [])
    assert len(ov) == 3  # 固定补齐 QEMU / Libvirt / KVM
    assert [o["project"] for o in ov] == ["QEMU", "Libvirt", "KVM"]


def test_sanitize_evidence_ref_controls_url_project_and_state():
    evidence = [{
        "ref": "T001", "project": "qemu", "subject": "Original",
        "url": "https://trusted.example/1", "time": "2026-07-12",
        "state": "closed", "topic": "security",
    }]
    _, secs = report._sanitize([], [{
        "key": "kvm", "name": "KVM", "items": [{
            "ref": "T001", "title": "Edited", "url": "https://fake.example/",
            "impact": "存在风险，需尽快修复", "status": "评审中",
        }],
    }], evidence)
    item = secs[0]["items"][0]  # qemu bucket, regardless of model section
    assert item["url"] == "https://trusted.example/1"
    assert item["status"] == "已关闭"
    assert "关闭结论" in item["impact"]
    assert secs[2]["items"] == []


def test_sanitize_uses_evidence_architecture_and_prioritizes_focus():
    evidence = [
        {"ref": "T001", "project": "kvm", "subject": "generic", "url": "u1",
         "time": "2026-07-12", "state": "", "topic": "", "architectures": []},
        {"ref": "T002", "project": "kvm", "subject": "x86", "url": "u2",
         "time": "2026-07-12", "state": "", "topic": "", "architectures": ["x86"]},
    ]
    _, sections = report._sanitize([], [{"items": [
        {"ref": "T001", "title": "generic"}, {"ref": "T002", "title": "x86"},
    ]}], evidence)
    assert [item["ref"] for item in sections[2]["items"]] == ["T002", "T001"]
    assert sections[2]["items"][0]["architectures"] == ["x86"]


def test_limit_sections_keeps_project_coverage_and_focus_architectures():
    sections = [
        {"key": "qemu", "items": [
            {"ref": "T001", "architectures": []},
            {"ref": "T009", "architectures": ["x86"]},
        ]},
        {"key": "libvirt", "items": [{"ref": "T002", "architectures": []}]},
        {"key": "kvm", "items": [
            {"ref": "T003", "architectures": []},
            {"ref": "T010", "architectures": ["ARM"]},
        ]},
    ]
    limited = report._limit_sections(sections, 4)
    refs = {item["ref"] for section in limited for item in section["items"]}
    assert {"T001", "T002", "T003"}.issubset(refs)
    assert refs.intersection({"T009", "T010"})
    assert sum(len(section["items"]) for section in limited) == 4
    assert report.ITEM_LIMIT["daily"] == 30


def test_enrich_architectures_upgrades_old_report_content():
    content = {
        "period": "daily",
        "top_threads": [{"ref": "T001", "subject": "KVM: arm64: GIC fix"}],
        "sections": [{"items": [{"ref": "T001", "architectures": []}]}],
    }
    enriched = report.enrich_architectures(content)
    assert enriched["top_threads"][0]["architectures"] == ["ARM"]
    assert enriched["sections"][0]["items"][0]["architectures"] == ["ARM"]


def test_about_page_and_architecture_badge_render():
    about = html_render.render_about_html(Config())
    assert "KVM Forum" in about
    content = {
        "period": "daily", "period_key": "2026-07-12", "label": "2026-07-12",
        "headline": "", "fallback": False, "model": "test", "timezone": "Asia/Shanghai",
        "window": {"start": "2026-07-11T16:00:00Z", "end": "2026-07-12T16:00:00Z"},
        "stats": {"total_threads": 1, "total_items": 1, "ml_patches": 1,
                  "ml_rfc": 0, "gl_issues_opened": 0, "gl_mrs_merged": 0,
                  "by_project": {"kvm": 1}},
        "overview": [{"project": "KVM", "summary": "x86 update"}], "watchlist": [],
        "sections": [{"key": "kvm", "name": "KVM", "items": [{
            "title": "x86 update", "url": "https://example.com", "architectures": ["x86"],
            "tag": "KVM", "status": "评审中", "source": "KVM 邮件列表", "time": "2026-07-12",
            "summary": "summary", "impact": "impact", "original_title": "",
        }]}],
    }
    page = html_render.render_report_html(Config(), content)
    assert 'class="dyn focus-arch"' in page
    assert 'class="tag arch">x86' in page
    assert '<div class="d-actions"><span class="tag kind-other">' in page
    assert '<div class="d-meta"><span class="tag arch">x86' in page
    assert "↗" not in page
    assert 'name="color-scheme" content="light"' in page
    assert "theme-toggle" not in page
    assert "prefers-color-scheme:dark" not in page


def test_index_context_limits_recent_daily_reports(tmp_db):
    for day in range(1, 21):
        key = f"2026-07-{day:02d}"
        db.save_report(tmp_db, "daily", key, {"period": "daily", "period_key": key},
                       "Asia/Shanghai", item_count=1, model="test")
    context = web_server._index_context(tmp_db)
    assert len(context["daily"]) == 14
    assert context["daily"][0]["period_key"] == "2026-07-20"
    assert [cal["month_key"] for cal in context["calendars"]] == ["2026-07"]


def test_readiness_reports_missing_and_fresh_sources(tmp_db):
    config = Config(sources=Sources(mailing_lists=[
        MailingListSource("qemu-devel", "mbox", "https://example.test"),
    ]))
    assert web_server._readiness(tmp_db, config)["status"] == "degraded"
    db.record_fetch_run(
        tmp_db, source="ml", project="qemu-devel", started_at=db.now_utc_iso(),
        success=True, complete=True, new_count=0,
        requested_since="2026-07-14T00:00:00Z",
    )
    payload = web_server._readiness(tmp_db, config)
    assert payload["status"] == "ok"
    assert payload["sources"][0]["fresh"] is True


def test_home_has_daily_weekly_monthly_archive_tabs():
    context = {
        "daily": [], "weekly": [], "monthly": [], "rendered_months": {"2026-07"},
        "cal": html_render.build_calendar("2026-07", set()),
    }
    page = html_render.render_index_html(Config(), context)
    assert page.count("data-archive-tab=") == 3
    assert "home-toolbar" not in page
    assert "index-2026-07.html" not in page
    assert "daily/index.html" in page
    assert "topics.html" in page


def test_archive_and_topic_pages_render():
    archive = html_render.render_archive_html(Config(), "daily", [{
        "period_key": "2026-07-13", "item_count": 18,
        "model": "deepseek-v4-flash", "generated_at": "2026-07-14T00:00:00Z",
    }])
    assert "2026-07-13.html" in archive
    groups = topics.build_topic_groups([{
        "period": "daily", "period_key": "2026-07-13",
        "content_json": '{"sections":[{"name":"KVM","items":[{"title":"Improve migration performance","url":"u","summary":"s"}]}]}',
    }])
    page = html_render.render_topics_html(Config(), groups)
    assert "热迁移" in page
    assert "虚机性能" in page
    assert page.count('href="u"') == 2


def test_weekly_range_is_local_inclusive_natural_week():
    value = html_render._period_range("weekly", "2026-W28", "Asia/Shanghai")
    assert value["label"] == "2026 年第 28 周（7.6–7.12）"
    assert value["full"] == "2026-07-06 至 2026-07-12"


def test_archive_and_topics_offer_pagination_over_ten_items():
    reports = [{
        "period_key": f"2026-07-{day:02d}", "item_count": 20,
        "model": "test", "generated_at": "2026-07-14T00:00:00Z",
    } for day in range(1, 12)]
    archive = html_render.render_archive_html(Config(), "daily", reports)
    assert archive.count("data-page-item href") == 11
    assert '<option value="30">30</option>' in archive

    items = ",".join(
        '{"title":"migration %d","url":"u%d"}' % (i, i) for i in range(11)
    )
    groups = topics.build_topic_groups([{
        "period": "daily", "period_key": "2026-07-13",
        "content_json": '{"sections":[{"name":"KVM","items":[' + items + ']}]}',
    }])
    page = html_render.render_topics_html(Config(), groups)
    assert "data-page-controls" in page


def test_kvm_forum_renders_newest_first_with_source_links():
    editions = [{
        "year": year, "url": f"https://example.com/{year}", "titles": ["Talk"],
        "analysis": {"headline": f"H{year}", "themes": ["KVM"], "summary": "S"},
    } for year in (2010, 2025)]
    analysis = {
        "headline": "趋势", "overview": "概述", "model": "test", "method": "标题分析。",
        "eras": [
            {"years": "2010—2013", "name": "早期", "summary": "A"},
            {"years": "2022—2025", "name": "近期", "summary": "B"},
        ],
    }
    page = html_render.render_kvm_forum_html(Config(), editions, analysis)
    assert page.index('id="year-2025"') < page.index('id="year-2010"')
    assert page.index("2022—2025") < page.index("2010—2013")
    assert "查看 2025 年原始议程" in page
    assert 'class="era-years"' in page and 'class="era-name"' in page


def test_scheduler_uses_just_finished_periods():
    tz = ZoneInfo("Asia/Shanghai")
    daily_now = datetime(2026, 7, 15, 0, 15, tzinfo=tz)
    assert ("daily", ["daily", "2026-07-14", "--no-fetch"]) in scheduler.scheduled_commands(
        Config(), daily_now
    )
    weekly_now = datetime(2026, 7, 20, 0, 25, tzinfo=tz)
    commands = scheduler.scheduled_commands(Config(), weekly_now)
    assert any(name == "weekly" and command[-1] == "--no-fetch"
               for name, command in commands)
    assert scheduler.cron_matches("7 */4 * * *", datetime(
        2026, 7, 15, 8, 7, tzinfo=tz
    ))
    caught_up = scheduler.due_commands(
        Config(), datetime(2026, 7, 20, 0, 5, tzinfo=tz), weekly_now
    )
    assert {name for name, _command, _at in caught_up} == {"fetch", "daily", "weekly"}


def test_database_backup_and_restore_roundtrip(tmp_path):
    db_path = tmp_path / "data.db"
    connection = db.connect(db_path)
    db.save_report(connection, "daily", "2026-07-14", {
        "period": "daily", "period_key": "2026-07-14",
    }, "Asia/Shanghai")
    connection.close()
    archive, digest = maintenance.backup_database(db_path, tmp_path / "seed.db.gz")
    assert len(digest) == 64 and archive.exists()

    connection = db.connect(db_path)
    db.save_report(connection, "daily", "2026-07-15", {
        "period": "daily", "period_key": "2026-07-15",
    }, "Asia/Shanghai")
    connection.close()
    previous, restored_digest = maintenance.restore_database(
        db_path, archive, expected_sha256=digest, force=True,
    )
    assert previous and previous.exists()
    assert restored_digest == digest
    connection = db.connect(db_path)
    assert db.get_report(connection, "daily", "2026-07-14") is not None
    assert db.get_report(connection, "daily", "2026-07-15") is None
    connection.close()


# ---------- threads: 主题/系列折叠 (纯函数) ----------
def test_thread_key_series():
    # 同系列不同 patch 应同 key
    k1 = threads._thread_key("[RFC PATCH 115/134] foo", "AuthorA <a@x>", "2026-07-12")
    k2 = threads._thread_key("[RFC PATCH 116/134] bar", "AuthorA <a@x>", "2026-07-12")
    assert k1 == k2
    assert k1.startswith("series:")


def test_thread_key_series_different_author():
    k1 = threads._thread_key("[PATCH 1/9] x", "A <a@x>", "2026-07-12")
    k2 = threads._thread_key("[PATCH 2/9] x", "B <b@x>", "2026-07-12")
    assert k1 != k2  # 不同作者 -> 不同系列


def test_thread_key_subject():
    # 非系列按规范化 subject
    k1 = threads._thread_key("[PATCH] hw/nvme: fix X", None, "2026-07-12")
    k2 = threads._thread_key("Re: [PATCH] hw/nvme: fix X", None, "2026-07-12")
    assert k1 == k2


# ---------- threads: _compute_ml_roots (用临时 DB) ----------
@pytest.fixture
def tmp_db():
    with tempfile.TemporaryDirectory() as d:
        conn = db.connect(Path(d) / "t.db")
        yield conn
        conn.close()


def _insert(conn, nid, mid=None, irt=None, subject="s", author="a", created="2026-07-12T00:00:00Z"):
    db.upsert_item(conn, {
        "source": "ml", "project": "qemu-devel", "native_id": nid, "message_id": mid,
        "in_reply_to": irt, "thread_root": None, "author": author, "subject": subject,
        "kind": "patch", "created_at": created, "updated_at": created, "url": "u",
        "body_excerpt": "e", "raw_json": {},
    })


def test_compute_roots_inreplyto_chain(tmp_db):
    # A <- B <- C (in-reply-to 链)
    _insert(tmp_db, "A", mid="A", irt=None, subject="[PATCH 0/2] s")
    _insert(tmp_db, "B", mid="B", irt="A", subject="[PATCH 1/2] s")
    _insert(tmp_db, "C", mid="C", irt="B", subject="[PATCH 2/2] s")
    roots = threads._compute_ml_roots(tmp_db)
    assert roots[("qemu-devel", "B")] == "A"
    assert roots[("qemu-devel", "C")] == "A"  # C 回溯到根 A


def test_compute_roots_preset_hyperkitty(tmp_db):
    # hyperkitty 预设 thread_root (raw_json.thread_hash) 应被采用
    db.upsert_item(tmp_db, {
        "source": "ml", "project": "libvir-list", "native_id": "h1", "message_id": None,
        "in_reply_to": None, "thread_root": None, "author": "a", "subject": "s",
        "kind": "patch", "created_at": "2026-07-12T00:00:00Z", "updated_at": "2026-07-12T00:00:00Z",
        "url": "u", "body_excerpt": "e", "raw_json": {"thread_hash": "TH123"},
    })
    roots = threads._compute_ml_roots(tmp_db)
    assert roots[("libvir-list", "h1")] == "TH123"


# ---------- 数据身份 / 活动时间 ----------
def test_native_id_is_unique_per_project(tmp_db):
    common = {
        "source": "gitlab", "native_id": "issue:1", "message_id": None,
        "in_reply_to": None, "thread_root": "issue:1", "author": "a",
        "subject": "s", "kind": "issue", "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-07-12T00:00:00Z", "url": "u", "body_excerpt": "e",
        "raw_json": {"type": "issue"},
    }
    db.upsert_item(tmp_db, {**common, "project": "qemu"})
    db.upsert_item(tmp_db, {**common, "project": "libvirt"})
    assert tmp_db.execute(
        "SELECT COUNT(*) FROM items WHERE source='gitlab' AND native_id='issue:1'"
    ).fetchone()[0] == 2


def test_gitlab_thread_last_seen_uses_activity_time(tmp_db):
    db.upsert_item(tmp_db, {
        "source": "gitlab", "project": "qemu", "native_id": "issue:9",
        "message_id": None, "in_reply_to": None, "thread_root": "issue:9",
        "author": "a", "subject": "old issue active today", "kind": "issue",
        "created_at": "2024-01-01T00:00:00Z", "updated_at": "2026-07-12T03:00:00Z",
        "url": "u", "body_excerpt": "e",
        "raw_json": {"type": "issue", "user_notes_count": 1},
    })
    threads.rebuild_threads(tmp_db)
    row = tmp_db.execute(
        "SELECT first_seen,last_seen FROM threads WHERE project='qemu'"
    ).fetchone()
    assert row["first_seen"] == "2024-01-01T00:00:00Z"
    assert row["last_seen"] == "2026-07-12T03:00:00Z"


def test_activity_window_includes_recently_updated_issue(tmp_db):
    db.upsert_item(tmp_db, {
        "source": "gitlab", "project": "qemu", "native_id": "issue:10",
        "thread_root": "issue:10", "author": "a", "subject": "s", "kind": "issue",
        "created_at": "2024-01-01T00:00:00Z", "updated_at": "2026-07-12T03:00:00Z",
        "url": "u", "body_excerpt": "e", "raw_json": {"type": "issue"},
    })
    rows = db.get_activity_items_in_window(
        tmp_db, "2026-07-12T00:00:00Z", "2026-07-13T00:00:00Z"
    )
    assert [r["native_id"] for r in rows] == ["issue:10"]


# ---------- HyperKitty 官方 API 辅助 ----------
def test_hyperkitty_source_parts():
    origin, address, archive = hyperkitty._source_parts(
        "https://lists.libvirt.org/archives/list/devel@lists.libvirt.org/"
    )
    assert origin == "https://lists.libvirt.org"
    assert address == "devel@lists.libvirt.org"
    assert archive.endswith("/archives/list/devel@lists.libvirt.org/")


def test_hyperkitty_author_and_parent_hash():
    assert hyperkitty._author({
        "sender_name": "A User", "sender": {"address": "a (a) example.com"}
    }) == "A User <a@example.com>"
    assert hyperkitty._parent_hash(
        "https://host/archives/api/list/x/email/ABCDEFGHIJKLMNOPQRSTUVWX/?format=json"
    ) == "ABCDEFGHIJKLMNOPQRSTUVWX"


# ---------- 报表项目配额端到端 ----------
def test_report_selection_reserves_each_project(tmp_db):
    created = "2026-07-12T00:00:00Z"
    projects = ["qemu-devel"] * 8 + ["libvir-list", "kvm"]
    for idx, project in enumerate(projects):
        native_id = f"m{idx}@example.com"
        db.upsert_item(tmp_db, {
            "source": "ml", "project": project, "native_id": native_id,
            "message_id": native_id, "in_reply_to": None,
            "thread_root": None, "author": f"a{idx}",
            "subject": f"[PATCH] change {idx}", "kind": "patch",
            "created_at": created, "updated_at": created, "url": f"u{idx}",
            "body_excerpt": f"body {idx}", "raw_json": {},
        })
    threads.rebuild_threads(tmp_db)
    config = Config()
    config.llm.daily_top_n = 4
    content = report.generate(tmp_db, config, "daily", "2026-07-12")
    selected = {t["project"] for t in content["top_threads"]}
    assert selected == {"qemu-devel", "libvir-list", "kvm"}
    assert len(content["top_threads"]) == 4
