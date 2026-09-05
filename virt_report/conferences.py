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
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote, urlparse

import requests

from virt_report import __version__, db

CONTENT_PATH = Path(__file__).parent / "content" / "conferences.json"
INSTITUTION_OVERRIDES_PATH = (
    Path(__file__).parent / "content" / "conference_institutions.json"
)
INSTITUTION_DISPLAY_LIMIT = 5
REQUIRED_PAPER_FIELDS = {
    "id", "year", "venue", "title", "url", "introduction", "commentary",
    "relation", "topics",
}
DBLP_TOC_BASE = "https://dblp.org/db/{stream}/{name}{year}.xml"
CROSSREF_API = "https://api.crossref.org/works/{}"
CROSSREF_WORKS_API = "https://api.crossref.org/works"
CROSSREF_MAILTO = "taifu@taifua.com"
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
    r"\bvirtuali[sz](?:e[ds]?|ation)\b", r"\bvmm\b", r"\bmicrovm",
    r"\bvirtio\b", r"\bvhost\b", r"\bparavirtual", r"\bnested virtual",
    r"\bvm migration\b", r"\blive migration\b", r"\bvm exit",
)
_RELATED_PATTERNS = (
    r"\bsev(?:-snp|-es)?\b", r"\btdx\b", r"\barm cca\b", r"\bpkvm\b",
    r"\bguest_memfd\b", r"\bopen vswitch\b",
    r"\bconfidential vm", r"\bconfidential virtual", r"\btrusted execution",
    r"\bsecure enclave", r"\bdevice passthrough", r"\bsr-iov\b", r"\biommu\b",
    r"\bserverless.*snapshot", r"\bsnapshot.*serverless", r"\bmemory balloon",
    r"\bcloud.*isolation", r"\btenant.*isolation", r"\bvirtual.*i/o\b",
    r"\bvirtual.*nvme\b", r"\bvirtual.*gpu\b", r"\bvirtual.*device\b",
)
_EXCLUDE_PATTERNS = (
    r"\bjava virtual machine\b", r"\bjvm\b", r"\bvirtual reality\b",
    r"\bvirtual network function\b",
)
log = logging.getLogger(__name__)


def paper_id(venue: str, year: int, title: str) -> str:
    """Return a stable identifier independent of a publisher URL."""
    normalized = _normalized_title(title)
    digest = hashlib.sha256(
        f"{venue}|{year}|{normalized}".encode("utf-8")
    ).hexdigest()[:20]
    return f"{venue}-{year}-{digest}"


def _normalized_title(value: object) -> str:
    """Normalize publisher title markup for conservative exact matching."""
    if not isinstance(value, str):
        return ""
    value = html.unescape(re.sub(r"<[^>]+>", " ", value))
    return re.sub(r"\W+", " ", value.casefold()).strip()


def _official_title_matches(expected: object, actual: object) -> bool:
    """Allow only known editorial suffixes on an otherwise exact title."""
    expected_normalized = _normalized_title(expected)
    actual_normalized = _normalized_title(actual)
    if actual_normalized == expected_normalized:
        return True
    return actual_normalized in {
        f"{expected_normalized} operational systems",
        f"{expected_normalized} operational systems paper",
    }


def is_candidate_title(title: str, abstract: str | None = "") -> bool:
    """Flag metadata for review, never as an automatic inclusion decision.

    An abstract can recover an opaque title. Technical names such as TDX also
    occur in related-work paragraphs, so a match still needs editorial review.
    Generic virtual memory, JVM and VR are not system-virtualization evidence.
    """
    value = html.unescape(title).casefold()
    if any(re.search(pattern, value) for pattern in _EXCLUDE_PATTERNS):
        return False
    value += " " + html.unescape(abstract or "").casefold()
    return any(re.search(pattern, value) for pattern in (
        *_DIRECT_PATTERNS, *_RELATED_PATTERNS,
    ))


