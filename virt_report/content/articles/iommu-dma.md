## DMA 地址转换与隔离 {#preface}

网卡接收报文时，会把数据直接写进驱动提供的缓冲区。设备发出的地址必须落到正确的物理页；同一台宿主机上运行着多台虚拟机，还需要限制每个设备能访问的范围。IOMMU 在设备到内存的路径上完成地址转换与权限检查，使这两个要求可以由同一套映射表达。

本文以一块直通网卡接收 64 B 数据为例，沿着设备标识、I/O 页表、转换缓存和内存回收分析完整过程。硬件结构采用 Intel VT-d 的传统转换模式，设备未启用 ATS，客户机未配置虚拟 IOMMU。宿主 IOMMU 开启转换，客户机 RAM 已由宿主固定并建立映射；此时设备使用的 IOVA 与客户机 GPA 数值相同。

```baselines
```

## 一个接收缓冲区的地址路径 {#address-path}

客户机驱动把缓冲区地址 `0x00400080` 交给网卡。宿主提前映射了它所在的 4 KiB 页：

| 项目 | 本例中的值 | 用途 |
| --- | --- | --- |
| 设备 | `0000:03:00.0` | 本例 PCIe 功能的标识 |
| Requester ID | `0x0300` | 请求携带的总线号与设备/功能号 |
| IOVA 页 | `0x00400000` | 设备看到的地址区间起点 |
| HPA 页 | `0x21000000` | 客户机 RAM 的宿主后备页 |
| 页内偏移 | `0x80` | 转换前后保持不变 |
| 访问权限 | 设备可读、可写 | 本次接收使用写权限 |

网卡收到报文后，发出目标为 `IOVA 0x00400080` 的 DMA 写请求。IOMMU 根据设备身份选择转换上下文，查到页映射，再加上偏移，得到 `HPA 0x21000080`。这次 64 B 写入全部位于同一页内。请求跨页时，各段都必须满足相应映射和权限，不能只检查第一个地址。

同一个 IOVA 数值可以在不同转换域中映射到不同物理页。因此，“地址有效”包含两个条件：设备使用正确的转换域，地址在该域内得到允许。PCI segment 与平台拓扑帮助宿主选择对应的 IOMMU，不能把示例的 16 位 Requester ID 当作全系统唯一标识。

客户机 CPU 读取这个缓冲区时，使用的是另一条路径：客户机页表把 GVA 转成 GPA，EPT/NPT 再把 GPA 转成 HPA。CPU 的 TLB 与 IOMMU 的 IOTLB 分别缓存各自的转换。给客户机建立 EPT 映射，并不等于允许网卡向该页 DMA。两条路径最终需要指向同一后备页。有关 QEMU 建立 DMA 映射的过程，可结合 [VFIO 设备直通与 DMA](qemu-vfio-dma.html) 阅读。

## 从设备上下文到页表与 IOTLB {#translation}

### 设备标识选择页表根

在 Intel VT-d 传统模式中，总线号先索引 Root Table，得到该总线的 Context Table；设备号和功能号再选择 Context Entry。上下文记录转换类型、页表根地址、地址宽度和 Domain ID。这里的硬件 Domain ID 用于区分转换缓存所属的地址空间，不能与客户机中的进程 ID 混用。

