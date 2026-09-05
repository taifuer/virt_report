"""Offline release timeline sourced from official announcements and release tags.

The web reader never performs network I/O. Refreshes merge verified records into
an atomic JSON snapshot; a failed source cannot remove previously published data.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from threading import Lock
from urllib.parse import urljoin, urlsplit

from requests import HTTPError

from virt_report.collectors.base import http_get
from virt_report.config import Config
from virt_report.locking import process_lock

log = logging.getLogger(__name__)
CONTENT_DIR = Path(__file__).parent / "content"
PROJECTS = {"qemu": "QEMU", "kvm": "KVM", "libvirt": "Libvirt"}
QEMU_ARCHIVE = "https://www.qemu.org/blog/category/releases/index.html"
LIBVIRT_NEWS = "https://libvirt.org/news.html"
KERNEL_MIRROR = "https://kernel.googlesource.com/pub/scm/"
KERNEL_TAGS = KERNEL_MIRROR + "linux/kernel/git/stable/linux/+refs/tags?format=JSON"
GITLAB_REPOS = {"qemu": "qemu-project/qemu", "libvirt": "libvirt/libvirt"}
VERSION_RE = re.compile(r"\d+\.\d+(?:\.\d+){0,2}\Z")
FOCUS = re.compile(r"migration|migrat|snapshot|hotplug|performance|iothread|"
                   r"virtio|vfio|iommufd|memory|qemu|x86|arm|sev|tdx|dirty", re.I)


class Node:
    def __init__(self, tag: str, attrs: dict | None = None):
        self.tag = tag
        self.attrs = attrs or {}
        self.children: list[Node | str] = []

    def find(self, tag: str) -> list[Node]:
        return [found for child in self.children if isinstance(child, Node)
                for found in ([child] if child.tag == tag else []) + child.find(tag)]

    def text(self) -> str:
        return " ".join(" ".join(child.text() if isinstance(child, Node) else child
                                 for child in self.children).split())

    def raw_text(self) -> str:
        return "".join(child.raw_text() if isinstance(child, Node) else child
                       for child in self.children)


class Document(HTMLParser):
    def __init__(self, text: str):
        super().__init__(convert_charrefs=True)
        self.root = Node("root")
        self.stack = [self.root]
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img",
                       "input", "link", "meta", "param", "source", "wbr"}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, text):
        self.stack[-1].children.append(text)


def _read_json(path: Path, default: dict) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else default
    except (OSError, ValueError):
        return default


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    temporary.replace(path)


class SourceCache:
    """Cache small source pages and revalidate mutable indexes once per refresh."""

    def __init__(self, directory: Path):
        self.directory = directory
        self._locks: dict[str, Lock] = {}
        self._guard = Lock()

    def get(self, url: str, *, immutable: bool = False) -> str:
        # Several historical releases can share one monthly archive page.
        # Serialize writes to that cache key, not all network requests.
        with self._guard:
            lock = self._locks.setdefault(url, Lock())
        with lock:
            return self._get(url, immutable=immutable)

    def _get(self, url: str, *, immutable: bool = False) -> str:
        key = hashlib.sha256(url.encode()).hexdigest()
        path = self.directory / f"{key}.json"
        cached = _read_json(path, {})
        if immutable and cached.get("text"):
            return cached["text"]
        headers = {}
        if cached.get("etag"):
            headers["If-None-Match"] = cached["etag"]
        if cached.get("modified"):
            headers["If-Modified-Since"] = cached["modified"]
        response = http_get(url, headers=headers, retries=2)
        if response.status_code == 304 and cached.get("text"):
            return cached["text"]
        response.encoding = "utf-8"
        text = response.text
        if not text.strip() or len(response.content) > 6_000_000:
            raise ValueError("发布源返回空内容或超出索引大小限制")
        if "Making sure you're not a bot" in text or "Oh noes!" in text:
            raise ValueError("发布源返回了访问验证页")
        _write_json(path, {"url": url, "text": text,
                           "etag": response.headers.get("ETag"),
                           "modified": response.headers.get("Last-Modified")})
        return text


def release_family(project: str, version: str) -> str | None:
    """Return the feature version, respecting historical numbering schemes.

    Libvirt before 2.0 and Linux 2.6 used a fourth digit for maintenance.
    QEMU before 0.10 had no consistent feature/maintenance branch scheme.
    """
    if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
        return None
    parts = tuple(map(int, version.split(".")))
    if project == "kvm":
        if parts[:2] == (2, 6) and len(parts) in (3, 4) and parts[2] >= 20:
            return ".".join(map(str, parts[:3])) if len(parts) == 3 or parts[3] else None
        if parts[0] >= 3 and (len(parts) == 2 or (len(parts) == 3 and parts[2] > 0)):
            return ".".join(map(str, parts[:2]))
    elif project == "libvirt":
        if parts[0] < 2 and len(parts) in (3, 4):
            return ".".join(map(str, parts[:3])) if len(parts) == 3 or parts[3] else None
        if parts[0] >= 2 and len(parts) == 3:
            return f"{parts[0]}.{parts[1]}.0"
    elif project == "qemu":
        if parts == (1, 0) or (len(parts) == 3 and parts[:2] == (1, 0)):
            return "1.0"  # The original tag is v1.0, not v1.0.0.
        if len(parts) == 3:
            return version if parts[:2] < (0, 10) else f"{parts[0]}.{parts[1]}.0"
    return None


def _valid(record: dict, today: date) -> bool:
    try:
        if record["project"] not in PROJECTS or not VERSION_RE.fullmatch(record["version"]):
            return False
        family = release_family(record["project"], record["version"])
        if family is None:
            return False
        return (date.fromisoformat(record["released_on"]) <= today
                and record["kind"] == ("feature" if family == record["version"] else "maintenance")
                and urlsplit(record["source_url"]).scheme == "https")
    except (KeyError, TypeError, ValueError):
        return False


def _record(project: str, version: str, released_on: str, url: str,
            *, date_source: str = "announcement", **extra) -> dict:
    maintenance = release_family(project, version) != version
    return {"project": project, "version": version, "released_on": released_on,
            "kind": "maintenance" if maintenance else "feature",
            "source_url": url, "date_source": date_source,
            "highlights": [], **extra}


def _highlights(items: list[str]) -> list[str]:
    values = list(dict.fromkeys(text.strip() for text in items
                                if 8 < len(text.strip()) < 420))
    return sorted(values, key=lambda text: not bool(FOCUS.search(text)))[:3]


def parse_libvirt(text: str) -> list[dict]:
    records = []
    for section in Document(text).root.find("div"):
        headings = [node for node in section.children
                    if isinstance(node, Node) and node.tag in {"h1", "h2"}]
        if not headings:
            continue
        match = re.match(r"v(\d+\.\d+\.\d+)\s+\((\d{4}-\d{2}-\d{2})\)",
                         headings[0].text())
        if not match:
            continue
        version, released_on = match.groups()
        titles = []
        for item in section.find("li"):
            # The first paragraph is the change title; nested lists are groups.
            direct = [node for node in item.children if isinstance(node, Node)]
            if direct and direct[0].tag == "p" and not direct[0].find("strong"):
                titles.append(direct[0].text())
        records.append(_record(
            "libvirt", version, released_on,
            LIBVIRT_NEWS + "#" + section.attrs.get("id", ""),
            highlights=_highlights(titles),
        ))
    if not records:
        raise ValueError("Libvirt 发布记录结构无法识别")
    return records


def parse_qemu_archive(text: str) -> list[dict]:
    records = {}
    for link in Document(text).root.find("a"):
        match = re.match(r"QEMU (?:stable )?version (\d+\.\d+\.\d+) released$",
                         link.text())
        url = urljoin(QEMU_ARCHIVE, link.attrs.get("href", ""))
        stamp = re.search(r"/(\d{4})/(\d{2})/(\d{2})/", url)
        if match and stamp and urlsplit(url).netloc == "www.qemu.org":
            version = match[1]
            records[version] = _record("qemu", version, "-".join(stamp.groups()), url)
    if not records:
        raise ValueError("QEMU 发布档案结构无法识别")
    return list(records.values())


def parse_qemu_notes(text: str) -> dict:
    root = Document(text).root
    posts = [node for node in root.find("article") if "post" in node.attrs.get("class", "")]
    post = posts[0] if posts else root
    paragraphs = " ".join(node.text() for node in post.find("p"))
    stats = re.search(r"([\d,]+)\+? commits from ([\d,]+) authors", paragraphs)
    result = {"highlights": _highlights([node.text() for node in post.find("li")
                                         if not node.find("ul")])}
    if stats:
        result["commits"] = int(stats[1].replace(",", ""))
        result["commits_approximate"] = "+" in stats[0]
        result["contributors"] = int(stats[2].replace(",", ""))
    return result


def parse_kernel_tag(text: str, version: str) -> dict:
    """Use the annotated release tag's tagger date, never archive mtime."""
    root = Document(text).root
    tag_date = None
    for row in root.find("div"):
        if row.attrs.get("class") != "Metadata-row":
            continue
        terms = row.find("dt")
        if terms and terms[0].text() == "tagger":
            cells = [node for node in row.find("div")
                     if node.attrs.get("class") == "Metadata-descriptionCell"]
            if cells:
                tag_date = datetime.strptime(cells[-1].text(), "%a %b %d %H:%M:%S %Y %z")
                break
    messages = [node.raw_text() for node in root.find("pre")
                if "MetadataMessage" in node.attrs.get("class", "")]
    identity = (rf"(?:Linux v?{re.escape(version)}|"
                rf"This is the v?{re.escape(version)} (?:stable )?release|"
                rf"{re.escape(version)})(?:\s|$)")
    if not tag_date or not messages or not re.match(identity, messages[0], re.I):
        raise ValueError(f"Linux {version} 正式发布标签无法核验")
    return {"released_on": tag_date.date().isoformat(),
            "released_at": tag_date.astimezone(timezone.utc).isoformat()}


