"""virt-report 基础逻辑测试 (不调 LLM/网络)。覆盖周期窗口、线程折叠、URL/时间解析、sanitize、分类。"""
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from virt_report import access, db, maintenance, metrics, rss
from virt_report.collectors import base, hyperkitty, mbox
from virt_report.config import Config, MailingListSource, Sources, Storage
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
    assert topics.classify_item({
        "title": "Stable patch round-up", "body_excerpt": "migration hotplug performance",
    }) == []


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
        "llm_usage": {"total_tokens": 12345},
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
    assert 'data-item-filter="x86"' in page and 'data-item-filter="bug"' in page
    assert 'data-architectures="x86"' in page
    assert "12,345 tokens" not in page
    assert "↗" not in page
    assert 'name="color-scheme" content="light"' in page
    assert "theme-toggle" not in page
    assert "prefers-color-scheme:dark" not in page


def test_index_context_applies_home_report_limits(tmp_db):
    for index in range(1, 21):
        for period, key in (
            ("daily", f"2026-07-{index:02d}"),
            ("weekly", f"2026-W{index:02d}"),
            ("monthly", f"2026-{index:02d}"),
        ):
            db.save_report(tmp_db, period, key, {"period": period, "period_key": key},
                           "Asia/Shanghai", item_count=1, model="test")
    context = web_server._index_context(tmp_db)
    assert len(context["daily"]) == 15
    assert len(context["weekly"]) == 15
    assert len(context["monthly"]) == 15
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
    assert payload["status"] == "degraded"
    for period, state in payload["reports"].items():
        db.save_report(tmp_db, period, state["expected_key"], {
            "period": period, "period_key": state["expected_key"], "fallback": False,
        }, config.timezone, model="test")
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
    assert page.count("data-home-report-tab=") == 3


def test_archive_and_topic_pages_render(tmp_db):
    archive = html_render.render_archive_html(Config(), "daily", [{
        "period_key": "2026-07-13", "item_count": 18,
        "model": "deepseek-v4-flash", "generated_at": "2026-07-14T00:00:00Z",
    }])
    assert "2026-07-13.html" in archive
    _insert(tmp_db, "topic-1", subject="Improve migration performance")
    threads.rebuild_threads(tmp_db)
    _save_report_mentions(tmp_db, "weekly", "2026-W28", ["u"])
    groups = topics.build_topic_groups(tmp_db)
    page = html_render.render_topics_html(Config(), groups)
    assert "热迁移" in page
    assert "虚机性能" in page
    assert page.count('href="u"') == 2


def test_report_generated_date_uses_site_timezone():
    report_row = {
        "period_key": "2026-07-15", "item_count": 29,
        "model": "deepseek-v4-flash",
        "generated_at": "2026-07-15T16:16:05Z",
    }
    archive = html_render.render_archive_html(Config(), "daily", [report_row])
    assert "2026-07-16 生成" in archive
    assert "2026-07-15 生成" not in archive
    context = {
        "daily": [report_row], "weekly": [], "monthly": [],
        "cal": html_render.build_calendar("2026-07", {"2026-07-15"}),
    }
    home = html_render.render_index_html(Config(), context)
    assert "2026-07-16 生成" in home
    assert "2026-07-15 生成" not in home
    assert '<span class="brand-sub">虚拟化社区动态</span>' in home
    assert ('<img class="brand-mark" src="assets/brand-mark.png" alt="" '
            'width="31" height="31"><span>virt-report</span></a><span class="brand-sub">'
            '虚拟化社区动态</span>') in home
    assert '<link rel="shortcut icon" href="favicon.ico">' in home
    assert ('<link rel="icon" type="image/png" sizes="32x32" '
            'href="favicon-32.png">') in home
    assert ('<div class="report-card-top"><div class="r-title">2026-07-15</div>'
            '<span class="type">日报</span></div>') in home
    assert ".report-card-top{display:flex;align-items:flex-start" in home
    assert ".report-card .type{flex:0 0 auto;padding-top:1px;color:var(--brand);font-size:12px" in home
    assert ".brand-sub{display:none}" not in home
    assert "@media(max-width:620px)" in home
    assert ".brand-mark{width:28px;height:28px}" in home


