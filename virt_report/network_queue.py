"""Typed split-ring snapshots and source paths for the offline network lesson.

The narrative JSON owns the packet/application story. This module adds explicit
queue events, never reconstructing device state by parsing translated prose.
Addresses are teaching GPAs; the ring and buffer layout is pinned separately
from the backend implementation.
"""
from __future__ import annotations

from copy import deepcopy
from urllib.parse import urlsplit

from virt_report.network_presentation import clarify_network_presentation


LINUX = "7d0a66e4bb9081d75c82ec4957c50034cb0ea449"
QEMU = "698104725efad4b29079d857dfdebbd804e34c99"
DPDK = "1b3bf1128d9bda5595861814792f74b8f57160c8"
RX_CAPACITY = 1530  # hdr_len + ETH_HLEN + VLAN_HLEN + ETH_DATA_LEN


def _source(project: str, path: str, line: int, symbol: str) -> dict:
    revision, repository = {
        "Linux": (LINUX, "torvalds/linux"),
        "QEMU": (QEMU, "qemu/qemu"),
        "DPDK": (DPDK, "DPDK/dpdk"),
    }[project]
    return {
        "label": f"{project} · {symbol}", "symbol": symbol,
        "path": path, "line": line, "project": project, "revision": revision,
        "url": f"https://github.com/{repository}/blob/{revision}/{path}#L{line}",
    }


# Every entry is an implementation symbol, not a translated topic label.
FUNCTIONS = {
    "guest-sendto": ("Linux", "net/socket.c", 2209, "__sys_sendto()"),
    "guest-sock-send": ("Linux", "net/socket.c", 737, "__sock_sendmsg()"),
    "guest-udp-send": ("Linux", "net/ipv4/udp.c", 1270, "udp_sendmsg()"),
    "guest-copy-send": ("Linux", "net/ipv4/ip_output.c", 933, "ip_generic_getfrag()"),
    "guest-ip-output": ("Linux", "net/ipv4/ip_output.c", 200, "ip_finish_output2()"),
    "guest-recvfrom": ("Linux", "net/socket.c", 2269, "__sys_recvfrom()"),
    "guest-sock-recv": ("Linux", "net/socket.c", 1096, "sock_recvmsg()"),
    "guest-udp-recv": ("Linux", "net/ipv4/udp.c", 2053, "udp_recvmsg()"),
    "guest-udp-wait": ("Linux", "net/ipv4/udp.c", 1952, "__skb_recv_udp()"),
    "guest-udp-rcv": ("Linux", "net/ipv4/udp.c", 2906, "udp_rcv()"),
    "guest-udp-unicast": ("Linux", "net/ipv4/udp.c", 2642, "udp_unicast_rcv_skb()"),
    "guest-udp-enqueue": ("Linux", "net/ipv4/udp.c", 1699, "__udp_enqueue_schedule_skb()"),
    "guest-napi-input": ("Linux", "drivers/net/virtio_net.c", 2577, "virtnet_receive_done()"),
    "guest-gro-normal": ("Linux", "net/core/gro.c", 598, "gro_skb_finish()"),
    "guest-napi-complete": ("Linux", "net/core/dev.c", 6649, "napi_complete_done()"),
    "net-start-xmit": ("Linux", "drivers/net/virtio_net.c", 3369, "start_xmit()"),
    "net-xmit-skb": ("Linux", "drivers/net/virtio_net.c", 3315, "xmit_skb()"),
    "net-add-out": ("Linux", "drivers/net/virtio_net.c", 571, "virtnet_add_outbuf()"),
    "ring-add-out": ("Linux", "drivers/virtio/virtio_ring.c", 2337, "virtqueue_add_outbuf()"),
    "ring-add-split": ("Linux", "drivers/virtio/virtio_ring.c", 533, "virtqueue_add_split()"),
    "ring-kick": ("Linux", "drivers/virtio/virtio_ring.c", 2468, "virtqueue_kick_prepare()"),
    "ring-notify": ("Linux", "drivers/virtio/virtio_ring.c", 2485, "virtqueue_notify()"),
    "pci-notify": ("Linux", "drivers/virtio/virtio_pci_common.c", 51, "vp_notify()"),
    "kvm-ioeventfd": ("Linux", "virt/kvm/eventfd.c", 804, "ioeventfd_write()"),
    "kvm-irqfd": ("Linux", "virt/kvm/eventfd.c", 195, "irqfd_wakeup()"),
    "kvm-msi-route": ("Linux", "arch/x86/kvm/irq.c", 225, "kvm_set_msi()"),
    "kvm-msi-inatomic": ("Linux", "arch/x86/kvm/irq.c", 241, "kvm_arch_set_irq_inatomic()"),
    "vmx-pending": ("Linux", "arch/x86/kvm/vmx/vmx.c", 4242, "vmx_deliver_interrupt()"),
    "vmx-irq-allowed": ("Linux", "arch/x86/kvm/vmx/vmx.c", 5048, "vmx_interrupt_allowed()"),
    "lapic-ack": ("Linux", "arch/x86/kvm/lapic.c", 3085, "kvm_apic_ack_interrupt()"),
    "vmx-irq-inject": ("Linux", "arch/x86/kvm/vmx/vmx.c", 4915, "vmx_inject_irq()"),
    "guest-ring-irq": ("Linux", "drivers/virtio/virtio_ring.c", 2693, "vring_interrupt()"),
    "guest-edge-irq": ("Linux", "kernel/irq/chip.c", 823, "handle_edge_irq()"),
    "net-rx-poll": ("Linux", "drivers/net/virtio_net.c", 3114, "virtnet_poll()"),
    "net-receive": ("Linux", "drivers/net/virtio_net.c", 3022, "virtnet_receive()"),
    "net-receive-packets": ("Linux", "drivers/net/virtio_net.c", 2994, "virtnet_receive_packets()"),
    "net-rq-get": ("Linux", "drivers/net/virtio_net.c", 967, "virtnet_rq_get_buf()"),
    "net-receive-buf": ("Linux", "drivers/net/virtio_net.c", 2618, "receive_buf()"),
    "net-refill": ("Linux", "drivers/net/virtio_net.c", 2827, "try_fill_recv()"),
    "net-add-small": ("Linux", "drivers/net/virtio_net.c", 2668, "add_recvbuf_small()"),
    "ring-add-in": ("Linux", "drivers/virtio/virtio_ring.c", 2430, "virtqueue_add_inbuf_premapped()"),
    "net-free-tx": ("Linux", "drivers/net/virtio_net.c", 590, "__free_old_xmit()"),
    "ring-get": ("Linux", "drivers/virtio/virtio_ring.c", 2548, "virtqueue_get_buf()"),
    "ring-get-ctx": ("Linux", "drivers/virtio/virtio_ring.c", 2538, "virtqueue_get_buf_ctx()"),
    "ring-get-split": ("Linux", "drivers/virtio/virtio_ring.c", 815, "virtqueue_get_buf_ctx_split()"),
    "qemu-net-tx": ("QEMU", "hw/net/virtio-net.c", 2716, "virtio_net_flush_tx()"),
    "qemu-net-tx-complete": ("QEMU", "hw/net/virtio-net.c", 2683, "virtio_net_tx_complete()"),
    "qemu-net-rx": ("QEMU", "hw/net/virtio-net.c", 1903, "virtio_net_receive_rcu()"),
    "qemu-pop": ("QEMU", "hw/virtio/virtio.c", 2001, "virtqueue_pop()"),
    "qemu-push": ("QEMU", "hw/virtio/virtio.c", 1193, "virtqueue_push()"),
    "qemu-fill": ("QEMU", "hw/virtio/virtio.c", 1038, "virtqueue_fill()"),
    "qemu-flush": ("QEMU", "hw/virtio/virtio.c", 1177, "virtqueue_flush()"),
    "qemu-notify-check": ("QEMU", "hw/virtio/virtio.c", 2650, "virtio_should_notify()"),
    "qemu-notify": ("QEMU", "hw/virtio/virtio.c", 2701, "virtio_notify()"),
    "qemu-virtio-irq": ("QEMU", "hw/virtio/virtio.c", 2669, "virtio_irq()"),
    "qemu-msix-notify": ("QEMU", "hw/pci/msix.c", 540, "msix_notify()"),
    "qemu-msi-send": ("QEMU", "accel/kvm/kvm-all.c", 2189, "kvm_irqchip_send_msi()"),
    "qemu-host-notifier": ("QEMU", "hw/virtio/virtio.c", 3949, "virtio_queue_host_notifier_read()"),
    "qemu-queue-output": ("QEMU", "hw/virtio/virtio.c", 2468, "virtio_queue_notify_vq()"),
    "qemu-tx-kick": ("QEMU", "hw/net/virtio-net.c", 2849, "virtio_net_handle_tx_bh()"),
    "qemu-rx-kick": ("QEMU", "hw/net/virtio-net.c", 1621, "virtio_net_handle_rx()"),
    "vdpa-host-notifier": ("QEMU", "hw/virtio/vhost-vdpa.c", 704, "vhost_vdpa_host_notifier_init()"),
    "pci-host-notifier": ("QEMU", "hw/virtio/virtio-pci.c", 1372, "virtio_pci_set_host_notifier_mr()"),
    "vdpa-kick-setup": ("QEMU", "hw/virtio/vhost-vdpa.c", 1497, "vhost_vdpa_set_vring_kick()"),
    "qemu-tap-write": ("QEMU", "net/tap.c", 123, "tap_write_packet()"),
    "qemu-tap-iov": ("QEMU", "net/tap.c", 137, "tap_receive_iov()"),
    "qemu-tap-send": ("QEMU", "net/tap.c", 180, "tap_send()"),
    "qemu-rx-buffer": ("QEMU", "hw/net/virtio-net.c", 1651, "virtio_net_has_buffers()"),
    "qemu-sendv": ("QEMU", "net/net.c", 883, "qemu_sendv_packet_async()"),
    "vhost-kick-tx": ("Linux", "drivers/vhost/net.c", 1290, "handle_tx_kick()"),
    "vhost-handle-tx": ("Linux", "drivers/vhost/net.c", 967, "handle_tx()"),
    "vhost-copy-tx": ("Linux", "drivers/vhost/net.c", 754, "handle_tx_copy()"),
    "vhost-get-tx": ("Linux", "drivers/vhost/net.c", 642, "get_tx_bufs()"),
    "vhost-tx-desc": ("Linux", "drivers/vhost/net.c", 592, "vhost_net_tx_get_vq_desc()"),
    "vhost-get-rx": ("Linux", "drivers/vhost/net.c", 1046, "get_rx_bufs()"),
    "vhost-handle-rx": ("Linux", "drivers/vhost/net.c", 1133, "handle_rx()"),
    "vhost-get-desc": ("Linux", "drivers/vhost/vhost.c", 2818, "vhost_get_vq_desc_n()"),
    "vhost-signal-used": ("Linux", "drivers/vhost/net.c", 461, "vhost_net_signal_used()"),
    "vhost-add-used": ("Linux", "drivers/vhost/vhost.c", 3190, "vhost_add_used_and_signal_n()"),
    "vhost-fill-used": ("Linux", "drivers/vhost/vhost.c", 3001, "__vhost_add_used_n()"),
    "vhost-flush-used": ("Linux", "drivers/vhost/vhost.c", 3100, "vhost_add_used_n()"),
    "vhost-notify-check": ("Linux", "drivers/vhost/vhost.c", 3133, "vhost_notify()"),
    "vhost-notify-signal": ("Linux", "drivers/vhost/vhost.c", 3171, "vhost_signal()"),
    "dpdk-dequeue": ("DPDK", "lib/vhost/virtio_net.c", 3586, "rte_vhost_dequeue_burst()"),
    "dpdk-enqueue": ("DPDK", "lib/vhost/virtio_net.c", 1743, "rte_vhost_enqueue_burst()"),
    "dpdk-split-tx": ("DPDK", "lib/vhost/virtio_net.c", 3092, "virtio_dev_tx_split()"),
    "dpdk-desc-mbuf": ("DPDK", "lib/vhost/virtio_net.c", 2883, "desc_to_mbuf()"),
    "dpdk-split-rx": ("DPDK", "lib/vhost/virtio_net.c", 1393, "virtio_dev_rx_split()"),
    "dpdk-fill-used": ("DPDK", "lib/vhost/virtio_net.c", 254, "do_flush_shadow_used_ring_split()"),
    "dpdk-flush-used": ("DPDK", "lib/vhost/virtio_net.c", 267, "flush_shadow_used_ring_split()"),
}


