"""运行指标、采集状态与 LLM 成本估算。"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone

from virt_report.config import Config


def _usage_cost(model: str, usage: dict, config: Config) -> dict:
    rates = config.llm.pricing_cny.get(model) or {}
    prompt = int(usage.get("prompt_tokens") or 0)
    details = usage.get("prompt_tokens_details") or {}
    cache_hit = int(usage.get("prompt_cache_hit_tokens") or
                    details.get("cached_tokens") or 0)
    cache_miss = int(usage.get("prompt_cache_miss_tokens") or max(0, prompt - cache_hit))
    output = int(usage.get("completion_tokens") or 0)
    cost = (
        cache_hit * float(rates.get("cache_hit", 0)) +
        cache_miss * float(rates.get("cache_miss", 0)) +
        output * float(rates.get("output", 0))
    ) / 1_000_000
    return {
        "prompt_tokens": prompt, "cache_hit_tokens": cache_hit,
        "cache_miss_tokens": cache_miss, "output_tokens": output,
        "total_tokens": int(usage.get("total_tokens") or prompt + output),
        "estimated_cost_cny": round(cost, 6), "rates": rates,
    }


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
    })

    def add_usage(model: str, usage: dict, *, report_call: bool = False,
                  calls: int = 1) -> dict:
        cost = _usage_cost(model, usage, config)
        totals = model_totals[model or "unknown"]
        totals["calls"] += calls
        totals["reports"] += int(report_call)
        for field in ("total_tokens", "cache_hit_tokens", "cache_miss_tokens",
                      "output_tokens"):
            totals[field] += cost[field]
        totals["estimated_cost_cny"] += cost["estimated_cost_cny"]
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
        cost = (add_usage(
                    row["model"] or "", usage, report_call=True,
                    calls=max(1, int(content.get("llm_attempts") or 1)),
                )
                if usage else _usage_cost(row["model"] or "", usage, config))
        entry = {"period": row["period"], "period_key": row["period_key"],
                 "generated_at": row["generated_at"], "model": row["model"],
                 "fallback": fallback, **cost}
        report_rows.append(entry)
    analyses = []
    try:
        from virt_report.kvm_forum import ANALYSIS_PATH
        analysis = json.loads(ANALYSIS_PATH.read_text(encoding="utf-8"))
        usage = analysis.get("usage") or {}
        if usage:
            model = analysis.get("model") or config.llm.weekly_model
            cost = add_usage(model, usage)
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
        "pricing_note": "依据 config.yaml 中人民币/百万 tokens 单价估算，不等同于账单。",
    }
