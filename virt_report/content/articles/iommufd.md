## 设备分配中的地址空间管理 {#preface}

把一块网卡 VF 分配给虚拟机，需要同时建立两类访问：客户机通过设备寄存器提交请求，网卡通过 DMA 访问客户机内存。VFIO 提供设备区域、配置空间和中断接口；IOMMUFD 管理 DMA 使用的地址空间与硬件页表。两者通过设备绑定和页表附着连接起来。

本文沿一块 PCIe VF 的接入过程展开：打开设备，建立 DMA 所有权，准备 I/O 地址空间，把客户机 RAM 映射进去，最后停止设备并回收资源。采用 x86 / KVM、普通可固定的 RAM、无客户机虚拟 IOMMU 的配置。设备发出的 IOVA 与 GPA 数值相同，宿主 IOMMU 再把它转换为 HPA。地址转换本身见 [IOMMU：DMA 地址转换、缓存与失效](iommu-dma.html)，这里重点分析转换关系的建立与管理。

```baselines
```

## 两个文件描述符与三类对象 {#objects}

QEMU 打开 `/dev/vfio/devices/vfioX`，得到设备文件描述符 `devfd`；打开 `/dev/iommu`，得到 IOMMUFD 上下文的文件描述符 `iommufd`。示例中的 `vfioX` 是宿主注册设备时分配的节点名，与 PCI 地址分别标识设备节点和 PCI 功能。

| 入口 | 主要操作 | 保存的关系 |
| --- | --- | --- |
| VFIO 设备 fd | 绑定 IOMMUFD、附着或脱离页表、访问设备区域与中断 | 应用与具体设备的访问关系 |
| IOMMUFD fd | 分配 IOAS / HWPT、映射内存、查询能力与销毁对象 | 一个上下文中的地址空间、页表及对象 ID |

`dev_id`、`ioas_id` 和 `hwpt_id` 都是 IOMMUFD 上下文里的对象编号，不能当作 Linux 文件描述符传给 `close()`。同一编号在另一个独立打开的 IOMMUFD 上下文中，也不代表同一对象。

| 对象 | 含义 | 本例中的内容 |
| --- | --- | --- |
| DEVICE | 已绑定到 IOMMUFD 的设备 | 网卡 VF 的 `dev_id = 1`；绑定时建立 DMA 所有权 |
| IOAS | I/O Address Space，逻辑 I/O 地址空间 | `ioas_id = 10`；保存 IOVA 区间与后备内存的关系 |
| HWPT_PAGING | 由内核管理的硬件 I/O 页表对象 | `hwpt_id = 20`；关联 IOAS，对应 IOMMU 的 `iommu_domain` |

IOAS 统一表达映射，HWPT 把这些映射落实到具体 IOMMU 能使用的页表。一个 IOAS 可以关联多个 HWPT：兼容的设备可共享已有 HWPT；无法共用同一硬件页表、但允许共享这组映射的设备，可以使用另一个 HWPT。内核维护这些 HWPT 中一致的 IOVA 映射。**共享 IOAS 也意味着共享 DMA 可见的地址范围**，不能据此把相互不信任的设备放入同一地址空间。

```diagram iommufd-objects
IOMMUFD 中的设备、逻辑地址空间与硬件页表
上半部分是用户态管理入口，下半部分是对象关系。第二块设备和 HWPT 展示同一 IOAS 的扩展方式；它们不属于后文单 VF 演示的必需对象。
```

