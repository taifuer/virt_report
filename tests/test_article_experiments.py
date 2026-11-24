"""Run only the small userspace example; never resize an actual virtual machine."""
import hashlib
import re
import shutil
import subprocess
from html.parser import HTMLParser

import pytest

from virt_report import mechanisms
from virt_report.config import Config
from virt_report.render import render
from virt_report.versions import Document


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


def test_measured_environment_is_separate_from_source_baselines():
    slug = "memory-virtualization"
    page = render.render_mechanisms_html(Config(), slug)
    document = Document(page).root
    experiment = next(node for node in document.find("section")
                      if node.attrs.get("id") == "experiment")
    text = experiment.text()
    for marker in ("2026-11-08", "AMD Ryzen 5 5600G", "26300.9818", "WSL2",
                   "6.18.54.1-microsoft-standard-WSL2", "10.1.50", "g9febfa94b6",
                   "6.18.60-0-virt", "2 个 vCPU", "1 GiB", "prealloc=on",
                   "free-page-reporting=off", "query-kvm", "enabled=true",
                   "present=true", "AMD NPT", "源码基线", "未配置 swap"):
        assert marker in text
    baselines = {item["project"]: item["version"]
                 for item in mechanisms.get_article(slug)["baselines"]}
    assert baselines == {"QEMU": "10.2.0", "Linux": "6.18"}
    assert "本文不附 KVM 客户机的实测计数" not in text
    assert "没有把命令示例当成实机测量结果" not in text


def test_first_touch_observations_keep_the_measured_code_and_counts():
    page = render.render_mechanisms_html(Config(), "memory-virtualization")
    parser = CodeBlocks()
    parser.feed(page)
    code = next(block["text"] for block in parser.blocks
                if block["attrs"].get("id") == "first-touch-example")
    # Pin the tested source, not the outcome of the current host run above.
    assert hashlib.sha256(code.encode()).hexdigest() == (
        "bba5f11c2b766196fb8052947c5b9017823f56848b429fe887bb82b1dde646ba")
    document = Document(page).root
    table = next(node for node in document.find("table")
                 if node.attrs.get("id") == "first-touch-measurements")
    assert [[cell.text() for cell in row.find("td")]
            for row in table.find("tbody")[0].find("tr")] == [
        [f"第 {number} 次", "4096", "8192", "0"] for number in range(1, 4)]
    for marker in ("三个独立进程", "-static", "独立的 KVM 客户机实验",
                   "普通 Linux 用户态", "first_minor", "EPT/NPT 缺页", "吞吐和延迟"):
        assert marker in document.text()
    assert 'id="first-touch-measured"' in page
    assert "virtio-balloon.html" not in page
