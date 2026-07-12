"""mail-archive.com 邮件列表采集器 (qemu-devel / kvm)。

lore.kernel.org 的 /new.atom 仅返回最近 25 条且翻页/mbox 被反爬，
mail-archive.com 的 /maillist.xml 返回最近 100 条且不反爬，覆盖 4 倍。
缺点：RSS 与消息页均不含 In-Reply-To，故线程靠处理层按主题规范化折叠
(处理层 _compute_ml_roots 对无 in_reply_to 的条目按 subject 分组)。
"""
from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime, timezone

import feedparser

from virt_report import db
from . import base

log = logging.getLogger(__name__)


def _msg_num_from_link(link: str) -> str | None:
    m = re.search(r"/(msg\d+)\.html", link or "")
    return m.group(1) if m else None


def fetch(conn: sqlite3.Connection, source, *, since: datetime | None = None,
          max_pages: int = 1) -> int:
    """拉取 mail-archive.com 列表的近期消息 (RSS 100 条)。"""
    project = source.name
    feed_url = source.url.rstrip("/") + "/maillist.xml"
    since_iso = base.to_utc_iso(since) if since else None

    try:
        resp = base.http_get(feed_url)
    except Exception as e:
        log.warning("mailarchive %s: feed 获取失败: %s", project, e)
        return 0
    parsed = feedparser.parse(resp.content)
    if parsed.bozo and not parsed.entries:
        log.warning("mailarchive %s: feed 解析错误: %s", project, parsed.bozo_exception)
        return 0

    new_count = 0
    for entry in parsed.entries:
        link = entry.get("link", "")
        msg_num = _msg_num_from_link(link)
        if not msg_num:
            continue
        native_id = f"ma:{msg_num}"

        published = base.parse_dt(entry.get("published")) or datetime.now(timezone.utc)
        created_iso = base.to_utc_iso(published)
        if since_iso and created_iso < since_iso:
            break  # RSS 最新优先

        if db.item_exists(conn, "ml", project, native_id):
            continue

        subject = entry.get("title", "") or ""
        author = entry.get("author", "") or ""
        summary = entry.get("summary", "") or ""
        body = base.strip_html(summary)

        item = {
            "source": "ml",
            "project": project,
            "native_id": native_id,
            "message_id": None,        # mail-archive 不提供真实 Message-ID
            "in_reply_to": None,       # 无 In-Reply-To，靠主题折叠
            "thread_root": None,
            "author": author,
            "subject": subject,
            "kind": base.classify_subject(subject),
            "created_at": created_iso,
            "updated_at": created_iso,
            "url": link,
            "labels": [],
            "body_excerpt": base.extract_excerpt(body),
            "raw_json": {"msg_num": msg_num, "source": "mail-archive.com"},
        }
        if db.upsert_item(conn, item):
            new_count += 1

    db.set_fetch_state(conn, "ml", project,
                       last_fetch_at=base.to_utc_iso(datetime.now(timezone.utc)))
    log.info("mailarchive %s: fetched %d new", project, new_count)
    return new_count