def _function_sources(demo: dict) -> None:
    for source in demo["sources"].values():
        parsed = urlsplit(source["url"])
        if parsed.netloc == "github.com" and "/blob/" in parsed.path:
            revision, path = parsed.path.split("/blob/", 1)[1].split("/", 1)
            source.update(path=path, revision=revision, line=int(parsed.fragment[1:].split("-", 1)[0]))
    demo["sources"].update({key: _source(*value) for key, value in FUNCTIONS.items()})
    aliases = {
        "qemu-tx": "qemu-net-tx", "qemu-rx": "qemu-net-rx", "qemu-tap": "qemu-tap-send",
        "vhost-tx": "vhost-copy-tx", "vhost-used": "vhost-flush-used", "vhost-rx": "vhost-handle-rx",
        "guest-rx": "net-receive", "guest-virtio-send": "net-xmit-skb", "dpdk-copy": "dpdk-desc-mbuf",
        "dpdk-tx": "dpdk-split-tx", "dpdk-rx": "dpdk-split-rx", "dpdk-used": "dpdk-flush-used",
    }
    for identifier, target in aliases.items():
        demo["sources"][identifier] = deepcopy(demo["sources"][target])
    demo["sources"]["vhost-setup"] = _source("QEMU", "hw/virtio/vhost.c", 1256, "vhost_virtqueue_start()")
    demo["sources"]["vdpa-map"] = _source("QEMU", "hw/virtio/vhost-vdpa.c", 94, "vhost_vdpa_dma_map()")
    for identifier, symbol in (("dpdk-pmd-tx", "rte_eth_tx_burst()"), ("dpdk-pmd-rx", "rte_eth_rx_burst()")):
        demo["sources"][identifier].update(symbol=symbol, project="DPDK")
    for source in demo["sources"].values():
        if not source.get("symbol"):
            source.update(kind="reference", project="接口规范")


def _insert_before(scene: dict, before: str, step: dict) -> None:
    index = next(i for i, item in enumerate(scene["steps"]) if item["id"] == before)
    scene["steps"].insert(index, step)


def _queue_step(original: dict, identifier: str, title: str, description: str,
                detail: str, changes: dict, sources: list[str]) -> dict:
    return {
        "id": identifier, "title": title, "actor": original["actor"],
        "description": description, "detail": detail,
        "active": list(original["active"]), "links": [],
        "changes": changes, "nodeStates": {}, "sourceIds": sources, "focus": True,
    }


