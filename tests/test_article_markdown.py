"""Markdown prose shares the existing source, diagram and outline contracts."""
import re

import pytest
from markupsafe import Markup

from virt_report.render.article_markdown import render_article_markdown
from virt_report.render.article_outline import prepare_outline


PREFACE = "## 前言 {#preface}\n\n```baselines\n```\n\n"


def render(source):
    return render_article_markdown(
        source,
        baselines='<p class="mechanism-baselines">QEMU 10.2.0</p>',
        diagrams={"test-path": lambda title, description:
                  Markup(f'<figure><b>{title}</b><figcaption>{description}</figcaption></figure>')},
        components={"_example_lab.html": lambda: '<div><h3>交互标题</h3></div>'},
    )


def test_markdown_sections_tables_links_code_and_outline():
    source = PREFACE + """源码 [实现][source]。

## DMA 映射 {#mapping}

| 字段 | 含义 |
| --- | --- |
| `iova` | 设备地址 |

### 地址准备

```c
if (address < limit) { map(address); }
```

### 回收

见[映射](#mapping)。

## 总结 {#summary}

### 后续阅读

[source]: https://example.com/source#L10
"""
    html, outline = prepare_outline(render(source), [
        {"id": "preface", "title": "前言"}, {"id": "mapping", "title": "DMA 映射"},
        {"id": "summary", "title": "总结"},
    ])
    assert '<th scope="col">字段</th>' in html
    assert '<code>iova</code>' in html
    assert 'address &lt; limit' in html
    assert 'href="https://example.com/source#L10"' in html
    assert html.count('<section ') == html.count('</section>') == 3
    assert [child['number'] for child in outline[1]['children']] == ['1. ', '2. ']
    assert '<h3 id="summary-part-1">后续阅读</h3>' in html
    assert '{#' not in html


def test_fenced_components_reuse_html_without_becoming_code_or_outline_headings():
    html, outline = prepare_outline(render(PREFACE + """
```diagram test-path
地址路径
设备到内存。
权限随映射检查。
```

```component _example_lab.html
```
"""), [{"id": "preface", "title": "前言"}])
    assert '<figure><b>地址路径</b><figcaption>设备到内存。 权限随映射检查。</figcaption></figure>' in html
    assert '<div><h3>交互标题</h3></div>' in html
    assert not outline[0]['children']
    assert 'language-component' not in html


def test_prose_is_not_jinja_or_raw_html_and_ordinary_fences_stay_literal():
    html = render(PREFACE + """
{{ config.secret }} <script>alert(1)</script>

```jinja
{% include 'private.html' %}
```

[unsafe](javascript:alert(1))
""")
    assert '{{ config.secret }}' in html
    assert '&lt;script&gt;' in html and '<script>' not in html
    assert "{% include 'private.html' %}" in html
    assert 'href="javascript:' not in html


@pytest.mark.parametrize('source,match', [
    ('', 'requires'), ('prose before heading', 'start'),
    ('## 标题\n', 'stable'), ('## 标题 {#other}\n', 'first'),
    ('## 前言 {#preface}\n', 'baselines'),
    (PREFACE + '# 第二个大标题', 'metadata'),
    (PREFACE + '> ## 嵌套 {#nested}\n', 'top-level'),
    (PREFACE + '```baselines\n```', 'once'),
    ('## 前言 {#preface}\n\n```baselines extra\n```', 'arguments'),
    ('## 前言 {#preface}\n\n```baselines\nbody\n```', 'arguments'),
    (PREFACE + '```diagram missing\nTitle\nDescription\n```', 'registered'),
    (PREFACE + '```diagram test-path\nOnly title\n```', 'description'),
    (PREFACE + '```component ../base.html\n```', 'allowlisted'),
    (PREFACE + '```component _example_lab.html\nbody\n```', 'no body'),
    (PREFACE + '> ```component _example_lab.html\n> ```', 'standalone'),
])
def test_invalid_editorial_markup_fails_before_publication(source, match):
    with pytest.raises(ValueError, match=match):
        render(source)


