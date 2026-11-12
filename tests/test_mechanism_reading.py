"""Independent articles, plain references and deferred examples."""
import re
import shutil
import subprocess

import pytest

from virt_report import mechanisms
from virt_report.config import Config
from virt_report.render import render
from virt_report.render.article_links import canonicalize_article_links, compact_references


def test_kvm_uses_the_same_category_and_article_navigation_as_other_components():
    topic = mechanisms.get_topic('kvm-internals')
    assert topic['overview_article']['slug'] == 'kvm-vcpu-run'
    assert 'book' not in topic
    assert 'books' not in mechanisms._content()
    html = render.render_mechanisms_html(Config())
    assert 'href="#kvm-internals"' in html
    assert 'href="mechanisms/kvm-vcpu-run.html">KVM 原理</a>' in html
    assert 'href="kvm/"' not in html
    assert '查看 KVM 文章' not in html


@pytest.mark.parametrize('article', mechanisms.articles(), ids=lambda a: a['slug'])
def test_independent_article_has_only_its_own_contents_navigation(article):
    html = render.render_mechanisms_html(Config(), article['slug'])
    assert html.count('class="mechanism-toc"') == 1
    toc = re.search(r'<nav class="mechanism-toc".*?</nav>', html, re.S).group()
    assert all(href.startswith('#') for href in re.findall(r'href="([^"]+)"', toc))
    assert 'mechanism-chapters' not in html
    assert 'mechanism-adjacent' not in html
    assert 'mechanism-page-outline' not in html
    assert 'rel="prev"' not in html and 'rel="next"' not in html
    topic = mechanisms.get_topic(article['topic'])
    assert f'href="../mechanisms.html#{topic["slug"]}">{topic["title"]}</a>' in html
    assert '版本与上游出处' not in html


def test_basics_article_is_not_presented_as_kvm_and_has_plain_curated_sources():
    html = render.render_mechanisms_html(Config(), 'virtualization-basics')
    breadcrumb = re.search(r'<div class="mechanism-breadcrumb">.*?</div>', html).group()
    assert '虚拟化基础' in breadcrumb and 'KVM 原理' not in breadcrumb
    sources = html.split('<section id="sources"', 1)[1].split('</section>', 1)[0]
    assert '<details' not in sources
    assert sources.count('<li>') == 3
    assert 'KVM 对象接口' not in sources
    assert '>QEMU 10.2.0</a>' not in sources
    assert '>Linux 6.18</a>' not in sources


def test_references_preserve_curated_sources_without_appending_inline_links():
    json_block = '<script type="application/json">{"source":"https://example.test/json"}</script>'
    body = ('<section id="preface"><h2>基础</h2>'
            '<p><a href="https://example.test/a?x=1&amp;y=2"><code>first()</code></a>，'
            '<a href="https://example.test/a?x=1&amp;y=2">重复</a>。'
            '<a href="https://example.test/b">已列来源</a>'
            '<a href="local.html#section">本地</a></p>'
            '<pre><code>&lt;a href="https://example.test/code"&gt;</code></pre>'
            + json_block + '</section><section id="sources"><h2>参考资料</h2>'
            '<ul><li><a href="https://example.test/b">原有资料</a></li></ul></section>')
    output = compact_references(body)
    preface, sources = output.split('<section id="sources">', 1)
    assert '<p><code>first()</code>，重复。已列来源<a href="local.html#section">本地</a>' in preface
    assert json_block in preface
    assert '&lt;a href="https://example.test/code"&gt;' in preface
    assert '<a href="https://' not in preface
    assert 'https://example.test/a' not in sources
    assert sources.count('href="https://example.test/b"') == 1
    assert '<details' not in sources
    assert sources == '<h2>参考资料</h2><ul><li><a href="https://example.test/b">原有资料</a></li></ul></section>'
    # An article without a references section must not lose any links.
    standalone = '<p><a href="https://example.test/a">来源</a></p>'
    assert compact_references(standalone) == standalone


def test_old_article_links_keep_parameters_and_use_mapped_fragments():
    redirects = ({'slug': 'old', 'target': 'new', 'anchors': {'reading': 'sources'}},)
    html = ('<a href="old.html?scenario=one&amp;step=two#reading">本章</a>'
            '<a href="../mechanisms/old.html#other">另一节</a>'
            '<a href="https://example.test/old.html">外部</a>')
    output = canonicalize_article_links(html, redirects)
    assert 'href="new.html?scenario=one&amp;step=two#sources"' in output
    assert 'href="../mechanisms/new.html#other"' in output
    assert 'href="https://example.test/old.html"' in output


@pytest.mark.parametrize('slug', [a['slug'] for a in mechanisms.articles() if a.get('execution_demo')])
def test_demo_code_is_inert_until_loader_initializes_it(slug):
    html = render.render_mechanisms_html(Config(), slug)
    manifest = re.search(r'<template id="mechanism-lab-assets">(.*?)</template>', html, re.S).group(1)
    scripts = re.findall(r'data-lab-name="([^"]+)"', manifest)
    assert scripts and len(scripts) == len(set(scripts))
    assert set(scripts) <= set(render.MECHANISM_ASSET_NAMES)
    outside = html.replace(manifest, '')
    for script in scripts:
        assert f'src="../assets/{script}' not in outside
    assert 'data-lab-loader' in outside
    assert 'data-execution-fallback' in html or 'data-vbr-fallback' in html
    if slug == 'qemu-virtio-blk':
        assert scripts.index('virtio-blk-read-model.js') < scripts.index('virtio-blk-read.js')
        assert 'execution-demo.js' not in scripts
    else:
        assert scripts.index('execution-model.js') < scripts.index('execution-demo.js')


def test_extra_article_assets_cannot_load_unregistered_scripts(monkeypatch):
    original = mechanisms.get_article
    article = {**original('cpu-virtualization'), 'extra_scripts': ['https://invalid.test/code.js']}
    monkeypatch.setattr(mechanisms, 'get_article',
                        lambda slug: article if slug == article['slug'] else original(slug))
    render._render_mechanism_page.cache_clear()
    try:
        with pytest.raises(ValueError, match='registered local files'):
            render.render_mechanisms_html(Config(), article['slug'])
    finally:
        render._render_mechanism_page.cache_clear()


@pytest.mark.skipif(shutil.which('node') is None, reason='Node is optional')
def test_lazy_loading_error_recovery_order_and_focus_without_browser():
    result = subprocess.run(['node', '--test', 'tests/test_mechanism_labs.cjs'],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
