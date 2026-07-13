"""Jinja2 渲染引擎 + 报纸风格模板。"""
from __future__ import annotations

import calendar as _pycal
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from virt_report.config import Config

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
    return tpl.render(report=content, nav=nav, root="../", site_name=config.name)


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
