"""Request reads avoid schema work, full report decoding and stale data caches."""
import json
import sqlite3
from contextlib import closing

import pytest

from virt_report import db, server
from virt_report.config import Config, Storage


@pytest.fixture
def web_database(tmp_path):
    config = Config(storage=Storage(db_path=tmp_path / "web #1.db"))
    with closing(db.connect(config.db_path)) as conn:
        yield config, conn


def _save(conn, key, headline="报告", **extra):
    db.save_report(conn, "daily", key, {
        "period": "daily", "period_key": key, "headline": headline, **extra,
    }, "Asia/Shanghai")


def _request(handler_type, path="/"):
    handler = object.__new__(handler_type)
    handler.path = path
    handler.headers = {}
    responses = []
    handler._send = lambda status, body, *_args, **_kwargs: responses.append((status, body))
    handler._dispatch(head_only=False)
    assert len(responses) == 1
    return responses[0]


def _forbidden(*_args, **_kwargs):
    pytest.fail("Warm web requests must not initialize or migrate the database")


def test_readonly_connection_does_not_initialize_or_create_files(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "connect", _forbidden)
    path = tmp_path / "missing" / "web.db"
    with pytest.raises(sqlite3.OperationalError):
        db.connect_readonly(path)
    assert not path.parent.exists()


def test_readonly_connection_rejects_writes_and_reads_commits(web_database):
    config, writer = web_database
    _save(writer, "2026-10-10", "原始报告")
    with closing(db.connect_readonly(config.db_path)) as reader:
        assert server._list_reports(reader, "daily")[0]["headline"] == "原始报告"
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            reader.execute("DELETE FROM reports")
        _save(writer, "2026-10-11", "新增报告")
        assert server._list_reports(reader, "daily")[0]["headline"] == "新增报告"


@pytest.mark.parametrize("path", ["/", "/daily/index.html", "/search.html", "/healthz"])
def test_warm_get_reads_during_collector_write_without_migrations(
    web_database, monkeypatch, path,
):
    config, writer = web_database
    _save(writer, "2026-10-10")
    handler_type = server.make_handler(config)
    assert _request(handler_type, "/healthz")[0] == 200
    monkeypatch.setattr(db, "connect", _forbidden)
    monkeypatch.setattr(db, "_migrate_activity_history", _forbidden)
    monkeypatch.setattr(db, "_migrate_items_v2", _forbidden)
    writer.execute("BEGIN IMMEDIATE")
    try:
        writer.execute("UPDATE reports SET model='uncommitted'")
        assert _request(handler_type, path)[0] == 200
    finally:
        writer.rollback()


def test_handler_initializes_only_once_when_used_without_serve(web_database, monkeypatch):
    config, _writer = web_database
    initialize = db.connect
    calls = []

    def counted(path):
        calls.append(path)
        return initialize(path)

    monkeypatch.setattr(db, "connect", counted)
    handler_type = server.make_handler(config)
    assert calls == []
    assert _request(handler_type, "/healthz")[0] == 200
    assert _request(handler_type, "/healthz")[0] == 200
    assert calls == [config.db_path]


def test_serve_initializes_before_accepting_requests(web_database, monkeypatch):
    config, _writer = web_database
    initialize = db.connect
    calls = []

    def counted(path):
        calls.append(path)
        return initialize(path)

    class LocalServer:
        def __init__(self, address, handler_type):
            assert calls == [config.db_path]
            self.handler_type = handler_type

        def serve_forever(self):
            monkeypatch.setattr(db, "connect", _forbidden)
            assert _request(self.handler_type, "/healthz")[0] == 200

        def server_close(self):
            pass

    monkeypatch.setattr(db, "connect", counted)
    monkeypatch.setattr(server, "ThreadingHTTPServer", LocalServer)
    server.serve(config)


