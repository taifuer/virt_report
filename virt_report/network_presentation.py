"""Keep application, queue, interrupt and transport completion visibly distinct."""
from __future__ import annotations


def _receive_before_application(scene: dict, steps: dict) -> None:
    """Choose one normal RX trace: arrival and socket queuing precede recvfrom.

    Retain the old step ID for existing deep links, but not its blocking story.
    The caller replays queue-event deltas after this independent event is moved.
    """
    receive = steps["recvfrom-wait"]
    scene["steps"].remove(receive)
    scene["steps"].insert(scene["steps"].index(steps["socket-enqueue"]) + 1, receive)
    receive.update(
        title="应用读取已排队数据", actor="Guest 应用线程 · recvfrom",
        description="应用此时调用 recvfrom(fd, U1, 128, 0, ...)，socket 中已有本次 UDP 数据报。",
        detail="接下来由 __skb_recv_udp() 取出已排队的数据，不必睡眠等待。recvfrom 不向网卡请求某个包；"
               "若应用更早调用且队列为空，阻塞调用才会等待，非阻塞调用返回 EAGAIN。",
        changes={"applicationCall": "recvfrom(fd, U1, 128, 0, ...)",
                 "applicationResult": "调用进行中，准备取得并复制载荷",
                 "applicationProgress": "读取已排队的 UDP 数据报"},
        nodeStates={"application": "正在读取 socket 中已有的数据"},
    )
    enqueue = steps["socket-enqueue"]
    enqueue["detail"] = (
        "本例 NAPI 完成时刷新 GRO_NORMAL 普通接收列表；IPv4/UDP 检查通过后，数据进入已绑定的 socket。"
        "此时应用尚未调用 recvfrom，数据可先排队。若已有等待者，则通知其变为可运行；不是重新向设备请求收包。"
    )
    enqueue["active"] = ["guest", "stack"]
    enqueue["links"] = ["stack-input"]
    enqueue["changes"]["applicationProgress"] = "socket 已有数据，应用尚未读取"
    enqueue["nodeStates"]["application"] = "尚未调用 recvfrom；socket 已有数据"
    steps["initial"].update(
        description="socket 已绑定，应用尚未调用 recvfrom。驱动先准备空 RX 缓冲区，供随后到达的外部报文使用。",
        detail="接收空间由驱动维护，不由某次 recvfrom 临时创建。本例展示先到包、后读取的一条正常路径。",
    )
    steps["arrive"]["title"] = "外部报文到达"
    steps["arrive"]["detail"] += " 外部报文可以主动到达，不以 Guest 应用调用 recvfrom 为前提。"
    scene["summary"] = "驱动预置 RX 空间，外部报文到达后进入 Guest socket；应用随后用 recvfrom 读取 18 B 载荷，收包不由 recvfrom 发起。"
    scene["assumptions"].append("本例采用先到包、后调用 recvfrom 的顺序；socket 已绑定且接收预算充足。空队列上的阻塞等待不是收包前提。")
    # Source groups follow the same receive-first story, without inventing a
    # call from NAPI into the independent application thread.
    paths = {path["id"]: path for path in scene["callPaths"]}
    application = paths["guest-application"]
    application.update(title="recvfrom 读取已有数据", boundary="应用线程读取 socket 中已排队的数据；队列为空时才可能等待。设备收包与 NAPI 不由此调用发起。")
    scene["callPaths"].remove(application)
    scene["callPaths"].insert(scene["callPaths"].index(paths["guest-receive-return"]), application)
    paths["guest-network-input"]["boundary"] = "NAPI 完成时刷新普通接收列表，经过 IPv4/UDP 检查后入队。本例应用尚未调用 recvfrom；socket 排队不等待应用读取。"
    paths["guest-receive-return"].update(
        context="Guest 应用线程 · 接收调用返回",
        boundary="读取已有数据报，把 UDP 载荷复制到用户缓冲区并返回长度；网卡帧头与 virtio 头不作为应用载荷返回。",
        entry={"kind": "return", "label": "原接收线程取得已有数据，本次 recvfrom 复制后逐层返回"},
    )


