"""轻量 HTTP 服务：从 SQLite 按路由即时渲染报告。"""
from __future__ import annotations

import json
import logging
import re
from contextlib import closing
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from virt_report import access, db
from virt_report.config import Config
from virt_report.render import render
from virt_report.processing import topics
from virt_report.summarize import report as report_builder

log = logging.getLogger(__name__)
_REPORT_ROUTE = re.compile(r"^/(daily|weekly|monthly)/([^/]+?)(?:\.html)?$")
_ARCHIVE_ROUTE = re.compile(r"^/(daily|weekly|monthly)(?:/|/index\.html)?$")
_TOPIC_ROUTE = re.compile(r"^/topics/([a-z-]+)(?:/|\.html)?$")
READINESS_MAX_AGE_HOURS = 12


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


def _readiness(conn, config: Config) -> dict:
    """汇总数据库、数据源新鲜度与最新报告，供监控系统消费。"""
    expected = ([('ml', source.name) for source in config.sources.mailing_lists] +
                [('gitlab', source.name) for source in config.sources.gitlab])
    now = datetime.now(timezone.utc)
    sources = []
    ready = True
    for source, project in expected:
        row = conn.execute(
            "SELECT started_at,finished_at,success,complete,new_count,"
            "requested_since,coverage_start,coverage_end,error FROM fetch_runs "
            "WHERE source=? AND project=? ORDER BY id DESC LIMIT 1",
            (source, project),
        ).fetchone()
        age_hours = None
        if row:
            finished = datetime.fromisoformat(row["finished_at"].replace("Z", "+00:00"))
            age_hours = round((now - finished).total_seconds() / 3600, 2)
        fresh = bool(row and row["success"] and row["complete"] and
                     age_hours is not None and age_hours <= READINESS_MAX_AGE_HOURS)
        ready = ready and fresh
        sources.append({
            "source": source, "project": project, "fresh": fresh,
            "age_hours": age_hours, **(dict(row) if row else {}),
        })
    reports = {}
    for period in ("daily", "weekly", "monthly"):
        row = conn.execute(
            "SELECT period_key,generated_at,item_count,model FROM reports "
            "WHERE period=? ORDER BY period_key DESC LIMIT 1", (period,),
        ).fetchone()
        reports[period] = dict(row) if row else None
    return {
        "status": "ok" if ready else "degraded",
        "checked_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "max_source_age_hours": READINESS_MAX_AGE_HOURS,
        "sources": sources, "reports": reports,
    }