def parse_kvm_notes(text: str) -> list[str]:
    messages = [node.raw_text() for node in Document(text).root.find("pre")
                if "MetadataMessage" in node.attrs.get("class", "")]
    if not messages:
        return []
    message = messages[0].split("-----BEGIN PGP SIGNATURE-----")[0]
    # Keep the architecture with its bullet so e.g. an Arm feature is not
    # accidentally presented as an x86 feature after flattening the source.
    candidates, architecture, current = [], "", ""
    for line in message.splitlines() + [""]:
        if re.match(r"^[\w /()64-]+:$", line.strip()):
            if current:
                candidates.append(current)
            architecture, current = line.strip().rstrip(":"), ""
        elif re.match(r"^\s*[-*] ", line):
            if current:
                candidates.append(current)
            current = (architecture + ": " if architecture else "") + re.sub(r"^\s*[-*] ", "", line).strip()
        elif line.strip() and current:
            current += " " + line.strip()
        elif current:
            candidates.append(current)
            current = ""
    return _highlights(candidates)


def _kvm_note_ref(refs: dict, version: str) -> str | None:
    """Older KVM cycles use for-linus or a nested tags/ prefix."""
    names = [name for name in refs if re.fullmatch(
        rf"(?:tags/)?(?:kvm-{re.escape(version)}-\d+|"
        rf"for-linus(?:-with-topic-branches)?-{re.escape(version)})", name)]
    return min(names, key=lambda name: (not name.endswith("-1"),
                                        name.startswith("tags/"), name)) if names else None


