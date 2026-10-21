## 直通网卡的状态交接 {#preface}

一台虚拟机使用直通网卡 VF 接收数据包。网卡把报文写入客户机的接收缓冲区，推进接收队列，并产生完成通知。迁移这台虚拟机时，目的端既要拿到报文所在的 RAM，也要恢复网卡已经处理到哪一个请求。只恢复其中一部分，就可能把旧缓冲区当作新报文，或让已经完成的请求再次执行。

本文以一块支持 VFIO 迁移的网卡 VF 为例，跟踪源端继续收包、状态预拷贝、暂停交接和目的端恢复。客户机使用普通 RAM，未启用 vIOMMU；目的端已有兼容的设备与网络配置。设备型号、固件、驱动和管理面的配套共同决定兼容性，普通 VFIO 直通能力不包含迁移保证。

```baselines
```

## RAM、设备状态与运行权 {#consistency}

客户机驱动的描述符、报文缓冲区和完成记录通常位于客户机 RAM。网卡内部还可能保存队列上下文、内部缓存、协议处理进度等状态。这些内容由设备及其迁移驱动定义，QEMU 无法通过读取客户机内存把它们全部重建出来。

| 需要交接的内容 | 更新者与位置 | 迁移时的处理 |
| --- | --- | --- |
| 客户机执行状态 | vCPU 寄存器、中断控制器等 | 停机后保存并在目的端恢复 |
| CPU 写入的 RAM | 客户机 CPU 修改页表、队列和应用数据 | KVM 脏页日志等机制交给 QEMU RAM 迁移 |
| DMA 写入的 RAM | 直通设备写入报文、完成记录等 | 设备或 IOMMU 的 DMA 脏页跟踪交给 QEMU RAM 迁移 |
| VF 内部状态 | 设备队列上下文、内部缓存和运行进度 | VFIO 迁移驱动导出字节流，目的端兼容驱动导入 |
| 设备配置与运行权 | PCI 配置及源、目的端的执行状态 | 恢复配置，完成全部依赖后切换运行端 |

假设源端第一次传出了接收缓冲区所在的页 P。随后网卡把新报文写到 P，同时推进内部接收队列。下一轮迁移需要重新发送 P；停机交接还要拿到与该报文对应的设备状态。**CPU 写脏记录、DMA 写脏记录和设备内部变化需要分别覆盖，最终在同一个停止边界上对齐。**

```diagram vfio-migration-consistency
一次收包涉及的三类变化
CPU 与设备都能修改客户机 RAM；VFIO 导出的设备状态是另一条数据流。停止所有相关写入者后，目的端才能得到一致的恢复点。
```

RAM 可以在两端同时保留副本，但同一虚拟机的执行只能交给一个端点。源端停机后保留的内存与设备状态仍有恢复价值；目的端开始运行后，它们已经成为旧副本，不能再直接启动源端。

## VFIO 用状态机约束设备行为 {#states}

QEMU 通过设备 fd 上的 `VFIO_DEVICE_FEATURE_MIGRATION` 查询支持能力，再通过 `VFIO_DEVICE_FEATURE_MIG_DEVICE_STATE` 切换状态。设备迁移至少需要 `VFIO_MIGRATION_STOP_COPY`；运行中预拷贝由可选的 `VFIO_MIGRATION_PRE_COPY` 标志声明。

| 状态 | 设备对客户机的行为 | 状态数据的方向 |
| --- | --- | --- |
| `RUNNING` | 正常处理请求，可进行 DMA 和中断 | 无迁移数据会话 |
| `PRE_COPY` | 继续工作，并跟踪内部状态变化 | 源端读取初始状态和后续变化 |
| `STOP` | 不再改变内部状态，也不产生 DMA 或中断 | 无活动迁移数据会话 |
| `STOP_COPY` | 保持停止 | 源端读取剩余状态，直到永久结束 |
| `RESUMING` | 保持停止，接收要恢复的状态 | 目的端写入迁移字节流 |

