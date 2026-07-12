#!/usr/bin/env bash
# 仅采集 + 重建线程 (不生成报告)。
# lore.kernel.org 的 /new.atom 每次只返回最近 25 条且无翻页，故需频繁采集累积。
# 建议 crontab: 7 */4 * * *  /path/to/virt_report/scripts/run_fetch.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
cd "$ROOT"

VENV="$ROOT/.venv/bin/python"
[ -x "$ROOT/.venv/bin/virt-report" ] && BIN="$ROOT/.venv/bin/virt-report" || BIN="$VENV -m virt_report.cli"

$BIN fetch --since-days 4 --max-pages 5
