"""lore.kernel.org 邮件列表采集器 (qemu-devel / kvm)。

lore 的 /new.atom 给最新邮件，按 rel="next" 翻页向后。Atom entry 的 <id>
是 urn:uuid (非真实 Message-ID)，真实 Message-ID 编码在 link URL 里。
<content type="xhtml"> 已含邮件正文，无需访问 /raw (其有反爬)。
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

import feedparser

from virt_report import db
from . import base

log = logging.getLogger(__name__)


def _next_link(feed) -> str | None:
    for link in feed.get("feed", {}).get("links", []):
        if link.get("rel") == "next":
            return link.get("href")
    return None


def _entry_to_item(entry, project: str) -> dict | None:
    link = entry.get("link")
    message_id = base.lore_msgid_from_url(link)
    if not message_id:
        return None

    # in-reply-to: feedparser 暴露为 thr_in-reply-to (保留连字符)
    irt = entry.get("thr_in-reply-to") or entry.get("thr_in_reply_to")
    in_reply_to: str | None = None
    if isinstance(irt, dict) and irt.get("href"):
        in_reply_to = base.lore_msgid_from_url(irt["href"])

    subject = entry.get("title", "") or ""
    author = entry.get("author", "") or ""
    author_email = ""
    ad = entry.get("author_detail")
    if isinstance(ad, dict):
        author_email = ad.get("email", "") or ""

    # 正文
    body_html = ""
    contents = entry.get("content") or []
    if contents:
        body_html = contents[0].get("value", "") or ""
    elif entry.get("summary"):
        body_html = entry.get("summary", "")
    body = base.strip_html(body_html)

    updated = base.parse_dt(entry.get("updated")) or base.parse_dt(entry.get("published"))
    if not updated:
        updated = datetime.now(timezone.utc)

    kind = base.classify_subject(subject)

    return {
        "source": "ml",
        "project": project,
        "native_id": message_id,
        "message_id": message_id,
        "in_reply_to": in_reply_to,
        "thread_root": None,  # 处理层计算
        "author": f"{author} <{author_email}>" if author_email else author,
        "subject": subject,
        "kind": kind,
        "created_at": base.to_utc_iso(updated),
        "updated_at": base.to_utc_iso(updated),
        "url": link,
        "labels": [],
        "body_excerpt": base.extract_excerpt(body),
        "raw_json": {"subject": subject, "author": author, "email": author_email,
                     "in_reply_to": in_reply_to},
    }


def fetch(conn: sqlite3.Connection, source, *, since: datetime | None = None,
          max_pages: int = 8) -> int:
    """拉取一个 lore 邮件列表的近期邮件。

    Args:
        source: MailingListSource (name=project, url=lore 列表地址)
        since: 只取此时间之后的邮件 (UTC)；遇到更早的即停止翻页
        max_pages: 最多翻页数 (每页约 50 条)
    Returns: 新增条目数
    """
    project = source.name
    feed_url = source.url.rstrip("/") + "/new.atom"
    since_iso = base.to_utc_iso(since) if since else None

    new_count = 0
    url: str | None = feed_url
    pages = 0
    while url and pages < max_pages:
        resp = base.http_get(url)
        parsed = feedparser.parse(resp.content)
        if parsed.bozo and not parsed.entries:
            log.warning("lore %s: feed parse error: %s", project, parsed.bozo_exception)
            break

        stop = False
        for entry in parsed.entries:
            item = _entry_to_item(entry, project)
            if not item:
                continue
            # newest-first: 遇到早于 since 的就停止
            if since_iso and item["created_at"] < since_iso:
                stop = True
                break
            if db.upsert_item(conn, item):
                new_count += 1

        pages += 1
        if stop:
            break
        url = _next_link(parsed)

    db.set_fetch_state(conn, "ml", project,
                       last_fetch_at=base.to_utc_iso(datetime.now(timezone.utc)))
    log.info("lore %s: fetched %d new (pages=%d)", project, new_count, pages)
    return new_count
