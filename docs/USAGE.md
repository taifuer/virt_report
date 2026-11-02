# 使用指南

以下命令均在项目根目录执行，并使用项目虚拟环境。

## 安装与配置

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
cp .env.example .env
```

在 `.env` 中填写 `DEEPSEEK_API_KEY`。`GITLAB_TOKEN` 为可选项；生产环境还应设置随机长字符串 `METRICS_ACCESS_KEY`。数据源、模型、站点地址和调度时间由 `config.yaml` 管理。

## 采集与检查

```bash
# 采集近 3 天并重建线程
.venv/bin/virt-report fetch --since-days 3

# 从指定本地日期回填；max-pages 控制分页来源的上限
.venv/bin/virt-report fetch --since 2026-04-01 --max-pages 20

# 查看来源覆盖范围和最近采集状态
.venv/bin/virt-report status

# 回填较早的 KVM public-inbox epoch
.venv/bin/virt-report backfill-kvm --since 2025-01-01
```

较长回填建议分段执行并在每段后检查 `status`。qemu-devel 首次触及新月份时可能下载完整 mbox，KVM 历史回填也可能产生较大网络流量。

采集退出码为 `0`（完整成功）、`3`（部分成功或分页未完）和 `1`（失败）。调度器会重试尚未完成的来源；手动续跑同一批任务可重复使用 `--batch-key <唯一批次名>`。不要在不同回填窗口重复使用同一个批次名。

## 生成报告

```bash
# 默认生成前一个完整自然日、自然周或自然月
.venv/bin/virt-report daily
.venv/bin/virt-report weekly
.venv/bin/virt-report monthly

# 生成指定周期
.venv/bin/virt-report daily 2026-07-12
.venv/bin/virt-report weekly 2026-W28
.venv/bin/virt-report monthly 2026-07

# 复用数据库，不在生成前采集
.venv/bin/virt-report daily 2026-07-12 --no-fetch

# AI 失败时不发布正式报告，适合自动任务
.venv/bin/virt-report daily --no-fetch --require-ai

# 来源明确有缺口时暂不调用 AI，退出码为 4，供任务稍后重试
.venv/bin/virt-report daily --no-fetch --require-ai --wait-for-coverage
```

不带 `--require-ai` 的手工命令可生成模板内容用于排查，但不会覆盖正常报告，也不能绕过证据校验失败。自动调度要求有效 AI 结果；采集完整且确实没有活动时，不调用模型，发布明确的零活动说明。

## Web、专题与静态快照

```bash
# 启动 SQLite 驱动的动态站点
.venv/bin/virt-report serve --host 127.0.0.1 --port 8090

# 重建离线专题快照和已点评内容搜索索引
.venv/bin/virt-report topics-refresh
.venv/bin/virt-report search-refresh

# 导出完整静态站点到 site/
.venv/bin/virt-report index

