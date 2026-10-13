"""Offline, source-reviewed explanations, separate from generated reports."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def _content() -> dict:
    """Load trusted editorial metadata once; callers must not mutate entries."""
    path = Path(__file__).parent / "content" / "mechanisms.json"
    return json.loads(path.read_text(encoding="utf-8"))


def articles() -> tuple[dict, ...]:
    """Return source-reviewed articles in editorial order."""
    return tuple(_content()["articles"])


@lru_cache(maxsize=1)
def topics() -> tuple[dict, ...]:
    """Group published articles by topic without displaying empty sections."""
    groups = []
    for topic in _content()["topics"]:
        members = tuple(article for article in articles()
                        if article["topic"] == topic["slug"])
        if members:
            groups.append({**topic, "articles": members})
    return tuple(groups)


def get_topic(slug: str) -> dict | None:
    """Resolve a published editorial topic, distinct from community topics."""
    return next((topic for topic in topics() if topic["slug"] == slug), None)


def get_article(slug: str) -> dict | None:
    """Resolve only registered slugs, never a user-supplied template path."""
    return next((article for article in articles() if article["slug"] == slug), None)
