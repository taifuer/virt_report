"""轻量 HTTP 服务：从 SQLite 按路由即时渲染报告。"""
from __future__ import annotations

import json
import logging
import re
from contextlib import closing
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from virt_report import __version__, access, db, search as search_index
from virt_report.config import Config
from virt_report.render import render
from virt_report.processing import topics
from virt_report.summarize import report as report_builder
from virt_report.summarize import periods

log = logging.getLogger(__name__)
_REPORT_ROUTE = re.compile(r"^/(daily|weekly|monthly)/([^/]+?)(?:\.html)?$")
_ARCHIVE_ROUTE = re.compile(r"^/(daily|weekly|monthly)(?:/|/index\.html)?$")
_TOPIC_ROUTE = re.compile(r"^/topics/([a-z-]+)(?:/|\.html)?$")
READINESS_MAX_AGE_HOURS = 12
_REPORT_TYPE_NAMES = {"daily": "日报", "weekly": "周报", "monthly": "月报"}


def _published_content(row) -> dict | None:
    """解析正式报告；历史 fallback 行不再视为已发布内容。"""
    if not row:
        return None
    try:
        content = json.loads(row["content_json"])
    except (TypeError, ValueError):
        return None
    return None if content.get("fallback") else content


def _generation_state_view(row) -> dict:
    status = row["status"]
    title, description = {
        "running": (
            "AI 点评生成中",
            "本期报告尚未发布，完成后会自动显示。",
        ),
        "retry_wait": (
            "AI 点评正在重新生成",
            "首次生成未完成，系统将按计划自动重试。",
        ),
        "failed": (
            "AI 点评暂未完成",
            "本期报告尚未发布，请稍后再来查看。",
        ),
    }.get(status, ("AI 点评准备中", "本期报告尚未发布。"))
    period = row["period"]
    return {
        "period": period,
        "period_key": row["period_key"],
        "period_label": periods.label(period, row["period_key"]),
        "type_name": _REPORT_TYPE_NAMES[period],
        "status": status,
        "active": status in {"running", "retry_wait"},
        "title": title,
        "description": description,
        "attempt": int(row["attempt"] or 0),
        "updated_at": row["updated_at"],
        "retry_at": row["retry_at"],
    }


def _generation_states(conn, period: str) -> list[dict]:
    """只显示比最新正稿更新的未完成状态，避免历史失败长期占据首页。"""
    published = _list_reports(conn, period)
    latest_key = published[0]["period_key"] if published else ""
    result = []
    for row in db.list_report_generation_states(conn, period):
        if latest_key and row["period_key"] <= latest_key:
            continue
        if _published_content(db.get_report(conn, period, row["period_key"])):
            continue
        result.append(_generation_state_view(row))
    return result


def _list_reports(conn, period: str) -> list[dict]:
    rows = conn.execute(
        "SELECT period_key,generated_at,item_count,model,content_json FROM reports "
        "WHERE period=? ORDER BY period_key DESC", (period,),
    ).fetchall()
    result = []
    for row in rows:
        content = _published_content(row)
        if content is None:
            continue
        item = {key: row[key] for key in (
            "period_key", "generated_at", "item_count", "model"
        )}
        item["headline"] = content.get("headline", "")
        result.append(item)
    return result


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
        "daily": render.limit_home_reports("daily", daily),
        "weekly": render.limit_home_reports("weekly", weekly),
        "monthly": render.limit_home_reports(
            "monthly", _list_reports(conn, "monthly")
        ),
        "generation_states": {
            period: _generation_states(conn, period)[:1]
            for period in ("daily", "weekly", "monthly")
        },
    }


