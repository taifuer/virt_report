"""轻量 HTTP 服务：从 SQLite 按路由即时渲染报告。"""
from __future__ import annotations

import json
import logging
import re
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from virt_report import db
from virt_report.config import Config
from virt_report.render import render
from virt_report.processing.topics import build_topic_groups
from virt_report.summarize import report as report_builder

log = logging.getLogger(__name__)
_REPORT_ROUTE = re.compile(r"^/(daily|weekly|monthly)/([^/]+?)(?:\.html)?$")
_ARCHIVE_ROUTE = re.compile(r"^/(daily|weekly|monthly)(?:/|/index\.html)?$")


def _list_reports(conn, period: str) -> list[dict]:
    rows = conn.execute(
        "SELECT period_key,generated_at,item_count,model FROM reports "
        "WHERE period=? ORDER BY period_key DESC", (period,),
    ).fetchall()
    return [dict(row) for row in rows]


def _index_context(conn, timezone: str = "Asia/Shanghai") -> dict:
    daily = _list_reports(conn, "daily")
    daily_keys = {row["period_key"] for row in daily}
    months = sorted({key[:7] for key in daily_keys})
    if not months:
        from datetime import datetime
        months = [datetime.now().strftime("%Y-%m")]
    calendars = [render.build_calendar(month, daily_keys) for month in months]
    weekly = _list_reports(conn, "weekly")
    weekly = [dict(item, period_range=render._period_range(
        "weekly", item["period_key"], timezone
    )) for item in weekly]
    return {
        "cal": calendars[-1],
        "calendars": calendars,
        "daily": daily[:14],
        "weekly": weekly,
        "monthly": _list_reports(conn, "monthly"),
    }


def _nav(conn, period: str, key: str) -> dict:
    previous = conn.execute(
        "SELECT period_key FROM reports WHERE period=? AND period_key<? "
        "ORDER BY period_key DESC LIMIT 1", (period, key),
    ).fetchone()
    following = conn.execute(
        "SELECT period_key FROM reports WHERE period=? AND period_key>? "
        "ORDER BY period_key ASC LIMIT 1", (period, key),
    ).fetchone()

    def item(row):
        if not row:
            return None
        period_key = row["period_key"]
        labels = {"daily": period_key, "weekly": period_key, "monthly": period_key}
        return {"label": labels[period], "url": f"{period}/{period_key}.html"}

    return {"prev": item(previous), "next": item(following)}


def make_handler(config: Config):
    """创建绑定项目配置的请求处理器。"""

    class Handler(BaseHTTPRequestHandler):
        server_version = "virt-report/0.1"

        def do_HEAD(self) -> None:  # noqa: N802
            self._dispatch(head_only=True)

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch(head_only=False)

        def _send(self, status: int, body: str, head_only: bool = False,
                  content_type: str = "text/html; charset=utf-8") -> None:
            payload = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            if not head_only:
                self.wfile.write(payload)

        def _dispatch(self, head_only: bool) -> None:
            path = urlsplit(self.path).path
            if path == "/healthz":
                self._send(200, "ok\n", head_only, "text/plain; charset=utf-8")
                return
            if path in ("/about", "/about/", "/about.html"):
                self._send(200, render.render_about_html(config), head_only)
                return
            if path in ("/topics", "/topics/", "/topics.html"):
                with closing(db.connect(config.db_path)) as conn:
                    rows = conn.execute(
                        "SELECT period,period_key,content_json FROM reports "
                        "ORDER BY CASE period WHEN 'daily' THEN 0 "
                        "WHEN 'weekly' THEN 1 ELSE 2 END, period_key DESC"
                    ).fetchall()
                    html = render.render_topics_html(config, build_topic_groups(rows))
                self._send(200, html, head_only)
                return
            if path in ("/kvm-forum", "/kvm-forum/", "/kvm-forum.html"):
                try:
                    from virt_report.kvm_forum import load_content
                    editions, analysis = load_content()
                    html = render.render_kvm_forum_html(config, editions, analysis)
                    self._send(200, html, head_only)
                except FileNotFoundError:
                    self._send(503, "<h1>内容准备中</h1>", head_only)
                return

            archive_match = _ARCHIVE_ROUTE.fullmatch(path)
            if archive_match:
                period = archive_match.group(1)
                with closing(db.connect(config.db_path)) as conn:
                    html = render.render_archive_html(
                        config, period, _list_reports(conn, period)
                    )
                self._send(200, html, head_only)
                return

            if path in ("/", "/index.html"):
                with closing(db.connect(config.db_path)) as conn:
                    html = render.render_index_html(
                        config, _index_context(conn, config.timezone)
                    )
                self._send(200, html, head_only)
                return

            report_match = _REPORT_ROUTE.fullmatch(path)
            if report_match:
                period, key = report_match.groups()
                with closing(db.connect(config.db_path)) as conn:
                    row = db.get_report(conn, period, key)
                    if row:
                        content = report_builder.enrich_architectures(
                            json.loads(row["content_json"])
                        )
                        html = render.render_report_html(
                            config, content, _nav(conn, period, key)
                        )
                    else:
                        html = ""
                if html:
                    self._send(200, html, head_only)
                else:
                    self._send(404, "<h1>404</h1><p>未找到该报告。</p>", head_only)
                return

            self._send(404, "<h1>404</h1><p>页面不存在。</p>", head_only)

        def log_message(self, fmt: str, *args) -> None:
            log.info("%s - %s", self.address_string(), fmt % args)

    return Handler


def serve(config: Config, host: str = "127.0.0.1", port: int = 8090) -> None:
    """启动阻塞式多线程 HTTP 服务。"""
    server = ThreadingHTTPServer((host, port), make_handler(config))
    log.info("virt-report 服务已启动: http://%s:%d", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
