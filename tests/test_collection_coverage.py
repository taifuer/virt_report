from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from virt_report import cli, coverage, db, scheduler
from virt_report.collectors import gitlab, hyperkitty, lore, lore_git, mailarchive, mbox
from virt_report.config import Config, MailingListSource, Sources, Storage


@pytest.fixture
def collection(tmp_path, monkeypatch):
    config = Config(storage=Storage(db_path=tmp_path / "data.db"), sources=Sources(
        mailing_lists=[MailingListSource("qemu-devel", "mbox", "https://example.test/qemu/")],
    ))
    conn = db.connect(config.db_path)
    monkeypatch.setattr(cli.threads, "rebuild_threads", lambda _conn: None)
    monkeypatch.setattr(cli.topics, "refresh_topic_snapshots", lambda _conn: None)
    monkeypatch.setattr(cli.search_index, "refresh_index", lambda _conn: None)
    yield conn, config
    conn.close()


def _scan(conn, source="qemu-devel", start="2026-10-01T00:00:00Z",
          end="2026-10-05T00:00:00Z", status="complete", key="batch"):
    coverage.ensure_schema(conn)
    coverage.record_scan(conn, key=key, source="ml", project=source, start=start, end=end,
                         started_at=start, status=status, page_limit=8)


def test_coverage_unknown_without_scans_and_empty_complete_source(collection):
    conn, config = collection
    assert coverage.report_coverage(conn, config, "2026-10-01", "2026-10-05")["status"] == "unknown"
    _scan(conn)
    result = coverage.report_coverage(conn, config, "2026-10-01", "2026-10-05")
    assert result["status"] == "complete"
    assert result["missing_sources"] == []
    assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0


def test_recent_window_does_not_prove_month_and_disjoint_scans_leave_gap(collection):
    conn, config = collection
    _scan(conn)
    _scan(conn, start="2026-10-07T00:00:00Z", end="2026-10-10T00:00:00Z")
    result = coverage.report_coverage(conn, config, "2026-10-01", "2026-10-10")
    assert result["status"] == "partial"
    assert result["wait_recommended"] is True
    assert result["sources"][0]["gaps"] == [{"start": "2026-10-05T00:00:00Z", "end": "2026-10-07T00:00:00Z"}]
    assert coverage.report_coverage(conn, config, "2026-09-01", "2026-10-01")["status"] == "unknown"


def test_partial_or_failed_scan_never_closes_gap(collection):
    conn, config = collection
    _scan(conn, status="partial")
    _scan(conn, status="failed")
    assert coverage.report_coverage(conn, config, "2026-10-01", "2026-10-05")["status"] == "partial"
    _scan(conn, end="2026-10-03T00:00:00Z")
    _scan(conn, start="2026-10-03T00:00:00Z")
    assert coverage.report_coverage(conn, config, "2026-10-01", "2026-10-05")["status"] == "complete"


def test_fetch_mixed_failure_has_nonzero_exit_and_retry_skips_success(collection, monkeypatch):
    conn, config = collection
    config.sources.mailing_lists.append(MailingListSource("kvm", "mbox", "https://example.test/kvm/"))
    calls = []

    def fetch(conn, source, **kwargs):
        calls.append(source.name)
        if source.name == "kvm" and calls.count("kvm") == 1:
            raise RuntimeError("unavailable")
        db.set_fetch_state(conn, "ml", source.name, last_seen_id="complete")
        return 0

    monkeypatch.setattr(cli.mbox, "fetch", fetch)
    since = datetime(2026, 10, 1, tzinfo=timezone.utc)
    outcome = cli._fetch_all_locked(conn, config, since, batch_key="retryable")
    assert outcome["status"] == "partial" and outcome["exit_code"] == 3
    outcome = cli._fetch_all_locked(conn, config, since, batch_key="retryable")
    assert outcome["status"] == "success" and outcome["exit_code"] == 0
    assert calls == ["qemu-devel", "kvm", "kvm"]


def test_fetch_all_failed_and_main_returns_handler_exit_code(collection, monkeypatch):
    conn, config = collection
    monkeypatch.setattr(cli.mbox, "fetch", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("offline")))
    result = cli._fetch_all_locked(conn, config, datetime(2026, 10, 1, tzinfo=timezone.utc))
    assert result["status"] == "failed" and result["exit_code"] == 1
    monkeypatch.setattr(cli, "load_config", lambda _path: config)
    monkeypatch.setattr(cli, "cmd_fetch", lambda _args, _config: 3)
    assert cli.main(["fetch"]) == 3


