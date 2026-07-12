"""virt-report 命令行入口。

用法:
  virt-report fetch [--since-days N]       # 采集 + 重建线程
  virt-report daily [YYYY-MM-DD]           # 全链路生成某日日报 (默认今天)
  virt-report index                        # 渲染首页索引
"""
from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from virt_report import db
from virt_report.collectors import lore, lore_git, gitlab, hyperkitty, mailarchive, mbox
from virt_report.config import Config, load_config
from virt_report.processing import threads
from virt_report.render import render
from virt_report.summarize import periods, report

log = logging.getLogger(__name__)


def _today_local(tz_name: str) -> str:
    return datetime.now(ZoneInfo(tz_name)).strftime("%Y-%m-%d")


def _fetch_all(conn, config: Config, since: datetime, max_pages: int = 8) -> None:
    """采集所有源 (since 为 UTC datetime)。"""
    since_iso = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def run(source_kind: str, project: str, collector, source) -> None:
        started = db.now_utc_iso()
        try:
            count = collector.fetch(conn, source, since=since, max_pages=max_pages)
            state = db.get_fetch_state(conn, source_kind, project)
            complete = state.get("last_seen_id") != "partial"
            db.record_fetch_run(
                conn, source=source_kind, project=project, started_at=started,
                success=True, complete=complete, new_count=count,
                requested_since=since_iso,
            )
        except Exception as exc:
            log.exception("采集失败，继续其他数据源: %s/%s", source_kind, project)
            db.record_fetch_run(
                conn, source=source_kind, project=project, started_at=started,
                success=False, complete=False, new_count=0,
                requested_since=since_iso, error=str(exc)[:1000],
            )

    for ml in config.sources.mailing_lists:
        if ml.type == "mbox":
            run("ml", ml.name, mbox, ml)
        elif ml.type == "mailarchive":
            run("ml", ml.name, mailarchive, ml)
        elif ml.type == "lore":
            run("ml", ml.name, lore, ml)
        elif ml.type == "lore_git":
            run("ml", ml.name, lore_git, ml)
        elif ml.type == "hyperkitty":
            run("ml", ml.name, hyperkitty, ml)
    for gl in config.sources.gitlab:
        run("gitlab", gl.name, gitlab, gl)
    threads.rebuild_threads(conn)


def _list_reports(conn, period: str) -> list[dict]:
    rs = conn.execute(
        "SELECT period_key, generated_at, item_count, model FROM reports "
        "WHERE period=? ORDER BY period_key DESC", (period,),
    ).fetchall()
    return [{"period_key": r["period_key"], "generated_at": r["generated_at"],
             "item_count": r["item_count"] or 0, "model": r["model"]} for r in rs]


def _render_index(config: Config, conn) -> None:
    from virt_report.render.render import build_calendar

    daily_rows = conn.execute(
        "SELECT period_key FROM reports WHERE period='daily'"
    ).fetchall()
    daily_keys = {r["period_key"] for r in daily_rows}
    months = sorted({k[:7] for k in daily_keys})  # YYYY-MM
    if not months:
        now = datetime.now(ZoneInfo(config.timezone))
        months = [now.strftime("%Y-%m")]

    weekly = _list_reports(conn, "weekly")
    monthly = _list_reports(conn, "monthly")
    daily = _list_reports(conn, "daily")[:15]
    source_health = []
    for source, project, label in (
        ("ml", "qemu-devel", "QEMU 邮件"), ("ml", "kvm", "KVM 邮件"),
        ("ml", "libvir-list", "Libvirt 邮件"), ("gitlab", "qemu", "QEMU GitLab"),
        ("gitlab", "libvirt", "Libvirt GitLab"),
    ):
        row = conn.execute(
            "SELECT success,complete,coverage_end,error FROM fetch_runs "
            "WHERE source=? AND project=? ORDER BY id DESC LIMIT 1",
            (source, project),
        ).fetchone()
        source_health.append({
            "label": label, "ok": bool(row and row["success"] and row["complete"]),
            "coverage_end": row["coverage_end"] if row else None,
            "error": row["error"] if row else "尚无采集记录",
        })

    # 枚举 [min..max] 所有月份
    lo_y, lo_m = map(int, months[0].split("-"))
    hi_y, hi_m = map(int, months[-1].split("-"))
    all_months: list[str] = []
    yy, mm = lo_y, lo_m
    while (yy < hi_y) or (yy == hi_y and mm <= hi_m):
        all_months.append(f"{yy:04d}-{mm:02d}")
        mm += 1
        if mm > 12:
            mm = 1
            yy += 1
    rendered = set(all_months)

    for mk in all_months:
        ctx = {"cal": build_calendar(mk, daily_keys), "weekly": weekly,
               "monthly": monthly, "daily": daily, "rendered_months": rendered,
               "source_health": source_health}
        render.render_index(config, ctx, filename=f"index-{mk}.html")
    # 主 index.html = 最近一个月
    ctx = {"cal": build_calendar(months[-1], daily_keys), "weekly": weekly,
           "monthly": monthly, "daily": daily, "rendered_months": rendered,
           "source_health": source_health}
    render.render_index(config, ctx, filename="index.html")


