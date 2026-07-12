"""GNU pipermail mbox 采集器 (qemu-devel)。

lists.gnu.org / lists.nongnu.org 提供整月 mbox 下载 (无反爬)，含完整邮件头
(Message-ID / In-Reply-To / Subject / Date)，是历史回填与正确线程化的理想源。
每月 mbox 约 50MB，本地缓存；仅当月会重新拉取 (mbox 持续追加)。
"""
from __future__ import annotations

import logging
import mailbox
import sqlite3
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote

from virt_report import db
from virt_report.config import PROJECT_ROOT
from . import base

log = logging.getLogger(__name__)
CACHE_DIR = PROJECT_ROOT / "data" / "mbox"


def _decode_hdr(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _strip_mid(value: str | None) -> str | None:
    if not value:
        return None
    parts = value.strip().strip("<>").split()
    return parts[0] if parts else None


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _body_text(msg) -> str:
    """取邮件纯文本正文。"""
    try:
        part = msg.get_body(preferencelist=("plain",))
    except Exception:
        part = None
    if part is not None:
        try:
            return part.get_content()
        except Exception:
            payload = part.get_payload(decode=True)
            if payload:
                return payload.decode("utf-8", errors="replace")
    # 兜底：第一段 text/plain
    if msg.is_multipart():
        for p in msg.walk():
            if p.get_content_type() == "text/plain":
                payload = p.get_payload(decode=True)
                if payload:
                    return payload.decode("utf-8", errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            return payload.decode("utf-8", errors="replace")
    return ""


def _months_range(start: datetime, end: datetime) -> list[str]:
    """返回 [start..end] 之间的 YYYY-MM 列表。"""
    months = []
    y, m = start.year, start.month
    ey, em = end.year, end.month
    while (y, m) <= (ey, em):
        months.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1
    return months


def _download(base_url: str, ym: str, dest: Path) -> tuple[bool, bool]:
    """下载月度 mbox。带 If-Modified-Since: 服务端未改则 304 跳过 (省 52MB 重下)。
    返回 (dest 可用, 上游同步成功)。缓存可在断网时降级使用，但不能推进水位。"""
    url = base_url.rstrip("/") + "/" + ym
    headers: dict = {}
    if dest.exists():
        # 用缓存文件 mtime 作 If-Modified-Since (下载成功后会把 mtime 设为服务端 Last-Modified)
        from email.utils import formatdate
        headers["If-Modified-Since"] = formatdate(dest.stat().st_mtime, usegmt=True)
    try:
        resp = base.http_get(url, headers=headers, retries=2)
    except Exception as e:
        log.warning("mbox %s: 下载失败 %s: %s", ym, url, e)
        return dest.exists(), False  # 失败时若已有缓存则用缓存，但标记不完整
    if resp.status_code == 304:
        log.info("mbox %s: 未修改 (304), 用缓存", ym)
        return True, True
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(resp.content)
    # 把文件 mtime 设为服务端 Last-Modified, 下次 If-Modified-Since 才能正确命中 304
    lm = resp.headers.get("Last-Modified")
    if lm:
        try:
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(lm)
            if dt:
                import os
                os.utime(dest, (dt.timestamp(), dt.timestamp()))
        except Exception:
            pass
    return True, True


def fetch(conn: sqlite3.Connection, source, *, since: datetime | None = None,
          max_pages: int = 1) -> int:
    """拉取并解析 mbox。"""
    project = source.name
    base_url = source.url
    now = datetime.now(timezone.utc)
    if since is None:
        since = now
    months = _months_range(since, now)
    since_iso = base.to_utc_iso(since)

    # 批量加载已存 native_id (避免逐条 item_exists 查询)
    existing = {r[0] for r in conn.execute(
        "SELECT native_id FROM items WHERE source='ml' AND project=?", (project,))}

    new_count = 0
    incomplete = False
    for ym in months:
        dest = CACHE_DIR / f"{project}-{ym}.mbox"
        usable, synced = _download(base_url, ym, dest)
        incomplete = incomplete or not synced
        if not usable:
            continue

        try:
            mb = mailbox.mbox(str(dest))
        except Exception as e:
            log.warning("mbox %s: 解析失败: %s", dest.name, e)
            continue

        month_new = 0
        for msg in mb:
            mid = _strip_mid(msg.get("Message-ID"))
            if not mid:
                continue
            native_id = mid
            if native_id in existing:
                continue
            dt = _parse_date(msg.get("Date")) or now
            created_iso = base.to_utc_iso(dt)
            if since_iso and created_iso < since_iso:
                continue

            subject = _decode_hdr(msg.get("Subject"))
            frm = _decode_hdr(msg.get("From"))
            # 提取姓名 <邮箱>
            addrs = getaddresses([frm])
            author = addrs[0][0] or addrs[0][1] or frm if addrs else frm
            in_reply_to = _strip_mid(msg.get("In-Reply-To"))
            body = _body_text(msg)

            item = {
                "source": "ml", "project": project, "native_id": native_id,
                "message_id": mid, "in_reply_to": in_reply_to, "thread_root": None,
                "author": author, "subject": subject,
                "kind": base.classify_subject(subject),
                "created_at": created_iso, "updated_at": created_iso,
                # mbox 无单消息页; 用 lore.kernel.org 的 message-id 链接 (验证 200 可访问)
                "url": f"https://lore.kernel.org/{project}/{quote(mid, safe='')}/",
                "labels": [], "body_excerpt": base.extract_excerpt(body),
                "raw_json": {"from": frm, "in_reply_to": in_reply_to},
            }
            if db.upsert_item(conn, item):
                month_new += 1
            existing.add(native_id)  # 防同次运行内重复
        new_count += month_new
        log.info("mbox %s %s: %d new", project, ym, month_new)

    if incomplete:
        raise RuntimeError(f"mbox {project} 至少一个月份未与上游同步，水位未推进")
    db.set_fetch_state(conn, "ml", project, last_fetch_at=base.to_utc_iso(now),
                       last_seen_id="complete")
    log.info("mbox %s: fetched %d new (months=%s)", project, new_count, ",".join(months))
    return new_count
