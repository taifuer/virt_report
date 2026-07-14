"""容器与常驻进程使用的轻量周期调度器。"""
from __future__ import annotations

import logging
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from virt_report.config import Config
from virt_report import db
from virt_report.summarize import periods

log = logging.getLogger(__name__)


def _field_matches(field: str, value: int) -> bool:
    """匹配常用 cron 字段：*、*/N、数字、逗号列表和闭区间。"""
    for part in field.split(","):
        if part == "*":
            return True
        if part.startswith("*/"):
            return value % int(part[2:]) == 0
        if "-" in part:
            start, end = map(int, part.split("-", 1))
            if start <= value <= end:
                return True
        elif int(part) == value:
            return True
    return False


def cron_matches(expression: str, now: datetime) -> bool:
    """判断带时区的 datetime 是否命中五字段 cron 表达式。"""
    fields = expression.split()
    if len(fields) != 5:
        raise ValueError(f"无效 cron 表达式: {expression}")
    minute, hour, day, month, weekday = fields
    cron_weekday = (now.weekday() + 1) % 7  # cron: 0=周日, 1=周一
    return all((
        _field_matches(minute, now.minute),
        _field_matches(hour, now.hour),
        _field_matches(day, now.day),
        _field_matches(month, now.month),
        _field_matches(weekday, cron_weekday),
    ))


def scheduled_commands(config: Config, now: datetime) -> list[tuple[str, list[str]]]:
    """返回当前分钟应执行的命令，报告键均指向刚结束的周期。"""
    jobs: list[tuple[str, str, list[str]]] = [
        ("fetch", config.schedule.fetch_cron,
         ["fetch", "--since-days", "4", "--max-pages", "8"]),
        ("daily", config.schedule.daily_cron, [
            "daily", periods.period_key_for("daily", now - timedelta(days=1)),
        ]),
        ("weekly", config.schedule.weekly_cron, [
            "weekly", periods.period_key_for("weekly", now - timedelta(days=7)), "--no-fetch",
        ]),
        ("monthly", config.schedule.monthly_cron, [
            "monthly",
            periods.period_key_for("monthly", now.replace(day=1) - timedelta(days=1)),
            "--no-fetch",
        ]),
    ]
    return [(name, command) for name, expression, command in jobs
            if cron_matches(expression, now)]


def due_commands(config: Config, since: datetime,
                 now: datetime) -> list[tuple[str, list[str], datetime]]:
    """返回检查间隔内错过或刚到点的任务，每类只保留最近一次。"""
    due: dict[str, tuple[list[str], datetime]] = {}
    cursor = since.replace(second=0, microsecond=0) + timedelta(minutes=1)
    end = now.replace(second=0, microsecond=0)
    while cursor <= end:
        for name, command in scheduled_commands(config, cursor):
            due[name] = (command, cursor)
        cursor += timedelta(minutes=1)
    return [(name, command, scheduled_at)
            for name, (command, scheduled_at) in due.items()]


def _load_state(path: Path) -> set[str]:
    try:
        return set(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return set()


def _save_state(path: Path, completed: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(completed)[-200:], ensure_ascii=False), encoding="utf-8")


def _report_exists(config: Config, name: str, command: list[str]) -> bool:
    if name not in {"daily", "weekly", "monthly"}:
        return False
    conn = db.connect(config.db_path)
    try:
        return db.get_report(conn, name, command[1]) is not None
    finally:
        conn.close()


def run_forever(config: Config, config_path: str | None = None) -> None:
    """每分钟检查任务；失败任务不影响后续调度。"""
    timezone = ZoneInfo(config.timezone)
    state_path = config.db_path.parent / "scheduler_state.json"
    completed = _load_state(state_path)
    last_check = datetime.now(timezone) - timedelta(days=1)
    log.info("自动调度已启动 (%s)", config.timezone)
    while True:
        now = datetime.now(timezone).replace(second=0, microsecond=0)
        jobs = due_commands(config, last_check, now)
        last_check = now
        exported_report = False
        for name, command, scheduled_at in jobs:
            identity = (f"fetch:{scheduled_at.isoformat()}" if name == "fetch"
                        else f"{name}:{command[1]}")
            if identity in completed:
                continue
            if _report_exists(config, name, command):
                log.info("报告已存在，跳过自动重建: %s/%s", name, command[1])
                completed.add(identity)
                _save_state(state_path, completed)
                continue
            argv = [sys.executable, "-m", "virt_report.cli"]
            if config_path:
                argv.extend(["--config", config_path])
            argv.extend(command)
            log.info("执行自动任务 %s: %s", name, " ".join(command))
            result = subprocess.run(argv, check=False)
            if result.returncode == 0:
                completed.add(identity)
                _save_state(state_path, completed)
                exported_report = exported_report or name in {"daily", "weekly", "monthly"}
            else:
                log.error("自动任务 %s 失败，退出码 %d", name, result.returncode)
        if exported_report and config.schedule.auto_export:
            base_argv = [sys.executable, "-m", "virt_report.cli"]
            if config_path:
                base_argv.extend(["--config", config_path])
            subprocess.run(base_argv + ["index"], check=False)
        time.sleep(20)
