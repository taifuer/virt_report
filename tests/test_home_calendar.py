"""Publication filtering and shared static/dynamic homepage calendar data."""
import json
import re
from contextlib import closing

import pytest

from virt_report import cli, db, server
from virt_report.config import Config, Render, Storage
from virt_report.render import render


def _save(conn, period, key, **content):
    db.save_report(conn, period, key, {
        "period": period, "period_key": key, "headline": "Report headline",
        **content,
    }, "Asia/Shanghai", model="test")


def _embedded_index(page):
    match = re.search(r'<script id="report-calendar-index" type="application/json">'
                      r'(.*?)</script>', page, re.S)
    assert match
    return json.loads(match.group(1))


def test_calendar_retains_history_but_excludes_pending_and_fallback(tmp_path):
    with closing(db.connect(tmp_path / "calendar.db")) as conn:
        for number in range(1, 21):
            _save(conn, "daily", f"2026-07-{number:02d}")
            _save(conn, "weekly", f"2026-W{number:02d}")
            _save(conn, "monthly",
                  f"{2025 + (number - 1) // 12}-{(number - 1) % 12 + 1:02d}")
        for period, key in (("daily", "2026-07-21"), ("weekly", "2026-W21"),
                            ("monthly", "2026-09")):
            _save(conn, period, key, fallback=True)
            db.set_report_generation_state(conn, period, key, status="running")
        ctx = server._index_context(conn)
        assert [len(ctx[period]) for period in ("daily", "weekly", "monthly")] == [15, 9, 6]
        index = _embedded_index(render.render_index_html(Config(), ctx))
        assert [len(index[period]) for period in ("daily", "weekly", "monthly")] == [20, 20, 20]
        assert index["daily"][-1] == "2026-07-20"
        assert index["weekly"][-1]["key"] == "2026-W20"
        assert index["monthly"][0] == "2025-01"
        assert index["monthly"][-1] == "2026-08"
        assert "headline" not in json.dumps(index)
        assert "model" not in json.dumps(index)


def test_week_and_month_calendars_render_without_daily_reports(tmp_path):
    with closing(db.connect(tmp_path / "calendar.db")) as conn:
        _save(conn, "weekly", "2020-W53")
        _save(conn, "monthly", "2019-12")
        page = render.render_index_html(Config(), server._index_context(conn))
    assert 'href="weekly/2020-W53.html" aria-label="2020-12-28—2021-01-03 周报"' in page
    assert 'href="monthly/2019-12.html" aria-label="2019-12 月报"' in page
    assert '暂无已发布日报' in page
    assert 'class="archive-links"' not in page
    assert 'data-mobile-archive-toggle' not in page
    assert 'role="tabpanel" id="calendar-panel-weekly" aria-labelledby="calendar-tab-weekly"' in page
    for period in ("daily", "weekly", "monthly"):
        assert f'aria-controls="calendar-panel-{period}"' in page
        assert f'href="{period}/index.html"' in page


def test_static_export_and_dynamic_server_share_archive_context(tmp_path, monkeypatch):
    config = Config(storage=Storage(db_path=tmp_path / "calendar.db"),
                    render=Render(output_dir=tmp_path / "site"))

    class CapturedExport(Exception):
        pass

    captured = {}

    def capture(_config, ctx, filename):
        captured.update(ctx)
        raise CapturedExport

    monkeypatch.setattr(render, "render_index", capture)
    with closing(db.connect(config.db_path)) as conn:
        for year in (2024, 2025, 2026):
            _save(conn, "monthly", f"{year}-01")
            _save(conn, "weekly", f"{year}-W01")
            _save(conn, "daily", f"{year}-01-01")
        dynamic = server._index_context(conn, config.timezone)
        with pytest.raises(CapturedExport):
            cli._render_index(config, conn)
    assert captured["archive"] == dynamic["archive"]
    for period in ("daily", "weekly", "monthly"):
        assert captured[period] == dynamic[period]


def test_calendar_uses_compact_indexes_and_local_navigation():
    config = Config()
    ctx = render.build_home_context([], [], [{"period_key": "2026-01"}], config.timezone)
    page = render.render_index_html(config, ctx)
    assert _embedded_index(page)["monthly"] == ["2026-01"]
    assert 'timeZone:calendarIndex.timezone' in page
    assert 'sessionStorage.setItem(calendarStorageKey' in page
    assert "calendarTabKeys(archiveTabs" in page
    assert "calendarTabKeys(reportTabs" in page
    assert 'fetch(' not in page
    assert 'data-calendar-month=' not in page  # No pre-rendered historical month copies.
    assert '<noscript>' in page


def test_invalid_period_keys_cannot_create_broken_home_cards():
    ctx = render.build_home_context(
        [{"period_key": "2026-02-30"}, {"period_key": "2026-02-28"}],
        [{"period_key": "2021-W53"}, {"period_key": "2020-W53"}],
        [{"period_key": "2026-13"}, {"period_key": "2026-12"}],
    )
    assert [item["period_key"] for item in ctx["daily"]] == ["2026-02-28"]
    assert [item["period_key"] for item in ctx["weekly"]] == ["2020-W53"]
    assert [item["period_key"] for item in ctx["monthly"]] == ["2026-12"]