def test_brand_assets_export_with_static_site(tmp_path):
    html_render.export_brand_assets(tmp_path)
    mark = tmp_path / "assets" / "brand-mark.png"
    assert mark.read_bytes() == (
        html_render.ASSETS_DIR / "brand-mark.png"
    ).read_bytes()
    assert mark.stat().st_size < 8_000
    assert (tmp_path / "favicon-32.png").stat().st_size < 3_000
    assert (tmp_path / "favicon.ico").stat().st_size < 8_000


def test_weekly_range_is_local_inclusive_natural_week():
    value = html_render._period_range("weekly", "2026-W28", "Asia/Shanghai")
    assert value["label"] == "2026 年第 28 周（7.6–7.12）"
    assert value["full"] == "2026-07-06 至 2026-07-12"


def test_archive_and_topic_detail_offer_pagination_over_ten_items(tmp_db):
    reports = [{
        "period_key": f"2026-07-{day:02d}", "item_count": 20,
        "model": "test", "generated_at": "2026-07-14T00:00:00Z",
    } for day in range(1, 12)]
    archive = html_render.render_archive_html(Config(), "daily", reports)
    assert archive.count("data-page-item href") == 11
    assert '<option value="30">30</option>' in archive

    for index in range(11):
        _insert(tmp_db, f"migration-{index}", subject=f"migration {index}")
    threads.rebuild_threads(tmp_db)
    _save_report_mentions(tmp_db, "weekly", "2026-W28", ["u"])
    groups = topics.build_topic_groups(tmp_db)
    page = html_render.render_topics_html(Config(), groups)
    migration = next(group for group in groups if group["key"] == "migration")
    assert migration["total"] == 11
    assert migration["featured_count"] == 8
    assert "汇总 11 · 近期 0" in page
    assert ">查看全部条目</a>" in page
    assert "查看热迁移全部条目" not in page
    assert "topics/migration/" in page
    detail = topics.build_topic_detail(tmp_db, "migration", page=2, per_page=10)
    assert detail["page"] == 2 and detail["pages"] == 2
    assert len(detail["items"]) == 1
    detail_page = html_render.render_topic_detail_html(Config(), detail)
    assert "2 / 2" in detail_page
    assert "per_page=20" in detail_page


def test_security_topic_requires_raw_evidence_and_strict_cve(tmp_db):
    _insert(tmp_db, "cve", subject="KVM: fix CVE-2026-46113 use-after-free")
    _insert(tmp_db, "placeholder", subject="QEMU: CVE-2026-XXXX placeholder")
    _insert(tmp_db, "cca", subject="KVM: arm64: add Arm CCA support")
    threads.rebuild_threads(tmp_db)
    _save_report_mentions(tmp_db, "weekly", "2026-W28", ["u"])
    assert topics.sync_topic_index(tmp_db) == 3
    assert topics.sync_topic_index(tmp_db) == 0
    security = next(group for group in topics.build_topic_groups(tmp_db)
                    if group["key"] == "security")
    by_title = {item["title"]: item for item in security["items"]}
    assert by_title["KVM: fix CVE-2026-46113 use-after-free"]["cve_ids"] == [
        "CVE-2026-46113"
    ]
    assert "QEMU: CVE-2026-XXXX placeholder" not in by_title
    assert "KVM: arm64: add Arm CCA support" not in by_title
    assert security["raw_total"] == 2
    assert security["curated_count"] == 1
    assert tmp_db.execute(
        "SELECT COUNT(*) FROM topic_entries WHERE topic_key='security' "
        "AND security_type='enhancement'"
    ).fetchone()[0] == 1


def test_security_topic_does_not_treat_generic_patch_replies_as_defects(tmp_db):
    _insert(tmp_db, "rmm", subject="firmware: arm_rmm: Add RMM v2.0 support")
    _insert(tmp_db, "memfd", subject="KVM: guest_memfd cleanups")
    _insert(tmp_db, "nsvm", subject="KVM: x86: optimize nSVM TLB flushes")
    _insert(tmp_db, "oob", subject="QEMU: fix out-of-bounds access")
    threads.rebuild_threads(tmp_db)
    _save_report_mentions(tmp_db, "weekly", "2026-W28", ["u"])
    security = next(group for group in topics.build_topic_groups(tmp_db)
                    if group["key"] == "security")
    by_title = {item["title"]: item for item in security["items"]}
    assert "firmware: arm_rmm: Add RMM v2.0 support" not in by_title
    assert "KVM: guest_memfd cleanups" not in by_title
    assert "KVM: x86: optimize nSVM TLB flushes" not in by_title
    assert by_title["QEMU: fix out-of-bounds access"]["security_type"] == "defect"


