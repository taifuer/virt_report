"""Offline, source-reviewed explanations, separate from generated reports."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from virt_report.source_paths import validate_call_path_metadata

FLOW_FILES = {"virtio-blk-read": "virtio-blk-read.json"}
FLOW_REPOSITORIES = {"QEMU": "qemu/qemu", "Linux": "torvalds/linux"}


@lru_cache(maxsize=1)
def get_io_scenarios() -> dict:
    """Build offline, independently reviewed interface/backend read-write traces."""
    from virt_report.io_scenarios import build_io_scenarios

    return build_io_scenarios(
        get_flow("virtio-blk-read"), Path(__file__).parent / "content" / "flows",
    )


@lru_cache(maxsize=8)
def get_flow(slug: str) -> dict | None:
    """Load an allowlisted, pinned-source teaching scenario without I/O services.

    Like article metadata, these checked-in objects are immutable to callers.
    Browser playback uses the embedded copy, never a runtime source checkout.
    """
    filename = FLOW_FILES.get(slug)
    if filename is None:
        return None
    path = Path(__file__).parent / "content" / "flows" / filename
    flow = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(flow, dict) or flow.get("id") != slug:
        raise ValueError("A flow must match its registered identifier")
    baselines = flow.get("baselines", [])
    if not isinstance(baselines, list) or not baselines:
        raise ValueError("A flow needs pinned repository baselines")
    commits = {}
    for baseline in baselines:
        if not isinstance(baseline, dict):
            raise ValueError("A flow baseline must be an object")
        project = baseline.get("project")
        commit = baseline.get("commit", "")
        if (project not in FLOW_REPOSITORIES or project in commits
                or not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit)):
            raise ValueError("A flow baseline must pin a supported repository")
        expected = f"https://github.com/{FLOW_REPOSITORIES[project]}/tree/{commit}"
        if baseline.get("url") != expected:
            raise ValueError("A flow baseline URL must match its pinned commit")
        commits[project] = commit
    sources = flow.get("sources")
    if not isinstance(sources, dict) or not sources:
        raise ValueError("A flow needs source references")
    for source in sources.values():
        if not isinstance(source, dict):
            raise ValueError("A flow source must be an object")
        project = source.get("project")
        source_path = source.get("path", "")
        line = source.get("line")
        if (project not in commits or not isinstance(source_path, str)
                or not re.fullmatch(r"[A-Za-z0-9_./-]+", source_path)
                or source_path.startswith("/") or ".." in source_path.split("/")
                or isinstance(line, bool) or not isinstance(line, int) or line < 1):
            raise ValueError("A flow source must identify a local repository file and line")
        expected = (f"https://github.com/{FLOW_REPOSITORIES[project]}/blob/"
                    f"{commits[project]}/{source_path}#L{line}")
        if source.get("url") != expected:
            raise ValueError("A flow source URL must match its pinned file and line")
    steps = flow.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("A flow needs replayable steps")
    step_ids = set()
    for step in steps:
        if not isinstance(step, dict):
            raise ValueError("A flow step must be an object")
        identifier = step.get("id", "")
        references = step.get("sourceIds", [])
        if (not isinstance(identifier, str) or not re.fullmatch(r"[a-z0-9-]+", identifier)
                or identifier in step_ids
                or not isinstance(references, list) or not references
                or any(not isinstance(reference, str) or reference not in sources
                       for reference in references)):
            raise ValueError("Every flow step needs a unique identifier and verified sources")
        step_ids.add(identifier)
    call_paths = flow.get("callPaths")
    if not isinstance(call_paths, list) or not call_paths:
        raise ValueError("A flow needs context-separated source paths")
    path_ids = set()
    for call_path in call_paths:
        if not isinstance(call_path, dict):
            raise ValueError("A source path must be an object")
        identifier = call_path.get("id", "")
        references = call_path.get("sourceIds", [])
        if (not isinstance(identifier, str) or not re.fullmatch(r"[a-z0-9-]+", identifier)
                or identifier in path_ids
                or any(not isinstance(call_path.get(key), str) or not call_path[key].strip()
                       for key in ("title", "context", "boundary"))
                or not isinstance(references, list) or not references
                or any(not isinstance(reference, str) or reference not in sources
                       for reference in references)):
            raise ValueError("A source path needs a unique identity, context and verified sources")
        path_ids.add(identifier)
        validate_call_path_metadata(call_path, sources)
    return flow


@lru_cache(maxsize=1)
def _content() -> dict:
    """Load trusted editorial metadata once; callers must not mutate entries."""
    path = Path(__file__).parent / "content" / "mechanisms.json"
    return json.loads(path.read_text(encoding="utf-8"))


def articles() -> tuple[dict, ...]:
    """Return source-reviewed articles in editorial order."""
    baselines = {slug: version for version, slugs in _content().get("specification_baselines", {}).items()
                 for slug in slugs}
    result = []
    for item in _content()["articles"]:
        article = dict(item)
        if item["slug"] in baselines:
            version = baselines[item["slug"]]
            article.setdefault("specifications", [{"label": f"VIRTIO {version} CS01",
                "url": f"https://docs.oasis-open.org/virtio/virtio/v{version}/cs01/virtio-v{version}-cs01.html"}])
        result.append(article)
    return tuple(result)


def redirects() -> tuple[dict, ...]:
    """Keep merged article URLs separate from the published catalogue."""
    return tuple({"slug": slug, **entry}
                 for slug, entry in _content().get("redirects", {}).items())


def get_redirect(slug: str) -> dict | None:
    """Resolve an explicitly registered old URL to a published article."""
    entry = next((item for item in redirects() if item["slug"] == slug), None)
    return entry if entry and get_article(entry["target"]) else None


@lru_cache(maxsize=1)
def topics() -> tuple[dict, ...]:
    """Group published articles by topic without displaying empty sections."""
    groups = []
    for topic in _content()["topics"]:
        members = tuple(article for article in articles()
                        if article["topic"] == topic["slug"])
        if members:
            overview = next((item for item in members if item["slug"] == topic.get("overview")), None)
            details = tuple(item for item in members if item is not overview)
            clusters = []
            if topic.get("clusters"):
                by_slug = {item["slug"]: item for item in details}
                listed = [slug for cluster in topic["clusters"] for slug in cluster["articles"]]
                if len(set(listed)) != len(listed) or set(listed) != set(by_slug):
                    raise ValueError("Topic clusters must cover detail articles exactly once")
                clusters = [{"title": cluster["title"], "articles": tuple(by_slug[slug] for slug in cluster["articles"])}
                            for cluster in topic["clusters"]]
            groups.append({**topic, "articles": members, "overview_article": overview,
                           "detail_articles": details, "article_clusters": clusters})
    return tuple(groups)


def topic_groups() -> tuple[dict, ...]:
    """Order editorial groups; public views use only their published topics."""
    published = {topic["slug"]: topic for topic in topics()}
    result = []
    for group in _content().get("groups", ()):
        members = []
        for topic in _content()["topics"]:
            if topic.get("group") != group["slug"]:
                continue
            item = {**published.get(topic["slug"], topic)}
            item.setdefault("articles", ())
            members.append(item)
        if members:
            result.append({**group, "topics": tuple(members),
                           "published_topics": tuple(item for item in members if item["articles"])})
    return tuple(result)


def get_topic(slug: str) -> dict | None:
    """Resolve a published editorial topic, distinct from community topics."""
    return next((topic for topic in topics()
                 if topic["slug"] == slug or slug in topic.get("aliases", ())), None)


def get_article(slug: str) -> dict | None:
    """Resolve only registered slugs, never a user-supplied template path."""
    return next((article for article in articles() if article["slug"] == slug), None)
