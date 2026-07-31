"""基于原始线程的增量专题索引。"""
from __future__ import annotations

import json
import math
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

from virt_report import db
from . import architecture, category


RULE_VERSION = 7
TOPIC_RULES = (
    ("security", "安全与漏洞", "明确 CVE 与具有强原始证据的安全缺陷；编号和类型仅依据原始内容", ()),
    ("migration", "热迁移", "迁移链路、停机窗口、脏页收敛与跨主机兼容性", (
        "热迁移", "migration", "migrate", "multifd", "postcopy", "precopy",
        "switchover", "dirty page", "live-migration",
    )),
    ("live-upgrade", "热升级", "运行中软件更新、在线维护与服务连续性", (
        "热升级", "热更新", "live update", "live-update", "runtime update",
        "livepatch", "online update", "live upgrade", "live-upgrade",
    )),
    ("hotplug", "热插拔", "运行中增减 CPU、内存与设备的能力", (
        "热插拔", "hotplug", "hot-plug", "hot unplug", "hot-unplug",
        "vcpu unplug", "vcpu hotplug", "memory unplug", "memory hotplug",
        "device unplug", "device hotplug",
    )),
    ("lifecycle", "启动与生命周期", "启动、关机、重启、暂停恢复与生命周期可靠性", (
        "启动", "关机", "重启", "startup", "boot", "reboot", "shutdown", "reset",
        "suspend", "resume", "lifecycle",
    )),
    ("performance", "虚机性能", "时延、吞吐、资源开销与硬件加速优化", (
        "性能", "performance", "optimize", "optimization", "latency", "throughput",
        "acceleration", "accelerate", "scalability", "benchmark", "overhead",
        "fast path", "fast-path", "zero-copy", "ioeventfd", "pml", "tph",
    )),
)

_CVE_RE = re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.IGNORECASE)
_SECURITY_DEFECT_RE = re.compile(
    r"\b(?:use-after-free|uaf|out-of-bounds|buffer overflow|heap overflow|"
    r"integer overflow|double free|denial of service|privilege escalation|"
    r"information leak|memory corruption|security (?:bug|issue|fix)|dos)\b|"
    r"越界|释放后使用|拒绝服务|提权|信息泄漏|内存损坏|安全漏洞",
    re.IGNORECASE,
)
_SECURITY_ENHANCEMENT_RE = re.compile(
    r"\b(?:sev(?:-snp)?|tdx|arm cca|confidential computing|confidential vm|"
    r"secure virtualization|memory encryption|realm management monitor|rmm)\b|"
    r"机密计算|机密虚拟机|内存加密|安全增强",
    re.IGNORECASE,
)
_PROJECT_LABELS = {
    "qemu-devel": "QEMU", "qemu": "QEMU", "kvm": "KVM",
    "libvir-list": "Libvirt", "libvirt": "Libvirt",
}
_SECURITY_LABELS = {
    "cve": "明确 CVE", "defect": "安全缺陷", "enhancement": "安全增强",
}
_EXPLICIT_SECURITY_LABEL_RE = re.compile(
    r"^(?:security(?:::.+)?|vulnerability|cve)$", re.IGNORECASE,
)
_SECURITY_TECH_EXPLANATIONS = (
    (("arm_rmm", "arm rmm", "arm cca", "realm management monitor"),
     "Arm CCA 的 RMM 固件接口与主机支持"),
    (("sev-snp", "sev/snp", "snp"), "AMD SEV-SNP 机密虚拟化能力"),
    (("sev",), "AMD SEV 加密虚拟化能力"),
    (("tdx",), "Intel TDX 机密虚拟化能力"),
    (("confidential computing", "confidential vm"), "机密计算与机密虚机能力"),
    (("memory encryption",), "虚机内存加密能力"),
)
_SECURITY_RISK_EXPLANATIONS = (
    (("use-after-free", "uaf"), "释放后使用"),
    (("out-of-bounds", " oob", "越界"), "越界访问"),
    (("buffer overflow", "heap overflow", "integer overflow", "溢出"), "溢出"),
    (("denial of service", " dos", "拒绝服务"), "拒绝服务"),
    (("privilege escalation", "提权"), "权限提升"),
    (("information leak", "信息泄漏"), "信息泄漏"),
    (("memory corruption", "内存损坏"), "内存损坏"),
)
_BOARD_BOOT_RE = re.compile(
    r"\b(?:soc|cortex|firmware|board|machine model)\b.*\bboot\b|"
    r"\bboot\b.*\b(?:soc|cortex|firmware|board|machine model)\b",
    re.IGNORECASE,
)
_VIRTUAL_LIFECYCLE_RE = re.compile(
    r"\b(?:guest|vm|virtual machine|domain|qemu|kvm|libvirt|acpi|sev|tdx)\b",
    re.IGNORECASE,
)
_LOW_INFORMATION_SUMMARY_RE = re.compile(
    r"^(?:v\d+\s*)?(?:系列)?(?:已获|由).*(?:reviewed-by|acked-by|取代|重发|审核|评审)",
    re.IGNORECASE,
)


