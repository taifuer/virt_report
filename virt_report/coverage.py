"""Explicit collection windows, separate from the dates of stored messages.

An empty, fully scanned source covers its requested window. Conversely, a
database containing old and new messages does not prove that the gap was scanned.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from virt_report import db


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Add audit tables without inferring coverage for historical collections."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS collection_batches (
            batch_key TEXT PRIMARY KEY,
            requested_start TEXT NOT NULL,
            requested_end TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'running',
            finished_at TEXT
        );
        CREATE TABLE IF NOT EXISTS collection_scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_key TEXT NOT NULL,
            source TEXT NOT NULL,
            project TEXT NOT NULL,
            requested_start TEXT NOT NULL,
            requested_end TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL,
            status TEXT NOT NULL,
            page_limit INTEGER NOT NULL,
            error TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_collection_scans_source_window
            ON collection_scans(source, project, requested_start, requested_end);
        CREATE INDEX IF NOT EXISTS idx_collection_scans_batch
            ON collection_scans(batch_key, source, project);
    """)


def utc_iso(value: datetime | str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def start_batch(conn, key: str, start: datetime, end: datetime) -> dict:
    ensure_schema(conn)
    with db.transaction(conn):
        conn.execute(
            "INSERT OR IGNORE INTO collection_batches(batch_key,requested_start,requested_end) "
            "VALUES(?,?,?)", (key, utc_iso(start), utc_iso(end)),
        )
    return dict(conn.execute("SELECT * FROM collection_batches WHERE batch_key=?", (key,)).fetchone())


def finish_batch(conn, key: str, status: str) -> None:
    with db.transaction(conn):
        conn.execute("UPDATE collection_batches SET status=?,finished_at=? WHERE batch_key=?",
                     (status, db.now_utc_iso(), key))


def source_retry(conn, key: str, source: str, project: str, since: str, pages: int) -> tuple[bool, str, int]:
    """Skip this batch's successful sources; retain unfinished historical starts."""
    rows = conn.execute(
        "SELECT * FROM collection_scans WHERE batch_key=? AND source=? AND project=? ORDER BY id DESC",
        (key, source, project),
    ).fetchall()
    if any(row["status"] == "complete" for row in rows):
        return True, since, pages
    unfinished = conn.execute(
        "SELECT MIN(s.requested_start) FROM collection_scans s "
        "WHERE s.source=? AND s.project=? AND s.status!='complete' AND NOT EXISTS ("
        "SELECT 1 FROM collection_scans c WHERE c.source=s.source AND c.project=s.project "
        "AND c.status='complete' AND c.requested_start<=s.requested_start "
        "AND c.requested_end>=s.requested_end)", (source, project),
    ).fetchone()[0]
    if unfinished:
        since = min(since, unfinished)
    # Grow beyond the same first pages on retries, including subsequent cron jobs.
    previous = conn.execute(
        "SELECT status,page_limit FROM collection_scans WHERE source=? AND project=? ORDER BY id DESC LIMIT 1",
        (source, project),
    ).fetchone()
    if previous and previous["status"] in {"partial", "failed"}:
        prior_limit = previous["page_limit"]
        if previous["status"] == "partial":
            prior_limit = min(4096, prior_limit * 2)
        pages = max(pages, prior_limit)
    return False, since, pages


def record_scan(conn, *, key: str, source: str, project: str, start: str, end: str,
                started_at: str, status: str, page_limit: int, error: str | None = None) -> None:
    with db.transaction(conn):
        conn.execute(
            "INSERT INTO collection_scans(batch_key,source,project,requested_start,requested_end,"
            "started_at,finished_at,status,page_limit,error) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (key, source, project, start, end, started_at, db.now_utc_iso(), status, page_limit, error),
        )


def _gaps(start: str, end: str, intervals: list[tuple[str, str]]) -> list[dict]:
    missing = []
    cursor = start
    for left, right in sorted(intervals):
        if right <= cursor or left >= end:
            continue
        if left > cursor:
            missing.append({"start": cursor, "end": min(left, end)})
        cursor = max(cursor, min(right, end))
    if cursor < end:
        missing.append({"start": cursor, "end": end})
    return missing


def report_coverage(conn, config, start: datetime | str, end: datetime | str) -> dict:
    """Return JSON-safe completeness for [start, end), never using item extrema.

    Legacy windows without explicit overlapping scan records remain unknown;
    callers must not permanently block historical reports on that basis.
    """
    # Read paths must remain read-only (the Web service can use a read-only DB).
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='collection_scans'"
    ).fetchone()
    start, end = utc_iso(start), utc_iso(end)
    sources = []
    expected = [("ml", item.name, f"{item.name} 邮件列表", True) for item in config.sources.mailing_lists]
    expected += [("gitlab", item.name, f"{item.name} GitLab", False) for item in config.sources.gitlab]
    for source, project, label, required in expected:
        rows = conn.execute(
            "SELECT requested_start,requested_end,status FROM collection_scans "
            "WHERE source=? AND project=? AND requested_start<? AND requested_end>?",
            (source, project, end, start),
        ).fetchall() if exists else []
        gaps = _gaps(start, end, [(row["requested_start"], row["requested_end"])
                                for row in rows if row["status"] == "complete"])
        status = "complete" if not gaps else "partial" if rows else "unknown"
        sources.append({"source": source, "project": project, "label": label,
                        "required": required, "status": status, "gaps": gaps})
    status = ("unknown" if not sources or all(s["status"] == "unknown" for s in sources)
              else "complete" if all(s["status"] == "complete" for s in sources) else "partial")
    missing = [s["label"] for s in sources if s["status"] != "complete"]
    return {"status": status, "start": start, "end": end, "sources": sources,
            "missing_sources": missing,
            "wait_recommended": status == "partial" and any(
                s["required"] and s["status"] != "complete" for s in sources)}
