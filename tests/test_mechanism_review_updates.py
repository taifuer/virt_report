"""Keep source-reviewed corrections and observation examples in the articles."""
from pathlib import Path

from virt_report.config import Config
from virt_report.render import render


def test_cpu_overview_separates_vmx_mode_and_privilege():
    html = render.render_mechanisms_html(Config(), "cpu-virtualization")
    assert "VMX root" in html and "VMX non-root" in html
    assert "ring 3" in html and "ring 0" in html
    assert "cpu-vmx-state-controls.svg" in html
    assert "其余通用寄存器等仍由 KVM 保存" in html


def test_notification_race_stays_in_the_network_explanation_without_a_second_player(article_source):
    html = render.render_mechanisms_html(Config(), "network-io")
    assert "data-notification-race" not in html
    assert "先恢复通知，以必要的内存屏障约束顺序，再复查队列" in html
    assert "第 8 项早到，复查会发现它" in html
    assert "晚到，驱动按新的通知条件发出 kick" in html
    assert "hw/virtio/virtio.c#L515-L605" in article_source('network-io')


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


def test_libvirt_start_sequence_includes_host_resources_and_guest_boot():
    html = render.render_mechanisms_html(Config(), "libvirt-domain-start")
    assert "libvirt-domain-start.svg" in html
    assert html.index("libvirt-domain-start.svg") < html.index('id="objects"')
    for marker in ("qcow2 系统盘", "qemuBuildInterfaceConnect", "TAP FD",
                   "qmp_capabilities", "cont", "客户机引导", "STARTED / BOOTED"):
        assert marker in html
    source = (Path(__file__).resolve().parents[1] / "virt_report/content/diagrams"
              / "libvirt-domain-start.mmd").read_text()
    assert source.startswith("sequenceDiagram")
    for marker in ("宿主资源", "客户机固件 / OS", "HandshakeNotify", "qmp_capabilities",
                   "cont", "RESUME", "virtio-blk", "业务服务"):
        assert marker in source
    assert "par 管理侧完成启动" in source
    assert "and 客户机继续引导" in source


def test_qemu_start_has_disk_and_network_backends_before_device_frontends():
    html = render.render_mechanisms_html(Config(), "qemu-vm-startup")
    for marker in ("-blockdev driver=qcow2", "-device virtio-blk-pci", "bootindex=1",
                   "-netdev user,id=net0", "-device virtio-net-pci", "qemu_create_early_backends",
                   "qemu_create_late_backends", "net_init_clients", "qemu_create_cli_devices",
                   "挂载根文件系统", "网络配置失败"):
        assert marker in html
    source = (Path(__file__).resolve().parents[1] / "virt_report/content/diagrams"
              / "qemu-startup-phases.mmd").read_text()
    stages = ("EB[", "MP[", "C[", "D[", "E[", "F[", "G[", "H[", "I[", "J[", "L[")
    offsets = [source.index(stage) for stage in stages]
    assert offsets == sorted(offsets)
    assert "virtio-blk → os-disk" in source
    assert "virtio-net → net0" in source


def test_pcie_hotplug_explains_relation_to_resource_hotplug():
    html = render.render_mechanisms_html(Config(), "qemu-pcie-hotplug")
    assert 'href="hotplug.html"' in html
    assert 'href="x86-vcpu-hotplug.html"' in html
    assert "增加一个 vCPU，需要谁同意" not in render.render_mechanisms_html(
        Config(), "x86-vcpu-hotplug")


def test_interrupt_guide_locates_controllers_and_separates_observation_sides():
    html = render.render_mechanisms_html(Config(), "interrupt-virtualization")
    for marker in ("kernel-irqchip", "on：完整内核 irqchip", "split irqchip",
                   "QEMU，宿主用户态", "KVM，宿主内核", "ioeventfd", "irqfd",
                   "lspci -vv -s 0000:00:03.0", "cat /proc/interrupts",
                   "在客户机执行，只查询状态", "宿主的 <code>/proc/interrupts</code>"):
        assert marker in html
    assert "不能证明宿主启用了 irqfd 或 APICv" in html
    source = (Path(__file__).resolve().parents[1] / "virt_report/content/diagrams"
              / "interrupt-controller-paths.mmd").read_text()
    for group in ("subgraph DEVICE", "subgraph PLATFORM", "subgraph PROCESSOR"):
        assert group in source
    assert "PIN --> IO --> LAPIC" in source
    assert "MSG -->|消息写事务| LAPIC" in source