def test_partial_retries_increase_budget_and_retain_unfinished_start(collection, monkeypatch):
    conn, config = collection
    calls = []

    def fetch(conn, source, since, max_pages):
        calls.append((since, max_pages))
        db.set_fetch_state(conn, "ml", source.name, last_seen_id="partial" if max_pages < 32 else "complete")
        return 0

    monkeypatch.setattr(cli.mbox, "fetch", fetch)
    first = datetime(2026, 10, 1, tzinfo=timezone.utc)
    cli._fetch_all_locked(conn, config, first, batch_key="first")
    cli._fetch_all_locked(conn, config, first, batch_key="first")
    result = cli._fetch_all_locked(conn, config, datetime(2026, 10, 5, tzinfo=timezone.utc), batch_key="next")
    assert calls == [(first, 8), (first, 16), (first, 32)]
    assert result["status"] == "success"


def test_page_budget_does_not_grow_on_failure_or_reset_after_progress(collection):
    conn, _config = collection
    coverage.ensure_schema(conn)
    coverage.record_scan(conn, key="old", source="ml", project="qemu-devel", start="2026-10-01T00:00:00Z",
                         end="2026-10-03T00:00:00Z", started_at="2026-10-03T00:00:00Z",
                         status="failed", page_limit=32)
    assert coverage.source_retry(conn, "new", "ml", "qemu-devel", "2026-10-02T00:00:00Z", 8)[2] == 32
    with db.transaction(conn):
        conn.execute("UPDATE collection_scans SET status='partial',page_limit=4096")
    assert coverage.source_retry(conn, "new", "ml", "qemu-devel", "2026-10-02T00:00:00Z", 8)[2] == 4096


def test_coverage_wait_never_calls_model_and_last_attempt_removes_gate(collection, monkeypatch):
    conn, config = collection
    _scan(conn, status="failed")
    monkeypatch.setattr(cli.report, "generate", lambda *a, **kw: pytest.fail("AI called while coverage is pending"))
    args = SimpleNamespace(key="2026-10-02", no_fetch=True, require_ai=True, wait_for_coverage=True)
    assert cli._run_period(args, config, "daily") == 4
    command = ["daily", "2026-10-02", "--no-fetch", "--require-ai"]
    assert "--wait-for-coverage" in scheduler._attempt_command(config, "daily", command, "daily:key", 1)
    assert "--wait-for-coverage" not in scheduler._attempt_command(config, "daily", command, "daily:key", config.schedule.retry_limit)


def test_unpublished_report_has_failure_exit_not_saved_message(collection, monkeypatch, capsys):
    _conn, config = collection
    monkeypatch.setattr(cli.report, "generate", lambda *a, **kw: {"published": False})
    args = SimpleNamespace(key="2026-10-02", no_fetch=True, require_ai=True)
    assert cli._run_period(args, config, "daily") == 1
    assert "已生成并保存" not in capsys.readouterr().out