# 预览 Git 中的静态快照
.venv/bin/python scripts/preview_site.py
```

动态服务直接读取 SQLite，不要求每次报告生成后全量导出。只有执行 `index`，或设置 `schedule.auto_export: true`，才会刷新完整 `site/` 快照。

纯静态预览不支持内容搜索，搜索页会明确提示。升级专题分类或条目链接规则后，执行一次 `topics-refresh` 和 `search-refresh` 更新派生索引；不需要重新调用 AI。导出前确认数据库含有要保留的全部报告，不要用较旧的本地数据库覆盖较新的静态快照。

预览脚本默认监听 `127.0.0.1:8091`，禁用浏览器缓存，避免修改公共导航后仍显示旧 HTML；可用 `--port` 指定其他端口。它不启动采集或调用 AI。

若本地数据库较旧，仅需给现有快照更新共享导航、页脚、条目锚点、报告回链和静态资源，可执行 `.venv/bin/python scripts/refresh_static_presentation.py site`。此脚本不读取数据库、不改报告正文，也不代替完整数据导出或专题分类重建。

## 原理文章维护

原理内容按以下四组编排；`mechanisms.html` 在介绍下展示已发布内容的入口，正文沿用相同顺序：

| 分组 | 专题顺序 |
| --- | --- |
| 基础机制 | 虚拟化基础 → CPU 虚拟化 → 内存虚拟化 → 设备与 I/O 虚拟化 → 中断虚拟化 |
| 关键操作 | 虚拟机启动 → 热迁移 → 热插拔 → 热升级 |
| 内部机制 | QEMU 内部机制 → KVM 内部机制 → Libvirt 内部机制 |
| 交互演示 | 磁盘 I/O 读写过程 → 网络 I/O 收发过程 → CPU 指令执行 → 内存访问过程 → 中断传递 → GPU 命令执行 → virtio-gpu 渲染与编解码 |

目录只显示已有正文的专题及其主归属篇数，未发布的专题和分组不对读者展示。普通主题保留标题、简介和导读入口，细分文章默认收起，通过紧邻标题的独立箭头按钮展开；按钮不重复显示篇数，无障碍说明保留动作与主题名称。只有一篇的主题及交互演示不增加折叠。顶部目录、直接访问锚点和前进后退会展开对应主题，旧锚点继续可用。关闭 JavaScript 时仍显示完整文章列表。各文章 URL 保持稳定，交叉阅读放在导读正文，不在目录增加附加行或重复计数。

所有专题标题直接链接对应导读或覆盖该专题的主文章，介绍基本分工、具体例子、演进与取舍，再指向深入机制文章；列表不重复展示入口文章。基础部分为 `virtualization-basics.html`、`cpu-virtualization.html`、`memory-virtualization.html`、`device-virtualization.html`、`interrupt-virtualization.html`；操作部分为 `libvirt-domain-start.html`、`migration.html`、`hotplug.html`、`qemu-cpr-transfer.html`；内部机制为 `qemu-internals.html`、`kvm-vcpu-run.html`、`libvirt-internals.html`。

重复导读已整合到对应主文章：`vm-startup.html` → `libvirt-domain-start.html`，`live-upgrade.html` → `qemu-cpr-transfer.html`，`kvm-internals.html` → `kvm-vcpu-run.html`。动态服务返回 308，静态导出保留跳转页面和无脚本入口；旧章节锚点在目标文章中保留，不重复计入文章数量。

当前文章都位于 `mechanisms/`：

- CPU（`#cpu-virtualization`）：`kvm-vcpu-halt.html`、`kvm-virtual-time.html`、`qemu-tcg.html`、`cpu-model-migration.html`，等待与唤醒、虚拟时间、TCG，以及 CPU 模型的能力与迁移约束；KVM 执行循环归入内部机制专题。
- 内存（`#memory-virtualization`）：`kvm-ept-fault.html`、`kvm-hugepages.html`、`kvm-dirty-ring.html`、`vcpu-numa-placement.html`，地址映射、大页、脏页记录，以及客户机拓扑、线程与 RAM 的物理放置。
- 设备与 I/O（`#device-virtualization`）：`virtio-device-initialization.html`、`qemu-network-tx.html`、`virtio-net-rx.html`、`packed-virtqueue.html`、`qemu-vhost-net.html`、`dpdk-vhost-user-net.html`、`spdk-vhost-user-blk.html`、`vhost-vdpa.html`、`qemu-vfio-dma.html`、`qemu-virtio-blk.html`，从特性协商、队列与双向收发进入软件/硬件后端，解释 DMA、缓冲区所有权、迁移限制与持久化边界。
- 中断（`#interrupt-virtualization`）：`kvm-virtio-interrupt.html`，队列完成、通知判定、MSI-X 路由与 KVM 中断注入。
- 启动（`#vm-startup`）：`libvirt-domain-start.html`、`qemu-vm-startup.html`，从管理请求、进程握手到 QEMU 机器初始化和 vCPU 执行。
- 热迁移（`#migration`）：`qemu-memory-migration.html`、`qemu-migration-stream.html`、`qemu-multifd.html`、`qemu-postcopy.html`、`libvirt-live-migration.html`，内存迭代、流格式、多通道、后拷贝，以及管理层五阶段协调与失败恢复。
- 热插拔（`#hotplug`）：`qemu-pcie-hotplug.html`、`qemu-virtio-mem.html`、`x86-vcpu-hotplug.html`，分别跟踪 PCIe、内存和 vCPU 的添加、客户机协作及移除完成。
- 热升级（`#live-upgrade`）：`qemu-cpr-transfer.html`，同机更新中的共享 RAM、文件描述符与执行权交接。
- QEMU 内部机制（`#qemu-internals`）：`qemu-qom-device.html`、`qemu-memory-region.html`、`qemu-aio-iothread.html`、`qemu-qcow2.html`、`qemu-live-block-operations.html`，对象生命周期、内存初始化与地址空间、异步 I/O、镜像映射，以及外部快照与在线块图变更。
- KVM 内部机制（`#kvm-internals`）：`kvm-vcpu-run.html`、`kvm-vcpu-requests.html`，执行循环与 I/O 退出，以及 request 位图、vCPU 模式与 kick 协作；区分内核请求、宿主 IPI、客户机中断及 QEMU 信号。
- Libvirt 内部机制（`#libvirt-internals`）：`libvirt-rpc.html`，XDR 报文、服务端分发、响应匹配与守护进程边界。

设备接口、后端和 DMA 属于同一条 I/O 路径，合并在一个专题内阅读。原 `#io-virtualization` 锚点仍定位到合并后的专题，文章 URL 不变。

内存初始化文章以 QEMU 10.2 / Q35 / 8 GiB RAM 为主线，串起宿主后备、RAMBlock、高低端别名、FlatView 与 KVM 注册，配有初始化时序图和地址布局图。64 KiB 交互后置为独立的局部映射示例，支持别名偏移、优先级、窗口启停和地址查询，不代表实际机器布局。virtio-mem 交互用 512 MiB 聚合格子展示目标、实际提供与客户机管理容量，支持回收阻塞、BUSY、回退与重置。两者均保留无 JavaScript 说明，全屏时移动原 DOM，退出后状态保持。

新增交互：halt 时间线可调整事件、轮询预算和调度等待；大页模型展示 2 MiB EPT 叶子与 512 个小页叶子的转换；qcow2 模型比较 L1/L2 查找、两类引用计数、backing 回退和局部写入。SPDK 文章按 25.09 固定提交跟踪本地 NVMe 路径，采用离线架构图与时序图，不需要读者安装 SPDK 或访问外部图表服务。

TCG 交互跟踪翻译块的生成、命中、跳转链接与代码改写后的失效；与 KVM 文章对照 CPU 执行路径。公共 Mermaid 图默认不超过原始尺寸，长图等比缩小；细节可在全屏查看器中缩放。

