"""Check official title lists without publishing or invoking an LLM.

The small source registry lives alongside the reviewed content. This supplements
DBLP between acceptance and indexing, and only writes an untracked check report.
"""
from __future__ import annotations

import json
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

from virt_report import conferences, db


class _TitleListParser(HTMLParser):
    def __init__(self, parser: str, source_url: str) -> None:
        super().__init__()
        self.parser = parser
        self.source_url = source_url
        self.stack: list[tuple[str, dict]] = []
        self.capture_depth = 0
        self.parts: list[str] = []
        self.href = ""
        self.records: list[dict] = []
        self.in_accepted = False
        self.heading: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        attrs = dict(attrs)
        # HTML void elements never enter the stack.
        if tag in {"area", "base", "br", "col", "embed", "hr", "img", "input",
                   "link", "meta", "param", "source", "track", "wbr"}:
            return
        self.stack.append((tag, attrs))
        if self.parser == "socc" and tag == "h2":
            self.heading = []
        capture = (
            self.parser == "usenix" and tag == "a"
            and any(t == "h2" for t, _ in self.stack)
            and "/presentation/" in attrs.get("href", "")
        ) or (
            self.parser == "sosp" and tag == "b"
            and any("paperlist" in a.get("class", "").split()
                    for _, a in self.stack)
        ) or (self.parser == "socc" and tag == "h3" and self.in_accepted) or (
            self.parser == "pretalx" and tag == "div"
            and "title" in attrs.get("class", "").split()
            and any(t == "a" and "/talk/" in a.get("href", "")
                    for t, a in self.stack)
        )
        if capture and not self.capture_depth:
            self.capture_depth = len(self.stack)
            self.parts = []
            self.href = attrs.get("href", "")
            if self.parser == "pretalx":
                self.href = next(a["href"] for t, a in reversed(self.stack)
                                 if t == "a" and "/talk/" in a.get("href", ""))

    def handle_data(self, data: str) -> None:
        if self.capture_depth:
            self.parts.append(data)
        if self.heading is not None:
            self.heading.append(data)

    def handle_endtag(self, tag: str) -> None:
        index = next((i for i in range(len(self.stack) - 1, -1, -1)
                      if self.stack[i][0] == tag), None)
        if index is None:
            return
        if self.capture_depth and index < self.capture_depth:
            title = " ".join("".join(self.parts).split())
            if title:
                self.records.append({
                    "title": title,
                    "url": urljoin(self.source_url, self.href),
                })
            self.capture_depth = 0
        if self.heading is not None and tag == "h2":
            self.in_accepted = "accepted papers" in "".join(self.heading).lower()
            self.heading = None
        self.stack[index:] = []


def parse_title_list(body: str, source: dict) -> list[dict]:
    """Parse known HTML lists; reject empty/challenge pages instead of clearing data."""
    if source["parser"] not in {"usenix", "sosp", "socc", "pretalx"}:
        raise ValueError(f"未知会议列表解析器: {source['parser']}")
    parser = _TitleListParser(source["parser"], source["source_url"])
    parser.feed(body)
    unique = {}
    for item in parser.records:
        if urlparse(item["url"]).scheme != "https":
            continue
        unique.setdefault(conferences._normalized_title(item["title"]), item)
    if not unique:
        raise ValueError("未解析到论文标题，可能尚未公布或页面结构改变")
    return list(unique.values())


def check_updates(output: Path, *, venues: list[str] | None = None,
                  year: int | None = None) -> dict:
    """Save a private delta report; never alter reviews, SQLite, or site pages."""
    content = conferences.load_content()
    sources = content.get("edition_checks", [])
    if venues:
        unknown = set(venues) - {item["venue"] for item in sources}
        if unknown:
            raise ValueError(f"未配置官方核对来源: {', '.join(sorted(unknown))}")
        sources = [item for item in sources if item["venue"] in venues]
    if year is not None:
        sources = [item for item in sources if item["year"] == year]
    if not sources:
        raise ValueError("该范围没有配置官方核对来源")
    previous = json.loads(output.read_text(encoding="utf-8")) if output.exists() else {}
    old_sources = {item["key"]: item for item in previous.get("sources", [])}
    checked_at = db.now_utc_iso()
    results = []
    with requests.Session() as session:
        session.trust_env = False
        session.headers.update({
            "User-Agent": "virt-report conference metadata check",
            "Accept": "text/html",
        })
        for source in sources:
            key = f"{source['venue']}:{source['year']}"
            old = old_sources.get(key, {})
            result = {**old, "key": key, "source_url": source["source_url"],
                      "checked_at": checked_at}
            try:
                response = session.get(source["source_url"], timeout=(10, 25))
                response.raise_for_status()
                if "text/html" not in response.headers.get("Content-Type", ""):
                    raise ValueError("会议列表未返回 HTML")
                records = parse_title_list(response.text, source)
                old_titles = {conferences._normalized_title(item["title"])
                              for item in old.get("titles", [])}
                reviewed = {conferences._normalized_title(paper["title"])
                            for paper in content["papers"]
                            if paper["venue"] == source["venue"]
                            and paper["year"] == source["year"]}
                result.update({
                    "status": "ok", "error": None,
                    "last_success_at": checked_at, "titles": records,
                    "new_titles": [item for item in records
                                   if conferences._normalized_title(item["title"])
                                   not in old_titles],
                    "candidates": [item for item in records
                                   if conferences.is_candidate_title(item["title"])
                                   and conferences._normalized_title(item["title"])
                                   not in reviewed],
                })
            except (requests.RequestException, ValueError) as exc:
                result.update(status="error", error=str(exc))
            results.append(result)
    # Preserve other years/venues when checking only part of the registry.
    old_sources.update({item["key"]: item for item in results})
    report = {"checked_at": checked_at, "sources": list(old_sources.values())}
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    temporary.replace(output)
    return {"output": str(output), "sources": [{
        "key": item["key"], "status": item["status"], "error": item.get("error"),
        "title_count": len(item.get("titles", [])),
        "new_titles": len(item.get("new_titles", [])) if item["status"] == "ok" else None,
        "pending_candidates": item.get("candidates", []) if item["status"] == "ok" else [],
    } for item in results]}
