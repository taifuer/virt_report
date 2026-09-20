"""数据库快照备份与恢复。"""
from __future__ import annotations

import gzip
import hashlib
import os
import re
import shutil
import sqlite3
import stat
import tempfile
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def default_backup_path(db_path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return db_path.parent / "backups" / f"virt-report-{stamp}.db.gz"


def backup_database(db_path: Path, target: Path | None = None) -> tuple[Path, str]:
    """Validate and compress an online SQLite snapshot before atomic replacement."""
    if not db_path.exists():
        raise FileNotFoundError(f"数据库不存在: {db_path}")
    target = target or default_backup_path(db_path)
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise ValueError(f"备份目标必须是普通文件，不能是符号链接或目录: {target}")
    source_path = db_path.resolve()
    source_files = {Path(str(source_path) + suffix)
                    for suffix in ("", "-wal", "-shm", "-journal")}
    if (target.resolve() in source_files
            or (target.exists() and target.samefile(db_path))):
        raise ValueError("备份目标不能覆盖源数据库或其日志文件")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as temp_dir:
        snapshot = Path(temp_dir) / "snapshot.db"
        source_uri = db_path.resolve().as_uri() + "?mode=ro"
        with (
            closing(sqlite3.connect(source_uri, uri=True)) as source,
            closing(sqlite3.connect(str(snapshot))) as destination,
        ):
            source.backup(destination)
            result = destination.execute("PRAGMA integrity_check").fetchall()
            if result != [("ok",)]:
                raise RuntimeError(f"备份数据库完整性检查失败: {result}")
        compressed = Path(temp_dir) / "snapshot.db.gz"
        with snapshot.open("rb") as src, gzip.open(compressed, "wb", compresslevel=6) as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)
        digest = sha256_file(compressed)
        with compressed.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(compressed, target)
        directory_fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    return target, digest


def is_automatic_backup_path(path: Path) -> bool:
    """Match only canonical dated automatic snapshot names, not manual files."""
    match = re.fullmatch(r"auto-([0-9]{4}-[0-9]{2}-[0-9]{2})\.db\.gz", path.name)
    if not match:
        return False
    try:
        date.fromisoformat(match.group(1))
    except ValueError:
        return False
    return True


def _regular_file_identity(path: Path) -> tuple[int, int] | None:
    try:
        status = path.lstat()
    except FileNotFoundError:
        return None
    if stat.S_ISREG(status.st_mode):
        return status.st_dev, status.st_ino
    return None


def _real_directory(directory: Path) -> Path:
    """Reject cleanup paths that traverse a symlink, including parent components."""
    absolute = Path(os.path.abspath(directory))
    if absolute.resolve() != absolute or not absolute.is_dir():
        raise ValueError(f"备份清理目录必须是真实目录，不能经过符号链接: {directory}")
    return absolute


def prune_backups_by_count(directory: Path, keep_count: int, *, protected: Path,
                           exclude: tuple[Path, ...] = ()) -> list[Path]:
    """Keep the new snapshot plus the newest canonical dates, ignoring mtimes.

    Callers must hold the backup lock across snapshot creation and cleanup.
    A missing or unsafe protected snapshot aborts before any deletion.
    """
    if isinstance(keep_count, bool) or not isinstance(keep_count, int) or keep_count < 1:
        raise ValueError("keep_count 必须是正整数")
    absolute_directory = _real_directory(directory)
    protected = Path(os.path.abspath(protected))
    protected_identity = _regular_file_identity(protected)
    if (protected.parent != absolute_directory
            or not is_automatic_backup_path(protected)
            or protected_identity is None):
        raise ValueError("必须保留同一目录内已成功生成的普通 auto-YYYY-MM-DD.db.gz 备份")
    excluded = {path.resolve() for path in exclude}
    identities = {
        path: _regular_file_identity(path)
        for path in absolute_directory.iterdir()
        if is_automatic_backup_path(path) and path != protected
    }
    candidates = sorted(
        (path for path, identity in identities.items()
         if identity is not None and path.resolve() not in excluded),
        key=lambda path: path.name,
        reverse=True,
    )
    removed = []
    for path in candidates[keep_count - 1:]:
        if _regular_file_identity(protected) != protected_identity:
            raise ValueError("新备份已不存在或已改变，停止清理")
        if _regular_file_identity(path) == identities[path]:
            path.unlink()
            removed.append(directory / path.name)
    return removed


def prune_backups(directory: Path, keep_days: int, *, prefix: str = "auto-",
                  protected: Path | None = None,
                  exclude: tuple[Path, ...] = ()) -> list[Path]:
    """Remove expired automatic snapshots without touching manual backups."""
    if keep_days <= 0 or not directory.exists():
        return []
    _real_directory(directory)
    excluded = {path.resolve() for path in exclude}
    if protected is not None:
        excluded.add(protected.resolve())
    cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
    removed = []
    for path in directory.glob(f"{prefix}*.db.gz"):
        if _regular_file_identity(path) is None or path.resolve() in excluded:
            continue
        modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        if modified < cutoff:
            path.unlink()
            removed.append(path)
    return removed


def restore_database(db_path: Path, archive: Path, *, expected_sha256: str | None = None,
                     force: bool = False) -> tuple[Path | None, str]:
    """校验并原子恢复 gzip SQLite 快照；已有数据库会先自动备份。"""
    if not force:
        raise RuntimeError("恢复会替换当前数据库，请添加 --force")
    archive = archive.resolve()
    actual_sha256 = sha256_file(archive)
    if expected_sha256 and actual_sha256.lower() != expected_sha256.lower():
        raise RuntimeError("备份文件 SHA-256 校验失败")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    previous: Path | None = None
    with tempfile.TemporaryDirectory(dir=db_path.parent) as temp_dir:
        restored = Path(temp_dir) / "restored.db"
        with gzip.open(archive, "rb") as src, restored.open("wb") as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)
        connection = sqlite3.connect(str(restored))
        try:
            result = connection.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            connection.close()
        if result != "ok":
            raise RuntimeError(f"备份数据库完整性检查失败: {result}")
        if db_path.exists():
            previous, _digest = backup_database(db_path)
        for suffix in ("-wal", "-shm"):
            Path(str(db_path) + suffix).unlink(missing_ok=True)
        os.replace(restored, db_path)
    return previous, actual_sha256
