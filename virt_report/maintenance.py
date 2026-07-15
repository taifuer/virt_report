"""数据库快照备份与恢复。"""
from __future__ import annotations

import gzip
import hashlib
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
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
    """使用 SQLite Online Backup API 生成一致性 gzip 快照。"""
    if not db_path.exists():
        raise FileNotFoundError(f"数据库不存在: {db_path}")
    target = target or default_backup_path(db_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as temp_dir:
        snapshot = Path(temp_dir) / "snapshot.db"
        source = sqlite3.connect(str(db_path))
        destination = sqlite3.connect(str(snapshot))
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        compressed = Path(temp_dir) / "snapshot.db.gz"
        with snapshot.open("rb") as src, gzip.open(compressed, "wb", compresslevel=6) as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)
        os.replace(compressed, target)
    return target, sha256_file(target)


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
