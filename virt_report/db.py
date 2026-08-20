"""SQLite 存储层。

所有时间戳以 UTC ISO8601 字符串存储。表设计:

- items: 归一化后的所有条目 (邮件/issue/MR/note)，UNIQUE(source, native_id) 去重
- threads: 线程/issue 聚合视图 (处理层物化)
- reports: 已生成报告 (period + period_key 唯一)
- fetch_state: 每个源的增量游标
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source        TEXT NOT NULL,          -- ml | gitlab
    project       TEXT NOT NULL,          -- qemu-devel | libvirt | ...
    native_id     TEXT NOT NULL,          -- ML: message_id; GL: 'issue:891' etc.
    message_id    TEXT,                   -- ML message-id (GL 为空)
    in_reply_to   TEXT,                   -- ML in-reply-to
    thread_root   TEXT,                   -- 线程根 message-id (处理层填)
    author        TEXT,
    subject       TEXT,
    kind          TEXT,                   -- patch|rfc|discussion|issue|mr|note|bug
    created_at    TEXT NOT NULL,          -- UTC ISO8601
    updated_at    TEXT NOT NULL,          -- UTC ISO8601
    activity_at   TEXT NOT NULL,          -- ML=created_at; GitLab=最近活动时间
    url           TEXT,
    labels        TEXT,                   -- JSON array
    body_excerpt  TEXT,
    raw_json      TEXT,
    first_fetched_at TEXT NOT NULL,
    UNIQUE(source, project, native_id)
);

CREATE TABLE IF NOT EXISTS threads (
    thread_key        TEXT PRIMARY KEY,   -- source:project:root_id
    subject           TEXT,
    source            TEXT NOT NULL,
    project           TEXT NOT NULL,
    kind              TEXT,
    message_count     INTEGER DEFAULT 0,
    participant_count INTEGER DEFAULT 0,
    patch_count       INTEGER DEFAULT 0,
    first_seen        TEXT,
    last_seen         TEXT,
    salience_score    REAL DEFAULT 0,
    topic_tag         TEXT,
    summary_cached    TEXT,
    url               TEXT
);

CREATE TABLE IF NOT EXISTS reports (
    period        TEXT NOT NULL,          -- daily | weekly | monthly
    period_key    TEXT NOT NULL,          -- 2026-07-12 | 2026-W28 | 2026-07
    tz            TEXT,
    generated_at  TEXT NOT NULL,
    content_json  TEXT NOT NULL,
    html_path     TEXT,
    item_count    INTEGER DEFAULT 0,
    model         TEXT,
    PRIMARY KEY (period, period_key)
);

CREATE TABLE IF NOT EXISTS report_generation_state (
    period        TEXT NOT NULL,          -- daily | weekly | monthly
    period_key    TEXT NOT NULL,
    status        TEXT NOT NULL,          -- running | retry_wait | failed
    attempt       INTEGER NOT NULL DEFAULT 1,
    scheduled_at  TEXT,
    updated_at    TEXT NOT NULL,
    retry_at      TEXT,
    error         TEXT,
    PRIMARY KEY (period, period_key)
);

CREATE TABLE IF NOT EXISTS fetch_state (
    source        TEXT NOT NULL,
    project       TEXT NOT NULL,
    last_fetch_at TEXT,
    last_seen_id  TEXT,
    PRIMARY KEY (source, project)
);

CREATE TABLE IF NOT EXISTS fetch_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source        TEXT NOT NULL,
    project       TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT NOT NULL,
    success       INTEGER NOT NULL,
    complete      INTEGER NOT NULL DEFAULT 0,
    new_count     INTEGER NOT NULL DEFAULT 0,
    requested_since TEXT,
    coverage_start TEXT,
    coverage_end   TEXT,
    error         TEXT
);
CREATE INDEX IF NOT EXISTS idx_fetch_runs_source
    ON fetch_runs(source, project, finished_at);

CREATE TABLE IF NOT EXISTS topic_indexed_threads (
    thread_key        TEXT PRIMARY KEY,
    indexed_last_seen TEXT,
    rule_version      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS topic_entries (
    topic_key       TEXT NOT NULL,
    thread_key      TEXT NOT NULL,
    project         TEXT NOT NULL,
    source          TEXT NOT NULL,
    title           TEXT NOT NULL,
    url             TEXT,
    summary         TEXT,
    activity_at     TEXT,
    category        TEXT,
    architectures   TEXT,
    cve_ids         TEXT,
    security_type   TEXT,
    status          TEXT,
    PRIMARY KEY (topic_key, thread_key)
);
CREATE INDEX IF NOT EXISTS idx_topic_entries_activity
    ON topic_entries(topic_key, activity_at DESC);

CREATE TABLE IF NOT EXISTS topic_snapshots (
    topic_key       TEXT PRIMARY KEY,
    rule_version    INTEGER NOT NULL,
    generated_at    TEXT NOT NULL,
    content_json    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scheduler_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    identity      TEXT NOT NULL,
    job_name      TEXT NOT NULL,
    scheduled_at  TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    status        TEXT NOT NULL,
    attempt       INTEGER NOT NULL DEFAULT 1,
    exit_code     INTEGER,
    error         TEXT
);
CREATE INDEX IF NOT EXISTS idx_scheduler_runs_identity
    ON scheduler_runs(identity, started_at DESC);

CREATE TABLE IF NOT EXISTS conference_editions (
    venue         TEXT NOT NULL,
    year          INTEGER NOT NULL,
    source_url    TEXT,
    fetched_at    TEXT NOT NULL,
    source_status TEXT NOT NULL DEFAULT 'ok',
    paper_count   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (venue, year)
);

CREATE TABLE IF NOT EXISTS conference_papers (
    paper_id      TEXT PRIMARY KEY,
    venue         TEXT NOT NULL,
    year          INTEGER NOT NULL,
    title         TEXT NOT NULL,
    authors_json  TEXT NOT NULL DEFAULT '[]',
    affiliations_json TEXT NOT NULL DEFAULT '[]',
    institutions_json TEXT NOT NULL DEFAULT '[]',
    affiliation_source TEXT,
    affiliation_source_url TEXT,
    affiliation_verified_at TEXT,
    abstract      TEXT,
    doi           TEXT,
    official_url  TEXT,
    source_url    TEXT,
    fetched_at    TEXT NOT NULL,
    raw_json      TEXT
);
CREATE INDEX IF NOT EXISTS idx_conference_papers_year
    ON conference_papers(year DESC, venue);

CREATE TABLE IF NOT EXISTS conference_reviews (
    paper_id           TEXT PRIMARY KEY,
    relevance          TEXT NOT NULL,
    relevance_reason   TEXT,
    relation           TEXT,
    topics_json        TEXT NOT NULL DEFAULT '[]',
    architectures_json TEXT NOT NULL DEFAULT '[]',
    introduction_zh    TEXT,
    commentary         TEXT,
    representative     INTEGER NOT NULL DEFAULT 0,
    reviewed_at        TEXT NOT NULL,
    FOREIGN KEY (paper_id) REFERENCES conference_papers(paper_id)
        ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_conference_reviews_relevance
    ON conference_reviews(relevance, representative);

CREATE TABLE IF NOT EXISTS search_documents (
    document_key   TEXT PRIMARY KEY,
    thread_key     TEXT,
    project        TEXT NOT NULL,
    project_label  TEXT NOT NULL,
    source         TEXT NOT NULL,
    source_label   TEXT NOT NULL,
    kind           TEXT,
    category       TEXT NOT NULL DEFAULT 'other',
    architectures  TEXT NOT NULL DEFAULT '[]',
    topics         TEXT NOT NULL DEFAULT '[]',
    original_title TEXT NOT NULL,
    title_zh       TEXT,
    summary         TEXT,
    impact          TEXT,
    search_text     TEXT NOT NULL,
    first_seen      TEXT,
    last_seen       TEXT,
    url             TEXT,
    curated         INTEGER NOT NULL DEFAULT 0,
    salience_score  REAL NOT NULL DEFAULT 0,
    report_refs     TEXT NOT NULL DEFAULT '[]',
    indexed_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_search_documents_filters
    ON search_documents(curated, project, category, last_seen DESC);
CREATE INDEX IF NOT EXISTS idx_search_documents_architectures
    ON search_documents(architectures);

CREATE VIRTUAL TABLE IF NOT EXISTS search_documents_fts USING fts5(
    document_key UNINDEXED,
    title_zh,
    original_title,
    summary,
    impact,
    tags,
    tokenize='trigram'
);

CREATE TABLE IF NOT EXISTS search_index_state (
    singleton      INTEGER PRIMARY KEY CHECK (singleton = 1),
    refreshed_at   TEXT NOT NULL,
    document_count INTEGER NOT NULL,
    curated_count  INTEGER NOT NULL
);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.executescript(SCHEMA)
    _migrate_items_v2(conn)
    _migrate_conference_metadata(conn)
    conn.executescript("""
        CREATE INDEX IF NOT EXISTS idx_items_created ON items(created_at);
        CREATE INDEX IF NOT EXISTS idx_items_activity ON items(activity_at);
        CREATE INDEX IF NOT EXISTS idx_items_thread  ON items(thread_root);
        CREATE INDEX IF NOT EXISTS idx_items_project ON items(project);
    """)
    return conn


def _migrate_conference_metadata(conn: sqlite3.Connection) -> None:
    """Add affiliation provenance fields to pre-existing catalogues."""
    columns = {row[1] for row in conn.execute(
        "PRAGMA table_info(conference_papers)"
    )}
    additions = {
        "affiliations_json": "TEXT NOT NULL DEFAULT '[]'",
        "institutions_json": "TEXT NOT NULL DEFAULT '[]'",
        "affiliation_source": "TEXT",
        "affiliation_source_url": "TEXT",
        "affiliation_verified_at": "TEXT",
    }
    missing = [(name, declaration) for name, declaration in additions.items()
               if name not in columns]
    if not missing:
        return
    with transaction(conn):
        for name, declaration in missing:
            conn.execute(
                f"ALTER TABLE conference_papers ADD COLUMN {name} {declaration}"
            )


def _migrate_items_v2(conn: sqlite3.Connection) -> None:
    """迁移旧 items 表：项目级唯一键，并补齐统一活动时间。

    SQLite 不能直接修改 UNIQUE 约束，所以仅在检测到旧表时原子重建。
    线程表是物化数据，后续 fetch/报告流程会全量重建，无需在此迁移。
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(items)")}
    table_sql_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='items'"
    ).fetchone()
    table_sql = (table_sql_row[0] if table_sql_row else "").lower().replace(" ", "")
    has_project_unique = "unique(source,project,native_id)" in table_sql
    if "activity_at" in cols and has_project_unique:
        return

    with transaction(conn):
        conn.execute("ALTER TABLE items RENAME TO items_legacy")
        conn.execute("""
            CREATE TABLE items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                project TEXT NOT NULL,
                native_id TEXT NOT NULL,
                message_id TEXT,
                in_reply_to TEXT,
                thread_root TEXT,
                author TEXT,
                subject TEXT,
                kind TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                activity_at TEXT NOT NULL,
                url TEXT,
                labels TEXT,
                body_excerpt TEXT,
                raw_json TEXT,
                first_fetched_at TEXT NOT NULL,
                UNIQUE(source, project, native_id)
            )
        """)
        activity_expr = (
            "COALESCE(activity_at, CASE WHEN source='gitlab' THEN updated_at ELSE created_at END)"
            if "activity_at" in cols else
            "CASE WHEN source='gitlab' THEN updated_at ELSE created_at END"
        )
        conn.execute(f"""
            INSERT INTO items (
                id, source, project, native_id, message_id, in_reply_to, thread_root,
                author, subject, kind, created_at, updated_at, activity_at, url,
                labels, body_excerpt, raw_json, first_fetched_at
            )
            SELECT id, source, project, native_id, message_id, in_reply_to, thread_root,
                   author, subject, kind, created_at, updated_at, {activity_expr}, url,
                   labels, body_excerpt, raw_json, first_fetched_at
            FROM items_legacy
        """)
        conn.execute("DROP TABLE items_legacy")


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def start_scheduler_run(conn: sqlite3.Connection, *, identity: str, job_name: str,
                        scheduled_at: str, attempt: int) -> int:
    with transaction(conn):
        cursor = conn.execute(
            "INSERT INTO scheduler_runs "
            "(identity,job_name,scheduled_at,started_at,status,attempt) "
            "VALUES (?,?,?,?,?,?)",
            (identity, job_name, scheduled_at, now_utc_iso(), "running", attempt),
        )
    return int(cursor.lastrowid)