def fetch_gitlab_tags(project: str, cache: SourceCache) -> list[dict]:
    """Read public tag metadata in bounded pages, without cloning repositories."""
    repo = GITLAB_REPOS[project].replace("/", "%2F")
    tags = {}
    for page in range(1, 31):
        url = (f"https://gitlab.com/api/v4/projects/{repo}/repository/tags"
               f"?per_page=100&search=%5Ev&order_by=name&sort=asc&page={page}")
        batch = json.loads(cache.get(url))
        if not isinstance(batch, list):
            raise ValueError("GitLab 标签索引结构无法识别")
        for tag in batch:
            if release_family(project, tag.get("name", "").removeprefix("v")):
                tags[tag["name"]] = tag
        if len(batch) < 100:
            return list(tags.values())
    raise ValueError("GitLab 标签索引超过分页上限，未发布不完整结果")


def parse_gitlab_tag(project: str, tag: dict) -> dict | None:
    version = tag.get("name", "").removeprefix("v")
    if not release_family(project, version) or not tag.get("created_at"):
        return None  # A lightweight tag has no tag date; do not use commit date.
    stamp = datetime.fromisoformat(tag["created_at"].replace("Z", "+00:00"))
    commit_date = tag.get("commit", {}).get("committed_date")
    if commit_date:
        committed = datetime.fromisoformat(commit_date.replace("Z", "+00:00"))
        if abs((stamp - committed).total_seconds()) > 7 * 86400:
            return None  # Re-signed/imported tags can be years newer than release.
    return _record(project, version, stamp.date().isoformat(),
                   f"https://gitlab.com/{GITLAB_REPOS[project]}/-/tags/{tag['name']}",
                   date_source="release_tag", released_at=stamp.astimezone(timezone.utc).isoformat())


