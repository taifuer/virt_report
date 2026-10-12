"""Conservative series metadata derived from source mail, not model guesses."""
from __future__ import annotations

import re
import sqlite3
from email.utils import parseaddr


def series_identity(subject: str) -> tuple[str, int] | None:
    """Only compare cover letters or single patches with the same exact topic."""
    # Replies are activity on a version, never proof that a new version was sent.
    if re.match(r"\s*(?:re|fwd?):", subject, re.I):
        return None
    match = re.match(r"^\s*\[([^\]]+)\]\s*(.+)$", subject)
    if not match or not re.search(r"\b(?:PATCH|RFC)\b", match[1], re.I):
        return None
    part = re.search(r"\b(\d+)/(\d+)\b", match[1])
    if part and int(part[1]) != 0:
        return None
    version = re.search(r"\bv(\d+)\b", match[1], re.I)
    topic = re.sub(r"\s+", " ", match[2].strip().casefold())
    return topic, int(version[1]) if version else 1


def enrich_series(conn: sqlite3.Connection, evidence: list[dict], end: str) -> None:
    """Attach latest *known by period end* versions without importing future mail."""
    identities = {}
    for card in evidence:
        subject = re.sub(r"^(?:Re:\s*)+", "", card.get("subject", ""), flags=re.I)
        identity = series_identity(subject)
        if identity and card.get("source") == "ml":
            identities.setdefault((card["project"], identity[0]), []).append(card)
            card["series_version"] = identity[1]
            card["latest_known_version"] = identity[1]
            card["latest_version_url"] = card.get("url", "")
    if not identities:
        return
    projects = sorted({project for project, _ in identities})
    placeholders = ",".join("?" for _ in projects)
    candidates = conn.execute(
        "SELECT project,subject,author,url,created_at FROM items "
        f"WHERE source='ml' AND project IN ({placeholders}) "
        "AND created_at<? AND (subject LIKE '[PATCH%' OR subject LIKE '[RFC%') "
        "ORDER BY created_at,url", (*projects, end),
    )
    for row in candidates:
        identity = series_identity(row["subject"] or "")
        if not identity:
            continue
        for card in identities.get((row["project"], identity[0]), []):
            original_author = parseaddr(card.get("author", ""))[1].casefold()
            candidate_author = parseaddr(row["author"] or "")[1].casefold()
            if original_author and candidate_author and original_author != candidate_author:
                continue
            if identity[1] > card["latest_known_version"]:
                card["latest_known_version"] = identity[1]
                card["latest_version_url"] = row["url"] or ""
    for cards in identities.values():
        for card in cards:
            active, latest = card["series_version"], card["latest_known_version"]
            if latest > active:
                card["series_note"] = (
                    f"本期评审涉及 v{active}；截至本期末，已知最新系列为 v{latest}。"
                )


def consistency_error(item: dict, source: dict) -> str | None:
    """Catch narrow, evidence-checkable contradictions; not a semantic fact checker."""
    state = source.get("state", "").casefold()
    text = " ".join(item.get(key, "") or "" for key in ("title", "summary", "impact"))
    claimed_status = item.get("status", "") or ""

    def claims(pattern: str) -> bool:
        for match in re.finditer(pattern, text):
            prefix = text[max(0, match.start() - 14):match.start()]
            if not re.search(r"(?:未|不|没有|是否|若|如果)[^。；，,]{0,8}$", prefix):
                return True
        return False

    def current_claim(action: str) -> bool:
        # Do not equate background phrases ("影响已发布版本") or a patch
        # revision announcement ("作者已发布 v15") with this item's completion.
        pattern = (
            r"(?:^|[。！？；;\n])\s*"
            r"(?:(?:该|本|此)(?:补丁|特性|系列|变更|修复|问题|功能))?"
            rf"(?:已经|已)(?:正式)?(?:{action})(?!的|代码|版本|\s*[vV]\d)"
        )
        return any(re.search(pattern, item.get(field, "") or "")
                   for field in ("title", "summary", "impact"))

    if state != "merged" and (
        claimed_status in {"已合并", "已合入"}
        or current_claim("合入|合并")
    ):
        return "unverified_completion"
    # Closing an issue proves neither a fix nor a shipped release. A merged MR
    # also does not establish which public release contains it.
    if (claimed_status in {"已修复", "已发布", "可用"}
            or current_claim("修复|发布|上线")):
        return "unverified_completion"
    if claims(r"可(?:以)?(?:直接|立即)(?:使用|启用|部署)"):
        if not re.search(r"(?:合入|合并|发布|版本要求|条件).{0,18}(?:后|才|时)", text):
            return "unconditional_availability"
    active = source.get("series_version", 0)
    latest = source.get("latest_known_version", active)
    if active and latest > active:
        # Reject describing the older active revision as the latest advance.
        old = rf"v{active}(?!\d)"
        title = item.get("title", "") or ""
        if (re.search(rf"(?:推进|更新|迭代|升级|演进)(?:到|至|为)\s*{old}", title, re.I)
                or re.search(rf"最新(?:版本|系列|修订)(?:为|是|已到|为止)?\s*{old}", text, re.I)):
            return "stale_series_version"
    return None