def _split_publications(scene: dict) -> None:
    """Make descriptor/ring writes and their publication distinct snapshots."""
    tx = "-tx" in scene["id"]
    steps = {step["id"]: step for step in scene["steps"]}
    publish_id = "publish" if tx else ("replenish" if "replenish" in steps else "publish-buffer")
    publish = steps[publish_id]
    if not tx:
        prepared = {key: publish["changes"].pop(key) for key in ("desc", "payload")}
        _insert_before(scene, publish_id, _queue_step(
            publish, "prepare-buffer", "准备 RX 描述符", "desc[2] 指向一块 1530 B 设备可写空间。",
            "固定 non-mergeable small-buffer 路径，长度为 12 B virtio 头 + GOOD_PACKET_LEN（1518 B）。"
            "页片段实际分配还包括 headroom 和 skb 元数据，不能把分配容量当作描述符长度。此时尚未发布 available。",
            prepared, ["net-add-small", "ring-add-in", "ring-add-split"],
        ))
    head = 0 if tx else 2
    _insert_before(scene, publish_id, _queue_step(
        publish, "fill-avail", "填写 available 环项", f"avail.ring[0] = {head}，索引仍为 0。",
        "环项保存描述符链头，不保存报文。尚未推进 avail.idx，设备不能仅因槽位已有数字就消费这条链。",
        {"availHead": publish["changes"].pop("availHead")}, ["ring-add-split"],
    ))
    publish["description"] = "描述符与环项写入完成后，通过发布屏障再令 avail.idx = 1。"
    publish["sourceIds"] = ["ring-add-split", "ring-kick", "ring-notify"]
    used = steps["used"]
    backend = scene["id"].split("-", 1)[0]
    fill_sources = {
        "qemu": ["qemu-fill"], "vhost": ["vhost-fill-used"],
        "dpdk": ["dpdk-fill-used"], "vdpa": ["virtio-ring-spec"],
    }[backend]
    flush_sources = {
        "qemu": ["qemu-flush"], "vhost": ["vhost-flush-used"],
        "dpdk": ["dpdk-flush-used"], "vdpa": ["virtio-ring-spec"],
    }[backend]
    _insert_before(scene, "used", _queue_step(
        used, "fill-used", "填写 used 完成项",
        f"used.ring[0] = {{ id: {head}, len: {0 if tx else 72} }}；used.idx 仍为 0。",
        "设备已写好完成项，但尚未发布新索引。驱动仍必须按 used.idx 判断哪些记录可消费，"
        + ("TX 的 len=0 表示未向设备可写描述符写入字节，不表示发送帧长为零。" if tx else
           "RX 的 len=72 是实际写入量，不是 1530 B 空间容量。"),
        {"usedEntry": used["changes"].pop("usedEntry")}, fill_sources,
    ))
    if backend == "vdpa" and tx:
        fill_used = next(step for step in scene["steps"] if step["id"] == "fill-used")
        fill_used["description"] = "设备已结束对 Guest 链和缓冲区的使用，填写 used.ring[0] = { id: 0, len: 0 }。"
        fill_used["detail"] = (
            "只有满足缓冲区归还条件，设备才会填写完成项并随后发布 used.idx。"
            "具体硬件何时结束内部发送流水线由实现决定，本步不宣称已复制到设备私有缓冲区或已完成线缆发送。"
        )
    used["description"] = "先完成数据与 used 环项写入，再发布 used.idx = 1；驱动现在可以消费。"
    used["sourceIds"] = flush_sources
    if not tx:
        refill = steps["refill"]
        _insert_before(scene, "refill", _queue_step(
            refill, "refill-prepare", "重新关联 RX 空间", "已回收的 desc[2] 改指向另一块 1530 B 缓冲区。",
            "旧 P1 仍由接收 skb 引用。这里复用的是描述符槽，不是提前覆盖仍被协议栈引用的旧报文页。",
            {key: refill["changes"].pop(key) for key in ("desc", "payload")},
            ["net-refill", "net-add-small", "ring-add-in"],
        ))
        _insert_before(scene, "refill", _queue_step(
            refill, "refill-avail", "填写下一 available 环项", "avail.ring[1] = 2，avail.idx 暂时仍为 1。",
            "新一代链仍使用 head 2，但指向新的缓冲区；已经消费的 slot 0 和 used slot 0 保留旧值。",
            {"availHead": refill["changes"].pop("availHead")}, ["ring-add-split"],
        ))
        refill["description"] = "发布屏障之后令 avail.idx = 2，向设备交出新 RX 空间。"
        refill["sourceIds"] = ["net-refill", "ring-add-split", "ring-kick"]


def _descriptor(index: int, address: int, length: int, flags: list[str],
                next_index: int | None, label: str) -> dict:
    return {"index": index, "addr": address, "len": length, "flags": flags,
            "next": next_index, "label": label, "state": "prepared"}


def _backend_notifications(scene: dict) -> None:
    """Show queue publication and the transport notification as separate events."""
    backend = scene["id"].split("-", 1)[0]
    tx = "-tx" in scene["id"]
    queue_index = 1 if tx else 0
    sources = ["ring-kick", "ring-notify", "pci-notify"]
    if backend in {"qemu", "vhost"}:
        scene["links"].extend([
            {"id": "queue-doorbell", "from": "guest", "to": "kick",
             "label": f"写 notify 区：队列 {queue_index}", "kind": "notification"},
            {"id": "backend-kick", "from": "kick", "to": "backend",
             "label": "eventfd → QEMU 事件处理" if backend == "qemu" else "eventfd → vhost worker",
             "kind": "notification"},
        ])
        sources += ["kvm-ioeventfd"]
        if backend == "qemu":
            sources += ["qemu-host-notifier", "qemu-queue-output", "qemu-tx-kick" if tx else "qemu-rx-kick"]
        else:
            sources += ["vhost-setup", "vhost-kick-tx" if tx else "vhost-handle-rx"]
        scene["assumptions"].append(
            "通知路径启用 KVM ioeventfd；本例首次发布新链时后端请求 kick，未协商 VIRTIO_F_NOTIFICATION_DATA。"
            "notify 区写入队列编号，由 KVM 触发预先注册的 eventfd；发送载荷保存在 Guest RAM。"
        )
    elif backend == "vdpa":
        scene["links"].append({"id": "backend-kick", "from": "guest", "to": "backend",
                               "label": f"notify 区 → 硬件队列 {queue_index} 门铃", "kind": "notification"})
        sources += ["vdpa-host-notifier", "pci-host-notifier", "vdpa-kick-setup"]
        scene["assumptions"].append(
            "本例设备支持 host-notifier mmap，QEMU 已把硬件门铃页映射到 Guest notify 区；"
            "未协商 VIRTIO_F_NOTIFICATION_DATA，发布时需要 kick。direct 数据面本身不保证门铃可直接映射。"
        )
    else:
        # The selected polling path deliberately has no notification edge.
        scene["assumptions"].append(
            "DPDK 数据线程持续轮询，本例已通过 split-ring 通知抑制规则请求 Guest 不发 kick；"
            "驱动仍执行通知条件检查。完成通知使用普通 callfd 路径，未使用 guest_notify offload。"
            "vhost-user socket 用于配置，稳态报文不经该 socket 发送。"
        )

    publications = [step for step in scene["steps"]
                    if step["id"] in {"publish", "publish-buffer", "replenish", "refill"}]
    for published in publications:
        refill = published["id"] == "refill"
        suppressed = backend == "dpdk" or (refill and backend in {"qemu", "vhost"})
        published["changes"]["notification"] = "新索引已发布，尚未检查 kick 条件"
        if published["id"] == "publish":
            published["detail"] = "发布屏障使描述符和 available 环项先于新索引可见。后端依据 avail.idx 判断可消费的范围。"
        elif published["id"] == "replenish":
            published["detail"] = "head 2 的可写空间现在已发布。QEMU 先前因缺少 RX 缓冲区暂停了交付，下一步通知它重新检查队列。"
        published["sourceIds"] = ["ring-add-split"] if tx else ["net-refill", "ring-add-split"]
        step = {
            "id": "kick-refill" if refill else "kick", "title": "检查通知条件" if suppressed else "通知后端",
            "actor": "Guest virtio-net 驱动", "description": "", "detail": "",
            "active": ["guest"], "links": [], "changes": {}, "nodeStates": {},
            "sourceIds": sources, "focus": False,
        }
        if backend == "dpdk":
            step["description"] = "驱动检查通知抑制状态，本次无需写 notify 区；DPDK 数据线程继续轮询 available。"
            step["detail"] = ("virtqueue_kick_prepare() 返回 false，跳过 virtqueue_notify()。"
                              "TX 由 dequeue_burst 发现新链；RX 由 enqueue_burst 在来包时检查可写空间。")
            step["changes"] = {"notification": "kick 被抑制；DPDK 轮询共享队列"}
            step["nodeStates"] = {"guest": "通知检查完成，按约定不发 kick"}
            step["sourceIds"] = ["ring-kick"]
        elif suppressed:
            step["description"] = "驱动检查通知条件，本次补充 RX 空间无需发送 kick。"
            step["detail"] = (
                "后端处理本帧时已关闭 RX 队列通知，本例尚无下一帧；后续来包时会再次检查 available。"
                "若后端因缺少空间重新启用通知，驱动按相应规则发送 kick。"
            )
            step["changes"] = {"notification": "RX kick 被抑制；等待下一次网络事件"}
            step["nodeStates"] = {"guest": "已补充 RX 空间，本次不发 kick"}
            step["sourceIds"] = ["ring-kick", "qemu-rx-buffer" if backend == "qemu" else "vhost-handle-rx"]
        else:
            step["description"] = (
                f"驱动确认需要通知，vp_notify() 向 notify 区写入队列号 {queue_index}。"
                + ("该写入直达已映射的硬件门铃。" if backend == "vdpa" else
                   "KVM 匹配 ioeventfd 注册项，触发对应 eventfd。")
            )
            if backend == "qemu":
                step["detail"] = (
                    "QEMU 事件循环读取并清除 eventfd，调用队列处理函数；"
                    + ("TX handler 调度 bottom half，随后读取 available。" if tx else
                       "RX handler 尝试刷新等待队列。已有报文可重试交付；没有来包时保留可写空间。")
                    + "这次门铃写由 KVM 内核处理，不必让 KVM_RUN 以 MMIO 退出返回 QEMU。"
                )
            elif backend == "vhost":
                step["detail"] = (
                    "eventfd 唤起 vhost 的等待项并调度 worker，由 "
                    + ("handle_tx_kick() 进入发送处理。" if tx else "RX kick handler 进入接收处理；无来包时等待 TAP 可读事件。")
                    + "QEMU 已完成 kick FD 和后端配置，稳态通知无需经 QEMU 用户态转发。"
                )
            else:
                step["detail"] = (
                    "硬件收到门铃后按共享 available 索引读取队列。若设备不支持门铃 mmap，"
                    "后备路径为 KVM ioeventfd → vhost-vDPA kick 回调 → 设备门铃；报文 DMA 仍由硬件执行。"
                )
            step["active"] += (["kick"] if backend in {"qemu", "vhost"} else []) + ["backend"]
            step["links"] = (["queue-doorbell"] if backend in {"qemu", "vhost"} else []) + ["backend-kick"]
            step["changes"] = {"notification": "硬件队列门铃已写入" if backend == "vdpa" else "ioeventfd kick 已交给后端"}
            step["nodeStates"] = {"guest": "队列通知已发送", "backend": "已获新链通知，等待处理" if tx else "已获新 RX 空间通知，等待报文交付"}
            if backend in {"qemu", "vhost"}:
                step["nodeStates"]["kick"] = f"队列 {queue_index} 门铃已匹配，eventfd 已触发"
        offset = scene["steps"].index(published) + 1
        scene["steps"].insert(offset, step)


