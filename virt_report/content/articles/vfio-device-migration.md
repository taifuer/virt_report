## 序言 {#preface}

VFIO 设备热迁移让虚拟机从一台宿主机迁到另一台时，能在目的端的直通设备上接续运行。以正在用 AMD GPU 做计算的虚拟机为例：源端物理 GPU 通过 SR-IOV 提供一个虚拟功能（VF），客户机驱动把它当作自己的 PCI 设备使用。迁移结束后，接着执行任务的是目的端另一块 GPU 上的 VF。两端需要兼容的设备、固件和迁移驱动。

普通虚拟机热迁移已经负责搬运 vCPU、客户机 RAM 和 QEMU 模拟设备的状态。直通 GPU 增加了两项工作：**找出 GPU 通过 DMA 改写的客户机 RAM；把 VF 的显存和硬件执行状态交给目的端驱动。** 例如，一次计算把结果留在显存里，再向客户机内存写入完成记录。目的端看到这条完成记录时，对应的计算结果也应当已经就位。

```baselines
```

## VFIO 迁移接口的演变 {#evolution}

v1 和 v2 指的是 Linux VFIO 提供给 QEMU 的两代设备迁移接口。两代接口都由厂商驱动解释设备状态，变化在于 QEMU 用什么方式控制设备、又从哪里取数据。

| QEMU 版本 | 接口变化 | 对迁移过程的影响 |
| --- | --- | --- |
| 5.2：v1 | 驱动暴露迁移区域。QEMU 读写 `device_state` 位组合，用 `pending_bytes`、`data_offset` 和 `data_size` 找到待传内容。 | 控制字段和数据窗口共用设备文件；双方通过一系列按偏移的读写协调进度。 |
| 8.0：v2 | `VFIO_DEVICE_FEATURE` 查询能力、请求状态转换；进入保存或恢复会话时，内核返回独立的 `data_fd`。 | 设备文件负责控制，数据文件负责顺序读写。基础能力是停机导出和恢复。 |
| 8.1：v2 设备预拷贝 | 支持 `VFIO_MIGRATION_PRE_COPY` 的驱动可在设备工作时提供数据，并回答 `VFIO_MIG_GET_PRECOPY_INFO` 查询。 | 部分设备状态可以提前装入目的端，停机时只处理剩余部分。 |
| 10.0：设备状态接入 multifd | QEMU 可以用多个迁移通道发送 `STOP_COPY` 阶段的设备状态。 | 改变的是两台 QEMU 之间的传输；两端驱动继续顺序读写各自的数据 FD。 |

## QEMU 与内核的迁移流程 {#states}

### 建立会话与运行中预拷贝 {#precopy}

QEMU 先从源设备查询迁移能力。源端单块 VF 支持预拷贝时，`vfio_save_setup()` 请求 `RUNNING → PRE_COPY`。Linux VFIO 把请求交给驱动的 `migration_set_state`，把驱动返回的文件安装成 QEMU 可读的 FD。目的端则通过 `vfio_load_setup()` 进入 `RESUMING`，取得可写的 FD。此时源 VF 继续处理客户机任务，目的 VF 只接收恢复数据。

运行期间有两条数据流。RAM 迁移发送客户机内存页，VFIO 的 `vfio_save_iterate()` 则从设备 FD 读一块，写进迁移流。QEMU 用 `initial_bytes` 和 `dirty_bytes` 估算当前待传的**设备字节数**。启用 `switchover-ack` 时，目的端确认初始设备数据已加载，源端再进入停机切换。这样，初始数据触发的资源准备也可以放在虚拟机仍提供服务的时段。

### 停机与目的端恢复 {#handoff}

暂停 vCPU 后，设备先前接到的任务还可能完成一次 DMA 写入。源端 QEMU 通过虚拟机状态回调和保存回调把 VF 转入 `STOP_COPY`；驱动完成停止后，设备的最后一笔写入也确定下来。RAM 迁移同步最终脏页，VFIO 读完设备 FD 的剩余数据。仅支持停机拷贝的设备在这个阶段导出全部状态，客户机 RAM 的预拷贝仍照常进行。

目的端 QEMU 按设备字节流的顺序写入 FD，恢复 PCI 配置。驱动结束 `RESUMING`、安装设备状态，目的端具备完整的 RAM、vCPU 和设备状态后接管运行。多块 VF 之间有 P2P DMA 时，VFIO 的 P2P 静止阶段会先阻止新的设备间请求，并等在途事务完成，再分别停止设备。

```diagram vfio-migration-handoff
单块 GPU VF 从源端切换到目的端
源端运行时可以同时传客户机 RAM 和设备数据；停机后，最终 DMA 脏页与剩余设备字节在目的端会合。
```

### 运行中取消 {#failure}

