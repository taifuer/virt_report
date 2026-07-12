"""周期 (daily/weekly/monthly) 的窗口与周期键计算。

所有窗口以本地时区切分，返回 UTC 区间。存储统一 UTC。
  daily   key=YYYY-MM-DD     窗口=[当日 00:00, 次日 00:00)
  weekly  key=YYYY-Www       窗口=[周一 00:00, 下周一 00:00) (ISO 周)
  monthly key=YYYY-MM        窗口=[1 日 00:00, 下月 1 日 00:00)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

PERIODS = ("daily", "weekly", "monthly")


def _tz(tz_name: str) -> ZoneInfo:
    return ZoneInfo(tz_name)


def window(period: str, period_key: str, tz_name: str) -> tuple[datetime, datetime]:
    """周期键 -> [start, end) 的 UTC 区间。"""
    tz = _tz(tz_name)
    if period == "daily":
        start = datetime.fromisoformat(period_key).replace(tzinfo=tz)
        end = start + timedelta(days=1)
    elif period == "weekly":
        y, w = period_key.split("-W")
        start = datetime.fromisocalendar(int(y), int(w), 1).replace(tzinfo=tz)
        end = start + timedelta(weeks=1)
    elif period == "monthly":
        y, m = period_key.split("-")
        start = datetime(int(y), int(m), 1, tzinfo=tz)
        end = datetime(int(y) + (1 if int(m) == 12 else 0),
                       1 if int(m) == 12 else int(m) + 1, 1, tzinfo=tz)
    else:
        raise ValueError(f"未知 period: {period}")
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def period_key_for(period: str, dt_local: datetime) -> str:
    """本地时间 -> 该周期的键。"""
    if period == "daily":
        return dt_local.strftime("%Y-%m-%d")
    if period == "weekly":
        iso = dt_local.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    if period == "monthly":
        return dt_local.strftime("%Y-%m")
    raise ValueError(f"未知 period: {period}")


def label(period: str, period_key: str) -> str:
    """周期键 -> 中文展示标签。"""
    if period == "daily":
        return period_key
    if period == "weekly":
        y, w = period_key.split("-W")
        return f"{y} 年第 {int(w)} 周"
    if period == "monthly":
        y, m = period_key.split("-")
        return f"{y} 年 {int(m)} 月"
    return period_key