`RESUMING` 表示“正在恢复状态”，设备还没有恢复对客户机的服务。进入保存或恢复会话时，内核返回独立的 `data_fd`。QEMU 从源端 fd 读出字节，再按相同顺序写入目的端 fd。数据内容对 QEMU 是不透明的，不能根据名称把其中的 `dirty_bytes` 当成客户机 RAM 脏页数量。

```text
设备 fd：VFIO_DEVICE_FEATURE
  device_state = PRE_COPY / STOP_COPY / RESUMING
  data_fd      = 由内核返回的数据会话 fd

源端 data_fd --read--> QEMU 迁移流 --write--> 目的端 data_fd
                 保持设备状态字节顺序
```

状态切换成功返回，表示驱动已经完成该转换；失败时设备可能停在组合转换的中间状态。`ERROR` 需要设备复位，不能当作普通暂停处理。Linux UAPI 同时规定，中止目的端的 `RESUMING` 会话需要复位设备。复位回到初始 `RUNNING` 状态，会丢失正在恢复的上下文；需要保持停止时，还要继续请求 `STOP`。这里的初始 `RUNNING` 不代表客户机队列已经配置或目的端虚拟机已经启动。[状态与数据 fd 的定义](https://github.com/torvalds/linux/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/include/uapi/linux/vfio.h#L1011-L1255)

## 运行阶段：同步 RAM 与预拷贝设备状态 {#precopy}

迁移开始后，QEMU 为 RAM 开启脏页跟踪。支持设备预拷贝时，`vfio_save_setup()` 还把源端 VF 从 `RUNNING` 切到 `PRE_COPY`；目的端的 `vfio_load_setup()` 把对应设备放入 `RESUMING`。此时仍由源端处理业务，目的端只恢复数据。

VFIO 的预拷贝查询把待传状态分为 `initial_bytes` 和 `dirty_bytes`。前者表示初始设备状态，后者表示此前导出后又发生的内部变化。驱动可按设备特点选择导出粒度，这两个估计值不保证等于最终 `STOP_COPY` 阶段的剩余量。QEMU 使用查询结果参与迁移进度判断，再由 `vfio_save_iterate()` 分块读取并发送。[预拷贝查询语义](https://github.com/torvalds/linux/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/include/uapi/linux/vfio.h#L1257-L1305)、[QEMU 迭代发送](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/vfio/migration.c#L589-L632)

初始设备状态可能触发目的端较慢的资源分配。启用 `switchover-ack` 后，目的端加载初始数据并反馈确认，QEMU 再允许进入相应的停机切换。这可以把部分准备成本移到源端仍运行的阶段。**该确认只说明初始加载满足切换条件，不表示全部状态已经恢复，也不授予目的端运行权。**

设备不支持预拷贝时，RAM 仍可正常迭代；设备状态留到停机后导出。这条路径减少了运行中设备状态跟踪的要求，但停机窗口通常要承受更多设备状态的传输和加载工作。[初始数据标记及接收确认](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/vfio/migration.c#L602-L631)、[目的端确认处理](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/vfio/migration.c#L726-L807)

## 停机阶段：停止 DMA 并形成恢复点 {#handoff}

停止 vCPU 只能阻止客户机继续提交 CPU 请求。此前已经交给 VF 的工作仍可能推进，DMA 还可能修改 RAM。QEMU 通过虚拟机状态回调驱动 VFIO 状态转换，使设备到达停止状态，再同步最终的 DMA 脏页。停机后导出的设备状态才能与最终 RAM 内容对齐。

对于有预拷贝的路径，源端设备可进入 `STOP_COPY` 并继续读取同一保存会话的尾部；无预拷贝时，设备先停止，再进入 `STOP_COPY` 开始导出。目的端按顺序写入设备状态，结束 `RESUMING` 时，驱动完成最终校验和状态安装。在完整 RAM、vCPU、设备配置和内部状态都就绪之后，目的端才启动设备与客户机。

```diagram vfio-migration-handoff
源端、迁移流与目的端的交接
RAM 和设备状态在停机窗口内都要完成。图中的并列分支表示共同的完成条件，不要求它们按固定先后发送，也不表示两者必然使用并行通道。
```

`vfio_vmstate_change()` 在虚拟机恢复运行时请求设备进入 `RUNNING`。这个请求可以由内核沿合法的组合转换到达目标状态，其中包含结束 `RESUMING` 的校验；设备恢复成功是完成切换的重要条件。源端的 `STOP_COPY` 清理则放在保存清理阶段，避免不必要地延长关键停机路径。[虚拟机状态回调](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/vfio/migration.c#L851-L905)、[源端最终保存与清理](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/vfio/migration.c#L509-L538)

多设备间还可能存在 P2P DMA。支持 `VFIO_MIGRATION_P2P` 时，`RUNNING_P2P` / `PRE_COPY_P2P` 阶段先禁止新的 P2P 请求，并完成在途 P2P 事务，然后再把各设备彻底停下来。否则，一块设备已经停止，另一块却仍向它写入，会破坏一致性。QEMU 10.2 对多 VFIO 设备的迁移检查这项能力；单设备例子无需引入这组状态。[P2P 静止语义](https://github.com/torvalds/linux/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/include/uapi/linux/vfio.h#L1199-L1238)、[多设备能力检查](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/vfio/migration.c#L1041-L1081)

```component _vfio_migration_lab.html
```

## DMA 脏页跟踪接入 RAM 迁移 {#dma-dirty}

同一接收缓冲区 P 可能先被 CPU 初始化，再由网卡写入报文。KVM 对 CPU 写入的记录并不能覆盖这次设备 DMA。QEMU 的 VFIO MemoryListener 需要把设备侧的写脏结果合并到 RAM 迁移的脏页集合中，再由 RAM 迁移发送页面内容。

| 跟踪来源 | 观察范围 | 条件与成本 |
| --- | --- | --- |
| 设备 DMA 日志 | 设备报告它写入的 IOVA 区间 | 依赖设备迁移驱动；日志粒度及读取成本由实现决定 |
| IOMMU 硬件脏页跟踪 | 页表所管理的 DMA 映射 | 依赖宿主 IOMMU 和驱动支持，页表需以相应能力创建 |
| 保守重传 | 无法精确区分的映射范围 | 可能重复传输大量 RAM，难以满足停机与收敛目标 |

QEMU 10.2 的 IOMMUFD 后端已查询 `IOMMU_HW_CAP_DIRTY_TRACKING`，在支持时分配带跟踪能力的 HWPT，并通过对应 ioctl 启停与读取位图。这条路径依赖宿主 IOMMU 与驱动提供跟踪能力，未提供该能力的平台不能使用该方式。[HWPT 能力查询与分配](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/vfio/iommufd.c#L348-L399)、[IOMMUFD 脏页接口](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/backends/iommufd.c#L337-L387)

正常配置应在迁移前确认有效的 DMA 跟踪路径。在 QEMU 10.2 的自动选择模式下，设备和 IOMMU 均不支持可用跟踪时会添加迁移阻断条件。设备内部状态保存成功，仍不能弥补 RAM 中漏掉的 DMA 写入。[能力门控](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/vfio/migration.c#L1186-L1198)、[VFIO 脏页同步](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/vfio/listener.c#L1184-L1235)

## 取消迁移与失败边界 {#failure}

运行阶段取消迁移最容易保持原有业务：源端尚未停止，仍持有完整的当前状态；终止设备保存会话，让 VF 返回 `RUNNING`，销毁目的端准备状态即可。交互演示采用这个取消位置，并假定状态转换成功。

| 发生位置 | 需要确认的条件 | 处理边界 |
| --- | --- | --- |
| 源端运行中的预拷贝 | 目的端未运行，源设备仍可正常返回运行态 | 取消保存，复位并清理目的端恢复会话，继续使用源端 |
| 源端已经停止，目的端尚未运行 | 源设备状态仍可恢复，管理面确认目的端不会启动 | 根据错误和设备状态决定是否恢复源端；不能只根据网络断开判断 |
| 设备转换失败或进入 `ERROR` | 当前实际设备状态、复位结果及业务状态 | 复位可能丢失原有运行上下文，不能承诺无损继续 |
| 目的端已经运行 | 目的端已产生新的 CPU、设备和外部 I/O 状态 | 源端旧副本不能直接恢复，避免双端执行和重复外部写入 |

QEMU 的 `MIG_EVENT_PRECOPY_FAILED` 处理会尝试把源设备恢复到 `RUNNING`；底层状态转换失败还可能触发复位。这是错误处理路径，不构成“任何失败都可无损回退”的保证。管理面需要结合迁移状态、设备错误和端点隔离决定下一步。[失败通知与状态恢复](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/vfio/migration.c#L908-L932)、[状态转换失败处理](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/vfio/migration.c#L124-L214)

## 从停机导出到运行中准备 {#evolution}

直通把请求交给真实硬件后，设备内部状态的格式与停止方式也随硬件而变化。VFIO 迁移状态机把行为约束统一在内核接口上，设备驱动负责具体的导出、恢复和兼容性检查，QEMU 负责把这些操作接入虚拟机迁移生命周期。

| 改进 | 移走或减少的关键成本 | 仍然保留的条件 |
| --- | --- | --- |
| `STOP_COPY` / `RESUMING` | 建立统一的停止、导出和导入接口 | 设备必须有可迁移状态实现；全部导出会占用停机窗口 |
| 可选 `PRE_COPY` | 把部分设备状态传输提前到运行阶段 | 增加状态跟踪与重复传输；高变化率仍可能留下较多尾部 |
| `switchover-ack` | 把目的端初始资源准备尽量移出停机窗口 | 只覆盖相应初始加载，最终校验与恢复仍需完成 |
| 设备或 IOMMU DMA 日志 | 减少保守重传的 RAM | 跟踪粒度、查询开销和平台能力仍有约束 |
| VFIO 状态 multifd 传输 | 并行发送停机阶段的设备状态 | 目的端仍需保持每个设备字节流的加载顺序，并管理缓冲 |

QEMU 从 10.0 起支持用 multifd 传输 VFIO 的 `STOP_COPY` 状态。它扩展可用传输并行度，不能消除源设备停止、目的端安装状态和网络重新可达的耗时。分析停机时间应分段观察：停止与同步、最终 RAM、设备状态发送、目的端加载及启动；有些工作可重叠，应看关键路径，不能把所有阶段时长简单相加。[QEMU 10.2 VFIO 迁移与 multifd 说明](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/docs/devel/migration/vfio.rst)

QEMU 10.2 的 VFIO 迁移拒绝 RAM postcopy，且在启用客户机 vIOMMU 时添加阻断条件。[vIOMMU 映射同步](qemu-viommu.html)与本篇的迁移路径在该版本中需要分开使用。[postcopy 检查](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/vfio/migration.c#L419-L447)、[vIOMMU 检查](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/vfio/migration.c#L1200-L1210)

## 源码入口与参考资料 {#sources}

- [QEMU VFIO migration.c](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/vfio/migration.c)：沿 `vfio_save_setup()`、`vfio_save_iterate()`、`vfio_save_complete_precopy()` 和 `vfio_load_state()` 跟踪数据；结合 VM 状态回调观察停止与启动。
- [Linux VFIO UAPI](https://github.com/torvalds/linux/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/include/uapi/linux/vfio.h)：状态机、数据 fd、P2P 和预拷贝估计值的契约。
- [QEMU VFIO MemoryListener](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/vfio/listener.c)：跟踪启停与 RAM 脏页同步。
- [QEMU VFIO multifd 实现](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/vfio/migration-multifd.c)：停机状态的分块、通道传输与顺序加载。
- [VFIO 设备直通与 DMA 地址转换](qemu-vfio-dma.html)：设备区域、DMA 映射与请求访问的基础路径。
- [预拷贝内存迁移](qemu-memory-migration.html)、[迁移流格式](qemu-migration-stream.html)、[IOMMUFD 对象与生命周期](iommufd.html)：分别展开 RAM 迭代、迁移流组织与 DMA 页表管理。
