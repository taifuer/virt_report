"""Offline diagram validation, rendering contracts and reader controls."""
import re
from xml.etree import ElementTree

import pytest

from scripts.render_mechanism_diagrams import prepare_svg
from virt_report import mechanisms
from virt_report.config import Config
from virt_report.render import render


SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="-10 -20 640 480" '
       'width="100%"><title>标题</title><desc>说明</desc>{}</svg>')


def test_offline_svg_preserves_natural_dimensions_for_viewer_zoom():
    root = ElementTree.fromstring(prepare_svg(SVG.format('<path id="arrow" d="M0 0"/>')))
    assert root.get("width") == "640"
    assert root.get("height") == "480"
    assert root.get("viewBox") == "-10 -20 640 480"


@pytest.mark.parametrize("content", [
    '<script>alert(1)</script>', '<foreignObject>text</foreignObject>',
    '<path onclick="alert(1)"/>', '<image href="https://example.com/image.png"/>',
    '<image href="data:image/png;base64,test"/>',
])
def test_offline_svg_rejects_external_or_active_content(content):
    with pytest.raises(ValueError):
        prepare_svg(SVG.format(content))


@pytest.mark.parametrize("bad", [
    SVG.replace('<title>标题</title>', ''), SVG.replace('<desc>说明</desc>', '<desc/>'),
    SVG.replace('640 480', '0 480'), SVG.replace('640 480', 'nan 480'),
    SVG.replace('640 480', '640'),
])
def test_offline_svg_requires_accessibility_and_valid_geometry(bad):
    with pytest.raises(ValueError):
        prepare_svg(bad.format(''))


def test_diagram_registry_matches_sources_and_exported_assets():
    sources = render.ASSETS_DIR.parents[1] / "content" / "diagrams"
    assert set(render.MECHANISM_DIAGRAM_NAMES) == {path.stem for path in sources.glob('*.mmd')}
    for name in render.MECHANISM_DIAGRAM_NAMES:
        assert f"{name}.svg" in render.MECHANISM_ASSET_NAMES
        svg = (render.ASSETS_DIR / f"{name}.svg").read_text()
        root = ElementTree.fromstring(svg)
        assert float(root.get("width")) > 0
        assert float(root.get("height")) > 0


def test_inline_diagrams_do_not_upscale_or_fill_multiple_screens():
    css = (render.ASSETS_DIR / "mechanisms.css").read_text()
    rule = re.search(r"\.mechanism-diagram-scroll img\{([^}]+)\}", css).group(1)
    assert "width:auto;" in rule
    assert "height:auto;" in rule
    assert "max-width:100%;" in rule
    assert "max-height:min(640px,70svh);" in rule
    assert "margin-inline:auto" in rule
    # Inline constraints must not affect the separate fullscreen image.
    viewer = (render.ASSETS_DIR / "mechanism-viewer.css").read_text()
    assert ".mechanism-viewer-image" in viewer
    assert "max-height:" not in viewer.split(".mechanism-viewer-image{", 1)[1].split("}", 1)[0]


@pytest.mark.parametrize("slug", [article['slug'] for article in mechanisms.articles()])
def test_article_diagrams_are_local_with_progressive_fullscreen_controls(slug):
    page = render.render_mechanisms_html(Config(), slug)
    assert 'data-mechanism-view="diagram"' in page
    assert 'data-mechanism-expand' in page
    assert 'data-mechanism-title' in page
    assert 'Mermaid 源码' in page
    assert '打开原图</a>' in page
    # Mermaid is a build-time authoring tool, never a reader's network dependency.
    assert 'mermaid.min.js' not in page
    assert 'cdn.jsdelivr.net' not in page


def test_reader_only_article_has_no_placeholder_script_or_stylesheet():
    page = render.render_mechanisms_html(Config(), "kvm-vcpu-run")
    assert page
    assert '../assets/?v=' not in page
    assert 'assets/mechanisms.js?' not in page
    assert 'assets/mechanism-viewer.js?' in page
    for marker in ('struct kvm_run', 'KVM_RUN', 'KVM_EXIT_MMIO', 'immediate_exit'):
        assert marker in page


def test_related_article_links_resolve_to_published_articles_and_sections():
    pages = {f'{article["slug"]}.html': render.render_mechanisms_html(Config(), article['slug'])
             for article in mechanisms.articles()}
    for page in pages.values():
        for path, fragment in re.findall(r'href="([a-z0-9-]+\.html)(#[^"]+)?"', page):
            assert path in pages, path
            if fragment:
                assert f'id="{fragment[1:]}"' in pages[path], path + fragment
