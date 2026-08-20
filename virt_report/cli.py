"""virt-report 命令行入口。

用法:
  virt-report fetch [--since-days N | --since YYYY-MM-DD]  # 采集 + 重建线程
  virt-report daily [YYYY-MM-DD]           # 全链路生成某日日报 (默认今天)
  virt-report index                        # 渲染首页索引
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from virt_report import db, search as search_index
from virt_report.collectors import lore, lore_git, gitlab, hyperkitty, mailarchive, mbox
from virt_report.config import Config, load_config
from virt_report.processing import threads
from virt_report.locking import process_lock
from virt_report.processing import topics
from virt_report.render import render
from virt_report.summarize import periods, report

log = logging.getLogger(__name__)


def _today_local(tz_name: str) -> str:
    return datetime.now(ZoneInfo(tz_name)).strftime("%Y-%m-%d")


def _fetch_all(conn, config: Config, since: datetime, max_pages: int = 8) -> None:
    """采集所有源 (since 为 UTC datetime)。"""
    lock_path = config.db_path.parent / "fetch.lock"
    with process_lock(lock_path):
        _fetch_all_locked(conn, config, since, max_pages)


def _fetch_all_locked(conn, config: Config, since: datetime,
                      max_pages: int = 8) -> None:
    """在调用方已取得 fetch.lock 后执行全部采集器。"""
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
    topics.refresh_topic_snapshots(conn)
    search_index.refresh_index(conn)


def _list_reports(conn, period: str) -> list[dict]:
    rs = conn.execute(
        "SELECT period_key, generated_at, item_count, model, content_json FROM reports "
        "WHERE period=? ORDER BY period_key DESC", (period,),
    ).fetchall()
    result = []
    for r in rs:
        try:
            content = json.loads(r["content_json"])
        except (TypeError, ValueError):
            continue
        if content.get("fallback"):
            continue
        result.append({
            "period_key": r["period_key"], "generated_at": r["generated_at"],
            "item_count": r["item_count"] or 0, "model": r["model"],
            "headline": content.get("headline", ""),
        })
    return result


def _render_index(config: Config, conn) -> None:
    from virt_report.render.render import build_calendar

    render.export_brand_assets(config.output_dir)
    daily_rows = _list_reports(conn, "daily")
    daily_keys = {r["period_key"] for r in daily_rows}
    months = sorted({k[:7] for k in daily_keys})  # YYYY-MM
    if not months:
        now = datetime.now(ZoneInfo(config.timezone))
        months = [now.strftime("%Y-%m")]

    weekly = _list_reports(conn, "weekly")
    weekly = [dict(item, period_range=render._period_range(
        "weekly", item["period_key"], config.timezone
    )) for item in weekly]
    monthly = render.limit_home_reports("monthly", _list_reports(conn, "monthly"))
    daily = render.limit_home_reports("daily", _list_reports(conn, "daily"))
    weekly = render.limit_home_reports("weekly", weekly)
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

    calendars = [build_calendar(month, daily_keys) for month in months]
    ctx = {"cal": calendars[-1], "calendars": calendars, "weekly": weekly,
           "monthly": monthly, "daily": daily, "source_health": source_health}
    render.render_index(config, ctx, filename="index.html")
    # 单页日历在浏览器内切换月份，不再保留按月份复制的 index 文件。
    for stale in config.output_dir.glob("index-????-??.html"):
        stale.unlink()
    render.render_about(config)
    render.render_search(config, search_index.search(conn, ""))
    from virt_report.conferences import load_content as load_conference_content
    conference_content = load_conference_content()
    render.render_conferences(config, conference_content)
    render.render_academic_conferences(config, conference_content)
    render.render_conference_papers(config, conference_content)
    for period_name in ("daily", "weekly", "monthly"):
        render.render_archive(config, period_name, _list_reports(conn, period_name))
    render.render_topics(config, topics.build_topic_groups(conn))
    for topic_key, _name, _description, _words in topics.TOPIC_RULES:
        detail = topics.build_topic_detail(conn, topic_key, page=1, per_page=10)
        if detail:
            render.render_topic_detail(config, detail)
    # 运行指标必须经动态服务鉴权，不导出可绕过认证的静态快照。
    (config.output_dir / "metrics.html").unlink(missing_ok=True)
    from virt_report import rss
    feeds = {
        config.output_dir / "feed.xml": rss.report_feed(conn, config),
        config.output_dir / "daily" / "feed.xml": rss.report_feed(conn, config, "daily"),
        config.output_dir / "weekly" / "feed.xml": rss.report_feed(conn, config, "weekly"),
        config.output_dir / "monthly" / "feed.xml": rss.report_feed(conn, config, "monthly"),
    }
    # 安全专题是社区讨论观察，不是完整漏洞告警源；清理旧版静态 Feed。
    (config.output_dir / "topics" / "security" / "feed.xml").unlink(missing_ok=True)
    for path, body in feeds.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    try:
        from virt_report.kvm_forum import load_content
        editions, analysis = load_content()
        render.render_kvm_forum(config, editions, analysis)
    except FileNotFoundError:
        log.warning("KVM Forum 内容尚未生成，跳过静态页面")

    # `index` 是显式离线导出：将数据库中的全部报告写为静态快照。
    rows = conn.execute(
        "SELECT period,period_key,content_json FROM reports "
        "ORDER BY period,period_key"
    ).fetchall()
    for row in rows:
        try:
            raw_content = json.loads(row["content_json"])
        except (TypeError, ValueError):
            continue
        if raw_content.get("fallback"):
            (config.output_dir / row["period"] / f"{row['period_key']}.html").unlink(
                missing_ok=True
            )
            continue
        content = report.enrich_architectures(raw_content)
        render.render_report(
            config, content, nav=_nav(conn, row["period"], row["period_key"])
        )


def cmd_fetch(args, config: Config) -> None:
    conn = db.connect(config.db_path)
    try:
        if args.since:
            since = datetime.fromisoformat(args.since).replace(
                tzinfo=ZoneInfo(config.timezone)
            ).astimezone(timezone.utc)
        else:
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
    def adjacent(operator: str, direction: str):
        rows = conn.execute(
            f"SELECT period_key,content_json FROM reports WHERE period=? "
            f"AND period_key{operator}? ORDER BY period_key {direction}",
            (period, key),
        ).fetchall()
        for row in rows:
            try:
                if not json.loads(row["content_json"]).get("fallback"):
                    return row
            except (TypeError, ValueError):
                continue
        return None

    prev = adjacent("<", "DESC")
    nxt = adjacent(">", "ASC")
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
        # `fetch` 已负责重建线程；--no-fetch 用于批量回填报告时避免重复全量重建。
        content = report.generate(
            conn, config, period, key,
            publish_fallback=not args.require_ai,
        )
        if content.get("fallback") and args.require_ai:
            print(f"{period}报 AI 点评尚未完成，降级内容未发布: /{period}/{key}.html")
            return
        topics.refresh_topic_snapshots(conn)
        search_index.refresh_index(conn)
        print(f"{period}报已生成并保存到数据库: /{period}/{key}.html")
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
        topics.refresh_topic_snapshots(conn)
        search_index.refresh_index(conn)
        _render_index(config, conn)
        print("静态站点快照已导出")
    finally:
        conn.close()


def cmd_topics_refresh(args, config: Config) -> None:
    """离线重建网页直接读取的专题快照。"""
    del args
    conn = db.connect(config.db_path)
    try:
        counts = topics.refresh_topic_snapshots(conn)
        search_index.refresh_index(conn)
        print("专题快照已更新: " + " / ".join(
            f"{key}={count}" for key, count in counts.items()
        ))
    finally:
        conn.close()


def cmd_search_refresh(args, config: Config) -> None:
    """从现有线程、报告与专题快照重建离线搜索索引。"""
    del args
    conn = db.connect(config.db_path)
    try:
        status = search_index.refresh_index(conn)
        print(
            f"搜索索引已更新: 已点评 {status['document_count']}"
        )
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
        topics.refresh_topic_snapshots(conn)
    finally:
        conn.close()


def cmd_serve(args, config: Config) -> None:
    """启动数据库驱动的 Web 服务。"""
    from virt_report.server import serve
    serve(config, host=args.host, port=args.port)


def cmd_scheduler(args, config: Config) -> None:
    """启动常驻自动采集与报告调度器。"""
    from virt_report.scheduler import run_forever
    run_forever(config, config_path=args.config)


def cmd_kvm_forum(args, config: Config) -> None:
    """采集历年议程标题并用 Pro 模型重建 KVM Forum 专页。"""
    from virt_report.kvm_forum import analyze, fetch_titles, load_content, load_titles
    if args.no_fetch:
        editions = load_titles()
    else:
        editions = fetch_titles()
    analysis = analyze(config, editions)
    editions, analysis = load_content()
    render.render_kvm_forum(config, editions, analysis)
    print(f"KVM Forum 2010—2025 分析已生成（{analysis['model']}）")


def cmd_conference_catalog(args, config: Config) -> None:
    """Refresh the local full-title catalogue and show review candidates."""
    from virt_report import conferences

    conn = db.connect(config.db_path)
    try:
        if args.no_fetch:
            imported = conferences.import_editor_reviews(conn)
            result = {"imported_reviews": imported}
        else:
            result = conferences.refresh_catalogue(
                conn, start_year=args.from_year, end_year=args.to_year,
                venues=args.venue,
            )
        if args.enrich_abstracts:
            result["abstracts_added"] = conferences.enrich_candidate_abstracts(
                conn, limit=args.abstract_limit
            )
        if args.discover_dois:
            result["doi_discovery"] = conferences.discover_curated_dois(
                conn, limit=args.affiliation_limit,
            )
        if args.enrich_affiliations:
            if args.affiliation_source in {"all", "usenix"}:
                result["usenix_affiliations"] = (
                    conferences.enrich_usenix_affiliations(
                        conn, limit=args.affiliation_limit,
                        force=args.refresh_affiliations,
                    )
                )
            if args.affiliation_source in {"all", "crossref"}:
                result["crossref_affiliations"] = (
                    conferences.enrich_affiliations(
                        conn, limit=args.affiliation_limit,
                        force=args.refresh_affiliations,
                    )
                )
        if args.sync_public_metadata:
            result["public_metadata_updated"] = (
                conferences.sync_public_metadata(conn)
            )
        candidates = conferences.candidate_rows(
            conn, start_year=args.from_year, end_year=args.to_year
        )
        result["candidate_titles"] = len(candidates)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.list_candidates:
            for item in candidates:
                print(f"{item['year']}\t{item['venue']}\t{item['title']}")
    finally:
        conn.close()


def cmd_backup(args, config: Config) -> None:
    """生成可迁移的一致性数据库压缩快照。"""
    from virt_report.maintenance import backup_database, prune_backups
    target = Path(args.output).resolve() if args.output else None
    path, digest = backup_database(config.db_path, target)
    print(f"数据库备份完成: {path}")
    print(f"SHA256: {digest}")
    if args.keep_days:
        removed = prune_backups(path.parent, args.keep_days)
        print(f"已清理过期自动备份: {len(removed)} 个")


def cmd_restore(args, config: Config) -> None:
    """从压缩快照恢复数据库。恢复前应停止 web 与 scheduler。"""
    from virt_report.maintenance import restore_database
    previous, digest = restore_database(
        config.db_path, Path(args.archive), expected_sha256=args.sha256,
        force=args.force,
    )
    print(f"数据库恢复完成，SHA256: {digest}")
    if previous:
        print(f"原数据库已备份: {previous}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="virt-report",
                                     description="虚拟化研发动态追踪与日报生成")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--config", default=None, help="配置文件路径")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_fetch = sub.add_parser("fetch", help="采集数据 + 重建线程")
    p_fetch.add_argument("--since-days", type=int, default=3)
    p_fetch.add_argument("--since", help="按本地日期回填，如 2026-04-01")
    p_fetch.add_argument("--max-pages", type=int, default=8)

    def _add_period_opts(p, key_help):
        p.add_argument("key", nargs="?", help=key_help)
        p.add_argument("--since-days", type=int, default=3)
        p.add_argument("--max-pages", type=int, default=8)
        p.add_argument("--no-fetch", action="store_true", help="跳过采集，仅用已存数据")
        p.add_argument(
            "--require-ai", action="store_true",
            help="仅在 AI 点评成功时发布；失败时保留生成状态供自动重试",
        )

    p_daily = sub.add_parser("daily", help="生成日报（默认前一个完整自然日）")
    _add_period_opts(p_daily, "YYYY-MM-DD")
    p_weekly = sub.add_parser("weekly", help="生成周报（默认上一个完整自然周）")
    _add_period_opts(p_weekly, "YYYY-Www (如 2026-W28)")
    p_monthly = sub.add_parser("monthly", help="生成月报（默认上一个完整自然月）")
    _add_period_opts(p_monthly, "YYYY-MM (如 2026-07)")

    sub.add_parser("index", help="导出完整静态站点快照")
    sub.add_parser("topics-refresh", help="离线重建专题数据快照")
    sub.add_parser("search-refresh", help="离线重建社区议题搜索索引")
    sub.add_parser("status", help="显示数据源覆盖与最近采集健康状态")
    p_serve = sub.add_parser("serve", help="启动数据库驱动的 Web 服务")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8090)
    sub.add_parser("scheduler", help="启动自动采集与周期报告调度器")
    p_forum = sub.add_parser("kvm-forum", help="生成 KVM Forum 年度主题分析")
    p_forum.add_argument("--no-fetch", action="store_true", help="复用已保存的标题数据")
    p_catalog = sub.add_parser(
        "conference-catalog", help="采集学术会议标题元数据并列出待审核候选"
    )
    p_catalog.add_argument("--from-year", type=int, default=2010)
    p_catalog.add_argument("--to-year", type=int, default=2026)
    p_catalog.add_argument("--venue", action="append", default=None,
                           help="只采集指定会议 key，可重复")
    p_catalog.add_argument("--no-fetch", action="store_true",
                           help="不刷新 DBLP 目录；enrich/discover 选项仍会联网")
    p_catalog.add_argument("--list-candidates", action="store_true",
                           help="在摘要后逐行输出待审核标题")
    p_catalog.add_argument("--enrich-abstracts", action="store_true",
                           help="通过 Crossref 补充候选议题可用的公开摘要")
    p_catalog.add_argument("--abstract-limit", type=int, default=200,
                           help="单次最多补充的摘要数")
    p_catalog.add_argument("--enrich-affiliations", action="store_true",
                           help="通过会议官方页和 DOI/Crossref 补充论文单位")
    p_catalog.add_argument("--discover-dois", action="store_true",
                           help="通过 Crossref 标题检索精确匹配缺失 DOI（较慢）")
    p_catalog.add_argument("--affiliation-limit", type=int, default=200,
                           help="每个论文单位来源单次最多核验的记录数")
    p_catalog.add_argument("--affiliation-source",
                           choices=("all", "usenix", "crossref"), default="all",
                           help="限定单位来源（默认官方页和 Crossref）")
    p_catalog.add_argument("--refresh-affiliations", action="store_true",
                           help="重新核验已检查过的论文单位元数据")
    p_catalog.add_argument("--sync-public-metadata", action="store_true",
                           help="将已核验论文单位同步到公开会议快照")
    p_backup = sub.add_parser("backup", help="导出一致性的 gzip 数据库快照")
    p_backup.add_argument("output", nargs="?", help="输出 .db.gz 路径")
    p_backup.add_argument("--keep-days", type=int, default=0,
                          help="清理超过指定天数的 auto-*.db.gz")
    p_restore = sub.add_parser("restore", help="从 gzip 快照恢复数据库")
    p_restore.add_argument("archive", help="备份 .db.gz 路径")
    p_restore.add_argument("--sha256", help="预期 SHA-256")
    p_restore.add_argument("--force", action="store_true", help="确认替换当前数据库")
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
                "topics-refresh": cmd_topics_refresh,
                "search-refresh": cmd_search_refresh,
                "backfill-kvm": cmd_backfill_kvm, "serve": cmd_serve,
                "scheduler": cmd_scheduler, "kvm-forum": cmd_kvm_forum,
                "conference-catalog": cmd_conference_catalog,
                "backup": cmd_backup, "restore": cmd_restore}
    try:
        dispatch[args.cmd](args, config)
    except Exception as e:
        log.error("命令 %s 失败: %s", args.cmd, e, exc_info=args.verbose)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
