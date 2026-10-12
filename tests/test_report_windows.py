"""Report candidates and evidence must remain stable when later mail arrives."""
import pytest

from virt_report import db
from virt_report.processing.threads import rebuild_threads


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "windows.db")
    yield connection
    connection.close()


def mail(conn, native_id, created_at, *, parent=None, subject="[PATCH] x86 change",
         author="author", body="proposal", project="kvm"):
    db.upsert_item(conn, {
        "source": "ml", "project": project, "native_id": native_id,
        "message_id": native_id, "in_reply_to": parent, "author": author,
        "subject": subject, "kind": "patch" if parent is None else "discussion",
        "created_at": created_at, "updated_at": created_at,
        "url": f"https://example.org/{native_id}", "body_excerpt": body,
        "raw_json": {},
    })


@pytest.mark.parametrize("start,end,inside", [
    ("2026-10-10T16:00:00Z", "2026-10-11T16:00:00Z", "2026-10-11T15:59:59Z"),
    ("2026-10-04T16:00:00Z", "2026-10-11T16:00:00Z", "2026-10-10T10:00:00Z"),
    ("2026-08-31T16:00:00Z", "2026-09-30T16:00:00Z", "2026-09-28T10:00:00Z"),
])
def test_later_reply_does_not_drop_or_rerank_historical_thread(conn, start, end, inside):
    mail(conn, "root", "2026-08-01T00:00:00Z")
    mail(conn, "reply", inside, parent="root", author="reviewer")
    rebuild_threads(conn)
    before = db.get_top_threads(conn, start, end)
    assert len(before) == 1
    assert before[0]["message_count"] == 1
    assert before[0]["participant_count"] == 1
    assert before[0]["subject"] == "[PATCH] x86 change"
    mail(conn, "future", end, parent="root", author="future reviewer",
         subject="[RFC] arm security entirely different subject",
         body="Reviewed-by: Future Reviewer")
    rebuild_threads(conn)
    assert db.get_top_threads(conn, start, end) == before
    assert db.get_top_threads_for_projects(conn, start, end, ("kvm",), 30) == before
    context = db.get_thread_items_as_of(conn, before[0], end)
    assert [item["native_id"] for item in context] == ["root", "reply"]
    assert all("Reviewed-by" not in item["body_excerpt"] for item in context)
    assert before[0]["url"] == "https://example.org/root"


def test_activity_and_participant_counts_are_window_local(conn):
    mail(conn, "root", "2026-10-01T00:00:00Z")
    for number in range(5):
        mail(conn, f"older{number}", "2026-10-02T00:00:00Z",
             parent="root", author=f"old{number}")
    mail(conn, "in1", "2026-10-11T00:00:00Z", parent="root", author="current")
    mail(conn, "in2", "2026-10-11T01:00:00Z", parent="root", author="current")
    rebuild_threads(conn)
    row, = db.get_top_threads(conn, "2026-10-11T00:00:00Z", "2026-10-12T00:00:00Z")
    assert row["message_count"] == 2
    assert row["participant_count"] == 1
    assert row["patch_count"] == 0
    assert row["kind"] == "patch"  # Proposal type is inherited from its true root.
    assert row["first_seen"] == "2026-10-01T00:00:00Z"
    assert row["last_seen"] == "2026-10-11T01:00:00Z"


def test_project_limits_and_empty_windows(conn):
    mail(conn, "a", "2026-10-11T00:00:00Z", project="kvm")
    mail(conn, "b", "2026-10-11T00:00:00Z", project="qemu-devel")
    rebuild_threads(conn)
    args = (conn, "2026-10-11T00:00:00Z", "2026-10-12T00:00:00Z")
    assert len(db.get_top_threads(*args, limit=1)) == 1
    assert len(db.get_top_threads(*args, limit=0)) == 0
    assert db.get_top_threads_for_projects(*args, projects=(), limit=10) == []
    assert [row["project"] for row in db.get_top_threads_for_projects(
        *args, projects=("qemu-devel",), limit=10)] == ["qemu-devel"]
    assert db.get_top_threads(conn, "2026-10-12T00:00:00Z", "2026-10-13T00:00:00Z") == []


def test_period_review_signal_does_not_use_preperiod_review(conn):
    mail(conn, "root", "2026-10-01T00:00:00Z", body="Reviewed-by: Old Reviewer")
    mail(conn, "reply", "2026-10-11T00:00:00Z", parent="root")
    mail(conn, "other", "2026-10-01T00:00:00Z")
    mail(conn, "other-reply", "2026-10-11T00:00:00Z", parent="other")
    rebuild_threads(conn)
    rows = db.get_top_threads(conn, "2026-10-11T00:00:00Z", "2026-10-12T00:00:00Z")
    assert rows[0]["salience_score"] == rows[1]["salience_score"]