def _completion_status(scene: dict, steps: dict, tx: bool, backend: str) -> None:
    """Author independent state changes; never infer completion from phase order."""
    fields = [
        {"key": "applicationBoundary", "label": "应用 · sendto" if tx else "应用 · recvfrom", "initial": "尚未调用"},
        {"key": "queueBoundary", "label": "Guest TX 缓冲区" if tx else "Guest RX 缓冲区", "initial": "尚未提交" if tx else "尚未预置"},
        {"key": "interruptBoundary", "label": "同一次完成中断", "initial": "尚未发起"},
        {"key": "transportBoundary", "label": "宿主 mbuf M" if backend == "dpdk" else ("本地发送" if tx else "外部报文"),
         "initial": "尚未创建" if backend == "dpdk" and tx else ("尚未提交" if tx else "等待外部到达")},
    ]
    scene["completion"] = [{"key": field["key"], "label": field["label"]} for field in fields]
    scene["structures"].append({"id": "completion-boundaries", "kind": "queue", "title": "完成边界",
                                "fields": [{**field, "hidden": True} for field in fields]})

    def update(event: str, **changes: str) -> None:
        steps[event]["changes"].update(changes)

    update("notify", interruptBoundary="已发起 · 尚未进入 Guest")
    update("route-interrupt", interruptBoundary="vLAPIC 待处理 · 尚未注入")
    update("prepare-interrupt", interruptBoundary="已准备注入 · 尚未进入 Guest")
    update("guest-interrupt", interruptBoundary="Guest 已接受 · NAPI 已安排")
    if tx:
        update("sendto", applicationBoundary="内核正在接受载荷")
        update("sendto-return", applicationBoundary="返回 18 · 不等待设备完成")
        update("publish", queueBoundary="已提交 · 后端仍可读取")
        update("used", queueBoundary="used 已发布 · 可回收")
        update("reclaim", queueBoundary="驱动已回收 · 可复用")
        if backend == "dpdk":
            update("transfer", transportBoundary="独立副本 · 尚未提交 PMD")
            update("pmd", transportBoundary="PMD 已接受 · 等待网卡完成")
            update("pmd-cleanup", transportBoundary="网卡已结束使用 · 已回收")
        else:
            update("transfer", transportBoundary="宿主已接受 · 未确认发完" if backend != "vdpa" else "设备处理中 · 未确认发完")
            if backend == "vdpa":
                update("used", transportBoundary="已结束使用 Guest 数据 · 非远端确认")
    else:
        update("publish-buffer", queueBoundary="空 RX 空间已发布")
        update("arrive", transportBoundary="宿主 mbuf M 已收到帧" if backend == "dpdk" else "本次帧已到达后端")
        update("write-buffer", queueBoundary="帧已写入 · used 尚未发布")
        update("used", queueBoundary="used 已发布 · 等待 NAPI")
        update("receive", queueBoundary="NAPI 已取帧 · 尚未进 socket")
        update("socket-enqueue", applicationBoundary="socket 已有数据 · 尚未读取")
        update("recvfrom-wait", applicationBoundary="读取已排队的数据报")
        update("copy-to-user", applicationBoundary="18 B 已复制 · 准备返回")
        update("recvfrom-return", applicationBoundary="返回 18 · 应用取得载荷")
        update("refill", queueBoundary="新 RX 空间已补充")
        if backend == "dpdk":
            update("write-buffer", transportBoundary="已复制到 Guest · M 尚未释放")
            update("free-mbuf", transportBoundary="M 已释放 · Guest 处理自己的副本")


def clarify_network_presentation(scene: dict) -> None:
    """Clarify one published path, preserving the reference trace and source IDs."""
    steps = {step["id"]: step for step in scene["steps"]}
    tx = scene["id"].endswith("-tx")
    backend = scene["id"].split("-", 1)[0]
    if not tx:
        _receive_before_application(scene, steps)
    elif backend == "dpdk":
        scene["summary"] = "Guest 帧复制为独立的宿主 mbuf 后即可归还 TX 链；PMD 继续发送该副本。Guest 完成中断不是物理发送或远端收包确认。"
    elif backend == "vdpa":
        scene["summary"] = "硬件直接读取 Guest TX 缓冲区，结束使用后发布 used；Guest 回收发送资源，不能据此确认远端已收包。"

    for phase in scene["phases"]:
        if phase["start"] == "fill-used":
            phase["title"] = "发布 TX 完成" if tx else "发布 RX 完成"
        elif phase["start"] in {"notify", "prepare-interrupt"}:
            phase["title"] = "Guest 处理中断"
        elif phase["start"] == "pmd":
            phase["title"] = "提交宿主网卡"
        elif not tx and phase["start"] == "initial":
            phase["title"] = "预置 RX 空间"
        elif not tx and phase["start"] == "arrive":
            phase["title"] = "外部报文到达"

    steps["notify"]["title"] = "发起本次完成中断"
    steps["notify"]["detail"] += " 这是本次 used 完成的提示；随后 KVM 注入与 Guest 处理的是同一次中断，不是再次通知。"
    steps["route-interrupt"]["title"] = "本次中断等待 vCPU 接受"
    steps["prepare-interrupt"]["title"] = "准备注入同一次中断"
    steps["guest-interrupt"]["title"] = "Guest 接受本次完成中断"
    steps["used"]["detail"] += (
        " TX used 表示设备不再使用这份 Guest 缓冲区；驱动还需回收资源，不是物理网卡或远端的发送确认。"
        if tx else " RX used 表示数据已放入 Guest 接收空间；NAPI、协议栈与应用读取尚未完成。"
    )
    if backend == "dpdk":
        steps["route-interrupt"]["detail"] = (
            "本例 irqfd 快速路由在 eventfd 写调用内记录 IRR，随后 vhost 接口返回。"
            "目标 vCPU 尚未接受这次中断；下一步跟踪宿主调用者，随后继续观察同一次中断。"
        )
        if tx:
            steps["pmd"]["detail"] = (
                "Guest TX 链已归还，M 是独立副本；PMD 接受不等于网卡发完。"
                "此处不再发起 Guest 完成通知，vCPU 可在此前或此后处理已挂起的那次中断。"
                "本例只选择先提交 PMD 的一种执行交错，不表示 Guest 必须等待 PMD。"
            )
            steps["pmd"]["nodeStates"]["backend"] = "vhost 取包已返回；M 已交给 PMD"
            steps["pmd"]["changes"]["backendState"] = "vhost 取包已返回；M 已交给 PMD"
            steps["pmd-cleanup"]["nodeStates"]["port"] = "网卡已结束使用，PMD 已回收 mbuf M"
            steps["pmd-cleanup"]["changes"]["pmdQueue"] = "本次 TX 描述符已清理，M 已归还池"
        else:
            steps["free-mbuf"]["detail"] += " 释放宿主副本不再次触发 Guest 完成通知，也不等待 Guest 中断处理。"
    scene["assumptions"].append(
        "本例展示一次队列完成提示从发起到 Guest 接受的过程，不把 eventfd、vLAPIC 待处理与注入计为多次通知。"
        "实际中断可以合并或抑制，驱动也可轮询完成记录；一份报文不保证对应一次中断。"
    )
    _completion_status(scene, steps, tx, backend)