目前 19 个专题入口包含 53 篇原理文章与 7 个交互演示，另保留 3 个旧链接跳转页。内容覆盖 CPU 模型与迁移兼容性、嵌套 VMX、vCPU 热插拔、NUMA 放置、Libvirt 迁移协调、VFIO 设备迁移、virtio-net 接收与 NAPI、Packed virtqueue、vhost-vDPA、IOMMU 与 IOMMUFD、vIOMMU、virtio-gpu，以及外部快照与在线块操作；GPU 演示另区分 VFIO PCI 直通与限定历史实现的 mdev。

`virtio-gpu-rendering.html` 归入交互演示，提供 OpenGL 绘制、H.264 解码和 NV12 编码三个离线场景，可播放、单步、回退、重置与放大。命令包和图像资源随步骤变化，流程总览、关键结构、源码路径共用状态；下方保留详细说明与四张流程图。固定 QEMU 10.2、Linux 6.18、Mesa 25.0、virglrenderer 1.2，明确区分已接通的渲染主线与需要额外初始化的 VirGL video 扩展；不把普通 QEMU GL 配置当成视频加速支持，也不把 DMA-BUF、fence 与最终显示混为一谈。

`mechanisms/virtio-blk-read.html` 保留原 URL，页面名为“磁盘 I/O 读写过程”，独立归入交互演示，不再计入设备基础文章。读取与写入分别从 Guest 的 `pread()` / `pwrite()` 出发，固定一次 4 KiB 直接 I/O。流程总览显示系统调用、共享队列、后端处理、中断递送与应用返回；源码路径展示关键函数及异步边界。队列机制单独保留 8 个步骤：准备描述符、发布请求、设备取链、读写缓冲区、写回响应、填写完成项、发布完成、回收描述符。该视图以描述符回收为终点，不再逐步展开系统调用与中断注入；阶段导航和计数采用队列自己的进度。三种视图共用完整状态轨迹，队列视图按里程碑取快照，不修改底层因果顺序。支持单步、播放和回退，URL 保留步骤、视图和 `operation=write`。

队列视图初始显示“准备前”，三个描述符未填写、索引为零，不计入 8 个操作步骤。请求占用使用中性背景，仅本步操作高亮；回收后的旧环项仍保留并淡化，避免误解为回收必须清空内存。

默认的本地 virtio-blk 场景固定 Linux 6.18 / QEMU 10.2、split ring、ioeventfd / irqfd、raw 文件与 `aio=threads`；一次应用调用形成一个 bio / 块请求是配置假设，不推广到普通文件或任意 I/O。Guest 直接 I/O 与宿主 `cache.direct=off` 分开说明。VMX 软件中断路径明确区分 irqfd、MSI 路由、LAPIC pending、成功 VM-entry 注入与 Guest 回调，未启用 APICv / posted interrupt。此场景读数据描述符带 WRITE，写数据描述符不带 WRITE，成功 used.len 分别为 4097 / 1；普通写的完成不保证持久化。动画不代表实测时延；无 JavaScript 时保留默认本地读取的阶段说明，切换后端、操作与完成要求需要 JavaScript。

演示顶部选择设备接口与后端：本地 virtio-blk、QEMU virtio-scsi、QEMU librbd、SPDK vhost-blk 与 vhost-scsi；五种路径均可选择读取或写入，共十个独立场景。顶部统一显示一段流程概述，初始化细节放入“当前场景配置与源码版本”。切换场景会更新步骤、队列布局、执行上下文及源码路径，并回到当前视图的起点；切换视图时按已有进度定位对应步骤或队列里程碑。URL 的 `backend` 参数支持恢复选择。SPDK 固定轮询与 NVMe 路径，QEMU 只承担控制面；Ceph 展示到 QEMU / librbd 的异步接口与完成回调边界，不模拟集群内部。演示与 SPDK 专文互相链接；设备初始化、Packed 队列及中断实现继续由对应原理文章说明。

选择“写入”后，在同一播放器中选择“写入完成要求”，URL 以 `completion=ordinary|flush|fua` 表示。普通模式默认省略该参数；本地 virtio-blk 支持普通写与写后 FLUSH，本地 virtio-scsi 支持普通写与 FUA，RBD/SPDK 暂不提供未经核验的同步组合。`disk-completion-model.js` 在既有轨迹上添加完成条件及缓存保证，不再在正文下方放第二套持久化播放器。旧 `#persistence` 章节保留接口契约说明。

FLUSH 模式在普通写 R1 返回后显式发起刷新请求 F1，两个请求各自发布与回收，不能把刷新画成所有 WRITE 都自带的一步。FUA 模式从已构造的 `REQ_FUA` 块请求 R1 开始，经 SCSI CDB 和 QEMU 后端同步，到上层块请求完成为止；它不是完整的 `O_DSYNC` 用户态调用轨迹，Linux 在调用尾部仍可能执行额外同步。普通写、Guest O_DIRECT、宿主 direct I/O、后端同步和稳定存储保证分别解释，不把持久化等同原子性。

离线场景以 `virt_report/content/flows/virtio-blk-read.json` 为基础，`operationVariants.write` 保存本地写路径差异；`qemu-io-variants.json` 与 `spdk-io-variants.json` 保存其他后端已核验的读写差异。Python 在导出时构建完整场景目录，模型 `getScenario(base, operation, backend, catalog)` 返回独立副本。逐步状态模型与界面分别为 `virtio-blk-read-model.js`、`virtio-blk-read.js`。`callPaths` 按执行上下文组织固定源码链接，不能把通知、工作线程、协程恢复或完成回调串成跨上下文的普通函数调用链。修改场景须同时核验函数、缓冲区权限、发布顺序与各实现的完成长度；展示由静态数据驱动，不调用 LLM 或读取本机源码仓库。需要检查浏览器交互时，先启动本地服务，再使用已安装的 Playwright：

