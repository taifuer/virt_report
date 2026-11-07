"""Small, deterministic balloon traces using the shared execution player."""
from __future__ import annotations

from copy import deepcopy

QEMU = "https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/virtio/virtio-balloon.c"
LINUX = "https://github.com/torvalds/linux/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/drivers/virtio/virtio_balloon.c"


def build_demo() -> dict:
    """Keep the numeric example separate from observed host memory usage."""
    sources = {
        "target": {"label": "QEMU · virtio_balloon_to_target / virtio_balloon_stat", "url": QEMU + "#L796-L817"},
        "inflate": {"label": "Linux · fill_balloon / tell_host", "url": LINUX + "#L205-L290"},
        "deflate": {"label": "Linux · leak_balloon / release_pages_balloon", "url": LINUX + "#L292-L340"},
        "discard": {"label": "QEMU · balloon_inflate_page / balloon_deflate_page", "url": QEMU + "#L69-L170"},
        "queue": {"label": "QEMU · virtio_balloon_handle_output", "url": QEMU + "#L402-L460"},
        "report": {"label": "QEMU · virtio_balloon_handle_report", "url": QEMU + "#L341-L400"},
        "spec": {"label": "VIRTIO 1.2 CS01 · Memory Balloon Device", "url": "https://docs.oasis-open.org/virtio/virtio/v1.2/cs01/virtio-v1.2-cs01.html#x1-3140005"},
    }
    nodes = [
        {"id": "manager", "label": "QMP 管理请求", "role": "控制面", "initial": "未调整目标"},
        {"id": "driver", "label": "virtio_balloon 驱动", "role": "guest", "badge": "内核态", "initial": "等待配置变化"},
        {"id": "allocator", "label": "Guest 页分配器", "role": "guest", "badge": "内核态", "initial": "正常分配页面"},
        {"id": "device", "label": "QEMU balloon 设备", "role": "userspace", "initial": "等待队列"},
        {"id": "host", "label": "宿主内存管理", "role": "kernel", "initial": "维护后备页"},
        {"id": "queue", "label": "Guest RAM · 页列表与 virtqueue", "role": "memory", "initial": "尚未发布本批页面"},
    ]
    links = [
        ("target", "manager", "device", "control", "设置剩余内存目标"),
        ("config", "device", "driver", "notification", "配置变化通知"),
        ("isolate", "allocator", "driver", "control", "交出空闲页，暂停分配"),
        ("publish", "driver", "queue", "data", "发布页面记录并按条件 kick"),
        ("consume", "queue", "device", "data", "读取队列记录"),
        ("discard", "device", "host", "control", "请求处理后备页"),
        ("complete", "device", "queue", "data", "发布 used 完成"),
        ("ack", "queue", "driver", "notification", "驱动发现完成"),
        ("release", "driver", "allocator", "control", "页面重新可分配"),
    ]
    initial = {"configured": "8 GiB", "target": "8 GiB", "numPages": "0", "actual": "0",
               "held": "0", "guestPool": "8 GiB（含已用与空闲）", "discard": "尚未请求",
               "queue": "空闲", "pages": "尚无本批 PFN", "hostRss": "需实测，演示不推算"}
    labels = {"configured": "Guest 配置 RAM", "target": "QMP 目标（剩余内存）",
              "numPages": "config.num_pages（4 KiB 页数）", "actual": "config.actual（4 KiB 页数）",
              "held": "Guest 暂停分配的页数", "guestPool": "Guest 非气球部分",
              "discard": "宿主后备页处理", "queue": "当前队列", "pages": "队列载荷", "hostRss": "QEMU RSS"}
    layout = {"domains": [
        {"id": "guest", "component": "guest", "title": "Guest", "context": "页分配器与驱动", "nodes": ["allocator", "driver"]},
        {"id": "kvm", "component": "kvm", "title": "KVM", "context": "宿主内核 · 执行与通知支持", "nodes": [], "note": "维持 Guest 执行与地址映射，配合 virtio 通知；气球页的选择由 Guest 驱动完成。"},
        {"id": "qemu", "component": "qemu", "title": "QEMU", "context": "管理接口与设备", "nodes": ["manager", "device"]},
        {"id": "host", "component": "host-kernel", "title": "Host kernel", "context": "后备内存管理", "nodes": ["host"]},
        {"id": "hardware", "component": "hardware", "title": "物理内存", "context": "宿主页面的承载", "nodes": [], "note": "实际驻留受宿主内存策略影响；本例不按气球目标直接推算物理内存使用量。"},
    ], "memory": {"title": "Guest RAM", "description": "队列提交页的地址信息；宿主处理对应后备页。", "nodes": ["queue"]}}

    def step(identifier, title, actor, description, detail, active=(), edges=(), values=None,
             states=None, source="spec"):
        return {"id": identifier, "title": title, "actor": actor, "description": description,
                "detail": detail, "active": list(active), "links": list(edges),
                "changes": values or {}, "nodeStates": states or {}, "sourceIds": [source], "focus": True}

    scenarios = []
    for mode, label in [("inflate", "回收 2 GiB · inflate"), ("deflate", "归还 2 GiB · deflate"),
                        ("report", "空闲页报告 · reporting"), ("inhibited", "回收目标已达成，宿主未 discard")]:
        reporting, deflating = mode == "report", mode == "deflate"
        start = dict(initial)
        if deflating:
            start.update(target="6 GiB", numPages="524288", actual="524288", held="524288", guestPool="6 GiB（含已用与空闲）")
        steps = [step("initial", "初始状态", "两端已就绪", "8 GiB RAM 的地址布局保持不变。",
                      "本例把一次小批页面交接与最终目标分开展示；各步骤没有真实耗时。")]
        if reporting:
            steps.extend([
                step("isolate", "隔离空闲页", "Guest 页分配器", "页报告框架暂时取出一批空闲页。",
                     "这些页在队列完成前不能重新分配；本例展示 512 个 4 KiB 页组成的 2 MiB 区域。",
                     ["allocator", "driver"], ["isolate"], {"held": "512"}, {"driver": "正在报告空闲页"}),
                step("publish", "提交 reporting_vq", "Guest 驱动", "描述符直接指向可丢弃的空闲内存区域。",
                     "reporting_vq 传入设备可写缓冲区，不传 inflateq 使用的 PFN 数组。",
                     ["driver", "queue"], ["publish"], {"queue": "reporting_vq · available 已发布", "pages": "addr = 0x200000, len = 2097152, WRITE"}),
                step("discard", "处理空闲区域", "QEMU → 宿主", "QEMU 校验区域、对齐及 discard 条件，尝试丢弃后备页。",
                     "页报告完成允许 Guest 再次使用这些地址；其原内容已经无须保留。",
                     ["device", "host"], ["consume", "discard"], {"discard": "已请求 discard 2 MiB；RSS 另行观察"}, source="report"),
                step("complete", "完成队列交接", "QEMU → Guest", "QEMU 发布 used；驱动发现队列完成。",
                     "设备处理完这一批后才允许页报告框架重新放回页面。",
                     ["device", "queue", "driver"], ["complete", "ack"], {"queue": "reporting_vq · used 已消费"}, source="report"),
                step("release", "恢复页面分配", "Guest 页分配器", "这一批页面重新回到空闲页管理。",
                     "config.num_pages 与 config.actual 均保持 0。下次使用时，宿主可能需要重新建立后备页。",
                     ["driver", "allocator"], ["release"], {"held": "0"}, {"allocator": "页面可重新分配"}),
            ])
        else:
            queue = "deflateq" if deflating else "inflateq"
            steps.extend([
                step("target", "更新内存目标", "QMP → QEMU", "管理端请求剩余内存为 8 GiB。" if deflating else "管理端请求剩余内存为 6 GiB。",
                     "QEMU 将目标换算成气球页数，通过配置变化通知驱动；目标值尚未代表执行结果。",
                     ["manager", "device", "driver"], ["target", "config"],
                     {"target": "8 GiB" if deflating else "6 GiB", "numPages": "0" if deflating else "524288"}, source="target"),
                step("prepare", "准备一批 PFN", "Guest 驱动", "从气球页链表取出 256 页，准备归还。" if deflating else "从 Guest 分配器取得 256 页，交给气球驱动保管。",
                     "每个 PFN 为 32 位，表示 GPA >> 12。256 个 PFN 占 1024 B，指向的页面共 1 MiB。",
                     ["driver", "allocator"], [] if deflating else ["isolate"],
                     {"held": "524288" if deflating else "256", "pages": "PFN[256]，例如 0x200 → GPA 0x200000"}, source="deflate" if deflating else "inflate"),
                step("publish", "提交页面列表", "Guest 驱动", f"通过 {queue} 发布 PFN 数组，并按通知条件 kick 设备。",
                     "设备读取 PFN 数组；队列描述符指向数组，数组元素才指向目标页面。",
                     ["driver", "queue"], ["publish"], {"queue": f"{queue} · available 已发布"}, source="inflate"),
                step("host", "处理后备页", "QEMU → 宿主", "QEMU 对应地址发出 WILLNEED 提示。" if deflating else
                     "QEMU 当前禁止 discard，跳过后备页丢弃。" if mode == "inhibited" else "QEMU 按 PFN 找到 RAMBlock，尝试丢弃对应后备页。",
                     "队列交接可以正常完成；宿主 RSS 的变化取决于后备类型、页粒度和实际分配。",
                     ["device", "host"], ["consume", "discard"], {"discard": "WILLNEED 提示，未承诺立即驻留" if deflating else
                     "discard 被禁止，未释放后备页" if mode == "inhibited" else "本批请求 discard 1 MiB；RSS 另行观察"}, source="discard"),
                step("complete", "确认本批交接", "QEMU → Guest", "QEMU 发布 used，驱动等待本批完成。",
                     "Linux leak_balloon 在 tell_host 返回后才调用 release_pages_balloon，把页交还分配器。" if deflating else
                     "气球驱动继续持有这些页，普通 Guest 分配不能使用它们。",
                     ["device", "queue", "driver"], ["complete", "ack"], {"queue": f"{queue} · used 已消费"}, source="queue"),
                step("converge", "完成后续批次", "Guest 驱动", "重复小批交接，最终归还 2 GiB。" if deflating else "在内存足够的前提下，重复小批交接，最终达到 2 GiB 气球目标。",
                     "本步汇总剩余批次。actual 是驱动报告的气球页数；QMP query-balloon 的 actual 是剩余字节数。",
                     ["driver", "allocator", "device"], ["release"] if deflating else [],
                     {"held": "0" if deflating else "524288", "actual": "0" if deflating else "524288",
                      "guestPool": "8 GiB（含已用与空闲）" if deflating else "6 GiB（含已用与空闲）",
                      "queue": f"{queue} · 各批次均已回收"},
                     {"driver": "已报告 actual", "allocator": "2 GiB 已重新可用" if deflating else "2 GiB 由气球驱动持有"}, source="deflate" if deflating else "inflate"),
            ])
        phases = [{"id": "prepare", "title": "准备与目标", "start": "initial"},
                  {"id": "queue", "title": "队列交接", "start": "publish"},
                  {"id": "finish", "title": "完成与结果", "start": "complete"}]
        scenarios.append({"id": mode, "label": label, "summary": "区分 Guest 页的使用权、队列交接和宿主后备页处理。",
                          "assumptions": ["x86/KVM，Guest 与后备内存均按 4 KiB 页讲解；RAM 固定 8 GiB，未混合 DIMM 或 virtio-mem。",
                                          "设备、队列与特性协商已完成。通知使用通用 virtio 机制，不限定每次必须注入一次中断。",
                                          "目标达成场景假定 Guest 能取得足够页面；演示不代表 RSS 实测，也不模拟内存压力控制策略。"],
                          "nodes": deepcopy(nodes), "layout": deepcopy(layout),
                          "links": [dict(zip(("id", "from", "to", "kind", "label"), item)) for item in links],
                          "structures": [{"id": "balloon", "title": "气球配置、页列表与后备页", "kind": "address",
                                          "fields": [{"key": key, "label": labels[key], "initial": value} for key, value in start.items()]}],
                          "phases": phases, "steps": steps})
    return {"id": "virtio-balloon", "title": "virtio-balloon 内存交接", "defaultScenario": "inflate",
            "sources": sources, "scenarios": scenarios,
            "selector": {"scenarioLabel": "内存操作", "groups": [{"label": "页面交接", "scenarios": [
                {"id": scene["id"], "label": scene["label"]} for scene in scenarios]}]}}
