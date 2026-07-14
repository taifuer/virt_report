"""Jinja2 渲染引擎 + 报纸风格模板。"""
from __future__ import annotations

import calendar as _pycal
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

from virt_report.config import Config
from virt_report.summarize import periods

TEMPLATES_DIR = Path(__file__).parent / "templates"


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def build_calendar(month_key: str, daily_keys: set[str]) -> dict:
    """构建月历数据。daily_keys 为有日报的 'YYYY-MM-DD' 集合。"""
    y, m = map(int, month_key.split("-"))
    cal = _pycal.Calendar(firstweekday=0)  # 周一为首
    weeks = []
    for week in cal.monthdatescalendar(y, m):
        row = []
        for d in week:
            if d.month != m:
                row.append(None)
            else:
                k = d.strftime("%Y-%m-%d")
                row.append({"day": d.day, "key": k if k in daily_keys else None})
        weeks.append(row)
    pm, py = (12, y - 1) if m == 1 else (m - 1, y)
    nm, ny = (1, y + 1) if m == 12 else (m + 1, y)
    return {
        "month_key": month_key,
        "label": f"{y} 年 {m} 月",
        "weeks": weeks,
        "prev": f"{py:04d}-{pm:02d}",
        "next": f"{ny:04d}-{nm:02d}",
    }


def render_report(config: Config, content: dict, nav: dict | None = None) -> Path:
    """渲染通用报告 HTML 到 site/<period>/<period_key>.html，返回路径。"""
    html = render_report_html(config, content, nav)
    out = Path(config.output_dir) / content["period"] / f"{content['period_key']}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out


def render_report_html(config: Config, content: dict, nav: dict | None = None) -> str:
    """将报告渲染为 HTML 字符串，供静态导出和后端路由共用。"""
    env = _env()
    tpl = env.get_template("report.html")
    return tpl.render(report=content, nav=nav, period_range=_period_range(
        content["period"], content["period_key"], config.timezone
    ), root="../", site_name=config.name)


def _period_range(period: str, period_key: str, timezone: str) -> dict:
    """返回本地时区的闭区间标签，避免展示 UTC 和排他结束日。"""
    start_utc, end_utc = periods.window(period, period_key, timezone)
    tz = ZoneInfo(timezone)
    start = start_utc.astimezone(tz)
    end = (end_utc - timedelta(microseconds=1)).astimezone(tz)
    short = f"{start.month}.{start.day}–{end.month}.{end.day}"
    return {
        "short": short,
        "full": f"{start:%Y-%m-%d} 至 {end:%Y-%m-%d}",
        "label": f"{periods.label(period, period_key)}（{short}）"
        if period == "weekly" else periods.label(period, period_key),
    }


def render_index(config: Config, ctx: dict, filename: str = "index.html") -> Path:
    """渲染首页/月份页。

    ctx: {'cal': calendar_dict, 'weekly': [...], 'monthly': [...], 'cur_month': 'YYYY-MM'}
    """
    html = render_index_html(config, ctx)
    out = Path(config.output_dir) / filename
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out


def render_index_html(config: Config, ctx: dict) -> str:
    """将首页渲染为 HTML 字符串。"""
    env = _env()
    tpl = env.get_template("index.html")
    return tpl.render(ctx=ctx, root="", site_name=config.name)


def render_about(config: Config, filename: str = "about.html") -> Path:
    """导出关于页面。"""
    html = render_about_html(config)
    out = Path(config.output_dir) / filename
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out


def render_about_html(config: Config) -> str:
    """将关于页面渲染为 HTML 字符串。"""
    env = _env()
    tpl = env.get_template("about.html")
    return tpl.render(root="", site_name=config.name)


def render_archive(config: Config, period: str, reports: list[dict]) -> Path:
    """导出某一报告类型的归档页。"""
    html = render_archive_html(config, period, reports)
    out = Path(config.output_dir) / period / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out


def render_archive_html(config: Config, period: str, reports: list[dict]) -> str:
    """渲染日报、周报或月报归档页。"""
    env = _env()
    tpl = env.get_template("archive.html")
    enriched = [dict(item, period_range=_period_range(
        period, item["period_key"], config.timezone
    )) for item in reports]
    return tpl.render(period=period, reports=enriched, root="../", site_name=config.name)


def render_kvm_forum(config: Config, editions: list[dict], analysis: dict) -> Path:
    """导出 KVM Forum 年度主题页。"""
    html = render_kvm_forum_html(config, editions, analysis)
    out = Path(config.output_dir) / "kvm-forum.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out


def render_kvm_forum_html(config: Config, editions: list[dict], analysis: dict) -> str:
    env = _env()
    tpl = env.get_template("kvm_forum.html")
    return tpl.render(editions=editions, analysis=analysis, root="", site_name=config.name)


def render_topics(config: Config, groups: list[dict]) -> Path:
    """导出专题聚合页。"""
    html = render_topics_html(config, groups)
    out = Path(config.output_dir) / "topics.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out


def render_topics_html(config: Config, groups: list[dict]) -> str:
    """渲染运维与性能专题聚合页。"""
    env = _env()
    tpl = env.get_template("topics.html")
    return tpl.render(groups=groups, root="", site_name=config.name)