def cmd_fetch(args, config: Config) -> None:
    conn = db.connect(config.db_path)
    try:
        since = datetime.now(timezone.utc) - timedelta(days=args.since_days)
        _fetch_all(conn, config, since=since, max_pages=args.max_pages)
    finally:
        conn.close()


def _period_key(args, config: Config, period: str) -> str:
    """默认取上一个完整周期 (日报=昨天, 周报=上一完整周, 月报=上月)。"""
    if getattr(args, "key", None):
        return args.key
    now = datetime.now(ZoneInfo(config.timezone))
    if period == "daily":
        return periods.period_key_for("daily", now - timedelta(days=1))
    if period == "weekly":
        return periods.period_key_for("weekly", now - timedelta(weeks=1))
    if period == "monthly":
        first_of_month = now.replace(day=1)
        return periods.period_key_for("monthly", first_of_month - timedelta(days=1))
    return periods.period_key_for(period, now)


def _nav(conn, period: str, key: str) -> dict:
    """取同周期相邻已生成报告的导航 (prev/next)，无则对应项为 None。"""
    prev = conn.execute(
        "SELECT period_key FROM reports WHERE period=? AND period_key<? "
        "ORDER BY period_key DESC LIMIT 1", (period, key),
    ).fetchone()
    nxt = conn.execute(
        "SELECT period_key FROM reports WHERE period=? AND period_key>? "
        "ORDER BY period_key ASC LIMIT 1", (period, key),
    ).fetchone()
    def item(r):
        if not r:
            return None
        k = r["period_key"]
        return {"label": periods.label(period, k), "url": f"{period}/{k}.html"}
    return {"prev": item(prev), "next": item(nxt)}


def _run_period(args, config: Config, period: str) -> None:
    key = _period_key(args, config, period)
    conn = db.connect(config.db_path)
    try:
        if not args.no_fetch:
            # 采集窗口覆盖目标周期起点 (留 7 天 buffer, mbox 会自动取整月)
            period_start, _ = periods.window(period, key, config.timezone)
            since = period_start - timedelta(days=7)
            _fetch_all(conn, config, since=since, max_pages=args.max_pages)
        else:
            threads.rebuild_threads(conn)
        content = report.generate(conn, config, period, key)
        path = render.render_report(config, content, nav=_nav(conn, period, key))
        _render_index(config, conn)
        print(f"{period}报已生成: {path}")
        if period == "daily":
            ic = sum(len(s.get("items", [])) for s in content.get("sections", []))
            print(f"  总览 {len(content.get('overview', []))} / 项目 {len(content.get('sections',[]))} / 动态 {ic} / "
                  f"模型 {content['model']} (降级={content['fallback']})")
        else:
            ic = sum(len(s.get("items", [])) for s in content.get("sections", []))
            print(f"  总览 {len(content.get('overview', []))} / "
                  f"项目 {len(content.get('sections', []))} / 动态 {ic} / "
                  f"模型 {content['model']} (降级={content['fallback']})")
    finally:
        conn.close()


def cmd_daily(args, config: Config) -> None:
    _run_period(args, config, "daily")


def cmd_weekly(args, config: Config) -> None:
    _run_period(args, config, "weekly")


def cmd_monthly(args, config: Config) -> None:
    _run_period(args, config, "monthly")


def cmd_index(args, config: Config) -> None:
    conn = db.connect(config.db_path)
    try:
        _render_index(config, conn)
        print("首页索引已渲染")
    finally:
        conn.close()