DEVICE 到 HWPT 的连接由附着操作建立。仅完成绑定时，设备已有所属的 IOMMUFD 上下文，尚未取得这组 RAM 的 DMA 映射。[Linux IOMMUFD 对象说明](https://docs.kernel.org/6.18/userspace-api/iommufd.html#user-visible-objects)

## 一块 VF 从打开到可用 {#setup}

下面沿 QEMU 10.2 首次创建 IOMMUFD 地址空间的路径，区分每一步增加的能力。示例取客户机 RAM 中一个 2 MiB 区间：IOVA 起点 `0x00400000`，QEMU HVA 起点 `0x70000000`。它表示内存布局中的一段 RAM，并不要求每次 I/O 都重新映射。

### 打开与绑定

`open()` 得到设备 fd 后，设备访问仍受限制。QEMU 在该 fd 上调用 `VFIO_DEVICE_BIND_IOMMUFD`，把 `iommufd` 作为输入。成功后返回 `out_devid`，VFIO 才授予设备接口访问权限。内核在此过程中建立设备的 DMA 所有权，并检查所在 IOMMU group 的约束。

设备 cdev 让应用直接按设备打开文件，但物理隔离边界仍由 IOMMU group 决定。同组设备不能同时交给互不相容的 DMA 所有者；PCIe 拓扑造成的隔离限制也不会因接口改名而消失。[VFIO 绑定与访问授权](https://github.com/torvalds/linux/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/drivers/vfio/device_cdev.c#L83-L170)

### 分配地址空间与附着页表

QEMU 在 IOMMUFD fd 上调用 `IOMMU_IOAS_ALLOC`，得到空的 IOAS。对于本例物理设备，QEMU 随后寻找可复用的 HWPT；没有兼容对象时，通过 `IOMMU_HWPT_ALLOC` 指定 `dev_id` 和 `ioas_id`，分配相应 HWPT。

接着，在设备 fd 上调用 `VFIO_DEVICE_ATTACH_IOMMUFD_PT`，将 `pt_id` 设为 HWPT ID。附着成功后，设备与该硬件转换域关联起来。此时 IOAS 仍为空，目标 RAM 的 DMA 访问尚不可用。[QEMU 分配与附着 HWPT](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/vfio/iommufd.c#L302-L399)

API 也允许直接向 IOAS 附着，由内核选择兼容 HWPT 或创建一个。QEMU 这里显式管理物理设备的 HWPT，便于查询能力、复用页表和设置分配标志。两种方式都要经过设备与页表的兼容性检查。

### 注册 RAM 映射

地址空间关联完成后，QEMU 注册 VFIO MemoryListener。内存监听器枚举现有 RAM 区间，并将设备侧地址、QEMU 内存位置、长度和权限传给 IOMMUFD 后端。

```text
IOMMU_IOAS_MAP
  ioas_id = 10
  iova    = 0x00400000      # 设备侧起点，本例等于 GPA
  user_va = 0x70000000      # QEMU 进程中的后备内存起点
  length  = 0x00200000      # 2 MiB 的映射区间
  flags   = FIXED_IOVA | READABLE | WRITEABLE
```

内核验证范围与权限，获取并固定后备页，再把映射同步到关联的 HWPT。2 MiB 的区间长度不保证生成一个 2 MiB 硬件大页；实际页粒度还取决于物理连续性、对齐、IOMMU 能力和映射条件。`READABLE`、`WRITEABLE` 从设备视角解释：网卡读取发送缓冲区需要读权限，写入接收缓冲区需要写权限。

QEMU 的本条路径是**先附着、后映射**。API 允许先向 IOAS 映射、再附着设备，但新设备带来的 IOVA 保留区、地址宽度或页粒度限制，可能使后续附着失败。先后顺序可以选择，地址空间必须满足最终所有设备的约束。[QEMU 首次接入顺序](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/vfio/iommufd.c#L536-L615)、[映射参数构造](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/backends/iommufd.c#L199-L229)

## 映射建立后的 DMA 与资源回收 {#lifecycle}

当客户机驱动向网卡提交 `IOVA 0x00400080` 的接收缓冲区时，设备通过已附着的页表访问对应宿主页。IOMMUFD 的 map ioctl 不出现在每个数据包的路径上；它配置的页表和内存引用，服务于后续一系列 DMA。

```diagram iommufd-lifecycle
单个 VF 的初始化、运行与回收
初始化沿 QEMU 的先附着、后映射顺序；回收示意显式释放依赖。正常 DMA 位于设备与宿主内存之间，用户态只参与配置和生命周期管理。
```

资源回收从设备使用结束开始：停止提交新请求，按设备协议停止队列并处理在途 DMA。随后脱离 HWPT，销毁不再使用的 HWPT，撤销 RAM 映射，再释放 IOAS 和设备绑定。回收共享对象前，还要确认其他设备不再使用它。

`VFIO_DEVICE_DETACH_IOMMUFD_PT` 解除设备与页表的关系，**不会自动删除 IOAS 中的映射**。`IOMMU_IOAS_UNMAP` 删除映射，内核完成相应的页表更新、转换缓存处理与页引用回收。它也不会销毁 IOAS。两类操作分别处理关联与内容，故障回滚时同样需要分清。

绑定由 VFIO 设备 fd 持有，关闭设备 fd 时执行解绑。IOAS 与显式分配的 HWPT 可以通过 `IOMMU_DESTROY` 释放；仍被依赖的对象不能任意销毁。设备绑定还会持有 IOMMUFD 上下文引用，因此仅提前关闭 `/dev/iommu` 的用户态 fd，不能视作所有设备和地址空间已立即回收。[QEMU 设备脱离及对象释放](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/vfio/iommufd.c#L403-L457)、[内核解绑路径](https://github.com/torvalds/linux/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/drivers/vfio/device_cdev.c#L173-L192)

```component _iommufd_lab.html
```

## 共享与失败处理 {#sharing}

接入第二块设备时，首先判断它是否应共享这组 RAM 映射。如果两块设备属于同一虚拟机，且 DMA 地址布局相同，使用同一 IOAS 可以避免应用重复维护逻辑映射；内核仍可能需要更新多个硬件页表。若设备属于不同安全域，应使用独立地址空间，并确保底层硬件能够隔离它们。

| 阶段 | 典型失败条件 | 后续处理 |
| --- | --- | --- |
| BIND | DMA 所有权冲突、设备令牌或访问条件不满足 | 不使用设备；关闭已打开的 fd |
| ALLOC / ATTACH HWPT | 所选页表与设备不兼容、请求能力不支持 | 尝试允许的兼容页表，或释放本次新建对象并退出 |
| MAP | IOVA 重叠、地址受限、后备内存不能固定、资源不足 | 保留既有有效映射；回收本次初始化中已建立的资源 |
| UNMAP / DESTROY | 范围不合法、对象仍被依赖 | 核对映射边界与引用，完成脱离或解除依赖后再释放 |

例如，本次创建了 IOAS 和 HWPT，附着成功，却因目标 IOVA 落入保留区而映射失败。这时设备尚未进入正常 DMA 阶段，回滚可以从脱离 HWPT 开始。另一种情况是在已有 IOAS 上新增一段 RAM 失败：应用必须保留原来仍供其他设备使用的映射，不能把整个共享地址空间当作本次操作的临时资源删除。

QEMU 对复用 HWPT 的失败也作区分：附着返回 `EINVAL` 时，按不兼容处理并继续尝试其他 HWPT；其他错误中止该路径。新建 HWPT 后附着失败，则销毁刚分配的 HWPT。清理边界由本次获得的资源及其共享关系决定。[HWPT 复用与失败分支](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/vfio/iommufd.c#L315-L379)

## 源码中的控制路径 {#implementation}

前面的三个动作在源码中有清晰分工。BIND 和 ATTACH 发给 VFIO 设备 fd；ALLOC、MAP、UNMAP 和 DESTROY 发给 IOMMUFD fd。

```text
iommufd_cdev_attach()
  iommufd_cdev_connect_and_bind()
    VFIO_DEVICE_BIND_IOMMUFD       → DEVICE
  iommufd_backend_alloc_ioas()
    IOMMU_IOAS_ALLOC               → IOAS
  iommufd_cdev_attach_container()
    iommufd_cdev_autodomains_get()
      复用兼容 HWPT，或 IOMMU_HWPT_ALLOC
      VFIO_DEVICE_ATTACH_IOMMUFD_PT
  vfio_listener_register()
    RAM 区域通知 → iommufd_backend_map_dma()
      IOMMU_IOAS_MAP
```

在 Linux 侧，VFIO 先从 fd 找到 IOMMUFD 上下文，再把设备操作交给具体 VFIO 驱动。IOMMUFD 的 IOAS 映射入口则验证用户参数，进入后备页和 I/O 页表管理。设备访问权限、对象引用以及映射失败回滚，都在这些边界上检查；用户态得到的成功返回值对应已经建立的关系，不能只看对象 ID 是否非零。

| 源码入口 | 重点观察 |
| --- | --- |
| [`iommufd_cdev_connect_and_bind()`](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/vfio/iommufd.c#L88-L139) | 两个 fd 的连接、设备 ID 及失败回滚 |
| [`iommufd_cdev_autodomains_get()`](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/vfio/iommufd.c#L302-L400) | HWPT 复用、能力查询与显式分配 |
| [`iommufd_backend_map_dma()`](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/backends/iommufd.c#L199-L229) | IOVA、HVA、长度与设备读写权限 |
| [`vfio_df_ioctl_attach_pt()`](https://github.com/torvalds/linux/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/drivers/vfio/device_cdev.c#L194-L250) | VFIO 设备接口到 IOMMUFD 页表关联 |
| [`iommufd_ioas_map()`](https://github.com/torvalds/linux/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/drivers/iommu/iommufd/ioas.c#L250-L284) | 映射参数校验与用户后备页管理 |

QEMU 还提供以文件描述符和偏移描述后备内存的 `IOMMU_IOAS_MAP_FILE` 分支。它改变内存来源的表达方式，IOVA、IOAS 与 HWPT 的关系保持一致。[文件后备映射](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/backends/iommufd.c#L232-L267)

## 从 VFIO Container 到独立的页表管理 {#evolution}

传统 VFIO Type1 路径以 group 和 container 组织 DMA：group 加入 container，应用在 container fd 上配置映射，再获得设备访问。它能满足常规直通，并支持兼容设备共享映射；地址空间接口与 VFIO 的 container 模型紧密关联。

IOMMUFD 把通用 I/O 页表管理独立出来，用对象 ID 明确表达“逻辑地址空间”“具体硬件页表”和“设备附着”。QEMU 在 [2023 年引入 IOMMUFD 后端](https://github.com/qemu/qemu/commit/5ee3dc7af7859e7b8aa34c10c21778101c15e812)，保留公共 VFIO 内存监听逻辑，再由不同后端完成映射。这使设备访问接口和页表能力能够分别演进。

| 需求 | IOMMUFD 的组织方式 | 仍需付出的成本或满足的条件 |
| --- | --- | --- |
| 多设备共享 RAM | 一个 IOAS 关联兼容 HWPT；必要时同步多个 HWPT | 多个硬件页表仍需维护；所有设备的地址限制要相容 |
| 控制硬件页表属性 | 显式分配 HWPT，并查询设备所处 IOMMU 能力 | 分配与附着可能失败，应用需处理对象依赖和回滚 |
| 嵌套 DMA 转换 | 用嵌套 HWPT 与父页表表达两阶段关系 | 依赖硬件、驱动与 VMM 配套，不能给普通 HWPT 任意添加一层 |
| DMA 脏页跟踪 | 支持的 HWPT 可启停跟踪并读取位图 | 能力按 IOMMU / 驱动判断，设备状态迁移仍需另行支持 |

QEMU 10.2 已在 `hw/vfio/iommufd.c` 查询 `IOMMU_HW_CAP_DIRTY_TRACKING`，具备能力时请求 `IOMMU_HWPT_ALLOC_DIRTY_TRACKING`，并实现跟踪启停及位图读取。它并不代表所有平台都提供该能力，也不代表任何直通设备都能热迁移。设备状态保存、队列停止、DMA 一致性以及目标端兼容性仍需共同满足。[能力门控与 HWPT 分配](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/vfio/iommufd.c#L348-L399)、[脏页跟踪操作](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/backends/iommufd.c#L337-L387)

对最初的网卡 VF，正常运行的数据路径依旧是设备通过 IOMMU 访问 RAM。IOMMUFD 的主要收益体现在地址空间表达、能力扩展与资源管理上。评估时应分别测量接入与大内存映射耗时、运行期 I/O 性能、动态映射更新和回收成本，不能仅凭更换接口就推导吞吐提升。

## 参考资料 {#sources}

- [Linux 6.18：IOMMUFD 用户 API](https://docs.kernel.org/6.18/userspace-api/iommufd.html)：IOAS、HWPT、设备绑定及对象生命周期。
- [Linux：VFIO 设备与隔离组](https://github.com/torvalds/linux/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/Documentation/driver-api/vfio.rst)：cdev 接口、DMA 所有权及 group 隔离。
- [QEMU：VFIO IOMMUFD 实现](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/vfio/iommufd.c)与 [IOMMUFD ioctl 后端](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/backends/iommufd.c)。
- [Linux：IOAS 实现](https://github.com/torvalds/linux/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/drivers/iommu/iommufd/ioas.c)与 [VFIO 设备 cdev](https://github.com/torvalds/linux/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/drivers/vfio/device_cdev.c)。
- [VFIO 设备直通与 DMA 地址转换](qemu-vfio-dma.html)：结合缓冲区地址与访问权限观察完整 DMA 请求。