def _completion_interrupts(scene: dict) -> None:
    """Separate device notification, pending IRQ, injection and Guest handling."""
    backend = scene["id"].split("-", 1)[0]
    tx = "-tx" in scene["id"]
    notify = next(step for step in scene["steps"] if step["id"] == "notify")
    scene["assumptions"].append(
        "完成中断固定为 MSI-X，表项未屏蔽，队列允许本次通知；未协商 EVENT_IDX。"
        "使用 KVM 内核 LAPIC 与 VMX 软件注入，未启用 APICv / posted interrupt；"
        "目标 vCPU 的 IF=1、无中断阴影且优先级允许接受向量 0x51。"
        + ("Guest 开启 TX NAPI，TX 回调安排后续 NAPI 回收。" if tx else
           "RX 回调安排 NAPI；NAPI 获得调度后再消费完成项。")
    )
    if backend != "qemu":
        scene["assumptions"].append("call eventfd 已绑定 KVM irqfd；本次 MSI 快速路由成功，不进入工作队列回退。")
    if backend == "qemu":
        description = "QEMU 检查队列通知条件和 MSI-X 屏蔽状态，通过 KVM_SIGNAL_MSI 提交本次完成中断。"
        detail = (
            "固定 QEMU 主事件循环持有 BQL 的设备处理路径：virtio_irq() 经 MSI-X 设备通知进入 KVM。"
            "此时 used 已发布，Guest 尚未执行中断处理；使用 IOThread 的设备可另走 guest notifier / irqfd。"
        )
        label = "MSI-X → KVM_SIGNAL_MSI"
        sources = ["qemu-notify", "qemu-notify-check", "qemu-virtio-irq", "qemu-msix-notify", "qemu-msi-send"]
    elif backend == "vhost":
        description = "vhost 检查队列通知条件，触发 call eventfd；已绑定的 KVM irqfd 接收该事件。"
        detail = "callfd 承载完成提示，和 Guest 提交请求时的 kickfd 方向相反。used 已发布，irqfd 按预先配置的 MSI 路由转交中断。"
        label = "call eventfd → KVM irqfd"
        sources = ["vhost-notify-check", "vhost-notify-signal", "vhost-setup", "kvm-irqfd"]
    elif backend == "dpdk":
        description = "DPDK vhost 库检查队列通知条件，向 callfd 写入 eventfd 事件，交给 KVM irqfd。"
        detail = "DPDK 轮询的是 Guest 提交队列；Guest 本例仍依靠完成中断安排回收。callfd 由 vhost-user 控制面事先传递，本次通知不通过控制 socket 传送报文。"
        label = "callfd → KVM irqfd"
        sources = ["dpdk-split-tx" if tx else "dpdk-split-rx", "vhost-user", "kvm-irqfd"]
    else:
        description = "vDPA 硬件发布完成后产生设备中断，宿主 vDPA 回调触发 call eventfd，再进入 KVM irqfd。"
        detail = "本例选择宿主回调与 irqfd 的软件递送路径；硬件直接处理 virtqueue 不等于中断也直投 Guest。厂商中断寄存器与固件内部实现不在图中展开。"
        label = "设备 IRQ → vDPA 回调 → irqfd"
        sources = ["virtio-ring-spec", "vhost-setup", "kvm-irqfd"]
    notify.update(
        title="提交完成中断", description=description, detail=detail,
        active=["backend", "interrupt"], links=["notify"], sourceIds=sources,
        changes={"notification": "完成通知已提交，等待 KVM 路由",
                 "completionNotification": "KVM_SIGNAL_MSI 已提交" if backend == "qemu" else "call eventfd / irqfd 已触发"},
        nodeStates={"interrupt": "收到完成通知，准备查找目标 vCPU",
                    "guest": "used 已发布，尚未执行本次 Guest 中断"}, focus=False,
    )
    notify_link = next(link for link in scene["links"] if link["id"] == "notify")
    notify_link.update(to="interrupt", label=label)
    scene["links"].extend([
        {"id": "prepare-interrupt", "from": "interrupt", "to": "vcpu",
         "label": "待处理中断 → VM-entry 注入字段", "kind": "notification"},
        {"id": "guest-interrupt", "from": "vcpu", "to": "guest",
         "label": "成功 VM-entry → Guest IRQ → NAPI", "kind": "notification"},
    ])
    injections = [
        {
            "id": "route-interrupt", "title": "记录待处理中断", "actor": "KVM · MSI 路由与 vLAPIC",
            "description": "KVM 按 MSI 地址与数据选中目标 vCPU，在其虚拟 LAPIC 中记录向量 0x51。",
            "detail": "IRR 记录待处理状态，并请求目标 vCPU 处理事件；必要时唤醒或踢出正在运行的 vCPU。此时尚未跳转到 Guest 中断入口。",
            "active": ["interrupt"], "links": [],
            "changes": {"lapicState": "IRR：0x51 待处理"},
            "nodeStates": {"interrupt": "IRR 中有 0x51，等待 vCPU 检查接受条件"},
            "sourceIds": ["kvm-msi-route" if backend == "qemu" else "kvm-msi-inatomic", "vmx-pending"], "focus": False,
        },
        {
            "id": "prepare-interrupt", "title": "准备中断注入", "actor": "KVM · 目标 vCPU 线程",
            "description": "目标 vCPU 线程检查中断接受条件，应答 LAPIC 请求，并准备下一次 VM-entry 的中断注入字段。",
            "detail": "本例 IF、优先级和中断阴影检查均通过。vmx_inject_irq() 填写 VMCS；设置注入字段还没有执行 Guest 指令。若当前不可接受，KVM 保留待处理状态，等待合适窗口。",
            "active": ["interrupt", "vcpu"], "links": ["prepare-interrupt"],
            "changes": {"lapicState": "IRR 已应答；ISR：0x51", "entryState": "VM-entry 中断字段：0x51"},
            "nodeStates": {"interrupt": "0x51 已从待处理转入服务状态", "vcpu": "VMCS 已准备，尚未成功进入 Guest"},
            "sourceIds": ["vmx-irq-allowed", "lapic-ack", "vmx-irq-inject"], "focus": False,
        },
        {
            "id": "guest-interrupt", "title": "进入 Guest 中断", "actor": "Guest 内核 · IRQ 与 virtio 回调",
            "description": "VM-entry 成功后，CPU 按 Guest IDT 转入中断入口；virtio 队列回调安排 NAPI。",
            "detail": ("本例开启 TX NAPI，skb_xmit_done() 安排发送完成清理。" if tx else
                       "vring_interrupt() 调用 skb_recv_done()，安排 RX NAPI。")
                      + "中断入口的 APIC 应答只更新中断服务状态；NAPI 随后依据 used.idx 回收队列，两个进度各自维护。",
            "active": ["vcpu", "guest"], "links": ["guest-interrupt"],
            "changes": {"entryState": "VM-entry 成功，Guest 已接受 0x51",
                        "lapicState": "本次中断已由 Guest 应答（EOI）",
                        "guestIrqState": "队列回调已安排 NAPI，尚未回收 used"},
            "nodeStates": {"vcpu": "已成功进入 Guest IRQ 处理路径",
                           "interrupt": "Guest 已发送 EOI，ISR 中的 0x51 已清除",
                           "guest": "NAPI 已安排，等待消费 used 完成项"},
            "sourceIds": ["vmx-irq-inject", "guest-edge-irq", "guest-ring-irq"], "focus": False,
        },
    ]
    if backend == "dpdk":
        api = "rte_vhost_dequeue_burst()" if tx else "rte_vhost_enqueue_burst()"
        returned = ("dequeue 已返回 1，调用者持有独立 mbuf M" if tx else
                    "enqueue 已返回 1，本帧已复制进 Guest 缓冲区")
        injections[0]["detail"] += (
            f"本例 irqfd 快速路由在 eventfd 写调用内完成；随后 {api} 返回 1。"
            "以下从这个共同边界分别观察 Guest 中断处理与宿主 mbuf 的后续操作。"
        )
        injections[0]["changes"]["backendState"] = returned
        if tx:
            injections[0]["changes"]["mbufOwner"] = "DPDK 应用持有 M"
        scene["assumptions"].append(
            f"选择 {api} 返回后再观察 Guest 接受中断的一种调度。"
            "实际目标 vCPU 也可能更早响应已经挂起的中断；演示中的分支内部保持各自依赖。"
        )
    offset = scene["steps"].index(notify) + 1
    scene["steps"][offset:offset] = injections
    reclaim = next(step for step in scene["steps"] if step["id"] == ("reclaim" if tx else "receive"))
    reclaim["changes"]["guestIrqState"] = "NAPI 已消费本次 used；中断计数与完成数独立"