def _security_evidence(title_text: str, cve_text: str,
                       labels: list[str]) -> tuple[str | None, list[str]]:
    """依据原始标题和明确标签识别安全类型，避免正文或回复造成误判。"""
    cve_ids = sorted({match.upper() for match in _CVE_RE.findall(cve_text)})
    if cve_ids:
        return "cve", cve_ids
    if any(_EXPLICIT_SECURITY_LABEL_RE.match(label.strip()) for label in labels):
        return "defect", []
    if _SECURITY_DEFECT_RE.search(title_text):
        return "defect", []
    if _SECURITY_ENHANCEMENT_RE.search(title_text):
        return "enhancement", []
    return None, []


def classify_item(item: dict) -> list[str]:
    """返回条目命中的专题键；安全类型可由调用方预先提供。"""
    text = " ".join(
        " ".join(str(item.get(field, "")).split())
        for field in ("title", "original_title", "tag")
    ).lower()
    keys = []
    if item.get("security_type"):
        keys.append("security")
    for key, _name, _description, words in TOPIC_RULES:
        if key == "security" or not any(word in text for word in words):
            continue
        # 裸机板卡/SoC 固件启动不等同于虚机启动与生命周期。
        if (key == "lifecycle" and _BOARD_BOOT_RE.search(text)
                and not _VIRTUAL_LIFECYCLE_RE.search(text)):
            continue
        keys.append(key)
    return keys


def _status(item: sqlite3.Row, raw: dict, title: str) -> str:
    if raw.get("merged"):
        return "已合并"
    state = str(raw.get("state") or "").lower()
    if state in {"closed", "merged"}:
        return "已关闭" if state == "closed" else "已合并"
    if "stable" in title.lower() or "backport" in title.lower() or "回传" in title:
        return "稳定版回传"
    if state in {"opened", "open"}:
        return "处理中"
    if item["kind"] in {"patch", "rfc"}:
        return "评审中"
    return "持续跟踪"


