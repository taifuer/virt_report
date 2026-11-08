"""Run only the small userspace example; never resize an actual virtual machine."""
import hashlib
import json
import re
import shlex
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


def test_balloon_example_separates_requests_observations_and_restore():
    page = render.render_mechanisms_html(Config(), "virtio-balloon")
    parser = CodeBlocks()
    parser.feed(page)
    commands = [json.loads(line) for block in parser.blocks
                if "data-balloon-qmp" in block["attrs"] for line in block["text"].splitlines()]
    assert commands[0] == {"execute": "qmp_capabilities"}
    values = [item["arguments"]["value"] for item in commands if item["execute"] == "balloon"]
    assert values == [768 * 1024**2, 1024**3]
    assert [item["execute"] for item in commands].count("query-balloon") == 3
    assert "可丢弃" in page and "恢复初始" in page and "实机测量结果" in page
    launch = next(block["text"] for block in parser.blocks
                  if "data-balloon-launch" in block["attrs"])
    args = shlex.split(launch)
    assert args[args.index("-machine") + 1] == "q35,accel=kvm,memory-backend=ram"
    assert args[args.index("-m") + 1] == "1024"
    assert args[args.index("-smp") + 1] == "2"
    assert args[args.index("-object") + 1] == "memory-backend-ram,id=ram,size=1G,prealloc=on"
    assert args[args.index("-device") + 1] == "virtio-balloon-pci,free-page-reporting=off"
    assert args[args.index("-qmp") + 1].startswith("unix:${LAB_DIR}/")


@pytest.mark.parametrize("slug", ["memory-virtualization", "virtio-balloon"])
def test_measured_environment_is_separate_from_source_baselines(slug):
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
    for marker in ("三个独立进程", "-static", "不会启动虚拟机",
                   "没有采集 NPT 缺页、VM-Exit 或 TLB 计数", "没有做吞吐或延迟比较"):
        assert marker in document.text()
    assert 'id="first-touch-measured"' in page
    assert 'href="virtio-balloon.html#balloon-measured"' in page


def test_balloon_measurements_preserve_units_and_lazy_residency():
    page = render.render_mechanisms_html(Config(), "virtio-balloon")
    document = Document(page).root
    table = next(node for node in document.find("table")
                 if node.attrs.get("id") == "balloon-measurements")
    assert [cell.text() for cell in table.find("th")] == [
        "采样阶段", "QMP actual（MiB）", "Guest 总量（KiB）", "QEMU RSS（KiB）"]
    # Reviewed samples from the local KVM run on 2026-11-08; no VM in unit tests.
    rows = [[cell.text() for cell in row.find("td")]
            for row in table.find("tbody")[0].find("tr")]
    assert rows == [
        ["初始", "1024", "994728", "1091428"],
        ["回收 256 MiB 后", "768", "732584", "829328"],
        ["恢复 1 GiB 后", "1024", "994728", "829328"],
        ["再次访问后", "1024", "994728", "1091472"],
    ]
    for marker in ("512 MiB", "4096 B", "627608 KiB", "VmRSS", "oom_kill",
                   "pswpin", "pswpout", "各列在同一采样阶段依次读取",
                   "同样的 balloon 调整未必带来相同的 RSS 降幅",
                   "已恢复初始目标并正常关闭测试机", "不作为性能基准"):
        assert marker in document.text()
    assert 'id="balloon-measured"' in page
    assert 'href="memory-virtualization.html#first-touch-measured"' in page