def qemu_announcement_links(text: str, base: str, version: str) -> list[str]:
    """Find original release announcements, not replies, RCs or patch series."""
    # Early releases used 0.2 on the website and v0.2.0 in the imported git tags.
    names = [version]
    if version.endswith(".0"):
        names.append(version[:-2])
    pattern = r"(?<![\d.])(?:" + "|".join(map(re.escape, names)) + r")(?![\d.\w-])"
    links = []
    for link in Document(text).root.find("a"):
        title = link.text()
        if (re.search(pattern, title, re.I) and re.search(r"qemu", title, re.I)
                and re.search(r"release|available|announce", title, re.I)
                and not re.search(r"\bRe:|\bFwd?:|\bPATCH\b|\brc\d|candidate", title, re.I)):
            url = urljoin(base, link.attrs.get("href", ""))
            if url.startswith(base) and re.search(r"/msg\d+\.html$", url):
                links.append(url)
    return list(dict.fromkeys(links))


def parse_mail_date(text: str) -> str:
    for row in Document(text).root.find("tr"):
        cells = row.find("td")
        if len(cells) == 2 and cells[0].text().rstrip(": ") == "Date":
            return parsedate_to_datetime(cells[1].text()).date().isoformat()
    raise ValueError("发布公告邮件缺少原始 Date 字段")


def fetch_qemu_announcement(tag: dict, cache: SourceCache) -> dict | None:
    """Use commit month only to locate the original announcement, not as a date."""
    version = tag["name"].removeprefix("v")
    missing_path = cache.directory / f"qemu-announcement-{version}.json"
    previous = _read_json(missing_path, {})
    checked_on = date.today()
    try:
        if 0 <= (checked_on - date.fromisoformat(previous["checked_on"])).days < 30:
            return None
    except (KeyError, TypeError, ValueError):
        pass
    stamp = tag.get("commit", {}).get("committed_date", "")
    if not stamp:
        return None
    year, month = int(stamp[:4]), int(stamp[5:7])
    complete = True
    for offset in (0, -1, 1):
        index = year * 12 + month - 1 + offset
        base = f"https://lists.nongnu.org/archive/html/qemu-devel/{index // 12:04}-{index % 12 + 1:02}/"
        try:
            links = qemu_announcement_links(cache.get(base, immutable=True), base, version)
            for url in links:
                published = parse_mail_date(cache.get(url, immutable=True))
                return _record("qemu", version, published, url)
        except HTTPError as exc:
            # An old archive that never existed is an absence, not an outage.
            if exc.response is None or exc.response.status_code != 404:
                complete = False
            log.debug("QEMU %s 历史公告暂不可用: %s", version, base)
        except Exception:
            complete = False
            log.debug("QEMU %s 历史公告暂不可用: %s", version, base)
    if complete:
        _write_json(missing_path, {"checked_on": checked_on.isoformat()})
    return None


def kernel_versions(refs: dict) -> list[str]:
    """All KVM-era feature releases plus the latest stable tag of each branch."""
    features, maintenance = set(), {}
    for name in refs:
        version = name.removeprefix("refs/tags/").removeprefix("v")
        family = release_family("kvm", version)
        if family is None:
            continue
        if family == version:
            features.add(version)
        elif tuple(map(int, version.split("."))) > tuple(map(int, maintenance.get(family, "0").split("."))):
            maintenance[family] = version
    return sorted(features | {version for family, version in maintenance.items() if family in features},
                  key=lambda version: tuple(map(int, version.split("."))), reverse=True)


