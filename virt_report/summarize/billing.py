"""Per-request cost estimates. Store rates, not credentials or model reasoning."""
from __future__ import annotations

import math
from datetime import datetime
from zoneinfo import ZoneInfo

from virt_report.config import LLMConfig

PRICE_SOURCE = "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/"
FLASH_ALIASES = {"deepseek-v4-flash", "deepseek-v4-flash-vision-exp"}


def usage_totals(usage: dict) -> dict:
    """Normalize both DeepSeek and OpenAI-compatible cache usage fields."""
    prompt = max(0, int(usage.get("prompt_tokens") or 0))
    details = usage.get("prompt_tokens_details") or {}
    hit = usage.get("prompt_cache_hit_tokens")
    if hit is None:
        hit = details.get("cached_tokens", 0)
    hit = min(prompt, max(0, int(hit or 0)))
    miss = usage.get("prompt_cache_miss_tokens")
    miss = max(0, int(miss)) if miss is not None else prompt - hit
    output = max(0, int(usage.get("completion_tokens") or 0))
    return {
        "prompt_tokens": prompt, "cache_hit_tokens": hit,
        "cache_miss_tokens": miss, "output_tokens": output,
        "total_tokens": int(usage.get("total_tokens") or prompt + output),
    }


def estimate(usage: dict, rates: dict) -> dict:
    """An unknown or incomplete tariff is unpriced, never a zero-cost request."""
    totals = usage_totals(usage)
    try:
        normalized = {key: float(rates[key]) for key in ("cache_hit", "cache_miss", "output")}
        if not all(math.isfinite(value) and value >= 0 for value in normalized.values()):
            raise ValueError("Invalid rate")
    except (KeyError, TypeError, ValueError):
        return {**totals, "rates": {}, "estimated_cost_cny": None}
    cost = (totals["cache_hit_tokens"] * normalized["cache_hit"] +
            totals["cache_miss_tokens"] * normalized["cache_miss"] +
            totals["output_tokens"] * normalized["output"]) / 1_000_000
    return {**totals, "rates": normalized, "estimated_cost_cny": round(cost, 8)}


def snapshot_call(call: dict, config: LLMConfig) -> dict:
    """Freeze request-time rates; response.model is recorded, not guessed.

    A request spanning a pricing boundary uses its start time for estimation.
    Official invoices remain authoritative; missing usage cannot be priced.
    """
    model = call.get("requested_model", "")
    tariff_model = "deepseek-flash" if model in FLASH_ALIASES else model
    schedule = config.pricing_schedule_cny.get(tariff_model)
    rates = config.pricing_cny.get(model, {})
    tariff: dict = {"pricing_tier": "flat", "pricing_model": model}
    if schedule:
        # Invalid time or configuration must not prevent publishing a valid report.
        rates = {}
        tariff = {"pricing_tier": "unknown", "pricing_model": tariff_model}
        try:
            started = datetime.fromisoformat(call["started_at"])
            effective = datetime.fromisoformat(schedule["effective_from"])
            if started.tzinfo is None or effective.tzinfo is None:
                raise ValueError("Timezone required")
            if started >= effective:
                local = started.astimezone(ZoneInfo(schedule["timezone"]))
                hour = local.hour + local.minute / 60 + local.second / 3600
                peak = local.weekday() in schedule["peak_weekdays"] and any(
                    start <= hour < end for start, end in schedule["peak_hours"]
                )
                tier = "peak" if peak else "off_peak"
                rates = schedule[tier]
                tariff.update(pricing_tier=tier, pricing_timezone=schedule["timezone"],
                              pricing_effective_from=schedule["effective_from"],
                              pricing_source=PRICE_SOURCE)
            else:
                rates = config.pricing_cny.get(model, {})
                tariff.update(pricing_tier="legacy", pricing_model=model)
        except (KeyError, TypeError, ValueError):
            pass
    usage = call.get("usage") or {}
    cost = estimate(usage, rates)
    if usage.get("prompt_tokens") is None or usage.get("completion_tokens") is None:
        cost["estimated_cost_cny"] = None
    return {**call, **tariff, **cost}


def summarize_calls(calls: list[dict]) -> dict:
    """Sum stored estimates, including retries with different tariffs."""
    fields = ("prompt_tokens", "cache_hit_tokens", "cache_miss_tokens", "output_tokens", "total_tokens")
    totals = dict.fromkeys(fields, 0)
    known = []
    for call in calls:
        usage = usage_totals(call.get("usage") or {})
        for key in fields:
            totals[key] += usage[key]
        if call.get("estimated_cost_cny") is not None:
            known.append(call["estimated_cost_cny"])
    return {**totals, "estimated_cost_cny": round(sum(known), 8) if known else None,
            "unpriced_calls": len(calls) - len(known), "rates": {},
            "pricing_basis": "snapshot"}
