#!/usr/bin/env bash
# 生成上一个月的月报。建议 crontab: 35 0 1 * *  /path/to/virt_report/scripts/run_monthly.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
cd "$ROOT"

if [ -f "$ROOT/.env" ]; then set -a; . "$ROOT/.env"; set +a; fi

VENV="$ROOT/.venv/bin/python"
[ -x "$ROOT/.venv/bin/virt-report" ] && BIN="$ROOT/.venv/bin/virt-report" || BIN="$VENV -m virt_report.cli"

# 上一个月的键 (如 2026-06)
MONTH="$($VENV -c '
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from virt_report.summarize import periods
now = datetime.now(ZoneInfo("Asia/Shanghai")).replace(day=1) - timedelta(days=1)
print(periods.period_key_for("monthly", now))')"

$BIN monthly "$MONTH" --no-fetch
