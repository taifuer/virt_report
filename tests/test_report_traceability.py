"""Stable links, safe evidence rendering and explicit static search behavior."""
from copy import deepcopy

import pytest

from virt_report import db, search
from virt_report.config import Config
from virt_report.processing import topics
from virt_report.render import render
from virt_report.report_links import (
    prepare_report_links, report_item_anchor, report_item_href, source_url,
)


def _report() -> dict:
    return {
        "period": "weekly", "period_key": "2026-W40", "label": "2026 年第 40 周",
        "timezone": "Asia/Shanghai", "fallback": False, "model": "test",
        "stats": {"total_threads": 2, "total_items": 2, "ml_patches": 2,
                  "ml_rfc": 0, "gl_issues_opened": 0, "gl_mrs_merged": 0},
        "top_threads": [
            {"ref": "T001", "project": "qemu-devel", "subject": "migration: first <script>title</script>",
             "url": "https://example.org/first"},
            {"ref": "T002", "project": "kvm", "subject": "KVM: guest memory support",
             "url": "https://example.org/not-selected"},
        ],
        "overview": [{"project": "QEMU", "summary": "本期 [T001] 与 T002 的变化。<script>unsafe</script>"}],
        "period_analysis": [{"topic": "虚机状态", "progress": "推进 T001，讨论 T002。",
                             "unresolved": "跟进 [T002]。", "refs": ["T001", "T002"],
                             "evidence": [{"ref": "T001", "url": "javascript:alert(1)", "title": "forged"}]}],
        "sections": [{"key": "qemu", "name": "QEMU", "items": [{
            "ref": "T001", "title": "迁移点评", "original_title": "migration: first",
            "url": "https://forged.example/ignored", "summary": "解释迁移变化。",
            "category": "feature", "architectures": ["x86"], "source": "邮件列表",
        }]}], "watchlist": [],
    }


def test_item_anchor_is_canonical_and_independent_of_title_rank_and_ref():
    expected = report_item_anchor("qemu", "https://example.org/first")
    assert expected.startswith("item-")
    assert report_item_anchor("QEMU", "https://example.org/first#reply") == expected
    assert report_item_anchor("qemu-devel", "https://example.org/first") == expected
    assert report_item_anchor("KVM", "https://example.org/first") != expected
    content = _report()
    before = deepcopy(content)
    first = prepare_report_links(content)["sections"][0]["items"][0]
    assert first["anchor"] == expected
    assert first["url"] == "https://example.org/first"
    assert content == before
    content["sections"][0]["items"][0].update(ref="T047", title="新的中文标题")
    content["top_threads"][0].update(ref="T047", rank=47)
    assert prepare_report_links(content)["sections"][0]["items"][0]["anchor"] == expected


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/html,unsafe", "//evil.example/",
                                     "https://user:secret@example.org/", "https://example.org/a\nb", "u"])
def test_unsafe_sources_cannot_become_item_links(url):
    assert source_url(url) == ""
    assert report_item_anchor("qemu", url) == ""


def test_refs_link_to_selected_items_or_original_evidence_and_escape_all_text():
    content = _report()
    anchor = report_item_anchor("qemu", "https://example.org/first")
    page = render.render_report_html(Config(), content)
    assert f'id="{anchor}"' in page
    assert f'href="#{anchor}"' in page
    assert 'href="https://example.org/not-selected" target="_blank"' in page
    assert f'id="{report_item_anchor("kvm", "https://example.org/not-selected")}"' not in page
    assert "T001" not in page and "T002" not in page
    assert "javascript:" not in page and "forged.example" not in page
    assert "<script>unsafe" not in page and "&lt;script&gt;unsafe" in page
    assert "&lt;script&gt;title&lt;/script&gt;" in page
    assert 'data-copy-report-link aria-label="分享条目：迁移点评"' in page
    assert 'aria-pressed="true" data-project-filter="all"' in page
    assert 'aria-pressed="false" data-item-filter="feature"' in page
    assert 'data-report-filter-status>显示 1 / 1 条精选' in page
    assert 'data-report-filter-empty hidden' in page


def test_duplicate_selection_uses_one_target_and_unknown_refs_remain_plain_text():
    content = _report()
    content["sections"][0]["items"].append(dict(content["sections"][0]["items"][0]))
    content["overview"][0]["summary"] += " T999"
    prepared = prepare_report_links(content)
    assert prepared["sections"][0]["items"][1]["anchor"] == ""
    assert prepared["overview"][0]["summary_parts"][-1]["text"].endswith("T999")


