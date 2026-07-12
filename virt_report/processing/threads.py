"""线程重建与聚合。

线程根 (thread_root) 三种来源:
  1. 预设 (HyperKitty 的 thread hash) -- 直接采用
  2. in-reply-to 链回溯 (lore) -- 父 message_id 在库则上溯
  3. 主题折叠 (mail-archive 无 in-reply-to) -- 按规范化 subject 分组
GitLab 每个 issue/MR 自成一线程。聚合后写 threads 表并打显著性分。
全量重建，幂等 (数据量小且为近期)。
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3

from virt_report import db
from . import classify, rank

log = logging.getLogger(__name__)

_BRACKET_RE = re.compile(r"\[(patch|rfc|stable|resend|v\d+)[^\]]*\]", re.IGNORECASE)
# 系列标记: [PATCH v3 5/9] / [RFC PATCH 115/134] / [Stable-10.0.12 38/75]
_SERIES_RE = re.compile(r"\[\s*([^\]]*?)\s+(\d+)/(\d+)\s*\]")


def _norm_subject(subject: str | None) -> str | None:
    """规范化主题用于折叠分组：去 Re:/Fwd:，把 [PATCH v3 5/9] 折成 [PATCH]。"""
    if not subject:
        return None
    s = classify._strip_leader(subject)
    s = _BRACKET_RE.sub(lambda m: "[" + m.group(1).upper() + "]", s)
    return s.lower().strip()


def _thread_key(subject: str | None, author: str | None, day: str | None) -> str | None:
    """线程分组键。系列邮件 ([... n/N]) 按 (类型, 总数 N, 作者, 日) 分组；
    其余按规范化主题分组。"""
    if not subject:
        return None
    s = classify._strip_leader(subject)
    m = _SERIES_RE.search(s)
    if m:
        bracket = m.group(1).strip().upper() or "PATCH"
        total = m.group(3)
        return f"series:{bracket}|{total}|{(author or '')[:40]}|{day or ''}"
    s = _BRACKET_RE.sub(lambda mm: "[" + mm.group(1).upper() + "]", s)
    return "subj:" + s.lower().strip()


def _compute_ml_roots(conn: sqlite3.Connection) -> dict[tuple[str, str], str]:
    """返回 (project, native_id) -> thread_root 映射 (仅 ml 条目)。

    预设根 (HyperKitty) 从 raw_json.thread_hash 取，而非 items.thread_root 列
    (后者会被本函数上次运行写脏)。
    """
    rows = conn.execute(
        "SELECT project, native_id, message_id, in_reply_to, thread_root, subject, author, created_at, raw_json "
        "FROM items WHERE source='ml'"
    ).fetchall()
    preset: dict[tuple[str, str], str] = {}
    for r in rows:
        try:
            rj = json.loads(r["raw_json"] or "{}")
        except (TypeError, ValueError):
            rj = {}
        th = rj.get("thread_hash")
        if th:  # HyperKitty 采集器写入的线程 hash
            preset[(r["project"], r["native_id"])] = th
    mid_to_nid = {
        (r["project"], r["message_id"]): r["native_id"]
        for r in rows if r["message_id"]
    }
    irt_of = {
        (r["project"], r["native_id"]): r["in_reply_to"] for r in rows
    }

    roots: dict[tuple[str, str], str] = {}
    key_root: dict[tuple[str, str], str] = {}  # (project, 分组键) -> 根

    def chain_root(project: str, nid: str) -> str:
        seen: set[tuple[str, str]] = set()
        cur = nid
        while True:
            current_key = (project, cur)
            if current_key in seen:
                break
            seen.add(current_key)
            irt = irt_of.get(current_key)
            if not irt:
                break
            parent = mid_to_nid.get((project, irt))  # 仅在同项目内找父邮件
            if not parent:
                break
            cur = parent
        return cur

    for r in rows:
        project = r["project"]
        nid = r["native_id"]
        item_key = (project, nid)
        if item_key in preset:
            roots[item_key] = preset[item_key]
        elif r["in_reply_to"]:
            roots[item_key] = chain_root(project, nid)
        elif r["message_id"]:
            # 有 message_id 无 in_reply_to = 真正的线程根 (mbox/lore)
            roots[item_key] = nid
        else:
            # 无 message_id (mail-archive) -> 主题折叠
            day = (r["created_at"] or "")[:10]
            key = _thread_key(r["subject"], r["author"], day)
            if key:
                project_key = (project, key)
                key_root.setdefault(project_key, nid)
                roots[item_key] = key_root[project_key]
            else:
                roots[item_key] = nid
    return roots


def rebuild_threads(conn: sqlite3.Connection) -> int:
    """重建所有线程聚合。返回线程数。"""
    roots = _compute_ml_roots(conn)

    # 更新 ml 条目的 thread_root (按 native_id)
    with db.transaction(conn):
        conn.executemany(
            "UPDATE items SET thread_root=? WHERE project=? AND native_id=? AND source='ml'",
            [(root, project, nid) for (project, nid), root in roots.items()],
        )

    # 清掉旧线程 (全量重建，避免已删 items 的脏线程残留)
    with db.transaction(conn):
        conn.execute("DELETE FROM threads")

    # 按线程分组聚合 (ml 用 thread_root，gitlab thread_root=native_id)
    groups = conn.execute(
        """
        SELECT source, project, thread_root,
               MIN(created_at) AS first_seen, MAX(activity_at) AS last_seen,
               COUNT(*) AS cnt
        FROM items
        WHERE thread_root IS NOT NULL
        GROUP BY source, project, thread_root
        """
    ).fetchall()

    n = 0
    for g in groups:
        items = conn.execute(
            "SELECT * FROM items WHERE source=? AND project=? AND thread_root=? "
            "ORDER BY created_at ASC",
            (g["source"], g["project"], g["thread_root"]),
        ).fetchall()
        if not items:
            continue

        kinds = [it["kind"] for it in items]
        subjects = [it["subject"] for it in items]
        authors = {it["author"] for it in items if it["author"]}
        subject = classify.best_subject(subjects)

        if g["source"] == "ml":
            message_count = len(items)
            participant_count = len(authors)
            patch_count = sum(1 for k in kinds if k == "patch")
        else:
            # gitlab: 一个 issue/MR，讨论量来自 notes
            rj = json.loads(items[0]["raw_json"] or "{}")
            notes = rj.get("user_notes_count", 0) or 0
            message_count = 1 + notes
            participant_count = 1
            patch_count = 0

        # 线程 url: ml 取根邮件 url，gitlab 取条目 url
        root_item = next((it for it in items if it["message_id"] == g["thread_root"]
                          or it["thread_root"] == it["native_id"]), items[0])
        url = root_item["url"]

        thread = {
            "thread_key": f"{g['source']}:{g['project']}:{g['thread_root']}",
            "subject": subject,
            "source": g["source"],
            "project": g["project"],
            "kind": classify.thread_kind(kinds),
            "message_count": message_count,
            "participant_count": participant_count,
            "patch_count": patch_count,
            "first_seen": g["first_seen"],
            "last_seen": g["last_seen"],
            "salience_score": 0.0,
            "topic_tag": classify.extract_topic(subject),
            "url": url,
        }
        thread["salience_score"] = rank.score(thread, items)
        db.upsert_thread(conn, thread)
        n += 1

    log.info("rebuilt %d threads", n)
    return n