def finish_scheduler_run(conn: sqlite3.Connection, run_id: int, *, status: str,
                         exit_code: int | None = None, error: str | None = None) -> None:
    with transaction(conn):
        conn.execute(
            "UPDATE scheduler_runs SET finished_at=?,status=?,exit_code=?,error=? "
            "WHERE id=?", (now_utc_iso(), status, exit_code, error, run_id),
        )


def set_report_generation_state(
    conn: sqlite3.Connection,
    period: str,
    period_key: str,
    *,
    status: str,
    attempt: int = 1,
    scheduled_at: str | None = None,
    retry_at: str | None = None,
    error: str | None = None,
) -> None:
    """记录尚未正式发布的周期报告生成状态。"""
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO report_generation_state (
                period,period_key,status,attempt,scheduled_at,updated_at,retry_at,error
            ) VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(period,period_key) DO UPDATE SET
                status=excluded.status,
                attempt=excluded.attempt,
                scheduled_at=COALESCE(excluded.scheduled_at,
                                      report_generation_state.scheduled_at),
                updated_at=excluded.updated_at,
                retry_at=excluded.retry_at,
                error=excluded.error
            """,
            (period, period_key, status, attempt, scheduled_at, now_utc_iso(),
             retry_at, error),
        )


def get_report_generation_state(
    conn: sqlite3.Connection, period: str, period_key: str,
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM report_generation_state WHERE period=? AND period_key=?",
        (period, period_key),
    ).fetchone()


def list_report_generation_states(
    conn: sqlite3.Connection, period: str | None = None,
) -> list[sqlite3.Row]:
    if period:
        return conn.execute(
            "SELECT * FROM report_generation_state WHERE period=? "
            "ORDER BY period_key DESC", (period,),
        ).fetchall()
    return conn.execute(
        "SELECT * FROM report_generation_state "
        "ORDER BY period,period_key DESC"
    ).fetchall()


def clear_report_generation_state(
    conn: sqlite3.Connection, period: str, period_key: str,
) -> None:
    with transaction(conn):
        conn.execute(
            "DELETE FROM report_generation_state WHERE period=? AND period_key=?",
            (period, period_key),
        )


def upsert_item(conn: sqlite3.Connection, item: dict[str, Any]) -> bool:
    """插入或更新一条 item。返回 True 表示新增，False 表示已存在(仅更新)。"""
    existed = item_exists(conn, item["source"], item["project"], item["native_id"])
    labels = item.get("labels") or []
    raw = item.get("raw_json")
    params = {
        "source": item["source"],
        "project": item["project"],
        "native_id": item["native_id"],
        "message_id": item.get("message_id"),
        "in_reply_to": item.get("in_reply_to"),
        "thread_root": item.get("thread_root"),
        "author": item.get("author"),
        "subject": item.get("subject"),
        "kind": item.get("kind"),
        "created_at": item["created_at"],
        "updated_at": item["updated_at"],
        "activity_at": item.get("activity_at") or (
            item["updated_at"] if item["source"] == "gitlab" else item["created_at"]
        ),
        "url": item.get("url"),
        "labels": json.dumps(labels, ensure_ascii=False),
        "body_excerpt": item.get("body_excerpt"),
        "raw_json": json.dumps(raw, ensure_ascii=False) if raw is not None else None,
        "first_fetched_at": item.get("first_fetched_at") or now_utc_iso(),
    }
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO items (
                source, project, native_id, message_id, in_reply_to, thread_root,
                author, subject, kind, created_at, updated_at, activity_at, url, labels,
                body_excerpt, raw_json, first_fetched_at
            ) VALUES (
                :source, :project, :native_id, :message_id, :in_reply_to, :thread_root,
                :author, :subject, :kind, :created_at, :updated_at, :activity_at, :url, :labels,
                :body_excerpt, :raw_json, :first_fetched_at
            )
            ON CONFLICT(source, project, native_id) DO UPDATE SET
                updated_at = excluded.updated_at,
                activity_at = excluded.activity_at,
                subject = COALESCE(NULLIF(excluded.subject, ''), items.subject),
                author = COALESCE(NULLIF(excluded.author, ''), items.author),
                kind = COALESCE(NULLIF(excluded.kind, ''), items.kind),
                url = COALESCE(NULLIF(excluded.url, ''), items.url),
                body_excerpt = COALESCE(NULLIF(excluded.body_excerpt, ''), items.body_excerpt),
                raw_json = COALESCE(excluded.raw_json, items.raw_json),
                labels = excluded.labels
            """,
            params,
        )
    return not existed


