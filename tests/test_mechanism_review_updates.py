"""Keep source-reviewed corrections and observation examples in the articles."""
from virt_report.config import Config
from virt_report.render import render


def test_cpu_overview_separates_vmx_mode_and_privilege():
    html = render.render_mechanisms_html(Config(), "cpu-virtualization")
    assert "VMX root" in html and "VMX non-root" in html
    assert "ring 3" in html and "ring 0" in html
    assert "cpu-vmx-state-controls.svg" in html
    assert "并不自动保存所有通用寄存器" in html


def test_notification_race_has_static_explanation_and_three_scenarios():
    html = render.render_mechanisms_html(Config(), "qemu-network-tx")
    assert "data-notification-race" in html
    for scenario in ("recheck", "broken", "late"):
        assert f'value="{scenario}"' in html
    assert "恢复通知 → 屏障 → 复查队列" in html
    assert "hw/virtio/virtio.c#L515-L605" in html


def test_halt_observation_keeps_existing_subsection_anchor():
    html = render.render_mechanisms_html(Config(), "kvm-vcpu-halt")
    assert 'id="evaluation-trace"' in html
    assert 'id="evaluation-part-1"' in html
    assert 'href="#evaluation-part-1"' in html
    assert "并非性能实测" in html
    assert "这是返回侧记录" in html
    assert "common_pid == $TID" in html


def test_migration_downtime_and_qemu_doc_range_are_corrected():
    migration = (render.TEMPLATES_DIR / "_migration_overview_article.html").read_text()
    qemu = (render.TEMPLATES_DIR / "_qemu_overview_article.html").read_text()
    assert "QMP" in migration and "业务不可用" in migration
    assert "#L51-L146" not in qemu
    assert "#L51-L139" in qemu
