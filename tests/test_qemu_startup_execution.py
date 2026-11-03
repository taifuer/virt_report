"""Protect the firmware, first-fetch and Guest code handoff explanation offline."""
from pathlib import Path

from virt_report.config import Config
from virt_report.mechanisms import get_article
from virt_report.render.render import render_mechanisms_html


ROOT = Path(__file__).resolve().parents[1]


def page():
    return render_mechanisms_html(Config(), "qemu-vm-startup")


def test_startup_outline_includes_firmware_mapping_and_first_userspace():
    article = get_article("qemu-vm-startup")
    ids = [section["id"] for section in article["sections"]]
    assert ids[ids.index("vcpu") + 1:ids.index("objects")] == [
        "firmware", "first-run", "guest-boot",
    ]
    assert {baseline["project"] for baseline in article["baselines"]} == {
        "QEMU", "SeaBIOS", "Linux",
    }


def test_firmware_mapping_matches_the_fixed_256k_image_and_reset_vector():
    html = page()
    for marker in ("-bios bios-256k.bin", "0xfffc0000–0xffffffff", "0x000e0000–0x000fffff",
                   "0x20000–0x3ffff", "0xffff0000 + 0xfff0", "0x3fff0",
                   "ea 5b e0 00 f0", "ljmp $0xf000, $0xe05b", "0x000fe05b"):
        assert marker in html
    # Arithmetic is independent of the configured 4 GiB RAM size.
    rom_base = 2**32 - 256 * 1024
    reset = 0xffff0000 + 0xfff0
    assert reset - rom_base == 0x3fff0
    assert (0xf000 << 4) + int.from_bytes(bytes.fromhex("5b e0"), "little") == 0xfe05b
    assert "不是额外的一份固件" in html
    assert "不是所有 BIOS 的固定入口" in html
    assert "selector × 16" in html and "隐藏基址" in html


def test_first_fetch_is_physical_cpu_execution_not_a_qemu_function_call():
    html = page()
    for marker in ("KVM_SET_REGS", "KVM_SET_SREGS / KVM_SET_SREGS2",
                   "ioctl(KVM_RUN)", "VM-entry", "物理 CPU",
                   "不是 QEMU 用 C 函数调用跳进固件", "一次 KVM_RUN 可以跨过很多 Guest 指令"):
        assert marker in html
    diagram = (ROOT / "virt_report/content/diagrams/qemu-startup-vcpu.mmd").read_text()
    assert "物理 CPU · Guest 上下文" in diagram
    assert "VM-entry：装载 Guest 状态" in diagram
    assert "0xfffffff0，取 ROM 指令" in diagram


def test_kernel_entry_and_userspace_handoff_are_not_conflated_with_reset():
    html = page()
    for marker in ("0x7c00", "boot_params", "startup_32", "startup_64", "start_kernel()",
                   "PID 1", "kernel_execve()", "/init", "/sbin/init", "CPL 从 0 到 3",
                   "VMX 仍是 non-root", "不天然引起 VM-exit", "动态链接器"):
        assert marker in html
    assert "它不是上电后的第一条指令" in html
    assert "不是 QEMU 的函数调用链" in html


def test_alternative_boot_paths_do_not_replace_the_bios_cold_start_story():
    html = page()
    assert "UEFI / OVMF" in html and "pflash" in html
    assert "fw_cfg 与 linuxboot option ROM" in html
    assert "-incoming / -loadvm" in html
    assert "不重新走 BIOS 复位入口" in html
    assert "b52ca86e094d19b58e2304417787e96b940e39c6/src/romlayout.S" in html
