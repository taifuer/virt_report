"""Offline regressions for validated snapshots and bounded automatic retention."""
import gzip
import hashlib
import os
import sqlite3
from argparse import Namespace
from contextlib import contextmanager
from pathlib import Path

import pytest

from virt_report import cli, maintenance
from virt_report.config import Config, Storage
from virt_report.locking import process_lock


def _database(path: Path) -> Path:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE sample (value TEXT)")
        connection.execute("INSERT INTO sample VALUES ('saved')")
    return path


def _archive(directory: Path, day: str, content: bytes = b"snapshot") -> Path:
    path = directory / f"auto-{day}.db.gz"
    path.write_bytes(content)
    return path


def _args(output: Path | None, *, count: int | None = 1, days: int = 0) -> Namespace:
    return Namespace(output=str(output) if output else None,
                     keep_count=count, keep_days=days)


@pytest.mark.parametrize("keep_count", [1, 2])
def test_count_retention_keeps_exact_count_and_protects_new_snapshot(tmp_path, keep_count):
    old = _archive(tmp_path, "2026-08-01")
    future = _archive(tmp_path, "2099-12-31")
    new = _archive(tmp_path, "2026-09-20")
    os.utime(old, (9_000_000_000, 9_000_000_000))
    os.utime(new, (1, 1))

    removed = maintenance.prune_backups_by_count(tmp_path, keep_count, protected=new)

    expected = {new} if keep_count == 1 else {new, future}
    assert set(tmp_path.iterdir()) == expected
    assert set(removed) == {old, future, new} - expected


def test_count_retention_ignores_manual_temporary_invalid_and_nonregular_files(tmp_path):
    new = _archive(tmp_path, "2026-09-20")
    old = _archive(tmp_path, "2026-09-19")
    untouched_names = [
        "manual.db.gz", "virt-report-20260920T000000Z.db.gz",
        "auto-2026-09-18.db.gz.tmp", "auto-2026-09-18.db",
        "auto-2026-9-18.db.gz", "auto-2026-02-30.db.gz",
        "auto-2026-09-18-extra.db.gz", "auto-２０２６-09-18.db.gz",
    ]
    for name in untouched_names:
        (tmp_path / name).write_bytes(b"leave alone")
    directory = tmp_path / "auto-2026-09-17.db.gz"
    directory.mkdir()
    nested = _archive(directory, "2026-09-01")
    symlink = tmp_path / "auto-2026-09-16.db.gz"
    symlink.symlink_to(tmp_path / "manual.db.gz")
    dangling = tmp_path / "auto-2026-09-15.db.gz"
    dangling.symlink_to(tmp_path / "missing")

    assert maintenance.prune_backups_by_count(tmp_path, 1, protected=new) == [old]
    assert new.exists() and directory.is_dir() and nested.exists()
    assert symlink.is_symlink() and dangling.is_symlink()
    for name in untouched_names:
        assert (tmp_path / name).read_bytes() == b"leave alone"


@pytest.mark.parametrize("protected_kind", ["missing", "manual", "directory", "symlink", "outside"])
def test_count_retention_fails_closed_without_regular_automatic_protected_file(
        tmp_path, protected_kind):
    directory = tmp_path / "backups"
    directory.mkdir()
    old = _archive(directory, "2026-09-19")
    protected = directory / "auto-2026-09-20.db.gz"
    if protected_kind == "manual":
        protected = directory / "manual.db.gz"
        protected.write_bytes(b"manual")
    elif protected_kind == "directory":
        protected.mkdir()
    elif protected_kind == "symlink":
        protected.symlink_to(old)
    elif protected_kind == "outside":
        protected = _archive(tmp_path, "2026-09-20")

    with pytest.raises(ValueError):
        maintenance.prune_backups_by_count(directory, 1, protected=protected)
    assert old.read_bytes() == b"snapshot"


@pytest.mark.parametrize("count", [0, -1, True, 1.5, "1"])
def test_count_retention_rejects_invalid_counts_without_deletion(tmp_path, count):
    old = _archive(tmp_path, "2026-09-19")
    protected = _archive(tmp_path, "2026-09-20")
    with pytest.raises(ValueError, match="正整数"):
        maintenance.prune_backups_by_count(tmp_path, count, protected=protected)
    assert old.exists() and protected.exists()


def test_count_retention_refuses_symlink_directory(tmp_path):
    directory = tmp_path / "real"
    directory.mkdir()
    old = _archive(directory, "2026-09-19")
    protected = _archive(directory, "2026-09-20")
    link = tmp_path / "linked"
    link.symlink_to(directory, target_is_directory=True)
    with pytest.raises(ValueError, match="符号链接"):
        maintenance.prune_backups_by_count(link, 1, protected=link / protected.name)
    assert old.exists() and protected.exists()


