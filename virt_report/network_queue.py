"""Typed split-ring snapshots and source paths for the offline network lesson.

The narrative JSON owns the packet/application story. This module adds explicit
queue events, never reconstructing device state by parsing translated prose.
Addresses are teaching GPAs; the ring and buffer layout is pinned separately
from the backend implementation.
"""
from __future__ import annotations

from copy import deepcopy
from urllib.parse import urlsplit


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


def _snapshot_queue(scene: dict) -> None:
    """Replay known event IDs into typed snapshots; no translated text parsing."""
    tx = "-tx" in scene["id"]
    scene["queue"] = {
        "structureId": "virtqueue",
        "name": "TX" if tx else "RX", "index": 1 if tx else 0, "size": 8,
        "descriptorIndices": [0, 2] if tx else [2], "head": 0 if tx else 2,
        "requestId": "P1", "direct": True, "addressSpace": "示意 GPA / IOVA",
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
        _snapshot_queue(scene)
        _call_paths(scene)