```bash
node scripts/check_virtio_blk_read_browser.cjs http://127.0.0.1:8090
node scripts/check_disk_completion_browser.cjs http://127.0.0.1:8090
node scripts/check_execution_demos_browser.cjs http://127.0.0.1:8090
node scripts/check_virtio_gpu_rendering_browser.cjs http://127.0.0.1:8090
node scripts/check_walkthrough_style_browser.cjs http://127.0.0.1:8090
node scripts/check_walkthrough_density_browser.cjs http://127.0.0.1:8090
node scripts/check_walkthrough_wide_layout_browser.cjs http://127.0.0.1:8090
node scripts/check_source_presentation_browser.cjs http://127.0.0.1:8090
node scripts/check_mechanism_directory_browser.cjs http://127.0.0.1:8090
node scripts/check_inline_disclosures_browser.cjs http://127.0.0.1:8090
node scripts/check_back_top_and_badge_browser.cjs http://127.0.0.1:8090
# 非默认安装位置可通过 PLAYWRIGHT_MODULE 指定；VBR_SCREENSHOT_DIR 可保存检查截图。
```

其余六个演示与原理文章互链，均使用独立、可回退的离线场景：

- `network-io.html`：QEMU+TAP、内核 vhost-net+TAP、DPDK vhost-user+PMD、硬件 vDPA 各自收发，以及 QEMU RX 缓冲区不足和 TX 背压。Guest RAM 拆分描述符表、available ring、used ring 和报文缓冲区；字段视图显示 `addr/len/flags/next`、索引和访问权限，并将填写环项与发布索引分开。
- `memory-access.html`：RAM 缓存命中、页表遍历、Guest #PF、EPT 缺项、宿主后备页缺失和脏页写保护；MMIO 区分 QEMU 模拟读取、KVM 内核 LAPIC 读取、匹配 ioeventfd 的门铃写，以及可映射 VFIO BAR 的稳态直接访问。硬件 VM-exit 不等于 `KVM_RUN` 必须返回 QEMU，CPU 的 BAR 访问也不能与设备 DMA 的 IOMMU 路径混为一谈。
- `cpu-execution.html`：普通 ADD、Guest 系统调用、内核处理 CPUID 和返回 QEMU 的 MMIO 使用同一 vCPU 执行时间线，持续显示指令、寄存器、CPL、VMX 状态与 `KVM_RUN` 交接。普通指令不画成“物理 CPU 把请求交给 Guest”。HLT 与两个定时休眠场景继续区分 Guest 任务、vCPU 和宿主调度状态。
- `interrupt-delivery.html`：从设备写回数据、发布完成和通知条件检查开始，展示软件 MSI-X、irqfd、通知抑制、IF 屏蔽、MSI-X PBA，以及限定条件下的 APICv posted interrupt。队列完成、LAPIC pending、注入准备、Guest 接受与驱动消费分开；通知被抑制时通过后续轮询观察完成，不伪造必然发生的中断。
- `gpu-execution.html`：三个已限定实现的场景——VFIO PCI 直通 AMD Vega10 / SDMA 4.0 的 Guest RAM→VRAM 上传与 VRAM→Guest RAM 读回，以及旧代 Skylake GVT-g mdev 的 BLT 系统内存缓冲区复制。跟踪 command buffer、ring、GPU 映射、数据、fence 与应用等待；mdev 额外展示 ELSP 捕获、审核、影子上下文、宿主调度和虚拟事件。QEMU 控制面与稳态直接映射访问分开，闭源厂商内部不作推断，GVT-g 不冒充全部 mdev 的共同实现。
- `virtio-gpu-rendering.html`：通过 controlq 的 SUBMIT_3D 跟踪 VirGL 图形与视频命令，展示输入资源、宿主执行结果、fence 响应和应用消费。视频场景明确以 VMM 已接通 VirGL video 为前提。

它们共享 `execution-model.js`、`execution-demo.js` 和 `_execution_lab.html`，数据放在 `content/flows/` 对应 JSON。场景以 QEMU 10.2 / Linux 6.18 为基础，CPU、内存和中断选取 Intel VMX/EPT 主线；网络 DPDK 路径另固定 24.11。流程总览保留完整顺序，关键结构视图按 `focus` 取累计快照。网络的 `network_queue.py` 生成结构化 `queueState`，不再从中文说明中正则推断描述符和环状态；磁盘模型也提供 typed queue 快照，两者复用 `walkthrough-components.js` 的描述符、环与缓冲区组件，保留各自协议字段和布局。URL 的 `scenario`、`view`、`step` 可恢复选择；无 JavaScript 时保留全部场景的文字流程。放大查看优先使用浏览器全屏，不支持时使用页内覆盖层。`EXECUTION_SCREENSHOT_DIR` 可保存浏览器回归截图。

总览只保留跨层流程及描述符链、avail/used 索引与空闲数摘要，不叠放完整队列表和缓冲区卡片；完整字段集中于队列机制／关键结构视图，切换时保留进度。两个视图使用同一份状态快照。