def _fallback_summary(item: dict, topic_name: str) -> str:
    """为尚无 AI 报告摘要的条目生成克制的中文规则说明。"""
    project = item.get("project") or "上游"
    status = item.get("status") or "持续跟踪"
    raw_title = item.get("title") or ""
    title = raw_title.lower()
    core_title = re.sub(
        r"^(?:\[[^\]]*(?:patch|rfc|resend|stable)[^\]]*\]\s*)+", "",
        raw_title, flags=re.I,
    ).strip()
    title_parts = [part.strip() for part in core_title.split(":")]
    component = title_parts[0] if len(title_parts) > 1 else ""
    if component.lower() == project.lower():
        component = title_parts[1] if len(title_parts) > 2 else ""
    security_type = item.get("security_type")
    if security_type == "cve":
        cves = "、".join(item.get("cve_ids") or [])
        risk = next((label for words, label in _SECURITY_RISK_EXPLANATIONS
                     if any(word in title for word in words)), "安全问题")
        location = f"{component} 中" if component else ""
        trigger = "可由 guest 触发的" if "guest-trigger" in title else ""
        return (f"{project} 正在修复 {location}{trigger}{risk}（{cves}），"
                f"当前状态为{status}；影响范围以原始线程和上游公告为准。")
    if security_type == "defect":
        risk = next((label for words, label in _SECURITY_RISK_EXPLANATIONS
                     if any(word in title for word in words)), "潜在安全问题")
        location = f"{component} 在相关操作中" if component else "该议题"
        trigger = "存在可由 guest 触发的" if "guest-trigger" in title else "涉及"
        return (f"{location}{trigger}{risk}，当前状态为{status}；"
                "触发条件与影响边界以原始页面为准。")
    if security_type == "enhancement":
        tech = next((label for words, label in _SECURITY_TECH_EXPLANATIONS
                     if any(word in title for word in words)), "虚拟化安全能力增强")
        return f"{project} 社区正在讨论“{tech}”，归入安全增强；当前状态为{status}。"
    if "hardening fixes" in title:
        return (f"该补丁系列集中修正{topic_name}链路的健壮性问题，当前状态为{status}；"
                "具体修复项请查看原始线程。")
    display_title = core_title[:100] or raw_title[:100]
    return (f"{project} 正在讨论“{display_title}”相关改动，当前状态为{status}；"
            "具体技术范围请查看原始线程。")


def _thread_entry(conn: sqlite3.Connection, thread: sqlite3.Row,
                  prefetched: dict | None = None) -> dict | None:
    root = thread["thread_key"].split(":", 2)[2]
    group_key = (thread["source"], thread["project"], root)
    rows = prefetched.get(group_key, []) if prefetched is not None else conn.execute(
        "SELECT * FROM items WHERE source=? AND project=? AND thread_root=? "
        "ORDER BY activity_at DESC", group_key,
    ).fetchall()
    if not rows:
        return None
    latest = rows[0]
    primary = next(
        (item for item in rows
         if item["native_id"] == root or item["message_id"] == root),
        rows[-1],
    )
    labels = []
    raw = {}
    text_parts = [thread["subject"] or "", thread["topic_tag"] or ""]
    for item in rows:
        try:
            item_labels = json.loads(item["labels"] or "[]")
        except (TypeError, ValueError):
            item_labels = []
        labels.extend(str(label) for label in item_labels)
        text_parts.extend((item["subject"] or "", item["body_excerpt"] or ""))
    try:
        raw = json.loads(latest["raw_json"] or "{}")
    except (TypeError, ValueError):
        raw = {}
    title = thread["subject"] or latest["subject"] or "未命名讨论"
    evidence_text = " ".join(text_parts)
    title_text = " ".join((title, primary["subject"] or ""))
    security_type, cve_ids = _security_evidence(title_text, evidence_text, labels)
    summary = primary["body_excerpt"] or ""
    summary = " ".join((summary or "").split())[:360]
    return {
        "thread_key": thread["thread_key"], "project": thread["project"],
        "source": thread["source"], "title": title, "url": thread["url"] or latest["url"],
        "summary": summary, "activity_at": thread["last_seen"],
        "category": category.classify_change(thread["kind"], title),
        "architectures": architecture.detect_architectures([
            title, " ".join(labels), evidence_text[:1200],
        ]),
        "cve_ids": cve_ids, "security_type": security_type,
        "status": _status(latest, raw, title), "body_excerpt": primary["body_excerpt"] or "",
    }


