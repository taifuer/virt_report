"""Function links point at their verified pinned-source declarations."""
import pytest

from virt_report.execution_demos import get_demo


@pytest.mark.parametrize("demo_id,source_id,symbol,path,line", [
    ("interrupt-delivery", "routing", "kvm_set_msi()", "arch/x86/kvm/irq.c", 225),
    ("interrupt-delivery", "allowed", "vmx_interrupt_allowed()", "arch/x86/kvm/vmx/vmx.c", 5048),
    ("interrupt-delivery", "inject", "vmx_inject_irq()", "arch/x86/kvm/vmx/vmx.c", 4915),
    ("interrupt-delivery", "msi-entry", "kvm_apic_mem_write()", "hw/i386/kvm/apic.c", 210),
    ("interrupt-delivery", "used-publish", "virtqueue_split_flush()", "hw/virtio/virtio.c", 1059),
    ("gpu-execution", "vmx-inject", "vmx_inject_irq()", "arch/x86/kvm/vmx/vmx.c", 4915),
    ("network-io", "kvm-msi-route", "kvm_set_msi()", "arch/x86/kvm/irq.c", 225),
    ("network-io", "kvm-msi-inatomic", "kvm_arch_set_irq_inatomic()", "arch/x86/kvm/irq.c", 241),
    ("network-io", "vmx-irq-allowed", "vmx_interrupt_allowed()", "arch/x86/kvm/vmx/vmx.c", 5048),
    ("network-io", "vmx-irq-inject", "vmx_inject_irq()", "arch/x86/kvm/vmx/vmx.c", 4915),
])
def test_interrupt_source_anchors_match_the_named_function(demo_id, source_id, symbol, path, line):
    source = get_demo(demo_id)["sources"][source_id]
    assert source["symbol"] == symbol
    assert source["path"] == path
    assert source["line"] == line
    assert source["url"].split("#", 1)[1].split("-", 1)[0] == f"L{line}"
    assert len(source["revision"]) == 40