def cmd_status(args, config: Config) -> None:
    """显示各数据源覆盖范围与最近采集结果。"""
    del args
    conn = db.connect(config.db_path)
    try:
        rows = conn.execute(
            """
            SELECT i.source, i.project, COUNT(*) AS items,
                   MIN(i.activity_at) AS coverage_start,
                   MAX(i.activity_at) AS coverage_end,
                   (SELECT success FROM fetch_runs f
                    WHERE f.source=i.source AND f.project=i.project
                    ORDER BY f.id DESC LIMIT 1) AS last_success,
                   (SELECT complete FROM fetch_runs f
                    WHERE f.source=i.source AND f.project=i.project
                    ORDER BY f.id DESC LIMIT 1) AS last_complete,
                   (SELECT error FROM fetch_runs f
                    WHERE f.source=i.source AND f.project=i.project
                    ORDER BY f.id DESC LIMIT 1) AS last_error
            FROM items i GROUP BY i.source, i.project
            ORDER BY i.source, i.project
            """
        ).fetchall()
        for row in rows:
            state = "OK" if row["last_success"] == 1 and row["last_complete"] == 1 else "WARN"
            print(f"{state:4} {row['source']:7} {row['project']:14} "
                  f"items={row['items']:6} {row['coverage_start']} .. {row['coverage_end']}")
            if row["last_error"]:
                print(f"     error={row['last_error']}")
    finally:
        conn.close()


def cmd_backfill_kvm(args, config: Config) -> None:
    """按 public-inbox epoch 回填 KVM 历史；默认覆盖已知 epoch 0/1。"""
    source = next((s for s in config.sources.mailing_lists if s.name == "kvm"), None)
    if source is None or source.type != "lore_git":
        raise SystemExit("config.yaml 中未配置 lore_git 类型的 kvm 源")
    since = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
    urls = args.epoch_url or [
        source.url.rsplit("/", 1)[0] + "/0.git",
        source.url.rsplit("/", 1)[0] + "/1.git",
    ]
    conn = db.connect(config.db_path)
    try:
        for url in urls:
            print(f"回填 {url}（从 {args.since} 起）")
            lore_git.fetch(conn, replace(source, url=url), since=since)
        threads.rebuild_threads(conn)
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="virt-report",
                                     description="虚拟化研发动态追踪与日报生成")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--config", default=None, help="配置文件路径")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_fetch = sub.add_parser("fetch", help="采集数据 + 重建线程")
    p_fetch.add_argument("--since-days", type=int, default=3)
    p_fetch.add_argument("--max-pages", type=int, default=8)

    def _add_period_opts(p, key_help):
        p.add_argument("key", nargs="?", help=key_help)
        p.add_argument("--since-days", type=int, default=3)
        p.add_argument("--max-pages", type=int, default=8)
        p.add_argument("--no-fetch", action="store_true", help="跳过采集，仅用已存数据")

    p_daily = sub.add_parser("daily", help="生成日报 (默认今天)")
    _add_period_opts(p_daily, "YYYY-MM-DD")
    p_weekly = sub.add_parser("weekly", help="生成周报 (默认本周)")
    _add_period_opts(p_weekly, "YYYY-Www (如 2026-W28)")
    p_monthly = sub.add_parser("monthly", help="生成月报 (默认本月)")
    _add_period_opts(p_monthly, "YYYY-MM (如 2026-07)")

    sub.add_parser("index", help="渲染首页索引")
    sub.add_parser("status", help="显示数据源覆盖与最近采集健康状态")
    p_backfill = sub.add_parser("backfill-kvm", help="从 lore Git epochs 回填 KVM 历史")
    p_backfill.add_argument("--since", required=True, help="UTC 日期，如 2025-01-01")
    p_backfill.add_argument("--epoch-url", action="append", default=None,
                            help="指定 epoch Git URL，可重复；默认 0.git 与 1.git")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    config = load_config(args.config)

    dispatch = {"fetch": cmd_fetch, "daily": cmd_daily, "weekly": cmd_weekly,
                "monthly": cmd_monthly, "index": cmd_index, "status": cmd_status,
                "backfill-kvm": cmd_backfill_kvm}
    try:
        dispatch[args.cmd](args, config)
    except Exception as e:
        log.error("命令 %s 失败: %s", args.cmd, e, exc_info=args.verbose)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