def fetch_project(project: str, cache: SourceCache, since_year: int,
                  today: date, existing: dict[str, dict]) -> list[dict]:
    if project in GITLAB_REPOS:
        tags = fetch_gitlab_tags(project, cache)
        records = {row["version"]: row for tag in tags
                   if (row := parse_gitlab_tag(project, tag)) is not None}
        primary = (parse_libvirt(cache.get(LIBVIRT_NEWS)) if project == "libvirt"
                   else parse_qemu_archive(cache.get(QEMU_ARCHIVE)))
        # Announcement date takes precedence over tag creation date.
        records.update({row["version"]: row for row in primary})
        if project == "libvirt":
            return list(records.values())
        missing = [tag for tag in tags if tag["name"][1:] not in records
                   and tag["name"][1:] not in existing
                   and tag.get("commit", {}).get("committed_date", "")[:4] >= str(since_year)]
        with ThreadPoolExecutor(max_workers=4) as pool:
            for row in pool.map(lambda tag: fetch_qemu_announcement(tag, cache), missing):
                if row:
                    records[row["version"]] = row
        for version, row in existing.items():
            records.setdefault(version, dict(row))
        for record in records.values():
            if record["source_url"].split("/")[2] != "www.qemu.org" or record["kind"] != "feature":
                continue
            if int(record["released_on"][:4]) < since_year or record["released_on"] > today.isoformat():
                continue
            previous = existing.get(record["version"], {})
            if previous.get("highlights"):
                record.update({key: previous[key] for key in (
                    "highlights", "commits", "commits_approximate", "contributors"
                ) if key in previous})
            else:
                try:
                    record.update(parse_qemu_notes(cache.get(record["source_url"], immutable=True)))
                except Exception:
                    log.warning("QEMU %s 发布要点暂未取得", record["version"])
        return list(records.values())

    stable_refs = json.loads(cache.get(KERNEL_TAGS).removeprefix(")]}'\n"))
    try:
        refs_text = cache.get(KERNEL_MIRROR + "virt/kvm/kvm/+refs/tags?format=JSON")
        refs = json.loads(refs_text.removeprefix(")]}'\n"))
    except Exception:
        refs = {}
    def read_kernel(version: str) -> dict:
        feature = release_family("kvm", version) == version
        if version in existing:
            record = dict(existing[version])
        else:
            repo = "torvalds" if feature else "stable"
            url = KERNEL_MIRROR + f"linux/kernel/git/{repo}/linux/+/refs/tags/v{version}"
            tag = parse_kernel_tag(cache.get(url, immutable=True), version)
            record = _record("kvm", version, tag["released_on"], url,
                             date_source="release_tag", released_at=tag["released_at"])
        note_ref = _kvm_note_ref(refs, version) if feature else None
        if note_ref and not record.get("highlights") and int(record["released_on"][:4]) >= since_year:
            notes_url = KERNEL_MIRROR + f"virt/kvm/kvm/+/refs/tags/{note_ref}"
            try:
                notes = parse_kvm_notes(cache.get(notes_url, immutable=True))
                if notes:
                    record.update(highlights=notes, notes_url=notes_url)
            except Exception:
                log.warning("KVM / Linux %s 合并说明暂未取得", version)
        return record

    records = ReleaseBatch()
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(read_kernel, version): version for version in kernel_versions(stable_refs)}
        for future in as_completed(futures):
            try:
                records.append(future.result())
            except Exception:
                records.failed_versions.append(futures[future])
                log.warning("Linux %s 发布标签暂未取得，保留已有记录", futures[future])
    if not records:
        raise ValueError("Linux 发布源未返回正式版本")
    return records


class ReleaseBatch(list):
    """Keep partial successes publishable without hiding failed tag lookups."""

    def __init__(self):
        super().__init__()
        self.failed_versions: list[str] = []