def test_coverage_notes_and_no_activity_label_only_use_explicit_metadata():
    content = _report()
    assert "本期来源覆盖尚未核验" not in render.render_report_html(Config(), content)
    content["data_coverage"] = {"status": "unknown", "missing_sources": []}
    assert "本期来源覆盖尚未核验" in render.render_report_html(Config(), content)
    content["data_coverage"] = {"status": "partial", "missing_sources": ["QEMU 邮件列表"]}
    assert "QEMU 邮件列表来源缺少完整采集记录" in render.render_report_html(Config(), content)
    content.update(generation_status="no_activity", model=None)
    page = render.render_report_html(Config(), content)
    assert "本期无动态" in page and "AI 分析" not in page and "摘要由 AI" not in page


def test_search_and_topic_mentions_match_report_anchors(tmp_path):
    conn = db.connect(tmp_path / "traceability.db")
    content = _report()
    db.save_report(conn, "weekly", "2026-W40", content, "Asia/Shanghai", item_count=1, model="test")
    search.refresh_index(conn)
    results = search.search(conn, "迁移")
    expected = report_item_href("weekly", "2026-W40", "qemu", "https://example.org/first")
    assert results["items"][0]["report_refs"][0]["href"] == expected
    mentions = topics._report_mentions(conn)
    assert list(mentions) == ["https://example.org/first"]
    assert mentions["https://example.org/first"][0]["href"] == expected
    item = {"url": "https://example.org/first", "title": "migration: first", "project": "QEMU"}
    topics._apply_report_evidence(item, "热迁移", mentions[item["url"]])
    assert item["report_refs"] == [{"period_key": "2026-W40", "href": expected}]
    conn.close()


def test_historical_entries_hidden_by_current_limits_do_not_get_dead_fragments(tmp_path):
    conn = db.connect(tmp_path / "historical-limits.db")
    content = _report()
    content["top_threads"] = []
    content["sections"][0]["items"] = [
        {"ref": f"T{number:03}", "title": f"迁移讨论 {number}",
         "original_title": f"migration: review {number}",
         "url": f"https://example.org/{number}", "summary": "迁移点评内容。"}
        for number in range(1, 31)
    ]
    db.save_report(conn, "weekly", "2026-W40", content, "Asia/Shanghai", item_count=30, model="test")
    search.refresh_index(conn)
    results = search.search(conn, "迁移", per_page=30)
    refs = {item["url"]: item["report_refs"][0]["href"] for item in results["items"]}
    assert refs["https://example.org/27"].startswith("weekly/2026-W40.html#item-")
    assert refs["https://example.org/28"] == "weekly/2026-W40.html"
    mentions = topics._report_mentions(conn)
    assert mentions["https://example.org/30"][0]["href"] == "weekly/2026-W40.html"
    conn.close()


def test_static_search_disables_query_but_dynamic_search_remains_available(tmp_path):
    conn = db.connect(tmp_path / "search.db")
    result = search.search(conn, "")
    config = Config()
    config.render.output_dir = tmp_path / "site"
    static_page = render.render_search(config, result).read_text()
    dynamic_page = render.render_search_html(config, result)
    assert 'fieldset class="search-fieldset" disabled' in static_page
    assert "搜索需要运行 virt-report Web 服务" in static_page
    assert "search.html?q=" not in static_page
    assert 'data-search-form' not in static_page.split("{% block scripts", 1)[0].split("<script>", 1)[0]
    assert 'fieldset class="search-fieldset" disabled' not in dynamic_page
    assert "search.html?q=热迁移" in dynamic_page
    assert "method=\"get\" role=\"search\" data-search-form" in dynamic_page
    conn.close()


def test_filter_script_reveals_hidden_link_targets_and_announces_results():
    script = (render.ASSETS_DIR / "site.js").read_text()
    assert "setAttribute('aria-pressed',String(active))" in script
    assert "if(target.hidden||target.closest('.project-section')?.hidden)resetFilters()" in script
    assert "if(empty)empty.hidden=matched>0" in script
    assert "navigator.clipboard.writeText(link.href)" in script
    assert ".dyn:target" in (render.ASSETS_DIR / "site.css").read_text()


@pytest.mark.parametrize("title", [
    "qemu-iotests: migrate away from HMP", "migrate to Python 3.12",
    "migration from Meson to CMake", "decodetree migrations", "API migration cleanup",
])
def test_migration_classification_rejects_source_code_and_build_migrations(title):
    assert "migration" not in topics.classify_item({"title": title})


@pytest.mark.parametrize("title", [
    "migration: fix snapshot load", "migration/multifd: fix buffers", "multifd compression",
    "postcopy resume fix", "post-copy RAM transfer", "Reduce live migration downtime",
    "KVM: guest migration support", "KVM: arm64 migration fix", "Migrate virtual machine state", "优化虚机迁移链路",
])
def test_migration_classification_keeps_explicit_vm_evidence(title):
    assert "migration" in topics.classify_item({"title": title})
