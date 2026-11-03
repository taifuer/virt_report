"""Separate released building blocks from dated KVM live-upgrade proposals."""
import re

from virt_report import mechanisms
from virt_report.config import Config, Render, Storage
from virt_report.render import render


def _section(page: str, section: str) -> str:
    return re.search(
        rf'<section id="{section}"[^>]*>(.*?)</section>', page, re.S,
    ).group(1)


def test_kernel_upgrade_has_a_separate_article_and_keeps_the_cpr_entry():
    topic = mechanisms.get_topic("live-upgrade")
    assert topic["overview"] == "qemu-cpr-transfer"
    assert [article["slug"] for article in topic["detail_articles"]] == ["kvm-live-upgrade"]
    assert mechanisms.get_redirect("live-upgrade")["target"] == "qemu-cpr-transfer"
    article = mechanisms.get_article("kvm-live-upgrade")
    assert article["reviewed_on"] == "2026-11-03"
    assert article["baselines"][0]["commit"] == "05f7e89ab9731565d8a62e3b5d1ec206485eeb0b"
    assert "script" not in article and "execution_demo" not in article
    assert len([item for item in mechanisms.articles()
                if item.get("presentation") == "walkthrough"]) == 7
    page = render.render_mechanisms_html(Config(), "qemu-cpr-transfer")
    assert 'href="kvm-live-upgrade.html"' in page
    assert 'href="kvm-live-upgrade.html#cpr-luo"' in page


def test_kernel_upgrade_distinguishes_code_patching_and_state_handoff():
    page = render.render_mechanisms_html(Config(), "kvm-live-upgrade")
    preface = _section(page, "preface")
    for phrase in ("Guest 不重启", "vCPU 持续执行", "业务无停顿", "memfd"):
        assert phrase in preface
    assert "不是主线通用模块升级接口" in preface
    patching = _section(page, "livepatch")
    assert "VM 和 vCPU 不因此被重新创建" in patching
    assert "一致性模型" in patching
    dual = _section(page, "dual-kvm")
    assert "不随此次升级替换" in dual and "kvm.ko" in dual
    assert "硬件资源协调" in dual and "DMA" in dual
    handoff = _section(page, "kernel-handoff")
    for phrase in ("scratch", "fd 数值不必", "重新创建 KVM", "额外实现", "VFIO"):
        assert phrase in handoff


def test_patch_examples_keep_pause_and_device_limitations_explicit():
    page = render.render_mechanisms_html(Config(), "kvm-live-upgrade")
    cpr = _section(page, "cpr-luo")
    for phrase in ("2026-10-12", "postmigrate", "不能提前退出", "禁止 VFIO",
                   "不继续运行指令"):
        # The release-state summary and detailed explanation must agree.
        assert phrase in cpr + _section(page, "preface")
    assert "QEMU 10.2" in cpr
    caretaker = _section(page, "caretaker")
    for phrase in ("2026-09-20", "概念验证", "不能处理的退出", "等待新宿主", "Idle Passthrough"):
        assert phrase in caretaker
    compatibility = _section(page, "compatibility")
    assert "2026-10-19" in compatibility and "不代表功能已发布" in compatibility
    assert "不是本文执行过" in compatibility
    assert "旧端不能直接继续运行" in compatibility
    assert not re.search(r"(?:sudo |system\(|exec\().*\b(?:kexec|rmmod)\b", page)


def test_kernel_upgrade_exports_without_runtime_data(tmp_path):
    config = Config(storage=Storage(db_path=tmp_path / "no-database/report.db"),
                    render=Render(output_dir=tmp_path / "site"))
    render.render_mechanisms(config)
    page = (config.output_dir / "mechanisms/kvm-live-upgrade.html").read_text()
    assert 'href="qemu-cpr-transfer.html"' in page
    assert 'href="#caretaker"' in page
    assert "2026-10-12" in page
    assert not config.db_path.parent.exists()
