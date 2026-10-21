## 客户机管理的 DMA 地址空间 {#preface}

虚拟机使用直通网卡时，网卡直接读写客户机 RAM 的宿主后备页。宿主 IOMMU 负责限制物理设备可访问的内存；客户机启用 vIOMMU 后，还可以为设备分配自己的 DMA 地址、设置访问权限，并在缓冲区回收时撤销映射。QEMU 需要把客户机维护的映射落实到物理设备实际使用的转换路径中。

本文以一块直通网卡 VF 的接收缓冲区为例，采用 q35、Intel vIOMMU 的传统页表模式和软件合成映射路径，启用 `caching-mode`，不启用设备 ATS。先跟踪一页缓冲区的建立、DMA 写入和撤销，再分析相同 IOVA 重新用于另一页时的同步要求。客户机使用 4 KiB I/O 页；地址值用于说明映射关系。

```baselines
```

## 一个缓冲区对应三种地址 {#addresses}

客户机为网卡准备缓冲区 A，它位于 GPA `0x40000000`。客户机 IOMMU 驱动选取 IOVA `0x00800000`，把这一页映射到 A；设备驱动将这个 IOVA 写入接收描述符。宿主把 A 的后备页放在 HPA `0x120000000`。网卡向页内偏移 `0x80` 写入 64 字节时，对应关系如下。

| 地址层次 | 示例地址 | 谁使用它 |
| --- | --- | --- |
| 客户机 IOVA | `0x00800080` | 网卡从描述符取得的 DMA 地址 |
| 客户机物理地址 GPA | `0x40000080` | 客户机 I/O 页表中的目标页加偏移 |
| 宿主物理地址 HPA | `0x120000080` | 宿主 IOMMU 转换后的实际内存位置 |

客户机 I/O 页表保存 **IOVA → GPA**。QEMU 根据 GPA 找到 RAM 对应的宿主虚拟地址 HVA，交给 VFIO 后端；宿主内核获取后备页，建立 **IOVA → HPA** 的硬件映射。HVA 用于管理内存，网卡的 DMA 请求不携带 QEMU 的进程地址。

这条软件合成路径把两段关系合并成宿主 IOMMU 的一段映射。正常 DMA 到达宿主 IOMMU 后，可以直接使用合成结果；每个数据包都不必再进入 QEMU 遍历客户机页表。vIOMMU 的成本主要出现在映射变更、失效通知和相关的页表维护中。

```diagram viommu-shadow-path
客户机映射与物理设备使用的合成映射
上方控制路径把 IOVA→GPA 与客户机 RAM 后备关系合成到宿主页表；下方数据路径直接使用宿主 IOMMU。两条路径在页表处汇合，QEMU 不转发每一笔直通 DMA。
```

