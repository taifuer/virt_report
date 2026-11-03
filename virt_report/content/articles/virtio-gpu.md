## 从客户机像素到显示输出 {#preface}

虚拟机中的桌面程序把窗口画进客户机内存，QEMU 则需要把这些像素交给宿主的显示窗口或远程显示服务。virtio-gpu 用资源编号、内存列表和更新命令连接这两端：客户机管理自己的绘图缓冲区，设备端保存图像资源，scanout 选择其中一块区域作为显示输出。

本文沿着一幅 1024 × 768 画面的更新过程，分析 QEMU 的普通二维后端。先建立完整画面，再只修改其中一个 128 × 64 的矩形。这条路径包含资源创建、内存关联、像素传输、输出绑定和更新通知，也能说明三维加速后来改变了哪些环节。

```baselines
```

## 图像资源、后备内存与显示输出 {#resources}

一幅画面在这条路径中涉及三组对象。**客户机后备内存（backing）**保存客户机写入的像素；**资源（resource）**是设备通过 `resource_id` 管理的图像对象；**scanout**表示一个显示输出及其选中的资源区域。QEMU 普通二维后端给资源分配 pixman 图像，像素传输时把 backing 中的数据复制到这张图像中。

scanout 不要求再保存一份完整像素。QEMU 创建的 `DisplaySurface` 可以直接引用 pixman 图像的相应区域。后续 `RESOURCE_FLUSH` 告诉显示后端哪些区域更新了，显示后端再按自身方式处理窗口重绘或远程传输。[资源与输出结构](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/include/hw/virtio/virtio-gpu.h#L45)、[显示表面的建立](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/display/virtio-gpu.c#L655)。

```diagram virtio-gpu-resources
二维资源的内存与输出关系
命令通过 controlq 到达 QEMU；像素由 TRANSFER 从客户机 backing 复制到 pixman 图像。scanout 引用资源区域，FLUSH 提交显示更新，cursorq 单独处理光标。
```

图中的 controlq 和 cursorq 都是 virtqueue。controlq 承载创建资源、关联内存、传输和显示设置等命令；cursorq 承载光标图像选择与位置移动。光标单独排队，有助于避免其操作排在大量普通控制请求之后。不过，光标图像本身仍要先在 controlq 上准备好，再通知 cursorq 使用，跨队列依赖需要明确同步。

下面的例子使用资源 7、输出 0，像素格式为 `VIRTIO_GPU_FORMAT_B8G8R8X8_UNORM`，每像素 4 字节。其中 X 字节不表示透明度。画面每行占 4096 字节，总计 3 MiB。客户机 backing 可以由不连续的内存段组成，设备按内存列表的顺序把它们视为一段逻辑字节序列。

## 一幅画面的建立与局部更新 {#update-flow}

### 建立资源和初始画面

客户机先发出 `RESOURCE_CREATE_2D`，提供资源编号、格式和尺寸。QEMU 分配 `virtio_gpu_simple_resource`，建立 pixman 图像，保存到资源列表。此时图像对象已经存在，客户机尚未把 backing 告诉设备，初始像素也还没有传过来。

`RESOURCE_ATTACH_BACKING` 随后提供一组 `virtio_gpu_mem_entry`。QEMU 用设备的 DMA 地址空间解析这些地址，建立 `res->iov`。普通、未使用客户机 IOMMU 的配置下，列表中的地址对应 GPA；启用相关地址转换功能时，应按设备可访问的 DMA 地址理解。关联 backing 建立访问关系，不会在这一操作中复制整幅图像。

本例接着传输完整的 1024 × 768 画面，再通过 `SET_SCANOUT` 把输出 0 关联到资源 7 的完整区域，最后发送 `RESOURCE_FLUSH`。先填好图像再接入显示可以避免展示未准备好的内容。资源和输出建立后，后续更新不必重复 CREATE、ATTACH 和 SET_SCANOUT。[创建资源](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/display/virtio-gpu.c#L242)、[关联 backing](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/display/virtio-gpu.c#L909)。

### 更新一个 128 × 64 的矩形

现在客户机只修改 `(x=128, y=96)` 开始的矩形。写入 backing 后，设备端 pixman 图像仍保留旧像素。客户机提交 `TRANSFER_TO_HOST_2D`，带上资源编号、矩形和 backing 中的起始偏移：

```text
画面：1024 × 768，每像素 4 字节
行跨度 stride = 1024 × 4 = 4096 B
矩形 r = { x: 128, y: 96, width: 128, height: 64 }
offset = 96 × 4096 + 128 × 4 = 393728 B
每行复制 = 128 × 4 = 512 B
本次有效像素 = 512 × 64 = 32768 B = 32 KiB
```

QEMU 对这个窄于整幅画面的矩形逐行复制。第 `h` 行的源偏移是 `offset + stride × h`，目的偏移是 `(y + h) × stride + x × 4`。因此每行跳过的未更新像素不会被误当成连续的矩形数据；`offset` 是 backing 字节流内的偏移，`r.x/r.y` 则指定资源图像中的位置。[实际复制循环](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/display/virtio-gpu.c#L433)。

传输完成后，客户机对同一区域发出 `RESOURCE_FLUSH`。QEMU 查找引用资源 7 的输出，把更新矩形与各输出的可见区域求交，再调用 `dpy_gfx_update()`。如果 scanout 只显示资源的一个子区域，坐标还要转换为该输出内部的坐标。[区域更新](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/display/virtio-gpu.c#L558)。

```diagram virtio-gpu-update-sequence
从初始画面到局部更新的命令顺序
初始化阶段建立 backing、资源与输出的关系；运行阶段重复客户机写像素、TRANSFER 和 FLUSH。图中只展开最后一条命令的带 fence 响应，显示端消费更新独立发生。
```

## 资源更新的交互演示 {#interactive}

依次点击下一步，可以观察客户机 backing、QEMU 图像与显示后端的变化。初始化完成后，矩形先出现在客户机内存中，再经 TRANSFER 进入 QEMU 图像，最后通过 FLUSH 通知显示端。可以切换更新区域大小，对比有效像素量。

```component _virtio_gpu_lab.html
```

演示把显示后端消费更新单独列成一步，以便区分命令完成与画面呈现。真实的 scanout 表面可以与 QEMU 图像共享像素；右侧画面表示显示端最近处理的更新，不表示 QEMU 又分配了第三份 framebuffer，也不模拟显示器的刷新时刻。

## 控制命令与 QEMU 处理路径 {#commands}

### 描述符链承载命令和响应

驱动把命令头和参数放在设备可读的描述符中，把响应缓冲区放在设备可写的描述符中，再发布到 controlq。ATTACH 命令后跟内存段列表，像素则留在列表描述的 backing 中。普通 TRANSFER 命令只携带资源、矩形和偏移，不需要把整幅图像塞进命令队列。

| 结构 | 关键字段 | 本例中的含义 |
| --- | --- | --- |
| `virtio_gpu_ctrl_hdr`，24 B | `type`、`flags`、`fence_id`、`ctx_id`、`ring_idx` | 标识命令及同步信息；二维基础命令不使用三维渲染上下文 |
| `virtio_gpu_rect`，16 B | `x`、`y`、`width`、`height` | 资源内的矩形坐标 |
| `virtio_gpu_mem_entry`，16 B | `addr`、`length`、padding | 一段客户机后备内存；列表按顺序组成 backing |
| `virtio_gpu_transfer_to_host_2d`，56 B | 命令头、矩形、`offset`、`resource_id`、padding | 从 backing 偏移 393728 B 读取资源 7 的更新像素 |
| QEMU `virtio_gpu_simple_resource` | `resource_id`、`iov`、`image`、`scanout_bitmask` | 联系客户机内存、设备端图像与引用它的输出 |

协议中的多字节整数字段采用小端序。`resource_id` 是客户机与设备之间使用的对象编号，与像素的地址和 virtqueue 描述符编号各自独立。[协议结构定义](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/include/standard-headers/linux/virtio_gpu.h#L137)。

### 从队列通知到更新显示

QEMU 收到 controlq 通知后，`virtio_gpu_handle_ctrl_cb()` 调度 bottom half；`virtio_gpu_handle_ctrl()` 取出描述符链并加入内部 `cmdq`；`virtio_gpu_process_cmdq()` 再调用所选后端的处理函数。普通二维后端按命令类型分派到创建资源、复制像素和刷新区域等函数。这条软件路径由 QEMU 执行，不要求客户机取得一块物理 GPU 的所有权。

请求处理结束后，QEMU 把响应写入描述符链中的可写部分，发布 used 记录，并通过 virtio 通知机制提示驱动。错误响应同样需要检查：不存在的资源编号、缺失 backing、越界矩形或内存分配失败，都不能作为正常画面更新继续处理。[队列入口与分派](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/display/virtio-gpu.c#L1021)、[响应与 used 发布](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/display/virtio-gpu.c#L160)。

## 完成语义与资源回收 {#completion}

virtio-gpu 允许控制命令采用异步处理。驱动需要确认命令处理已经完成时，在命令头设置 `VIRTIO_GPU_FLAG_FENCE`；设备在满足该命令的完成条件后返回携带对应 `fence_id` 的响应。普通二维复制通常直接在 QEMU 软件处理路径内完成，而加速后端还可能等待渲染器完成操作。`fence_id` 用来对应请求和完成，不能仅凭普通 used 记录就推广出所有后端的渲染完成保证。[Virtio 1.2，第 5.7.6.5 节：命令生命周期与 fence](https://docs.oasis-open.org/virtio/virtio/v1.2/virtio-v1.2.html)。

这里需要区分三个进度：客户机的像素写入已经完成、设备对命令的处理已经完成、显示后端已经消费更新并最终呈现。FLUSH 的带 fence 响应也不能证明远程客户端已经收到图像，或物理显示器已经扫描到那一行。评估桌面延迟时，需要把这些阶段分别观察。

释放资源时同样要处理依赖关系。驱动先停止对资源的继续提交，并确保仍在使用 backing 的工作完成；输出可以用 `SET_SCANOUT(resource_id=0)` 关闭，或改绑其他资源。之后解除 backing、释放资源，并在相应操作完成后回收客户机内存。QEMU 的资源销毁函数会清理关联输出、pixman 引用和映射，驱动仍需遵守命令的完成顺序，不能在设备继续访问时复用 backing。[资源销毁](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/display/virtio-gpu.c#L388)、[映射清理](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/display/virtio-gpu.c#L881)。

## 从二维像素传输到三维加速 {#evolution}

二维资源路径把客户机绘图和宿主显示分开，命令少、状态关系清楚，适合普通控制台与基本桌面。但绘制工作仍在客户机软件栈中完成，更新画面还要经过像素复制。1024 × 768 的 32 位画面若每秒完整传输 60 次，仅有效像素量就约为 180 MiB/s；这还没有计入内存读写流量、队列处理和显示编码。局部更新把本例一次传输从 3 MiB 减到 32 KiB，适合变化区域较小的界面；全屏视频和复杂三维场景难以仅靠这种办法降本。

后续扩展分别针对渲染、资源共享和进程隔离展开：

| 路径或能力 | 主要变化 | 引入的条件与代价 |
| --- | --- | --- |
| 普通二维后端 | 客户机形成像素，QEMU 复制并通知显示 | 无需宿主三维渲染器；客户机软件绘制与像素复制仍占用 CPU 和带宽 |
| virgl / virglrenderer | 客户机发送图形命令表示，宿主渲染器调用图形 API | 依赖客户机 Mesa、协议能力和宿主渲染栈的兼容性；增加渲染上下文及同步状态 |
| Venus | 通过 virtio-gpu 承载 Vulkan 协议 | 需要相应 capset、上下文初始化及 host blob 等能力；不能由普通二维后端自动获得 |
| resource blob / host-visible memory | 扩展资源的内存表示与映射，服务共享和加速路径 | 需协商功能并满足后端、内核和内存映射条件；是否减少复制取决于具体路径 |
| rutabaga_gfx / gfxstream | 提供另一套渲染与跨域集成后端 | 按 capset 接入相应渲染、显示能力，与 virglrenderer 属于不同实现选择 |
| vhost-user-gpu | 把图形设备后端移到独立进程 | 改变进程边界和通信方式；渲染能力取决于实际后端 |

这些能力可以组合，优化目标也不同：三维命令处理减少客户机软件渲染负担，资源共享降低某些路径上的复制成本，独立后端进程调整隔离边界。无论选择哪种实现，都仍需维护资源的所有权、完成顺序和显示输出关系。[QEMU 10.2 的设备后端说明](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/docs/system/devices/virtio/virtio-gpu.rst#L18)、[Mesa Venus](https://docs.mesa3d.org/drivers/venus.html)。

## 总结与进一步阅读 {#summary}

普通二维 virtio-gpu 的主线可以归纳为：建立资源及 backing，选择输出，再反复执行像素写入、TRANSFER 和 FLUSH。资源编号解决对象引用，内存段列表解决 backing 的访问，矩形解决更新范围，fence 解决命令处理的同步。这几组关系清楚之后，便能沿着同一条路径分析画面未更新、局部内容错误或显示延迟等问题。

- [virtio 设备初始化](virtio-device-initialization.html)：功能协商、队列配置和设备状态。
- [virtio-gpu 渲染与编解码](virtio-gpu-rendering.html)：从应用命令到宿主执行，区分 VirGL 渲染、视频扩展和完成反馈。
- [virtio-gpu 渲染与编解码过程](virtio-gpu-execution.html)：单独切换绘制、解码与编码，逐步观察命令和资源。
- [QEMU 的 virtio-net 发送路径](qemu-network-tx.html)：描述符链、avail 与 used 的交接。
- [VFIO 设备直通与 DMA](qemu-vfio-dma.html)：物理设备分配与 DMA 地址转换，理解另一类设备使用方式。

## 参考资料 {#references}

- [Virtio 1.2，第 5.7 节 GPU Device](https://docs.oasis-open.org/virtio/virtio/v1.2/virtio-v1.2.html)：资源、输出、队列与控制命令。
- [QEMU 10.2：virtio-gpu.c](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/display/virtio-gpu.c#L242)：二维资源、数据复制、输出绑定与命令完成。
- [QEMU 10.2：virtio-gpu.h](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/include/hw/virtio/virtio-gpu.h#L45)：QEMU 内部资源和 scanout 结构。
- [QEMU 10.2：VirtIO GPU 设备文档](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/docs/system/devices/virtio/virtio-gpu.rst#L45)：二维、virgl、Venus 和 rutabaga 后端。
- [Mesa：Virtio-GPU Venus](https://docs.mesa3d.org/drivers/venus.html)：Vulkan 协议及渲染栈要求。