def _nav(conn, period: str, key: str) -> dict:
    def adjacent(operator: str, direction: str):
        rows = conn.execute(
            f"SELECT period_key,content_json FROM reports WHERE period=? "
            f"AND period_key{operator}? ORDER BY period_key {direction}",
            (period, key),
        ).fetchall()
        return next((row for row in rows if _published_content(row)), None)

    previous = adjacent("<", "DESC")
    following = adjacent(">", "ASC")

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
    local_now = now.astimezone(ZoneInfo(config.timezone))
    daily_offset = 1 if local_now.hour >= 1 else 2
    weekly_offset = 14 if local_now.weekday() == 0 and local_now.hour < 1 else 7
    previous_month_end = local_now.replace(day=1) - timedelta(days=1)
    if local_now.day == 1 and local_now.hour < 1:
        previous_month_end = previous_month_end.replace(day=1) - timedelta(days=1)
    expected_keys = {
        "daily": periods.period_key_for("daily", local_now - timedelta(days=daily_offset)),
        "weekly": periods.period_key_for("weekly", local_now - timedelta(days=weekly_offset)),
        "monthly": periods.period_key_for("monthly", previous_month_end),
    }
    reports = {}
    for period in ("daily", "weekly", "monthly"):
        row = conn.execute(
            "SELECT period_key,generated_at,item_count,model,content_json FROM reports "
            "WHERE period=? ORDER BY period_key DESC LIMIT 1", (period,),
        ).fetchone()
        fallback = True
        if row:
            try:
                fallback = bool(json.loads(row["content_json"]).get("fallback"))
            except (TypeError, ValueError):
                fallback = True
        report_fresh = bool(row and row["period_key"] >= expected_keys[period] and not fallback)
        ready = ready and report_fresh
        reports[period] = ({
            **{key: row[key] for key in ("period_key", "generated_at", "item_count", "model")},
            "expected_key": expected_keys[period], "fresh": report_fresh,
            "fallback": fallback,
        } if row else {
            "period_key": None, "expected_key": expected_keys[period],
            "fresh": False, "fallback": True,
        })
    return {
        "status": "ok" if ready else "degraded",
        "checked_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "max_source_age_hours": READINESS_MAX_AGE_HOURS,
        "sources": sources, "reports": reports,
    }


