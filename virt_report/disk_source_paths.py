"""Conservative relationship annotations for the reviewed disk source paths."""
from __future__ import annotations


# Only adjacent, source-reviewed implementation pairs override chronological order.
# A callback invoked synchronously through a function pointer is still a call;
# a later completion, worker, or wakeup belongs to a separate execution context.
_RELATIONS = {
    ("guest-pread", "guest-vfs-read"): "call",
    ("guest-vfs-read", "guest-sync-read"): "call",
    ("guest-sync-read", "guest-read-iter"): "call",
    ("guest-read-iter", "guest-direct-read"): "via",  # blkdev_direct_IO
    ("guest-pwrite", "guest-vfs-write"): "call",
    ("guest-vfs-write", "guest-sync-write"): "call",
    ("guest-sync-write", "guest-write-iter"): "call",
    ("guest-write-iter", "guest-direct-write"): "call",
    ("guest-direct-write", "guest-direct-read"): "via",  # blkdev_direct_IO
    ("guest-direct-read", "guest-bio-wait"): "call",
    ("guest-bio-wait", "guest-bio-submit"): "via",  # submit_bio / submit_bio_noacct
    ("guest-bio-submit", "guest-submit"): "via",  # reviewed direct-dispatch branch
    ("guest-submit", "guest-sg"): "call",
    ("guest-sg", "ring-add"): "via",  # virtqueue_add_sgs / virtqueue_add
    ("host-notifier", "queue-handler"): "via",  # notify_vq / handle_output
    ("queue-handler", "queue-pop"): "via",  # get_request / virtqueue_pop
    ("file-worker", "file-pread"): "call",
    ("file-worker", "file-pwrite"): "call",
    ("read-complete", "status-write"): "call",
    ("status-write", "used-record"): "via",  # virtqueue_push / virtqueue_fill
    ("guest-irq", "guest-done"): "call",  # synchronous virtqueue callback
    ("guest-done", "guest-reclaim"): "via",  # virtqueue_get_buf[_ctx]
    ("guest-end", "guest-block-end"): "call",
    ("guest-block-end", "guest-block-update"): "call",
    ("guest-block-update", "guest-bio-end"): "via",  # req_bio_endio
    ("guest-bio-end", "guest-wait-complete"): "call",  # bi_end_io callback
}

_ENTRIES = {
    "kvm-doorbell": ("notification", "Guest 写队列门铃，KVM 匹配 ioeventfd"),
    "qemu-submission": ("notification", "eventfd 就绪后，IOThread 执行队列通知回调"),
    "qemu-scsi-dispatch": ("notification", "eventfd 就绪后，IOThread 进入 virtio-scsi 队列回调"),
    "qemu-rbd-submission": ("notification", "eventfd 就绪后，IOThread 进入 virtio-blk 队列回调"),
    "host-worker-read": ("async", "aio=threads 把文件 I/O 交给独立 worker"),
    "qemu-completion": ("callback", "异步块 I/O 完成后，重新进入 virtio-blk 完成回调"),
    "qemu-scsi-completion": ("callback", "异步块 I/O 完成后，进入 SCSI 完成回调"),
    "rbd-callback-bh": ("callback", "librbd 在非 QEMU 线程回调，再安排 BH 恢复协程"),
    "kvm-completion-irq": ("notification", "完成侧写 irqfd/callfd，KVM 接收 MSI 路由通知"),
    "kvm-interrupt": ("notification", "完成侧写 irqfd，KVM 记录 pending；注入另在 vCPU 路径执行"),
    "kvm-vmentry-injection": ("async", "vCPU 处理待注入事件，满足条件后准备 VM-entry"),
    "guest-interrupt": ("notification", "硬件递送虚拟中断，Guest 进入队列中断处理"),
    "guest-completion": ("notification", "Guest 处理中断并消费 used；块完成可另行调度"),
    "guest-scsi-completion": ("notification", "Guest 处理中断并消费 SCSI 响应；块完成可另行调度"),
    "guest-bio-completion": ("callback", "块层直接或调度后运行完成回调，再满足 bio 等待条件"),
    "application-resume": ("return", "完成条件满足，原调用线程继续执行并逐层返回"),
    "spdk-nvme-completion": ("async", "NVMe 硬件完成后，poller 消费 CQ 并运行软件完成路径"),
    "spdk-completion": ("callback", "bdev 完成回调返回 vhost，请求仍须发布 used"),
}


def annotate_disk_source_paths(scene: dict) -> None:
    """Annotate assembled scenes without changing evidence, steps or ordering.

    Variant source lists differ, so relationships are applied only after all
    source maps and path replacements. Unknown pairs remain sequential stages;
    in particular pop/parse, queue publication/kick and callback/BH are not calls.
    """
    for path in scene["callPaths"]:
        returning = path["id"] == "application-resume"
        path["flow"] = "return" if returning else "sequence"
        references = path["sourceIds"]
        path["relations"] = [] if returning else [
            {"from": first, "to": second, "kind": _RELATIONS[first, second]}
            for first, second in zip(references, references[1:])
            if (first, second) in _RELATIONS
        ]
        if entry := _ENTRIES.get(path["id"]):
            path["entry"] = {"kind": entry[0], "label": entry[1]}