def _snapshot_queue(scene: dict) -> None:
    """Replay known event IDs into typed snapshots; no translated text parsing."""
    tx = "-tx" in scene["id"]
    scene["queue"] = {
        "structureId": "virtqueue",
        "name": "TX" if tx else "RX", "index": 1 if tx else 0, "size": 8,
        "descriptorIndices": [0, 2] if tx else [2], "head": 0 if tx else 2,
        "requestId": "P1", "direct": True, "addressSpace": "示意 GPA / IOVA",
        "headerLength": 12, "packetLength": 60, "payloadLength": 18,
    }
    state = {
        "descriptors": [],
        "avail": {"index": 0, "consumer": 0, "slots": [None] * 8},
        "used": {"index": 0, "consumer": 0, "slots": [None] * 8},
        "buffers": [], "freeDescriptors": 8,
        "ownership": "driver", "changed": [],
    }
    for step in scene["steps"]:
        event = step["id"]
        changed = []
        if event in {"prepare", "prepare-buffer"}:
            if tx:
                state["descriptors"] = [
                    _descriptor(0, 0x1000, 12, ["NEXT"], 2, "virtio-net 头 · 设备读"),
                    _descriptor(2, 0x2000, 60, [], None, "P1 Ethernet 帧 · 设备读"),
                ]
                state["buffers"] = [
                    {"id": "header", "label": "virtio-net 头", "addr": 0x1000, "length": 12,
                     "validBytes": 12, "writable": False, "state": "prepared"},
                    {"id": "packet", "label": "P1 · Guest skb S1", "addr": 0x2000, "length": 60,
                     "validBytes": 60, "writable": False, "state": "prepared"},
                ]
                state["freeDescriptors"] = 6
            else:
                state["descriptors"] = [_descriptor(2, 0x4000, RX_CAPACITY, ["WRITE"], None,
                                                    "RX 缓冲区 · 设备写")]
                state["buffers"] = [{"id": "rx-old", "label": "RX 缓冲区 B0", "addr": 0x4000,
                                     "length": RX_CAPACITY, "validBytes": 0, "writable": True,
                                     "state": "prepared"}]
                state["freeDescriptors"] = 7
            changed = ["descriptors", "buffers", "freeDescriptors"]
        elif event == "fill-avail":
            state["avail"]["slots"][0] = 0 if tx else 2
            changed = ["avail.slots"]
        elif event in {"publish", "publish-buffer", "replenish"}:
            state["avail"]["index"] = 1
            state["ownership"] = "device"
            for item in state["descriptors"] + state["buffers"]:
                item["state"] = "device"
            changed = ["avail.index", "ownership"]
        elif event in {"consume", "take-buffer"} or (event == "retry" and not tx):
            state["avail"]["consumer"] = 1
            changed = ["avail.consumer"]
        elif event == "write-buffer":
            state["buffers"][0]["validBytes"] = 72
            state["buffers"][0]["state"] = "data-ready"
            changed = ["buffers"]
        elif event == "fill-used":
            state["used"]["slots"][0] = {"id": 0 if tx else 2, "len": 0 if tx else 72}
            changed = ["used.slots"]
        elif event == "used":
            state["used"]["index"] = 1
            state["ownership"] = "completed"
            for item in state["descriptors"] + state["buffers"]:
                item["state"] = "completed"
            changed = ["used.index", "ownership", "buffers"]
        elif event in {"reclaim", "receive"}:
            state["used"]["consumer"] = 1
            state["freeDescriptors"] = 8
            state["ownership"] = "driver"
            for item in state["descriptors"]:
                item["state"] = "reclaimed"
            for item in state["buffers"]:
                item["state"] = "reusable" if tx else "napi"
            changed = ["used.consumer", "descriptors", "buffers", "ownership", "freeDescriptors"]
        elif event == "refill-prepare":
            state["descriptors"] = [_descriptor(2, 0x8000, RX_CAPACITY, ["WRITE"], None,
                                                "新 RX 缓冲区 · 设备写")]
            state["buffers"].append({"id": "rx-new", "label": "新 RX 缓冲区 B1", "addr": 0x8000,
                                     "length": RX_CAPACITY, "validBytes": 0, "writable": True,
                                     "state": "prepared"})
            state["freeDescriptors"] = 7
            changed = ["descriptors", "buffers", "freeDescriptors"]
        elif event == "refill-avail":
            state["avail"]["slots"][1] = 2
            changed = ["avail.slots"]
        elif event == "refill":
            state["avail"]["index"] = 2
            state["ownership"] = "device"
            state["descriptors"][0]["state"] = "device"
            state["buffers"][-1]["state"] = "device"
            changed = ["avail.index", "ownership"]
        elif event == "socket-enqueue":
            state["buffers"][0]["state"] = "socket"
            changed = ["buffers"]
        elif event == "copy-to-user":
            state["buffers"][0]["state"] = "consumed"
            changed = ["buffers"]
        state["changed"] = changed
        step["queueState"] = deepcopy(state)


def _path(identifier: str, title: str, context: str, sources: list[str], boundary: str) -> dict:
    return {"id": identifier, "title": title, "context": context,
            "sourceIds": sources, "boundary": boundary}