如果在预拷贝期间取消，业务仍在源端。源 VF 结束保存会话，返回 `RUNNING`；目的端复位正在恢复的 VF，清理这次迁移的副本。成功切换后，新的计算结果和 DMA 写入发生在目的端，源端保持停止。

## 用 multifd 并行传输设备状态 {#multifd}

单通道路径中，`vfio_save_complete_precopy()` 从源设备 FD 读到流结束，经主迁移通道发送。启用 VFIO multifd 后，QEMU 把停机导出交给工作线程：它读出设备数据，为每块编上序号，再把数据块放入 multifd 发送队列。对于显存较大或同时迁移多个设备的虚拟机，这段传输可能占去相当一部分停机时间。

多个通道的到达顺序可以不同。假设同一 VF 的块 8 比块 7 先到，目的端 `vfio_multifd_load_state_buffer()` 按序号暂存块 8；加载线程取得块 7 后，才顺序把 7、8 写进驱动。增加通道可以提高传输吞吐，乱序块会占用目的端缓冲，驱动导出和导入也各有自己的速率。因此，调优时要同时看停机待传字节、通道吞吐和目的端加载耗时。

## AMD GPU 驱动的实现 {#amd-driver}

### 从 PCI VF 进入 GIM

AMD 的 MxGPU 通过 SR-IOV 把物理 GPU 的资源分给 VF；GIM（GPU-IOV Module）管理这些 VF。宿主机的 `amd-vfio-pci` 把一个 VF 接入 Linux VFIO。在 GIM 9.4.0.K 中，`amd_vfio_pci_open_device()` 检查宿主 IOMMU 的脏页跟踪能力，向 GIM 取得迁移操作，再注册 VF 的 `mig_ops` 和能力标志。

QEMU 请求切换状态后，可以沿内核的 `migration_set_state` 一直追到 GIM 的 `gim_mig_set_state()`。GIM 按 VF 保存当前状态，用 `vfio_mig_get_next_state()` 找到下一步。进入源端预拷贝时，`gim_mig_setup_copy()` 建立缓冲区、复制线程和保存文件；目的端进入恢复时建立方向相反的会话。

### 客户机 RAM 和显存的脏页跟踪 {#dma-dirty}

| 被修改的数据 | 谁记录变化 | 目的端如何获得新内容 |
| --- | --- | --- |
| 客户机 RAM，例如命令队列和完成记录 | KVM 记录 vCPU 写入；对直通设备的 DMA 写入，QEMU 的 VFIO 内存监听器从相应跟踪后端收集脏页。本例的 AMD 驱动要求 IOMMU 支持跟踪。 | 脏页合入 QEMU 的 RAM 迁移集合，由 RAM 迁移重传页面。 |
| 分配给 VF 的 GPU 显存（驱动称为 FB），例如计算结果 | GIM 查询该 VF 的显存变化位图，`gim_mig_update_shadow_dirtybit()` 暂存位图并计算本轮待复制的大小。 | GIM 从置位的显存范围复制数据，经设备 FD 传输；目的端驱动写回对应显存。 |
| VF 硬件状态，例如固件维护的执行上下文 | GIM 区分迁移上下文、静态硬件数据和停机时刻的动态硬件数据。 | 驱动向迁移消息填入各段的类型、偏移和长度，目的端按类型导入。 |

例如，GPU 修改显存页 F，又对客户机 RAM 页 C 写了完成记录。**F 走 VFIO 设备数据流，C 走 RAM 迁移流。** 两者在同一个设备停止点对齐。进一步读芯片实现，可以看 `mi300_dirtybit_query_data()`：它根据显存共享配置选择查询硬件脏位或比较显存页哈希，向上层提供位图。DMA 的地址映射见[设备直通与 DMA 地址转换](qemu-vfio-dma.html)。

### 从一轮预拷贝到最后一次导出

QEMU 在保存 FD 上查询进度时，`gim_migf_save_ioctl()` 汇总当前消息中尚未读出的数据，并通过 `gim_mig_update_msg_copy_info()` 更新下一批内容。迁移早期包括 VF 上下文和静态硬件数据；显存内容按位图选择，连续的脏页可以合成一段复制。源 VF 仍在执行，已经传过的显存页可能再次变脏，后续轮次会补传新版本。

从 `PRE_COPY` 进入 `STOP_COPY` 时，GIM 暂停 VF，把动态硬件数据和最后一批显存变化加入导出。底层 `amdgv_migration_transfer_manifest_data()` 根据数据类型调用固件接口。目的端校验消息头、长度和设备上下文，导入静态、动态数据并写回显存；结束恢复会话时还要等复制线程把环中的数据消费完。QEMU 搬运的是一条字节流，AMD 驱动掌握其中每段的含义。

### 显存与 QEMU 之间的环形缓冲区