def _request_session() -> requests.Session:
    session = requests.Session()
    # Server deployments sometimes inherit a workstation-only proxy address.
    session.trust_env = False
    session.headers.update({
        "User-Agent": (
            f"virt-report/{__version__} (conference metadata; taifu@taifua.com)"
        ),
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


def _get_html(session: requests.Session, url: str) -> str:
    """Fetch one official metadata page with a bounded curl fallback."""
    try:
        response = session.get(url, timeout=30)
        response.raise_for_status()
        return response.text
    except requests.RequestException as exc:
        log.warning("会议官方页连接失败，切换 curl: %s", exc)
    result = subprocess.run([
        "curl", "--noproxy", "*", "-4", "-L", "--fail", "--silent",
        "--show-error", "--retry", "2", "--retry-all-errors",
        "--max-time", "45", url,
    ], check=True, capture_output=True, text=True)
    return result.stdout


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
        paper_doi = _doi_from_url(paper["url"])
        authors = paper.get("authors", [])
        affiliations = paper.get("author_affiliations", [])
        institutions = canonical_institutions(paper.get("institutions", []))
        affiliation_source = paper.get("affiliation_source")
        affiliation_source_url = paper.get("affiliation_source_url")
        affiliation_verified_at = paper.get("affiliation_verified_at")
        existing = conn.execute(
            "SELECT 1 FROM conference_papers WHERE paper_id=?", (pid,)
        ).fetchone()
        if not existing:
            conn.execute("""
                INSERT INTO conference_papers (
                    paper_id,venue,year,title,authors_json,affiliations_json,
                    institutions_json,doi,
                    affiliation_source,affiliation_source_url,
                    affiliation_verified_at,official_url,source_url,fetched_at,
                    raw_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                pid, paper["venue"], paper["year"], paper["title"],
                json.dumps(authors, ensure_ascii=False),
                json.dumps(affiliations, ensure_ascii=False),
                json.dumps(institutions, ensure_ascii=False), paper_doi or None,
                affiliation_source, affiliation_source_url,
                affiliation_verified_at, paper["url"], paper["url"],
                reviewed_at, "{}",
            ))
        else:
            # Metadata explicitly committed in the public snapshot is reviewed
            # provenance and must not be discarded on a fresh database import.
            conn.execute("""
                UPDATE conference_papers SET
                    official_url=?,doi=COALESCE(doi,?),
                    authors_json=CASE WHEN ? != '[]' THEN ? ELSE authors_json END,
                    affiliations_json=CASE WHEN ? != '[]' THEN ?
                                           ELSE affiliations_json END,
                    institutions_json=CASE WHEN ? != '[]' THEN ?
                                           ELSE institutions_json END,
                    affiliation_source=COALESCE(?,affiliation_source),
                    affiliation_source_url=COALESCE(?,affiliation_source_url),
                    affiliation_verified_at=COALESCE(?,affiliation_verified_at)
                WHERE paper_id=?
            """, (
                paper["url"], paper_doi or None,
                json.dumps(authors, ensure_ascii=False),
                json.dumps(authors, ensure_ascii=False),
                json.dumps(affiliations, ensure_ascii=False),
                json.dumps(affiliations, ensure_ascii=False),
                json.dumps(institutions, ensure_ascii=False),
                json.dumps(institutions, ensure_ascii=False),
                affiliation_source, affiliation_source_url,
                affiliation_verified_at, pid,
            ))
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
        is_candidate_title(row[0], row[1]) for row in conn.execute("""
            SELECT p.title,p.abstract FROM conference_papers p
            LEFT JOIN conference_reviews r ON r.paper_id=p.paper_id
            WHERE p.year BETWEEN ? AND ? AND r.paper_id IS NULL
        """, (start_year, end_year))
    )
    return {"venues": counts, "unreviewed": pending, "candidates": candidates,
            "errors": errors}


def candidate_rows(conn: sqlite3.Connection, start_year: int = 2010,
                   end_year: int = 2026) -> list[dict]:
    """Return metadata-matched, not-yet-reviewed rows for editorial inspection."""
    rows = conn.execute("""
        SELECT p.* FROM conference_papers p
        LEFT JOIN conference_reviews r ON r.paper_id=p.paper_id
        WHERE p.year BETWEEN ? AND ? AND r.paper_id IS NULL
        ORDER BY p.year DESC,p.venue,p.title
    """, (start_year, end_year)).fetchall()
    return [dict(row) for row in rows
            if is_candidate_title(row["title"], row["abstract"])]


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


def _clean_metadata_text(value: object) -> str:
    """Normalize a publisher-provided display string without inferring content."""
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def _canonical_institution_parts(value: object) -> list[str]:
    """Return conservative canonical institutions for one source label.

    Publisher metadata often repeats the same institution with department,
    country, or abbreviation variants.  Only aliases that unambiguously name
    the same organisation are collapsed here; unknown labels are preserved.
    A combined CAS/UCAS affiliation remains one collaboration label so the
    display preserves the official author line without counting spelling
    variants twice.
    """
    cleaned = _clean_metadata_text(value).strip(" ,;")
    if not cleaned:
        return []
    folded = cleaned.casefold()
    if "institute of computing technology" in folded and (
            "chinese academy of sciences" in folded
            or re.search(r"\bcas\b", folded)):
        if "university of chinese academy of sciences" in folded:
            return [
                "Institute of Computing Technology, Chinese Academy of "
                "Sciences / University of Chinese Academy of Sciences"
            ]
        return ["Institute of Computing Technology, Chinese Academy of Sciences"]
    if "university of chinese academy of sciences" in folded:
        return ["University of Chinese Academy of Sciences"]
    if "peking university" in folded:
        return ["Peking University"]
    if "huawei cloud" in folded:
        return ["Huawei Cloud"]
    return [cleaned]


def canonical_institutions(values: Iterable[object]) -> list[str]:
    """Canonicalize and de-duplicate institutions in source order."""
    result: list[str] = []
    keys: set[str] = set()
    for value in values:
        for institution in _canonical_institution_parts(value):
            key = re.sub(r"\W+", " ", institution.casefold()).strip()
            if key and key not in keys:
                keys.add(key)
                result.append(institution)
    return result


def _crossref_author_metadata(message: dict) -> tuple[list[str], list[dict]]:
    """Return names and explicit author-affiliation mappings from Crossref."""
    names: list[str] = []
    mappings: list[dict] = []
    for raw in message.get("author", []) or []:
        if not isinstance(raw, dict):
            continue
        name = _clean_metadata_text(raw.get("name"))
        if not name:
            name = " ".join(filter(None, (
                _clean_metadata_text(raw.get("given")),
                _clean_metadata_text(raw.get("family")),
            )))
        if not name:
            continue
        if name not in names:
            names.append(name)
        institutions: list[str] = []
        for affiliation in raw.get("affiliation", []) or []:
            if not isinstance(affiliation, dict):
                continue
            institution = _clean_metadata_text(affiliation.get("name"))
            if institution and institution not in institutions:
                institutions.append(institution)
        mappings.append({"name": name, "institutions": institutions})
    return names, mappings


class _CitationMetaParser(HTMLParser):
    """Collect only public citation names and institutions, never emails."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.mappings: list[dict] = []

    def handle_starttag(self, tag: str,
                        attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "meta":
            return
        values = {key.casefold(): value for key, value in attrs if value}
        name = values.get("name", "").casefold()
        content = _clean_metadata_text(values.get("content"))
        if not content:
            return
        if name == "citation_title":
            self.title = content
        elif name == "citation_author":
            self.mappings.append({"name": content, "institutions": []})
        elif name == "citation_author_institution" and self.mappings:
            institutions = self.mappings[-1]["institutions"]
            if content not in institutions:
                institutions.append(content)


def parse_citation_metadata(page: str) -> dict:
    """Parse citation meta tags from an official conference HTML page."""
    parser = _CitationMetaParser()
    parser.feed(page)
    mappings = _valid_affiliation_mappings(parser.mappings)
    return {
        "title": parser.title,
        "authors": list(dict.fromkeys(item["name"] for item in mappings)),
        "author_affiliations": mappings,
        "institutions": list(dict.fromkeys(
            institution for item in mappings
            for institution in item["institutions"]
        )),
    }


def enrich_usenix_affiliations(
        conn: sqlite3.Connection, limit: int = 200,
        force: bool = False) -> dict[str, int]:
    """Use verified USENIX HTML citation metadata for curated papers."""
    session = _request_session()
    rows = conn.execute("""
        SELECT p.paper_id,p.title,p.official_url,p.affiliation_source,
               p.affiliation_verified_at
        FROM conference_papers p
        INNER JOIN conference_reviews r ON r.paper_id=p.paper_id
        WHERE p.official_url IS NOT NULL
          AND (p.affiliation_source IS NULL
               OR p.affiliation_source IN ('crossref','usenix'))
          AND (? OR p.affiliation_source!='usenix'
                 OR p.affiliation_verified_at IS NULL)
        ORDER BY p.year DESC,p.venue,p.title
    """, (int(force),)).fetchall()
    eligible = []
    for row in rows:
        parsed = urlparse(row["official_url"])
        if (parsed.scheme == "https"
                and parsed.hostname in {"usenix.org", "www.usenix.org"}
                and parsed.path.startswith("/conference/")):
            eligible.append(row)
    result = {
        "eligible": len(eligible), "checked": 0, "updated": 0,
        "without_affiliations": 0, "title_mismatch": 0, "errors": 0,
    }
    for row in eligible[:max(0, limit)]:
        try:
            page = _get_html(session, row["official_url"])
        except (requests.RequestException, subprocess.SubprocessError,
                RuntimeError, ValueError) as exc:
            result["errors"] += 1
            log.warning("USENIX 论文单位补充失败 %s: %s", row["paper_id"], exc)
            continue
        metadata = parse_citation_metadata(page)
        result["checked"] += 1
        if not _official_title_matches(row["title"], metadata["title"]):
            result["title_mismatch"] += 1
            log.warning("USENIX 页面标题不一致，跳过 %s", row["paper_id"])
            continue
        if not metadata["authors"]:
            result["errors"] += 1
            log.warning("USENIX 页面缺少 citation_author: %s", row["paper_id"])
            continue
        if not metadata["institutions"]:
            result["without_affiliations"] += 1
        conn.execute("""
            UPDATE conference_papers SET authors_json=?,affiliations_json=?,
                institutions_json=?,
                affiliation_source='usenix',affiliation_source_url=?,
                affiliation_verified_at=?
            WHERE paper_id=?
        """, (
            json.dumps(metadata["authors"], ensure_ascii=False),
            json.dumps(metadata["author_affiliations"], ensure_ascii=False),
            json.dumps(canonical_institutions(metadata["institutions"]),
                       ensure_ascii=False),
            row["official_url"], db.now_utc_iso(), row["paper_id"],
        ))
        result["updated"] += 1
        time.sleep(0.25)
    conn.commit()
    return result


def discover_curated_dois(conn: sqlite3.Connection,
                          limit: int = 200) -> dict[str, int]:
    """Find missing curated-paper DOIs by exact Crossref title/year match.

    Crossref search is used only for discovery.  A DOI is accepted only when a
    single DOI has the same normalized title and publication year; ambiguous
    or approximate results remain unset for editorial review.
    """
    session = _request_session()
    rows = conn.execute("""
        SELECT p.paper_id,p.title,p.year
        FROM conference_papers p
        INNER JOIN conference_reviews r ON r.paper_id=p.paper_id
        WHERE p.doi IS NULL AND p.affiliation_source IS NULL
        ORDER BY p.year DESC,p.venue,p.title
    """).fetchall()
    result = {
        "checked": 0, "found": 0, "not_found": 0,
        "ambiguous": 0, "errors": 0,
    }
    for row in rows[:max(0, limit)]:
        result["checked"] += 1
        try:
            payload = _get_json(session, CROSSREF_WORKS_API, params={
                "query.title": row["title"],
                "filter": (f"from-pub-date:{row['year']}-01-01,"
                           f"until-pub-date:{row['year']}-12-31"),
                "rows": 5,
                "select": "DOI,title,published,issued",
                "mailto": CROSSREF_MAILTO,
            })
        except (requests.RequestException, subprocess.SubprocessError,
                json.JSONDecodeError, RuntimeError, ValueError) as exc:
            result["errors"] += 1
            log.warning("DOI 精确匹配失败 %s: %s", row["paper_id"], exc)
            continue
        expected = _normalized_title(row["title"])
        matches: dict[str, dict] = {}
        for item in payload.get("message", {}).get("items", []) or []:
            titles = item.get("title", []) or []
            if not titles or _normalized_title(titles[0]) != expected:
                continue
            years = set()
            for key in ("published", "issued"):
                parts = item.get(key, {}).get("date-parts", [])
                if parts and parts[0]:
                    years.add(parts[0][0])
            if years and row["year"] not in years:
                continue
            doi = _clean_metadata_text(item.get("DOI"))
            if doi:
                matches[doi.casefold()] = item
        if not matches:
            result["not_found"] += 1
        elif len(matches) > 1:
            result["ambiguous"] += 1
        else:
            doi = _clean_metadata_text(next(iter(matches.values())).get("DOI"))
            conn.execute(
                "UPDATE conference_papers SET doi=? WHERE paper_id=? AND doi IS NULL",
                (doi, row["paper_id"]),
            )
            result["found"] += 1
        time.sleep(0.25)
    conn.commit()
    return result


def enrich_affiliations(
        conn: sqlite3.Connection, limit: int = 200,
        force: bool = False) -> dict[str, int]:
    """Enrich DOI records with explicit Crossref author affiliations.

    Exact DOI lookup is the identity boundary.  Missing publisher affiliation
    data is recorded as checked and is never replaced with a guessed employer.
    Metadata from a manually reviewed non-Crossref source is left untouched.
    """
    session = _request_session()
    rows = conn.execute("""
        SELECT p.paper_id,p.doi,p.authors_json,p.affiliation_source,
               p.affiliation_verified_at
        FROM conference_papers p
        INNER JOIN conference_reviews r ON r.paper_id=p.paper_id
        WHERE p.doi IS NOT NULL
          AND (p.affiliation_source IS NULL OR p.affiliation_source='crossref')
          AND (? OR p.affiliation_verified_at IS NULL)
        ORDER BY p.year DESC,p.venue,p.title
    """, (int(force),)).fetchall()
    result = {
        "checked": 0, "authors_updated": 0, "affiliations_updated": 0,
        "without_affiliations": 0, "errors": 0,
    }
    for row in rows[:max(0, limit)]:
        doi = str(row["doi"]).strip()
        try:
            payload = _get_json(
                session, CROSSREF_API.format(doi),
                params={"mailto": CROSSREF_MAILTO},
            )
        except (requests.RequestException, subprocess.SubprocessError,
                json.JSONDecodeError, RuntimeError, ValueError) as exc:
            result["errors"] += 1
            log.warning("论文单位补充失败 %s: %s", row["paper_id"], exc)
            continue
        message = payload.get("message", {})
        returned_doi = _clean_metadata_text(message.get("DOI"))
        if returned_doi and returned_doi.casefold() != doi.casefold():
            result["errors"] += 1
            log.warning("Crossref DOI 不一致，跳过 %s", row["paper_id"])
            continue
        authors, mappings = _crossref_author_metadata(message)
        institutions = canonical_institutions(
            institution for mapping in mappings
            for institution in mapping["institutions"]
        )
        existing_authors = json.loads(row["authors_json"] or "[]")
        authors_json = row["authors_json"] or "[]"
        if authors and authors != existing_authors:
            authors_json = json.dumps(authors, ensure_ascii=False)
            result["authors_updated"] += 1
        verified_at = db.now_utc_iso()
        source_url = CROSSREF_API.format(doi)
        conn.execute("""
            UPDATE conference_papers SET authors_json=?,affiliations_json=?,
                institutions_json=?,
                affiliation_source='crossref',affiliation_source_url=?,
                affiliation_verified_at=?
            WHERE paper_id=?
        """, (
            authors_json, json.dumps(mappings, ensure_ascii=False),
            json.dumps(institutions, ensure_ascii=False),
            source_url, verified_at, row["paper_id"],
        ))
        result["checked"] += 1
        if institutions:
            result["affiliations_updated"] += 1
        else:
            result["without_affiliations"] += 1
        # Crossref asks clients to identify themselves; this small delay also
        # keeps this annual maintenance command well below public API limits.
        time.sleep(0.25)
    conn.commit()
    return result


def _doi_from_url(value: object) -> str:
    """Extract a DOI embedded in a trusted publisher URL, if present."""
    cleaned = unquote(_clean_metadata_text(value))
    match = re.search(r"(?:doi\.org/|/doi/)(10\.\d{4,9}/[^?#\s]+)", cleaned,
                      flags=re.IGNORECASE)
    return match.group(1).rstrip("/.") if match else ""


def _valid_string_list(value: object) -> list[str]:
    """Return unique non-empty strings from untrusted stored JSON."""
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        cleaned = _clean_metadata_text(item)
        if cleaned and cleaned not in result:
            result.append(cleaned)
    return result


def _valid_affiliation_mappings(value: object) -> list[dict]:
    """Validate public author-affiliation mappings without adding guesses."""
    if not isinstance(value, list):
        return []
    result: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = _clean_metadata_text(item.get("name"))
        if not name:
            continue
        result.append({
            "name": name,
            "institutions": _valid_string_list(item.get("institutions", [])),
        })
    return result


def sync_public_metadata(conn: sqlite3.Connection,
                         content_path: Path = CONTENT_PATH) -> int:
    """Copy verified metadata for curated papers into the public snapshot."""
    content = json.loads(content_path.read_text(encoding="utf-8"))
    updated = 0
    for paper in content.get("papers", []):
        pid = paper_id(paper["venue"], paper["year"], paper["title"])
        row = conn.execute("""
            SELECT authors_json,affiliations_json,institutions_json,
                   affiliation_source,
                   affiliation_source_url,affiliation_verified_at
            FROM conference_papers WHERE paper_id=?
        """, (pid,)).fetchone()
        if not row:
            continue
        authors = _valid_string_list(json.loads(row["authors_json"] or "[]"))
        mappings = _valid_affiliation_mappings(
            json.loads(row["affiliations_json"] or "[]")
        )
        institutions = canonical_institutions(
            json.loads(row["institutions_json"] or "[]")
        )
        if not institutions:
            institutions = canonical_institutions(
                institution for mapping in mappings
                for institution in mapping["institutions"]
            )
        if not authors and not institutions:
            continue
        before = {key: paper.get(key) for key in (
            "authors", "author_affiliations", "institutions",
            "affiliation_source", "affiliation_source_url",
            "affiliation_verified_at",
        )}
        if authors:
            paper["authors"] = authors
        if mappings:
            paper["author_affiliations"] = mappings
        if institutions:
            paper["institutions"] = institutions
        if row["affiliation_source"]:
            paper["affiliation_source"] = row["affiliation_source"]
        if row["affiliation_source_url"]:
            paper["affiliation_source_url"] = row["affiliation_source_url"]
        if row["affiliation_verified_at"]:
            paper["affiliation_verified_at"] = row["affiliation_verified_at"]
        after = {key: paper.get(key) for key in before}
        updated += before != after
    if updated:
        content["updated_at"] = db.now_utc_iso()[:10]
    content_path.write_text(
        json.dumps(content, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    load_content.cache_clear()
    return updated


def _read_content() -> dict:
    content = json.loads(CONTENT_PATH.read_text(encoding="utf-8"))
    if not INSTITUTION_OVERRIDES_PATH.exists():
        return content
    overrides = json.loads(
        INSTITUTION_OVERRIDES_PATH.read_text(encoding="utf-8")
    )
    papers_by_id = {paper["id"]: paper for paper in content.get("papers", [])}
    unknown = set(overrides.get("papers", {})) - papers_by_id.keys()
    if unknown:
        raise ValueError(
            f"单位核验文件引用未知论文: {', '.join(sorted(unknown))}"
        )
    verified_at = overrides.get("verified_at")
    for pid, metadata in overrides.get("papers", {}).items():
        if not isinstance(metadata, dict):
            raise ValueError(f"论文单位核验记录格式错误: {pid}")
        institutions = canonical_institutions(metadata.get("institutions", []))
        source_url = _clean_metadata_text(metadata.get("source_url"))
        if not institutions or not source_url.startswith("https://"):
            raise ValueError(f"论文单位核验记录不完整: {pid}")
        paper = papers_by_id[pid]
        paper["institutions"] = institutions
        paper["affiliation_source"] = metadata.get("source", "official")
        paper["affiliation_source_url"] = source_url
        paper["affiliation_verified_at"] = (
            metadata.get("verified_at") or verified_at
        )
    return content


@lru_cache(maxsize=1)
def load_content() -> dict:
    """Return validated public content plus derived page data."""
    content = _read_content()
    venues = content.get("venues", [])
    venue_keys = {item["key"] for item in venues}
    if len(venue_keys) != len(venues):
        raise ValueError("会议目录包含重复 key")
    for check in content.get("edition_checks", []):
        if (check["venue"] not in venue_keys
                or not check["source_url"].startswith("https://")):
            raise ValueError("会议核对记录包含未知会议或非 HTTPS 来源")

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
        authors = _valid_string_list(paper.get("authors", []))
        mappings = _valid_affiliation_mappings(
            paper.get("author_affiliations", [])
        )
        if not authors:
            authors = list(dict.fromkeys(item["name"] for item in mappings))
        institutions = canonical_institutions(
            _valid_string_list(paper.get("institutions", []))
        )
        if not institutions:
            institutions = canonical_institutions(
                institution for mapping in mappings
                for institution in mapping["institutions"]
            )
        source_url = paper.get("affiliation_source_url")
        if source_url and not source_url.startswith("https://"):
            raise ValueError(f"论文单位来源必须使用 HTTPS: {paper['id']}")
        paper["authors"] = authors
        paper["author_affiliations"] = mappings
        paper["institutions"] = institutions
        paper["institution_label"] = " · ".join(
            institutions[:INSTITUTION_DISPLAY_LIMIT]
        )
        paper["institution_hidden"] = institutions[INSTITUTION_DISPLAY_LIMIT:]
        paper["institution_hidden_label"] = " · ".join(
            paper["institution_hidden"]
        )
        paper["affiliation_source_label"] = {
            "usenix": "USENIX 官方页面", "crossref": "Crossref",
            "official": "会议官方页面",
            "institution": "机构论文页面",
        }.get(paper.get("affiliation_source"), paper.get("affiliation_source", ""))
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
        checks = [item for item in content.get("edition_checks", [])
                  if item["venue"] == venue["key"]]
        venue["latest_check"] = max(
            checks, key=lambda item: (item["year"], item["checked_at"]),
            default=None,
        )
        venue["latest_year"] = max(
            (paper["year"] for paper in papers if paper["venue"] == venue["key"]),
            default=None,
        )

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
    content["year_counts"] = {
        year: sum(paper["year"] == year for paper in papers)
        for year in content["years"]
    }
    content["academic_venues"] = [
        venue for venue in venues if venue["kind"] == "学术会议"
    ]
    content["paper_venues"] = [
        venue for venue in content["academic_venues"] if venue["paper_count"]
    ]
    related = {"topics": content.get("topic_links", [])}
    known_topics = {item["key"]: item for item in related["topics"]}
    if len(known_topics) != len(related["topics"]):
        raise ValueError("专题关联包含重复 key")
    content["related_topics"] = []
    for item in related["topics"]:
        if not re.fullmatch(r"[a-z]+(?:-[a-z]+)*", item["key"]):
            raise ValueError("专题关联 key 格式错误")
        ids = item["paper_ids"]
        if not 1 <= len(ids) <= 3 or len(set(ids)) != len(ids):
            raise ValueError("每个专题应关联 1—3 篇不重复的精选论文")
        if any(not release["href"].startswith("versions.html?")
               for release in item.get("releases", [])):
            raise ValueError("相关版本只允许指向本站版本页")
        unknown = set(item["paper_ids"]) - paper_ids
        if unknown:
            raise ValueError(f"专题关联引用未知论文: {sorted(unknown)}")
        content["related_topics"].append({
            **item, "papers": [by_id[pid] for pid in item["paper_ids"]],
        })
    for paper in papers:
        paper["community_topics"] = [
            {"key": key, "name": item["name"]}
            for key, item in known_topics.items()
            if paper["id"] in item["paper_ids"]
        ]
    return content


def related_topic_content(topic_key: str) -> dict | None:
    """Return a small editorial reference list, separate from community items."""
    return next((item for item in load_content()["related_topics"]
                 if item["key"] == topic_key), None)
