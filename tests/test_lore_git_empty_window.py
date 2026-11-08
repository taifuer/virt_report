"""Idle public-inbox archives must not look like network failures or lose history."""
import os
import subprocess
from datetime import datetime, timezone

import pytest

from virt_report import db
from virt_report.collectors import lore_git
from virt_report.config import MailingListSource


SINCE = datetime(2026, 11, 5, tzinfo=timezone.utc)
URL = "https://example.test/virtio-dev/git/0.git"


@pytest.mark.parametrize("existing", [False, True])
def test_idle_tip_confirms_empty_window_without_changing_cached_history(tmp_path, monkeypatch, existing):
    monkeypatch.setattr(lore_git, "CACHE_DIR", tmp_path)
    cache = tmp_path / "virtio-dev-0.git"
    if existing:
        cache.mkdir()
        (cache / "HEAD").write_text("previous-head")
    calls = []

    def run(args, *, git_dir=None):
        calls.append((args, git_dir))
        if any(arg.startswith("--shallow-since=") for arg in args):
            raise subprocess.CalledProcessError(128, args, stderr=b"fatal: error processing shallow info: 4")
        if args[0] == "clone":
            assert "--depth=1" in args
            return b""
        assert args == ["log", "-1", "--format=%ct", "HEAD"]
        return str(int(datetime(2026, 10, 31, tzinfo=timezone.utc).timestamp())).encode()

    monkeypatch.setattr(lore_git, "_run_git", run)
    assert lore_git._ensure_repo(URL, "virtio-dev", SINCE) is None
    assert len(calls) == 3
    assert not list(tmp_path.glob("tip-probe-*"))
    if existing:
        assert (cache / "HEAD").read_text() == "previous-head"
    else:
        assert not cache.exists()


@pytest.mark.parametrize("stderr,confirm", [
    (b"fatal: HTTP 403", None),
    (b"fatal: error processing shallow info: 4", False),
])
def test_unverified_errors_do_not_advance_watermark(tmp_path, monkeypatch, stderr, confirm):
    monkeypatch.setattr(lore_git, "CACHE_DIR", tmp_path / "cache")
    failure = subprocess.CalledProcessError(128, ["git"], stderr=stderr)

    def fail(*args, **kwargs):
        raise failure

    def probe(*args):
        assert confirm is not None, "HTTP failures must not trigger a tip probe"
        return confirm

    monkeypatch.setattr(lore_git, "_run_git", fail)
    monkeypatch.setattr(lore_git, "_confirm_empty_window", probe)
    conn = db.connect(tmp_path / "db.sqlite")
    try:
        with pytest.raises(subprocess.CalledProcessError) as caught:
            lore_git.fetch(conn, MailingListSource("virtio-dev", "lore_git", URL), since=SINCE)
        assert caught.value is failure
        assert db.get_fetch_state(conn, "ml", "virtio-dev") == {
            "last_fetch_at": None, "last_seen_id": None,
        }
    finally:
        conn.close()


def test_tip_probe_requires_a_commit_strictly_before_the_buffered_cutoff(tmp_path, monkeypatch):
    monkeypatch.setattr(lore_git, "CACHE_DIR", tmp_path)
    cutoff = datetime(2026, 11, 4, tzinfo=timezone.utc)
    monkeypatch.setattr(lore_git, "_run_git", lambda args, **kw: (
        str(int(cutoff.timestamp())).encode() if args[0] == "log" else b""))
    assert not lore_git._confirm_empty_window(URL, cutoff)


def test_probe_failure_is_not_an_empty_window(tmp_path, monkeypatch):
    monkeypatch.setattr(lore_git, "CACHE_DIR", tmp_path)

    def fail(args, **kwargs):
        raise subprocess.CalledProcessError(128, args, stderr=b"fatal: HTTP 403")

    monkeypatch.setattr(lore_git, "_run_git", fail)
    with pytest.raises(subprocess.CalledProcessError):
        lore_git._confirm_empty_window(URL, SINCE)
    assert not list(tmp_path.glob("tip-probe-*"))


def test_verified_empty_window_records_success_without_parsing_old_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(lore_git, "_ensure_repo", lambda *args: None)

    def unexpected(*args):
        raise AssertionError("No cached commits should be read for a verified empty window")

    monkeypatch.setattr(lore_git, "_commit_ids", unexpected)
    conn = db.connect(tmp_path / "db.sqlite")
    try:
        assert lore_git.fetch(conn, MailingListSource("virtio-dev", "lore_git", URL), since=SINCE) == 0
        assert db.get_fetch_state(conn, "ml", "virtio-dev")["last_seen_id"] == "complete"
    finally:
        conn.close()


def test_local_git_archive_handles_idle_active_and_idle_again(tmp_path, monkeypatch):
    """Exercise Git's actual shallow-window failure using only a local file URL."""
    monkeypatch.setattr(lore_git, "CACHE_DIR", tmp_path / "cache")
    upstream = tmp_path / "upstream"

    def git(*args, date=None):
        env = dict(os.environ)
        if date:
            env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
        return subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.test",
                               *args], check=True, capture_output=True, env=env).stdout

    git("init", str(upstream))
    git("-C", str(upstream), "commit", "--allow-empty", "-m", "old", date="2026-10-30T00:00:00Z")
    url = upstream.as_uri()
    assert lore_git._ensure_repo(url, "test", SINCE) is None
    git("-C", str(upstream), "commit", "--allow-empty", "-m", "new", date="2026-11-06T00:00:00Z")
    cache = lore_git._ensure_repo(url, "test", SINCE)
    assert cache is not None
    commits = lore_git._commit_ids(cache, SINCE)
    assert len(commits) == 1
    assert lore_git._ensure_repo(url, "test", datetime(2026, 11, 10, tzinfo=timezone.utc)) is None
    assert lore_git._commit_ids(cache, SINCE) == commits