def _node_states(scene: dict) -> None:
    """Keep overview cards aligned with the explicit queue and packet events.

    Node text describes the last observed state, independently of the current
    highlight. Event IDs and typed counters define the transitions; translated
    descriptions are never parsed to infer progress or delivery.
    """
    tx = "-tx" in scene["id"]
    backend = scene["id"].split("-", 1)[0]
    head = scene["queue"]["head"]
    initial = {
        "application": "18 B 载荷待提交，U0 由应用持有" if tx else "128 B 接收空间，尚未调用 recvfrom",
        "stack": "尚未构建本次 skb" if tx else "UDP 接收队列为空",
        "guest": "等待本次发送 skb" if tx else "等待准备本次 RX 空间",
        "queue": "尚未准备本次描述符，avail.idx = used.idx = 0",
        "backend": "队列已启用，等待本次 TX" if tx else "队列已启用，等待本次接收",
        "port": "尚未接受本次发送帧" if tx else "等待本次帧到达",
        "network": "尚未发出本次帧；远端状态未知" if tx else "本次帧尚未到达本机",
        "control": "队列与后端已配置，不逐包处理",
        "kick": "notify 地址与 eventfd 已登记，尚无本次 kick",
        "interrupt": "MSI 路由已配置，尚无本次待处理中断",
        "vcpu": "尚未准备本次完成中断的 VM-entry 注入",
    }
    for node in scene["nodes"]:
        node["initial"] = initial[node["id"]]

    queue_states = {
        "prepare": "链 0 → 2 已准备，尚未发布",
        "prepare-buffer": "desc[2] 指向 1530 B 可写空间，尚未发布",
        "fill-avail": f"avail.ring[0] = {head}；avail.idx 仍为 0",
        "publish": "avail.idx = 1，等待后端取链",
        "publish-buffer": "avail.idx = 1，设备可写 RX 空间",
        "replenish": "avail.idx = 1，新 RX 链已发布",
        "consume": "后端已取链，尚未发布完成",
        "take-buffer": "后端已取 RX 链，尚未写入本帧",
        "write-buffer": "已写入 72 B；used.idx 仍为 0",
        "fill-used": "used 环项已填写；used.idx 仍为 0",
        "used": "used.idx = 1，完成已发布，等待驱动回收",
        "reclaim": "描述符已回收，Guest 发送缓冲区可复用",
        "receive": "描述符已回收；旧帧由 NAPI 持有",
        "refill-prepare": "desc[2] 改指新缓冲区；旧帧仍由 NAPI 持有",
        "refill-avail": "avail.ring[1] = 2；avail.idx 仍为 1",
        "refill": "avail.idx = 2，新 RX 空间可写；旧帧仍在 NAPI",
        "socket-enqueue": "新 RX 空间等待设备；旧帧由 socket 接收路径持有",
        "copy-to-user": "新 RX 空间等待设备；旧帧的本次接收引用已释放",
        "blocked": "原 TX 链仍在途，used.idx = 0",
        "no-buffer": "无已发布的 RX 空间，不能写入 Guest RAM",
        "retry": ("TAP 已接受原链，等待完成回调发布 used" if tx else
                  "后端已取得新 RX 链，准备写入本帧"),
    }
    guest_states = {
        "prepare": "已准备设备可读链 0 → 2",
        "prepare-buffer": "已准备设备可写 RX 描述符",
        "fill-avail": "链头已写入环项，尚未交给设备",
        "publish": "TX 已发布，等待设备归还",
        "publish-buffer": "RX 空间已发布，等待设备填入报文",
        "replenish": "新 RX 空间已发布，等待后端重试",
        "notify": "used 已发布，等待驱动检查完成项",
        "reclaim": "TX 链已回收，发送 skb 的设备引用已释放",
        "refill-prepare": "为下一帧关联新的 RX 缓冲区",
        "refill-avail": "下一条 RX 环项已填，索引尚未发布",
    }
    for step in scene["steps"]:
        event = step["id"]
        updates = {}
        if event in queue_states:
            updates["queue"] = queue_states[event]
        if event in guest_states:
            updates["guest"] = guest_states[event]
        # This field is already an explicit backend state, not prose to parse.
        if "backendState" in step["changes"]:
            updates["backend"] = step["changes"]["backendState"]
        if event == "fill-used":
            updates["backend"] = "完成项已填写，等待发布 used.idx"
        elif event == "notify":
            updates["backend"] = "本链完成已发布，本例发出队列检查提示"
        elif event == "arrive":
            updates.update(network="P1 已到达本机", backend="本次帧已到达，等待取得 RX 空间")
            updates["port"] = {
                "qemu": "TAP 帧已交给 QEMU 接收路径",
                "vhost": "TAP 帧可由 vhost worker 读取",
                "dpdk": "PMD 已向 DPDK 应用交付 mbuf M",
                "vdpa": "物理端口已收到 P1",
            }[backend]
        elif event == "transfer":
            if backend in {"qemu", "vhost"}:
                updates["port"] = "TAP 已接受 P1，后续转发独立进行"
            elif backend == "vdpa":
                updates["port"] = "硬件正在处理 P1；内部发送进度未展开"
            else:
                updates["port"] = "帧已复制至 mbuf M，尚未提交 PMD"
            updates["network"] = "远端接收尚未确认"
        elif event == "blocked":
            updates["port"] = "TAP 暂不可写，等待可写事件"
        elif event == "no-buffer":
            updates["port"] = "TAP 读取暂停，本帧暂存本地队列"
        elif event == "retry" and tx:
            updates["port"] = "TAP 已接受重试帧，后续转发独立进行"
            updates["network"] = "远端接收尚未确认"
        elif event == "used" and scene["id"] == "qemu-rx-no-buffer":
            updates["port"] = "本帧已写入 Guest，TAP 可恢复读取"
        elif event == "refill":
            updates["backend"] = "新 RX 空间已发布，等待下一帧"
        elif event == "free-mbuf":
            updates["port"] = "本次宿主 mbuf M 已释放，端口可继续收包"
        elif event == "pmd":
            updates.update(port="PMD 已接受 mbuf M，等待网卡完成", network="远端接收尚未确认")
        elif event == "pmd-cleanup":
            updates.update(port="网卡已结束使用，PMD 可回收 mbuf M", network="本地缓冲区已归还；远端接收仍未确认")
        elif event == "egress":
            updates["port"] = "继续本地网络转发；未取得远端确认"
            updates["network"] = "远端接收仍未确认"
        # Preserve more specific application/NAPI narrative state where supplied.
        step["nodeStates"] = {**updates, **step["nodeStates"]}


