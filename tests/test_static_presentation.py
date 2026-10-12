"""Presentation refresh must preserve exports newer than the local database."""
from scripts.refresh_static_presentation import (
    Document, protected_report_parts, refresh_report_html, refresh_topic_html,
)
from virt_report.report_links import report_item_anchor


def test_snapshot_refresh_adds_stable_targets_and_preserves_report_prose():
    html = '''<div class="report-layout"><div class="report-filters"><div class="project-tools"><button data-project-filter="all">全部</button><button data-item-filter="bug">缺陷</button></div></div><section data-project="qemu"><article class="dyn"><div class="d-heading"><div class="d-title"><a href="https://example.com/patch?a=1&amp;b=2">中文 &amp; 原题</a></div></div><div class="d-meta">2026-09-01</div><p class="d-sum">[T001] 历史点评 &lt;code&gt;：不改写。</p><details><summary>原题</summary>source</details></article></section></div>'''
    refreshed, targets = refresh_report_html(html)
    anchor = report_item_anchor("qemu-devel", "https://example.com/patch?a=1&b=2")
    assert targets == {("qemu", "https://example.com/patch?a=1&b=2"): anchor}
    assert f'id="{anchor}"' in refreshed
    assert 'data-project-filter="all" aria-pressed="true"' in refreshed
    assert 'data-item-filter="bug" aria-pressed="false"' in refreshed
    assert 'data-report-filter-status>显示 1 / 1 条精选' in refreshed
    assert 'data-report-filter-empty hidden' in refreshed
    assert protected_report_parts(refreshed) == protected_report_parts(html)
    assert "[T001] 历史点评" in refreshed  # No candidate mapping: do not guess.
    again, repeated = refresh_report_html(refreshed)
    assert again == refreshed and repeated == targets


def test_topic_snapshot_backlinks_only_use_existing_exact_report_targets():
    html = '''<article class="topic-item"><div class="topic-item-head"><a href="https://example.com/series-v2">series title</a></div><div class="topic-item-meta-primary"><span class="tag">QEMU</span></div><span>收录于 2026-08、2026-W32、2026-09-01</span><p>原始点评保持。</p></article>'''
    anchor = report_item_anchor("qemu", "https://example.com/series-v2")
    reports = {"monthly/2026-08.html": {("qemu", "https://example.com/series-v2"): anchor},
               "weekly/2026-W32.html": {("qemu", "https://example.com/series-v1"): "older-anchor"}}
    refreshed, linked = refresh_topic_html(html, root="../../", reports=reports)
    assert linked == 2
    assert f'href="../../monthly/2026-08.html#{anchor}"' in refreshed
    assert 'href="../../weekly/2026-W32.html"' in refreshed
    assert "older-anchor" not in refreshed
    assert "daily/2026-09-01.html" not in refreshed
    assert "原始点评保持。" in refreshed
    assert refresh_topic_html(refreshed, root="../../", reports=reports) == (refreshed, 0)


def test_document_preserves_offsets_for_entities_comments_svg_and_scripts():
    html = '''<!-- 单独保留 -->\n<div><svg><path d="M1 1"/></svg><p>QEMU &amp; KVM</p><script>if(a<3){}</script></div>'''
    doc = Document(html)
    paragraph = next(node for node in doc.nodes if node.tag == "p")
    assert html[paragraph.start:paragraph.end] == "<p>QEMU &amp; KVM</p>"
    assert doc.text(paragraph) == "QEMU & KVM"
    assert next(node for node in doc.nodes if node.tag == "script").end > paragraph.end