本例 `03:00.0` 对应 Root Table 的第 3 项，以及该 Context Table 中设备/功能号为 0 的项。Linux 的 `iommu_context_addr()` 完成这两个索引；`domain_context_mapping_one()` 将页表根和域标识写入上下文。硬件可缓存上下文，运行时不必每次重新读取整张表。[上下文寻址](https://github.com/torvalds/linux/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/drivers/iommu/intel/iommu.c#L348-L386) · [上下文配置](https://github.com/torvalds/linux/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/drivers/iommu/intel/iommu.c#L1455-L1504)

### 页表提供映射，缓存减少重复遍历

多级 I/O 页表逐级缩小地址范围，叶子项给出目标物理页和访问权限。对于 4 KiB 叶子，低 12 位是页内偏移。页表级数受地址宽度与硬件配置影响；本文将多级遍历合并显示，不把所有平台都画成固定四级。

IOTLB 保存已经取得的转换及权限。命中时可复用缓存结果；未命中时，硬件遍历页表并取得可用转换。**IOTLB 未命中是正常的缓存事件，页表中没有有效映射才是另一种情况。** 在本文普通 DMA 模式下，缺失映射或写权限不足会阻止访问；它不会自动变成由 QEMU 补一页后继续的 CPU 缺页流程。

```diagram iommu-address-path
一次 DMA 写入的转换路径
设备上下文确定地址空间，IOTLB 与页表提供转换和权限。图中的“遍历页表”由硬件执行，Linux 的映射函数负责事先维护页表。
```

### 三类结构各自保存的内容

| 结构 | 关键内容 | 更新者与使用者 |
| --- | --- | --- |
| Root / Context 表 | 设备到转换上下文的关联、页表根、Domain ID | 宿主配置，IOMMU 查询或缓存 |
| I/O 页表叶子项 | HPA 页基址、读写权限、页大小等属性 | 宿主写入，IOMMU 页表遍历使用 |
| IOTLB | 已取得的地址转换及权限、所属上下文 | IOMMU 缓存，宿主通过失效操作要求更新 |

IOTLB 不保存报文本身。DMA 数据经过检查后才写入 RAM。页表更新需要满足硬件可见性要求；已存在的转换缓存还需要按相应规则失效。CPU 数据缓存一致性、页表写入可见性和 IOTLB 失效分别解决不同的问题，一次普通 CPU 内存写入无法代替所有这些操作。

## 接收、缓存命中与访问故障 {#receive}

下面固定使用同一个缓冲区，切换四种初始条件：第一次转换、已有缓存、仅允许设备读取、目标页未映射。正常路径继续展示停用缓冲区后的回收过程。故障路径在访问检查处停止，不把未写入的数据当作一次成功接收。

```component _iommu_lab.html
```

成功写入后，网卡按照自身协议更新完成信息，客户机驱动才能消费数据。驱动仍需遵守队列、内存顺序与 DMA 同步要求。IOMMU 负责地址与权限，不代替设备的完成协议，也不判断一条报文是否已经被客户机应用读取。

在这台未配置客户机 IOMMU 的虚拟机里，宿主通常长期映射可供 DMA 的 RAM。驱动重复使用这个接收缓冲区时，可以复用既有映射与缓存。下一节展示的是**宿主最终撤销该区域**时的处理：例如设备退出或 RAM 区域移除，而非每接收一个报文都执行一次宿主 UNMAP。

## 撤销映射与安全回收 {#invalidation}

持续运行的接收队列可能已提交多个缓冲区。释放本例后备页之前，先停止向相关队列提交新工作，并按设备协议等待或终止对这块内存的在途访问。只删除页表项，无法撤回已经被设备或总线接受的全部事务。

随后，宿主撤销页表中的映射，发出所需转换缓存失效，并等待完成。完成同步后，旧的 IOVA 到 HPA 转换不再可供后续访问复用；解除页面固定、回收内存才有了必要条件。若设备支持并启用了 ATS，还要处理设备侧缓存，这比本文的无 ATS 路径多了一层。

```diagram iommu-buffer-lifetime
从停止使用到解除映射
示意按安全回收顺序拆开设备停止、页表修改、转换缓存失效及后备页释放。图中 UNMAP 内的子步骤不表示调用返回后还允许保留旧转换。
```

交互中“页表已删除、缓存待同步”是内核撤销流程中的中间状态，设备已停止使用该区域。它帮助说明两项工作的先后关系。Linux 的 `iommu_unmap()` 在清除映射后执行 `iommu_iotlb_sync()`；`iommu_unmap_fast()` 则供调用方批量收集范围，再负责完成同步。应用不应在 UNMAP 尚未成功完成时把后备页交给新的使用者。[同步撤销与批处理接口](https://github.com/torvalds/linux/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/drivers/iommu/iommu.c#L2649-L2688)

映射建立也有同步要求。例如，硬件可能缓存过某些无效转换，上下文从无效改为有效时就要执行对应处理。Intel 驱动的 `context_present_cache_flush()` 根据硬件能力决定上下文、IOTLB 或写缓冲同步；因此，失效操作不能简单概括成“只在删除映射时需要”。[上下文生效处理](https://github.com/torvalds/linux/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/drivers/iommu/intel/iommu.c#L1438-L1453)

## Linux 的映射与失效实现 {#implementation}

前面的数据路径由硬件执行。源码中对应的是配置这条路径的控制操作：设置页表、清除页表，以及让硬件观察到这些改变。

### 把通用映射请求写入硬件页表

`iommu_domain` 表达 IOMMU 子系统中的地址空间及操作接口。Intel 驱动用 `dmar_domain` 保存具体页表和硬件关联。映射请求最终到达以下路径：

```text
IOMMU 子系统分派 map_pages
  intel_iommu_map_pages(domain, iova, paddr, pgsize, pgcount, prot, ...)
    检查页大小及 IOVA / HPA 对齐
    intel_iommu_map(...)
      IOMMU_READ / IOMMU_WRITE → Intel 页表权限
      __domain_mapping(...)
        选择可用页粒度，分配或定位页表项
        写入物理页地址与属性
```

`intel_iommu_map_pages()` 接受 4 KiB、2 MiB 或 1 GiB 粒度，并验证对齐；实际能采用的粒度还受设备所在 IOMMU 能力及域配置约束。`__domain_mapping()` 从物理页号与权限构造页表值。本例最终得到覆盖 `IOVA 0x00400000` 的可写叶子。[映射入口](https://github.com/torvalds/linux/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/drivers/iommu/intel/iommu.c#L3561-L3617) · [页表项构造](https://github.com/torvalds/linux/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/drivers/iommu/intel/iommu.c#L1604-L1680)

### 分开收集撤销范围与同步硬件缓存

```text
IOMMU 子系统分派 unmap_pages
  intel_iommu_unmap_pages(..., gather)
    intel_iommu_unmap(...)
      domain_unmap(...)              清除映射，收集待释放页表页
      iommu_iotlb_gather_add_page()   记录需要失效的范围

IOMMU 子系统执行 iotlb_sync
  intel_iommu_tlb_sync(domain, gather)
    cache_tag_flush_range(...)       同步相关硬件转换缓存
    iommu_put_pages_list(...)        释放已脱离的页表页
```

`gather` 可以汇集撤销范围，减少逐页重复发出同步操作。图中的后备数据页，与这里 `freelist` 中待释放的**页表页**是两类内存：页表页保存转换结构，后备数据页保存客户机数据。后者由上层映射管理代码维护固定引用，不能把这段页表页释放代码解释成整个虚拟机内存已经回收。[清除映射与同步](https://github.com/torvalds/linux/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/drivers/iommu/intel/iommu.c#L3619-L3673)

Linux 6.18 的 `cache_tag_flush_range()` 区分 IOMMU IOTLB 与设备转换缓存，按域关联的标签分别处理。使用 Queued Invalidation 时，`qi_submit_sync()` 提交描述符及等待描述符，并检查完成状态。图中的“等待失效完成”在这里落实为同步协议，不能只看 CPU 已写入队列就认为硬件处理完毕。[缓存范围失效](https://github.com/torvalds/linux/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/drivers/iommu/intel/cache.c#L429-L476) · [队列提交与完成等待](https://github.com/torvalds/linux/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/drivers/iommu/intel/dmar.c#L1369-L1487)

## 扩展能力与性能取舍 {#evolution}

设备直接使用物理地址时，驱动需要处理寻址范围与连续性约束，也缺少按地址空间限制 DMA 的硬件边界。IOMMU 引入 IOVA 与页表后，可以把连续的设备地址映射到不连续的宿主页，并按设备所属域限制访问。代价是页表管理、缓存未命中和映射更新同步。设备直通把这组能力用于多虚拟机隔离，但真实隔离仍取决于 PCIe 拓扑与 IOMMU group，不能仅凭“每个设备设置一张表”判断。

### 大页与较长映射生命周期

大页使一个 IOTLB 表项覆盖更多内存，也减少页表结构和遍历压力。使用它需要地址对齐、物理连续性及硬件支持；局部撤销、细粒度权限和内存热插拔可能迫使映射拆分。长期保留映射减少反复建立与失效的成本，却延长了页面固定时间和设备可访问窗口。两种优化都应结合内存生命周期评估。

| 做法 | 主要收益 | 需要同时评估 |
| --- | --- | --- |
| 更大的 I/O 页 | 更多地址落在同一缓存项，页表更小 | 对齐与连续性、部分撤销、细粒度保护 |
| 复用缓冲区和已有映射 | 减少映射管理与失效次数 | 页面固定规模、回收与隔离范围 |
| 合并失效范围 | 减少同步操作次数 | 失效完成边界与资源复用时机 |
| 设备 ATS 缓存转换 | 减少设备重复获取相同转换的开销 | 设备、IOMMU 支持，以及设备侧缓存失效 |

内核 DMA API 的非严格延迟失效策略是另一项明确的隔离与性能取舍，不能直接套用于本文同步撤销的解释，也不能推导成“所有 UNMAP 都立即逐页刷新”。无论采用哪种策略，软件必须遵守对应接口约定，不能自行跳过设备停止与内存生命周期管理。

### ATS、PASID 与 PRI 的分工

ATS 允许支持该能力的设备取得并缓存地址转换，缓存位于设备侧，常称 ATC 或 Device-TLB；IOMMU 内部的 IOTLB 仍然存在。PASID 为请求增加地址空间标识，使同一个设备可以区分多个进程或上下文。PRI 则提供设备请求处理缺页的协议。三者解决的是缓存位置、地址空间选择和缺页处理三个不同问题。

在 SVA 等配置中，这些能力可以协作，让设备访问进程虚拟地址，并与 CPU 页表更新同步。本文固定后备页、预先建立映射的路径不自动具备这些行为；启用 ATS 也不等于已经实现可恢复缺页。[Linux SVA 与 PCIe 扩展说明](https://docs.kernel.org/6.18/arch/x86/sva.html)

对本例而言，先区分三类现象最有帮助：没有映射或写权限不足属于访问故障；有效映射下大量 IOTLB 未命中属于转换开销；映射建立、撤销或停止设备较慢属于控制路径成本。然后再分别观察工作集、页大小、DMA 方向和映射生命周期，避免把所有现象归为“直通性能”。

## 参考资料 {#sources}

- [Linux DMA API 使用指南](https://docs.kernel.org/6.18/core-api/dma-api-howto.html)：CPU 地址、设备地址与 DMA 同步。
- [Linux 6.18 Intel IOMMU 驱动](https://github.com/torvalds/linux/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/drivers/iommu/intel/iommu.c)：上下文、页表映射与撤销。
- [Intel IOMMU 缓存管理](https://github.com/torvalds/linux/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/drivers/iommu/intel/cache.c)与 [Queued Invalidation 实现](https://github.com/torvalds/linux/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/drivers/iommu/intel/dmar.c)。
- [VFIO 设备、隔离组与生命周期](https://docs.kernel.org/6.18/driver-api/vfio.html)。
- [IOMMUFD：设备绑定与 I/O 地址空间管理](iommufd.html)：从用户态组织设备、逻辑地址空间与硬件页表。