当前步骤说明统一放在图形上方，桌面分列呈现标题、动作和边界，手机按阅读顺序堆叠；图形使用完整正文宽度，不再挤在右侧解释栏旁。说明不裁切、不重复堆放源码链接。源码集中于“源码路径”视图，按 `callPaths` 展示上下文、关键函数或指令和异步交接；函数节点使用横向箭头连接，空间不足时自然换行，不裁切函数名。节点只显示函数名，文件位置放在链接提示中，固定提交和准确行号保留在跳转地址中。箭头统一表示关键执行顺序，不重复显示“调用”等文字；已核验的关系保留在悬停提示和无障碍说明中。返回路径与通知、线程、回调入口按上下文说明。可选的 `flow`、`relations`、`entry` 元数据在导出时校验，不能依据正文推测。当前步骤的补充依据也留在该视图。`source.kind` 区分函数/汇编与规范、参考资料、硬件包格式，后者单独显示，不编号混入调用顺序。实现来源固定提交；硬件执行、规范约束及跨线程通知不能伪装为 C 函数调用链。

执行域使用显式 `component` 区分 Guest、KVM、QEMU、Host kernel 与物理硬件；KVM 是宿主内核子系统，独立呈现是职责划分，不是另一个操作系统。Guest 节点通过 `badge` 标注用户态或内核态。当前路径未展开或仅参与初始化的组件以 `nodes: []`、`note` 放在下方职责说明中，不虚构动画节点或必经连线；所有真实节点仍须恰好归属一个域或内存区。磁盘各后端同样补齐职责边界，例如本地读可命中宿主页缓存，SPDK 不走 QEMU 块层，Ceph 不冒充本地物理磁盘。

GPU 直通明确分开命令内存、门铃与完成：Guest AMDGPU 写 GTT 中的 ring / IB，再经预建 EPT 写可直接映射的 doorbell BAR；物理 GPU 自行取命令并进行数据传输，系统内存 DMA 使用宿主 IOMMU 映射。宿主设备绑定 `vfio-pci`，不由宿主 AMDGPU 逐命令调度；普通 MSI 经 VFIO eventfd、KVM pending 和 VM-entry 注入进入 Guest 驱动。GVT-g 则显式经过 KVM 捕获、QEMU VFIO region 与宿主 i915/GVT-g 审核调度，不能沿用整卡直通的快路径。

同一组件还嵌入 `libvirt-domain-start.html#lifecycle`，串起冷启动、暂停恢复、正常关机和强制停止，不新增主分类。对应数据为 `vm-lifecycle.json`，页面映射由 `execution_demos.DEMO_PAGES` 维护。生命周期内容固定 Libvirt 12.0.0、QEMU 10.2.0 与 Linux 6.18，区分命令返回、客户机状态、应用就绪与宿主资源回收。`disk-persistence.json` 保留独立核验资料，但磁盘页不再用 `persistence-scenario` 等参数维护第二个交互状态；读写与同步要求统一由主播放器选择。

七个演示共用 `walkthrough.css` 与 `walkthrough-fullscreen.js`，统一场景选择、视图切换、播放控制、进度和说明栏。总览和源码视图按 `execution-phases.json` 中的流程阶段导航，单步与播放保留阶段内全部操作；关键结构视图只停在结构相关的状态。当前操作、技术细节和磁盘缓冲区归属合成一段说明，图上的交接不再另起一行复述。配置与源码版本位于演示下方；放大入口位于选择区，退出后保留场景与步骤。执行域和共享内存布局由 `execution-layouts.json` 或场景内嵌的 `layout` 指定；CPU 指令场景使用专门时间线。连线使用 `walkthrough-routing.js` 避开结构，数据、通知、控制切换和地址映射采用不同图例。修改布局须保留实际归属，不能把页表、宿主后备页、硬件中断状态或 GPU VRAM 都标成 Guest RAM。样式回归覆盖 320–1440px、下拉箭头和两种全屏方式；新增 virtio-gpu 演示另检查命令与图像状态切换。

`execution-selectors.json` 将场景入口与技术状态分开维护，新 GPU 演示将对应 `selector` 内嵌在自己的场景文件中。网络按后端与发送／接收选择，仅在相应路径下展示背压或缓冲不足；内存先选 RAM / MMIO，再按具体处理路径分组；CPU、中断使用分组预设；GPU 先选 VFIO PCI 或 mdev，再选已实现的操作。每个场景必须恰好被收录一次，界面只提供已核验的组合。网络总览从一次 18 B UDP 载荷的应用收发开始，区分用户缓冲区、Guest 协议栈与队列，以及本地后端；关键结构视图不逐步展开系统调用。应用返回、队列完成和网络交付分别解释，不能用 TX used 表示远端收到，也不能把驱动补充 RX 空间画成等待应用读取之后才进行。

三篇交互分别展示 vIOMMU 的客户机页表、宿主映射与 IOTLB 同步，VFIO 迁移的源端/目的端设备状态与 DMA 脏页交接，以及嵌套 VMX 的 VMCS01/12/02 和退出处理归属。正文区分可组合能力与版本限制，例如固定 QEMU 10.2 路径不支持启用 vIOMMU 的 VFIO 迁移。

后续可聚焦 guest_memfd 的私有/共享内存转换、virtio-fs 的文件请求与缓存一致性，以及增量备份的 dirty bitmap 继承与恢复验证。ARM 暂缓；目录不加入尚未成文的入口。优先补足具体过程和失败边界，避免重复已有概述。

