"""virt-report 基础逻辑测试 (不调 LLM/网络)。覆盖周期窗口、线程折叠、URL/时间解析、sanitize、分类。"""
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from virt_report import access, conferences, db, kvm_forum, maintenance, metrics, rss
from virt_report.collectors import base, hyperkitty, mbox
from virt_report.config import Config, MailingListSource, Sources, Storage
from virt_report.processing import architecture, category, classify, threads, topics
from virt_report.render import render as html_render
from virt_report import server as web_server
from virt_report import scheduler
from virt_report.summarize import llm_provider, periods, report


def _site_css() -> str:
    return (html_render.ASSETS_DIR / "site.css").read_text(encoding="utf-8")


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
    assert architecture.detect_architectures(["KVM: arm64: update GICv4"]) == ["Arm"]
    assert architecture.detect_architectures(["target/riscv: vector update"]) == ["RISC-V"]
    assert architecture.detect_architectures(["target/hexagon: update HVX"]) == ["Hexagon"]
    assert architecture.detect_architectures(["loongarch64 user-only"]) == ["LoongArch"]
    assert architecture.detect_architectures(["harmless refactor"]) == []


def test_focus_architecture_priority():
    assert architecture.normalize_architectures(["ARM", "Arm", "x86"]) == ["Arm", "x86"]
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


def test_board_firmware_boot_is_not_vm_lifecycle():
    item = {"title": "hw/arm: Add Cortex-M7 SoC firmware\nboot support"}
    assert "lifecycle" not in topics.classify_item(item)
    assert "lifecycle" in topics.classify_item({
        "title": "x86/fred: Fix early boot failures on SEV-SNP guests",
    })
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
    assert len(ov) == 3  # 固定补齐 QEMU / KVM / Libvirt
    assert [o["project"] for o in ov] == ["QEMU", "KVM", "Libvirt"]


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
    assert secs[1]["items"] == []


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
    assert [item["ref"] for item in sections[1]["items"]] == ["T002", "T001"]
    assert sections[1]["items"][0]["architectures"] == ["x86"]