def load_content(config: Config, *, today: date | None = None) -> dict:
    """Read bundled and local snapshots only; usable in an empty deployment."""
    today = today or date.today()
    bundled = _read_json(CONTENT_DIR / "versions.json", {})
    local = _read_json(config.db_path.parent / "versions.json", {})
    rows = {}
    for snapshot in (bundled, local):
        for row in snapshot.get("releases", []):
            if isinstance(row, dict) and _valid(row, today):
                rows[(row["project"], row["version"])] = row
    notes = _read_json(CONTENT_DIR / "version_notes.json", {})
    releases = []
    for row in rows.values():
        entry = dict(row, project_label=PROJECTS[row["project"]],
                     year=row["released_on"][:4])
        reviewed = notes.get(f'{row["project"]}:{row["version"]}', {})
        entry["notes"] = reviewed.get("highlights", [])
        entry["note_source_url"] = reviewed.get("source_url") or row.get("notes_url") or row["source_url"]
        releases.append(entry)
    releases.sort(key=lambda row: (row["released_on"], row["project"],
                                   tuple(map(int, row["version"].split(".")))), reverse=True)
    return {"releases": releases, "projects": PROJECTS,
            "years": sorted({row["year"] for row in releases}, reverse=True),
            "default_year": str(today.year),
            "checked_at": local.get("checked_at") or bundled.get("checked_at", ""),
            "sources": {**bundled.get("sources", {}), **local.get("sources", {})}}


def export_public_snapshot(config: Config, *, today: date | None = None) -> Path:
    """Explicit maintainer export; exclude runtime status and view-only fields."""
    content = load_content(config, today=today)
    fields = {"project", "version", "released_on", "released_at", "kind",
              "source_url", "date_source", "highlights", "notes_url",
              "commits", "commits_approximate", "contributors"}
    releases = [{key: value for key, value in row.items() if key in fields}
                for row in content["releases"]]
    path = CONTENT_DIR / "versions.json"
    _write_json(path, {"schema_version": 1, "checked_at": content["checked_at"],
                       "releases": releases})
    return path


def refresh(config: Config, *, from_year: int = 2003,
            today: date | None = None) -> dict:
    """Refresh independent sources, preserve history and atomically publish."""
    today = today or date.today()
    if not 2003 <= from_year <= today.year:
        raise ValueError(f"起始年份应在 2003—{today.year} 之间")
    directory = config.db_path.parent
    with process_lock(directory / "versions.lock"):
        previous = load_content(config, today=today)
        # Strip view-only fields when persisting source records.
        fields = {"project_label", "year", "notes", "note_source_url"}
        rows = {(row["project"], row["version"]):
                {key: value for key, value in row.items() if key not in fields}
                for row in previous["releases"]}
        status = previous["sources"].copy()
        cache = SourceCache(directory / "versions" / "http")
        checked_at = datetime.now(timezone.utc).isoformat()
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(fetch_project, project, cache, from_year, today,
                                   {version: row for (name, version), row in rows.items()
                                    if name == project}): project for project in PROJECTS}
            for future in as_completed(futures):
                project = futures[future]
                try:
                    result = future.result()
                    failed_versions = getattr(result, "failed_versions", [])
                    fetched = [row for row in result if _valid(row, today)
                               and int(row["released_on"][:4]) >= from_year]
                    if not fetched:
                        raise ValueError("本次未取得有效发布记录")
                    for row in fetched:
                        key = (project, row["version"])
                        rows[key] = {**rows.get(key, {}), **row}
                    status[project] = {"ok": not failed_versions, "checked_at": checked_at,
                                       "count": len(fetched), "failed_versions": failed_versions}
                    if failed_versions:
                        status[project]["error"] = f"{len(failed_versions)} 个发布标签暂未取得"
                    else:
                        status[project]["last_success_at"] = checked_at
                    log.info("%s 已核验 %d 条版本记录", PROJECTS[project], len(fetched))
                except Exception as exc:
                    status[project] = {**status.get(project, {}), "ok": False,
                                       "checked_at": checked_at, "error": str(exc)[:300]}
                    log.warning("%s 版本更新失败，保留已有记录: %s", PROJECTS[project], exc)
        snapshot = {"schema": 1, "checked_at": checked_at, "sources": status,
                    "releases": sorted(rows.values(), key=lambda row: (
                        row["released_on"], row["project"], row["version"]), reverse=True)}
        _write_json(directory / "versions.json", snapshot)
        return snapshot
