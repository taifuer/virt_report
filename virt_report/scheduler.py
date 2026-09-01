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
from virt_report.locking import process_lock
from virt_report.summarize import periods

log = logging.getLogger(__name__)
REPORT_JOBS = {"daily", "weekly", "monthly"}


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
            "--no-fetch", "--require-ai",
        ]),
        ("weekly", config.schedule.weekly_cron, [
            "weekly", periods.period_key_for("weekly", now - timedelta(days=7)),
            "--no-fetch", "--require-ai",
        ]),
        ("monthly", config.schedule.monthly_cron, [
            "monthly",
            periods.period_key_for("monthly", now.replace(day=1) - timedelta(days=1)),
            "--no-fetch", "--require-ai",
        ]),
        ("backup", config.schedule.backup_cron, [
            "backup", str(
                config.db_path.parent / "backups" / f"auto-{now:%Y-%m-%d}.db.gz"
            ),
            "--keep-days", str(config.schedule.backup_keep_days),
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
    if name not in REPORT_JOBS:
        return False
    conn = db.connect(config.db_path)
    try:
        row = db.get_report(conn, name, command[1])
        if row is None:
            return False
        try:
            return not bool(json.loads(row["content_json"]).get("fallback"))
        except (TypeError, ValueError):
            return False
    finally:
        conn.close()


def run_forever(config: Config, config_path: str | None = None) -> None:
    """每分钟检查任务；首次启动不回放历史任务，重启时补最近一天。"""
    timezone = ZoneInfo(config.timezone)
    state_path = config.db_path.parent / "scheduler_state.json"
    completed = _load_state(state_path)
    now = datetime.now(timezone)
    # 全新部署不自动触发昂贵的历史采集；已有状态的重启才补跑最近一天。
    last_check = now - (timedelta(days=1) if state_path.exists()
                        else timedelta(minutes=1))
    scheduler_lock = config.db_path.parent / "scheduler.lock"
    with process_lock(scheduler_lock):
        conn = db.connect(config.db_path)
        try:
            interrupted = db.interrupt_stale_scheduler_runs(conn)
        finally:
            conn.close()
        if interrupted:
            log.warning("已关闭重启前未完成的调度记录: %d", interrupted)
        _run_loop(config, config_path, timezone, state_path, completed, last_check)


def _parse_state_time(value: str | None, timezone: ZoneInfo,
                      default: datetime) -> datetime:
    try:
        parsed = datetime.fromisoformat(value or "")
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone)
        return parsed.astimezone(timezone)
    except ValueError:
        return default


def _restore_report_retries(
    config: Config, timezone: ZoneInfo, now: datetime,
) -> tuple[
    dict[str, tuple[str, list[str], datetime, int, datetime]],
    set[str],
]:
    """恢复重启前尚未完成的报告；崩溃时的 running 状态立即进入下一次尝试。"""
    restored: dict[str, tuple[str, list[str], datetime, int, datetime]] = {}
    exhausted: set[str] = set()
    conn = db.connect(config.db_path)
    try:
        for row in db.list_report_generation_states(conn):
            identity = f"{row['period']}:{row['period_key']}"
            if row["status"] == "failed":
                exhausted.add(identity)
                continue
            if row["status"] not in {"running", "retry_wait"}:
                continue
            attempt = int(row["attempt"] or 0)
            if attempt >= config.schedule.retry_limit:
                db.set_report_generation_state(
                    conn, row["period"], row["period_key"], status="failed",
                    attempt=attempt, scheduled_at=row["scheduled_at"],
                    error="调度进程中断且已达到重试上限",
                )
                exhausted.add(identity)
                continue
            scheduled_at = _parse_state_time(row["scheduled_at"], timezone, now)
            retry_at = (_parse_state_time(row["retry_at"], timezone, now)
                        if row["status"] == "retry_wait" else now)
            command = [row["period"], row["period_key"], "--no-fetch", "--require-ai"]
            restored[identity] = (
                row["period"], command, scheduled_at, attempt, retry_at,
            )
    finally:
        conn.close()
    return restored, exhausted


def _run_loop(config: Config, config_path: str | None, timezone: ZoneInfo,
              state_path: Path, completed: set[str], last_check: datetime) -> None:
    log.info("自动调度已启动 (%s, catchup=%s)", config.timezone,
             "24h" if state_path.exists() else "current-minute")
    pending, exhausted = _restore_report_retries(
        config, timezone, datetime.now(timezone).replace(second=0, microsecond=0)
    )
    if exhausted:
        completed.update(exhausted)
        _save_state(state_path, completed)
    while True:
        now = datetime.now(timezone).replace(second=0, microsecond=0)
        jobs = due_commands(config, last_check, now)
        last_check = now
        jobs.extend((name, command, scheduled_at)
                    for _identity, (name, command, scheduled_at, _attempt, retry_at)
                    in list(pending.items()) if retry_at <= now)
        exported_report = False
        cycle_identities: set[str] = set()
        for name, command, scheduled_at in jobs:
            identity = (f"fetch:{scheduled_at.isoformat()}" if name == "fetch"
                        else f"{name}:{command[1]}")
            # 重启补跑与持久化重试可能指向同一报告；同一轮只执行一次，并尊重等待期。
            if identity in cycle_identities:
                continue
            cycle_identities.add(identity)
            retry = pending.get(identity)
            if retry and retry[4] > now:
                continue
            if identity in completed:
                pending.pop(identity, None)
                continue
            if _report_exists(config, name, command):
                log.info("报告已存在，跳过自动重建: %s/%s", name, command[1])
                completed.add(identity)
                pending.pop(identity, None)
                if name in REPORT_JOBS:
                    conn = db.connect(config.db_path)
                    try:
                        db.clear_report_generation_state(conn, name, command[1])
                    finally:
                        conn.close()
                _save_state(state_path, completed)
                continue
            argv = [sys.executable, "-m", "virt_report.cli"]
            if config_path:
                argv.extend(["--config", config_path])
            argv.extend(command)
            attempt = pending.get(identity, (None, None, None, 0, None))[3] + 1
            log.info("执行自动任务 %s (attempt=%d): %s", name, attempt,
                     " ".join(command))
            conn = db.connect(config.db_path)
            try:
                if name in REPORT_JOBS:
                    db.set_report_generation_state(
                        conn, name, command[1], status="running", attempt=attempt,
                        scheduled_at=scheduled_at.isoformat(),
                    )
                run_id = db.start_scheduler_run(
                    conn, identity=identity, job_name=name,
                    scheduled_at=scheduled_at.isoformat(), attempt=attempt,
                )
            finally:
                conn.close()
            result = None
            error = None
            try:
                result = subprocess.run(
                    argv, check=False, timeout=config.schedule.job_timeout_seconds
                )
                healthy = result.returncode == 0 and (
                    name not in REPORT_JOBS or
                    _report_exists(config, name, command)
                )
                status = "success" if healthy else (
                    "fallback" if result.returncode == 0 else "failed"
                )
            except subprocess.TimeoutExpired:
                healthy = False
                status = "timeout"
                error = f"任务超过 {config.schedule.job_timeout_seconds} 秒"
            conn = db.connect(config.db_path)
            try:
                db.finish_scheduler_run(
                    conn, run_id, status=status,
                    exit_code=result.returncode if result else None, error=error,
                )
            finally:
                conn.close()
            if healthy:
                completed.add(identity)
                pending.pop(identity, None)
                _save_state(state_path, completed)
                exported_report = exported_report or name in REPORT_JOBS
            else:
                exit_code = result.returncode if result else -1
                log.error("自动任务 %s 未完成 (%s, exit=%d)", name, status, exit_code)
                # 先移除旧条目，避免达到上限时反复执行同一个 attempt。
                pending.pop(identity, None)
                if attempt < config.schedule.retry_limit:
                    retry_at = (datetime.now(timezone).replace(microsecond=0) +
                                timedelta(seconds=config.schedule.retry_delay_seconds))
                    pending[identity] = (name, command, scheduled_at, attempt, retry_at)
                    if name in REPORT_JOBS:
                        conn = db.connect(config.db_path)
                        try:
                            db.set_report_generation_state(
                                conn, name, command[1], status="retry_wait",
                                attempt=attempt, scheduled_at=scheduled_at.isoformat(),
                                retry_at=retry_at.isoformat(), error=error or status,
                            )
                        finally:
                            conn.close()
                    log.info("任务 %s 将在 %s 重试", name, retry_at.isoformat())
                else:
                    if name in REPORT_JOBS:
                        conn = db.connect(config.db_path)
                        try:
                            db.set_report_generation_state(
                                conn, name, command[1], status="failed",
                                attempt=attempt, scheduled_at=scheduled_at.isoformat(),
                                error=error or status,
                            )
                        finally:
                            conn.close()
                    completed.add(identity)
                    _save_state(state_path, completed)
        if exported_report and config.schedule.auto_export:
            base_argv = [sys.executable, "-m", "virt_report.cli"]
            if config_path:
                base_argv.extend(["--config", config_path])
            subprocess.run(base_argv + ["index"], check=False)
        time.sleep(20)
