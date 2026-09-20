"""Backup count defaults and compatibility with explicit legacy retention."""
from datetime import datetime

import pytest
import yaml

from virt_report.config import load_config
from virt_report.scheduler import scheduled_commands


@pytest.mark.parametrize(("settings", "expected"), [
    ({}, ["--keep-count", "1"]),
    ({"backup_keep_count": 2}, ["--keep-count", "2"]),
    ({"backup_keep_days": 7}, ["--keep-days", "7"]),
    ({"backup_keep_days": 0}, []),
    ({"backup_keep_count": 1, "backup_keep_days": 7}, ["--keep-count", "1"]),
    ({"backup_keep_count": 0, "backup_keep_days": 7}, ["--keep-days", "7"]),
])
def test_loaded_backup_retention(tmp_path, settings, expected):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump({"schedule": settings}), encoding="utf-8")
    config = load_config(config_path)
    jobs = dict(scheduled_commands(config, datetime(2026, 9, 20, 1, 5)))
    assert jobs["backup"][2:] == expected
    assert jobs["backup"][1].endswith("backups/auto-2026-09-20.db.gz")