def item_exists(conn: sqlite3.Connection, source: str, project: str, native_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM items WHERE source=? AND project=? AND native_id=? LIMIT 1",
        (source, project, native_id),
    ).fetchone()
    return row is not None


def get_items_in_window(
    conn: sqlite3.Connection,
    start_utc: str,
    end_utc: str,
    source: str | None = None,
    kinds: list[str] | None = None,
) -> list[sqlite3.Row]:
    """取 [start, end) UTC 时间窗内的 items (按 created_at)。"""
    sql = "SELECT * FROM items WHERE created_at >= ? AND created_at < ?"
    params: list[Any] = [start_utc, end_utc]
    if source:
        sql += " AND source = ?"
        params.append(source)
    if kinds:
        placeholders = ",".join("?" * len(kinds))
        sql += f" AND kind IN ({placeholders})"
        params.extend(kinds)
    sql += " ORDER BY created_at ASC"
    return conn.execute(sql, params).fetchall()


def get_activity_items_in_window(
    conn: sqlite3.Connection,
    start_utc: str,
    end_utc: str,
) -> list[sqlite3.Row]:
    """取时间窗内发生过活动的条目；ML 活动即发信，GitLab 活动即最近更新。"""
    return conn.execute(
        "SELECT * FROM items WHERE activity_at >= ? AND activity_at < ? "
        "ORDER BY activity_at ASC",
        (start_utc, end_utc),
    ).fetchall()


