#!/usr/bin/env bash
# 每日采集 + 生成日报。建议 crontab: 17 9 * * *  /path/to/virt_report/scripts/run_daily.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
cd "$ROOT"

# 加载 .env (若存在)
if [ -f "$ROOT/.env" ]; then
  set -a; . "$ROOT/.env"; set +a
fi

VENV="$ROOT/.venv/bin/python"
[ -x "$ROOT/.venv/bin/virt-report" ] && BIN="$ROOT/.venv/bin/virt-report" || BIN="$VENV -m virt_report.cli"

# 默认生成"昨天"的日报 (本地时区); 也可传日期参数: run_daily.sh 2026-07-12
DATE="${1:-}"
if [ -z "$DATE" ]; then
  DATE="$($VENV -c 'from datetime import datetime,timedelta; from zoneinfo import ZoneInfo; print((datetime.now(ZoneInfo("Asia/Shanghai"))-timedelta(days=1)).strftime("%Y-%m-%d"))')"
fi

$BIN daily "$DATE"