def test_duplicate_anchors_and_metadata_mismatch_still_fail_outline_validation():
    html = render(PREFACE + '## 重复 {#preface}\n\n内容。')
    with pytest.raises(ValueError, match='Duplicate'):
        prepare_outline(html, [])
    with pytest.raises(ValueError, match='disagree'):
        prepare_outline(render(PREFACE), [{"id": "preface", "title": "旧标题"}])


def test_optional_h3_anchor_preserves_bookmarks_when_migrating_existing_prose():
    html, outline = prepare_outline(render(PREFACE + '### 使用 `IOAS` {#stable}\n\n内容。'),
                                    [{"id": "preface", "title": "前言"}])
    assert '<h3 id="stable">使用 <code>IOAS</code></h3>' in html
    assert outline[0]['children'] == [{'id': 'stable', 'title': '使用 IOAS', 'number': ''}]
    with pytest.raises(ValueError, match='Duplicate'):
        prepare_outline(render(PREFACE + '### 重复 {#preface}\n'), [])


def test_real_diagram_macro_escapes_untrusted_looking_titles_and_captions():
    from virt_report.render.render import _env

    diagram = _env().get_template('_mechanism_diagram.html').make_module({
        'mechanism_diagrams': {'test-path': {'source': 'flowchart LR\nA-->B'}},
        'root': '../', 'mechanism_asset_version': 'test',
    }).diagram
    html = render_article_markdown(
        PREFACE + '```diagram test-path\n<script>alert(1)</script>\n<img onerror=alert(1)>\n```',
        baselines='<p>版本</p>',
        diagrams={'test-path': lambda title, desc: diagram('test-path', title, desc)},
        components={},
    )
    assert '<section id="preface"' in html
    assert '&lt;script&gt;' in html and '<script>' not in html
    assert 'alt="&lt;img onerror=alert(1)&gt;"' in html


def test_new_article_sources_are_included_in_distribution():
    from pathlib import Path
    import tomllib

    root = Path(__file__).resolve().parents[1]
    package = tomllib.loads((root / 'pyproject.toml').read_text())
    assert 'content/articles/*.md' in package['tool']['setuptools']['package-data']['virt_report']
    assert any(re.match(r'markdown-it-py\b', value) for value in package['project']['dependencies'])


@pytest.mark.parametrize('slug,related', [
    ('iommu-dma', 'qemu-vfio-dma'),
    ('iommufd', 'qemu-vfio-dma'),
    ('virtio-gpu', 'qemu-vfio-dma'),
    ('qemu-viommu', 'iommufd'),
    ('vfio-device-migration', 'qemu-vfio-dma'),
    ('kvm-nested-vmx', 'kvm-vcpu-run'),
])
def test_markdown_articles_use_pinned_sources_local_components_and_existing_navigation(slug, related):
    from virt_report import mechanisms
    from virt_report.config import Config
    from virt_report.render import render as pages

    article = mechanisms.get_article(slug)
    html = pages.render_mechanisms_html(Config(), slug)
    assert article['source'] == f'{slug}.md'
    assert article['components']
    assert html.count('data-mechanism-view="diagram"') >= 2
    assert 'data-mechanism-view="lab"' in html
    assert 'language-diagram' not in html and 'language-component' not in html
    assert f'href="{related}.html"' in html
    commits = re.findall(r'https://github.com/(?:qemu/qemu|torvalds/linux)/blob/([^/]+)/', html)
    assert commits
    assert set(commits) <= {baseline['commit'] for baseline in article['baselines']}


@pytest.mark.parametrize('field,value,match', [
    ('source', '../private.md', 'local Markdown'),
    ('source', '/tmp/private.md', 'local Markdown'),
    ('template', '_base.html', 'one local'),
    ('components', ['../_private_lab.html'], 'local lab'),
    ('components', ['base.html'], 'local lab'),
])
def test_article_metadata_cannot_load_arbitrary_markdown_or_template_paths(monkeypatch, field, value, match):
    from virt_report import mechanisms
    from virt_report.render import render as pages

    article = {**mechanisms.get_article('iommu-dma'), field: value}
    monkeypatch.setattr(mechanisms, 'get_article', lambda slug: article)
    with pytest.raises(ValueError, match=match):
        pages._render_mechanism_page.__wrapped__('test', 'iommu-dma')