def upsert_thread(conn: sqlite3.Connection, t: dict[str, Any]) -> None:
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO threads (
                thread_key, subject, source, project, kind, message_count,
                participant_count, patch_count, first_seen, last_seen,
                salience_score, topic_tag, summary_cached, url
            ) VALUES (
                :thread_key, :subject, :source, :project, :kind, :message_count,
                :participant_count, :patch_count, :first_seen, :last_seen,
                :salience_score, :topic_tag, :summary_cached, :url
            )
            ON CONFLICT(thread_key) DO UPDATE SET
                subject = COALESCE(excluded.subject, threads.subject),
                kind = COALESCE(excluded.kind, threads.kind),
                message_count = excluded.message_count,
                participant_count = excluded.participant_count,
                patch_count = excluded.patch_count,
                first_seen = excluded.first_seen,
                last_seen = excluded.last_seen,
                salience_score = excluded.salience_score,
                topic_tag = COALESCE(excluded.topic_tag, threads.topic_tag),
                url = COALESCE(excluded.url, threads.url)
            """,
            {
                "thread_key": t["thread_key"],
                "subject": t.get("subject"),
                "source": t["source"],
                "project": t.get("project"),
                "kind": t.get("kind"),
                "message_count": t.get("message_count", 0),
                "participant_count": t.get("participant_count", 0),
                "patch_count": t.get("patch_count", 0),
                "first_seen": t.get("first_seen"),
                "last_seen": t.get("last_seen"),
                "salience_score": t.get("salience_score", 0.0),
                "topic_tag": t.get("topic_tag"),
                "summary_cached": t.get("summary_cached"),
                "url": t.get("url"),
            },
        )


def get_top_threads(
    conn: sqlite3.Connection,
    start_utc: str,
    end_utc: str,
    limit: int = 30,
) -> list[sqlite3.Row]:
    """取时间窗内 salience 最高的线程 (按 last_seen 落在窗内)。"""
    rows = conn.execute(
        """
        SELECT * FROM threads
        WHERE last_seen >= ? AND last_seen < ?
        ORDER BY salience_score DESC, message_count DESC
        LIMIT ?
        """,
        (start_utc, end_utc, limit),
    ).fetchall()
    return rows


def get_top_threads_for_projects(
    conn: sqlite3.Connection, start_utc: str, end_utc: str,
    projects: tuple[str, ...], limit: int,
) -> list[sqlite3.Row]:
    """按项目组独立取 Top 线程，避免高流量项目挤掉其他项目。"""
    placeholders = ",".join("?" for _ in projects)
    return conn.execute(
        f"""
        SELECT * FROM threads
        WHERE last_seen >= ? AND last_seen < ?
          AND project IN ({placeholders})
        ORDER BY salience_score DESC, message_count DESC
        LIMIT ?
        """,
        (start_utc, end_utc, *projects, limit),
    ).fetchall()


def save_report(
    conn: sqlite3.Connection,
    period: str,
    period_key: str,
    content: dict[str, Any],
    tz: str,
    html_path: str | None = None,
    item_count: int = 0,
    model: str | None = None,
) -> None:
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO reports (period, period_key, tz, generated_at, content_json, html_path, item_count, model)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(period, period_key) DO UPDATE SET
                tz = excluded.tz,
                generated_at = excluded.generated_at,
                content_json = excluded.content_json,
                html_path = excluded.html_path,
                item_count = excluded.item_count,
                model = excluded.model
            """,
            (
                period, period_key, tz, now_utc_iso(),
                json.dumps(content, ensure_ascii=False),
                html_path, item_count, model,
            ),
        )
        # 正式内容写入与生成中状态清理保持在同一事务内；读取端不会看到半发布状态。
        conn.execute(
            "DELETE FROM report_generation_state WHERE period=? AND period_key=?",
            (period, period_key),
        )


