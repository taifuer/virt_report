"""通用周期报告生成器 (daily / weekly / monthly)。

取周期窗口内 Top 线程 -> LLM 总结 -> 结构化内容 (含 themes/takeaways/dynamics/stats)。
无 LLM key 或调用失败时走模板降级。内容落 reports 表。
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone

from virt_report import db
from virt_report.config import Config
from virt_report.processing.architecture import detect_architectures, focus_priority
from virt_report.processing.category import category_label, classify_change
from . import llm_provider, periods, prompts

log = logging.getLogger(__name__)

# 每周期喂给 LLM 的线程数上限 (DeepSeek v4 上下文 64K+, 可喂更多)
TOP_N = {"daily": None, "weekly": 60, "monthly": 100}  # daily 用 config.llm.daily_top_n
# DeepSeek V4 最大输出远高于本值；这里为完整 JSON 和思考内容留足余量。
MAX_TOKENS = {"daily": 12000, "weekly": 24000, "monthly": 32000}
# 思考强度 (high/max); 高强度更慢，high 已足够且快约一倍
REASONING_EFFORT = {"daily": "high", "weekly": "high", "monthly": "high"}
# 周期越大、线程越多，单条摘要越短以控制输入
EXCERPT_LEN = {"daily": 450, "weekly": 350, "monthly": 250}
ITEM_LIMIT = {"daily": 18, "weekly": 27, "monthly": 36}



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


def _build_threads_data(conn: sqlite3.Connection, rows, excerpt_len: int = 300) -> list[dict]:
    data = []
    for i, r in enumerate(rows, 1):
        context = _thread_context(conn, r)
        architectures = detect_architectures((r["subject"], r["topic_tag"]))
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
    ("libvirt", "Libvirt", ("libvir-list", "libvirt")),
    ("kvm", "KVM", ("kvm",)),
]


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


def enrich_architectures(content: dict) -> dict:
    """为旧报告按内含证据补齐架构标签，并应用当前排序与条数规则。"""
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
            item["architectures"] = architectures
            item["category"] = category
            item["category_label"] = category_label(category)
        section["items"].sort(key=lambda item: (
            focus_priority(item.get("architectures", [])),
            int((item.get("ref") or "T9999")[1:] or 9999),
        ))
    period = content.get("period")
    if period in ITEM_LIMIT:
        content["sections"] = _limit_sections(content.get("sections", []), ITEM_LIMIT[period])
    return content


def generate(conn: sqlite3.Connection, config: Config, period: str,
             period_key: str) -> dict:
    """生成某个周期(日/周/月)的报告内容并落库。"""
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
    threads_data = _build_threads_data(conn, rows, EXCERPT_LEN[period])

    provider = llm_provider.get_provider(config.llm)
    model = {"daily": config.llm.daily_model, "weekly": config.llm.weekly_model,
             "monthly": config.llm.monthly_model}[period]
    fallback = False

    parsed = None
    if provider and threads_data:
        user = prompts.build_prompt(period, period_key, threads_data)
        for attempt in range(2):
            try:
                text = provider.complete(
                    user, system=prompts.system_prompt(period, period_key), model=model,
                    max_tokens=MAX_TOKENS[period], json_mode=True,
                    thinking="enabled", reasoning_effort=REASONING_EFFORT[period],
                )
                parsed = llm_provider.extract_json(text)
                if parsed:
                    break
                log.warning("LLM 返回空或不可解析 (len=%d), %s", len(text or ""),
                            "重试" if attempt == 0 else "放弃")
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
        "llm_usage": getattr(provider, "last_usage", {}) if provider and not fallback else {},
        "llm_finish_reason": getattr(provider, "last_finish_reason", None) if provider and not fallback else None,
        "reasoning_chars": getattr(provider, "last_reasoning_chars", 0) if provider and not fallback else 0,
    }
    db.save_report(conn, period, period_key, content, config.timezone,
                   item_count=item_count, model=used_model)
    log.info("%s报 %s 生成完成 (overview=%d, sections=%d, items=%d, model=%s, fallback=%s)",
             period, period_key, len(overview), len(sections), item_count,
             used_model, fallback)
    return content