`gim_mig_buffer_init()` 分配一块 CPU 和 GPU 都能访问的宿主内存作环形缓冲区。源端复制线程 `gim_mig_data_copy_thread()` 根据显存位图填环，QEMU 一侧的 `read()` 从已填好的区域取走数据。目的端反过来：QEMU 通过 `write()` 填环，复制线程把数据送回目的 VF。读写指针区分空闲与可用空间，环满时生产方等待，空间释放后继续。

这让 GPU 复制下一批显存页时，QEMU 可以处理上一批。驱动与用户空间之间仍有 `copy_to_user()` 和 `copy_from_user()`。**环形缓冲区衔接本机 GPU 和 QEMU；multifd 衔接两台主机的 QEMU。** 观察到网络空闲时，还要看源端能多快填环、目的端能多快把环里的数据写回显存。

```diagram vfio-migration-consistency
显存和客户机内存的两条传输路径
显存经驱动环形缓冲区和 VFIO 数据 FD 传输；DMA 写脏的客户机内存由 QEMU 的 RAM 迁移发送。目的端用两条路径的数据恢复同一次计算。
```

## 单块 GPU VF 的迁移演示 {#demo}

选择“设备预拷贝”和“仅停机拷贝”，比较暂停 vCPU 时目的端已有的显存页。页 F 在运行中被再次写入；最后一笔 DMA 则落在客户机 RAM 页 C。切换场景后，观察这两页分别在哪一步追上源端。

```component _vfio_migration_lab.html
```

## 总结 {#summary}

VFIO 迁移要交接两样东西：设备写过的客户机内存，以及设备本身。QEMU 把 DMA 脏页纳入 RAM 迁移，通过 VFIO 接口控制设备的保存与恢复；AMD 驱动跟踪 VF 显存，导出硬件状态，在目的 GPU 上重建它们。

评估停机时间时，先量停机边界还剩多少设备数据，再区分时间花在源 GPU 导出、跨主机传输还是目的 GPU 导入。设备预拷贝减少停机待传量，GIM 的环形缓冲区让本机复制与读写重叠，multifd 增加跨主机的传输并行度。这三项优化分别作用于不同环节。

## 核心源码 {#sources}

- [QEMU hw/vfio/migration.c：设备迁移会话、预拷贝与恢复](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/vfio/migration.c#L449)
- [QEMU hw/vfio/listener.c：把设备 DMA 脏页并入 RAM 迁移](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/vfio/listener.c#L1184)
- [QEMU hw/vfio/migration-multifd.c：停机时拆分并传输设备状态](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/vfio/migration-multifd.c#L165)
- [Linux include/uapi/linux/vfio.h：迁移状态、数据 FD 与预拷贝查询](https://github.com/torvalds/linux/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/include/uapi/linux/vfio.h#L1011)
- [Linux drivers/vfio/vfio_main.c：向厂商驱动分派状态请求](https://github.com/torvalds/linux/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/drivers/vfio/vfio_main.c#L888)
- [AMD gim/amd-vfio-pci/amd-vfio.c：为 GPU 的 VF 注册迁移回调](https://github.com/amd/MxGPU-Virtualization/blob/1336e0c98e90e719375abeda5269fd72bd8f34bc/gim/amd-vfio-pci/amd-vfio.c#L115)
- [AMD gim/gim_shim/gim_mig.c：跟踪预拷贝进度、通过环形缓冲区搬运显存](https://github.com/amd/MxGPU-Virtualization/blob/1336e0c98e90e719375abeda5269fd72bd8f34bc/gim/gim_shim/gim_mig.c#L710)
- [AMD libgv/core/amdgv_dirtybit.c：累积显存脏页位图](https://github.com/amd/MxGPU-Virtualization/blob/1336e0c98e90e719375abeda5269fd72bd8f34bc/libgv/core/amdgv_dirtybit.c#L333)
- [AMD mi300_dirtybit.c：读取 MI300 的硬件脏页记录](https://github.com/amd/MxGPU-Virtualization/blob/1336e0c98e90e719375abeda5269fd72bd8f34bc/libgv/core/hw/AI/mi300/mi300_dirtybit.c#L440)
- [AMD libgv/core/amdgv_live_migration.c：导出与导入固件状态](https://github.com/amd/MxGPU-Virtualization/blob/1336e0c98e90e719375abeda5269fd72bd8f34bc/libgv/core/amdgv_live_migration.c#L404)
- [QEMU 提交：接入 VFIO v2 迁移接口](https://github.com/qemu/qemu/commit/31bcbbb5be04c7036223ce680a12927f5e51dc77)
- [QEMU 提交：为 VFIO 设备加入预拷贝](https://github.com/qemu/qemu/commit/eda7362af9595a9c6b1f1fefdd94b5ef711c250c)
