"""The shared navigation remains accessible on every content page."""
from pathlib import Path
import shutil
import subprocess

import pytest

from virt_report.config import Config
from virt_report.render import render
from virt_report.versions import Document


def navigation(page):
    document = Document(page).root
    return next(node for node in document.find("nav")
                if node.attrs.get("class") == "main-nav")


def test_navigation_groups_reports_and_exposes_reference_pages():
    page = render.render_index_html(Config(), render.build_home_context([], [], []))
    nav = navigation(page)
    reports = next(node for node in nav.find("details")
                   if "data-report-menu" in node.attrs)
    assert reports.find("summary")[0].text() == "报告"
    assert [node.text() for node in reports.find("a")] == ["日报", "周报", "月报"]
    direct_links = [node for node in nav.children if getattr(node, "tag", "") == "a"]
    assert [node.text() for node in direct_links] == ["首页", "专题", "会议", "版本", "原理", "关于", ""]
    assert direct_links[0].attrs["aria-current"] == "page"
    assert 'aria-controls="site-navigation" hidden' in page
    assert 'id="site-navigation" hidden' not in page
    assert 'class="brand-sub">虚拟化社区动态' in page


def test_mobile_brand_keeps_subtitle_outside_the_home_link():
    page = render.render_index_html(Config(), render.build_home_context([], [], []))
    document = Document(page).root
    lockup = next(node for node in document.find("div")
                  if node.attrs.get("class") == "brand-lockup")
    links = lockup.find("a")
    assert len(links) == 1
    assert links[0].text() == "virt-report"
    assert links[0].attrs["href"] == "index.html"
    assert links[0].find("img")[0].attrs["class"] == "brand-mark"
    assert any(getattr(child, "attrs", {}).get("class") == "brand-sub"
               for child in lockup.children)
    css = (render.ASSETS_DIR / "site.css").read_text(encoding="utf-8")
    assert ".brand-lockup{position:relative;min-width:0;flex:1 1 auto;display:grid;gap:0}" in css
    assert ".brand{width:fit-content;padding-left:37px}" in css


@pytest.mark.parametrize("period", ["daily", "weekly", "monthly"])
def test_report_archive_selects_both_parent_and_period(period):
    nav = navigation(render.render_archive_html(Config(), period, []))
    assert nav.find("summary")[0].attrs["class"] == "on"
    current = [link for link in nav.find("a") if link.attrs.get("aria-current") == "page"]
    assert len(current) == 1
    assert current[0].attrs["href"] == f"../{period}/index.html"


@pytest.mark.parametrize("period,key", [
    ("daily", "2026-10-12"), ("weekly", "2026-W41"), ("monthly", "2026-09"),
])
def test_published_and_pending_reports_share_the_period_selection(period, key):
    published = render.render_report_html(Config(), {
        "period": period, "period_key": key, "label": key, "headline": "",
        "fallback": False, "model": "test", "timezone": "Asia/Shanghai",
        "window": {}, "stats": {}, "overview": [], "watchlist": [], "sections": [],
    })
    pending = render.render_report_pending_html(Config(), {
        "period": period, "period_key": key, "period_label": key,
        "type_name": "报告", "title": "生成中", "description": "稍后查看",
        "status": "running", "active": False,
    })
    for page in (published, pending):
        nav = navigation(page)
        assert nav.find("summary")[0].attrs["class"] == "on"
        current = [link for link in nav.find("a")
                   if link.attrs.get("aria-current") == "page"]
        assert len(current) == 1 and current[0].attrs["href"] == f"../{period}/index.html"


def test_reference_page_marks_its_navigation_destination():
    page = render.render_versions_html(Config(), {
        "releases": [], "projects": {"qemu": "QEMU"}, "years": [],
        "default_year": "", "checked_at": "", "sources": {},
    })
    current = [link for link in navigation(page).find("a")
               if link.attrs.get("aria-current") == "page"]
    assert len(current) == 1 and current[0].attrs["href"] == "versions.html"


def test_mobile_menu_has_progressive_disclosure_and_footer_is_compact():
    script = (render.ASSETS_DIR / "site.js").read_text(encoding="utf-8")
    css = (render.ASSETS_DIR / "site.css").read_text(encoding="utf-8")
    assert "siteMenuToggle.hidden=false" in script
    assert "siteHeader.classList.add('menu-ready')" in script
    assert "event.key!=='Escape'" in script
    assert "!siteHeader.contains(event.target)" in script
    assert "mobileNavigation.addEventListener('change'" in script
    assert "currentNav.scrollIntoView" not in script
    assert ".site-head.menu-ready .head-actions:not(.is-open){display:none}" in css
    assert ".site-head:not(.menu-ready){position:static}" in css
    assert "main{flex:1;min-height:0;padding:34px 0 28px}" in css
    assert "justify-content:center;gap:4px 12px;padding:12px 0;text-align:center" in css
    assert "@media(max-width:620px){.foot-row{flex-direction:column;gap:4px}}" in css
    assert ".main-nav{order:3" not in css


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is optional for navigation tests")
def test_navigation_pointer_and_keyboard_behavior():
    result = subprocess.run(
        ["node", "--test", str(Path(__file__).with_suffix(".cjs"))],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
