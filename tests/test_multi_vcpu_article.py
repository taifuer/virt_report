"""Offline contracts for the SMP lifecycle and its runnable pthread example."""
from html import unescape
import re
import shutil
import subprocess

import pytest

from virt_report import editorial_search, mechanisms
from virt_report.config import Config
from virt_report.render import render


SLUG = "multi-vcpu-coordination"


def page():
    return render.render_mechanisms_html(Config(), SLUG)


def test_multi_vcpu_article_is_discoverable_with_pinned_sources():
    article = mechanisms.get_article(SLUG)
    assert article["topic"] == "cpu-virtualization"
    assert [item["id"] for item in article["sections"]] == [
        "preface", "machine", "discovery", "firmware", "kernel",
        "cooperation", "scheduling", "application", "verification", "sources",
    ]
    assert {item["project"] for item in article["baselines"]} == {
        "QEMU", "Linux", "SeaBIOS",
    }
    html = page()
    assert all(f'/blob/{item["commit"]}/' in html for item in article["baselines"])
    assert f'mechanisms/{SLUG}.html' in render.render_mechanisms_html(Config())
    assert any(item["slug"] == SLUG for item in editorial_search.find("SMP")["articles"])


@pytest.mark.parametrize("slug", ["cpu-virtualization", "qemu-vm-startup", "kvm-vcpu-run"])
def test_existing_cpu_articles_link_to_the_smp_narrative(slug):
    assert f'href="{SLUG}.html"' in render.render_mechanisms_html(Config(), slug)


def test_firmware_and_linux_have_distinct_ap_startup_and_online_stages():
    html = page()
    for marker in ("SMPLock", "handle_smp()", "HLT 循环停驻",
                   "start_secondary()", "bringup_nonboot_cpus()", "CPUHP_AP_ONLINE_IDLE",
                   "INIT/SIPI", "MP Services", "并行阶段"):
        assert marker in html
    firmware = html.split('<section id="firmware"', 1)[1].split('</section>', 1)[0]
    kernel = html.split('<section id="kernel"', 1)[1].split('</section>', 1)[0]
    assert "SMPLock" in firmware and "entry_smp" in firmware
    assert "smp_init()" in kernel and "Linux CPU 上线流程完成" in kernel
    assert html.index('id="firmware"') < html.index('id="kernel"') < html.index('id="application"')


def test_guest_notifications_and_host_scheduling_keep_their_boundaries():
    html = page()
    for marker in ("MADT", "CPUID", "Guest CPU 编号、APIC ID、QEMU cpu-index",
                   "Guest IPI", "KVM 内部请求与宿主 IPI", "APICv / AVIC",
                   "并不必然导致", "KVM_RUN", "volatile", "Guest affinity",
                   "宿主 affinity", "steal time", "锁持有者被抢占"):
        assert marker in html
    assert 'data-diagram="multi-vcpu-boot"' in html
    assert 'data-diagram="multi-vcpu-scheduling"' in html


def test_observation_commands_distinguish_tids_online_cpus_and_measurements():
    html = page()
    for marker in ("kernel-irqchip=on", "-snapshot", "-S", "query-cpus-fast",
                   "props.thread-id", "ps -L", "lscpu -e=CPU,ONLINE,SOCKET,CORE,NODE",
                   "/sys/devices/system/cpu/possible", "/sys/devices/system/cpu/present",
                   "/sys/devices/system/cpu/online", "/proc/interrupts",
                   "瞬时采样", "机制示意", "区分宿主验证与 Guest 内验证"):
        assert marker in html


@pytest.mark.skipif(not shutil.which("cc"), reason="C compiler not installed")
def test_article_pthread_example_compiles_and_partitions_the_same_work(tmp_path):
    code = re.search(r'<code id="multi-vcpu-worker-code">(.*?)</code>', page(), re.S)
    assert code is not None
    source = tmp_path / "vcpu-workers.c"
    source.write_text(unescape(code.group(1)), encoding="utf-8")
    binary = tmp_path / "vcpu-workers"
    # An uneven, bounded work size exercises partitioning without a CI benchmark.
    count = 10003
    subprocess.run([
        shutil.which("cc"), "-O2", "-std=c11", "-Wall", "-Wextra", "-Werror", "-pthread",
        f"-DWORK_ITEMS={count}", str(source), "-o", str(binary),
    ], check=True, capture_output=True, text=True, timeout=20)
    mask = (1 << 64) - 1
    expected = 0
    for i in range(count):
        value = (i + 0x9e3779b97f4a7c15) & mask
        value = ((value ^ (value >> 30)) * 0xbf58476d1ce4e5b9) & mask
        value = ((value ^ (value >> 27)) * 0x94d049bb133111eb) & mask
        expected = (expected + (value ^ (value >> 31))) & mask
    for workers in (1, 4):
        result = subprocess.run([str(binary), str(workers)], check=True, capture_output=True,
                                text=True, timeout=5)
        assert f"workers={workers} checksum={expected}" in result.stdout
        assert len(re.findall(r"worker=\d+ guest_tid=\d+ guest_cpu=", result.stdout)) == workers
    for args in ([], ["2"], ["4", "extra"]):
        result = subprocess.run([str(binary), *args], capture_output=True, text=True, timeout=5)
        assert result.returncode == 2
        assert "usage:" in result.stderr
