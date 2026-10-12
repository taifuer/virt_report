"""Stable report-item links shared by rendering and offline indexes."""
from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from urllib.parse import urlsplit, urlunsplit


def canonical_project(value: object) -> str:
    """Normalize collector IDs and display names without fuzzy matching."""
    key = str(value or "").strip().casefold()
    return {"qemu-devel": "qemu", "libvir-list": "libvirt"}.get(key, key)


def source_url(value: object) -> str:
    """Only allow public HTTP(S) links, never script or credential-bearing URLs."""
    value = str(value or "").strip()
    if not value or any(character.isspace() or ord(character) < 32 for character in value):
        return ""
    try:
        parts = urlsplit(value)
        if (parts.scheme.lower() not in {"http", "https"} or not parts.hostname
                or parts.username is not None or parts.password is not None):
            return ""
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(),
                           parts.path, parts.query, ""))
    except ValueError:
        return ""


def report_item_anchor(project: object, url: object) -> str:
    """Keep an item address unchanged when its title, rank or T-number changes."""
    project_key, trusted_url = canonical_project(project), source_url(url)
    if project_key not in {"qemu", "kvm", "libvirt"} or not trusted_url:
        return ""
    identity = f"{project_key}\n{trusted_url}".encode("utf-8")
    return "item-" + hashlib.sha256(identity).hexdigest()[:20]


def report_item_href(period: str, period_key: str, project: object, url: object) -> str:
    """Build a root-relative-in-the-export href for a known report selection."""
    patterns = {"daily": r"\d{4}-\d{2}-\d{2}", "weekly": r"\d{4}-W\d{2}",
                "monthly": r"\d{4}-\d{2}"}
    if period not in patterns or not re.fullmatch(patterns[period], period_key):
        return ""
    anchor = report_item_anchor(project, url)
    base = f"{period}/{period_key}.html"
    return f"{base}#{anchor}" if anchor else base


def selected_item_source(item: dict, section: dict, evidence: dict) -> tuple[str, str]:
    """Prefer collector-backed evidence over model-supplied item fields."""
    source = evidence.get(item.get("ref"))
    if source is not None:
        return canonical_project(source.get("project")), source_url(source.get("url"))
    return canonical_project(section.get("key") or section.get("name")), source_url(item.get("url"))


def visible_report_anchors(content: dict) -> set[str]:
    """Mirror historical display limits before emitting exact index backlinks.

    New reports are already limited at publication. Older stored reports can
    contain more entries than current rendering exposes; their extra search
    references must point to the report page, not a nonexistent fragment.
    """
    from virt_report.summarize.report import ITEM_LIMIT, enrich_architectures

    sections = content.get("sections", [])
    limit = ITEM_LIMIT.get(content.get("period"))
    if limit and sum(len(section.get("items", [])) for section in sections) > limit:
        sections = enrich_architectures(deepcopy(content)).get("sections", [])
    evidence = {item.get("ref"): item for item in content.get("top_threads", [])
                if isinstance(item, dict) and item.get("ref")}
    return {anchor for section in sections for item in section.get("items", [])
            if (anchor := report_item_anchor(*selected_item_source(item, section, evidence)))}


def reference_parts(value: object, evidence: dict, selected: dict) -> list[dict]:
    """Tokenize references for autoescaped templates; never produce trusted HTML."""
    text = str(value or "")
    parts, position = [], 0
    for match in re.finditer(r"(?<![A-Za-z0-9_])(?:\[(T\d{3,})\]|(T\d{3,}))(?![A-Za-z0-9_])", text):
        source = evidence.get(match.group(1) or match.group(2))
        if not source or not source.get("subject") or not source_url(source.get("url")):
            continue
        if match.start() > position:
            parts.append({"text": text[position:match.start()]})
        anchor = report_item_anchor(source.get("project"), source.get("url"))
        internal = bool(anchor and anchor in selected)
        parts.append({"text": source["subject"],
                      "href": f"#{anchor}" if internal else source_url(source["url"]),
                      "external": not internal})
        position = match.end()
    if position < len(text):
        parts.append({"text": text[position:]})
    return parts


def prepare_report_links(content: dict) -> dict:
    """Decorate report data without mutating stored historical AI text."""
    prepared = dict(content)
    evidence = {item.get("ref"): item for item in content.get("top_threads", [])
                if isinstance(item, dict) and item.get("ref")}
    # Older published reports can carry sanitized analysis evidence without the
    # original candidate list. Retain their source links, but never use this
    # fallback to override a collector-backed candidate list.
    if "top_threads" not in content:
        for analysis in content.get("period_analysis", []):
            for item in analysis.get("evidence", []):
                if item.get("ref") and source_url(item.get("url")):
                    evidence.setdefault(item["ref"], dict(item, subject=item.get("title", "")))
    selected = {}
    sections = []
    for section in content.get("sections", []):
        entries = []
        for item in section.get("items", []):
            project, url = selected_item_source(item, section, evidence)
            anchor = report_item_anchor(project, url)
            entry = dict(item, url=url, anchor=anchor if anchor not in selected else "",
                         latest_version_url=source_url(item.get("latest_version_url")))
            if anchor:
                selected.setdefault(anchor, entry)
            entries.append(entry)
        sections.append(dict(section, items=entries))
    prepared["sections"] = sections
    prepared["selected_count"] = sum(len(section["items"]) for section in sections)
    for field, text_fields in (("overview", ("summary",)), ("watchlist", ("reason",)),
                               ("period_analysis", ("progress", "unresolved"))):
        values = []
        for item in content.get(field, []):
            entry = dict(item)
            for text_field in text_fields:
                entry[text_field + "_parts"] = reference_parts(item.get(text_field), evidence, selected)
            if field == "period_analysis":
                refs = item.get("refs") or [value.get("ref") for value in item.get("evidence", [])]
                entry["evidence"] = []
                for ref in dict.fromkeys(refs):
                    source = evidence.get(ref)
                    if not source or not source.get("subject") or not source_url(source.get("url")):
                        continue
                    anchor = report_item_anchor(source.get("project"), source["url"])
                    internal = bool(anchor and anchor in selected)
                    entry["evidence"].append({"title": source["subject"],
                                              "project": {"qemu": "QEMU", "kvm": "KVM", "libvirt": "Libvirt"}.get(
                                                  canonical_project(source.get("project")), source.get("project", "")),
                                              "url": f"#{anchor}" if internal else source_url(source["url"]),
                                              "external": not internal})
            values.append(entry)
        prepared[field] = values
    return prepared
