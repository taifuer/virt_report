"""Offline checks for the live block-operation examples and lifecycle contract."""
import json
import re
from html import unescape
from pathlib import Path

from jinja2 import Environment, FileSystemLoader


TEMPLATES = Path(__file__).parents[1] / "virt_report" / "render" / "templates"


def test_live_block_article_distinguishes_snapshot_view_and_job_completion():
    source = (TEMPLATES / "_block_operations_article.html").read_text()
    Environment(loader=FileSystemLoader(TEMPLATES)).parse(source)
    for marker in (
        "快照时刻的磁盘视图由 <code>A + B</code>", "0x1000", "v1", "v2", "v3",
        "copy-on-read", "copy-before-write", "active commit", "READY",
        "CONCLUDED", "auto-finalize:false", "auto-dismiss:false",
        "已写入 base 的数据不会自动撤销", "仍不会 pivot", "实际块图",
        "不能按页面顺序连续执行", "block-live-operations", "block-job-pivot",
        "运行时块图与镜像头部", "并不会自动把 B 的路径写进",
        "job-cancel", "force=true", "force:false", "block-job-cancel",
    ):
        assert marker in source
    assert "698104725efad4b29079d857dfdebbd804e34c99" in source


def test_live_block_qmp_examples_are_valid_and_keep_results_for_inspection():
    source = (TEMPLATES / "_block_operations_article.html").read_text()
    commands = []
    decoder = json.JSONDecoder()
    for block in re.findall(r"<pre><code>(.*?)</code></pre>", source, re.DOTALL):
        remaining = unescape(block).strip()
        while remaining:
            command, end = decoder.raw_decode(remaining)
            commands.append(command)
            remaining = remaining[end:].strip()
    assert commands[0]["execute"] == "blockdev-add"
    assert commands[0]["arguments"]["backing"] is None
    assert commands[1]["arguments"] == {"node": "node-B", "overlay": "node-C"}
    jobs = {command["execute"]: command["arguments"] for command in commands
            if command["execute"] in ("block-stream", "block-commit", "blockdev-mirror")}
    assert len(jobs) == 3
    assert all(job["auto-dismiss"] is False for job in jobs.values())
    assert "base-node" not in jobs["block-stream"]
    assert jobs["block-commit"]["top-node"] == "node-B"
    assert jobs["block-commit"]["base-node"] == "node-A"
    assert jobs["blockdev-mirror"]["sync"] == "full"
    assert jobs["blockdev-mirror"]["target"] == "node-T"
    complete = next(command for command in commands if command["execute"] == "job-complete")
    assert complete["arguments"]["id"] == jobs["blockdev-mirror"]["job-id"]
