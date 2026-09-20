"""Archive coverage and calendar edge cases, independent of homepage cards."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from virt_report.render import calendar as archive_calendar
from virt_report.render.calendar import build_archive_index, build_archive_views


TODAY = date(2026, 9, 20)


def test_archive_index_keeps_all_history_independent_of_card_limits():
    daily = [(date(2026, 1, 1) + timedelta(days=offset)).isoformat()
             for offset in range(100)]
    weekly = [f"2026-W{week:02d}" for week in range(1, 31)]
    monthly = [f"{year}-{month:02d}"
               for year in (2025, 2026) for month in range(1, 13)]

    index = build_archive_index(reversed(daily), reversed(weekly),
                                reversed(monthly), today=TODAY)

    assert index["daily"] == daily
    assert [item["key"] for item in index["weekly"]] == weekly
    assert index["monthly"] == monthly
    assert index["views"] == {
        "daily": {"first": "2026-01", "last": "2026-04", "initial": "2026-04"},
        "weekly": {"first": "2025-12", "last": "2026-07", "initial": "2026-07"},
        "monthly": {"first": "2025", "last": "2026", "initial": "2026"},
    }
    assert json.loads(json.dumps(index)) == index


def test_archive_index_ignores_invalid_keys_and_deduplicates():
    index = build_archive_index(
        ["2024-02-29", "2024-02-29", "2023-02-29", "2026-9-01", "20260901",
         "2026-13-01", "0000-01-01", "2026-09-01/../../x", None, 1],
        ["2020-W53", "2020-W53", "2021-W53", "2026-W00", "2026-W54",
         "2026-W1", "2026-w01", "0000-W01", None, 1],
        ["2024-02", "2024-02", "2026-13", "2026-00", "0000-01", "2026-9",
         "2026-09-extra", None, 1],
        today=TODAY,
    )

    assert index["daily"] == ["2024-02-29"]
    assert index["weekly"] == [{"key": "2020-W53", "start": "2020-12-28",
                                "end": "2021-01-03"}]
    assert index["monthly"] == ["2024-02"]


def test_empty_archives_use_today_for_each_independent_view():
    index = build_archive_index([], [], [], today=TODAY)
    views = build_archive_views(index)

    assert index["today"] == "2026-09-20"
    assert index["views"] == {
        "daily": {"first": "2026-09", "last": "2026-09", "initial": "2026-09"},
        "weekly": {"first": "2026-09", "last": "2026-09", "initial": "2026-09"},
        "monthly": {"first": "2026", "last": "2026", "initial": "2026"},
    }
    assert views["daily"]["label"] == "2026 年 9 月"
    assert views["monthly"]["label"] == "2026 年"
    assert not any(cell["href"] for week in views["daily"]["weeks"]
                   for cell in week)
    assert not any(row["href"] for row in views["weekly"]["weeks"])
    assert not any(cell["href"] for cell in views["monthly"]["months"])
    assert json.loads(json.dumps(views)) == views


def test_weekly_and_monthly_archives_work_without_daily_reports():
    index = build_archive_index([], ["2020-W53"], ["2019-12"], today=TODAY)
    views = build_archive_views(index)

    assert views["daily"]["key"] == "2026-09"
    assert views["weekly"]["key"] == "2021-01"
    assert views["monthly"]["key"] == "2019"
    assert any(row["href"] == "weekly/2020-W53.html"
               for row in views["weekly"]["weeks"])
    assert views["monthly"]["months"][-1]["href"] == "monthly/2019-12.html"


def test_default_today_uses_the_configured_site_timezone(monkeypatch):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 19, 18, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(archive_calendar, "datetime", FixedDatetime)

    east = build_archive_index([], [], [], timezone="Asia/Shanghai")
    west = build_archive_index([], [], [], timezone="America/Los_Angeles")

    assert east["today"] == "2026-09-20"
    assert west["today"] == "2026-09-19"
    assert east["timezone"] == "Asia/Shanghai"
    assert west["timezone"] == "America/Los_Angeles"


def test_leap_february_is_monday_first_and_only_links_in_month_reports():
    index = build_archive_index(
        ["2024-01-31", "2024-02-01", "2024-02-29", "2024-03-01"], [], [],
        today=date(2024, 2, 29),
    )
    view = build_archive_views(index, {"daily": "2024-02"})["daily"]
    cells = {cell["date"]: cell for week in view["weeks"] for cell in week}

    assert view["label"] == "2024 年 2 月"
    assert len(view["weeks"]) == 5
    assert all(len(week) == 7 for week in view["weeks"])
    assert view["weeks"][0][0]["date"] == "2024-01-29"
    assert view["weeks"][-1][-1]["date"] == "2024-03-03"
    assert cells["2024-02-29"] == {
        "day": 29, "date": "2024-02-29", "in_month": True, "today": True,
        "href": "daily/2024-02-29.html",
    }
    assert cells["2024-02-01"]["href"] == "daily/2024-02-01.html"
    assert cells["2024-02-02"]["href"] == ""
    assert cells["2024-01-31"]["href"] == ""
    assert cells["2024-03-01"]["href"] == ""
    assert not cells["2024-01-31"]["in_month"]


@pytest.mark.parametrize(("key", "week_count"), [("2021-02", 4), ("2026-03", 6)])
def test_month_grids_use_four_to_six_complete_weeks(key, week_count):
    index = build_archive_index([f"{key}-01"], [], [], today=TODAY)

    view = build_archive_views(index)["daily"]

    assert len(view["weeks"]) == week_count
    assert all(len(week) == 7 for week in view["weeks"])


def test_iso_week_year_and_adjacent_months_share_the_same_week_report():
    index = build_archive_index([], ["2021-W01", "2020-W53"], [], today=TODAY)
    december = build_archive_views(index, {"weekly": "2020-12"})["weekly"]
    january = build_archive_views(index, {"weekly": "2021-01"})["weekly"]
    dec_row = next(row for row in december["weeks"] if row["href"])
    jan_rows = [row for row in january["weeks"] if row["href"]]

    assert index["views"]["weekly"] == {
        "first": "2020-12", "last": "2021-01", "initial": "2021-01",
    }
    assert dec_row["href"] == jan_rows[0]["href"] == "weekly/2020-W53.html"
    assert dec_row["start"] == jan_rows[0]["start"] == "2020-12-28"
    assert dec_row["end"] == jan_rows[0]["end"] == "2021-01-03"
    assert dec_row["label"] == "2020-12-28—2021-01-03 周报"
    assert jan_rows[1]["start"] == "2021-01-04"
    assert jan_rows[1]["end"] == "2021-01-10"
    assert jan_rows[1]["href"] == "weekly/2021-W01.html"
    assert not any(day["href"] for row in january["weeks"] for day in row["days"])
    assert [day["in_month"] for day in dec_row["days"]] == [
        True, True, True, True, False, False, False,
    ]
    assert [day["in_month"] for day in jan_rows[0]["days"]] == [
        False, False, False, False, True, True, True,
    ]


def test_month_view_has_twelve_months_with_only_published_month_links():
    index = build_archive_index([], [], ["2025-12", "2026-02", "2026-09"],
                                today=TODAY)
    view = build_archive_views(index)["monthly"]

    assert view["key"] == "2026"
    assert [cell["month"] for cell in view["months"]] == list(range(1, 13))
    assert view["months"][1] == {
        "month": 2, "key": "2026-02", "current": False,
        "href": "monthly/2026-02.html",
    }
    assert view["months"][8] == {
        "month": 9, "key": "2026-09", "current": True,
        "href": "monthly/2026-09.html",
    }
    assert view["months"][11]["href"] == ""


def test_view_selections_are_independent_and_bounded():
    index = build_archive_index(["2024-02-01", "2026-09-01"], ["2020-W53"],
                                ["2025-12", "2026-09"], today=TODAY)
    original = json.dumps(index, sort_keys=True)
    selected = build_archive_views(index, {
        "daily": "2025-05", "weekly": "2020-12", "monthly": "2025",
    })
    invalid = build_archive_views(index, {
        "daily": "2026-13", "weekly": "../../x", "monthly": "0000",
    })
    bounded = build_archive_views(index, {
        "daily": "2020-01", "weekly": "2026-01", "monthly": "9999",
    })

    assert [selected[k]["key"] for k in ("daily", "weekly", "monthly")] == [
        "2025-05", "2020-12", "2025",
    ]
    assert [invalid[k]["key"] for k in ("daily", "weekly", "monthly")] == [
        "2026-09", "2021-01", "2026",
    ]
    assert [bounded[k]["key"] for k in ("daily", "weekly", "monthly")] == [
        "2024-02", "2021-01", "2026",
    ]
    assert json.dumps(index, sort_keys=True) == original