def test_interrupt_detail_identifies_shared_and_private_state():
    html = render.render_mechanisms_html(Config(), "interrupt-virtualization")
    assert "保存位置与访问方" in html
    for marker in ("客户机 RAM，由 QEMU 映射访问", "QEMU 的 MSI-X 模拟状态",
                   "宿主内核；QEMU 经 ioctl 配置", "KVM 的每 vCPU 状态"):
        assert marker in html


def test_resource_observation_uses_guest_state_and_not_just_qmp_success():
    cpu = render.render_mechanisms_html(Config(), "x86-vcpu-hotplug")
    for marker in ("cat /sys/devices/system/cpu/present",
                   "cat /sys/devices/system/cpu/online", "lscpu -e=CPU,ONLINE"):
        assert marker in cpu
    memory = render.render_mechanisms_html(Config(), "hotplug")
    assert "query-memory-devices" in memory
    assert "lsmem" in memory
    assert "客户机已能分配这些页面" in memory
    pci = render.render_mechanisms_html(Config(), "qemu-pcie-hotplug")
    for marker in ("query-pci", "qdev_id = net1", "lspci -nnk", "ip -br link"):
        assert marker in pci


def test_memory_hotplug_covers_dimm_and_acpi_independently_of_virtio_mem(article_source):
    html = render.render_mechanisms_html(Config(), 'hotplug')
    example = html.split('<section id="example"', 1)[1].split('</section>', 1)[0]
    for marker in ('pc-dimm', 'ACPI', 'online', 'offline', '_EJ0', 'DEVICE_DELETED',
                   '不可迁移页', 'query-memory-devices', 'lsmem'):
        assert marker in example
    assert example.index('内存热添加') < example.index('内存热移除')
    assert 'virtio-mem' not in example
    assert 'virtio-mem 是另一种在线调整方式' in html
    assert 'docs/specs/acpi_mem_hotplug.rst' in article_source('hotplug')
    assert 'Documentation/admin-guide/mm/memory-hotplug.rst' in article_source('hotplug')


def test_pcie_article_connects_registered_callbacks_to_async_removal(article_source):
    html = render.render_mechanisms_html(Config(), "qemu-pcie-hotplug")
    for marker in ("pcie_slot_class_init", "qbus_set_hotplug_handler",
                   "hc-&gt;unplug_request = pcie_cap_slot_unplug_request_cb;",
                   "rp_write_config", "两段处理由插槽寄存器和设备的待删除状态连接"):
        assert marker in html
    assert "hw/pci/pcie_port.c#L214-L225" in article_source('qemu-pcie-hotplug')


def test_vcpu_loop_distinguishes_exit_handling_from_blocking():
    html = render.render_mechanisms_html(Config(), "kvm-vcpu-run")
    for marker in ("qemu_process_cpu_events(cpu)", "if (r &lt;= 0)",
                   "CPUID 与 MMIO 示例都从", "MMIO 返回 0",
                   "vcpu_block()</code> 则处理当前不能运行、需要等待的 vCPU"):
        assert marker in html
    assert "CPUID 走前一分支" not in html


def test_network_article_connects_private_queues_and_tap_host_receive():
    html = render.render_mechanisms_html(Config(), "network-io")
    for marker in ('data-diagram="virtqueue-private-shared"',
                   'id="virtqueue-private-state"', 'id="tap-host-rx"',
                   "struct vring_virtqueue", "split.avail_idx_shadow",
                   "last_avail_idx", "last_used_idx", "file-&gt;private_data",
                   "tun_chr_write_iter", "tun_rx_batched", "netif_receive_skb",
                   "客户机的 TX 在宿主 TAP 接口上表现为 RX"):
        assert marker in html
    assert "netif_rx_ni" not in html


def test_block_article_connects_callback_registration_and_guest_reclamation():
    html = render.render_mechanisms_html(Config(), "qemu-virtio-blk")
    for marker in ('data-diagram="virtio-blk-request-cycle"',
                   'id="request-callbacks"', 'id="guest-completion"',
                   "virtio_queue_rq", "VirtQueue.handle_output", "virtio_blk_rw_complete",
                   "virtblk_done", "blk_mq_rq_from_pdu", "virtblk_request_done",
                   "blk_mq_end_request", "virtblk_poll"):
        assert marker in html