文章元数据在 `virt_report/content/mechanisms.json`。正文可以使用 `render/templates/` 中的 HTML/Jinja 模板，也可以使用 `content/articles/*.md`。元数据的 `source` 与 `template` 二选一，按内容与组件需求选择，无需统一迁移。两种格式最终都生成 `site/mechanisms/*.html`，不在浏览器中解析 Markdown。

Markdown 使用普通段落、列表、表格、链接及代码块。每个二级标题写成 `## DMA 地址转换 {#translation}`，名称与元数据 `sections` 一致，首节 ID 为 `preface`。三级标题不手写编号；需要保留既有锚点时可写 `### 缓存失效 {#stable-anchor}`，未指定时沿用自动子标题锚点。特殊代码块用于嵌入现有组件：

````markdown
## DMA 地址转换 {#preface}

正文使用 Markdown 编写。

```baselines
```

```diagram iommu-address-path
设备 DMA 的地址转换路径
图的说明文字，同时用于无障碍描述。
```

```component _iommu_lab.html
```
````

`baselines` 只在首节出现一次；`diagram` 名称须已注册，并生成对应离线 SVG；`component` 模板须列入该文章元数据的 `components`。Markdown 正文不执行 Jinja，也不直接解析 HTML，需要交互时复用独立的 HTML/CSS/JS 组件。普通代码块始终作为示例显示。新增渲染依赖通过 `.venv/bin/pip install -e .` 安装；旧文章无需批量转换，后续修订按实际需要选择格式，并保留 URL 和锚点。

共用阅读样式为 `mechanisms.css`，每篇的交互脚本与样式由元数据指定；纯正文与图表文章可省略 `script` / `stylesheet`，不加载空脚本。元数据 `groups` 定义分组，`topics[].group` 指定分组，文章 `topic` 指定主归属，`topics[].overview` 指定导读文章 slug。增加已审核文章后，会自动进入目录并显示篇数。内容绑定源码版本与提交，不随日报生成，不调用模型。

核验实现时读取指定 tag/commit，不切换或覆盖已有源码工作区。交互是明确标注假设的教学模型，不是实际迁移跟踪或性能预测。
内存迭代演示采用带单位的轮次近似，停机附加开销为明示的示例值，不等同于 QMP 测量结果；
内存迭代与迁移流演示显示源端、传输通道与目的端；传输动画到达后才更新接收状态，支持暂停、后退和重置。
右上角可打开全屏演示窗口，退出保留已完成步骤。内存默认 8 GiB，可选 4 / 8 / 16 GiB；带宽、脏页速率与停机目标可调，修改参数后从初始状态重新计算。
迁移流演示用具体 RAMBlock、页偏移和 section ID 展示字段编码，省略的数据明示为省略，不作为可导入快照或真实抓包。
`migration-wire-layout.svg` 是手工维护的字段布局图，与 Mermaid 时序图分开管理；修改标志或字段时需同时核对对应固定版本源码和回归测试。
字段布局图在正文内完整等比显示，点击后可适应窗口、适应宽度或手动缩放；公共查看器为 `mechanism-viewer.js` / `.css`，无 JavaScript 时仍可查看完整图与原始 SVG。
MultiFD 演示可改变通道数与慢通道，分别观察发送侧完成和目的端接收屏障；演示步数不表示实测时间。
网卡演示固定 QEMU 后端，切换设备接口并逐步观察描述符、通知、数据和完成处理。
virtio 模式默认显示环槽、描述符表、链链接和数据缓冲区的交互图，可局部步进或放大；圆形表示索引循环，实际环槽为连续数组。可选择起始索引 0 / 3 / 65535 检查环槽与 16 位计数器回绕；完成后旧环项保留，缓冲区由驱动回收。DPDK 文章固定 24.11 的普通同步复制路径，区分客户机 used、PMD 接受与硬件完成。
发送篇另含通知竞态步进演示：比较恢复通知后复查、错误地省略复查，以及新请求较晚到达的三种交错。Packed 队列允许分别发布、完成和回收，观察四槽环的 wrap、Buffer ID 与空闲额度；教学模型采用单描述符顺序完成，正文另解释乱序与多描述符链。HLT 的 perf 片段用于观察宿主调度，CPR 的 QMP 片段用于测试双进程交接；二者均不代表本站实测数据，不由网页执行。
vhost 演示切换后端，分别显示初始化控制、队列通知、报文数据与完成通知；Dirty Ring 演示允许逐页写入、收割和重置，检查重新保护与迁移读取的先后关系。
VFIO 演示显示映射请求字段与硬件页表关系，缺失或只读映射下的非法 DMA 不产生成功完成；后拷贝演示区分缺页事件、请求、页面安装和线程唤醒。
新增文章使用正式机制名称作为标题，版本信息放在开篇。先说明要解决的问题和解决思路，再用同一小例子串起架构、关键结构与源码过程，最后说明收益、代价、评价指标及演进方向，避免对话式说明和未经实测的性能结论。目录标题应能说明文章的实际内容，避免连续使用“基本原理”“关键源码”等泛称。
基础导读需要先解释读者必须理解的概念，不能从实现术语或调用链直接开始。用同一例子回答“问题是什么、最直接的办法为何不够、改进如何生效、成本转移到哪里”，再进入源码文章。发展演变按问题和技术层次组织：例如 PIC/APIC、MSI-X、irqfd、APICv 分别涉及控制器、设备通知、内核接口和硬件交付，不应画成彼此完全替代的单线历史。图中的地址、队列和状态应与正文例子一致；确切年份需有原始文档或提交依据。