@pytest.mark.parametrize("stage", ["integrity", "compression", "digest"])
def test_failed_snapshot_preserves_same_day_archive_and_does_not_prune(
        tmp_path, monkeypatch, stage):
    db_path = _database(tmp_path / "source.db")
    previous = _archive(tmp_path, "2026-09-19")
    target = _archive(tmp_path, "2026-09-20", b"previous successful same-day backup")
    original_target = target.read_bytes()

    def fail(*_args, **_kwargs):
        raise RuntimeError("injected failure")

    if stage == "integrity":
        original_connect = sqlite3.connect

        class InvalidSnapshot(sqlite3.Connection):
            def execute(self, sql, parameters=(), /):
                if sql == "PRAGMA integrity_check":
                    return super().execute("SELECT 'injected integrity failure'")
                return super().execute(sql, parameters)

        def invalid_snapshot_connect(database, *args, **kwargs):
            if str(database).endswith("snapshot.db"):
                kwargs["factory"] = InvalidSnapshot
            return original_connect(database, *args, **kwargs)

        monkeypatch.setattr(maintenance.sqlite3, "connect", invalid_snapshot_connect)
    elif stage == "compression":
        monkeypatch.setattr(maintenance.shutil, "copyfileobj", fail)
    else:
        monkeypatch.setattr(maintenance, "sha256_file", fail)
    monkeypatch.setattr(
        maintenance, "prune_backups_by_count",
        lambda *_args, **_kwargs: pytest.fail("failed snapshot must not trigger cleanup"),
    )

    config = Config(storage=Storage(db_path=db_path))
    with pytest.raises(RuntimeError):
        cli.cmd_backup(_args(target), config)

    assert target.read_bytes() == original_target
    assert previous.read_bytes() == b"snapshot"
    assert not any(path.is_dir() for path in tmp_path.iterdir())
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT value FROM sample").fetchone() == ("saved",)


def test_backup_includes_committed_wal_data_and_reports_archive_digest(tmp_path):
    db_path = tmp_path / "source.db"
    source = sqlite3.connect(db_path)
    try:
        source.execute("PRAGMA journal_mode=WAL")
        source.execute("CREATE TABLE sample (value TEXT)")
        source.execute("INSERT INTO sample VALUES ('uncheckpointed')")
        source.commit()
        assert Path(str(db_path) + "-wal").stat().st_size > 0

        archive, digest = maintenance.backup_database(db_path, tmp_path / "snapshot.db.gz")

        assert digest == hashlib.sha256(archive.read_bytes()).hexdigest()
        restored = tmp_path / "restored.db"
        restored.write_bytes(gzip.decompress(archive.read_bytes()))
        with sqlite3.connect(restored) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
            assert connection.execute("SELECT value FROM sample").fetchone() == ("uncheckpointed",)
    finally:
        source.close()


@pytest.mark.parametrize("kind", ["source", "hardlink", "symlink", "wal", "shm", "journal"])
def test_backup_never_overwrites_source_or_sidecar(tmp_path, kind):
    db_path = _database(tmp_path / "source.db")
    original = db_path.read_bytes()
    target = tmp_path / "target.db.gz"
    if kind == "source":
        target = db_path
    elif kind == "hardlink":
        target.hardlink_to(db_path)
    elif kind == "symlink":
        target.symlink_to(db_path)
    else:
        target = Path(str(db_path) + "-" + kind)

    with pytest.raises(ValueError):
        maintenance.backup_database(db_path, target)
    assert db_path.read_bytes() == original
    if kind == "symlink":
        assert target.is_symlink()


@pytest.mark.parametrize("flags", [
    ["--keep-count", "0"], ["--keep-count", "-1"], ["--keep-count", "abc"],
    ["--keep-count", "1", "--keep-days", "7"],
])
def test_invalid_cli_retention_flags_are_rejected_before_configuration_or_mutation(
        tmp_path, monkeypatch, flags):
    monkeypatch.setattr(cli, "load_config", lambda *_: pytest.fail("must fail in parser"))
    target = tmp_path / "auto-2026-09-20.db.gz"
    with pytest.raises(SystemExit) as result:
        cli.main(["backup", str(target), *flags])
    assert result.value.code == 2
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("name", [None, "manual.db.gz", "auto-2026-02-30.db.gz"])
def test_count_cli_rejects_nonautomatic_output_before_writing(tmp_path, name):
    config = Config(storage=Storage(db_path=tmp_path / "source.db"))
    target = tmp_path / name if name else None
    with pytest.raises(ValueError, match="auto-YYYY-MM-DD"):
        cli.cmd_backup(_args(target), config)
    assert not list(tmp_path.iterdir())


