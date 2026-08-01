"""Conference metadata collection, editorial review, and public snapshots.

The SQLite catalogue may contain every title returned by DBLP.  The committed
JSON snapshot contains only editor-reviewed virtualization-related entries and
Chinese paraphrases; raw abstracts remain in the untracked database.
"""
from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import sqlite3
import subprocess
import time
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import requests

from virt_report import db

CONTENT_PATH = Path(__file__).parent / "content" / "conferences.json"
REQUIRED_PAPER_FIELDS = {
    "id", "year", "venue", "title", "url", "introduction", "commentary",
    "relation", "topics",
}
DBLP_TOC_BASE = "https://dblp.org/db/{stream}/{name}{year}.xml"
CROSSREF_API = "https://api.crossref.org/works/{}"
DBLP_STREAMS = {
    "vee": "conf/vee",
    "asplos": "conf/asplos",
    "osdi": "conf/osdi",
    "sosp": "conf/sosp",
    "eurosys": "conf/eurosys",
    "atc": "conf/usenix",
    "socc": "conf/cloud",
    "nsdi": "conf/nsdi",
    "fast": "conf/fast",
    "usenix-security": "conf/uss",
}
DBLP_TOC_NAMES = {
    "vee": "vee", "asplos": "asplos", "osdi": "osdi", "sosp": "sosp",
    "eurosys": "eurosys", "atc": "usenix", "socc": "socc",
    "nsdi": "nsdi", "fast": "fast", "usenix-security": "uss",
}
_DIRECT_PATTERNS = (
    r"\bqemu\b", r"\bkvm\b", r"\bhypervisor", r"\bvirtual machine",
    r"\bvirtualized?\b", r"\bvirtualization\b", r"\bvmm\b", r"\bmicrovm",
    r"\bvirtio\b", r"\bvhost\b", r"\bparavirtual", r"\bnested virtual",
    r"\bvm migration\b", r"\blive migration\b", r"\bvm exit",
)
_RELATED_PATTERNS = (
    r"\bconfidential vm", r"\bconfidential virtual", r"\btrusted execution",
    r"\bsecure enclave", r"\bdevice passthrough", r"\bsr-iov\b", r"\biommu\b",
    r"\bserverless.*snapshot", r"\bsnapshot.*serverless", r"\bmemory balloon",
    r"\bcloud.*isolation", r"\btenant.*isolation", r"\bvirtual.*i/o\b",
    r"\bvirtual.*nvme\b", r"\bvirtual.*gpu\b", r"\bvirtual.*device\b",
)
_EXCLUDE_PATTERNS = (
    r"\bjava virtual machine\b", r"\bjvm\b", r"\bvirtual reality\b",
    r"\bvirtual network function\b", r"\bvirtual memory\b",
)
log = logging.getLogger(__name__)


def paper_id(venue: str, year: int, title: str) -> str:
    """Return a stable identifier independent of a publisher URL."""
    normalized = re.sub(r"\W+", " ", title.casefold()).strip()
    digest = hashlib.sha256(
        f"{venue}|{year}|{normalized}".encode("utf-8")
    ).hexdigest()[:20]
    return f"{venue}-{year}-{digest}"


def is_candidate_title(title: str) -> bool:
    """Conservatively flag titles for human virtualization review."""
    value = html.unescape(title).casefold()
    if any(re.search(pattern, value) for pattern in _EXCLUDE_PATTERNS):
        return False
    return any(re.search(pattern, value) for pattern in (
        *_DIRECT_PATTERNS, *_RELATED_PATTERNS,
    ))


def _request_session() -> requests.Session:
    session = requests.Session()
    # Server deployments sometimes inherit a workstation-only proxy address.
    session.trust_env = False
    session.headers.update({
        "User-Agent": "virt-report/0.1 (conference metadata; taifu@taifua.com)",
        "Accept": "application/json",
    })
    return session


