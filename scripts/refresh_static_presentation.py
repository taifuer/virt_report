#!/usr/bin/env python3
"""Refresh presentation of an existing export without reading a runtime DB.

Use when the checked-in snapshot is newer than local report data. Existing
report prose, archive rows, dates and topic selections are preserved verbatim.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from functools import lru_cache
from html import escape, unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re

from virt_report import search
from virt_report.config import Config
from virt_report.render import render
from virt_report.report_links import canonical_project, report_item_anchor, source_url


@dataclass
class Node:
    tag: str
    attrs: dict
    start: int
    opening_end: int
    closing_start: int = 0
    end: int = 0
    parent: Node | None = None

    def has_class(self, name: str) -> bool:
        return name in (self.attrs.get("class") or "").split()

    def within(self, other: Node) -> bool:
        return other.opening_end <= self.start < other.closing_start


class Document(HTMLParser):
    """Record source offsets instead of serializing/reformatting the HTML."""

    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input",
            "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, html: str):
        super().__init__(convert_charrefs=False)
        self.html = html
        self.lines = [0, *[match.end() for match in re.finditer("\n", html)]]
        self.nodes: list[Node] = []
        self.stack: list[Node] = []
        self.feed(html)
        self.close()

    def source_position(self) -> int:
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def handle_starttag(self, tag, attrs):
        start = self.source_position()
        node = Node(tag, dict(attrs), start, start + len(self.get_starttag_text()),
                    parent=self.stack[-1] if self.stack else None)
        self.nodes.append(node)
        if tag in self.VOID:
            node.closing_start = node.end = node.opening_end
        else:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            node = self.stack.pop()
            node.closing_start = node.end = node.opening_end

    def handle_endtag(self, tag):
        for position in range(len(self.stack) - 1, -1, -1):
            if self.stack[position].tag == tag:
                node = self.stack[position]
                node.closing_start = self.source_position()
                node.end = self.html.index(">", node.closing_start) + 1
                del self.stack[position:]
                break

    def descendants(self, node: Node) -> list[Node]:
        return [child for child in self.nodes if child.within(node)]

    def text(self, node: Node) -> str:
        return unescape(re.sub(r"<[^>]*>", "", self.html[node.opening_end:node.closing_start]))


def apply_edits(html: str, edits: list[tuple[int, int, str]]) -> str:
    previous = len(html) + 1
    for start, end, replacement in sorted(edits, reverse=True):
        if not 0 <= start <= end <= len(html) or end > previous:
            raise ValueError("Overlapping static-presentation edits")
        html = html[:start] + replacement + html[end:]
        previous = start
    return html


def add_attributes(doc: Document, node: Node, values: dict) -> tuple[int, int, str]:
    """Only add attributes not already supplied by the current snapshot."""
    extra = "".join(f' {key}="{escape(str(value), quote=True)}"'
                    for key, value in values.items() if key not in node.attrs)
    return node.opening_end - 1, node.opening_end - 1, extra


def protected_report_parts(html: str) -> list[str]:
    """Report text/statistics/headers must not change in a presentation refresh."""
    doc = Document(html)
    classes = {"report-hero", "overview-card", "d-heading", "d-meta", "d-sum"}
    return [html[node.start:node.end] for node in doc.nodes
            if any(node.has_class(name) for name in classes)
            or node.attrs.get("id") in {"stats", "period-analysis"}
            or (node.tag == "details" and node.parent and node.parent.has_class("dyn"))]


def refresh_report_html(html: str) -> tuple[str, dict[tuple[str, str], str]]:
    doc, edits, targets = Document(html), [], {}
    entries = [node for node in doc.nodes if node.has_class("dyn")]
    used_ids = {node.attrs["id"] for node in doc.nodes if node.attrs.get("id")}
    for node in entries:
        section = node.parent
        while section and "data-project" not in section.attrs:
            section = section.parent
        if not section:
            continue
        title = next((child for child in doc.descendants(node) if child.has_class("d-title")), None)
        link = next((child for child in doc.descendants(title) if child.tag == "a"), None) if title else None
        if not link:
            continue
        project, url = canonical_project(section.attrs["data-project"]), source_url(link.attrs.get("href"))
        anchor = report_item_anchor(project, url)
        if not anchor:
            continue
        existing_id = node.attrs.get("id")
        if existing_id and existing_id != anchor:
            # Preserve previously shared targets, even if they use another ID scheme.
            targets[(project, url)] = existing_id
            continue
        if not existing_id and anchor in used_ids:
            continue
        targets[(project, url)] = anchor
        if not existing_id:
            edits.append(add_attributes(doc, node, {"id": anchor}))
            used_ids.add(anchor)
        if not any("data-copy-report-link" in child.attrs for child in doc.descendants(node)):
            label = escape("分享条目：" + doc.text(title), quote=True)
            edits.append((node.closing_start, node.closing_start,
                          f'<div class="item-permalink-row"><a class="item-permalink" href="#{anchor}" '
                          f'data-copy-report-link aria-label="{label}">分享</a>'
                          '<span class="sr-only" role="status" data-copy-status></span></div>'))
    for node in doc.nodes:
        for key in ("data-project-filter", "data-item-filter"):
            if key in node.attrs:
                edits.append(add_attributes(doc, node, {"aria-pressed": str(node.attrs[key] == "all").lower()}))
        if node.has_class("project-tools"):
            edits.append(add_attributes(doc, node, {"role": "group"}))
        if node.has_class("report-filters"):
            if not any("data-report-filter-status" in child.attrs for child in doc.descendants(node)):
                edits.append((node.closing_start, node.closing_start,
                              '<p class="report-filter-status" role="status" aria-live="polite" '
                              f'data-report-filter-status>显示 {len(entries)} / {len(entries)} 条精选</p>'))
            if not any("data-report-filter-empty" in child.attrs for child in doc.nodes):
                edits.append((node.end, node.end, '<p class="report-filter-empty" data-report-filter-empty hidden>'
                              '当前筛选没有匹配条目。<button type="button" data-report-filter-reset>清除筛选</button></p>'))
    refreshed = apply_edits(html, edits)
    if protected_report_parts(refreshed) != protected_report_parts(html):
        raise ValueError("Refusing to change existing report content")
    return refreshed, targets


def refresh_topic_html(html: str, *, root: str, reports: dict) -> tuple[str, int]:
    doc, edits, linked = Document(html), [], 0
    for item in (node for node in doc.nodes if node.has_class("topic-item")):
        children = doc.descendants(item)
        heading = next((node for node in children if node.has_class("topic-item-head")), None)
        primary = next((node for node in children if node.has_class("topic-item-meta-primary")), None)
        if not heading or not primary:
            continue
        link = next((node for node in doc.descendants(heading) if node.tag == "a"), None)
        project_tag = next((node for node in doc.descendants(primary) if node.has_class("tag")), None)
        if not link or not project_tag:
            continue
        project, url = canonical_project(doc.text(project_tag)), source_url(link.attrs.get("href"))
        for node in children:
            if node.tag != "span" or not doc.text(node).startswith("收录于 "):
                continue
            if any(child.tag == "a" for child in doc.descendants(node)):
                continue
            text = doc.text(node)

            def replace_key(match):
                nonlocal linked
                key = match.group()
                period = "weekly" if "W" in key else "daily" if len(key) == 10 else "monthly"
                path = f"{period}/{key}.html"
                if path not in reports:
                    return key
                anchor = reports[path].get((project, url))
                href = root + path + (f"#{anchor}" if anchor else "")
                linked += 1
                return f'<a href="{escape(href, quote=True)}">{key}</a>'

            body = re.sub(r"\d{4}-(?:W\d{2}|\d{2}(?:-\d{2})?)", replace_key, escape(text))
            edits.append((node.opening_end, node.closing_start, body))
    return apply_edits(html, edits), linked


def refresh_asset_urls(html: str) -> str:
    return re.sub(r"((?:assets/(?:site\.(?:css|js)|brand-mark\.png)|favicon(?:-32\.png|\.ico))\?v=)[a-f0-9]+",
                  lambda match: match[1] + render.ASSET_VERSION, html)


@lru_cache(maxsize=64)
def _page_chrome(relative: str, site_name: str) -> dict[str, str]:
    """Use the actual shared template, without regenerating stored report text."""
    root = "../" * len(Path(relative).parts[:-1])
    active = set()
    period = relative.split("/", 1)[0]
    if relative == "index.html":
        active.add("home")
    elif period in {"daily", "weekly", "monthly"}:
        active.update(("reports", period))
    elif relative == "topics.html" or period == "topics":
        active.add("topics")
    elif relative in {"conferences.html", "kvm-forum.html", "academic-conferences.html", "conference-papers.html"}:
        active.add("conferences")
    elif relative == "mechanisms.html" or period == "mechanisms":
        active.add("mechanisms")
    elif relative in {"about.html", "versions.html"}:
        active.add(relative.removesuffix(".html"))
    elif relative == "search.html":
        active.update(("search", "search_mobile"))
    blocks = ["{% extends 'base.html' %}", "{% block nav_home %}",
              "on" if "home" in active else "", "{% endblock %}"]
    for name in sorted(active - {"home"}):
        blocks.append("{% block nav_" + name + " %}on{% endblock %}")
    page = render._env().from_string("".join(blocks)).render(root=root, site_name=site_name)
    doc = Document(page)
    return {name: page[node.start:node.end] for node in doc.nodes
            for name in ("site-head", "site-foot") if node.has_class(name)}


def refresh_page_chrome(html: str, relative: str, site_name: str) -> str:
    """Update only the shared header/footer; keep main content byte-for-byte."""
    doc = Document(html)
    chrome = _page_chrome(relative, site_name)
    edits = [(node.start, node.end, chrome[name]) for node in doc.nodes
             for name in chrome if node.has_class(name)]
    return apply_edits(html, edits)


def refresh_snapshot(site: Path) -> dict:
    files = sorted(site.rglob("*.html"))
    originals = {path: path.read_text(encoding="utf-8") for path in files}
    changes = dict(originals)
    reports = {}
    for path, html in originals.items():
        relative = path.relative_to(site).as_posix()
        if re.fullmatch(r"(?:daily/\d{4}-\d{2}-\d{2}|weekly/\d{4}-W\d{2}|monthly/\d{4}-\d{2})\.html", relative):
            changes[path], reports[relative] = refresh_report_html(html)
    linked = 0
    for path, html in originals.items():
        relative = path.relative_to(site).as_posix()
        if relative == "topics.html" or relative.startswith("topics/"):
            root = "../" * len(path.relative_to(site).parts[:-1])
            changes[path], count = refresh_topic_html(html, root=root, reports=reports)
            linked += count
    config = Config()
    config.render.output_dir = site
    # Static explanatory entry deliberately does not contain a local DB count.
    result = {"query": "", "project": "", "category": "", "architecture": "",
              "sort": "relevance", "per_page": 10, "page": 1, "pages": 0,
              "projects": search.PROJECTS, "categories": search.CATEGORIES,
              "architectures": search.ARCHITECTURE_FILTERS, "sorts": search.SORTS,
              "items": [], "index": {}}
    changes[site / "search.html"] = render.render_search_html(config, result, static_mode=True)
    render.export_brand_assets(site)
    for path, html in changes.items():
        updated = refresh_asset_urls(refresh_page_chrome(
            html, path.relative_to(site).as_posix(), config.name,
        ))
        if updated != originals.get(path):
            path.write_text(updated, encoding="utf-8")
    return {"reports": len(reports), "report_items": sum(len(items) for items in reports.values()),
            "topic_report_links_added": linked, "asset_version": render.ASSET_VERSION,
            "report_paths": sorted(reports)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("site", type=Path, nargs="?", default=Path("site"))
    args = parser.parse_args()
    summary = refresh_snapshot(args.site)
    summary.pop("report_paths")
    print(json.dumps(summary, ensure_ascii=False))