def get_report(conn: sqlite3.Connection, period: str, period_key: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM reports WHERE period=? AND period_key=?",
        (period, period_key),
    ).fetchone()


def get_fetch_state(conn: sqlite3.Connection, source: str, project: str) -> dict[str, str | None]:
    row = conn.execute(
        "SELECT * FROM fetch_state WHERE source=? AND project=?",
        (source, project),
    ).fetchone()
    if not row:
        return {"last_fetch_at": None, "last_seen_id": None}
    return {"last_fetch_at": row["last_fetch_at"], "last_seen_id": row["last_seen_id"]}


def set_fetch_state(
    conn: sqlite3.Connection,
    source: str,
    project: str,
    last_fetch_at: str | None = None,
    last_seen_id: str | None = None,
) -> None:
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO fetch_state (source, project, last_fetch_at, last_seen_id)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(source, project) DO UPDATE SET
                last_fetch_at = COALESCE(excluded.last_fetch_at, fetch_state.last_fetch_at),
                last_seen_id = COALESCE(excluded.last_seen_id, fetch_state.last_seen_id)
            """,
            (source, project, last_fetch_at, last_seen_id),
        )


def record_fetch_run(
    conn: sqlite3.Connection, *, source: str, project: str, started_at: str,
    success: bool, complete: bool, new_count: int, requested_since: str | None,
    error: str | None = None,
) -> None:
    """记录一次源级采集结果，供完整性审计与告警使用。"""
    coverage = conn.execute(
        "SELECT MIN(activity_at) AS start, MAX(activity_at) AS end "
        "FROM items WHERE source=? AND project=?", (source, project)
    ).fetchone()
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO fetch_runs (
                source, project, started_at, finished_at, success, complete,
                new_count, requested_since, coverage_start, coverage_end, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (source, project, started_at, now_utc_iso(), int(success), int(complete),
             new_count, requested_since, coverage["start"], coverage["end"], error),
        )
