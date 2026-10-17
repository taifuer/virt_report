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

原理内容按以下三组编排；`mechanisms.html` 在介绍下展示已有文章的专题入口，正文沿用相同顺序：

| 分组 | 专题顺序 |
| --- | --- |
| 基础机制 | 虚拟化基础 → CPU 虚拟化 → 内存虚拟化 → 设备与 I/O 虚拟化 → 中断虚拟化 |
| 关键操作 | 虚拟机启动 → 热迁移 → 热插拔 → 热升级 |
| 内部机制 | QEMU 内部机制 → KVM 内部机制 → Libvirt 内部机制 |

目录只显示已有正文的专题及其主归属篇数，未发布的专题和分组不对读者展示。各文章 URL 保持稳定，交叉阅读放在导读正文，不在目录增加附加行或重复计数。

所有专题标题直接链接对应导读，介绍基本分工、具体例子、演进与取舍，再指向深入机制文章；列表不重复展示导读。基础部分为 `virtualization-basics.html`、`cpu-virtualization.html`、`memory-virtualization.html`、`device-virtualization.html`、`interrupt-virtualization.html`；操作部分为 `vm-startup.html`、`migration.html`、`hotplug.html`、`live-upgrade.html`；内部机制为 `qemu-internals.html`、`kvm-internals.html`、`libvirt-internals.html`。

当前文章都位于 `mechanisms/`：

- CPU（`#cpu-virtualization`）：`kvm-vcpu-run.html`、`kvm-vcpu-halt.html`、`kvm-virtual-time.html`、`qemu-tcg.html`、`cpu-model-migration.html`，执行与退出、等待与唤醒、虚拟时间、TCG，以及 CPU 模型的能力与迁移约束。
- 内存（`#memory-virtualization`）：`kvm-ept-fault.html`、`kvm-hugepages.html`、`kvm-dirty-ring.html`、`vcpu-numa-placement.html`，地址映射、大页、脏页记录，以及客户机拓扑、线程与 RAM 的物理放置。
- 设备与 I/O（`#device-virtualization`）：`virtio-device-initialization.html`、`qemu-network-tx.html`、`virtio-net-rx.html`、`packed-virtqueue.html`、`qemu-vhost-net.html`、`dpdk-vhost-user-net.html`、`spdk-vhost-user-blk.html`、`vhost-vdpa.html`、`qemu-vfio-dma.html`、`qemu-virtio-blk.html`，从特性协商、队列与双向收发进入软件/硬件后端，解释 DMA、缓冲区所有权、迁移限制与持久化边界。
- 中断（`#interrupt-virtualization`）：`kvm-virtio-interrupt.html`，队列完成、通知判定、MSI-X 路由与 KVM 中断注入。
- 启动（`#vm-startup`）：`libvirt-domain-start.html`、`qemu-vm-startup.html`，从管理请求、进程握手到 QEMU 机器初始化和 vCPU 执行。
- 热迁移（`#migration`）：`qemu-memory-migration.html`、`qemu-migration-stream.html`、`qemu-multifd.html`、`qemu-postcopy.html`、`libvirt-live-migration.html`，内存迭代、流格式、多通道、后拷贝，以及管理层五阶段协调与失败恢复。
- 热插拔（`#hotplug`）：`qemu-pcie-hotplug.html`、`qemu-virtio-mem.html`、`x86-vcpu-hotplug.html`，分别跟踪 PCIe、内存和 vCPU 的添加、客户机协作及移除完成。
- 热升级（`#live-upgrade`）：`qemu-cpr-transfer.html`，同机更新中的共享 RAM、文件描述符与执行权交接。
- QEMU 内部机制（`#qemu-internals`）：`qemu-qom-device.html`、`qemu-memory-region.html`、`qemu-aio-iothread.html`、`qemu-qcow2.html`、`qemu-live-block-operations.html`，对象生命周期、地址空间、异步 I/O、镜像映射，以及外部快照与在线块图变更。
- KVM 内部机制（`#kvm-internals`）：`kvm-vcpu-requests.html`，request 位图、vCPU 模式与 kick 协作；区分内核请求、宿主 IPI、客户机中断及 QEMU 信号。
- Libvirt 内部机制（`#libvirt-internals`）：`libvirt-rpc.html`，XDR 报文、服务端分发、响应匹配与守护进程边界。

设备接口、后端和 DMA 属于同一条 I/O 路径，合并在一个专题内阅读。原 `#io-virtualization` 锚点仍定位到合并后的专题，文章 URL 不变。

MemoryRegion 交互使用 64 KiB 示例布局，支持别名偏移、优先级、窗口启停和地址查询。virtio-mem 交互用 512 MiB 聚合格子展示目标、实际提供与客户机管理容量，支持回收阻塞、BUSY、回退与重置。两者均保留无 JavaScript 说明，全屏时移动原 DOM，退出后状态保持。

新增交互：halt 时间线可调整事件、轮询预算和调度等待；大页模型展示 2 MiB EPT 叶子与 512 个小页叶子的转换；qcow2 模型比较 L1/L2 查找、两类引用计数、backing 回退和局部写入。SPDK 文章按 25.09 固定提交跟踪本地 NVMe 路径，采用离线架构图与时序图，不需要读者安装 SPDK 或访问外部图表服务。

TCG 交互跟踪翻译块的生成、命中、跳转链接与代码改写后的失效；与 KVM 文章对照 CPU 执行路径。公共 Mermaid 图默认不超过原始尺寸，长图等比缩小；细节可在全屏查看器中缩放。

目前包含 12 篇基础与组件导读、38 篇具体机制文章。新增内容包括 CPU 模型与迁移兼容性、vCPU 热插拔、NUMA 放置、Libvirt 迁移协调、virtio-net 接收与 NAPI、Packed virtqueue、vhost-vDPA，以及外部快照与在线块操作。

后续可聚焦增量备份的 dirty bitmap 继承与恢复验证、嵌套虚拟化的一次 VM Entry、块设备在线扩容、virtio-scsi 取消与复位，以及 Libvirt job / 锁的具体竞争。ARM 暂缓；目录不加入尚未成文的入口。迁移与更新选题应聚焦直通状态、后端重连或失败恢复等具体问题，避免重复现有预拷贝、迁移流与 CPR 文章。

文章元数据在 `virt_report/content/mechanisms.json`，正文和交互模板在 `render/templates/`，
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

12 篇导读均需单独审阅正文，不能以章节齐全或测试通过代替内容审查。组件导读用 KVM 的设备读取、QEMU 的 4 KiB 磁盘读取、Libvirt 的暂停与恢复贯穿分层；操作导读解释状态如何建立和交接，区分命令受理、操作完成与业务就绪。审阅时确认：基本概念先于结构名，例子能从输入走到结果，演进写明收益和剩余代价，图与正文一致，最终有评价方法和继续阅读路径。不得仅添加引言就标记为完整重写。
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
