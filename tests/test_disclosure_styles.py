"""Disclosure indicators stay beside their labels, not at the row's far edge."""
from datetime import date
import re

from virt_report import versions
from virt_report.config import Config, Storage
from virt_report.render import render


def test_about_updates_preserve_native_collapsed_disclosure():
    page = render.render_about_html(Config())
    disclosure = re.search(r'<details class="about-updates"[^>]*>(.*?)</details>', page, re.S)
    assert disclosure
    assert '<details class="about-updates">' in disclosure.group()
    assert '<summary><h2>更新记录</h2></summary>' in disclosure.group()


def test_year_indicator_immediately_follows_year_before_count(tmp_path):
    config = Config(storage=Storage(db_path=tmp_path / "report.db"))
    content = versions.load_content(config, today=date(2026, 10, 12))
    page = render.render_versions_html(config, content)
    headings = re.findall(r'<summary><h2 id="year-title-\d+">(.*?)</h2></summary>', page)
    assert headings
    for heading in headings:
        assert re.match(r'\d{4}<svg class="version-year-chevron"', heading)
        assert heading.index('</svg>') < heading.index('data-version-year-count')
        assert 'aria-hidden="true"' in heading
    assert '<details class="version-year-disclosure" data-version-disclosure open>' in page


def test_custom_disclosure_indicators_do_not_use_far_edge_alignment():
    css = (render.ASSETS_DIR / "site.css").read_text(encoding="utf-8")
    for selector in ('.about-updates>summary', '.nav-reports>summary'):
        rules = re.findall(re.escape(selector) + r'\{([^}]*)\}', css)
        assert rules
        assert not any('justify-content:space-between' in rule for rule in rules)
    icon = re.search(r'\.version-year-chevron\{([^}]*)\}', css).group(1)
    assert 'margin-left:auto' not in icon
    assert 'position:absolute' not in icon
    assert '.about-updates[open]>summary::after' in css
    assert '.version-year-disclosure[open] .version-year-chevron' in css