EPT 服务于客户机 CPU 的 GPA 访问；这里的宿主 I/O 页表服务于设备 DMA。两者可能最终指向相同 RAM 后备页，但通过不同硬件入口、权限与失效机制维护。[VFIO 的 vIOMMU 映射处理](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/vfio/listener.c#L125-L193)

## 从客户机页表更新到可用的 DMA 映射 {#mapping}

在本例的传统 Intel VT-d 模式下，设备的 Requester ID 选择 root/context 表中的设备上下文，context 指向 I/O 页表并关联 domain。页表的叶子项给出 GPA 和读写权限；多个设备能否共享映射，由其 domain 关系及宿主隔离约束共同决定。

客户机把页表写在 RAM 中，这些内存写入不会逐笔通知 QEMU。QEMU 需要一个明确的同步入口，才能知道哪些转换关系发生了变化。`caching-mode=on` 为本例提供这一约定：客户机 IOMMU 驱动在新增 DMA 映射后也执行同步的 IOTLB invalidation，等待完成，再允许设备使用这个地址。QEMU 在处理失效时扫描相关页表范围，建立或更新宿主映射。[固定版本的 caching-mode 说明](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/qemu-options.hx#L1202-L1208)

完整建立过程分为四步：

1. 客户机驱动申请缓冲区 A，调用 DMA API 建立映射。客户机 IOMMU 驱动分配 IOVA，写入 `IOVA 0x00800000 → GPA 0x40000000`，并发布页表更新。
2. 客户机向虚拟 VT-d 提交失效请求。QEMU 根据 domain、IOVA 和范围找到对应地址空间，遍历客户机页表。
3. QEMU 产生 IOMMU MAP 通知。VFIO listener 把目标 GPA 换算成 RAM 后备地址，调用宿主映射接口，建立 `IOVA 0x00800000 → HPA 0x120000000`。
4. DMA API 完成映射并返回后，客户机驱动才向网卡发布接收描述符。网卡发出 DMA，宿主 IOMMU 检查转换与权限，写入 A。

这里的 invalidation 同时承担了通知 VMM 更新合成映射的作用。**客户机写完页表与物理设备可使用新映射，是两个不同的完成点。** 如果过早提交 DMA，宿主可能还没有对应映射，访问就可能失败。若宿主建图失败，页表内容和客户机侧完成信息也不足以证明设备已可用；还要结合 QEMU 的映射错误和宿主 IOMMU 故障判断。

模拟设备采用另一条执行路径：QEMU 在设备访问内存时可以调用 `IOMMUMemoryRegion.translate`，在软件中取得 GPA。直通设备的请求绕过 QEMU，因此需要提前维护上述硬件映射。[软件转换入口](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/i386/intel_iommu.c#L3943-L3983)

## 撤销映射与复用 IOVA 的顺序 {#invalidation}

映射生效后，宿主 IOMMU 可以把转换结果缓存在 IOTLB 中。客户机随后清除自己的页表项，并不会自动删除合成页表，也不会自动清空宿主 IOTLB。释放缓冲区必须把设备使用结束、页表撤销和失效完成连成一条顺序。

```diagram viommu-map-lifecycle
缓冲区 A 的撤销与缓冲区 B 的接入
先按设备协议完成旧请求，再撤销 A 的映射并等待同步。旧 IOVA 可以在完成撤销后复用，新映射也要完成同步，才能交给设备。示意采用同步回收；实际驱动可以批处理，但必须保持相同的生命周期约束。
```

A 使用结束时，客户机驱动先确认相应接收请求不再由设备持有，再撤销 DMA 映射。QEMU 处理失效请求时读到无效页表项，向 VFIO 发出 UNMAP；宿主删除硬件映射、处理相应转换缓存及后备页引用。完成撤销后，客户机才能将 A 用于其他用途，并安全复用原 IOVA。

随后准备缓冲区 B：GPA 为 `0x50000000`，HPA 为 `0x140000000`，仍使用 IOVA `0x00800000`。新映射完成同步后，相同 DMA 地址 `0x00800080` 的目标变为 `0x140000080`。设备只看到相同 IOVA，背后的页面已经改变。

| 阶段 | 客户机页表 | 宿主有效映射 | 缓冲区处理要求 |
| --- | --- | --- | --- |
| A 正在使用 | 指向 A | 指向 A，IOTLB 也可能缓存 A | 保持 A 有效 |
| 已清除客户机 PTE，尚未同步 | 无映射 | 仍可能指向 A | 不可据此认为旧访问已被阻断 |
| UNMAP 与失效已完成 | 无映射 | 无映射，旧缓存已处理 | 结合设备完成条件回收 A |
| B 的映射已同步 | 指向 B | 指向 B | 可以向设备提交 B |

失效处理负责转换关系，设备协议负责已经提交的工作。**IOTLB 失效不能代替等待旧请求完成。** 更早已经通过地址转换的 DMA 也要按设备和平台的完成语义处理。本例先确认旧请求完成，再撤销映射；设备复位、异常队列和批量延迟回收需要另外维护相应边界。

## 映射与缓存的交互演示 {#interactive}

逐步操作同一个 IOVA，观察客户机页表、宿主合成页表和 IOTLB 的差异。“遗漏失效”场景故意在客户机只清除 PTE 后复用 A，并发出一笔违规的迟到 DMA，用来展示仍有效的旧映射；它没有执行宿主 UNMAP。正常场景在撤销完成后探测同一地址，访问被阻止。

```component _viommu_lab.html
```

两种场景最终都会重新建立 B 的映射。错误场景中的旧页写入不会因之后补做失效而被撤回；它说明缓冲区生命周期必须在交接时保证正确。演示不模拟设备队列内部、ATS 或已经通过 IOMMU 的在途事务。

## QEMU 中的页表同步路径 {#implementation}

前面的建立和撤销共用一条通知链。QEMU 为设备维护 `VTDAddressSpace`，其中的 `IOMMUMemoryRegion` 表示设备侧地址空间；`iova_tree` 记录已经通知后端的映射范围。扫描客户机页表时，QEMU 将新结果与已知映射比较，避免重复通知，并找出需要撤销或新增的区间。

| 结构 | 保存的内容 | 在本例中的作用 |
| --- | --- | --- |
| 客户机 root/context 与 I/O 页表 | 设备上下文、domain、IOVA→GPA、权限 | 客户机希望设备看到的 DMA 地址空间 |
| `VTDAddressSpace` / `iova_tree` | 设备侧 QEMU 地址空间、已通知的映射范围 | 比较和同步变更；它不等同宿主硬件 IOTLB |
| `IOMMUTLBEntry` / `IOMMUTLBEvent` | IOVA、目标地址空间、translated_addr、范围与权限 | 向后端传递 MAP / UNMAP 结果 |
| `VFIOGuestIOMMU` / `IOMMUNotifier` | VFIO 后端与 QEMU IOMMU 地址空间的关联 | 把通知转成宿主映射操作 |
| 宿主 I/O 页表及 IOTLB | IOVA→HPA 及缓存的转换结果 | 物理设备 DMA 实际使用的转换 |

以页粒度的 queued invalidation 为例，主要调用关系如下。domain 或全局失效覆盖的范围更大，但最终仍需使客户机声明的转换变化与后端一致。

```text
客户机更新 I/O 页表，提交 IOTLB invalidation
  vtd_process_iotlb_desc()
    vtd_iotlb_page_invalidate()
      vtd_iotlb_page_invalidate_notify()
        vtd_sync_shadow_page_table_range()
          vtd_page_walk() → vtd_page_walk_one()
            MAP / UNMAP → memory_region_notify_iommu()
              vfio_iommu_map_notify()
                MAP: GPA → RAM 后备地址 → vfio_container_dma_map()
                UNMAP: vfio_container_dma_unmap()
```

`vfio_iommu_map_notify()` 只接受目标为系统内存的转换。MAP 分支取得对应 RAM 区域和宿主地址，同时检查设备写权限及 RAM 是否只读；UNMAP 分支撤销对应 IOVA 范围。实际 ioctl 由选定的 VFIO 后端实现：传统 Type1 和 IOMMUFD 接收相同的上层映射意图，但使用不同管理接口。

| 源码入口 | 阅读重点 |
| --- | --- |
| [`vtd_page_walk_one()`](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/i386/intel_iommu.c#L1268-L1347) | 比较既有映射，发送撤销或新增通知 |
| [`vtd_sync_shadow_page_table_range()`](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/i386/intel_iommu.c#L1631-L1685) | 建立扫描范围和通知回调；处理地址空间同步 |
| [`vtd_iotlb_page_invalidate_notify()`](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/i386/intel_iommu.c#L2467-L2529) | 根据 domain、PASID 和后端能力选择同步方式 |
| [`vfio_iommu_map_notify()`](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/vfio/listener.c#L125-L193) | GPA 后备页解析、权限传播及宿主 MAP / UNMAP |
| [VFIO notifier 注册与 replay](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/vfio/listener.c#L520-L565) | 后端接入时重新发布现有映射，而非仅等待下一次变化 |

源码对直接把同一 IOVA 从 A 改成 B 的情况采用撤销旧映射再建立新映射，并注明这会留下短暂的无映射窗口。因此，客户机需要按设备和 DMA API 的约定完成旧请求与映射交接；不能依赖一次 PTE 改写实现正在使用的缓冲区原子切换。

## 从软件合成到硬件嵌套转换 {#evolution}

无客户机 vIOMMU 的常见直通配置，使用 GPA 作为设备 DMA 地址，宿主集中维护 GPA→HPA。它的映射关系相对稳定；客户机对设备 DMA 地址空间的控制也较少。加入 vIOMMU 后，客户机可以像物理系统一样选择 DMA 地址和隔离范围，宿主则要承担同步这些变化的成本。

软件合成映射兼容较广的宿主硬件，代价是每次相关映射变动都可能经过客户机失效、QEMU 页表扫描和宿主映射接口。频繁短生命周期缓冲区会放大控制路径开销；批量映射、复用缓冲区和批量失效可以减少更新次数，但延迟回收期间必须继续保留正确的页引用和 IOVA 生命周期。

硬件嵌套转换把客户机 IOVA→GPA 与宿主 GPA→HPA 分开交给 IOMMU 执行。客户机维护第一阶段页表，宿主管理第二阶段的可访问内存范围；VMM 仍需配置两阶段关系、传播失效并处理异常。它可以减少软件合成映射的工作，同时引入两阶段页表遍历、缓存一致性和硬件兼容要求。

| 路径 | 转换关系的维护 | 主要成本与边界 |
| --- | --- | --- |
| 无客户机 vIOMMU | 宿主映射 GPA→HPA | 配置简单；客户机没有独立的 DMA 转换视图 |
| vIOMMU + 软件合成 | 客户机 IOVA→GPA 经 QEMU 合成为 IOVA→HPA | 映射变更需同步到宿主；正常 DMA 使用合成结果 |
| vIOMMU + 硬件嵌套 | 第一阶段 IOVA→GPA，第二阶段 GPA→HPA | 依赖 IOMMU、驱动与 VMM 配套；仍需失效与生命周期管理 |

Linux 6.18 的 IOMMUFD 以 `HWPT_NESTED` 和父 `HWPT_PAGING` 表达两阶段关系。QEMU 10.2 的 Intel vIOMMU 代码也区分传统影子同步与 scalable/first-stage 相关路径；启用 vIOMMU 并不自动选择硬件嵌套。具体设备和平台能否使用，需要核对完整的能力与配置组合。[IOMMUFD 的嵌套页表对象](https://docs.kernel.org/6.18/userspace-api/iommufd.html#user-visible-objects)

此外，IOMMUFD 中名为 `VIOMMU` 的对象表示某个物理 IOMMU 的虚拟化资源切片，与客户机看到的一整个虚拟 IOMMU 不必一一对应。区分接口对象和客户机设备模型，有助于阅读跨多个物理 IOMMU 的部署路径。

设备迁移还有独立的兼容性限制：QEMU 10.2 的 `vfio_migration_realize()` 检测到 VFIO 设备启用 vIOMMU 时，会阻止该配置迁移。即使设备具有状态保存能力，也不能据此把本文的 vIOMMU 路径与设备热迁移直接组合。[固定版本的迁移限制](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/vfio/migration.c#L1205-L1209)；未启用 vIOMMU 的设备迁移过程见 [VFIO 设备热迁移](vfio-device-migration.html)。

## 总结与进一步阅读 {#summary}

本例围绕同一个 IOVA 完成了 A 到 B 的交接：客户机建立映射，QEMU 同步合成页表，物理设备通过宿主 IOMMU 访问 RAM；旧请求结束后撤销映射，再把地址交给新的缓冲区。页表内容、转换缓存和设备正在使用的请求分别有自己的状态，正确交接依赖它们按顺序收敛。

排查问题时可以沿这条路径逐层核对：客户机 DMA API 返回的地址与权限、失效是否完成、QEMU 的 MAP / UNMAP 错误、宿主 IOMMU 故障，以及设备描述符是否已经回收。性能评估则需要把映射建立和回收开销，与正常 DMA 的吞吐和延迟分开测量。

- [IOMMU：DMA 地址转换、缓存与失效](iommu-dma.html)：设备标识、页表遍历与缓存的基础机制。
- [IOMMUFD：设备绑定与 I/O 地址空间管理](iommufd.html)：宿主侧 IOAS、HWPT 及设备附着的生命周期。
- [VFIO 设备直通与 DMA 地址转换](qemu-vfio-dma.html)：设备区域、内存后备和地址转换的完整关系。

## 参考资料 {#sources}

- [QEMU 10.2：Intel IOMMU 模型](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/i386/intel_iommu.c)：客户机页表扫描、影子映射同步与失效处理。
- [QEMU 10.2：VFIO MemoryListener](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/vfio/listener.c)：IOMMU 通知、RAM 后备解析与映射接口。
- [QEMU 10.2：Intel IOMMU 参数](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/qemu-options.hx#L1183-L1221)：caching-mode、device-iotlb 与地址宽度。
- [Linux 6.18：IOMMUFD](https://docs.kernel.org/6.18/userspace-api/iommufd.html)：逻辑地址空间、硬件嵌套页表及 VIOMMU 对象。
- [Linux：DMA API 使用指南](https://docs.kernel.org/6.18/core-api/dma-api-howto.html)：DMA 地址、缓冲区映射与设备使用结束后的回收。
