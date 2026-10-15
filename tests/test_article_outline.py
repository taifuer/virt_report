"""The reading hierarchy is generated once, identically in prose and contents."""
import re
from urllib.parse import urlsplit

import pytest

from virt_report import mechanisms
from virt_report.config import Config
from virt_report.render import render
from virt_report.render.article_outline import chinese_number, prepare_outline


@pytest.mark.parametrize("value,label", [(1, "一"), (9, "九"), (10, "十"),
                                         (11, "十一"), (20, "二十"), (32, "三十二"), (99, "九十九")])
def test_chinese_section_number(value, label):
    assert chinese_number(value) == label


@pytest.mark.parametrize("value", [0, -1, 100])
def test_number_range_is_explicit(value):
    with pytest.raises(ValueError):
        chinese_number(value)


def test_subsections_keep_inline_code_ids_and_exclude_widget_titles():
    body = ('<section class="mechanism-section" id="path"><h2>执行 &amp; 恢复</h2>'
            '<h3>进入 <code>KVM_RUN</code></h3><img src="local.svg">'
            '<div class="lab"><h3>交互演示</h3><section><h3>状态</h3></section></div>'
            '<h3 id="stable">完成请求</h3></section>'
            '<section id="cost" class="mechanism-section"><h2>代价</h2><h3>CPU</h3></section>')
    html, toc = prepare_outline(body, [{"id": "path", "title": "执行 & 恢复"},
                                       {"id": "cost", "title": "代价"}])
    assert [s["number"] for s in toc] == ["一、", "二、"]
    assert toc[0]["children"] == [
        {"id": "path-part-1", "title": "进入 KVM_RUN", "number": "1. "},
        {"id": "stable", "title": "完成请求", "number": "2. "},
    ]
    assert toc[1]["children"][0]["number"] == ""
    assert '<h3 id="cost-part-1">CPU</h3>' in html
    assert '<h3>交互演示</h3>' in html
    assert '<h3>状态</h3>' in html
    assert '<h3 id="path-part-1"><span class="mechanism-heading-number">1. </span>进入 <code>KVM_RUN</code></h3>' in html
    assert '执行 &amp; 恢复' in html


@pytest.mark.parametrize('explicit_id', [False, True])
def test_single_subsection_keeps_its_anchor_without_body_or_toc_number(explicit_id):
    attribute = ' id="initialization"' if explicit_id else ''
    body = ('<section id="queue" class="mechanism-section"><h2>队列</h2>'
            f'<h3{attribute}>确认 <code>FEATURES_OK</code></h3>'
            '<div class="lab"><h3>交互标题</h3></div></section>')
    html, toc = prepare_outline(body, [{'id': 'queue', 'title': '队列'}])
    identifier = 'initialization' if explicit_id else 'queue-part-1'
    assert toc[0]['children'] == [
        {'id': identifier, 'title': '确认 FEATURES_OK', 'number': ''},
    ]
    assert f'<h3 id="{identifier}">确认 <code>FEATURES_OK</code></h3>' in html
    assert html.count('class="mechanism-heading-number"') == 1  # Only the h2.
    assert '<h3>交互标题</h3>' in html


def test_device_guide_single_subsection_matches_unnumbered_toc():
    page = render.render_mechanisms_html(Config(), 'device-virtualization')
    assert '<h3 id="queue-part-1">先协商格式，再启用队列</h3>' in page
    assert 'href="#queue-part-1">先协商格式，再启用队列</a>' in page
    assert 'href="#queue-part-1">1.' not in page


def test_incorrect_metadata_and_fragment_collision_fail_before_publication():
    body = '<section id="one" class="mechanism-section"><h2>正文</h2><h3>子节</h3></section>'
    with pytest.raises(ValueError, match="disagree"):
        prepare_outline(body, [{"id": "one", "title": "旧目录"}])
    with pytest.raises(ValueError, match="Duplicate"):
        prepare_outline(body + '<div id="one-part-1"></div>', [{"id": "one", "title": "正文"}])


@pytest.mark.parametrize("first,second", [("same", "same"), ("one", "different")])
def test_explicit_heading_ids_cannot_collide(first, second):
    body = (f'<section id="one" class="mechanism-section"><h2>正文</h2>'
            f'<h3 id="{first}">甲</h3><h3 id="{second}">乙</h3></section>')
    with pytest.raises(ValueError, match="Duplicate article anchor"):
        prepare_outline(body, [{"id": "one", "title": "正文"}])


@pytest.mark.parametrize("article", mechanisms.articles(), ids=lambda article: article["slug"])
def test_published_outline_matches_section_and_subsection_numbers_without_js(article):
    page = render.render_mechanisms_html(Config(), article["slug"])
    toc = re.search(r'<nav class="mechanism-toc".*?</nav>', page, re.S).group()
    for number, section in enumerate(article["sections"], 1):
        label = chinese_number(number) + "、"
        assert f'href="#{section["id"]}">{label}{section["title"]}</a>' in toc
    for fragment in re.findall(r'href="#([^"]+)"', toc):
        assert f'id="{fragment}"' in page
    assert 'counter(section' not in page  # Actual text, not CSS-only numbering.


def test_every_topic_has_a_complete_intro_and_detail_links():
    index = render.render_mechanisms_html(Config())
    for topic in mechanisms.topics():
        overview = topic["overview_article"]
        assert overview and overview["slug"] == topic["overview"]
        assert f'<a href="mechanisms/{overview["slug"]}.html">{topic["title"]}</a>' in index
        page = render.render_mechanisms_html(Config(), overview["slug"])
        for section in ("evolution", "reading", "sources"):
            assert f'id="{section}"' in page
        assert len(re.findall(r'href="[a-z-]+\.html"', page)) >= 3
        for article in topic["detail_articles"]:
            assert f'href="{article["slug"]}.html"' in page


def test_cross_article_reading_links_resolve_to_published_pages_and_anchors():
    pages = {article["slug"]: render.render_mechanisms_html(Config(), article["slug"])
             for article in mechanisms.articles()}
    for slug, page in pages.items():
        for href in re.findall(r'href="([^"]+)"', page):
            link = urlsplit(href)
            if link.scheme or link.netloc or not re.fullmatch(r'[a-z0-9-]+\.html', link.path):
                continue
            target = link.path.removesuffix('.html')
            assert target in pages, f'{slug}: missing article {href}'
            if link.fragment:
                assert f'id="{link.fragment}"' in pages[target], f'{slug}: missing anchor {href}'