def _call_paths(scene: dict) -> None:
    tx = "-tx" in scene["id"]
    backend = scene["id"].split("-", 1)[0]
    scene["callPaths"] = [
        _path("guest-application", "sendto 与 Guest 发送协议栈" if tx else "recvfrom 进入阻塞等待",
              "Guest 用户态 → 内核态 · 原应用线程",
              ["guest-sendto", "guest-sock-send", "guest-udp-send", "guest-copy-send", "guest-ip-output"] if tx else
              ["guest-recvfrom", "guest-sock-recv", "guest-udp-recv", "guest-udp-wait"],
              "发送链列关键执行阶段，省略 socket/IPv4 包装与路由细节；无 cork、邻居已解析、无 qdisc 排队。"
              "sendto 返回仅表示本地接受，不等待 TX used 或对端收到。" if tx else
              "接收线程在 __skb_recv_udp 中等待；设备接收与 NAPI 在其他执行上下文推进，不是这个线程普通调用后端。"),
        _path("guest-submit", "Guest 发布发送链" if tx else "Guest 准备接收空间", "Guest 内核 · virtio-net 驱动",
              ["net-start-xmit", "net-xmit-skb", "net-add-out", "ring-add-out", "ring-add-split"] if tx else
              ["net-refill", "net-add-small", "ring-add-in", "ring-add-split"],
              "链头和缓冲区通过 Guest RAM 共享。函数调用不会跨入宿主后端；通知或轮询才使后端获得执行机会。"),
        _path("queue-contract", "队列发布与通知边界", "Guest 驱动 · split-ring 协议",
              ["ring-kick", "ring-notify", "virtio-ring-spec", "virtio-net-spec"],
              "kick_prepare 先检查是否需要通知，notify 才执行传输层通知。设备可以轮询，完成通知也可被抑制；协议规范不是硬件内部函数。"),
    ]
    if backend == "qemu":
        paths = [
            _path("qemu-backend", "QEMU 解析与数据交付", "宿主用户态 · QEMU 网络事件上下文",
                  ["qemu-net-tx", "qemu-pop", "qemu-sendv", "qemu-tap-iov", "qemu-tap-write"] if tx else
                  ["qemu-tap-send", "qemu-net-rx", "qemu-rx-buffer", "qemu-pop"],
                  "TX 经网络分发到 TAP；同步接受和异步背压是不同分支。RX 从 TAP 的可读事件进入设备接收回调。"),
            _path("backend-complete", "完成项与索引发布", "宿主用户态 · QEMU 设备完成路径",
                  (["qemu-net-tx-complete"] if scene["id"] == "qemu-tx-backpressure" else [])
                  + (["qemu-push"] if tx else [])
                  + ["qemu-fill", "qemu-flush", "qemu-notify", "qemu-notify-check"],
                  "背压场景由异步完成回调归还原链；普通同步场景直接继续。通知不是对 Guest IRQ handler 的普通函数调用。"),
        ]
    elif backend == "vhost":
        paths = [
            _path("vhost-backend", "vhost-net 内核数据面", "宿主内核 · vhost worker",
                  ["vhost-kick-tx", "vhost-handle-tx", "vhost-copy-tx", "vhost-get-tx", "vhost-tx-desc", "vhost-get-desc"] if tx else
                  ["vhost-handle-rx", "vhost-get-rx", "vhost-get-desc"],
                  "worker 由 kick 或 socket 事件调度；Guest 驱动与 worker 不在同一调用栈，QEMU 不逐包解析队列。"),
            _path("backend-complete", "vhost 完成发布与通知", "宿主内核 · vhost worker",
                  ["vhost-signal-used", "vhost-add-used", "vhost-flush-used", "vhost-fill-used", "vhost-notify-signal", "vhost-notify-check"],
                  "vhost_add_used_n 内部填写环项并在写屏障后更新索引；vhost_signal 按规则触发 eventfd。"),
        ]
    elif backend == "dpdk":
        paths = [
            _path("dpdk-backend", "DPDK 同步复制路径", "宿主用户态 · DPDK 数据线程",
                  ["dpdk-dequeue", "dpdk-split-tx", "dpdk-desc-mbuf"] if tx else
                  ["dpdk-enqueue", "dpdk-split-rx"],
                  "接口名以 vhost 为视角：dequeue 从 Guest 取出 TX，enqueue 把 RX 送入 Guest。Guest 缓冲区和宿主 mbuf 是两份存储。"),
            _path("backend-complete", "DPDK 发布完成", "宿主用户态 · DPDK 数据线程",
                  ["dpdk-flush-used", "dpdk-fill-used"],
                  "flush_shadow_used_ring_split 先复制 shadow used 项，再 release 更新共享 used.idx。TX Guest 链完成后，独立 mbuf 仍可继续进入 PMD。"),
            _path("dpdk-pmd", "独立 mbuf 与物理网卡", "宿主用户态 · DPDK PMD 接口边界",
                  ["dpdk-pmd-tx", "dpdk-api"] if tx else ["dpdk-pmd-rx", "dpdk-api"],
                  "ethdev burst 是设备驱动接口，不指定某家网卡实现；接受 mbuf 的数量与 Guest 队列完成不是同一事件，不宣称 TX used 等于对端收到。"),
        ]
    else:
        paths = [_path("vdpa-backend", "硬件读取队列与 DMA", "硬件 vDPA · 固定 direct 模式",
                       ["vdpa-map", "virtio-ring-spec", "virtio-net-spec"],
                       "QEMU 配置 DMA 映射，硬件按协议执行队列。此处引用硬件接口规范，不虚构设备固件内部函数；未启用 SVQ。")]
    scene["callPaths"].extend(paths)
    step_by_id = {step["id"]: step for step in scene["steps"]}
    kick_sources = list(dict.fromkeys(
        source for step in scene["steps"] if step["id"] in {"kick", "kick-refill"}
        for source in step["sourceIds"]
    ))
    scene["callPaths"].insert(3, _path(
        "queue-kick", "发布后的通知检查与后端交接", "Guest 驱动 · 传输通知边界",
        kick_sources,
        "DPDK 轮询场景检查后跳过 notify。QEMU / vhost 通过 ioeventfd，"
        "选定的 vDPA 场景通过直接映射的门铃；RX 再补充空间时需重新判断通知条件。",
    ))
    scene["callPaths"].extend([
        _path("completion-signal", "后端提交完成通知", "后端 → KVM · MSI / irqfd 边界",
              step_by_id["notify"]["sourceIds"],
              "QEMU 主事件循环使用 KVM_SIGNAL_MSI；vhost、DPDK 与本例 vDPA 使用 call eventfd / irqfd。"
              "提交中断不表示目标 vCPU 已进入 Guest IRQ。"),
        _path("kvm-injection", "KVM 路由、挂起与注入", "宿主内核 · KVM 与目标 vCPU 线程",
              step_by_id["route-interrupt"]["sourceIds"] + step_by_id["prepare-interrupt"]["sourceIds"],
              "向量先进入虚拟 LAPIC 待处理状态。接受条件通过后准备 VMCS，成功 VM-entry 才能进入 Guest 中断入口。"),
        _path("guest-interrupt", "Guest IRQ 与 NAPI 调度", "Guest 内核 · 中断入口与 virtio 回调",
              step_by_id["guest-interrupt"]["sourceIds"],
              "中断应答与 used 回收分别推进；TX 本例开启 NAPI，RX 回调安排接收 NAPI，稍后消费队列。"),
    ])
    scene["callPaths"].append(_path(
        "guest-complete", "Guest 消费完成", "Guest 内核 · virtio-net 完成 / NAPI",
        ["net-free-tx", "ring-get", "ring-get-split"] if tx else
        ["net-rx-poll", "net-receive", "net-receive-packets", "net-rq-get", "ring-get-ctx", "ring-get-split", "net-receive-buf"],
        "驱动依据 used.idx 消费记录后才能回收描述符；RX 数据引用可以继续留在 skb，不能随描述符回收立刻覆盖。",
    ))
    if tx:
        scene["callPaths"].append(_path(
            "guest-send-return", "sendto 成功返回", "Guest 原应用线程 · 返回方向",
            ["guest-udp-send", "guest-sock-send", "guest-sendto", "send-api"],
            "这是之前提交栈的返回方向，不是完成通知重新调用 sendto。驱动仍可能持有 skb；用户缓冲区此前已复制，应用现在可复用。",
        ))
    else:
        scene["callPaths"].extend([
            _path("guest-network-input", "NAPI → UDP socket 入队", "Guest 内核 · NAPI / 网络接收上下文",
                  ["guest-napi-input", "guest-gro-normal", "guest-napi-complete", "guest-udp-rcv",
                   "guest-udp-unicast", "guest-udp-enqueue"],
                  "固定 GRO_NORMAL、无 RPS 分流且本轮 NAPI 完成；普通接收列表刷新后经 IPv4 到 UDP。"
                  "这里省略协议分派包装，不把 NAPI、socket 入队与用户线程恢复当作同一调用栈。"),
            _path("guest-receive-return", "复制载荷并返回 recvfrom", "Guest 原应用线程 · 恢复后的接收路径",
                  ["guest-udp-wait", "guest-udp-recv", "guest-sock-recv", "guest-recvfrom", "recv-api"],
                  "恢复等待后消费数据报，把 UDP 载荷复制至用户缓冲区并返回长度；网卡帧头与 virtio 头不会作为应用载荷返回。"),
        ])
    # These are execution-context entries, not calls from the preceding group.
    # Unannotated neighbours remain processing order: many are sibling calls.
    path_by_id = {path["id"]: path for path in scene["callPaths"]}
    if backend == "qemu":
        path_by_id["qemu-backend"]["entry"] = {
            "kind": "notification" if tx else "callback",
            "label": "队列 kick 经 QEMU 事件处理推进发送" if tx else "TAP 可读事件进入 QEMU 网络接收回调",
        }
        if scene["id"] == "qemu-tx-backpressure":
            path_by_id["backend-complete"]["entry"] = {
                "kind": "callback", "label": "背压解除后，异步发送完成回调归还原描述符链",
            }
    elif backend == "vhost":
        path_by_id["vhost-backend"]["entry"] = {
            "kind": "async", "label": "kick 或 socket 事件调度独立的 vhost worker",
        }
    elif backend == "dpdk":
        path_by_id["dpdk-backend"]["entry"] = {
            "kind": "async", "label": "独立 DPDK 数据线程调用 vhost 接口处理共享队列",
        }
    return_path = path_by_id["guest-send-return" if tx else "guest-receive-return"]
    return_path["flow"] = "return"
    return_path["entry"] = {
        "kind": "return",
        "label": "沿原 sendto 提交栈返回，不等待 TX 完成" if tx else
                 "UDP 数据入队唤醒后，原接收线程恢复并逐层返回",
    }
    if not tx:
        path_by_id["guest-complete"]["entry"] = {
            "kind": "async", "label": "NAPI 调度后消费 RX 完成项",
        }
    step_sources = {
        "prepare": ["net-xmit-skb", "net-add-out", "ring-add-out", "ring-add-split"],
        "driver-submit": ["net-start-xmit", "net-xmit-skb"],
        "reclaim": ["net-free-tx", "ring-get", "ring-get-split"],
        "receive": ["net-rx-poll", "net-receive", "net-rq-get", "ring-get-ctx", "ring-get-split", "net-receive-buf"],
    }
    entry = {"qemu": "qemu-net-tx" if tx else "qemu-net-rx",
             "vhost": "vhost-copy-tx" if tx else "vhost-handle-rx",
             "dpdk": "dpdk-split-tx" if tx else "dpdk-split-rx", "vdpa": "virtio-ring-spec"}[backend]
    step_sources.update({key: [entry] for key in ("consume", "take-buffer", "transfer", "write-buffer")})
    for step in scene["steps"]:
        if step["id"] in step_sources:
            step["sourceIds"] = list(dict.fromkeys(step_sources[step["id"]] + step["sourceIds"]))
        # Match existing narrative citations to the actual function paths, not
        # to an identical-looking translated label with a different identifier.
        aliases = {
            "qemu-tx": "qemu-net-tx", "qemu-rx": "qemu-net-rx",
            "qemu-tap": "qemu-tap-write" if tx else "qemu-tap-send",
            "guest-rx": "net-receive", "guest-virtio-send": "net-xmit-skb",
            "vhost-tx": "vhost-copy-tx", "vhost-used": "vhost-flush-used", "vhost-rx": "vhost-handle-rx",
            "dpdk-copy": "dpdk-desc-mbuf", "dpdk-tx": "dpdk-split-tx",
            "dpdk-rx": "dpdk-split-rx", "dpdk-used": "dpdk-flush-used",
        }
        step["sourceIds"] = list(dict.fromkeys(aliases.get(source, source) for source in step["sourceIds"]))


