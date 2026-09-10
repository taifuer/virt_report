"""运行指标、采集状态与 LLM 成本估算。"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone

from virt_report.config import Config
from virt_report.summarize import billing


def _usage_cost(model: str, usage: dict, config: Config) -> dict:
    """Legacy estimates deliberately do not apply new time-of-day schedules."""
    cost = billing.estimate(usage, config.llm.pricing_cny.get(model) or {})
    if cost["estimated_cost_cny"] is not None:
        cost["estimated_cost_cny"] = round(cost["estimated_cost_cny"], 6)
    return {**cost, "unpriced_calls": int(bool(usage) and cost["estimated_cost_cny"] is None),
            "pricing_basis": "legacy"}


def build_metrics(conn: sqlite3.Connection, config: Config) -> dict:
    """构建可用于 HTML 和 JSON API 的运行统计。"""
    counts = {
        "items": conn.execute("SELECT COUNT(*) FROM items").fetchone()[0],
        "threads": conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0],
        "topic_entries": conn.execute("SELECT COUNT(*) FROM topic_entries").fetchone()[0],
    }
    report_counts: dict[str, int] = defaultdict(int)
    scheduler_runs = [dict(row) for row in conn.execute(
        "SELECT identity,job_name,scheduled_at,started_at,finished_at,status,attempt,"
        "exit_code,error FROM scheduler_runs ORDER BY id DESC LIMIT 20"
    ).fetchall()]
    scheduler_failures_24h = conn.execute(
        "SELECT COUNT(*) FROM scheduler_runs WHERE status NOT IN ('success','running') "
        "AND julianday(started_at)>=julianday('now','-1 day')"
    ).fetchone()[0]
    latest_sources = []
    keys = conn.execute(
        "SELECT DISTINCT source,project FROM fetch_runs ORDER BY source,project"
    ).fetchall()
    for key in keys:
        latest = conn.execute(
            "SELECT *,(julianday(finished_at)-julianday(started_at))*86400 duration_s "
            "FROM fetch_runs WHERE source=? AND project=? ORDER BY id DESC LIMIT 1",
            (key["source"], key["project"]),
        ).fetchone()
        aggregate = conn.execute(
            "SELECT COUNT(*) runs,SUM(CASE WHEN success=1 AND complete=1 THEN 1 ELSE 0 END) ok_runs,"
            "AVG((julianday(finished_at)-julianday(started_at))*86400) avg_duration_s,"
            "SUM(new_count) new_count FROM (SELECT * FROM fetch_runs WHERE source=? AND project=? "
            "ORDER BY id DESC LIMIT 10)", (key["source"], key["project"]),
        ).fetchone()
        item = dict(latest)
        item.update({
            "duration_s": round(item.get("duration_s") or 0, 1),
            "recent_runs": aggregate["runs"] or 0,
            "recent_ok_runs": aggregate["ok_runs"] or 0,
            "avg_duration_s": round(aggregate["avg_duration_s"] or 0, 1),
            "recent_new_count": aggregate["new_count"] or 0,
        })
        latest_sources.append(item)

    report_rows = []
    model_totals: dict[str, dict] = defaultdict(lambda: {
        "calls": 0, "reports": 0, "total_tokens": 0, "cache_hit_tokens": 0,
        "cache_miss_tokens": 0, "output_tokens": 0, "estimated_cost_cny": 0.0,
        "unpriced_calls": 0,
    })

    def add_usage(model: str, usage: dict, *, report_call: bool = False,
                  calls: int = 1, snapshots: list[dict] | None = None) -> dict:
        cost = (billing.summarize_calls(snapshots) if snapshots
                else _usage_cost(model, usage, config))
        if not snapshots and cost["unpriced_calls"]:
            cost["unpriced_calls"] = calls
        totals = model_totals[model or "unknown"]
        totals["calls"] += len(snapshots) if snapshots else calls
        totals["reports"] += int(report_call)
        for field in ("total_tokens", "cache_hit_tokens", "cache_miss_tokens",
                      "output_tokens"):
            totals[field] += cost[field]
        totals["estimated_cost_cny"] += cost["estimated_cost_cny"] or 0
        totals["unpriced_calls"] += cost["unpriced_calls"]
        return cost

    fallback_count = 0
    rows = conn.execute(
        "SELECT period,period_key,generated_at,model,content_json FROM reports "
        "ORDER BY generated_at DESC"
    ).fetchall()
    for row in rows:
        try:
            content = json.loads(row["content_json"])
        except (TypeError, ValueError):
            content = {}
        fallback = bool(content.get("fallback"))
        fallback_count += int(fallback)
        if not fallback:
            report_counts[row["period"]] += 1
        usage = content.get("llm_usage") or {}
        snapshots = content.get("llm_calls") or []
        request_model = content.get("llm_requested_model") or row["model"] or ""
        cost = (add_usage(
                    request_model, usage, report_call=True,
                    calls=max(1, int(content.get("llm_attempts") or 1)),
                    snapshots=snapshots,
                )
                if usage or snapshots else _usage_cost(request_model, usage, config))
        entry = {"period": row["period"], "period_key": row["period_key"],
                 "generated_at": row["generated_at"], "model": row["model"],
                 "fallback": fallback, "requested_model": request_model,
                 "response_models": content.get("llm_response_models") or [], **cost}
        report_rows.append(entry)
    analyses = []
    try:
        from virt_report.kvm_forum import ANALYSIS_PATH
        analysis = json.loads(ANALYSIS_PATH.read_text(encoding="utf-8"))
        usage = analysis.get("usage") or {}
        if usage:
            model = analysis.get("model") or config.llm.weekly_model
            cost = add_usage(model, usage, snapshots=analysis.get("llm_calls"))
            analyses.append({"name": "KVM Forum 2010—2025", "model": model, **cost})
    except (FileNotFoundError, TypeError, ValueError):
        pass
    for totals in model_totals.values():
        totals["estimated_cost_cny"] = round(totals["estimated_cost_cny"], 4)
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "counts": counts, "report_counts": dict(report_counts),
        "scheduler_runs": scheduler_runs,
        "scheduler_failures_24h": scheduler_failures_24h,
        "sources": latest_sources, "reports": report_rows[:30], "analyses": analyses,
        "models": dict(model_totals), "fallback_count": fallback_count,
        "estimated_cost_cny": round(sum(
            model["estimated_cost_cny"] for model in model_totals.values()
        ), 4),
        "unpriced_calls": sum(model["unpriced_calls"] for model in model_totals.values()),
        "pricing_note": ("新请求按请求开始时间保存峰谷费率，历史报告保留原估算口径。"
                         "费用仅统计已记录且可计价的用量；缺少价格或用量的请求单独标注，不等同于账单。"),
    }