def make_handler(config: Config):
    """创建绑定项目配置的请求处理器。"""

    class Handler(BaseHTTPRequestHandler):
        server_version = f"virt-report/{__version__}"

        def version_string(self) -> str:
            """Avoid disclosing the host Python version in response headers."""
            return self.server_version

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

        def _send(self, status: int, body: str | bytes, head_only: bool = False,
                  content_type: str = "text/html; charset=utf-8",
                  extra_headers: dict[str, str] | None = None,
                  cache_control: str = "no-cache") -> None:
            payload = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", cache_control)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
            self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                "script-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; "
                "form-action 'self'",
            )
            for name, value in (extra_headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            if not head_only:
                self.wfile.write(payload)

        def _send_feed(self, body: str, head_only: bool = False) -> None:
            """发送可缓存的 Feed，并响应订阅器的条件请求。"""
            from virt_report import rss
            headers = rss.feed_http_headers(body)
            not_modified = rss.is_not_modified(self.headers, headers)
            self._send(
                304 if not_modified else 200,
                "" if not_modified else body,
                head_only,
                "application/rss+xml; charset=utf-8",
                headers,
                cache_control="public, max-age=300",
            )

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
            brand_assets = {
                "/favicon-32.png": ("favicon-32.png", "image/png"),
                "/favicon.ico": ("favicon.ico", "image/x-icon"),
                "/assets/brand-mark.png": ("brand-mark.png", "image/png"),
                "/assets/site.css": ("site.css", "text/css; charset=utf-8"),
                "/assets/site.js": ("site.js", "text/javascript; charset=utf-8"),
            }
            if path in brand_assets:
                filename, content_type = brand_assets[path]
                body = (render.ASSETS_DIR / filename).read_bytes()
                cache_control = (
                    "public, max-age=31536000, immutable"
                    if filename in {"site.css", "site.js"}
                    else "public, max-age=3600"
                )
                self._send(
                    200, body, head_only, content_type,
                    cache_control=cache_control,
                )
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
                        "/monthly/feed.xml"):
                from virt_report import rss
                with closing(db.connect(config.db_path)) as conn:
                    period = path.split("/")[1] if path != "/feed.xml" else None
                    body = rss.report_feed(conn, config, period)
                self._send_feed(body, head_only)
                return
            if path in ("/search", "/search/", "/search.html"):
                def search_value(name: str, default: str = "") -> str:
                    return query.get(name, [default])[0]

                def search_number(name: str, default: int) -> int:
                    try:
                        return int(search_value(name, str(default)))
                    except (TypeError, ValueError):
                        return default

                with closing(db.connect(config.db_path)) as conn:
                    result = search_index.search(
                        conn, search_value("q"),
                        project=search_value("project"),
                        category_key=search_value("category"),
                        architecture_key=search_value("architecture"),
                        sort=search_value("sort", "relevance"),
                        page=search_number("page", 1),
                        per_page=search_number("per_page", 10),
                    )
                self._send(200, render.render_search_html(config, result), head_only)
                return
            if path in ("/about", "/about/", "/about.html"):
                self._send(200, render.render_about_html(config), head_only)
                return
            if path in ("/conferences", "/conferences/", "/conferences.html"):
                from virt_report.conferences import load_content
                self._send(200, render.render_conferences_html(
                    config, load_content()
                ), head_only)
                return
            if path in ("/academic-conferences", "/academic-conferences/",
                        "/academic-conferences.html"):
                from virt_report.conferences import load_content
                self._send(200, render.render_academic_conferences_html(
                    config, load_content()
                ), head_only)
                return
            if path in ("/conference-papers", "/conference-papers/",
                        "/conference-papers.html"):
                from virt_report.conferences import load_content
                self._send(200, render.render_conference_papers_html(
                    config, load_content()
                ), head_only)
                return
            if path in ("/topics", "/topics/", "/topics.html"):
                with closing(db.connect(config.db_path)) as conn:
                    groups = topics.build_topic_groups(conn, allow_rebuild=False)
                if groups is None:
                    static_path = config.output_dir / "topics.html"
                    if static_path.exists():
                        self._send(200, static_path.read_bytes(), head_only)
                    else:
                        self._send(503, "<h1>专题快照准备中</h1>", head_only)
                    return
                html = render.render_topics_html(config, groups)
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
                        per_page=number("per_page", 10),
                        sort=query.get("sort", ["priority"])[0],
                        scope=query.get("scope", ["curated"])[0],
                        allow_rebuild=False,
                    )
                if topic:
                    self._send(200, render.render_topic_detail_html(config, topic), head_only)
                else:
                    static_path = (
                        config.output_dir / "topics" / topic_match.group(1) / "index.html"
                    )
                    if not query and static_path.exists():
                        self._send(200, static_path.read_bytes(), head_only)
                    elif any(
                        key == topic_match.group(1)
                        for key, _name, _description, _words in topics.TOPIC_RULES
                    ):
                        self._send(503, "<h1>专题快照准备中</h1>", head_only)
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
                        config, period, _list_reports(conn, period),
                        _generation_states(conn, period)[:1],
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
                    parsed = _published_content(row)
                    state = db.get_report_generation_state(conn, period, key)
                    if parsed:
                        content = report_builder.enrich_architectures(
                            parsed
                        )
                        html = render.render_report_html(
                            config, content, _nav(conn, period, key)
                        )
                        response_status = 200
                        response_cache = "no-cache"
                    elif state:
                        state_view = _generation_state_view(state)
                        html = render.render_report_pending_html(config, state_view)
                        response_status = 202 if state_view["active"] else 200
                        response_cache = "no-store"
                    else:
                        html = ""
                if html:
                    headers = ({"Retry-After": "60"}
                               if response_status == 202 else None)
                    self._send(
                        response_status, html, head_only,
                        extra_headers=headers, cache_control=response_cache,
                    )
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