def test_count_cli_retains_one_snapshot_and_never_deletes_source(tmp_path):
    db_path = _database(tmp_path / "auto-2026-09-01.db.gz")
    old = _archive(tmp_path, "2026-09-19")
    manual = tmp_path / "manual.db.gz"
    manual.write_bytes(b"manual")
    target = tmp_path / "auto-2026-09-20.db.gz"

    cli.cmd_backup(_args(target), Config(storage=Storage(db_path=db_path)))

    assert target.exists() and db_path.exists() and not old.exists()
    assert manual.read_bytes() == b"manual"
    assert gzip.decompress(target.read_bytes()).startswith(b"SQLite format 3\0")
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT value FROM sample").fetchone() == ("saved",)


def test_backup_lock_covers_snapshot_and_pruning(tmp_path, monkeypatch):
    config = Config(storage=Storage(db_path=tmp_path / "source.db"))
    target = tmp_path / "auto-2026-09-20.db.gz"
    calls = []

    @contextmanager
    def lock(path):
        assert path == config.db_path.parent / "backup.lock"
        calls.append("locked")
        yield
        calls.append("unlocked")

    def backup(*_args):
        assert calls == ["locked"]
        calls.append("backup")
        return target, "digest"

    def prune(*_args, **kwargs):
        assert calls == ["locked", "backup"]
        assert kwargs == {"protected": target, "exclude": (config.db_path,)}
        calls.append("prune")
        return []

    monkeypatch.setattr(cli, "process_lock", lock)
    monkeypatch.setattr(maintenance, "backup_database", backup)
    monkeypatch.setattr(maintenance, "prune_backups_by_count", prune)
    cli.cmd_backup(_args(target), config)
    assert calls == ["locked", "backup", "prune", "unlocked"]


def test_concurrent_backup_is_rejected_before_snapshot(tmp_path, monkeypatch):
    config = Config(storage=Storage(db_path=tmp_path / "source.db"))
    target = tmp_path / "auto-2026-09-20.db.gz"
    monkeypatch.setattr(
        maintenance, "backup_database",
        lambda *_args: pytest.fail("must not start while another backup holds the lock"),
    )
    with process_lock(tmp_path / "backup.lock"):
        with pytest.raises(RuntimeError, match="任务已在运行"):
            cli.cmd_backup(_args(target), config)
    assert not target.exists()


@pytest.mark.parametrize("kind", ["source", "symlink", "hardlink", "wal", "shm", "journal"])
def test_backup_lock_never_truncates_source_or_sidecar(tmp_path, kind):
    lock_path = tmp_path / "backup.lock"
    db_path = _database(lock_path if kind == "source" else tmp_path / "source.db")
    original_source = db_path.read_bytes()
    sidecar = None
    if kind == "symlink":
        lock_path.symlink_to(db_path)
    elif kind == "hardlink":
        lock_path.hardlink_to(db_path)
    elif kind in {"wal", "shm", "journal"}:
        sidecar = Path(str(db_path) + "-" + kind)
        sidecar.write_bytes(b"source sidecar must survive")
        lock_path.hardlink_to(sidecar)
    target = tmp_path / "auto-2026-09-20.db.gz"

    with pytest.raises(ValueError, match="备份锁"):
        cli.cmd_backup(_args(target), Config(storage=Storage(db_path=db_path)))

    assert db_path.read_bytes() == original_source
    assert not target.exists()
    if sidecar is not None:
        assert sidecar.read_bytes() == b"source sidecar must survive"


@pytest.mark.parametrize("kind", ["direct", "symlink", "hardlink"])
def test_backup_target_cannot_replace_held_lock(tmp_path, kind):
    db_path = _database(tmp_path / "source.db")
    lock_path = tmp_path / "backup.lock"
    lock_path.write_bytes(b"existing lock contents")
    target = lock_path
    if kind != "direct":
        target = tmp_path / "manual.db.gz"
        if kind == "symlink":
            target.symlink_to(lock_path)
        else:
            target.hardlink_to(lock_path)

    with pytest.raises(ValueError, match="备份目标不能覆盖备份锁"):
        cli.cmd_backup(_args(target, count=None), Config(storage=Storage(db_path=db_path)))

    assert lock_path.read_bytes() == b"existing lock contents"


def test_legacy_age_retention_preserves_protected_and_symlink_files(tmp_path):
    old = _archive(tmp_path, "2026-09-18")
    protected = _archive(tmp_path, "2026-09-20")
    symlink = tmp_path / "auto-2026-09-19.db.gz"
    symlink.symlink_to(protected)
    for path in (old, protected):
        os.utime(path, (1, 1))

    assert maintenance.prune_backups(tmp_path, 7, protected=protected) == [old]
    assert protected.exists() and symlink.is_symlink()