def test_security_feed_excludes_enhancements_but_keeps_internal_index(tmp_db):
    _insert(tmp_db, "cve-feed", subject="KVM: CVE-2026-46113 use-after-free",
            url="https://e/cve", created="2026-07-20T00:00:00Z")
    _insert(tmp_db, "cca-feed", subject="KVM: arm64: add Arm CCA support",
            url="https://e/cca", created="2026-07-20T00:00:00Z")
    threads.rebuild_threads(tmp_db)
    _save_report_mentions(tmp_db, "daily", "2026-07-20", [
        "https://e/cve", "https://e/cca",
    ])
    feed = rss.security_feed(tmp_db, Config())
    assert "CVE-2026-46113" in feed
    assert "Arm CCA support" not in feed
    assert tmp_db.execute(
        "SELECT COUNT(*) FROM topic_entries WHERE security_type='enhancement'"
    ).fetchone()[0] == 1


def test_topic_mobile_layout_wraps_controls_and_long_titles():
    page = html_render.render_topics_html(Config(), [])
    assert ".topic-page{min-width:0;max-width:100%}" in page
    assert ".topic-item-head>a{min-width:0;overflow-wrap:anywhere" in page
    assert ".topic-heading{align-items:flex-start;flex-direction:column;gap:8px}" in page
    assert ".topic-controls>div{width:100%;min-width:0;flex-wrap:wrap}" in page


def test_topic_public_layers_use_curated_and_recent_report_evidence(tmp_db):
    _insert(tmp_db, "curated", subject="migration curated", url="https://e/c",
            created="2026-06-01T00:00:00Z")
    _insert(tmp_db, "recent", subject="migration recent", url="https://e/r",
            created="2026-07-20T00:00:00Z")
    _insert(tmp_db, "stale", subject="migration stale", url="https://e/s",
            created="2026-06-01T00:00:00Z")
    threads.rebuild_threads(tmp_db)
    _save_report_mentions(tmp_db, "weekly", "2026-W22", ["https://e/c"])
    _save_report_mentions(tmp_db, "daily", "2026-07-20", ["https://e/r", "https://e/s"])
    migration = next(group for group in topics.build_topic_groups(tmp_db)
                     if group["key"] == "migration")
    assert migration["raw_total"] == 3
    assert migration["curated_count"] == 1
    assert migration["recent_count"] == 1
    assert {item["url"] for item in migration["items"]} == {
        "https://e/c", "https://e/r",
    }


def test_daily_continuing_threads_are_capped_but_new_threads_remain():
    rows = [{
        "url": f"https://e/{index}", "thread_key": f"t{index}",
        "project": "qemu-devel", "salience_score": 100 - index,
        "message_count": index,
    } for index in range(10)]
    history = {row["url"]: {"summary": "旧摘要"} for row in rows[:8]}
    selected = report._limit_continuing_threads(rows, history, limit=3)
    assert {row["url"] for row in rows[8:]}.issubset({row["url"] for row in selected})
    assert sum(row["url"] in history for row in selected) == 3


def test_rss_and_metrics_use_stored_report_usage(tmp_db):
    content = {
        "period": "daily", "period_key": "2026-07-15", "headline": "测试日报",
        "overview": [], "fallback": False,
        "llm_usage": {"prompt_tokens": 1000, "completion_tokens": 500,
                      "prompt_cache_hit_tokens": 200,
                      "prompt_cache_miss_tokens": 800},
    }
    db.save_report(tmp_db, "daily", "2026-07-15", content, "Asia/Shanghai",
                   item_count=1, model="deepseek-v4-flash")
    feed = rss.report_feed(tmp_db, Config(), "daily")
    assert "<rss version=\"2.0\">" in feed
    assert "测试日报" in feed
    assert feed == rss.report_feed(tmp_db, Config(), "daily")
    values = metrics.build_metrics(tmp_db, Config())
    assert values["models"]["deepseek-v4-flash"]["total_tokens"] == 1500
    assert values["models"]["deepseek-v4-flash"]["calls"] == 1
    assert values["estimated_cost_cny"] > 0
    page = html_render.render_metrics_html(Config(), values)
    assert "built-in method" not in page
    assert ">0</strong><span>原始条目" in page