各专题的入口文章均需单独审阅正文，不能以章节齐全或测试通过代替内容审查。组件介绍用 KVM 的设备读取、QEMU 的 4 KiB 磁盘读取、Libvirt 的暂停与恢复贯穿分层；操作介绍解释状态如何建立和交接，区分命令受理、操作完成与业务就绪。审阅时确认：基本概念先于结构名，例子能从输入走到结果，演进写明收益和剩余代价，图与正文一致，最终有评价方法和继续阅读路径。不得仅添加引言就标记为完整重写。文章开头统一显示“本文由 OpenAI Codex (GPT-6-Astra) 辅助编写。”。
`article_outline.py` 在服务端生成正文与目录的统一编号：一级“一、二、三”；同一章节有多个子标题时使用“1. 2. 3.”，只有一个时不加序号。保留旧 section 和子标题锚点；交互组件内部的标题不进入目录，也不计入编号数量。`sections` 的顺序和名称必须与正文一致，重复锚点或元数据不匹配会阻止渲染；关闭 JavaScript 时编号和目录仍可使用。纯排版不要改动已有锚点。
每篇需展示解释机制所必需的数据结构：关键字段、读写角色、对象关联与操作前后的变化，并区分本地对象和线格式、地址与数据、共享状态和私有游标。结构表与交互应使用一致的示例值；已有文章也按这一要求检查。
更新后运行 `pytest`；安装 Node 时会同时执行交互模型测试，也可单独运行：

```bash
node --test tests/test_*.cjs
```
仅导出原理页面及其资源，不重建历史报告：

```bash
.venv/bin/python -c 'from virt_report.config import load_config; from virt_report.render.render import render_mechanisms; render_mechanisms(load_config())'
```

完整 `index` 导出会一并包含原理页面。动态服务只读取已审核内容，不访问源码仓库或在线分析。

Mermaid 图源保存在 `virt_report/content/diagrams/*.mmd`，已生成的 SVG 随项目分发，
访问页面不加载外部 CDN，也不需要 Node。修改图源时使用 Mermaid CLI 11.12.0 运行
`.venv/bin/python scripts/render_mechanism_diagrams.py`，再导出原理页面。
脚本支持 `--mmdc` 指定工具路径、`--puppeteer-config` 指定本地浏览器，以及 `--diagram 图名` 仅重建指定图；这些只是编辑工具，不是部署依赖。生成时检查可访问性说明与本地资源约束，并保存固有尺寸供缩放使用；渲染或校验失败不会覆盖原图。
正文中的图表完整等比显示，点击“放大查看”后默认适应窗口，可切换适应宽度、100% 或逐级缩放；退出后回到原阅读位置。无 JavaScript 时仍能阅读图片、图源及打开原图。
对象关联用结构图，跨线程协作用时序图，操作边界用流程图或状态表；不要求每篇都有动画。图表应补充文字难以直观表达的关系，沿用正文中的地址、字段与示例，不重复堆叠同一流程。每份 `.mmd` 需要 `accTitle` 和 `accDescr`，并登记到 `MECHANISM_DIAGRAM_NAMES`；交互演示继续负责可操作的状态变化。

## 会议内容维护

```bash
# 核对配置过的官方录用名单，不等待 DBLP 收录；不调用模型、不自动发布
.venv/bin/virt-report conference-check --year 2026
# 也可限定来源，重复检查会列出相较上次新增的标题
.venv/bin/virt-report conference-check --venue sosp --year 2026

# 更新 KVM Forum 标题并生成年度主题分析
.venv/bin/virt-report kvm-forum

# 复用已保存的 KVM Forum 标题
.venv/bin/virt-report kvm-forum --no-fetch

# 拉取学术会议目录并列出待人工复核候选
.venv/bin/virt-report conference-catalog \
  --from-year 2010 --to-year 2026 --list-candidates

# DBLP 限流时按会议续跑
.venv/bin/virt-report conference-catalog \
  --venue vee --from-year 2010 --to-year 2026

# 补充公开摘要与已核验单位，并同步公开快照
.venv/bin/virt-report conference-catalog --no-fetch \
  --enrich-abstracts --abstract-limit 20
.venv/bin/virt-report conference-catalog --no-fetch \
  --enrich-affiliations --sync-public-metadata

# 缺少 DOI 时执行严格标题匹配；此操作较慢
.venv/bin/virt-report conference-catalog --no-fetch \
  --discover-dois --enrich-affiliations --sync-public-metadata
```

学术会议条目需要人工复核后才能进入公开内容；维护过程不读取论文 PDF。
`conference-check` 的来源在 `virt_report/content/conferences.json` 的 `edition_checks` 中配置，
核对报告保存在数据库同目录的 `conference-checks.json`（默认 `data/`，不提交）。
首次检查列出全部标题作为基线，后续列出新增标题和仍未收录的匹配候选；来源出错时保留上次成功数据并返回失败状态。
它不修改公开的“核对时间”，也不删除消失的论文。建议会议公布结果期间手动或按月执行；默认调度器不新增网络任务。

候选识别覆盖 KVM、QEMU、SEV-SNP、TDX 等技术名，并可参考目录中已有的公开摘要。
命中只是人工复核线索，不能把摘要背景中提到的技术自动视为研究对象。
DBLP 全量目录继续保存在 SQLite；官方名单检查作为提前发现新论文的补充，两者都不自动发布。
编辑完成后更新论文、年度点评及对应 `edition_checks` 的日期和说明，再运行 `index` 导出静态快照。
只有标题的已录用论文须标注 `publication_status: accepted` 和 `evidence_level: title`，不能推断机制、架构或性能数字。