def sync_topic_index(conn: sqlite3.Connection, *, force: bool = False) -> int:
    """增量物化发生变化的原始线程，返回本次重建线程数。"""
    if force:
        with db.transaction(conn):
            conn.execute("DELETE FROM topic_entries")
            conn.execute("DELETE FROM topic_indexed_threads")
    changed = conn.execute(
        "SELECT t.* FROM threads t LEFT JOIN topic_indexed_threads x "
        "ON x.thread_key=t.thread_key WHERE x.thread_key IS NULL "
        "OR x.indexed_last_seen<>t.last_seen OR x.rule_version<>?",
        (RULE_VERSION,),
    ).fetchall()
    prefetched = None
    if len(changed) > 50:
        prefetched = defaultdict(list)
        for item in conn.execute(
            "SELECT * FROM items WHERE thread_root IS NOT NULL ORDER BY activity_at DESC"
        ):
            prefetched[(item["source"], item["project"], item["thread_root"])].append(item)
    with db.transaction(conn):
        for thread in changed:
            conn.execute("DELETE FROM topic_entries WHERE thread_key=?", (thread["thread_key"],))
            entry = _thread_entry(conn, thread, prefetched)
            if entry:
                for topic_key in classify_item(entry):
                    conn.execute(
                        "INSERT OR REPLACE INTO topic_entries "
                        "(topic_key,thread_key,project,source,title,url,summary,activity_at,"
                        "category,architectures,cve_ids,security_type,status) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (topic_key, entry["thread_key"], entry["project"], entry["source"],
                         entry["title"], entry["url"], entry["summary"], entry["activity_at"],
                         entry["category"], json.dumps(entry["architectures"], ensure_ascii=False),
                         json.dumps(entry["cve_ids"]), entry["security_type"], entry["status"]),
                    )
            conn.execute(
                "INSERT OR REPLACE INTO topic_indexed_threads "
                "(thread_key,indexed_last_seen,rule_version) VALUES (?,?,?)",
                (thread["thread_key"], thread["last_seen"], RULE_VERSION),
            )
        conn.execute("DELETE FROM topic_entries WHERE thread_key NOT IN (SELECT thread_key FROM threads)")
        conn.execute("DELETE FROM topic_indexed_threads WHERE thread_key NOT IN (SELECT thread_key FROM threads)")
    return len(changed)


