"""Run only the small userspace example; never resize an actual virtual machine."""
import json
import re
import shutil
import subprocess
from html.parser import HTMLParser

import pytest

from virt_report.config import Config
from virt_report.render import render


class CodeBlocks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.blocks = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        if tag == "code":
            self.current = {"attrs": dict(attrs), "text": ""}

    def handle_data(self, text):
        if self.current is not None:
            self.current["text"] += text

    def handle_endtag(self, tag):
        if tag == "code" and self.current is not None:
            self.blocks.append(self.current)
            self.current = None


def test_first_touch_example_compiles_and_runs_as_an_unprivileged_process(tmp_path):
    compiler = shutil.which("cc")
    if not compiler:
        pytest.skip("A C compiler is needed to verify the article example")
    parser = CodeBlocks()
    parser.feed(render.render_mechanisms_html(Config(), "memory-virtualization"))
    code = next(block["text"] for block in parser.blocks
                if block["attrs"].get("id") == "first-touch-example")
    source, executable = tmp_path / "first-touch.c", tmp_path / "first-touch"
    source.write_text(code, encoding="utf-8")
    subprocess.run([compiler, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
                    str(source), "-o", str(executable)], check=True, capture_output=True, timeout=30)
    result = subprocess.run([str(executable)], text=True, capture_output=True, timeout=10)
    if result.returncode and "madvise: Invalid argument" in result.stderr:
        pytest.skip("This kernel lacks MADV_NOHUGEPAGE support; compilation passed")
    assert result.returncode == 0, result.stderr
    counts = dict(re.findall(r"(\w+)=(\d+)", result.stdout))
    assert int(counts["page_bytes"]) > 0
    assert int(counts["first_minor"]) > 0
    assert int(counts["second_minor"]) >= 0  # No environment-specific performance assertion.


def test_balloon_example_separates_requests_observations_and_restore():
    page = render.render_mechanisms_html(Config(), "virtio-balloon")
    parser = CodeBlocks()
    parser.feed(page)
    commands = [json.loads(line) for block in parser.blocks
                if "data-balloon-qmp" in block["attrs"] for line in block["text"].splitlines()]
    assert commands[0] == {"execute": "qmp_capabilities"}
    values = [item["arguments"]["value"] for item in commands if item["execute"] == "balloon"]
    assert values == [6 * 1024**3, 8 * 1024**3]
    assert [item["execute"] for item in commands].count("query-balloon") == 3
    assert "可丢弃" in page and "恢复初始" in page and "实机测量结果" in page
