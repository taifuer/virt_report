"""通用周期报告生成器 (daily / weekly / monthly)。

取周期窗口内 Top 线程 -> LLM 总结 -> 结构化内容 (含 themes/takeaways/dynamics/stats)。
无 LLM key 或调用失败时可生成内部模板降级内容；自动任务只发布 AI 正稿。
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime, timezone

from virt_report import db
from virt_report.config import Config
from virt_report.processing.architecture import (
    detect_architectures,
    focus_priority,
    normalize_architectures,
)
from virt_report.processing.category import category_label, classify_change
from . import llm_provider, periods, prompts

log = logging.getLogger(__name__)

# 每周期喂给 LLM 的线程数上限。V4 Flash 支持 1M 上下文；候选数仍按信噪比和
# 成本约束，而不是为了填满上下文盲目扩大。
TOP_N = {"daily": None, "weekly": 60, "monthly": 100}  # daily 用 config.llm.daily_top_n
# V4 Flash 最大输出为 384K；思考 token 与最终 JSON 共用输出预算。以下上限可避免
# 日报在 high reasoning 下被 12K 提前截断，同时保留明确的成本边界。
MAX_TOKENS = {"daily": 32768, "weekly": 49152, "monthly": 65536}
MAX_RETRY_TOKENS = 98304
# 思考强度 (high/max); 高强度更慢，high 已足够且快约一倍
REASONING_EFFORT = {"daily": "high", "weekly": "high", "monthly": "high"}
# 采集层单段证据上限为 600 字；V4 Flash 的 1M 上下文足以让长周期报告保留
# 完整的 opening/latest/review 证据，不再为月报额外截成 250 字。
EXCERPT_LEN = {"daily": 600, "weekly": 600, "monthly": 600}
ITEM_LIMIT = {"daily": 30, "weekly": 27, "monthly": 36}
DAILY_CONTINUING_LIMIT = 5


def _merge_usage(total: dict, current: dict) -> None:
    """累加多次模型响应的 token 用量，避免截断重试低估成本。"""
    for key, value in (current or {}).items():
        if isinstance(value, dict):
            nested = total.setdefault(key, {})
            if isinstance(nested, dict):
                _merge_usage(nested, value)
        elif isinstance(value, (int, float)):
            total[key] = total.get(key, 0) + value


def _complete_llm_json(parsed: dict | None, finish_reason: str | None) -> bool:
    """即使 JSON 可解析，length 也代表响应被上限截断，不能正式发布。"""
    return bool(parsed) and finish_reason != "length"



def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_root(thread_key: str) -> str:
    parts = thread_key.split(":", 2)
    return parts[2] if len(parts) == 3 else thread_key


def _thread_context(conn: sqlite3.Connection, row) -> dict[str, str]:
    root = _parse_root(row["thread_key"])
    items = conn.execute(
        "SELECT body_excerpt, raw_json FROM items "
        "WHERE source=? AND project=? AND thread_root=? ORDER BY activity_at ASC",
        (row["source"], row["project"], root),
    ).fetchall()
    if not items:
        return {"excerpt": "", "latest_excerpt": "", "review_excerpt": "",
                "state": ""}
    excerpts = [(it["body_excerpt"] or "") for it in items]
    review = next((e for e in reversed(excerpts)
                   if "reviewed-by:" in e.lower() or "acked-by:" in e.lower()
                   or "tested-by:" in e.lower()), "")
    try:
        raw = json.loads(items[-1]["raw_json"] or "{}")
    except (TypeError, ValueError):
        raw = {}
    return {
        "excerpt": excerpts[0],
        "latest_excerpt": excerpts[-1] if len(excerpts) > 1 else "",
        "review_excerpt": review,
        "state": raw.get("state", "") or "",
    }


def _daily_report_history(conn: sqlite3.Connection, period_key: str,
                          limit: int = 14) -> dict[str, dict]:
    """Return the most recent published context for each URL before a daily report."""
    history: dict[str, dict] = {}
    rows = conn.execute(
        "SELECT period_key,content_json FROM reports WHERE period='daily' "
        "AND period_key<? ORDER BY period_key DESC LIMIT ?", (period_key, limit),
    ).fetchall()
    for row in rows:
        try:
            content = json.loads(row["content_json"])
        except (TypeError, ValueError):
            continue
        if content.get("fallback"):
            continue
        for section in content.get("sections", []):
            for item in section.get("items", []):
                url = item.get("url")
                if url and url not in history:
                    history[url] = {
                        "period_key": row["period_key"],
                        "summary": item.get("summary", "") or "",
                        "impact": item.get("impact", "") or "",
                        "status": item.get("status", "") or "",
                    }
    return history


def _limit_continuing_threads(rows: list, history: dict[str, dict],
                              limit: int = DAILY_CONTINUING_LIMIT) -> list:
    """Keep all unseen threads and only a small, project-balanced update set."""
    fresh = [row for row in rows if row["url"] not in history]
    continuing = [row for row in rows if row["url"] in history]
    selected = []
    selected_keys = set()
    for _key, _name, projects in _PROJECT_GROUPS:
        match = next((row for row in continuing if row["project"] in projects), None)
        if match is not None and len(selected) < limit:
            selected.append(match)
            selected_keys.add(match["thread_key"])
    for row in continuing:
        if len(selected) >= limit:
            break
        if row["thread_key"] not in selected_keys:
            selected.append(row)
            selected_keys.add(row["thread_key"])
    combined = fresh + selected
    combined.sort(key=lambda row: (row["salience_score"], row["message_count"]),
                  reverse=True)
    return combined


def _build_threads_data(conn: sqlite3.Connection, rows, excerpt_len: int = 300,
                        history: dict[str, dict] | None = None) -> list[dict]:
    data = []
    for i, r in enumerate(rows, 1):
        context = _thread_context(conn, r)
        architectures = detect_architectures((r["subject"], r["topic_tag"]))
        previous = (history or {}).get(r["url"])
        data.append({
            "ref": f"T{i:03d}",
            "rank": i,
            "project": r["project"],
            "kind": r["kind"],
            "category": classify_change(r["kind"], r["subject"]),
            "msg_count": r["message_count"],
            "participants": r["participant_count"],
            "topic": r["topic_tag"],
            "subject": r["subject"],
            "excerpt": context["excerpt"][:excerpt_len],
            "latest_excerpt": context["latest_excerpt"][:excerpt_len],
            "review_excerpt": context["review_excerpt"][:excerpt_len],
            "state": context["state"],
            "architectures": architectures,
            "url": r["url"],
            "time": (r["last_seen"] or "")[:10],
            "novelty": "updated" if previous else "new",
            "previous_report": previous or {},
        })
    return data


def _stats(conn: sqlite3.Connection, start_iso: str, end_iso: str) -> dict:
    items = db.get_activity_items_in_window(conn, start_iso, end_iso)
    by_project: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    by_source: dict[str, int] = {}
    ml_patches = ml_rfc = gl_issues_opened = 0
    for it in items:
        by_project[it["project"]] = by_project.get(it["project"], 0) + 1
        by_kind[it["kind"]] = by_kind.get(it["kind"], 0) + 1
        by_source[it["source"]] = by_source.get(it["source"], 0) + 1
        if it["source"] == "ml":
            if it["kind"] == "patch":
                ml_patches += 1
            elif it["kind"] == "rfc":
                ml_rfc += 1
        else:
            rj = json.loads(it["raw_json"] or "{}")
            if rj.get("type") == "issue" and start_iso <= it["created_at"] < end_iso:
                gl_issues_opened += 1
    mrs = conn.execute(
        "SELECT raw_json FROM items WHERE source='gitlab' AND kind='mr' "
        "AND updated_at>=? AND updated_at<?", (start_iso, end_iso),
    ).fetchall()
    gl_mrs_merged = sum(1 for r in mrs if json.loads(r["raw_json"] or "{}").get("merged"))
    total_threads = conn.execute(
        "SELECT COUNT(*) c FROM threads WHERE last_seen>=? AND last_seen<?",
        (start_iso, end_iso),
    ).fetchone()["c"]
    return {
        "total_items": len(items),
        "total_threads": total_threads,
        "by_project": by_project,
        "by_kind": by_kind,
        "by_source": by_source,
        "ml_patches": ml_patches,
        "ml_rfc": ml_rfc,
        "gl_issues_opened": gl_issues_opened,
        "gl_mrs_merged": gl_mrs_merged,
    }


def _source_label(t: dict) -> str:
    proj = t["project"]
    if proj in ("libvirt", "qemu"):
        return f"GitLab {proj}"
    return {"qemu-devel": "qemu-devel 邮件列表",
            "libvir-list": "libvirt-devel 邮件列表",
            "kvm": "KVM 邮件列表"}.get(proj, f"邮件列表 {proj}")


_PROJECT_GROUPS = [
    ("qemu", "QEMU", ("qemu-devel", "qemu")),
    ("kvm", "KVM", ("kvm",)),
    ("libvirt", "Libvirt", ("libvir-list", "libvirt")),
]


def _order_report_projects(content: dict) -> dict:
    """按站点固定阅读顺序整理概览和项目区块，不改变条目内容。"""
    key_order = {key: index for index, (key, _name, _members) in enumerate(
        _PROJECT_GROUPS
    )}
    name_order = {name.casefold(): index for index, (_key, name, _members) in enumerate(
        _PROJECT_GROUPS
    )}
    content["overview"] = sorted(
        content.get("overview", []),
        key=lambda item: name_order.get(
            str(item.get("project", "")).casefold(), len(name_order)
        ) if isinstance(item, dict) else len(name_order),
    )
    content["sections"] = sorted(
        content.get("sections", []),
        key=lambda section: key_order.get(
            str(section.get("key", "")).casefold(), len(key_order)
        ) if isinstance(section, dict) else len(key_order),
    )
    return content


def _project_of(t: dict) -> tuple[str, str]:
    """线程 -> (key, display_name)。"""
    proj = t.get("project", "")
    for key, name, members in _PROJECT_GROUPS:
        if proj in members:
            return key, name
    return "qemu", "QEMU"


def _fallback(threads_data: list[dict], period: str, period_key: str,
              key_env: str | None) -> tuple[list[dict], list[dict]]:
    """降级：按 QEMU/Libvirt/KVM 分组。返回 (overview, sections)。"""
    range_word = {"daily": "当日", "weekly": "本周", "monthly": "本月"}[period]
    env_hint = f"（未配置 {key_env}，降级模式）" if key_env else "(降级模式)"
    buckets: dict[str, list[dict]] = {key: [] for key, _, _ in _PROJECT_GROUPS}
    for t in threads_data:
        key, _ = _project_of(t)
        buckets[key].append(t)
    overview: list[dict] = []
    sections: list[dict] = []
    for key, name, _ in _PROJECT_GROUPS:
        ts = buckets[key]
        if not ts:
            continue
        top_titles = "；".join(t["subject"][:30] for t in ts[:2])
        overview.append({"project": name,
                         "summary": f"{range_word}活跃 {len(ts)} 项：{top_titles}{env_hint}"})
        items = []
        for t in ts[:10]:
            items.append({
                "ref": t["ref"],
                "title": t["subject"], "source": _source_label(t), "time": t["time"],
                "summary": (t.get("excerpt") or "")[:100] or t["subject"][:60],
                "impact": "", "status": t.get("state") or "",
                "url": t["url"], "tag": t.get("topic") or "",
                "architectures": t.get("architectures", []),
                "category": t.get("category", "other"),
                "category_label": category_label(t.get("category")),
            })
        sections.append({"key": key, "name": name, "items": items})
    if not overview:
        overview = [{"project": "(无)",
                     "summary": f"{period_key} {range_word}无显著活跃数据{env_hint}"}]
    return overview, sections


def _sanitize(overview: list, sections: list,
              threads_data: list[dict] | None = None) -> tuple[list[dict], list[dict]]:
    """校验结构，并用 ref 回填可信来源字段，阻断模型伪造 URL/项目归属。"""
    ov_map: dict[str, dict] = {}
    for o in overview or []:
        if isinstance(o, dict):
            project = o.get("project", "") or ""
            ov_map[project.lower()] = {"project": project,
                                       "summary": o.get("summary", "") or ""}
    ov: list[dict] = []
    for _key, name, _members in _PROJECT_GROUPS:
        found = ov_map.get(name.lower())
        ov.append(found or {"project": name, "summary": "本期未观察到显著动态。"})

    evidence = {t["ref"]: t for t in (threads_data or [])}
    if evidence:
        buckets = {key: [] for key, _name, _members in _PROJECT_GROUPS}
        seen: set[str] = set()
        for section in sections or []:
            if not isinstance(section, dict) or not isinstance(section.get("items"), list):
                continue
            for it in section["items"]:
                if not isinstance(it, dict):
                    continue
                ref = it.get("ref", "")
                source = evidence.get(ref)
                if not source or ref in seen:
                    continue
                seen.add(ref)
                project_key, _ = _project_of(source)
                canonical_state = (source.get("state") or "").lower()
                canonical_status = {"open": "开放", "closed": "已关闭",
                                    "merged": "已合并", "reopened": "重新开放"}.get(canonical_state)
                impact = it.get("impact", "") or ""
                if canonical_state == "closed" and any(
                    word in impact for word in ("需尽快", "需要尽快", "待修复", "尚需修复")
                ):
                    impact = "该问题已关闭；具体修复方式和影响范围请以原始 issue 的关闭结论为准。"
                buckets[project_key].append({
                    "ref": ref,
                    "title": it.get("title", "") or source["subject"],
                    "original_title": source["subject"],
                    "source": _source_label(source), "time": source["time"],
                    "summary": it.get("summary", "") or "",
                    "impact": impact,
                    "status": canonical_status or it.get("status", "") or "",
                    "url": source["url"],
                    "tag": it.get("tag", "") or it.get("subsystem", "") or source.get("topic", "") or "",
                    "architectures": source.get("architectures", []),
                    "category": source.get("category", "other"),
                    "category_label": category_label(source.get("category")),
                    "novelty": source.get("novelty", "new"),
                    "novelty_label": ("有新进展" if source.get("novelty") == "updated"
                                      else "首次出现"),
                    "previous_report": source.get("previous_report", {}),
                })
        for items in buckets.values():
            items.sort(key=lambda item: (
                focus_priority(item.get("architectures", [])),
                int((item.get("ref") or "T999")[1:] or 999),
            ))
        return ov, [
            {"key": key, "name": name, "items": buckets[key]}
            for key, name, _members in _PROJECT_GROUPS
        ]

    secs: list[dict] = []
    for s in sections or []:
        if not isinstance(s, dict):
            continue
        raw = s.get("items")
        raw = raw if isinstance(raw, list) else []
        items = []
        for it in raw:
            if not isinstance(it, dict):
                continue
            items.append({
                "ref": it.get("ref", "") or "",
                "title": it.get("title", "") or "",
                "original_title": it.get("original_title", "") or "",
                "source": it.get("source", "") or "",
                "time": it.get("time", "") or "",
                "summary": it.get("summary", "") or "",
                "impact": it.get("impact", "") or "",
                "status": it.get("status", "") or "",
                "url": it.get("url", "") or "",
                "tag": it.get("tag", "") or it.get("subsystem", "") or "",
                "architectures": it.get("architectures", [])
                if isinstance(it.get("architectures", []), list) else [],
                "category": it.get("category", "other")
                if it.get("category") in {"feature", "bug", "other"} else "other",
                "category_label": category_label(it.get("category")),
            })
        secs.append({"key": s.get("key", "") or "", "name": s.get("name", "") or "",
                     "items": items})
    return ov, secs


def _limit_sections(sections: list[dict], limit: int) -> list[dict]:
    """硬限制展示条数：先保留项目覆盖，再按架构关注度和证据排名筛选。"""
    candidates: list[tuple[int, int, int, int, dict]] = []
    selected_ids: set[int] = set()
    selected: list[tuple[int, dict]] = []
    for section_index, section in enumerate(sections):
        items = section.get("items", [])
        if items:
            item = items[0]
            selected.append((section_index, item))
            selected_ids.add(id(item))
        for position, item in enumerate(items):
            ref = item.get("ref", "")
            try:
                evidence_rank = int(ref[1:])
            except (TypeError, ValueError):
                evidence_rank = 9999
            candidates.append((
                focus_priority(item.get("architectures", [])),
                evidence_rank, section_index, position, item,
            ))
    for _priority, _rank, section_index, _position, item in sorted(candidates):
        if len(selected) >= limit:
            break
        if id(item) in selected_ids:
            continue
        selected.append((section_index, item))
        selected_ids.add(id(item))

    kept = {id(item) for _index, item in selected[:limit]}
    return [
        {**section, "items": [item for item in section.get("items", []) if id(item) in kept]}
        for section in sections
    ]


def _complete_watchlist(watchlist: list[dict], sections: list[dict]) -> list[dict]:
    """补全模型遗漏的观察理由；无法关联证据的空项不进入页面。"""
    by_project: dict[str, list[dict]] = {}
    for section in sections:
        items = section.get("items", [])
        for project in (section.get("key", ""), section.get("name", "")):
            if project:
                by_project.setdefault(project.casefold(), []).extend(items)

    completed: list[dict] = []
    for entry in watchlist:
        if not isinstance(entry, dict):
            continue
        project = str(entry.get("project", "") or "").strip()
        topic = str(entry.get("topic", "") or "").strip()
        reason = str(entry.get("reason", "") or "").strip()
        if not project or not topic:
            continue
        if not reason:
            topic_words = {
                word for word in re.findall(r"[a-z0-9_+-]+", topic.casefold())
                if len(word) > 2
            }
            best: tuple[int, dict] | None = None
            for item in by_project.get(project.casefold(), []):
                haystack = " ".join(str(item.get(field, "") or "") for field in (
                    "title", "original_title", "tag", "summary",
                )).casefold()
                compact_topic = re.sub(r"\W+", "", topic.casefold())
                compact_haystack = re.sub(r"\W+", "", haystack)
                score = 100 if compact_topic and compact_topic in compact_haystack else 0
                score += 10 * len(topic_words.intersection(
                    re.findall(r"[a-z0-9_+-]+", haystack)
                ))
                if score and (best is None or score > best[0]):
                    best = (score, item)
            if best:
                item = best[1]
                summary = str(item.get("summary", "") or "").rstrip("。；; ")
                status = str(item.get("status", "") or "").strip()
                if summary:
                    reason = summary + "。"
                if status and status not in {"已合并", "已关闭"}:
                    reason += f"当前状态为{status}，后续版本与评审结论值得观察。"
            if not reason:
                continue
        completed.append({"project": project, "topic": topic, "reason": reason})
    return completed[:4]


def enrich_architectures(content: dict) -> dict:
    """为旧报告按内含证据补齐架构标签，并应用当前排序与条数规则。"""
    _order_report_projects(content)
    evidence: dict[str, tuple[list[str], str]] = {}
    for thread in content.get("top_threads", []):
        architectures = detect_architectures((
            thread.get("subject"), thread.get("topic"),
        ))
        thread["architectures"] = architectures
        category = classify_change(thread.get("kind"), thread.get("subject"))
        thread["category"] = category
        if thread.get("ref"):
            evidence[thread["ref"]] = (architectures, category)
    for section in content.get("sections", []):
        for item in section.get("items", []):
            architectures, category = evidence.get(
                item.get("ref", ""),
                (item.get("architectures", []), item.get("category", "other")),
            )
            item["architectures"] = normalize_architectures(architectures)
            item["category"] = category
            item["category_label"] = category_label(category)
        section["items"].sort(key=lambda item: (
            focus_priority(item.get("architectures", [])),
            int((item.get("ref") or "T9999")[1:] or 9999),
        ))
    period = content.get("period")
    if period in ITEM_LIMIT:
        content["sections"] = _limit_sections(content.get("sections", []), ITEM_LIMIT[period])
    content["watchlist"] = _complete_watchlist(
        content.get("watchlist", []), content.get("sections", [])
    )
    return content


def generate(conn: sqlite3.Connection, config: Config, period: str,
             period_key: str, *, publish_fallback: bool = True) -> dict:
    """生成某个周期报告；自动任务可选择只在 AI 成功时发布。"""
    start_utc, end_utc = periods.window(period, period_key, config.timezone)
    start_iso, end_iso = _iso(start_utc), _iso(end_utc)

    top_n = TOP_N[period] if TOP_N[period] else config.llm.daily_top_n
    # 每个项目组独立配额，避免 qemu-devel 的邮件量吞掉 Libvirt/KVM。
    quotas = {
        "qemu": (top_n + 1) // 2,
        "libvirt": (top_n + 3) // 4,
        "kvm": top_n - ((top_n + 1) // 2) - ((top_n + 3) // 4),
    }
    rows = []
    for key, _name, members in _PROJECT_GROUPS:
        rows.extend(db.get_top_threads_for_projects(
            conn, start_iso, end_iso, members, quotas[key]
        ))
    rows.sort(key=lambda r: (r["salience_score"], r["message_count"]), reverse=True)
    history = _daily_report_history(conn, period_key) if period == "daily" else {}
    if period == "daily" and history:
        rows = _limit_continuing_threads(rows, history)
    threads_data = _build_threads_data(conn, rows, EXCERPT_LEN[period], history)

    provider = llm_provider.get_provider(config.llm)
    model = {"daily": config.llm.daily_model, "weekly": config.llm.weekly_model,
             "monthly": config.llm.monthly_model}[period]
    fallback = False
    aggregate_usage: dict = {}
    aggregate_reasoning_chars = 0
    llm_attempts = 0

    parsed = None
    if provider and threads_data:
        user = prompts.build_prompt(period, period_key, threads_data)
        for attempt in range(2):
            try:
                max_tokens = (MAX_TOKENS[period] if attempt == 0 else
                              min(MAX_TOKENS[period] * 2, MAX_RETRY_TOKENS))
                attempt_user = user
                if attempt:
                    attempt_user += (
                        "\n\n上一次响应未形成完整合法 JSON。请减少铺陈，严格遵守字段"
                        "和条目数量限制，确保在输出上限内闭合整个 JSON 对象。"
                    )
                text = provider.complete(
                    attempt_user, system=prompts.system_prompt(period, period_key), model=model,
                    max_tokens=max_tokens, json_mode=True,
                    thinking="enabled", reasoning_effort=REASONING_EFFORT[period],
                )
                llm_attempts += 1
                _merge_usage(aggregate_usage, getattr(provider, "last_usage", {}))
                aggregate_reasoning_chars += getattr(provider, "last_reasoning_chars", 0)
                parsed = llm_provider.extract_json(text)
                finish_reason = getattr(provider, "last_finish_reason", None)
                if _complete_llm_json(parsed, finish_reason):
                    break
                parsed = None
                log.warning(
                    "LLM 返回空或不可解析 (len=%d, finish=%s, max_tokens=%d), %s",
                    len(text or ""), finish_reason, max_tokens,
                    "提高输出预算后重试" if attempt == 0 else "放弃",
                )
            except Exception as e:
                log.warning("LLM 调用失败 (provider 已重试), 使用降级: %s", e)
                parsed = None
                break  # provider 内部已退避重试, 不再 report 层重试

    stats = _stats(conn, start_iso, end_iso)
    overview, sections = [], []
    headline = ""
    watchlist: list[dict] = []
    if parsed:
        headline = parsed.get("headline", "") or ""
        overview = parsed.get("overview", []) or []
        sections = parsed.get("sections", []) or []
        raw_watchlist = parsed.get("watchlist", [])
        if isinstance(raw_watchlist, list):
            watchlist = [{
                "project": w.get("project", "") or "",
                "topic": w.get("topic", "") or "",
                "reason": w.get("reason", "") or "",
            } for w in raw_watchlist if isinstance(w, dict)][:4]
    if not parsed or (not overview and not sections):
        fallback = True
        overview, sections = _fallback(threads_data, period, period_key, config.llm.api_key_env)
        headline = f"{period_key} 虚拟化社区活跃动态（模板摘要）"
    overview, sections = _sanitize(overview, sections, threads_data if parsed else None)
    sections = _limit_sections(sections, ITEM_LIMIT[period])
    watchlist = _complete_watchlist(watchlist, sections)
    used_model = "fallback" if fallback else model
    item_count = sum(len(s.get("items", [])) for s in sections)
    content = {
        "period": period, "period_key": period_key,
        "label": periods.label(period, period_key), "timezone": config.timezone,
        "window": {"start": start_iso, "end": end_iso},
        "headline": headline, "overview": overview, "watchlist": watchlist,
        "sections": sections, "stats": stats,
        "top_threads": threads_data, "model": used_model, "fallback": fallback,
        "generated_at": db.now_utc_iso(),
        "llm_usage": aggregate_usage,
        "llm_attempts": llm_attempts,
        "llm_finish_reason": getattr(provider, "last_finish_reason", None) if provider else None,
        "reasoning_chars": aggregate_reasoning_chars,
    }
    published = not fallback or publish_fallback
    if published:
        db.save_report(conn, period, period_key, content, config.timezone,
                       item_count=item_count, model=used_model)
    log.info("%s报 %s 生成完成 (overview=%d, sections=%d, items=%d, model=%s, "
             "fallback=%s, published=%s)",
             period, period_key, len(overview), len(sections), item_count,
             used_model, fallback, published)
    return content