def test_metrics_access_accepts_bearer_and_signed_cookie():
    key = "test-only-long-access-key"
    token = access.issue_session(key, 12, now=1000)
    assert access.verify_session(token, key, now=1001)
    assert not access.verify_session(token, key, now=1000 + 12 * 3600 + 1)
    assert access.is_authorized({"Authorization": f"Bearer {key}"}, key)
    assert access.is_authorized({"Cookie": f"{access.COOKIE_NAME}={token}"}, key, now=1001)
    assert not access.is_authorized({"Authorization": "Bearer wrong"}, key)
    login = html_render.render_metrics_login_html(Config(), error=True)
    assert 'type="password"' in login and "密钥不正确" in login
    assert "该页面包含采集状态信息，请输入访问密钥。" in login
    assert "模型成本信息" not in login
    assert ">运行状态</a>" in login
    assert ">运行</a>" not in login


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
    backup_now = datetime(2026, 7, 20, 1, 5, tzinfo=tz)
    assert any(name == "backup" and command[-2:] == ["--keep-days", "14"]
               for name, command in scheduler.scheduled_commands(Config(), backup_now))


def test_scheduler_retries_fallback_report_and_records_runs(tmp_path):
    config = Config(storage=Storage(db_path=tmp_path / "scheduler.db"))
    conn = db.connect(config.db_path)
    db.save_report(conn, "daily", "2026-07-14", {
        "period": "daily", "period_key": "2026-07-14", "fallback": True,
    }, config.timezone, model="test")
    conn.close()
    command = ["daily", "2026-07-14", "--no-fetch"]
    assert scheduler._report_exists(config, "daily", command) is False
    conn = db.connect(config.db_path)
    db.save_report(conn, "daily", "2026-07-14", {
        "period": "daily", "period_key": "2026-07-14", "fallback": False,
    }, config.timezone, model="test")
    run_id = db.start_scheduler_run(
        conn, identity="daily:2026-07-14", job_name="daily",
        scheduled_at="2026-07-15T00:15:00+08:00", attempt=1,
    )
    db.finish_scheduler_run(conn, run_id, status="success", exit_code=0)
    values = metrics.build_metrics(conn, config)
    conn.close()
    assert scheduler._report_exists(config, "daily", command) is True
    assert values["scheduler_runs"][0]["status"] == "success"


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


def test_prune_backups_only_removes_expired_automatic_files(tmp_path):
    old = tmp_path / "auto-2026-01-01.db.gz"
    current = tmp_path / "auto-2026-07-24.db.gz"
    manual = tmp_path / "virt-report.db.gz"
    for path in (old, current, manual):
        path.write_bytes(b"snapshot")
    os.utime(old, (1, 1))
    assert maintenance.prune_backups(tmp_path, 14) == [old]
    assert current.exists() and manual.exists()


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


def _insert(conn, nid, mid=None, irt=None, subject="s", author="a",
            created="2026-07-12T00:00:00Z", url="u"):
    db.upsert_item(conn, {
        "source": "ml", "project": "qemu-devel", "native_id": nid, "message_id": mid,
        "in_reply_to": irt, "thread_root": None, "author": author, "subject": subject,
        "kind": "patch", "created_at": created, "updated_at": created, "url": url,
        "body_excerpt": "e", "raw_json": {},
    })


def _save_report_mentions(conn, period, period_key, urls):
    items = [{"url": url, "summary": f"{period} 精选摘要", "impact": "影响说明"}
             for url in urls]
    db.save_report(conn, period, period_key, {
        "period": period, "period_key": period_key, "fallback": False,
        "sections": [{"key": "qemu", "items": items}],
    }, "Asia/Shanghai", item_count=len(items), model="test")


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