def make_handler(config: Config):
    """创建绑定项目配置的请求处理器。"""

    class Handler(BaseHTTPRequestHandler):
        server_version = "virt-report/0.1"

        def do_HEAD(self) -> None:  # noqa: N802
            self._dispatch(head_only=True)

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch(head_only=False)

        def do_POST(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            if path != "/metrics/login":
                self._send(404, "<h1>404</h1><p>页面不存在。</p>")
                return
            access_key = config.metrics_access.access_key
            if not access_key:
                self._send(503, "<h1>运行状态尚未配置访问密钥</h1>")
                return
            try:
                length = min(int(self.headers.get("Content-Length", "0")), 4096)
                form = parse_qs(self.rfile.read(length).decode("utf-8"))
                supplied = form.get("access_key", [""])[0]
            except (TypeError, ValueError, UnicodeDecodeError):
                supplied = ""
            if access.is_authorized({"Authorization": f"Bearer {supplied}"}, access_key):
                ttl = config.metrics_access.session_ttl_hours
                token = access.issue_session(access_key, ttl)
                self._send(303, "", extra_headers={
                    "Location": "/metrics.html",
                    "Set-Cookie": (f"{access.COOKIE_NAME}={token}; Path=/; Max-Age={ttl * 3600}; "
                                   "HttpOnly; Secure; SameSite=Strict"),
                })
            else:
                self._send(401, render.render_metrics_login_html(config, error=True))

        def _send(self, status: int, body: str, head_only: bool = False,
                  content_type: str = "text/html; charset=utf-8",
                  extra_headers: dict[str, str] | None = None) -> None:
            payload = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-cache")
            for name, value in (extra_headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            if not head_only:
                self.wfile.write(payload)

        def _dispatch(self, head_only: bool) -> None:
            parsed = urlsplit(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)
            if path == "/healthz":
                try:
                    with closing(db.connect(config.db_path)) as conn:
                        conn.execute("SELECT 1").fetchone()
                    self._send(200, "ok\n", head_only, "text/plain; charset=utf-8")
                except Exception:
                    self._send(503, "database unavailable\n", head_only,
                               "text/plain; charset=utf-8")
                return
            if path == "/readyz":
                with closing(db.connect(config.db_path)) as conn:
                    payload = _readiness(conn, config)
                status = 200 if payload["status"] == "ok" else 503
                public_payload = {key: payload[key] for key in ("status", "checked_at")}
                self._send(status, json.dumps(public_payload, ensure_ascii=False), head_only,
                           "application/json; charset=utf-8")
                return
            if path in ("/api/status", "/api/metrics"):
                if not access.is_authorized(self.headers, config.metrics_access.access_key):
                    self._send(401, json.dumps({"error": "unauthorized"}), head_only,
                               "application/json; charset=utf-8",
                               {"WWW-Authenticate": "Bearer"})
                    return
                if path == "/api/status":
                    with closing(db.connect(config.db_path)) as conn:
                        payload = _readiness(conn, config)
                    self._send(200, json.dumps(payload, ensure_ascii=False), head_only,
                               "application/json; charset=utf-8")
                    return
                from virt_report.metrics import build_metrics
                with closing(db.connect(config.db_path)) as conn:
                    payload = build_metrics(conn, config)
                self._send(200, json.dumps(payload, ensure_ascii=False), head_only,
                           "application/json; charset=utf-8")
                return
            if path in ("/feed.xml", "/daily/feed.xml", "/weekly/feed.xml",
                        "/monthly/feed.xml", "/topics/security/feed.xml"):
                from virt_report import rss
                with closing(db.connect(config.db_path)) as conn:
                    if path == "/topics/security/feed.xml":
                        body = rss.security_feed(conn, config)
                    else:
                        period = path.split("/")[1] if path != "/feed.xml" else None
                        body = rss.report_feed(conn, config, period)
                self._send(200, body, head_only, "application/rss+xml; charset=utf-8")
                return
            if path in ("/about", "/about/", "/about.html"):
                self._send(200, render.render_about_html(config), head_only)
                return
            if path in ("/topics", "/topics/", "/topics.html"):
                with closing(db.connect(config.db_path)) as conn:
                    html = render.render_topics_html(
                        config, topics.build_topic_groups(conn)
                    )
                self._send(200, html, head_only)
                return
            topic_match = _TOPIC_ROUTE.fullmatch(path)
            if topic_match:
                def number(name: str, default: int) -> int:
                    try:
                        return int(query.get(name, [default])[0])
                    except (TypeError, ValueError):
                        return default
                with closing(db.connect(config.db_path)) as conn:
                    topic = topics.build_topic_detail(
                        conn, topic_match.group(1), page=number("page", 1),
                        per_page=number("per_page", 20),
                        sort=query.get("sort", ["priority"])[0],
                    )
                if topic:
                    self._send(200, render.render_topic_detail_html(config, topic), head_only)
                else:
                    self._send(404, "<h1>404</h1><p>专题不存在。</p>", head_only)
                return
            if path in ("/metrics", "/metrics/", "/metrics.html"):
                if not access.is_authorized(self.headers, config.metrics_access.access_key):
                    status = 200 if config.metrics_access.access_key else 503
                    html = (render.render_metrics_login_html(config) if status == 200 else
                            "<h1>运行状态尚未配置访问密钥</h1>")
                    self._send(status, html, head_only)
                    return
                from virt_report.metrics import build_metrics
                with closing(db.connect(config.db_path)) as conn:
                    html = render.render_metrics_html(config, build_metrics(conn, config))
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
