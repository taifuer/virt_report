"""GitLab observations preserve known history without fabricating old states."""
import json

import pytest

from virt_report import db
from virt_report.processing.threads import rebuild_threads


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "activity.db")
    yield connection
    connection.close()


def gitlab_item(updated_at, *, state="opened", title="Original proposal", notes=0):
    return {
        "source": "gitlab", "project": "qemu", "native_id": "mr:1",
        "thread_root": "mr:1", "author": "author", "subject": title,
        "kind": "mr", "created_at": "2026-09-01T00:00:00Z",
        "updated_at": updated_at, "url": "https://example.org/mr/1",
        "body_excerpt": f"Proposal is {state}",
        "raw_json": {"state": state, "merged": state == "merged",
                     "user_notes_count": notes},
    }


def test_updates_do_not_erase_prior_window_activity_or_state(conn):
    initial = gitlab_item("2026-10-10T10:00:00Z", notes=40)
    db.upsert_item(conn, initial)
    rebuild_threads(conn)
    before = db.get_top_threads(conn, "2026-10-10T00:00:00Z", "2026-10-11T00:00:00Z")
    db.upsert_item(conn, gitlab_item("2026-10-11T10:00:00Z", state="merged",
                                  title="Changed after report", notes=100))
    rebuild_threads(conn)
    assert db.get_top_threads(conn, "2026-10-10T00:00:00Z", "2026-10-11T00:00:00Z") == before
    assert before[0]["message_count"] == 1  # Never use lifetime note totals.
    row, = db.get_activity_items_in_window(conn, "2026-10-10T00:00:00Z", "2026-10-11T00:00:00Z")
    assert row["subject"] == "Original proposal"
    assert json.loads(row["raw_json"])["state"] == "opened"
    context, = db.get_thread_items_as_of(conn, before[0], "2026-10-11T00:00:00Z")
    assert context["history_known"]
    assert json.loads(context["raw_json"])["state"] == "opened"
    later, = db.get_thread_items_as_of(conn, before[0], "2026-10-12T00:00:00Z")
    assert json.loads(later["raw_json"])["merged"]


def test_creation_fact_does_not_backdate_current_body_or_state(conn):
    db.upsert_item(conn, gitlab_item("2026-10-10T10:00:00Z", state="merged"))
    rebuild_threads(conn)
    row, = db.get_activity_items_in_window(conn, "2026-09-01T00:00:00Z", "2026-09-02T00:00:00Z")
    assert row["activity_at"] == "2026-09-01T00:00:00Z"
    assert not row["history_known"]
    assert row["history_note"]
    assert row["body_excerpt"] == ""
    assert row["raw_json"] == "{}"
    assert "历史内容未保存" in row["subject"]
    assert db.get_top_threads(conn, "2026-09-01T00:00:00Z", "2026-09-02T00:00:00Z") == []


def test_unknown_gitlab_history_does_not_consume_project_candidate_quota(conn):
    db.upsert_item(conn, gitlab_item("2026-10-10T10:00:00Z", state="merged"))
    db.upsert_item(conn, {
        "source": "ml", "project": "qemu-devel", "native_id": "mail", "message_id": "mail",
        "subject": "New discussion", "kind": "discussion", "author": "author",
        "created_at": "2026-09-01T10:00:00Z", "updated_at": "2026-09-01T10:00:00Z",
        "url": "https://example.org/mail", "body_excerpt": "Actual proposal", "raw_json": {},
    })
    rebuild_threads(conn)
    stats_items = db.get_activity_items_in_window(conn, "2026-09-01T00:00:00Z", "2026-09-02T00:00:00Z")
    assert len(stats_items) == 2  # The independent creation fact remains counted.
    candidate, = db.get_top_threads_for_projects(
        conn, "2026-09-01T00:00:00Z", "2026-09-02T00:00:00Z", ("qemu", "qemu-devel"), 1)
    assert candidate["project"] == "qemu-devel"


def test_observations_are_idempotent_and_window_counts_are_distinct(conn):
    first = gitlab_item("2026-10-10T10:00:00Z")
    db.upsert_item(conn, first)
    db.upsert_item(conn, first)
    assert conn.execute("SELECT COUNT(*) FROM item_activity_history").fetchone()[0] == 2
    db.upsert_item(conn, gitlab_item("2026-10-10T12:00:00Z", state="closed"))
    rows = db.get_activity_items_in_window(conn, "2026-10-10T00:00:00Z", "2026-10-11T00:00:00Z")
    assert len(rows) == 1
    assert json.loads(rows[0]["raw_json"])["state"] == "closed"


def test_out_of_order_observation_preserves_current_and_historical_state(conn):
    db.upsert_item(conn, gitlab_item("2026-10-11T10:00:00Z", state="merged"))
    db.upsert_item(conn, gitlab_item("2026-10-10T10:00:00Z"))
    current = conn.execute("SELECT raw_json FROM items").fetchone()[0]
    assert json.loads(current)["state"] == "merged"
    old, = db.get_activity_items_in_window(conn, "2026-10-10T00:00:00Z", "2026-10-11T00:00:00Z")
    assert json.loads(old["raw_json"])["state"] == "opened"


def test_snapshot_does_not_reuse_description_cleared_in_new_observation(conn):
    db.upsert_item(conn, gitlab_item("2026-10-10T10:00:00Z"))
    cleared = gitlab_item("2026-10-11T10:00:00Z")
    cleared["body_excerpt"] = ""
    db.upsert_item(conn, cleared)
    new, = db.get_activity_items_in_window(conn, "2026-10-11T00:00:00Z", "2026-10-12T00:00:00Z")
    assert new["body_excerpt"] == ""


def test_legacy_migration_is_idempotent_and_does_not_forge_history(tmp_path):
    path = tmp_path / "legacy.db"
    connection = db.connect(path)
    db.upsert_item(connection, gitlab_item("2026-10-10T10:00:00Z", state="closed"))
    # Simulate a database from before the immutable observation table existed.
    connection.execute("DROP TABLE item_activity_history")
    connection.commit()
    connection.close()
    connection = db.connect(path)
    old, = db.get_activity_items_in_window(connection, "2026-10-10T00:00:00Z", "2026-10-11T00:00:00Z")
    assert not old["history_known"]
    assert old["history_provenance"] == "legacy"
    assert old["raw_json"] == "{}"
    assert old["body_excerpt"] == ""
    assert json.loads(connection.execute("SELECT raw_json FROM items").fetchone()[0])["state"] == "closed"
    count = connection.execute("SELECT COUNT(*) FROM item_activity_history").fetchone()[0]
    connection.close()
    connection = db.connect(path)
    assert connection.execute("SELECT COUNT(*) FROM item_activity_history").fetchone()[0] == count
    # A real repeated collection supplies a trusted snapshot, unlike migration.
    db.upsert_item(connection, gitlab_item("2026-10-10T10:00:00Z", state="closed"))
    known, = db.get_activity_items_in_window(connection, "2026-10-10T00:00:00Z", "2026-10-11T00:00:00Z")
    assert known["history_known"]
    assert json.loads(known["raw_json"])["state"] == "closed"
    connection.close()
