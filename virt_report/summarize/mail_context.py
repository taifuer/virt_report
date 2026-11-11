"""Bounded, source-linked prose selection for report evidence cards."""
from __future__ import annotations

import re


_WITHDRAWAL = re.compile(r"\b(?:revert(?:ed)?|unapplied|withdraw(?:n)?|do not merge|drop this)\b", re.I)
_DISCUSSION = re.compile(
    r"\b(?:regression|broken|incorrect|unsafe|race condition|disagree|cannot|can't|"
    r"depends? on|requires?|limitation|not safe|not ready|changes? (?:in|since)|"
    r"v\d+ changes|changelog)\b", re.I,
)
_REVIEW = re.compile(r"^(?:Reviewed|Acked|Tested)-by:", re.I | re.M)


def body(item: dict) -> str:
    """Old snapshots remain usable without inventing the missing full body."""
    return item.get("body_text") or item.get("body_excerpt") or ""


def priority(text: str) -> int:
    return 2 if _WITHDRAWAL.search(text) else 1 if _DISCUSSION.search(text) else 0


def excerpt(text: str, limit: int = 600) -> str:
    """Include opening context and the strongest later constraint within budget.

    Keep verbatim spans in original order. The gap marker makes omissions
    explicit; keyword matching is selection, never a verdict about patch state.
    """
    if len(text) <= limit:
        return text
    if limit < 120:
        return text[:max(0, limit - 1)] + "…" if limit > 0 else ""
    matches = (list(_WITHDRAWAL.finditer(text)) or list(_DISCUSSION.finditer(text))
               or list(_REVIEW.finditer(text)))
    late = [match for match in matches if match.end() > limit // 2]
    if not late:
        return text[:limit - 1] + "…"
    match = late[-1]
    marker = "\n[…]\n"
    opening = limit // 3
    remaining = limit - opening - len(marker)
    start = max(opening, match.start() - remaining // 3)
    # Shift the window back if near the end so surrounding context fits.
    start = max(opening, min(start, len(text) - remaining))
    return text[:opening] + marker + text[start:start + remaining]


def discussion_evidence(items: list[dict], already_selected: list[dict]) -> list[dict]:
    """Retain at most two substantive middle replies with their exact sources."""
    candidates = [item for item in items if item not in already_selected
                  and priority(body(item)) and item.get("url")]
    candidates.sort(key=lambda item: (priority(body(item)), item.get("activity_at") or
                                     item.get("created_at") or ""), reverse=True)
    selected = candidates[:2]
    selected.sort(key=lambda item: item.get("activity_at") or item.get("created_at") or "")
    return [{"excerpt": excerpt(body(item), 400), "url": item["url"],
             "at": item.get("activity_at") or item.get("created_at") or "",
             "author": item.get("author") or ""} for item in selected]
