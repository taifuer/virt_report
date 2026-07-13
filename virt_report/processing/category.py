"""将社区条目保守地归类为功能、缺陷或其他。"""
from __future__ import annotations

import re


_BUG_RE = re.compile(
    r"(?<![a-z0-9])(?:fix(?:e[ds])?|bug|regression|crash|overflow|out[- ]of[- ]bounds|"
    r"use[- ]after[- ]free|deadlock|leak|corrupt(?:ion)?|failure|cve-\d+)(?![a-z0-9])|"
    r"修复|缺陷|漏洞|崩溃|越界|回归|泄漏|损坏",
    re.IGNORECASE,
)
_FEATURE_RE = re.compile(
    r"(?<![a-z0-9])(?:add|support|implement|enable|introduce|allow|expose|extend)"
    r"(?![a-z0-9])|新增|支持|实现|启用|引入|扩展",
    re.IGNORECASE,
)

CATEGORY_LABELS = {"feature": "功能", "bug": "缺陷", "other": "其他"}


def classify_change(kind: str | None, subject: str | None) -> str:
    """优先识别缺陷；证据不明确的 patch/RFC 不武断归为功能。"""
    if kind in {"bug", "security"}:
        return "bug"
    text = subject or ""
    if _BUG_RE.search(text):
        return "bug"
    if _FEATURE_RE.search(text):
        return "feature"
    return "other"


def category_label(category: str | None) -> str:
    return CATEGORY_LABELS.get(category or "", CATEGORY_LABELS["other"])