@pytest.mark.parametrize("fallback", [
    None, False, 0, -0.0, "", [], {}, True, 1, -1, 0.1,
    "0", "false", "[]", "{}", "\x00", [0], {"value": 0},
])
def test_report_projection_preserves_fallback_truthiness(web_database, fallback):
    _config, conn = web_database
    _save(conn, "2026-10-10", fallback=fallback)
    row = db.get_report(conn, "daily", "2026-10-10")
    expected = not bool(fallback)
    assert (server._published_content(row) is not None) is expected
    assert bool(server._list_reports(conn, "daily")) is expected
    assert bool(server._nav(conn, "daily", "2026-10-11")["prev"]) is expected


@pytest.mark.parametrize("headline", ["中文与 patch 标题", "", None, 0, [], {"title": "x"}])
def test_report_projection_preserves_headline_values(web_database, headline):
    _config, conn = web_database
    _save(conn, "2026-10-10", headline)
    assert server._list_reports(conn, "daily")[0]["headline"] == headline


@pytest.mark.parametrize("raw", ["{broken", "null", "[]", "[1]", "true", "42", '"text"'])
def test_corrupt_and_non_object_reports_are_ignored(web_database, raw):
    _config, conn = web_database
    conn.execute(
        "INSERT INTO reports(period,period_key,generated_at,content_json) VALUES (?,?,?,?)",
        ("daily", "2026-10-10", "2026-10-11T00:00:00Z", raw),
    )
    conn.commit()
    assert server._published_content(db.get_report(conn, "daily", "2026-10-10")) is None
    assert server._list_reports(conn, "daily") == []
    assert server._nav(conn, "daily", "2026-10-11") == {"prev": None, "next": None}


def test_list_and_navigation_do_not_decode_report_bodies(web_database, monkeypatch):
    _config, conn = web_database
    for day in (9, 10, 11):
        _save(conn, f"2026-10-{day:02d}", f"报告 {day}", sections=[{
            "body": "大量报告正文" * 20000,
        }])
    _save(conn, "2026-10-12", "模板内容", fallback=True)
    decode = json.loads
    decoded = []

    def small_values_only(value, *args, **kwargs):
        assert len(value) < 100, "Lists must not decode full report documents"
        decoded.append(value)
        return decode(value, *args, **kwargs)

    monkeypatch.setattr(server.json, "loads", small_values_only)
    assert len(server._list_reports(conn, "daily")) == 3
    assert len(decoded) == 3
    assert server._nav(conn, "daily", "2026-10-10") == {
        "prev": {"label": "2026-10-09", "url": "daily/2026-10-09.html"},
        "next": {"label": "2026-10-11", "url": "daily/2026-10-11.html"},
    }
    assert len(decoded) == 3
    assert server._nav(conn, "daily", "2026-10-11")["next"] is None


def test_home_queries_each_report_period_once(web_database, monkeypatch):
    _config, conn = web_database
    list_reports = server._list_reports
    calls = []

    def counted(connection, period):
        calls.append(period)
        return list_reports(connection, period)

    monkeypatch.setattr(server, "_list_reports", counted)
    server._index_context(conn)
    assert calls == ["daily", "weekly", "monthly"]


def test_next_get_sees_generation_state_and_new_publication(web_database, monkeypatch):
    config, conn = web_database
    _save(conn, "2026-10-10", "已发布")
    handler_type = server.make_handler(config, _database_initialized=True)
    monkeypatch.setattr(server.render, "render_index_html", lambda _config, context: context)
    monkeypatch.setattr(db, "connect", _forbidden)
    first = _request(handler_type)[1]
    assert first["daily"][0]["headline"] == "已发布"
    assert first["generation_states"]["daily"] == []
    db.set_report_generation_state(conn, "daily", "2026-10-11", status="running")
    second = _request(handler_type)[1]
    assert second["generation_states"]["daily"][0]["status"] == "running"
    db.set_report_generation_state(conn, "daily", "2026-10-11", status="retry_wait")
    assert _request(handler_type)[1]["generation_states"]["daily"][0]["status"] == "retry_wait"
    _save(conn, "2026-10-11", "新点评")
    final = _request(handler_type)[1]
    assert final["daily"][0]["headline"] == "新点评"
    assert final["generation_states"]["daily"] == []
