"""The migration overview fits inline; an explicit viewer provides detail."""

from html.parser import HTMLParser
from pathlib import Path
import re

from virt_report.config import Config
from virt_report.render import render


class DiagramParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_diagram = False
        self.nodes = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "figure" and attrs.get("class") == "mechanism-wire-diagram":
            self.in_diagram = True
        if self.in_diagram:
            self.nodes.append((tag, attrs))

    def handle_endtag(self, tag):
        if tag == "figure":
            self.in_diagram = False


def test_wire_diagram_offers_explicit_zoom_and_original_fallback():
    page = render.render_mechanisms_html(Config(), "qemu-migration-stream")
    parsed = DiagramParser()
    parsed.feed(page)
    figure = next(attrs for tag, attrs in parsed.nodes if tag == "figure")
    assert figure["data-mechanism-view"] == "diagram"
    button = next(attrs for tag, attrs in parsed.nodes if tag == "button")
    assert button["type"] == "button"
    assert "data-mechanism-expand" in button
    assert "hidden" in button
    assert "QEMU 迁移流与字段布局" in button["aria-label"]
    assert any(tag == "a" and "migration-wire-layout.svg" in attrs["href"]
               for tag, attrs in parsed.nodes)
    assert "图内可左右滚动" not in page
    assert "点击放大查看字段细节" in page


def test_wire_diagram_inline_image_has_no_fixed_minimum_width():
    asset = Path(render.__file__).parent / "assets/mechanisms.css"
    css = asset.read_text()
    rule = re.search(r"\.mechanism-wire-scroll img\{([^}]+)\}", css).group(1)
    declarations = dict(item.split(":", 1) for item in rule.split(";") if item)
    assert declarations["width"] == "100%"
    assert declarations["min-width"] == "0"
    assert declarations["max-width"] == "100%"
    assert declarations["height"] == "auto"