def test_limit_sections_keeps_project_coverage_and_focus_architectures():
    sections = [
        {"key": "qemu", "items": [
            {"ref": "T001", "architectures": []},
            {"ref": "T009", "architectures": ["x86"]},
        ]},
        {"key": "libvirt", "items": [{"ref": "T002", "architectures": []}]},
        {"key": "kvm", "items": [
            {"ref": "T003", "architectures": []},
            {"ref": "T010", "architectures": ["Arm"]},
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
        "overview": [
            {"project": "Libvirt"}, {"project": "QEMU"}, {"project": "KVM"},
        ],
        "sections": [
            {"key": "libvirt", "items": []},
            {"key": "qemu", "items": []},
            {"key": "kvm", "items": [{"ref": "T001", "architectures": []}]},
        ],
    }
    enriched = report.enrich_architectures(content)
    assert enriched["top_threads"][0]["architectures"] == ["Arm"]
    assert [item["project"] for item in enriched["overview"]] == [
        "QEMU", "KVM", "Libvirt",
    ]
    assert [section["key"] for section in enriched["sections"]] == [
        "qemu", "kvm", "libvirt",
    ]
    assert enriched["sections"][1]["items"][0]["architectures"] == ["Arm"]


def test_enrich_report_completes_or_drops_empty_watchlist_reasons():
    content = {
        "period": "daily",
        "top_threads": [],
        "watchlist": [
            {"project": "QEMU", "topic": "migration fast snapshot load", "reason": ""},
            {"project": "KVM", "topic": "unmatched topic", "reason": ""},
        ],
        "sections": [{"key": "qemu", "name": "QEMU", "items": [{
            "ref": "T001", "title": "migration: 实现快照快速加载",
            "original_title": "[PATCH v4] migration: fast snapshot load",
            "summary": "仅加载设备状态，RAM 页按需加载",
            "status": "评审中", "architectures": ["ARM"], "category": "feature",
        }]}],
    }

    enriched = report.enrich_architectures(content)

    assert enriched["sections"][0]["items"][0]["architectures"] == ["Arm"]
    assert enriched["watchlist"] == [{
        "project": "QEMU",
        "topic": "migration fast snapshot load",
        "reason": "仅加载设备状态，RAM 页按需加载。当前状态为评审中，后续版本与评审结论值得观察。",
    }]


def test_about_page_and_architecture_badge_render():
    config = Config()
    assert {config.llm.daily_model, config.llm.weekly_model,
            config.llm.monthly_model} == {"deepseek-v4-flash"}
    about = html_render.render_about_html(config)
    assert "KVM Forum" in about
    assert "2026 年 8 月 1 日起" in about
    assert "DeepSeek-V4-Flash 正式版" in about
    assert 'href="https://api-docs.deepseek.com/zh-cn/updates/"' in about
    assert 'href="mailto:taifu@taifua.com"' in about
    assert "摘要偏差、分类错误、链接失效或其他问题" in about
    assert "收录会议" in about and "USENIX Security" in about
    assert "关注会议" not in about
    assert about.index("会议内容扩展") < about.index("AI 模型更新")
    assert "学术会议内容覆盖 2010—2026 年" in about
    assert "RSS 订阅" in about
    assert 'href="feed.xml"><span>全部报告</span><small>三类报告 · 最近 50 份</small>' in about
    assert 'href="daily/feed.xml"><span>日报</span><small>最近 30 期</small>' in about
    assert 'href="weekly/feed.xml"><span>周报</span><small>最近 26 期</small>' in about
    assert 'href="monthly/feed.xml"><span>月报</span><small>最近 24 期</small>' in about
    assert 'topics/security/feed.xml' not in about
    assert "RSS 提供近期报告更新，完整历史请查看对应报告归档" in about
    assert "漏洞告警源" not in about
    footer = about.split('<footer class="site-foot">', 1)[1].split("</footer>", 1)[0]
    assert ">RSS</a>" not in footer
    assert "虚拟化社区动态。数据来自社区，分析仅供参考。" in footer
    assert '分析仅供参考。<a href="metrics.html">运行状态</a>' in footer
    assert footer.count("<span") == 1
    assert ".foot-row a{white-space:nowrap}" in _site_css()
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
    assert 'class="tag arch focus">x86' in page
    assert '<div class="d-actions"><span class="tag kind-other">' in page
    assert '<div class="d-meta"><span class="tag arch focus">x86' in page
    assert 'data-item-filter="x86"' in page and 'data-item-filter="bug"' in page
    assert page.index('data-item-filter="feature"') < page.index('data-item-filter="x86"')
    assert 'data-item-filter="Arm">Arm</button>' in page
    assert 'data-architectures="x86"' in page
    assert "12,345 tokens" not in page
    assert "↗" not in page
    assert 'name="color-scheme" content="light"' in page
    assert "theme-toggle" not in page
    assert "prefers-color-scheme:dark" not in page
    css = _site_css()
    assert ".report-hero:before" not in css
    assert ".sec-title>span:first-child:before" not in css
    assert ".dyn.focus-arch{border-left" not in css
    assert "border-left:3px solid var(--brand)" not in css
    weekly = dict(content, period="weekly", period_key="2026-W28",
                  label="2026 年第 28 周")
    weekly_page = html_render.render_report_html(Config(), weekly)
    assert ('<h1 class="title weekly-title"><span>2026 年第 28 周</span>'
            '<span class="weekly-range">7.6–7.12</span></h1>') in weekly_page
    assert "（7.6–7.12） 社区动态</h1>" not in weekly_page


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
    assert len(context["weekly"]) == 9
    assert len(context["monthly"]) == 6
    assert context["daily"][0]["period_key"] == "2026-07-20"
    assert context["weekly"][-1]["period_key"] == "2026-W12"
    assert context["monthly"][-1]["period_key"] == "2026-15"
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
    assert [group["key"] for group in groups] == [
        "migration", "live-upgrade", "hotplug", "performance", "lifecycle", "security",
    ]
    assert page.index('id="migration"') < page.index('id="security"')
    assert ('id="security" data-topic-group' in page
            and 'aria-controls="topic-group-content-security"' in page)
    assert ('aria-controls="topic-group-content-security">安全与漏洞</button>' in page
            and 'id="topic-group-content-security" class="topic-group-content" '
                'data-topic-group-content hidden' in page)
    assert ('aria-controls="topic-group-content-migration">热迁移</button>' in page
            and 'id="topic-group-content-migration" class="topic-group-content" '
                'data-topic-group-content>' in page)
    assert "热迁移" in page
    assert "虚机性能" in page
    assert page.count('href="u"') == 2


def test_topic_snapshot_serves_overview_and_detail_without_recompute(tmp_db, monkeypatch):
    _insert(tmp_db, "snapshot-topic", subject="Improve migration performance")
    threads.rebuild_threads(tmp_db)
    _save_report_mentions(tmp_db, "weekly", "2026-W28", ["u"])
    counts = topics.refresh_topic_snapshots(tmp_db)
    assert counts["migration"] == 1
    assert tmp_db.execute("SELECT COUNT(*) FROM topic_snapshots").fetchone()[0] == len(
        topics.TOPIC_RULES
    )

    def fail_recompute(_conn):
        raise AssertionError("Web request must not recompute topic snapshots")

    monkeypatch.setattr(topics, "_compute_topic_payloads", fail_recompute)
    groups = topics.build_topic_groups(tmp_db, allow_rebuild=False)
    migration = next(group for group in groups if group["key"] == "migration")
    assert migration["total"] == 1
    assert "report_mentions" not in migration["items"][0]
    detail = topics.build_topic_detail(
        tmp_db, "migration", page=1, per_page=10, allow_rebuild=False,
    )
    assert detail["total"] == 1
    assert detail["items"][0]["url"] == "u"


def test_topic_request_does_not_rebuild_when_snapshot_is_missing(tmp_db, monkeypatch):
    def fail_recompute(_conn):
        raise AssertionError("Request path attempted an offline rebuild")

    monkeypatch.setattr(topics, "_compute_topic_payloads", fail_recompute)
    assert topics.build_topic_groups(tmp_db, allow_rebuild=False) is None
    assert topics.build_topic_detail(
        tmp_db, "migration", allow_rebuild=False,
    ) is None


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
    assert (f'<img class="brand-mark" src="assets/brand-mark.png?v='
            f'{html_render.ASSET_VERSION}" alt="" '
            'width="31" height="31"><span>virt-report</span></a><span class="brand-sub">'
            '虚拟化社区动态</span>') in home
    assert (f'<link rel="shortcut icon" href="favicon.ico?v='
            f'{html_render.ASSET_VERSION}">') in home
    assert (f'<link rel="icon" type="image/png" sizes="32x32" '
            f'href="favicon-32.png?v={html_render.ASSET_VERSION}">') in home
    assert ('<div class="report-card-top"><div class="r-title">2026-07-15</div>'
            '<span class="type">日报</span></div>') in home
    css = _site_css()
    assert ".report-card-top{display:flex;align-items:flex-start" in css
    assert ".report-card .type{flex:0 0 auto;padding-top:1px;color:var(--brand);font-size:12px" in css
    assert ".brand-sub{display:none}" not in css
    assert "@media(max-width:620px)" in css
    assert ".brand-mark{width:28px;height:28px}" in css
    assert (f'<link rel="stylesheet" href="assets/site.css?v='
            f'{html_render.ASSET_VERSION}">') in home
    assert (f'<script src="assets/site.js?v={html_render.ASSET_VERSION}"></script>'
            in home)
    assert "<style>" not in home


def test_brand_assets_export_with_static_site(tmp_path):
    html_render.export_brand_assets(tmp_path)
    mark = tmp_path / "assets" / "brand-mark.png"
    assert mark.read_bytes() == (
        html_render.ASSETS_DIR / "brand-mark.png"
    ).read_bytes()
    # The raster-first mark retains subtle perspective shading while staying small.
    assert mark.stat().st_size < 48_000
    for filename in ("site.css", "site.js"):
        exported = tmp_path / "assets" / filename
        assert exported.read_bytes() == (html_render.ASSETS_DIR / filename).read_bytes()
    assert (tmp_path / "favicon-32.png").stat().st_size < 3_000
    assert (tmp_path / "favicon.ico").stat().st_size < 8_000


def test_weekly_range_is_local_inclusive_natural_week():
    value = html_render._period_range("weekly", "2026-W28", "Asia/Shanghai")
    assert value["name"] == "2026 年第 28 周"
    assert value["label"] == "2026 年第 28 周（7.6–7.12）"
    assert value["full"] == "2026-07-06 至 2026-07-12"
    archive = html_render.render_archive_html(Config(), "weekly", [{
        "period_key": "2026-W28", "item_count": 27, "model": "test",
        "generated_at": "2026-07-13T00:00:00Z",
    }])
    assert 'class="archive-key weekly-key">2026 年第 28 周（7.6–7.12）' in archive


def test_archive_and_topic_detail_offer_pagination_over_ten_items(tmp_db):
    reports = [{
        "period_key": f"2026-07-{day:02d}", "item_count": 20,
        "model": "test", "generated_at": "2026-07-14T00:00:00Z",
    } for day in range(1, 12)]
    archive = html_render.render_archive_html(Config(), "daily", reports)
    assert '<div data-pager data-default-size="10"><div class="archive-table">' in archive
    assert archive.count("data-page-item href") == 11
    assert archive.count("data-page-item href=\"2026-07-") == 11
    assert archive.count(" hidden") == 1
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
    assert 'class="on" href="?scope=curated&sort=priority&per_page=10">10</a>' in detail_page

    default_detail = topics.build_topic_detail(tmp_db, "migration")
    assert default_detail["per_page"] == 10
    assert default_detail["pages"] == 2
    assert len(default_detail["items"]) == 10
    assert topics.build_topic_detail(tmp_db, "migration", per_page=999)["per_page"] == 10

    assets = (html_render.ASSETS_DIR / "site.js").read_text(encoding="utf-8")
    assert "[data-topic-group]" in assets and "aria-expanded" in assets


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


def test_security_topic_excludes_enhancements_but_keeps_internal_index(tmp_db):
    _insert(tmp_db, "cve-feed", subject="KVM: CVE-2026-46113 use-after-free",
            url="https://e/cve", created="2026-07-20T00:00:00Z")
    _insert(tmp_db, "cca-feed", subject="KVM: arm64: add Arm CCA support",
            url="https://e/cca", created="2026-07-20T00:00:00Z")
    threads.rebuild_threads(tmp_db)
    _save_report_mentions(tmp_db, "daily", "2026-07-20", [
        "https://e/cve", "https://e/cca",
    ])
    detail = topics.build_topic_detail(
        tmp_db, "security", page=1, per_page=20, sort="latest", scope="recent"
    )
    titles = {item["title"] for item in detail["items"]}
    assert "KVM: CVE-2026-46113 use-after-free" in titles
    assert "KVM: arm64: add Arm CCA support" not in titles
    assert tmp_db.execute(
        "SELECT COUNT(*) FROM topic_entries WHERE security_type='enhancement'"
    ).fetchone()[0] == 1


def test_topic_mobile_layout_wraps_controls_and_long_titles():
    css = _site_css()
    assert ".topic-page{min-width:0;max-width:100%}" in css
    assert ".topic-item-head>a{min-width:0;overflow-wrap:anywhere" in css
    assert ".topic-heading{align-items:flex-start;flex-direction:column;gap:8px}" in css
    assert ".topic-controls>div{width:100%;min-width:0;flex-wrap:wrap}" in css


def test_report_mobile_layout_prevents_long_content_overflow():
    css = _site_css()
    assert "html{max-width:100%;overflow-x:hidden" in css
    assert ".report-layout{display:grid;min-width:0;max-width:100%" in css
    assert ".d-title{min-width:0;overflow-wrap:anywhere" in css
    assert ".d-sum,.dyn details,.dyn details span{min-width:0;max-width:100%;overflow-wrap:anywhere" in css
    assert ".wrap{width:calc(100% - 24px);max-width:1180px}" in css


def test_home_mobile_uses_one_report_switch_and_visible_archive():
    context = {
        "daily": [], "weekly": [], "monthly": [],
        "cal": html_render.build_calendar("2026-07", set()),
    }
    page = html_render.render_index_html(Config(), context)
    css = _site_css()
    assert page.count("data-home-report-tab=") == 3
    assert '<div class="mobile-archive-head"><span>报告归档</span><span data-mobile-archive-label>日报</span></div>' in page
    assert 'class="report-group first mobile-active"' in page
    assert "[data-home-report-panel]{display:none;margin-top:0}" in css
    assert "[data-home-report-panel].mobile-active{display:block}" in css
    assert ".archive-row{grid-template-columns:minmax(0,1fr) auto;gap:5px 12px}" in css
    assert ".archive-key.weekly-key{white-space:nowrap;font-size:15px}" in css
    assert ".archive-browser{display:block" in css
    assert ".archive-tabs{display:none}" in css
    assert "data-mobile-archive-toggle" not in page
    assert ".report-card:nth-child(n+6)" not in css
    assert "scrollbar-width:thin" in css
    assert "if(mobileReports.matches)selectArchive(button.dataset.homeReportTab)" in page
    assert "sessionStorage.getItem(reportTabStorageKey)" in page
    assert "sessionStorage.setItem(reportTabStorageKey,type)" in page


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


def test_topic_merges_patch_versions_and_keeps_curated_evidence(tmp_db):
    old_url = "https://e/live-update-v2"
    new_url = "https://e/live-update-v5"
    _insert(
        tmp_db, "live-v2",
        subject="[PATCH v2 00/22] vfio/pci: Base Live Update support for VFIO device files",
        url=old_url, created="2026-06-01T00:00:00Z",
    )
    _insert(
        tmp_db, "live-v5",
        subject="[PATCH v5 00/20] vfio/pci: Base Live Update support for VFIO",
        url=new_url, created="2026-07-20T00:00:00Z",
    )
    threads.rebuild_threads(tmp_db)
    _save_report_mentions(
        tmp_db, "monthly", "2026-06", [old_url],
        summary="v2 版本已经加入基础设备状态迁移流程",
    )
    _save_report_mentions(
        tmp_db, "daily", "2026-07-20", [new_url],
        summary="v5 版本已经调整设备状态迁移流程",
    )
    group = next(group for group in topics.build_topic_groups(tmp_db)
                 if group["key"] == "live-upgrade")
    assert group["total"] == 1
    assert group["items"][0]["url"] == new_url
    assert group["items"][0]["series_count"] == 2
    assert group["items"][0]["series_version"] == 5
    assert group["items"][0]["scope"] == "curated"
    assert group["items"][0]["summary"] == "v5 版本已经调整设备状态迁移流程"
    page = html_render.render_topics_html(Config(), [group])
    assert "迭代至 v5" in page


def test_security_fallback_names_component_risk_and_status():
    summary = topics._fallback_summary({
        "project": "QEMU",
        "title": "hw/virtio-rng: guest-triggered use-after-free during reset",
        "security_type": "defect",
        "status": "处理中",
    }, "安全与漏洞")
    assert "hw/virtio-rng" in summary
    assert "可由 guest 触发的释放后使用" in summary
    assert "当前状态为处理中" in summary
    assert "标题显示" not in summary


def test_topic_series_marks_summary_from_an_older_version():
    common = {
        "project": "QEMU", "thread_key": "thread", "salience_score": 1,
        "architectures": [], "cve_ids": [], "status": "评审中",
    }
    result = topics._merge_series([
        {
            **common, "title": "[PATCH v5] migration: improve switchover",
            "url": "https://e/v5", "activity_at": "2026-07-20T00:00:00Z",
            "report_mentions": [],
        },
        {
            **common, "title": "[PATCH v2] migration: improve switchover",
            "url": "https://e/v2", "activity_at": "2026-06-20T00:00:00Z",
            "report_mentions": [{
                "period": "monthly", "period_key": "2026-06",
                "generated_at": "2026-07-01T00:00:00Z", "url": "https://e/v2",
                "summary": "v2 调整了虚机迁移的切换处理流程", "impact": "",
            }],
        },
    ], "热迁移")[0]
    assert result["url"] == "https://e/v5"
    assert "此前版本调整了虚机迁移的切换处理流程" in result["summary"]
    assert "当前已迭代至 v5" in result["summary"]


def test_cve_fallback_does_not_claim_more_than_the_title():
    summary = topics._fallback_summary({
        "project": "KVM",
        "title": "[PATCH] KVM: x86: fix use-after-free (CVE-2026-12345)",
        "security_type": "cve",
        "cve_ids": ["CVE-2026-12345"],
        "status": "评审中",
    }, "安全与漏洞")
    assert "KVM 正在修复 x86 中释放后使用（CVE-2026-12345）" in summary
    assert "影响范围以原始线程和上游公告为准" in summary


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
    assert '<rss xmlns:atom="http://www.w3.org/2005/Atom" version="2.0">' in feed
    assert "测试日报" in feed
    assert "<link>http://127.0.0.1:8090/</link>" in feed
    assert ('<atom:link href="http://127.0.0.1:8090/daily/feed.xml" '
            'rel="self" type="application/rss+xml"') in feed
    assert "<category>日报</category>" in feed
    assert "<generator>virt-report</generator>" in feed
    assert feed == rss.report_feed(tmp_db, Config(), "daily")
    headers = rss.feed_http_headers(feed)
    assert headers["ETag"].startswith('"') and headers["ETag"].endswith('"')
    assert headers["Last-Modified"] in feed
    assert rss.is_not_modified({"If-None-Match": headers["ETag"]}, headers)
    assert rss.is_not_modified({"If-Modified-Since": headers["Last-Modified"]}, headers)
    assert not rss.is_not_modified({"If-None-Match": '"stale"',
                                    "If-Modified-Since": headers["Last-Modified"]}, headers)
    values = metrics.build_metrics(tmp_db, Config())
    assert values["models"]["deepseek-v4-flash"]["total_tokens"] == 1500
    assert values["models"]["deepseek-v4-flash"]["calls"] == 1
    assert values["estimated_cost_cny"] > 0
    page = html_render.render_metrics_html(Config(), values)
    assert "built-in method" not in page
    assert ">0</strong><span>原始条目" in page


def test_rss_period_limits_are_explicit_and_security_feed_is_not_public():
    assert rss.FEED_LIMITS == {None: 50, "daily": 30, "weekly": 26, "monthly": 24}
    assert not hasattr(rss, "security_feed")


def test_daily_rss_keeps_the_latest_thirty_reports(tmp_db):
    for day in range(1, 32):
        content = {
            "period": "daily", "period_key": f"2026-07-{day:02d}",
            "headline": f"第 {day} 期", "overview": [],
        }
        db.save_report(tmp_db, "daily", content["period_key"], content,
                       "Asia/Shanghai", item_count=1, model="test")
    feed = rss.report_feed(tmp_db, Config(), "daily")
    assert feed.count("<item>") == 30


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
    assert "1 个议题" in page and "个标题" not in page
    assert "查看长期演进概述" not in page
    assert 'class="forum-overview-mobile"' not in page


def test_conference_catalogue_is_curated_and_renders_without_documents():
    content = conferences.load_content()
    assert content["paper_count"] >= 80
    assert content["years"] == list(range(2026, 2009, -1))
    assert all(not paper["url"].lower().endswith((".pdf", ".ppt", ".pptx"))
               for paper in content["papers"])
    assert any(paper["venue"] == "vee" for paper in content["papers"])
    assert any("KVM" in paper["topics"] for paper in content["papers"])
    assert len(content["analysis"]["years"]) == 17
    assert all(item["paper_count"] for item in content["analysis"]["years"])
    assert content["year_counts"] == {
        year: sum(paper["year"] == year for paper in content["papers"])
        for year in content["years"]
    }
    assert all(paper["institutions"] for paper in content["papers"])
    blowfish = next(
        paper for paper in content["papers"] if paper["id"] == "osdi26-blowfish"
    )
    assert len(blowfish["institutions"]) == 4
    assert blowfish["institutions"].count("Peking University") == 1

    page = html_render.render_conferences_html(Config(), content)
    assert '<div class="kicker">Conference Archive</div>' in page
    assert "学术会议" in page and "academic-conferences.html" in page
    assert '<div><h2>KVM Forum</h2><span class="guide-range">2010—2025</span></div>' in page
    assert (f'<div><h2>学术会议</h2><span class="guide-range">'
            f'{content["analysis"]["coverage"]}</span></div>') in page
    assert 'href="kvm-forum.html">查看技术演进</a>' in page
    assert 'href="conference-papers.html">查看相关论文</a>' in page
    assert (f'跨 {len(content["academic_venues"])} 个系统会议收录 '
            f'{content["paper_count"]} 篇虚拟化相关论文') in page
    assert '<section class="conference-sources"><h2>收录学术会议</h2>' in page
    for venue in content["academic_venues"]:
        assert (f'href="conference-papers.html?venue={venue["key"]}">'
                f'{venue["paper_count"]} 篇相关论文</a>') in page
    assert "国内技术活动" not in page
    assert "持续讨论与技术来源" not in page
    assert '<details class="conference-sources"' not in page
    assert "重点" not in page and "待严格筛选" not in page
    assert "data-conference-browser" not in page
    assert "不调用 DeepSeek 自动生成" in page
    assert 'href="conferences.html" class="on">会议</a>' in page
    assert ".guide-range{display:block" in _site_css()

    timeline = html_render.render_academic_conferences_html(Config(), content)
    assert "会议</a> · Academic Conference Review" in timeline
    assert timeline.index('id="year-2026"') < timeline.index('id="year-2010"')
    assert "技术演进" not in timeline.split("<h1>", 1)[1].split("</h1>", 1)[0]
    assert f'{content["paper_count"]} 篇论文' not in timeline
    assert "查看 2026 年相关论文" in timeline
    assert 'href="conference-papers.html">查看全部相关论文</a>' not in timeline
    assert "查看长期演进概述" not in timeline
    assert 'class="forum-overview-mobile"' not in timeline

    papers = html_render.render_conference_papers_html(Config(), content)
    assert "会议</a> · Academic Paper Archive" in papers
    assert "会议</a> · <a href=\"academic-conferences.html\">学术会议</a>" not in papers
    paper_tags = [part.split(">", 1)[0]
                  for part in papers.split("data-conference-item")[1:]]
    assert len(paper_tags) == content["paper_count"]
    assert all(" hidden" not in tag for tag in paper_tags[:10])
    assert all(" hidden" in tag for tag in paper_tags[10:])
    assert "data-conference-browser" in papers
    assert 'role="group" aria-label="论文筛选"' in papers
    assert papers.count(
        f'value="" data-option-label="全部">全部（{content["paper_count"]}）</option>'
    ) == 2
    for venue in content["paper_venues"]:
        assert (f'value="{venue["key"]}" data-option-label="{venue["name"]}">'
                f'{venue["name"]}（{venue["paper_count"]}）</option>') in papers
    for year in content["years"]:
        assert (f'value="{year}" data-option-label="{year}">'
                f'{year}（{content["year_counts"][year]}）</option>') in papers
    assert papers.count('aria-live="polite" aria-atomic="true"') == 2
    assert "编辑点评" not in papers and "<strong>点评</strong>" in papers
    assert "<strong>作者</strong>" not in papers
    assert papers.count("<strong>单位</strong>") == content["paper_count"]
    assert papers.count('class="paper-credit-more"') == sum(
        len(paper["institutions"]) > conferences.INSTITUTION_DISPLAY_LIMIT
        for paper in content["papers"]
    )
    assert "另 1 家单位" in papers and "另 2 家单位" in papers
    assert "代表性标记只用于辅助观察技术脉络" in papers
    assert "conference-papers.html?year=2025" not in papers

    assets = (html_render.ASSETS_DIR / "site.js").read_text(encoding="utf-8")
    assert "new URLSearchParams(window.location.search)" in assets
    assert "matching.length" in assets and "篇论文" in assets
    assert "facetCounts(venue,'venue','year',year.value)" in assets
    assert "facetCounts(year,'year','venue',venue.value)" in assets
    assert "option.disabled=" in assets and "optionCount===0" in assets
    assert "window.history.replaceState" in assets
    assert "url.searchParams.set" in assets and "url.searchParams.delete" in assets
    assert "[hidden]{display:none!important}" in _site_css()


def test_conference_title_filter_is_conservative():
    assert conferences.is_candidate_title(
        "Accelerating Nested Virtualization with HyperTurtle"
    )
    assert conferences.is_candidate_title(
        "A KVM Hypervisor for ARM"
    )
    assert not conferences.is_candidate_title(
        "Java Virtual Machine Garbage Collection"
    )
    assert not conferences.is_candidate_title(
        "A Virtual Reality Display"
    )


def test_conference_schema_separates_catalogue_and_editor_review(tmp_path):
    conn = db.connect(tmp_path / "conference.db")
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert {"conference_editions", "conference_papers",
            "conference_reviews"} <= tables
    assert conferences.import_editor_reviews(conn) >= 80
    assert conn.execute("SELECT COUNT(*) FROM conference_reviews").fetchone()[0] >= 80
    conn.close()


def test_conference_static_xml_parser_uses_complete_edition(monkeypatch):
    payload = b"""<bht><dblpcites><r><inproceedings key="conf/vee/T25">
      <author>Alice</author><title>A KVM Hypervisor.</title><year>2025</year>
      <ee>https://doi.org/10.1/test</ee><url>db/conf/vee/vee2025.html#T25</url>
    </inproceedings></r></dblpcites></bht>"""
    monkeypatch.setattr(conferences, "_get_dblp_xml", lambda _s, _u: payload)
    rows = conferences.fetch_dblp_edition(object(), "vee", 2025)
    assert rows and rows[0]["title"] == "A KVM Hypervisor"
    assert rows[0]["authors"] == ["Alice"]
    assert rows[0]["doi"] == "10.1/test"


def test_kvm_forum_uses_conference_navigation():
    editions = [{
        "year": 2025, "url": "https://example.com/2025", "titles": ["Talk"],
        "analysis": {"headline": "H", "themes": [], "summary": "S"},
    }]
    analysis = {"headline": "H", "overview": "O", "model": "M",
                "method": "标题分析。", "eras": []}
    page = html_render.render_kvm_forum_html(Config(), editions, analysis)
    assert 'href="conferences.html" class="on">会议</a>' in page
    assert ">KVM Forum</a>" not in page.split("</nav>", 1)[0]
    assert "会议</a> · KVM Forum Review" in page
    assert "会议</a> · Conference Archive" not in page


def test_kvm_wiki_parser_uses_title_columns_and_splits_lightning_talks():
    wiki = """
{| border="1"
! !! colspan="2"|Track 1 !! colspan="2"|Track 2 !! colspan="2"|Track 3
|-
! Time !! Title !! Speaker !! Title !! Speaker
|-
|1:00pm || Talk A || Alice || Talk B || Bob || Talk C || Carol
|-
|2:00pm || Lightning Talks: [[Media:a.pdf | Short A]] * [[Media:b.pdf | Short B]] || A/B || ||
|-
|3:00pm || colspan="4" align="center"|Break
|}
"""
    assert kvm_forum._parse_wiki_titles(wiki) == [
        "Talk A", "Talk B", "Talk C", "Short A", "Short B",
    ]


def test_kvm_wiki_list_parser_stops_before_page_appendices():
    wiki = """
== Videos and Slides ==
* Useful virtualization topic by Alice ([https://example.com/video video])
== Photos ==
* https://example.com/photo
== Blogs / News Reports ==
* https://example.com/blog
"""
    assert kvm_forum._parse_wiki_titles(wiki) == ["Useful virtualization topic"]


def test_scheduler_uses_just_finished_periods():
    tz = ZoneInfo("Asia/Shanghai")
    daily_now = datetime(2026, 7, 15, 0, 15, tzinfo=tz)
    assert ("daily", ["daily", "2026-07-14", "--no-fetch", "--require-ai"]) in scheduler.scheduled_commands(
        Config(), daily_now
    )
    weekly_now = datetime(2026, 7, 20, 0, 25, tzinfo=tz)
    commands = scheduler.scheduled_commands(Config(), weekly_now)
    assert any(name == "weekly" and "--no-fetch" in command and "--require-ai" in command
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


def test_report_generation_state_is_cleared_by_atomic_publish(tmp_db):
    db.set_report_generation_state(
        tmp_db, "daily", "2026-07-15", status="running", attempt=1,
        scheduled_at="2026-07-16T00:15:00+08:00",
    )
    assert db.get_report_generation_state(tmp_db, "daily", "2026-07-15")
    db.save_report(tmp_db, "daily", "2026-07-15", {
        "period": "daily", "period_key": "2026-07-15", "fallback": False,
    }, "Asia/Shanghai", model="deepseek-v4-flash")
    assert db.get_report_generation_state(tmp_db, "daily", "2026-07-15") is None


def test_pending_report_is_separate_from_published_lists_and_rss(tmp_db):
    db.set_report_generation_state(
        tmp_db, "daily", "2026-07-16", status="retry_wait", attempt=1,
        scheduled_at="2026-07-17T00:15:00+08:00",
        retry_at="2026-07-17T00:30:00+08:00",
    )
    context = web_server._index_context(tmp_db)
    assert context["daily"] == []
    assert context["generation_states"]["daily"][0]["title"] == "AI 点评正在重新生成"
    page = html_render.render_index_html(Config(), context)
    assert "2026-07-16 · AI 点评正在重新生成" in page
    assert "最近 0 期" in page
    assert "2026-07-16" not in rss.report_feed(tmp_db, Config(), "daily")

    state = web_server._generation_state_view(
        db.get_report_generation_state(tmp_db, "daily", "2026-07-16")
    )
    detail = html_render.render_report_pending_html(Config(), state)
    assert '<meta name="robots" content="noindex, nofollow">' in detail
    assert "页面将在 60 秒后自动刷新" in detail
    archive = html_render.render_archive_html(Config(), "daily", [], [state])
    assert "共 0 期已发布报告" in archive
    assert "AI 点评正在重新生成" in archive


def test_legacy_fallback_is_not_public_or_used_as_topic_evidence(tmp_db):
    db.save_report(tmp_db, "daily", "2026-07-15", {
        "period": "daily", "period_key": "2026-07-15", "fallback": True,
        "headline": "模板摘要", "overview": [], "sections": [{
            "key": "qemu", "name": "QEMU", "items": [{
                "url": "https://example.com/fallback", "summary": "非 AI 摘要",
            }],
        }],
    }, "Asia/Shanghai", model="fallback")
    assert web_server._list_reports(tmp_db, "daily") == []
    assert "模板摘要" not in rss.report_feed(tmp_db, Config(), "daily")
    assert "https://example.com/fallback" not in topics._report_mentions(tmp_db)


def test_deferred_fallback_is_not_published(tmp_db, monkeypatch):
    monkeypatch.setattr(llm_provider, "get_provider", lambda _config: None)
    content = report.generate(
        tmp_db, Config(), "daily", "2026-07-12", publish_fallback=False,
    )
    assert content["fallback"] is True
    assert db.get_report(tmp_db, "daily", "2026-07-12") is None


def test_v4_flash_output_budgets_leave_room_for_reasoning():
    assert report.MAX_TOKENS == {
        "daily": 32768, "weekly": 49152, "monthly": 65536,
    }
    assert report.MAX_RETRY_TOKENS <= 384000
    assert report.EXCERPT_LEN == {"daily": 600, "weekly": 600, "monthly": 600}


def test_llm_usage_is_accumulated_across_json_retries():
    total = {}
    report._merge_usage(total, {
        "prompt_tokens": 100, "completion_tokens": 200,
        "prompt_tokens_details": {"cached_tokens": 40},
    })
    report._merge_usage(total, {
        "prompt_tokens": 80, "completion_tokens": 120,
        "prompt_tokens_details": {"cached_tokens": 30},
    })
    assert total == {
        "prompt_tokens": 180, "completion_tokens": 320,
        "prompt_tokens_details": {"cached_tokens": 70},
    }
    assert report._complete_llm_json({"sections": []}, "stop") is True
    assert report._complete_llm_json({"sections": []}, "length") is False


def test_scheduler_restore_marks_exhausted_report_completed(tmp_path):
    config = Config(storage=Storage(db_path=tmp_path / "scheduler-retry.db"))
    conn = db.connect(config.db_path)
    db.set_report_generation_state(
        conn, "daily", "2026-07-15", status="running",
        attempt=config.schedule.retry_limit,
        scheduled_at="2026-07-16T00:15:00+08:00",
    )
    conn.close()
    now = datetime(2026, 7, 16, 1, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    pending, exhausted = scheduler._restore_report_retries(
        config, ZoneInfo("Asia/Shanghai"), now,
    )
    assert pending == {}
    assert exhausted == {"daily:2026-07-15"}
    conn = db.connect(config.db_path)
    assert db.get_report_generation_state(
        conn, "daily", "2026-07-15"
    )["status"] == "failed"
    conn.close()


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


def _save_report_mentions(conn, period, period_key, urls, summary=None):
    items = [{"url": url, "summary": summary or f"{period} 精选摘要", "impact": "影响说明"}
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
