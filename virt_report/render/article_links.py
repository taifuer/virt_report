"""Keep canonical article links and collect upstream references at the end."""
from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urlsplit, urlunsplit


def canonicalize_article_links(html: str, redirects: tuple[dict, ...]) -> str:
    """Resolve old article links and bookmarks in trusted, rendered HTML."""
    by_slug = {entry["slug"]: entry for entry in redirects}

    def replace(match: re.Match) -> str:
        address = match.group(1)
        url = urlsplit(address)
        if url.scheme or url.netloc:
            return match.group()
        prefix, _, name = url.path.rpartition("/")
        redirect = by_slug.get(name.removesuffix(".html")) if name.endswith(".html") else None
        if not redirect:
            return match.group()
        path = (prefix + "/" if prefix else "") + redirect["target"] + ".html"
        fragment = redirect.get("anchors", {}).get(url.fragment, url.fragment)
        return 'href="' + urlunsplit(("", "", path, url.query, fragment)) + '"'

    return re.sub(r'href="([^"]+)"', replace, html)


class _ReferenceLinks(HTMLParser):
    def __init__(self, html: str):
        super().__init__(convert_charrefs=True)
        self.lines = [0]
        for line in html.splitlines(keepends=True):
            self.lines.append(self.lines[-1] + len(line))
        self.section_depth = 0
        self.sources_depth = None
        self.sources_heading_end = None
        self.sources_end = None
        self.anchor = None
        self.links = []
        self.feed(html)

    def position(self) -> int:
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "section":
            self.section_depth += 1
            if attributes.get("id") == "sources":
                self.sources_depth = self.section_depth
        if tag == "a" and attributes.get("href", "").startswith(("https://", "http://")):
            self.anchor = {"url": attributes["href"], "start": self.position(),
                           "opening_end": self.position() + len(self.get_starttag_text()),
                           "in_sources": self.sources_depth is not None}

    def handle_endtag(self, tag):
        if tag == "a" and self.anchor is not None:
            self.anchor.update(closing_start=self.position(), end=self.position() + len("</a>"))
            if not self.anchor["in_sources"]:
                self.links.append(self.anchor)
            self.anchor = None
        if tag == "h2" and self.sources_depth is not None and self.sources_heading_end is None:
            self.sources_heading_end = self.position() + len("</h2>")
        if tag == "section":
            if self.sources_depth == self.section_depth:
                self.sources_end = self.position()
                self.sources_depth = None
            self.section_depth -= 1


def compact_references(html: str) -> str:
    """Keep the curated references list plain and remove inline upstream links.

    Source URLs remain in the authored content and baseline metadata. Do not
    append another catalogue of versions and individual function links. Code,
    JSON, diagrams, the references section and local links remain unchanged.
    """
    parsed = _ReferenceLinks(html)
    if parsed.sources_heading_end is None or parsed.sources_end is None:
        return html
    edits = []
    for link in parsed.links:
        edits.extend(((link["start"], link["opening_end"], ""),
                      (link["closing_start"], link["end"], "")))
    for start, end, replacement in sorted(edits, reverse=True):
        html = html[:start] + replacement + html[end:]
    return html
