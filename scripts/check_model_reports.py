"""Opt-in live model check using in-memory database copies; never publishes reports.

Run with .venv/bin/python scripts/check_model_reports.py --live.
By default this makes paid API calls for the latest stored daily/weekly reports.
Artifacts remain in ignored data/model-checks/ and are not static site exports.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone

from virt_report.config import load_config
from virt_report.render import render
from virt_report.summarize import billing, report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="authorize selected paid report checks")
    parser.add_argument("--period", action="append", choices=("daily", "weekly", "monthly"),
                        help="repeat to select periods; defaults to daily and weekly")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--model", default="deepseek-flash")
    args = parser.parse_args()
    if not args.live:
        parser.error("Pass --live to explicitly enable paid API calls.")
    config = load_config(args.config)
    if not config.llm.api_key:
        parser.error("Configured API key is not available.")
    source_uri = config.db_path.resolve().as_uri() + "?mode=ro"

    def originals():
        with closing(sqlite3.connect(source_uri, uri=True)) as source:
            return source.execute(
                "SELECT period,period_key,model,generated_at,content_json FROM reports "
                "ORDER BY period,period_key"
            ).fetchall()

    before = originals()
    jobs = []
    for period in dict.fromkeys(args.period or ["daily", "weekly"]):
        candidates = [row for row in before if row[0] == period
                      and not json.loads(row[4]).get("fallback")]
        if not candidates:
            parser.error(f"No stored {period} report available for comparison.")
        jobs.append(max(candidates, key=lambda row: row[1]))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = config.db_path.parent / "model-checks" / stamp
    output.mkdir(parents=True, exist_ok=False)
    config = replace(config, llm=replace(config.llm, daily_model=args.model,
                     weekly_model=args.model, monthly_model=args.model),
                     render=replace(config.render, output_dir=output))
    render.export_brand_assets(output)
    print(f"Isolated output: {output}", flush=True)

    def check(row):
        period, key = row[:2]
        started = time.monotonic()
        with closing(sqlite3.connect(source_uri, uri=True)) as source, \
                closing(sqlite3.connect(":memory:")) as scratch:
            source.backup(scratch)
            scratch.row_factory = sqlite3.Row
            content = report.generate(scratch, config, period, key, publish_fallback=False)
        artifact = output / f"{period}-{key}.json"
        artifact.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
        render.render_report(config, content)
        old = json.loads(row[4])
        refs = {t["ref"]: t["url"] for t in content["top_threads"]}
        items = [item for section in content["sections"] for item in section["items"]]
        valid = not content["fallback"] and bool(items) and all(
            item.get("url") == refs.get(item.get("ref")) for item in items
        )
        result = {"period": period, "key": key, "valid": valid,
                  "seconds": round(time.monotonic() - started, 1),
                  "model": content["model"], "response_models": content["llm_response_models"],
                  "items": len(items), "previous_items": sum(len(s.get("items", [])) for s in old.get("sections", [])),
                  "period_analysis": len(content["period_analysis"]),
                  "calls": len(content["llm_calls"]),
                  **billing.summarize_calls(content["llm_calls"])}
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return result

    results = []
    with ThreadPoolExecutor(max_workers=min(3, len(jobs))) as pool:
        for future in as_completed([pool.submit(check, row) for row in jobs]):
            results.append(future.result())
    unchanged = originals() == before
    summary = {"source_reports_unchanged": unchanged, "results": results}
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    if not unchanged or not all(result["valid"] for result in results):
        raise SystemExit("Model check failed; inspect the isolated artifacts.")
    print(f"All {len(results)} selected reports passed structural/link checks; original reports unchanged.")


if __name__ == "__main__":
    main()
