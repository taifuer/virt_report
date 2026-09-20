"""Published-report calendar indexes and server-rendered archive views."""
from __future__ import annotations

import calendar
import re
from collections.abc import Iterable
from datetime import date, datetime
from zoneinfo import ZoneInfo

_DATE_KEY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_WEEK_KEY = re.compile(r"([0-9]{4})-W([0-9]{2})")
_MONTH_KEY = re.compile(r"([0-9]{4})-([0-9]{2})")
_YEAR_KEY = re.compile(r"[0-9]{4}")


def _valid_date(key: object) -> bool:
    if not isinstance(key, str) or not _DATE_KEY.fullmatch(key):
        return False
    try:
        date.fromisoformat(key)
    except ValueError:
        return False
    return True


def _valid_month(key: object) -> bool:
    if not isinstance(key, str) or not _MONTH_KEY.fullmatch(key):
        return False
    try:
        date(int(key[:4]), int(key[5:]), 1)
    except ValueError:
        return False
    return True


def _bounds(keys: list[str], fallback: str) -> dict[str, str]:
    first, last = (min(keys), max(keys)) if keys else (fallback, fallback)
    return {"first": first, "last": last, "initial": last}


def build_archive_index(
    daily_keys: Iterable[str],
    weekly_keys: Iterable[str],
    monthly_keys: Iterable[str],
    timezone: str = "Asia/Shanghai",
    today: date | None = None,
) -> dict:
    """Index all published periods, ignoring malformed and duplicate keys."""
    local_today = today if today is not None else datetime.now(
        ZoneInfo(timezone)
    ).date()
    today_key = local_today.isoformat()
    daily = sorted({key for key in daily_keys if _valid_date(key)})
    monthly = sorted({key for key in monthly_keys if _valid_month(key)})
    weeks = {}
    for key in weekly_keys:
        if not isinstance(key, str):
            continue
        match = _WEEK_KEY.fullmatch(key)
        if not match:
            continue
        year, week = map(int, match.groups())
        try:
            start = date.fromisocalendar(year, week, 1)
            end = date.fromisocalendar(year, week, 7)
        except ValueError:
            continue
        weeks[key] = {"key": key, "start": start.isoformat(),
                      "end": end.isoformat()}
    weekly = [weeks[key] for key in sorted(weeks)]
    return {
        "today": today_key,
        "timezone": timezone,
        "daily": daily,
        "weekly": weekly,
        "monthly": monthly,
        "views": {
            "daily": _bounds([key[:7] for key in daily], today_key[:7]),
            "weekly": _bounds(
                [item[edge][:7] for item in weekly
                 for edge in ("start", "end")],
                today_key[:7],
            ),
            "monthly": _bounds([key[:4] for key in monthly], today_key[:4]),
        },
    }


def _selected_key(bounds: dict, requested: object, year_only: bool) -> str:
    if year_only:
        valid = (isinstance(requested, str)
                 and _YEAR_KEY.fullmatch(requested)
                 and 1 <= int(requested) <= 9999)
    else:
        valid = _valid_month(requested)
    if not valid:
        return bounds["initial"]
    return min(max(requested, bounds["first"]), bounds["last"])


def _month_view(key: str, today: str, published: set[str]) -> dict:
    year, month = map(int, key.split("-"))
    cells = []
    # Integer tuples also support the outside days of December 9999 without
    # constructing dates beyond datetime.date's supported range.
    for cell_year, cell_month, day, _ in calendar.Calendar(
        firstweekday=calendar.MONDAY
    ).itermonthdays4(year, month):
        day_key = f"{cell_year:04d}-{cell_month:02d}-{day:02d}"
        in_month = cell_year == year and cell_month == month
        cells.append({
            "day": day,
            "date": day_key,
            "in_month": in_month,
            "today": day_key == today,
            "href": f"daily/{day_key}.html"
            if in_month and day_key in published else "",
        })
    return {
        "key": key,
        "label": f"{year} 年 {month} 月",
        "weeks": [cells[offset:offset + 7]
                  for offset in range(0, len(cells), 7)],
    }


def build_archive_views(index: dict, selection: dict | None = None) -> dict:
    """Build independent views, clamping valid selections to archive bounds."""
    selection = selection or {}
    selected = {
        period: _selected_key(bounds, selection.get(period), period == "monthly")
        for period, bounds in index["views"].items()
    }
    daily = _month_view(selected["daily"], index["today"], set(index["daily"]))
    weekly = _month_view(selected["weekly"], index["today"], set())
    published_weeks = {item["start"]: item for item in index["weekly"]}
    rows = []
    for days in weekly["weeks"]:
        start, end = days[0]["date"], days[-1]["date"]
        published = published_weeks.get(start)
        rows.append({
            "start": start,
            "end": end,
            "label": f"{start}—{end} 周报",
            "href": f"weekly/{published['key']}.html" if published else "",
            "days": days,
        })
    weekly["weeks"] = rows
    year = selected["monthly"]
    published_months = set(index["monthly"])
    months = []
    for month in range(1, 13):
        key = f"{year}-{month:02d}"
        months.append({
            "month": month,
            "key": key,
            "current": key == index["today"][:7],
            "href": f"monthly/{key}.html" if key in published_months else "",
        })
    return {
        "daily": daily,
        "weekly": weekly,
        "monthly": {"key": year, "label": f"{int(year)} 年", "months": months},
    }