def _report_mentions(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    """Return report evidence grouped by canonical source URL."""
    mentions: dict[str, list[dict]] = defaultdict(list)
    rows = conn.execute(
        "SELECT period,period_key,generated_at,content_json FROM reports ORDER BY "
        "CASE period WHEN 'daily' THEN 0 WHEN 'weekly' THEN 1 ELSE 2 END, period_key DESC"
    ).fetchall()
    for row in rows:
        try:
            content = json.loads(row["content_json"])
        except (TypeError, ValueError):
            continue
        for section in content.get("sections", []):
            for item in section.get("items", []):
                url = item.get("url")
                if not url:
                    continue
                mentions[url].append({
                    "period": row["period"], "period_key": row["period_key"],
                    "generated_at": row["generated_at"],
                    "url": url,
                    "summary": item.get("summary", "") or "",
                    "impact": item.get("impact", "") or "",
                })
    for values in mentions.values():
        values.sort(key=lambda item: item["generated_at"], reverse=True)
    return mentions


def _topic_definition(topic_key: str) -> tuple[str, str] | None:
    return next(((name, description) for key, name, description, _ in TOPIC_RULES
                 if key == topic_key), None)


def _canonical_title(item: dict) -> str:
    if item.get("cve_ids"):
        return "cve:" + ",".join(item["cve_ids"])
    title = (item.get("title") or "").lower()
    title = re.sub(r"^(?:re:\s*)+", "", title)
    title = re.sub(
        r"^(?:\[[^\]]*(?:patch|rfc|resend|stable)[^\]]*\]\s*)+", "", title,
    )
    title = re.sub(r"\bv\d+\b|\b\d+/\d+\b", "", title)
    return re.sub(r"\W+", " ", title).strip()


def _priority(item: dict, reference: datetime) -> tuple[float, list[str]]:
    score = float(item.get("salience_score") or 0)
    reasons = []
    if item.get("curated_mentions"):
        score += 35
        reasons.append("周月报精选")
    elif item.get("daily_mentions"):
        score += 18
        reasons.append("日报观察")
    security_type = item.get("security_type")
    if security_type == "cve":
        title = (item.get("title") or "").upper()
        direct_cve = any(cve in title for cve in item.get("cve_ids") or [])
        score += 45 if direct_cve else 25
        reasons.append("CVE" if direct_cve else "含 CVE")
        years = [int(cve.split("-")[1]) for cve in item.get("cve_ids") or []]
        if years and max(years) < reference.year - 2:
            score -= 30
    elif security_type == "defect":
        score += 28
        reasons.append("安全缺陷")
    elif security_type == "enhancement":
        score += 12
    if item.get("category") == "bug":
        score += 12
    elif item.get("category") == "feature":
        score += 5
    focus_arch = {"x86", "ARM"}.intersection(item.get("architectures") or [])
    if focus_arch:
        score += 10 * len(focus_arch)
        reasons.append("架构重点")
    if item.get("status") in {"处理中", "评审中"}:
        score += 8
    elif item.get("status") == "已关闭":
        score -= 8
    try:
        activity = datetime.fromisoformat(item["activity_at"].replace("Z", "+00:00"))
        age = max(0, (reference - activity).days)
        score += 16 if age <= 2 else 10 if age <= 7 else 4 if age <= 30 else 0
    except (AttributeError, TypeError, ValueError):
        pass
    return round(score, 2), reasons


def _summary_is_usable(summary: str) -> bool:
    summary = " ".join((summary or "").split())
    return len(summary) >= 12 and not _LOW_INFORMATION_SUMMARY_RE.search(summary)


def _apply_report_evidence(item: dict, name: str, mentions: list[dict]) -> None:
    mentions.sort(key=lambda value: value.get("generated_at") or "", reverse=True)
    curated = [mention for mention in mentions
               if mention["period"] in {"weekly", "monthly"}]
    daily = [mention for mention in mentions if mention["period"] == "daily"]
    current = [mention for mention in mentions
               if mention.get("url") == item.get("url")]
    preferred = next(
        (mention for mention in (current + curated + daily)
         if _summary_is_usable(mention.get("summary", ""))),
        None,
    )
    item["report_mentions"] = mentions
    item["curated_mentions"] = curated
    item["daily_mentions"] = daily
    item["summary_source"] = preferred["period"] if preferred else "rule"
    item["summary"] = preferred["summary"] if preferred else _fallback_summary(item, name)
    item["impact"] = preferred.get("impact", "") if preferred else ""
    if curated:
        periods = {mention["period"] for mention in curated}
        item["scope"] = "curated"
        item["scope_label"] = "月报汇总" if "monthly" in periods else "周报汇总"
        item["report_keys"] = list(dict.fromkeys(
            mention["period_key"] for mention in curated
        ))[:3]
    else:
        item["scope"] = "candidate"
        item["scope_label"] = ""
        item["report_keys"] = []


def _same_series(left: str, right: str) -> bool:
    if not left or not right:
        return False
    if left == right:
        return True
    if left.startswith("cve:") or right.startswith("cve:"):
        return False
    if min(len(left), len(right)) < 18:
        return False
    ratio = SequenceMatcher(None, left, right).ratio()
    left_tokens = {token[:-1] if len(token) > 4 and token.endswith("s") else token
                   for token in left.split()}
    right_tokens = {token[:-1] if len(token) > 4 and token.endswith("s") else token
                    for token in right.split()}
    overlap = len(left_tokens & right_tokens) / max(1, min(len(left_tokens), len(right_tokens)))
    return ratio >= 0.84 or (overlap >= 0.88 and len(left_tokens & right_tokens) >= 4)


def _series_version(title: str) -> int:
    match = re.match(r"^\[[^\]]*\bv(\d+)\b", title or "", re.IGNORECASE)
    return int(match.group(1)) if match else 0


def _merge_series(items: list[dict], name: str) -> list[dict]:
    """合并同一补丁系列的跨版本线程，并把旧版本报告证据带到最新版本。"""
    grouped: list[tuple[str, str, list[dict]]] = []
    ordered = sorted(items, key=lambda item: item.get("activity_at") or "", reverse=True)
    for item in ordered:
        canonical = _canonical_title(item) or item["thread_key"]
        project = item.get("project") or ""
        match = next(
            (group for group in grouped
             if group[1] == project and _same_series(canonical, group[0])),
            None,
        )
        if match:
            match[2].append(item)
        else:
            grouped.append((canonical, project, [item]))

    merged = []
    for _canonical, _project, members in grouped:
        representative = max(
            members,
            key=lambda member: (
                _series_version(member.get("title") or ""),
                member.get("activity_at") or "",
            ),
        )
        latest = dict(representative)
        latest["series_count"] = len(members)
        latest["series_version"] = max(
            (_series_version(member.get("title") or "") for member in members),
            default=0,
        )
        latest["series_titles"] = [member["title"] for member in members]
        latest["activity_at"] = max(
            (member.get("activity_at") or "" for member in members),
            default=latest.get("activity_at") or "",
        )
        latest["time"] = latest["activity_at"][:10]
        latest["salience_score"] = max(
            float(member.get("salience_score") or 0) for member in members
        )
        latest["architectures"] = list(dict.fromkeys(
            architecture_name
            for member in members
            for architecture_name in member.get("architectures", [])
        ))
        latest["cve_ids"] = list(dict.fromkeys(
            cve for member in members for cve in member.get("cve_ids", [])
        ))
        mentions = []
        seen_mentions = set()
        for member in members:
            for mention in member.get("report_mentions", []):
                key = (mention.get("period"), mention.get("period_key"),
                       mention.get("generated_at"), mention.get("summary"))
                if key not in seen_mentions:
                    seen_mentions.add(key)
                    mentions.append(mention)
        _apply_report_evidence(latest, name, mentions)
        mentioned_versions = [
            int(value) for value in re.findall(
                r"\bv(\d+)(?!\d)", latest.get("summary") or "", re.IGNORECASE,
            )
        ]
        if (latest["series_version"] > 1 and mentioned_versions
                and max(mentioned_versions) < latest["series_version"]):
            latest["summary"] = re.sub(
                r"\bv\d+(?!\d)\s*", "此前版本", latest["summary"],
                flags=re.IGNORECASE,
            ).rstrip("。； ")
            latest["summary"] += (
                f"；当前已迭代至 v{latest['series_version']}，"
                "最新变化以原始线程为准。"
            )
        merged.append(latest)
    return merged


def _load_topic_items(conn: sqlite3.Connection, topic_key: str, name: str,
                      report_mentions: dict[str, list[dict]]) -> list[dict]:
    rows = conn.execute(
        "SELECT e.*,t.salience_score FROM topic_entries e LEFT JOIN threads t "
        "ON t.thread_key=e.thread_key WHERE e.topic_key=?", (topic_key,),
    ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["project"] = _PROJECT_LABELS.get(item["project"], item["project"])
        item["architectures"] = json.loads(item["architectures"] or "[]")
        item["cve_ids"] = json.loads(item["cve_ids"] or "[]")
        item["security_label"] = _SECURITY_LABELS.get(item.get("security_type"), "")
        item["category_label"] = category.category_label(item.get("category"))
        item["time"] = (item.get("activity_at") or "")[:10]
        mentions = report_mentions.get(item.get("url"), [])
        item["report_mentions"] = list(mentions)
        items.append(item)
    items = _merge_series(items, name)
    reference = max((datetime.fromisoformat(item["activity_at"].replace("Z", "+00:00"))
                     for item in items if item.get("activity_at")),
                    default=datetime.now(timezone.utc))
    for item in items:
        item["priority_score"], item["priority_reasons"] = _priority(item, reference)
    return items


def _deduplicate(items: list[dict]) -> list[dict]:
    seen = set()
    result = []
    for item in items:
        canonical = _canonical_title(item) or item["thread_key"]
        if canonical in seen:
            continue
        seen.add(canonical)
        result.append(item)
    return result


def _partition_items(items: list[dict], topic_key: str) -> tuple[list[dict], list[dict]]:
    """Split the raw candidate index into durable curation and short-lived watch items."""
    # 安全能力建设不等同于漏洞。SEV/TDX/Arm CCA 等增强仍保留在内部索引和
    # 周期报告中，但安全专题公开层只呈现明确 CVE 与具有强证据的缺陷。
    if topic_key == "security":
        items = [item for item in items
                 if item.get("security_type") in {"cve", "defect"}]
    reference = max((datetime.fromisoformat(item["activity_at"].replace("Z", "+00:00"))
                     for item in items if item.get("activity_at")),
                    default=datetime.now(timezone.utc))
    cutoff = reference - timedelta(days=14)

    def recent(item: dict) -> bool:
        try:
            activity = datetime.fromisoformat(item["activity_at"].replace("Z", "+00:00"))
        except (AttributeError, TypeError, ValueError):
            return False
        if activity < cutoff or item["curated_mentions"]:
            return False
        if item["daily_mentions"]:
            item["scope"] = "recent"
            item["scope_label"] = "近期观察"
            item["report_keys"] = [m["period_key"] for m in item["daily_mentions"][:3]]
            return True
        if (topic_key == "security" and
                item.get("security_type") in {"cve", "defect"}):
            item["scope"] = "recent"
            item["scope_label"] = "安全观察"
            return True
        return False

    curated = [item for item in items if item["curated_mentions"]]
    watch = [item for item in items if recent(item)]

    def curated_key(item: dict) -> tuple:
        mentions = item["curated_mentions"]
        monthly = any(m["period"] == "monthly" for m in mentions)
        return (monthly, len(mentions), item["priority_score"], item.get("activity_at") or "")

    curated.sort(key=curated_key, reverse=True)
    watch.sort(key=lambda item: (item["priority_score"], item.get("activity_at") or ""),
               reverse=True)
    return _deduplicate(curated), _deduplicate(watch)


_PUBLIC_ITEM_FIELDS = {
    "thread_key", "project", "source", "title", "url", "summary", "impact",
    "activity_at", "category", "architectures", "cve_ids", "security_type",
    "status", "security_label", "category_label", "time", "report_keys",
    "scope", "scope_label", "series_count", "series_version", "priority_score",
}


def _public_item(item: dict) -> dict:
    """只保留页面、分页与 RSS 需要的字段，控制持久化快照体积。"""
    return {key: value for key, value in item.items() if key in _PUBLIC_ITEM_FIELDS}


def _compute_topic_payloads(conn: sqlite3.Connection) -> dict[str, dict]:
    """从物化专题索引离线计算版本链、报告证据和公开分层。"""
    sync_topic_index(conn)
    mentions = _report_mentions(conn)
    payloads = {}
    for key, name, description, _words in TOPIC_RULES:
        all_items = _load_topic_items(conn, key, name, mentions)
        curated, recent = _partition_items(all_items, key)
        payloads[key] = {
            "key": key, "name": name, "description": description,
            "raw_total": len(all_items),
            "curated": [_public_item(item) for item in curated],
            "recent": [_public_item(item) for item in recent],
        }
    return payloads


def refresh_topic_snapshots(conn: sqlite3.Connection) -> dict[str, int]:
    """离线重建专题公开快照，并在全部计算成功后原子替换旧快照。"""
    payloads = _compute_topic_payloads(conn)
    generated_at = db.now_utc_iso()
    with db.transaction(conn):
        conn.execute("DELETE FROM topic_snapshots")
        conn.executemany(
            "INSERT INTO topic_snapshots "
            "(topic_key,rule_version,generated_at,content_json) VALUES (?,?,?,?)",
            [
                (key, RULE_VERSION, generated_at,
                 json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
                for key, payload in payloads.items()
            ],
        )
    return {
        key: len(payload["curated"]) + len(payload["recent"])
        for key, payload in payloads.items()
    }


def _load_topic_snapshots(conn: sqlite3.Connection) -> dict[str, dict] | None:
    rows = conn.execute(
        "SELECT topic_key,rule_version,content_json FROM topic_snapshots"
    ).fetchall()
    expected = {key for key, _name, _description, _words in TOPIC_RULES}
    if ({row["topic_key"] for row in rows} != expected
            or any(row["rule_version"] != RULE_VERSION for row in rows)):
        return None
    try:
        return {row["topic_key"]: json.loads(row["content_json"]) for row in rows}
    except (TypeError, ValueError):
        return None


def build_topic_groups(conn: sqlite3.Connection, limit: int = 8, *,
                       allow_rebuild: bool = True) -> list[dict] | None:
    """从持久化快照构建专题概览；仅离线任务允许缺失时现场计算。"""
    payloads = _load_topic_snapshots(conn)
    if payloads is None:
        if not allow_rebuild:
            return None
        payloads = _compute_topic_payloads(conn)
    groups = []
    for key, _name, _description, _words in TOPIC_RULES:
        payload = payloads[key]
        curated = payload["curated"]
        recent = payload["recent"]
        recent_slots = min(len(recent), max(1, limit // 4)) if curated else limit
        curated_slots = min(len(curated), limit - recent_slots)
        featured = curated[:curated_slots] + recent[:recent_slots]
        if len(featured) < limit:
            featured.extend(curated[curated_slots:limit - len(featured) + curated_slots])
        if len(featured) < limit:
            featured.extend(recent[recent_slots:limit - len(featured) + recent_slots])
        groups.append({
            "key": key, "name": payload["name"],
            "description": payload["description"],
            "items": featured, "raw_total": payload["raw_total"],
            "curated_count": len(curated), "recent_count": len(recent),
            "total": len(curated) + len(recent), "featured_count": len(featured),
            "detail_url": f"topics/{key}/",
        })
    return groups


def build_topic_detail(conn: sqlite3.Connection, topic_key: str, *, page: int = 1,
                       per_page: int = 20, sort: str = "priority",
                       scope: str = "curated",
                       allow_rebuild: bool = True) -> dict | None:
    """从持久化快照构建单专题详情和服务端分页数据。"""
    definition = _topic_definition(topic_key)
    if not definition:
        return None
    name, description = definition
    payloads = _load_topic_snapshots(conn)
    if payloads is None:
        if not allow_rebuild:
            return None
        payloads = _compute_topic_payloads(conn)
    payload = payloads[topic_key]
    curated = payload["curated"]
    recent = payload["recent"]
    scope = "recent" if scope == "recent" else "curated"
    items = list(recent if scope == "recent" else curated)
    if sort == "latest":
        items.sort(key=lambda item: item.get("activity_at") or "", reverse=True)
    else:
        sort = "priority"
        items.sort(key=lambda item: (item["priority_score"], item.get("activity_at") or ""),
                   reverse=True)
    per_page = per_page if per_page in {10, 20, 30} else 20
    pages = max(1, math.ceil(len(items) / per_page))
    page = min(max(1, page), pages)
    start = (page - 1) * per_page
    return {
        "key": topic_key, "name": name, "description": description,
        "items": items[start:start + per_page], "total": len(items),
        "curated_count": len(curated), "recent_count": len(recent), "scope": scope,
        "page": page, "pages": pages, "per_page": per_page, "sort": sort,
    }