def _get_json(session: requests.Session, url: str, *, params: dict) -> dict:
    """Read a public API politely, retrying throttling and transient failures."""
    if not getattr(session, "_virt_report_force_curl", False):
        for attempt in range(2):
            try:
                response = session.get(url, params=params, timeout=45)
            except requests.RequestException as exc:
                delay = min(20.0, 2.0 ** attempt)
                log.warning("会议元数据连接失败（%s），%.0f 秒后重试", exc, delay)
                time.sleep(delay)
                continue
            if response.status_code not in (429, 500, 502, 503, 504):
                response.raise_for_status()
                return response.json()
            retry_after = response.headers.get("Retry-After")
            delay = (float(retry_after) if retry_after and retry_after.isdigit()
                     else min(20.0, 2.0 ** attempt))
            log.warning("会议元数据接口返回 %s，%.0f 秒后重试",
                        response.status_code, delay)
            time.sleep(delay)
        session._virt_report_force_curl = True
        log.info("会议元数据请求切换到 curl 传输")
    # Some networks reset Python/OpenSSL clients while accepting curl.  Keep a
    # no-shell fallback for this annual, operator-invoked maintenance command.
    prepared = requests.Request("GET", url, params=params).prepare().url
    result = subprocess.run([
        "curl", "--noproxy", "*", "-4", "-L", "--fail", "--silent",
        "--show-error", "--retry", "5", "--retry-all-errors",
        "--retry-delay", "2", "--max-time", "90", prepared,
    ], check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def _get_dblp_xml(session: requests.Session, url: str) -> bytes | None:
    """Fetch a static DBLP TOC; return ``None`` for a genuinely absent edition."""
    if not getattr(session, "_virt_report_force_curl", False):
        try:
            response = session.get(url, timeout=45)
            if response.status_code == 404:
                return None
            if response.status_code == 200:
                return response.content
            log.warning("DBLP TOC 返回 %s，切换 curl: %s", response.status_code, url)
        except requests.RequestException as exc:
            log.warning("DBLP TOC 连接失败，切换 curl: %s", exc)
        session._virt_report_force_curl = True

    for attempt in range(3):
        result = subprocess.run([
            "curl", "--noproxy", "*", "-4", "-L", "--silent",
            "--show-error", "--max-time", "20", "--write-out", "\n%{http_code}",
            url,
        ], check=False, capture_output=True)
        body, separator, status_raw = result.stdout.rpartition(b"\n")
        status = int(status_raw) if separator and status_raw.isdigit() else 0
        if status == 200:
            return body
        if status == 404:
            return None
        if attempt < 2:
            delay = min(20.0, 2.0 ** attempt)
            log.warning("DBLP TOC 返回 %s，%.0f 秒后重试: %s", status, delay, url)
            time.sleep(delay)
    raise RuntimeError(f"无法获取 DBLP TOC: {url}")


def fetch_dblp_edition(session: requests.Session, venue: str,
                       requested_year: int) -> list[dict] | None:
    """Fetch and parse one complete DBLP edition, or ``None`` if absent."""
    stream = DBLP_STREAMS[venue]
    url = DBLP_TOC_BASE.format(
        stream=stream, name=DBLP_TOC_NAMES[venue], year=requested_year
    )
    payload = _get_dblp_xml(session, url)
    if payload is None:
        return None
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise RuntimeError(f"DBLP XML 解析失败 {venue}/{requested_year}") from exc
    result = []
    for entry in root.iter("inproceedings"):
        title_node = entry.find("title")
        if title_node is None:
            continue
        title = html.unescape("".join(title_node.itertext()))
        title = re.sub(r"\s+", " ", title).strip().rstrip(".")
        if not title:
            continue
        ee_values = [node.text for node in entry.findall("ee") if node.text]
        doi_url = next((value for value in ee_values
                        if value.startswith("https://doi.org/")), None)
        key = entry.attrib.get("key")
        source_url = f"https://dblp.org/rec/{key}" if key else entry.findtext("url")
        info = {
            "key": key, "title": title, "year": requested_year,
            "authors": [node.text for node in entry.findall("author") if node.text],
            "ee": ee_values, "url": entry.findtext("url"),
        }
        result.append({
            "paper_id": paper_id(venue, requested_year, title), "venue": venue,
            "year": requested_year, "title": title,
            "authors": info["authors"],
            "doi": doi_url.removeprefix("https://doi.org/") if doi_url else None,
            "official_url": ee_values[0] if ee_values else source_url,
            "source_url": source_url, "raw": info,
        })
    return result


def fetch_dblp_venue(
        session: requests.Session, venue: str, start_year: int,
        end_year: int) -> list[dict]:
    """Fetch complete per-edition DBLP XML files for an academic venue."""
    records = []
    for year in range(start_year, end_year + 1):
        edition = fetch_dblp_edition(session, venue, year)
        if edition is not None:
            records.extend(edition)
        time.sleep(0.8)
    return records


def _upsert_catalogue(conn: sqlite3.Connection, venue: str,
                      records: Iterable[dict]) -> int:
    fetched_at = db.now_utc_iso()
    grouped: dict[int, int] = {}
    count = 0
    for item in records:
        conn.execute("""
            INSERT INTO conference_papers (
                paper_id,venue,year,title,authors_json,doi,official_url,
                source_url,fetched_at,raw_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(paper_id) DO UPDATE SET
                authors_json=excluded.authors_json, doi=excluded.doi,
                official_url=excluded.official_url, source_url=excluded.source_url,
                fetched_at=excluded.fetched_at, raw_json=excluded.raw_json
        """, (
            item["paper_id"], venue, item["year"], item["title"],
            json.dumps(item["authors"], ensure_ascii=False), item["doi"],
            item["official_url"], item["source_url"], fetched_at,
            json.dumps(item["raw"], ensure_ascii=False),
        ))
        grouped[item["year"]] = grouped.get(item["year"], 0) + 1
        count += 1
    for year, paper_count in grouped.items():
        conn.execute("""
            INSERT INTO conference_editions (
                venue,year,source_url,fetched_at,source_status,paper_count
            ) VALUES (?,?,?,?,?,?)
            ON CONFLICT(venue,year) DO UPDATE SET
                source_url=excluded.source_url, fetched_at=excluded.fetched_at,
                source_status=excluded.source_status,
                paper_count=excluded.paper_count
        """, (venue, year, f"https://dblp.org/db/{DBLP_STREAMS[venue]}/",
              fetched_at, "ok", paper_count))
    conn.commit()
    return count


def import_editor_reviews(conn: sqlite3.Connection, content: dict | None = None) -> int:
    """Mirror committed editor reviews into SQLite without storing page prose twice."""
    content = content or _read_content()
    reviewed_at = db.now_utc_iso()
    count = 0
    for paper in content.get("papers", []):
        pid = paper_id(paper["venue"], paper["year"], paper["title"])
        existing = conn.execute(
            "SELECT 1 FROM conference_papers WHERE paper_id=?", (pid,)
        ).fetchone()
        if not existing:
            conn.execute("""
                INSERT INTO conference_papers (
                    paper_id,venue,year,title,authors_json,official_url,
                    source_url,fetched_at,raw_json
                ) VALUES (?,?,?,?,?,?,?,?,?)
            """, (pid, paper["venue"], paper["year"], paper["title"], "[]",
                  paper["url"], paper["url"], reviewed_at, "{}"))
        conn.execute("""
            INSERT INTO conference_reviews (
                paper_id,relevance,relevance_reason,relation,topics_json,
                architectures_json,introduction_zh,commentary,representative,
                reviewed_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(paper_id) DO UPDATE SET
                relevance=excluded.relevance,
                relevance_reason=excluded.relevance_reason,
                relation=excluded.relation, topics_json=excluded.topics_json,
                architectures_json=excluded.architectures_json,
                introduction_zh=excluded.introduction_zh,
                commentary=excluded.commentary,
                representative=excluded.representative,
                reviewed_at=excluded.reviewed_at
        """, (
            pid, "included", paper.get("relevance_reason", "编辑审核收录"),
            paper["relation"], json.dumps(paper["topics"], ensure_ascii=False),
            json.dumps(paper.get("architectures", []), ensure_ascii=False),
            paper["introduction"], paper["commentary"],
            int(bool(paper.get("representative"))), reviewed_at,
        ))
        count += 1
    conn.commit()
    return count


def refresh_catalogue(conn: sqlite3.Connection, start_year: int = 2010,
                      end_year: int = 2026,
                      venues: Iterable[str] | None = None) -> dict:
    """Fetch all configured academic venue metadata and flag pending candidates."""
    session = _request_session()
    counts, errors = {}, []
    selected_venues = list(venues or DBLP_STREAMS)
    unknown = set(selected_venues) - DBLP_STREAMS.keys()
    if unknown:
        raise ValueError(f"未知会议: {', '.join(sorted(unknown))}")
    for venue in selected_venues:
        counts[venue] = 0
        for year in range(start_year, end_year + 1):
            try:
                records = fetch_dblp_edition(session, venue, year)
            except RuntimeError as exc:
                errors.append(f"{venue}/{year}: {exc}")
                conn.execute("""
                    INSERT INTO conference_editions (
                        venue,year,source_url,fetched_at,source_status,paper_count
                    ) VALUES (?,?,?,?,?,0)
                    ON CONFLICT(venue,year) DO UPDATE SET
                        fetched_at=excluded.fetched_at,
                        source_status=excluded.source_status
                """, (venue, year, None, db.now_utc_iso(), "error"))
                conn.commit()
                continue
            if records is None:
                continue
            counts[venue] += _upsert_catalogue(conn, venue, records)
            time.sleep(0.8)
        log.info("会议元数据 %s: %d 篇", venue, counts[venue])
    import_editor_reviews(conn)
    pending = conn.execute("""
        SELECT COUNT(*) FROM conference_papers p
        LEFT JOIN conference_reviews r ON r.paper_id=p.paper_id
        WHERE p.year BETWEEN ? AND ? AND r.paper_id IS NULL
    """, (start_year, end_year)).fetchone()[0]
    candidates = sum(
        is_candidate_title(row[0]) for row in conn.execute("""
            SELECT p.title FROM conference_papers p
            LEFT JOIN conference_reviews r ON r.paper_id=p.paper_id
            WHERE p.year BETWEEN ? AND ? AND r.paper_id IS NULL
        """, (start_year, end_year))
    )
    return {"venues": counts, "unreviewed": pending, "candidates": candidates,
            "errors": errors}


def candidate_rows(conn: sqlite3.Connection, start_year: int = 2010,
                   end_year: int = 2026) -> list[dict]:
    """Return title-matched, not-yet-reviewed rows for editorial inspection."""
    rows = conn.execute("""
        SELECT p.* FROM conference_papers p
        LEFT JOIN conference_reviews r ON r.paper_id=p.paper_id
        WHERE p.year BETWEEN ? AND ? AND r.paper_id IS NULL
        ORDER BY p.year DESC,p.venue,p.title
    """, (start_year, end_year)).fetchall()
    return [dict(row) for row in rows if is_candidate_title(row["title"])]


def enrich_candidate_abstracts(conn: sqlite3.Connection, limit: int = 200) -> int:
    """Opportunistically add Crossref abstracts for title-matched DOI records."""
    session = _request_session()
    rows = conn.execute("""
        SELECT paper_id,doi,title FROM conference_papers
        WHERE abstract IS NULL AND doi IS NOT NULL
        ORDER BY year DESC,venue,title
    """).fetchall()
    updated = 0
    for row in rows:
        if updated >= limit or not is_candidate_title(row["title"]):
            continue
        try:
            payload = _get_json(
                session, CROSSREF_API.format(row["doi"]), params={}
            )
        except (requests.RequestException, subprocess.SubprocessError,
                json.JSONDecodeError, RuntimeError) as exc:
            log.warning("摘要补充失败 %s: %s", row["paper_id"], exc)
            continue
        abstract = payload.get("message", {}).get("abstract")
        if not abstract:
            continue
        abstract = html.unescape(re.sub(r"<[^>]+>", " ", abstract))
        abstract = re.sub(r"\s+", " ", abstract).strip()
        conn.execute(
            "UPDATE conference_papers SET abstract=? WHERE paper_id=?",
            (abstract, row["paper_id"]),
        )
        updated += 1
        time.sleep(0.5)
    conn.commit()
    return updated


def _read_content() -> dict:
    return json.loads(CONTENT_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def load_content() -> dict:
    """Return validated public content plus derived page data."""
    content = _read_content()
    venues = content.get("venues", [])
    venue_keys = {item["key"] for item in venues}
    if len(venue_keys) != len(venues):
        raise ValueError("会议目录包含重复 key")

    papers = content.get("papers", [])
    paper_ids: set[str] = set()
    for paper in papers:
        missing = REQUIRED_PAPER_FIELDS - paper.keys()
        if missing:
            raise ValueError(f"论文 {paper.get('id', '<unknown>')} 缺少字段: {missing}")
        if paper["id"] in paper_ids:
            raise ValueError(f"论文 id 重复: {paper['id']}")
        if paper["venue"] not in venue_keys:
            raise ValueError(f"论文引用未知会议: {paper['venue']}")
        if not paper["url"].startswith("https://"):
            raise ValueError(f"论文链接必须使用 HTTPS: {paper['id']}")
        paper.setdefault("architectures", [])
        paper.setdefault("representative", False)
        paper_ids.add(paper["id"])

    venue_order = {item["key"]: index for index, item in enumerate(venues)}
    papers.sort(key=lambda item: (
        -item["year"], item["relation"] != "直接关联",
        venue_order[item["venue"]], item["title"],
    ))
    counts = {key: 0 for key in venue_keys}
    venue_names = {item["key"]: item["name"] for item in venues}
    by_id = {paper["id"]: paper for paper in papers}
    for paper in papers:
        counts[paper["venue"]] += 1
        paper["venue_name"] = venue_names[paper["venue"]]
    for venue in venues:
        venue["paper_count"] = counts[venue["key"]]

    analysis = content.get("analysis", {})
    representative_ids = {
        pid for item in analysis.get("years", [])
        for pid in item.get("representative_ids", [])
    }
    for paper in papers:
        paper["representative"] = bool(
            paper.get("representative") or paper["id"] in representative_ids
        )
    for item in analysis.get("years", []):
        year_papers = [paper for paper in papers if paper["year"] == item["year"]]
        item["paper_count"] = len(year_papers)
        ids = item.get("representative_ids", [])
        item["representative_papers"] = [by_id[pid] for pid in ids if pid in by_id]

    content["analysis"] = analysis
    content["papers"] = papers
    content["paper_count"] = len(papers)
    content["direct_count"] = sum(
        paper["relation"] == "直接关联" for paper in papers
    )
    content["years"] = sorted({paper["year"] for paper in papers}, reverse=True)
    content["academic_venues"] = [
        venue for venue in venues if venue["kind"] == "学术会议"
    ]
    content["paper_venues"] = [
        venue for venue in content["academic_venues"] if venue["paper_count"]
    ]
    return content