KVM Forum 的会前预览独立保存在 `virt_report/content/kvm_forum_preview.json`，包含核对日期、公开议题链接与人工点评。
`kvm-forum` 命令仍用于历史标题和辅助分析，会调用配置中的模型；查看和导出页面不会调用模型，也不会覆盖独立预览。
会议结束后需人工复核最终议程，再将该届转为年度内容，不会仅凭日期自动更改“预览”状态。

论文页支持中英文关键词、单位与主题标签筛选，并与会议、年份、分页组合使用；URL 保留筛选状态。
专题的相关研究由 `topic_links` 手动精选，每类最多三篇，不参与社区条目数量或排序；版本链接只是同主题参考，不表示论文已落地。

## 版本时间线

```bash
# 核对官方发布记录，默认仅更新 data/ 中的版本快照
.venv/bin/virt-report versions-refresh --from-year 2003
# 使用离线数据显式导出 site/versions.html，不影响其他页面
.venv/bin/virt-report versions-refresh --no-fetch --export
# 维护者审核后更新仓库内公开基线，不重新联网
.venv/bin/virt-report versions-refresh --no-fetch --update-bundled
```

从关于页顶部“查看版本”进入版本时间线，主导航不增加“版本”项。
默认精简视图按年展示 QEMU 的功能版本，不只保留整数大版本；可切换 KVM、Libvirt 或全部项目，并按年份筛选。
“主题”依次提供设备直通 / VFIO、虚拟 I/O / virtio、热迁移筛选，可与项目、年份和详细视图分页组合。
选择“全部项目”可跨项目查看技术演变，例如
`versions.html?project=all&topic=vfio&view=detailed`。切换主题回到第一页；
版本直达链接会清除阻挡该版本显示的筛选并展开对应年份。
热迁移使用 `topic=migration`，涵盖数据传输、收敛、设备状态和兼容性改进，
不因提到迁移就纳入普通存储镜像、尚未支持的能力或 QEMU 在线升级。
各年份默认展开，精简视图可点击年份收起或展开；切换筛选或视图时重新展开，版本直达链接也会展开对应年份。
详细视图提供发布要点、来源链接及官方提供的提交数、贡献者数，支持
10／20／30 条分页，数量按功能版本计。两种视图均不列出维护补丁及 RC，
页面底部提供官方完整版本记录入口。KVM 按 Linux 版本标注；旧编号按各项目历史规则识别。
已采集的维护版本仍保留在离线数据中，不因页面精简而删除。

报告归档、论文、专题、搜索与版本详细视图使用同一分页组件。
改变筛选或每页数量回到第一页，翻页滚动到列表开头；URL 保留筛选和分页参数。

采集结果位于 `data/versions.json`，小型 HTTP 缓存位于 `data/versions/http/`。
`virt_report/content/versions.json` 提供可直接预览的公开基线；
`version_notes.json` 保存根据官方材料人工整理的中文要点。后续尚未整理的要点
保留官方原文或发布链接，不调用 AI 推断特性是否落入某个版本。
中文说明优先于自动提取的原文，采集刷新不会覆盖人工内容。每个版本以 2—4 条
实质变化为主，区分初步支持、正式支持和默认启用；KVM 仅归纳相关子系统变化。
历史补充可引用官方 Wiki、合并记录或发布标签下的历史文档，使用 `source_url`
记录要点依据，不改变版本本身的发布日期来源。缺少可靠依据时明确保留内容缺口。
`version_topics.json` 人工维护主题与版本的对应关系，只关联有中文要点及依据的功能版本，
不按关键词自动扩展，不将 VFIO 或 virtio 虚构为独立软件版本。增加成员后运行版本测试；
如需同时展示两个主题，可把同一版本加入两个列表，网页不会重复计数。
安装 Playwright 与 Chromium 后，可在静态预览运行期间执行
`node scripts/check_versions_browser.cjs http://127.0.0.1:8091/versions.html`，
检查组合筛选、分页、直达链接与移动端宽度；`PLAYWRIGHT_MODULE` 可指定已有安装路径。
网页只读快照；来源失败保留原记录，独立刷新不会重建全站。
默认 `schedule.auto_export: false` 时，定时刷新不写入 Git 跟踪的 `site/`；
启用 `auto_export` 或显式传入 `--export` 才导出版本页，`index` 仍可导出完整站点。
检查时间以 UTC 保存、按站点时区显示；官方发布日期保持原样。

## 备份与恢复

```bash
# 创建一致性的 gzip 快照并输出 SHA-256
.venv/bin/virt-report backup data/backups/virt-report.db.gz

# 创建自动快照，成功后只保留这一份（日期改为当天）
.venv/bin/virt-report backup data/backups/auto-2026-09-20.db.gz --keep-count 1

# 停止 Web 和调度器后恢复；原数据库会先自动备份
.venv/bin/virt-report restore data/backups/virt-report.db.gz \
  --sha256 <摘要> --force
```

不要直接复制正在写入的 SQLite 文件。默认每天自动备份，仅保留最新一份已校验快照；
手工备份不参与自动清理。`--keep-days` 仍可用于旧的按天保留方式，不能与 `--keep-count` 同用。
生产部署的完整操作见[部署与运维](DEPLOYMENT.md)。

## 开发检查

```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/python -m pyflakes virt_report tests
```
