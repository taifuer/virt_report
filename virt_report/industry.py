"""Reviewed industry briefs, separate from community reports and collection drafts."""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from virt_report.config import Config
from virt_report.locking import process_lock

BUNDLED = Path(__file__).parent / "content" / "industry"
BEIJING = ZoneInfo("Asia/Shanghai")
WEEK = re.compile(r"([0-9]{4})-W([0-9]{2})")
KEY = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
MAX_POINTS = 10


def week_dates(week: str) -> tuple[date, date]:
    """Inclusive Beijing calendar dates, including ISO year boundaries."""
    match = WEEK.fullmatch(week)
    if not match:
        raise ValueError("期号应为 YYYY-Www")
    monday = date.fromisocalendar(int(match[1]), int(match[2]), 1)
    return monday, monday + timedelta(days=6)


def previous_week(now: datetime | None = None) -> str:
    today = (now or datetime.now(BEIJING)).astimezone(BEIJING).date()
    year, week, _ = (today - timedelta(days=7)).isocalendar()
    return f"{year:04d}-W{week:02d}"


def store_dir(config: Config) -> Path:
    return config.db_path.parent / "industry"


def write_text(path: Path, text: str) -> None:
    """Replace a single file atomically so readers never see half an issue."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        tmp.write(text)
    temp = Path(tmp.name)
    try:
        temp.chmod(0o644)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def write_json(path: Path, content: dict) -> None:
    write_text(path, json.dumps(content, ensure_ascii=False, indent=2) + "\n")


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"缺少 {field}")
    return value.strip()


def _date(value: object) -> date:
    text = _text(value, "日期")
    parsed = date.fromisoformat(text)
    if parsed.isoformat() != text:
        raise ValueError("日期应为 YYYY-MM-DD")
    return parsed


def source_url(value: object) -> str:
    url = _text(value, "原始来源链接")
    parts = urlsplit(url)
    if (parts.scheme != "https" or not parts.hostname or parts.username is not None
            or parts.password is not None or any(c.isspace() or c == "\\" for c in url)):
        raise ValueError("原始来源必须是 HTTPS 网页")
    return url


def prepare(issue: dict) -> dict:
    """Validate dates, per-claim citations and event identity; number in reading order."""
    if not isinstance(issue, dict):
        raise ValueError("产业周报须为对象")
    week = _text(issue.get("week"), "期号")
    start, end = week_dates(week)
    published = _date(issue.get("published_on"))
    if published <= end:
        raise ValueError("发布日须在本期结束之后")
    points, sources = issue.get("points"), issue.get("sources")
    if not isinstance(points, list) or len(points) > MAX_POINTS or not isinstance(sources, dict):
        raise ValueError("每期最多 10 条，并须提供来源表")
    lead = _text(issue.get("lead"), "本周概括")
    numbers, events, urls = {}, set(), set()
    references, items = [], []

    def citations(ids: list) -> list[dict]:
        if not isinstance(ids, list) or any(not isinstance(k, str) for k in ids) or len(set(ids)) != len(ids):
            raise ValueError("来源编号须为不重复的列表")
        refs = []
        for key in ids:
            if key not in sources or not isinstance(sources[key], dict):
                raise ValueError(f"来源不存在：{key}")
            if key not in numbers:
                s = sources[key]
                url = source_url(s.get("url"))
                if url in urls or _date(s.get("date")) > published:
                    raise ValueError("来源链接重复或晚于出版日")
                urls.add(url)
                ref = dict(number=len(references)+1, url=url, title=_text(s.get("title"), "来源标题"),
                           publisher=_text(s.get("publisher"), "发布方"), date=s["date"])
                numbers[key] = ref
                references.append(ref)
            refs.append(numbers[key])
        return refs

    for p in points:
        if not isinstance(p, dict):
            raise ValueError("要点须为对象")
        event = _text(p.get("event"), "事件标识")
        if not KEY.fullmatch(event) or event in events or not start <= _date(p.get("date")) <= end:
            raise ValueError(f"事件标识重复、格式错误或日期不在本期：{event}")
        events.add(event)
        refs = citations(p.get("refs"))
        paragraphs = p.get("paragraphs")
        if not refs or not isinstance(paragraphs, list) or not paragraphs:
            raise ValueError("每条至少需要一个标题来源和一段正文")
        body = []
        for part in paragraphs:
            if not isinstance(part, dict):
                raise ValueError("正文段落须为对象")
            body.append(dict(text=_text(part.get("text"), "正文"), refs=citations(part.get("refs", []))))
        items.append(dict(event=event, title=_text(p.get("title"), "要点标题"),
                          date=p["date"], refs=refs, paragraphs=body))
    if set(sources) != set(numbers):
        raise ValueError("来源表包含未引用资料")
    return dict(week=week, start=start.isoformat(), end=end.isoformat(), published_on=published.isoformat(),
                lead=lead, points=items, sources=references)


def _files(config: Config) -> dict[str, Path]:
    found = {}
    for directory in (BUNDLED, store_dir(config) / "published"):
        found.update((p.stem, p) for p in directory.glob("*.json") if WEEK.fullmatch(p.stem))
    return found


def _read(path: Path) -> dict:
    issue = prepare(json.loads(path.read_text(encoding="utf-8")))
    if issue["week"] != path.stem:
        raise ValueError(f"文件名与期号不符：{path.name}")
    return issue


def edition_page(config: Config, week: str | None = None, *,
                 editions: list[dict] | None = None) -> tuple[dict | None, str | None, str | None]:
    """Return the issue and its published neighbours, reading at most one issue file."""
    index = _files(config) if editions is None else {ed["week"]: ed for ed in editions}
    key = week or max(index, default="")
    if key not in index:
        return None, None, None
    previous = max((w for w in index if w < key), default=None)
    following = min((w for w in index if w > key), default=None)
    issue = _read(index[key]) if editions is None else index[key]
    return issue, previous, following


def get(config: Config, week: str | None = None) -> dict | None:
    """Read one published issue, or the latest."""
    return edition_page(config, week)[0]


def issues(config: Config) -> list[dict]:
    """Read bundled editions and published runtime issues, newest first; exclude drafts."""
    files = _files(config)
    return [_read(files[key]) for key in sorted(files, reverse=True)]


def publish(config: Config, raw: dict) -> str:
    """Publish a human-approved issue once; a correction must be reviewed separately."""
    issue = prepare(raw)
    if issue["published_on"] > datetime.now(BEIJING).date().isoformat():
        raise ValueError("发布日尚未到")
    key = issue["week"]
    path = store_dir(config) / "published" / f"{key}.json"
    with process_lock(store_dir(config) / "publish.lock"):
        for other in issues(config):
            if other["week"] == key:
                if issue == other:
                    return key
                raise ValueError("本期已发布，不能覆盖已审正文")
            if {p["event"] for p in issue["points"]} & {p["event"] for p in other["points"]}:
                raise ValueError("往期已报道同一事件；后续进展应标明新的里程碑")
        write_json(path, {k: raw[k] for k in ("week", "published_on", "lead", "points", "sources")})
    return key