def test_fetch_retry_restores_same_batch_identity_and_respects_limit(collection):
    conn, config = collection
    now = datetime(2026, 10, 3, 5, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    identity = "fetch:2026-10-03T04:07:00+08:00"
    run_id = db.start_scheduler_run(conn, identity=identity, job_name="fetch",
                                    scheduled_at="2026-10-03T04:07:00+08:00", attempt=1)
    db.finish_scheduler_run(conn, run_id, status="partial", exit_code=3)
    pending, exhausted = scheduler._restore_fetch_retry(config, now.tzinfo, now)
    assert identity in pending and not exhausted
    command = scheduler._attempt_command(config, "fetch", pending[identity][1], identity, 2)
    assert command[-2:] == ["--batch-key", identity]
    with db.transaction(conn):
        conn.execute("UPDATE scheduler_runs SET attempt=? WHERE id=?", (config.schedule.retry_limit, run_id))
    pending, exhausted = scheduler._restore_fetch_retry(config, now.tzinfo, now)
    assert not pending and exhausted == {identity}


def test_scheduler_loop_bounds_waiting_and_never_regenerates_published_report(collection, monkeypatch, tmp_path):
    conn, config = collection
    config.schedule.retry_delay_seconds = 60
    tz = ZoneInfo("Asia/Shanghai")
    initial = datetime(2026, 10, 3, 0, 15, tzinfo=tz)
    clock = [initial]
    from datetime import timedelta

    class Clock(datetime):
        @classmethod
        def now(cls, timezone=None):
            return clock[0]

    monkeypatch.setattr(scheduler, "datetime", Clock)
    jobs = [("fetch", ["fetch", "--since-days", "4"], initial),
            ("daily", ["daily", "2026-10-02", "--no-fetch", "--require-ai"], initial)]
    monkeypatch.setattr(scheduler, "due_commands", lambda *a: jobs if clock[0] == initial else [])
    fetches, gates = [], []

    def run(argv, **kwargs):
        if "fetch" in argv:
            fetches.append(argv[-1])
            return SimpleNamespace(returncode=3 if len(fetches) == 1 else 0)
        gates.append("--wait-for-coverage" in argv)
        if gates[-1]:
            return SimpleNamespace(returncode=4)
        db.save_report(conn, "daily", "2026-10-02", {"fallback": False}, config.timezone)
        return SimpleNamespace(returncode=0)

    def sleep(_seconds):
        clock[0] += timedelta(minutes=1)
        if clock[0] >= initial + timedelta(minutes=4):
            raise StopIteration

    monkeypatch.setattr(scheduler.subprocess, "run", run)
    monkeypatch.setattr(scheduler.time, "sleep", sleep)
    with pytest.raises(StopIteration):
        scheduler._run_loop(config, None, tz, tmp_path / "state.json", set(), initial - timedelta(minutes=1))
    assert gates == [True, True, False]
    assert len(fetches) == 2 and fetches[0] == fetches[1]
    assert conn.execute("SELECT COUNT(*) FROM scheduler_runs WHERE status='coverage_wait'").fetchone()[0] == 2


def _atom(title="patch", next_url=None, updated="2026-10-02T00:00:00Z"):
    next_link = f'<link rel="next" href="{next_url}"/>' if next_url else ""
    return f'''<feed xmlns="http://www.w3.org/2005/Atom">{next_link}<entry>
      <title>{title}</title><id>urn:uuid:{title}</id>
      <link href="https://example.test/kvm/{title}%40example.test/"/>
      <updated>{updated}</updated><content>body</content>
    </entry></feed>'''.encode()


def test_lore_retry_rescans_head_and_requires_requested_boundary(collection, monkeypatch):
    conn, _config = collection
    source = MailingListSource("kvm", "lore", "https://example.test/kvm/")
    calls = []

    def get(url):
        calls.append(url)
        if "page=2" in url:
            return SimpleNamespace(content=_atom("older", updated="2026-09-30T00:00:00Z"))
        return SimpleNamespace(content=_atom("first" if len(calls) == 1 else "new_reply", "?page=2"))

    monkeypatch.setattr(lore.base, "http_get", get)
    since = datetime(2026, 10, 1, tzinfo=timezone.utc)
    lore.fetch(conn, source, since=since, max_pages=1)
    assert db.get_fetch_state(conn, "ml", "kvm")["last_seen_id"] == "partial"
    lore.fetch(conn, source, since=since, max_pages=2)
    assert db.get_fetch_state(conn, "ml", "kvm")["last_seen_id"] == "complete"
    assert calls == ["https://example.test/kvm/new.atom", "https://example.test/kvm/new.atom",
                     "https://example.test/kvm/new.atom?page=2"]
    assert conn.execute("SELECT COUNT(*) FROM items WHERE subject='new_reply'").fetchone()[0] == 1


@pytest.mark.parametrize("content", [_atom(), b'<feed xmlns="http://www.w3.org/2005/Atom"/>'])
def test_lore_terminal_recent_feed_is_not_complete_archive(collection, monkeypatch, content):
    conn, _config = collection
    monkeypatch.setattr(lore.base, "http_get", lambda _url: SimpleNamespace(content=content))
    lore.fetch(conn, MailingListSource("kvm", "lore", "https://example.test/kvm/"),
               since=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert db.get_fetch_state(conn, "ml", "kvm")["last_seen_id"] == "partial"


def test_lore_malformed_feed_raises_instead_of_success(collection, monkeypatch):
    conn, _config = collection
    monkeypatch.setattr(lore.base, "http_get", lambda _url: SimpleNamespace(content=b"not a feed"))
    with pytest.raises(ValueError):
        lore.fetch(conn, MailingListSource("kvm", "lore", "https://example.test/kvm/"))


def test_hyperkitty_successful_empty_scan_is_complete(collection, monkeypatch):
    conn, _config = collection
    monkeypatch.setattr(hyperkitty, "_months_range", lambda *a: [(2026, 10)])
    monkeypatch.setattr(hyperkitty, "_thread_ids_from_month", lambda *a: ([], True))
    source = MailingListSource("libvir-list", "hyperkitty", "https://example.test/archives/list/libvirt@example.test/")
    assert hyperkitty.fetch(conn, source) == 0
    assert db.get_fetch_state(conn, "ml", source.name)["last_seen_id"] == "complete"


def test_hyperkitty_missing_detail_is_partial_and_retry_fills_it(collection, monkeypatch):
    conn, _config = collection
    source = MailingListSource("libvir-list", "hyperkitty", "https://example.test/archives/list/libvirt@example.test/")
    monkeypatch.setattr(hyperkitty, "_months_range", lambda *a: [(2026, 10)])
    monkeypatch.setattr(hyperkitty, "_thread_ids_from_month", lambda *a: (["THREAD"], True))
    calls = []

    def get(url):
        if "/emails/" in url:
            return SimpleNamespace(json=lambda: [{"message_id_hash": "HASH", "message_id": "id@example.test", "date": "2026-10-02T00:00:00Z"}])
        calls.append(url)
        if len(calls) == 1:
            raise RuntimeError("detail offline")
        return SimpleNamespace(json=lambda: {"content": "Restored original message content"})

    monkeypatch.setattr(hyperkitty.base, "http_get", get)
    since = datetime(2026, 10, 1, tzinfo=timezone.utc)
    hyperkitty.fetch(conn, source, since=since)
    assert db.get_fetch_state(conn, "ml", source.name)["last_seen_id"] == "partial"
    hyperkitty.fetch(conn, source, since=since)
    assert db.get_fetch_state(conn, "ml", source.name)["last_seen_id"] == "complete"
    assert conn.execute("SELECT body_excerpt FROM items").fetchone()[0] == "Restored original message content"


def test_mailarchive_error_raises_and_truncated_feed_is_partial(collection, monkeypatch):
    conn, _config = collection
    source = MailingListSource("qemu-devel", "mailarchive", "https://example.test/")
    monkeypatch.setattr(mailarchive.base, "http_get", lambda _url: (_ for _ in ()).throw(RuntimeError("offline")))
    with pytest.raises(RuntimeError):
        mailarchive.fetch(conn, source)
    monkeypatch.setattr(mailarchive.base, "http_get", lambda _url: SimpleNamespace(content=b'<rss version="2.0"><channel></channel></rss>'))
    mailarchive.fetch(conn, source)
    assert db.get_fetch_state(conn, "ml", source.name)["last_seen_id"] == "partial"


def test_mbox_parse_error_cannot_mark_complete(collection, monkeypatch, tmp_path):
    conn, _config = collection
    monkeypatch.setattr(mbox, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(mbox, "_months_range", lambda *a: ["2026-10"])
    monkeypatch.setattr(mbox, "_download", lambda *a: (True, True))
    monkeypatch.setattr(mbox.mailbox, "mbox", lambda *a: (_ for _ in ()).throw(ValueError("invalid archive")))
    with pytest.raises(RuntimeError):
        mbox.fetch(conn, MailingListSource("qemu-devel", "mbox", "https://example.test/"))


def test_lore_git_successful_empty_window_is_complete(collection, monkeypatch, tmp_path):
    conn, _config = collection
    monkeypatch.setattr(lore_git, "_ensure_repo", lambda *a: tmp_path)
    monkeypatch.setattr(lore_git, "_commit_ids", lambda *a: [])
    assert lore_git.fetch(conn, MailingListSource("kvm", "lore_git", "https://example.test/")) == 0
    assert db.get_fetch_state(conn, "ml", "kvm")["last_seen_id"] == "complete"


def test_gitlab_nonlist_response_cannot_be_success(monkeypatch):
    monkeypatch.setattr(gitlab.base, "http_get", lambda *a, **kw: SimpleNamespace(json=lambda: {"error": "upstream unavailable"}))
    with pytest.raises(ValueError, match="non-list"):
        gitlab._fetch_paginated("https://example.test/issues", {}, 8)
