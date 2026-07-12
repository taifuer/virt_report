"""通过 public-inbox Git epoch 增量采集 lore 邮件。

与 `/new.atom` 的 25 条窗口不同，Git 归档可按时间浅克隆并持续 fetch，既不会漏掉
高流量时段，也保留原始 RFC822 邮件的全部线程头。配置 URL 指向当前 epoch，完整
历史回填时可依次配置/调用更早 epoch。
"""
from __future__ import annotations

import logging
import subprocess
from datetime import datetime, timedelta, timezone
from email import policy
from email.header import decode_header, make_header
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote, urlsplit

from virt_report import db
from virt_report.config import PROJECT_ROOT
from . import base

log = logging.getLogger(__name__)
CACHE_DIR = PROJECT_ROOT / "data" / "lore"


def _run_git(args: list[str], *, git_dir: Path | None = None) -> bytes:
    cmd = ["git"]
    if git_dir is not None:
        cmd.append(f"--git-dir={git_dir}")
    cmd.extend(args)
    result = subprocess.run(cmd, check=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    return result.stdout


def _decode_header(value: str | None) -> str:
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


def _message_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
        if parsed is None:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _body_text(message) -> str:
    try:
        part = message.get_body(preferencelist=("plain",))
        if part is not None:
            return part.get_content()
    except Exception:
        pass
    if not message.is_multipart():
        try:
            return message.get_content()
        except Exception:
            payload = message.get_payload(decode=True)
            return payload.decode("utf-8", errors="replace") if payload else ""
    return ""


def _ensure_repo(url: str, project: str, since: datetime) -> Path:
    epoch = Path(urlsplit(url).path).stem if "/git/" in url else "current"
    cache = CACHE_DIR / f"{project}-{epoch}.git"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    # 留一天边界，避免上游提交时间/邮件 Date 时区差造成浅克隆截断。
    shallow_since = base.to_utc_iso(since - timedelta(days=1))
    if not cache.exists():
        _run_git(["clone", "--bare", f"--shallow-since={shallow_since}", url,
                  str(cache)])
    else:
        _run_git(["fetch", f"--shallow-since={shallow_since}", "origin", "HEAD"],
                 git_dir=cache)
        _run_git(["update-ref", "HEAD", "FETCH_HEAD"], git_dir=cache)
    return cache


def _commit_ids(cache: Path, since: datetime) -> list[str]:
    since_iso = base.to_utc_iso(since)
    output = _run_git(["log", "--reverse", f"--since={since_iso}",
                       "--format=%H", "HEAD"], git_dir=cache)
    return [line for line in output.decode().splitlines() if line]


def _message_from_commit(cache: Path, commit: str):
    raw = _run_git(["show", f"{commit}:m"], git_dir=cache)
    return BytesParser(policy=policy.default).parsebytes(raw)


def fetch(conn, source, *, since: datetime | None = None, max_pages: int = 8) -> int:
    """按邮件 Date 增量导入当前 public-inbox Git epoch。"""
    del max_pages  # Git 时间边界替代网页分页。
    project = source.name
    now = datetime.now(timezone.utc)
    since = since or (now - timedelta(days=3))
    since_iso = base.to_utc_iso(since)
    cache = _ensure_repo(source.url, project, since)

    new_count = 0
    oldest: str | None = None
    newest: str | None = None
    for commit in _commit_ids(cache, since):
        message = _message_from_commit(cache, commit)
        message_id = _strip_mid(message.get("Message-ID"))
        if not message_id:
            continue
        created = _message_date(message.get("Date"))
        if not created:
            continue
        created_iso = base.to_utc_iso(created)
        if created_iso < since_iso:
            continue
        oldest = min(oldest, created_iso) if oldest else created_iso
        newest = max(newest, created_iso) if newest else created_iso
        if db.item_exists(conn, "ml", project, message_id):
            continue

        raw_from = _decode_header(message.get("From"))
        addresses = getaddresses([raw_from])
        if addresses:
            name, address = addresses[0]
            author = f"{name} <{address}>" if name and address else name or address
        else:
            author = raw_from
        subject = _decode_header(message.get("Subject"))
        in_reply_to = _strip_mid(message.get("In-Reply-To"))
        item = {
            "source": "ml", "project": project, "native_id": message_id,
            "message_id": message_id, "in_reply_to": in_reply_to,
            "thread_root": None, "author": author, "subject": subject,
            "kind": base.classify_subject(subject), "created_at": created_iso,
            "updated_at": created_iso, "activity_at": created_iso,
            "url": f"https://lore.kernel.org/{project}/{quote(message_id, safe='')}/",
            "labels": [], "body_excerpt": base.extract_excerpt(_body_text(message)),
            "raw_json": {"in_reply_to": in_reply_to, "git_commit": commit,
                         "git_epoch": source.url},
        }
        if db.upsert_item(conn, item):
            new_count += 1

    # 只有 Git 同步和解析全部成功后才推进水位。
    db.set_fetch_state(
        conn, "ml", project, last_fetch_at=base.to_utc_iso(now),
        last_seen_id=newest or oldest or "empty",
    )
    log.info("lore-git %s: fetched %d new (%s..%s)", project, new_count,
             oldest, newest)
    return new_count
