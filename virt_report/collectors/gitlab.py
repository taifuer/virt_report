"""GitLab 采集器 (libvirt / qemu-project)。

通过 GitLab API v4 增量拉取 issues 与 merge_requests。公开项目可匿名访问
(限流 500 req/min)；若设置 GITLAB_TOKEN 环境变量则带鉴权提高限额。
每个 issue/MR 作为独立 thread_root；user_notes_count 提供讨论热度，无需逐条拉 note。
"""
from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests

from virt_report import db
from . import base

log = logging.getLogger(__name__)

DEFAULT_WINDOW_DAYS = 7


def _project_path_encoded(project: str) -> str:
    return quote(project, safe="")


def _token_headers() -> dict:
    token = os.environ.get("GITLAB_TOKEN")
    if token:
        return {"PRIVATE-TOKEN": token}
    return {}


def _fetch_paginated(base_url: str, params: dict, max_pages: int) -> tuple[list[dict], bool]:
    """跟随 Link rel="next" 翻页，返回所有条目。"""
    items: list[dict] = []
    url: str | None = base_url
    page = 0
    next_url: str | None = None
    while url and page < max_pages:
        resp = base.http_get(url, params=params if page == 0 else None,
                             headers=_token_headers())
        data = resp.json()
        if not isinstance(data, list):
            log.warning("gitlab: unexpected response: %s", str(data)[:200])
            break
        items.extend(data)
        page += 1
        # Link 头里的 next 已含分页参数
        next_url = resp.links.get("next", {}).get("url") if resp.links else None
        if not next_url or not data:
            break
        url = next_url
        params = None  # next_url 自带参数
    return items, not bool(url and page >= max_pages and next_url)


def _kind_from_labels(base_kind: str, labels: list[str]) -> str:
    ls = {l.lower() for l in (labels or [])}
    if base_kind == "issue":
        if "security" in ls:
            return "security"
        if "bug" in ls or "defect" in ls:
            return "bug"
    return base_kind


def _issue_to_item(issue: dict, project: str) -> dict:
    labels = issue.get("labels") or []
    kind = _kind_from_labels("issue", labels)
    iid = issue["iid"]
    native_id = f"issue:{iid}"
    desc = issue.get("description") or ""
    return {
        "source": "gitlab",
        "project": project,
        "native_id": native_id,
        "message_id": None,
        "in_reply_to": None,
        "thread_root": native_id,
        "author": (issue.get("author") or {}).get("username", ""),
        "subject": issue.get("title", ""),
        "kind": kind,
        "created_at": base.norm_utc_iso(issue.get("created_at")),
        "updated_at": base.norm_utc_iso(issue.get("updated_at")),
        "url": issue.get("web_url"),
        "labels": labels,
        "body_excerpt": base.extract_excerpt(base.strip_html(desc)),
        "raw_json": {
            "type": "issue", "iid": iid, "state": issue.get("state"),
            "labels": labels, "user_notes_count": issue.get("user_notes_count", 0),
            "upvotes": issue.get("upvotes", 0), "downvotes": issue.get("downvotes", 0),
            "merged": None,
        },
    }


def _mr_to_item(mr: dict, project: str) -> dict:
    labels = mr.get("labels") or []
    kind = _kind_from_labels("mr", labels)
    iid = mr["iid"]
    native_id = f"mr:{iid}"
    desc = mr.get("description") or ""
    return {
        "source": "gitlab",
        "project": project,
        "native_id": native_id,
        "message_id": None,
        "in_reply_to": None,
        "thread_root": native_id,
        "author": (mr.get("author") or {}).get("username", ""),
        "subject": mr.get("title", ""),
        "kind": kind,
        "created_at": base.norm_utc_iso(mr.get("created_at")),
        "updated_at": base.norm_utc_iso(mr.get("updated_at")),
        "url": mr.get("web_url"),
        "labels": labels,
        "body_excerpt": base.extract_excerpt(base.strip_html(desc)),
        "raw_json": {
            "type": "mr", "iid": iid, "state": mr.get("state"),
            "labels": labels, "user_notes_count": mr.get("user_notes_count", 0),
            "upvotes": mr.get("upvotes", 0), "downvotes": mr.get("downvotes", 0),
            "merged": mr.get("state") == "merged", "merged_at": mr.get("merged_at"),
        },
    }


def fetch(conn: sqlite3.Connection, source, *, since: datetime | None = None,
          max_pages: int = 10) -> int:
    """拉取一个 GitLab 项目的近期 issues 与 MRs。

    Args:
        source: GitLabSource (name=项目简称, project='libvirt/libvirt', url='https://gitlab.com')
        since: 只取 updated_at 在此之后的 (UTC)；默认 now - 7 天
    Returns: 新增/更新条目数
    """
    project = source.name
    pid = _project_path_encoded(source.project)
    api = source.url.rstrip("/") + f"/api/v4/projects/{pid}"

    if since is None:
        state = db.get_fetch_state(conn, "gitlab", project)
        if state["last_fetch_at"]:
            since = base.parse_dt(state["last_fetch_at"])
    if since is None:
        since = datetime.now(timezone.utc) - timedelta(days=DEFAULT_WINDOW_DAYS)
    since_iso = base.to_utc_iso(since)

    new_count = 0
    failed_required = False
    all_complete = True
    for endpoint, mapper, label in (
        (f"{api}/issues", _issue_to_item, "issues"),
        (f"{api}/merge_requests", _mr_to_item, "mrs"),
    ):
        params = {"updated_after": since_iso, "per_page": 100,
                  "order_by": "updated_at", "sort": "asc"}
        try:
            records, complete = _fetch_paginated(endpoint, params, max_pages)
            all_complete = all_complete and complete
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 403:
                # 多半是该 endpoint 对此项目禁用 (如 qemu-project 禁用 MRs，补丁走邮件列表)
                log.info("gitlab %s/%s: 403 (endpoint disabled for project, skip)", project, label)
                if label != "mrs" or project != "qemu":
                    failed_required = True
            else:
                log.warning("gitlab %s/%s: HTTP %s", project, label,
                            e.response.status_code if e.response is not None else e)
                failed_required = True
            continue
        except Exception as e:
            log.warning("gitlab %s/%s: %s", project, label, e)
            failed_required = True
            continue
        for rec in records:
            item = mapper(rec, project)
            if db.upsert_item(conn, item):
                new_count += 1
        log.info("gitlab %s/%s: %d records since %s", project, label, len(records), since_iso[:10])

    if failed_required:
        raise RuntimeError(f"GitLab {project} 至少一个必要端点采集失败，水位未推进")
    db.set_fetch_state(
        conn, "gitlab", project,
        last_fetch_at=base.to_utc_iso(datetime.now(timezone.utc)),
        last_seen_id="complete" if all_complete else "partial",
    )
    return new_count
