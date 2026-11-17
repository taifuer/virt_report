"""Presentation refresh must preserve exports newer than the local database."""
from pathlib import Path

import pytest

from scripts.refresh_static_presentation import (
    Document, protected_report_parts, refresh_listing_heading, refresh_page_chrome,
    refresh_report_html, refresh_topic_html,
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


@pytest.mark.parametrize('attributes,source', [
    ('', '<a href="https://example.com/patch">中文标题</a>'),
    (' id="historical-anchor"', '<a href="https://example.com/patch">中文标题</a>'),
    ('', '中文标题'),
    ('', '<a href="javascript:alert(1)">中文标题</a>'),
])
def test_snapshot_refresh_shows_original_titles_and_removes_share_rows(attributes, source):
    original = '[PATCH v3 01/33] target-arm: &lt;tag&gt; &amp; queue'
    disclosure = ('<details style="color:gray"><summary>查看原始标题</summary>'
                  f'<span>{original}</span></details>')
    crossposts = '<details class="item-evidence-note"><summary>同文跨列表归档</summary>QEMU · KVM</details>'
    html = (f'<section data-project="qemu"><article class="dyn"{attributes}>'
            f'<div class="d-heading"><div class="d-title">{source}</div></div>'
            '<div class="d-meta">邮件列表 · 2026-11-16</div>'
            '<p class="d-sum">原始点评保持。</p>' + disclosure + crossposts +
            '<div class="item-permalink-row"><a href="#historical-anchor" '
            'data-copy-report-link>分享</a><span data-copy-status></span></div>'
            '</article></section>')
    refreshed, targets = refresh_report_html(html)
    paragraph = f'<p class="d-original-title">{original}</p>'
    assert paragraph in refreshed
    assert refreshed.index('class="d-heading"') < refreshed.index(paragraph) < refreshed.index('class="d-meta"')
    assert '查看原始标题' not in refreshed and 'item-permalink-row' not in refreshed
    assert 'data-copy-report-link' not in refreshed and 'data-copy-status' not in refreshed
    assert crossposts in refreshed
    assert protected_report_parts(refreshed) == protected_report_parts(html)
    if attributes:
        assert attributes in refreshed
        assert targets[('qemu', 'https://example.com/patch')] == 'historical-anchor'
    assert refresh_report_html(refreshed) == (refreshed, targets)


def test_snapshot_guard_still_detects_changed_original_title_content():
    html = '<article class="dyn"><p class="d-original-title">[PATCH] 原题</p></article>'
    changed = html.replace('[PATCH] 原题', '[PATCH] 改写')
    assert protected_report_parts(html) != protected_report_parts(changed)


def test_all_report_snapshots_show_original_titles_without_share_footers():
    site = Path(__file__).resolve().parents[1] / 'site'
    title_count = 0
    for period in ('daily', 'weekly', 'monthly'):
        for path in (site / period).glob('*.html'):
            html = path.read_text(encoding='utf-8')
            assert 'data-copy-report-link' not in html and 'item-permalink-row' not in html, path
            assert '查看原始标题' not in html, path
            doc = Document(html)
            for node in doc.nodes:
                if not node.has_class('d-original-title'):
                    continue
                assert node.tag == 'p' and node.parent.has_class('dyn'), path
                siblings = [child for child in doc.descendants(node.parent) if child.parent is node.parent]
                position = siblings.index(node)
                assert siblings[position - 1].has_class('d-heading'), path
                assert siblings[position + 1].has_class('d-meta'), path
                title_count += 1
    assert title_count > 0


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


def test_snapshot_chrome_uses_shared_navigation_without_touching_article():
    body = '<main id="main"><p>较新的历史点评 &amp; 日期：2026-10-12</p></main>'
    html = '<header class="site-head">old</header>' + body + '<footer class="site-foot">old</footer>'
    result = refresh_page_chrome(html, "daily/2026-10-12.html", "virt-report")
    assert body in result
    assert 'href="../mechanisms.html"' in result
    assert 'href="../versions.html"' in result
    assert 'href="../daily/index.html" class="on" aria-current="page"' in result
    assert '<summary class="on">报告' in result
    assert 'data-site-menu-toggle' in result
    assert '虚拟化社区动态</span><span class="foot-note">' in result
    assert '数据来自社区，分析仅供参考。<a href="../metrics.html">运行状态</a>' in result
    assert refresh_page_chrome(result, "daily/2026-10-12.html", "virt-report") == result


def test_snapshot_chrome_tracks_principle_and_version_routes():
    html = '<header class="site-head"></header>'
    for path, target in (("mechanisms/qemu-memory-migration.html", "../mechanisms.html"),
                         ("kvm-forum/2026.html", "../conferences.html"),
                         ("versions.html", "versions.html")):
        result = refresh_page_chrome(html, path, "virt-report")
        assert f'href="{target}" class="on" aria-current="page"' in result


def test_listing_heading_refresh_preserves_entries_breadcrumbs_and_report_dates():
    label = '<div class="kicker">Report Archive</div>'
    body = '<h1>日报</h1><p>共 99 期</p></header><a href="2026-11-11.html">原记录</a>'
    html = '<header>' + label + body
    for period in ("daily", "weekly", "monthly"):
        relative = f"{period}/index.html"
        result = refresh_listing_heading(html, relative)
        assert result == '<header>' + body
        assert refresh_listing_heading(result, relative) == result
    assert refresh_listing_heading(html, "daily/2026-11-11.html") == html
    breadcrumb = '<header><div class="kicker"><a href="topics.html">Topic Tracker</a></div></header>'
    assert refresh_listing_heading(breadcrumb, "topics.html") == breadcrumb
    report = '<header><div class="kicker">Daily Brief · 2026-11-11</div></header>'
    assert refresh_listing_heading(report, "daily/index.html") == report


def test_topic_and_conference_snapshots_use_current_shared_header():
    site = Path(__file__).resolve().parents[1] / "site"
    for relative in ("topics.html", "topics/migration/index.html", "conferences.html",
                     "academic-conferences.html", "conference-papers.html", "kvm-forum.html",
                     "kvm-forum/2026.html"):
        html = (site / relative).read_text(encoding="utf-8")
        assert refresh_page_chrome(html, relative, "virt-report") == html, relative


def test_all_static_footers_group_copyright_and_note_with_relative_status_link():
    from virt_report import mechanisms

    site = Path(__file__).resolve().parents[1] / "site"
    redirects = {f"mechanisms/{entry['slug']}.html": entry['target']
                 for entry in mechanisms.redirects()}
    for path in sorted(site.rglob("*.html")):
        html = path.read_text(encoding="utf-8")
        relative = path.relative_to(site).as_posix()
        if relative == "kvm/index.html":
            # The old standalone catalogue is now a category redirect.
            target = "../mechanisms.html#kvm-internals"
            assert f'<link rel="canonical" href="{target}">' in html
            assert f'<meta http-equiv="refresh" content="0; url={target}">' in html
            assert '<meta name="robots" content="noindex, follow">' in html
            assert f'<a href="{target}">KVM 原理</a>' in html
            continue
        target = redirects.get(relative)
        if target:
            # Merged URLs are small redirect documents, not content pages.
            assert f'<link rel="canonical" href="{target}.html">' in html
            assert 'src="../assets/mechanism-redirect.js"' in html
            continue
        footer = html.split('<footer class="site-foot">', 1)[1].split("</footer>", 1)[0]
        root = "../" * len(path.relative_to(site).parts[:-1])
        assert '<span class="foot-copyright">© 2026 ' in footer, path
        assert '虚拟化社区动态</span><span class="foot-note">' in footer, path
        assert (f'数据来自社区，分析仅供参考。<a href="{root}metrics.html">'
                '运行状态</a></span>') in footer, path
        assert "虚拟化社区动态。" not in footer, path
