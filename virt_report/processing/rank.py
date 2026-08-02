"""显著性排序：给每个线程打分，用于挑选喂给 LLM 与展示的 Top N。"""
from __future__ import annotations

import json
import math

from .architecture import FOCUS_ARCHITECTURES, detect_architectures


def score(thread: dict, items: list) -> float:
    """计算线程 salience 分数。

    thread: 线程聚合 dict
    items:  该线程下的 item 行 (sqlite3.Row)
    """
    msg_count = thread.get("message_count", 0)
    participants = thread.get("participant_count", 0)
    kind = thread.get("kind", "discussion")
    source = thread.get("source", "ml")

    s = 0.0
    s += math.log1p(msg_count) * 1.5          # 讨论量
    s += math.log1p(participants) * 2.0       # 参与人数 (越多人讨论越热)
    if kind == "rfc":
        s += 3.0                              # RFC 设计讨论新闻价值高
    elif kind == "patch":
        s += 1.5
    elif kind == "security":
        s += 4.0
    elif kind == "bug":
        s += 1.0
    elif kind == "mr":
        s += 0.5

    if source == "gitlab":
        for it in items:
            rj = json.loads(it["raw_json"] or "{}")
            if rj.get("merged"):
                s += 2.0                      # 已合并 MR
            notes = rj.get("user_notes_count", 0)
            if notes and notes > 3:
                s += 1.0
            if rj.get("upvotes", 0) > 0:
                s += 0.5
    else:
        # 邮件线程：含 Reviewed-by/Acked-by 说明接近合入
        for it in items:
            excerpt = (it["body_excerpt"] or "").lower()
            if "reviewed-by" in excerpt or "acked-by" in excerpt:
                s += 1.0
                break

    # 安全/缺陷标签加分
    for it in items:
        labels = json.loads(it["labels"] or "[]")
        if any("security" in (l or "").lower() for l in labels):
            s += 3.0
            break

    # 产品关注方向：有原文证据的 x86/Arm 议题优先进入候选集。
    architecture_parts = [thread.get("subject"), thread.get("topic_tag")]
    for it in items:
        architecture_parts.extend((it["subject"], it["labels"]))
    architectures = detect_architectures(architecture_parts)
    s += 2.5 * len(FOCUS_ARCHITECTURES.intersection(architectures))
    if architectures and not FOCUS_ARCHITECTURES.intersection(architectures):
        s += 0.5

    return round(s, 2)
