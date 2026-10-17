"""Editorial regression checks for migration coordination and the CPR example."""
import json
import re
import subprocess
from html import unescape
from pathlib import Path

from jinja2 import Environment, FileSystemLoader


TEMPLATES = Path(__file__).parents[1] / "virt_report" / "render" / "templates"


def test_libvirt_migration_has_five_phases_and_explicit_failure_boundaries():
    source = (TEMPLATES / "_libvirt_migration_article.html").read_text()
    Environment(loader=FileSystemLoader(TEMPLATES)).parse(source)
    for phase in ("Begin", "Prepare", "Perform", "Finish", "Confirm"):
        assert f"<td>{phase} ·" in source
    for marker in (
        "managed direct", "p2p", "控制面", "数据面", "PERFORM3_DONE",
        "qemuMigrationCookie", "qemuMigrationSrcConfirmPhase",
        "VIR_MIGRATE_POSTCOPY_RESUME", "RPC 响应在网络中丢失",
        "不能证明对方已停止", "持久定义", "只读观察命令",
    ):
        assert marker in source
    assert "4d937284669ecf180abea087d0b3ad67d9476349" in source
    assert "libvirt-migration-phases" in source


def test_cpr_example_shell_is_valid_and_uses_isolated_resources():
    source = (TEMPLATES / "_cpr_article.html").read_text()
    example = source.split('<section id="example"', 1)[1].split("</section>", 1)[0]
    blocks = [unescape(block) for block in re.findall(
        r"<pre><code>(.*?)</code></pre>", example, re.DOTALL,
    )]
    assert len(blocks) == 2
    shell, qmp = blocks
    # Parse, never execute: no QEMU, KVM, sockets or guest resources are started.
    result = subprocess.run(["bash", "-n"], input=shell, text=True,
                            capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    for marker in ("mktemp -d /tmp/qemu-cpr.XXXXXX", 'chmod 700 "$LAB"',
                   "memory-backend-memfd", "aux-ram-share=on",
                   "source.qmp", "target.qmp", "main.sock", "cpr.sock"):
        assert marker in shell
    assert shell.count('"$QEMU" "${COMMON[@]}"') == 2
    assert "未在本文中执行" in example
    assert "不能据此精确测量停机时间" in example
    assert "不要把旧端" in example

    decoder = json.JSONDecoder()
    commands = []
    remaining = qmp.strip()
    while remaining:
        command, end = decoder.raw_decode(remaining)
        commands.append(command)
        remaining = remaining[end:].strip()
    assert [item["execute"] for item in commands] == [
        "qmp_capabilities", "query-status", "migrate-set-parameters", "migrate",
        "query-migrate", "query-status",
    ]
    assert commands[2]["arguments"]["mode"] == "cpr-transfer"
    channels = commands[3]["arguments"]["channels"]
    assert {channel["channel-type"] for channel in channels} == {"main", "cpr"}
    assert all(channel["addr"]["type"] == "unix" for channel in channels)
