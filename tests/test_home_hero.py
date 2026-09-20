"""The banner-free homepage must preserve site branding and report navigation."""
from __future__ import annotations

import re
from pathlib import Path

from virt_report.config import Config
from virt_report.render.render import build_home_context, render_index_html


def _assert_home_starts_with_reports(page: str) -> None:
    assert not re.search(r'class="[^"]*\bhome-hero\b[^"]*"', page)
    assert "Libvirt/QEMU/KVM 虚拟化社区动态" not in page
    assert re.search(
        r'<main\b[^>]*id="main"[^>]*>\s*<div\b[^>]*class="wrap"[^>]*>\s*'
        r'<div\b[^>]*class="(?:home-report-tabs|home-grid)"',
        page,
        flags=re.DOTALL,
    )
    header = re.search(r"<header\b[^>]*>(.*?)</header>", page, flags=re.DOTALL)
    assert header is not None
    assert "<span>virt-report</span>" in header.group(1)
    assert '<span class="brand-sub">虚拟化社区动态</span>' in header.group(1)
    for period in ("daily", "weekly", "monthly"):
        assert f'data-home-report-panel="{period}"' in page
        assert f'data-archive-panel="{period}"' in page
    assert "data-report-calendar" in page


def test_home_starts_with_reports_and_preserves_header_branding():
    page = render_index_html(Config(), build_home_context([], [], []))

    _assert_home_starts_with_reports(page)


def test_static_home_starts_with_reports_and_preserves_header_branding():
    index_path = Path(__file__).resolve().parents[1] / "site" / "index.html"

    _assert_home_starts_with_reports(index_path.read_text(encoding="utf-8"))