def _parallel_network_paths(scene: dict) -> None:
    """Declare independent suffixes only after their common prerequisites."""
    identifier = scene["id"]
    if identifier in {"qemu-tx", "vhost-tx", "qemu-tx-backpressure"}:
        after = "retry" if identifier == "qemu-tx-backpressure" else "transfer"
        host_steps = ["egress"]
        title = "TAP 接受后，队列归还与宿主转发分别推进"
        description = (
            "普通复制路径已结束对 Guest 发送缓冲区的使用，后端可以发布 used。"
            "宿主网络按自己的调度继续转发，不等待 Guest 中断处理或 TX 链回收；"
            "其中一部分处理也可能已经在提交 TAP 的内核调用中完成。"
        )
        guest_id, guest_title = "guest-completion", "Guest 队列完成"
        guest_summary = "发布 used，经 KVM 递送完成中断，驱动回收 TX 链。"
        host_id, host_title = "host-forwarding", "宿主网络转发"
        host_summary = "观察已接受帧的本地转发；不据此推断物理发送完成或远端收到。"
    elif identifier == "dpdk-tx":
        after, host_steps = "route-interrupt", ["pmd", "pmd-cleanup"]
        title = "dequeue 返回后，Guest 回收与 PMD 发送分别推进"
        description = (
            "vhost 库已复制帧、发布 used 并通知 KVM，dequeue 已向调用者返回独立 mbuf M。"
            "Guest 可以处理完成中断，应用也可以把 M 交给 PMD；两边使用不同缓冲区，互不等待对方完成。"
        )
        guest_id, guest_title = "guest-completion", "Guest 队列回收"
        guest_summary = "目标 vCPU 接受完成中断，NAPI 依据 used 回收 Guest TX 链。"
        host_id, host_title = "host-forwarding", "PMD 发送与 mbuf 回收"
        host_summary = "应用提交独立 M；PMD 在网卡结束使用 M 后回收它。"
    elif identifier == "dpdk-rx":
        after, host_steps = "route-interrupt", ["free-mbuf"]
        title = "enqueue 返回后，Guest 接收与宿主 mbuf 释放分别推进"
        description = (
            "同步 enqueue 已把帧复制到 Guest RX 缓冲区并返回 1。"
            "Guest 继续处理中断与接收协议栈；DPDK 应用可以释放原 mbuf M，无需等到 recvfrom 返回。"
        )
        guest_id, guest_title = "guest-receive", "Guest 接收与应用返回"
        guest_summary = "NAPI 消费 RX 完成项、补充新空间，协议栈把数据报交给等待的应用。"
        host_id, host_title = "host-buffer", "宿主 mbuf 释放"
        host_summary = "应用释放已完成复制的 M；Guest 缓冲区中的帧保持有效。"
    else:
        return
    ids = [step["id"] for step in scene["steps"]]
    suffix = ids[ids.index(after) + 1:]
    scene["parallel"] = {
        "after": after, "title": title, "description": description,
        "branches": [
            {"id": guest_id, "title": guest_title, "summary": guest_summary,
             "steps": [step for step in suffix if step not in host_steps]},
            {"id": host_id, "title": host_title, "summary": host_summary,
             "steps": host_steps},
        ],
    }


def enrich_network_demo(demo: dict) -> None:
    """Add source-backed detail to a newly loaded network demo in place."""
    if demo["id"] != "network-io":
        raise ValueError("Network enrichment only accepts network-io")
    if all("queue" in scene for scene in demo["scenarios"]):
        return
    _function_sources(demo)
    for scene in demo["scenarios"]:
        scene["assumptions"].append(
            "队列固定为 8 槽 direct descriptors；地址是示意 GPA / IOVA。TX 选择 xmit_skb 的 can_push=false "
            "且 skb 无分片分支，形成 12 B 头 + 60 B 帧两项；RX 选择 add_recvbuf_small，"
            "设备可写长度 12 + GOOD_PACKET_LEN = 1530 B，不等于页片段的实际分配大小。"
        )
        _split_publications(scene)
        _backend_notifications(scene)
        _completion_interrupts(scene)
        _snapshot_queue(scene)
        _node_states(scene)
        _call_paths(scene)
        _parallel_network_paths(scene)


def simplify_network_presentation(demo: dict) -> None:
    """Publish one successful trace per backend/direction, without branch controls.

    Keep exceptional traces and independent-event regression data in the source
    catalog. A reading order is not a new dependency between Guest and host.
    """
    excluded = {"qemu-tx-backpressure", "qemu-rx-no-buffer"}
    demo["scenarios"] = [scene for scene in demo["scenarios"] if scene["id"] not in excluded]
    for category in demo["selector"]["categories"]:
        for group in category["groups"]:
            group["scenarios"] = [item for item in group["scenarios"] if item["id"] not in excluded]
    for scene in demo["scenarios"]:
        scene.pop("parallel", None)
        # Preserve queue events when moving independent host-buffer operations.
        previous, patches = {}, {}
        for step in scene["steps"]:
            snapshot = step["queueState"]
            patches[step["id"]] = {key: deepcopy(value) for key, value in snapshot.items()
                                    if key != "changed" and previous.get(key) != value}
            previous = snapshot
        by_id = {step["id"]: step for step in scene["steps"]}
        if scene["id"] in {"qemu-tx", "vhost-tx"}:
            # The TAP call already submits to the host network stack. Do not
            # invent an extra, later "forwarding" action after Guest reclamation.
            scene["steps"] = [step for step in scene["steps"] if step["id"] != "egress"]
            scene["links"] = [link for link in scene["links"] if link["id"] != "egress"]
            step = by_id["transfer"]
            step["title"] = "提交宿主网络栈"
            step["description"] = "报文经 TAP 交给宿主网络栈；本例采用普通复制路径，宿主已有独立的报文存储。"
            step["detail"] = (
                "qemu_sendv_packet_async() 经 tap_write_packet() 调用 writev()；成功返回后，QEMU 才发布 TX used。"
                if scene["id"] == "qemu-tx" else
                "vhost-net 经 TAP socket 的发送接口提交帧；成功接受且不再使用 Guest 缓冲区后，才发布 TX used。"
            ) + "这表示宿主已接受报文，不表示物理网卡发完或远端收到；后续转发不等待 Guest 回收。"
            step["changes"]["networkStage"] = "宿主网络栈已接受；出口发送与远端接收未确认"
            step["nodeStates"]["port"] = "宿主网络栈已接受，不等待 Guest 回收"
            scene["summary"] = "18 B UDP 载荷形成 60 B 帧，提交宿主网络栈后归还 TX 链；完成通知表示缓冲区可回收，不是对端收包确认。"
        elif scene["id"] in {"dpdk-tx", "dpdk-rx"}:
            # Follow the host caller after the synchronous library API returns,
            # then observe the Guest. Neither side waits for the other.
            event = "pmd" if scene["id"] == "dpdk-tx" else "free-mbuf"
            scene["steps"].remove(by_id[event])
            position = scene["steps"].index(by_id["route-interrupt"]) + 1
            scene["steps"].insert(position, by_id[event])
            by_id["route-interrupt"]["detail"] = by_id["route-interrupt"]["detail"].replace(
                "以下从这个共同边界分别观察 Guest 中断处理与宿主 mbuf 的后续操作。",
                "接着跟踪宿主调用者，再观察 Guest 处理；这是演示的阅读顺序，两者不互相等待。",
            )
            if scene["id"] == "dpdk-tx":
                scene["phases"].append({"id": "pmd-cleanup", "title": "宿主回收", "start": "pmd-cleanup"})
        clarify_network_presentation(scene)
        positions = {step["id"]: index for index, step in enumerate(scene["steps"])}
        scene["phases"] = sorted(
            (phase for phase in scene["phases"] if phase["start"] in positions),
            key=lambda phase: positions[phase["start"]],
        )
        snapshot = {}
        for step in scene["steps"]:
            snapshot.update(patches[step["id"]])
            step["queueState"] = {**deepcopy(snapshot), "changed": step["queueState"]["changed"]}
