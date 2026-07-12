"""官方 HyperKitty 归档采集器（libvirt-devel）。

HyperKitty 的全列表 threads API 会一次返回整个历史，不适合增量抓取；本采集器
先访问按月归档页取得 thread hash，再通过小粒度 REST API 获取线程邮件与正文。
由此保留真实 Message-ID、父子关系、作者、正文和官方线程 ID。
"""
from __future__ import annotations

import logging
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import quote, unquote, urlsplit

from virt_report import db
from . import base

log = logging.getLogger(__name__)

_THREAD_RE = re.compile(r"/thread/([A-Z0-9]{20,64})/")
_EMAIL_HASH_RE = re.compile(r"/email/([A-Z0-9]{20,64})/")


def _source_parts(url: str) -> tuple[str, str, str]:
    """返回 (origin, list_address, normalized_archive_url)。"""
    parsed = urlsplit(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    match = re.search(r"/archives/list/([^/]+)/?", parsed.path)
    if not match:
        raise ValueError(f"HyperKitty URL 缺少 /archives/list/<address>/: {url}")
    address = unquote(match.group(1))
    archive_url = f"{origin}/archives/list/{quote(address, safe='@')}/"
    return origin, address, archive_url


def _months_range(start: datetime, end: datetime) -> list[tuple[int, int]]:
    months: list[tuple[int, int]] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        months.append((year, month))
        month += 1
        if month == 13:
            year += 1
            month = 1
    return months


def _parent_hash(parent_url: str | None) -> str | None:
    match = _EMAIL_HASH_RE.search(parent_url or "")
    return match.group(1) if match else None


def _author(email_data: dict) -> str:
    sender = email_data.get("sender") or {}
    address = (sender.get("address") or "").replace(" (a) ", "@").strip()
    name = (email_data.get("sender_name") or "").strip()
    if name and address:
        return f"{name} <{address}>"
    return name or address


def _thread_ids_from_month(archive_url: str, year: int, month: int,
                           max_pages: int) -> tuple[list[str], bool]:
    """从月度 HTML 归档提取线程 ID；返回 (IDs, 是否完整遍历)。"""
    found: list[str] = []
    seen: set[str] = set()
    complete = False
    for page in range(1, max_pages + 1):
        url = f"{archive_url}{year}/{month}/?count=100&page={page}"
        html = base.http_get(url).text
        page_ids = []
        for thread_id in _THREAD_RE.findall(html):
            if thread_id not in seen:
                seen.add(thread_id)
                page_ids.append(thread_id)
                found.append(thread_id)
        # HyperKitty 返回空页或少于 100 个主列表线程时已经到底。页面还可能包含
        # recent/popular 侧栏链接，日期过滤会在 API 数据阶段剔除它们。
        if not page_ids or len(page_ids) < 100:
            complete = True
            break
    return found, complete


def fetch(conn: sqlite3.Connection, source, *, since: datetime | None = None,
          max_pages: int = 8) -> int:
    """按月分页拉取官方 HyperKitty 邮件，并保留完整线程元数据。"""
    project = source.name
    origin, address, archive_url = _source_parts(source.url)
    now = datetime.now(timezone.utc)
    since = since or now
    since_iso = base.to_utc_iso(since)
    api_list = f"{origin}/archives/api/list/{quote(address, safe='@')}"

    thread_ids: list[str] = []
    complete = True
    for year, month in _months_range(since, now):
        ids, month_complete = _thread_ids_from_month(
            archive_url, year, month, max_pages
        )
        thread_ids.extend(ids)
        complete = complete and month_complete

    new_count = 0
    fetched_any_thread = False
    pending: list[tuple[dict, str]] = []
    for thread_id in dict.fromkeys(thread_ids):
        emails_url = f"{api_list}/thread/{thread_id}/emails/?format=json"
        response = base.http_get(emails_url)
        emails = response.json()
        if not isinstance(emails, list):
            log.warning("hyperkitty %s: thread %s 返回非列表", project, thread_id)
            continue
        fetched_any_thread = True
        hash_to_mid = {
            e.get("message_id_hash"): e.get("message_id")
            for e in emails if e.get("message_id_hash") and e.get("message_id")
        }
        for meta in emails:
            created = base.parse_dt(meta.get("date"))
            if not created:
                continue
            created_iso = base.to_utc_iso(created)
            if created_iso < since_iso:
                continue
            message_id = meta.get("message_id")
            message_hash = meta.get("message_id_hash")
            if not message_id or not message_hash:
                continue
            if db.item_exists(conn, "ml", project, message_id):
                continue

            detail_url = f"{api_list}/email/{message_hash}/?format=json"
            parent_mid = hash_to_mid.get(_parent_hash(meta.get("parent")))
            subject = meta.get("subject", "") or ""
            human_url = f"{archive_url}message/{message_hash}/"
            item = {
                "source": "ml", "project": project, "native_id": message_id,
                "message_id": message_id, "in_reply_to": parent_mid,
                "thread_root": thread_id, "author": _author(meta),
                "subject": subject, "kind": base.classify_subject(subject),
                "created_at": created_iso, "updated_at": created_iso,
                "activity_at": created_iso, "url": human_url, "labels": [],
                "body_excerpt": "",
                "raw_json": {
                    "message_hash": message_hash, "thread_hash": thread_id,
                    "parent_message_id": parent_mid, "official_api": True,
                },
            }
            pending.append((item, detail_url))

    # 正文详情彼此独立，受控并发显著缩短月度回填；SQLite 写入仍在主线程串行完成。
    def load_detail(pair: tuple[dict, str]) -> tuple[dict, str]:
        item, detail_url = pair
        try:
            detail = base.http_get(detail_url).json()
            return item, base.extract_excerpt(detail.get("content", ""))
        except Exception as exc:
            log.warning("hyperkitty %s: 邮件详情失败 %s: %s",
                        project, detail_url, exc)
            return item, ""

    with ThreadPoolExecutor(max_workers=6, thread_name_prefix="hyperkitty") as pool:
        for item, excerpt in pool.map(load_detail, pending):
            item["body_excerpt"] = excerpt
            if db.upsert_item(conn, item):
                new_count += 1

    if fetched_any_thread:
        # 官方数据成功后移除旧 mail-archive 镜像记录，避免同一邮件重复出现。
        with db.transaction(conn):
            conn.execute(
                "DELETE FROM items WHERE source='ml' AND project=? "
                "AND native_id LIKE 'ma:%' AND created_at>=?", (project, since_iso)
            )
        db.set_fetch_state(
            conn, "ml", project, last_fetch_at=base.to_utc_iso(now),
            last_seen_id="complete" if complete else "partial",
        )
    log.info("hyperkitty %s: fetched %d new (threads=%d, complete=%s)",
             project, new_count, len(thread_ids), complete)
    return new_count
