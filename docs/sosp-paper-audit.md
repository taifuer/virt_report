# SOSP 2025 / 2026 论文目录复核（2026-11-19）

对照 [SOSP 2025 录用名单](https://sigops.org/s/conferences/sosp/2025/accepted.html)（66 个标题）与 [SOSP 2026 录用名单](https://sigops.org/s/conferences/sosp/2026/accepted.html)（62 个标题）逐项初筛，再核对候选的正式议程、DOI 和技术材料。范围是这两届的录用研究论文，不含海报、workshop 或其他会议的整届普查；逐项核对名单不等于读完 128 篇全文。

### 此前没有公开收录的 SOSP 论文

| 年份 | 论文与依据 | 论文解决的问题 |
| --- | --- | --- |
| 2025 | [Demeter · 作者机构摘要](https://research.cuhk.edu.hk/en/publications/demeter-a-scalable-and-elastic-tiered-memory-solution-for-virtual/) | Guest 负责选择冷热页，宿主仍控制各层容量；方案依赖两侧配合。 |
| 2025 | [Device-Assisted Live Migration of RDMA Devices · 论文](https://doi.org/10.1145/3731569.3764795) | 直通网卡状态不在 Guest RAM 内；作者给出设备迁移接口与 ConnectX 实现。[NVIDIA 文档](https://networking-docs.nvidia.com/doca/archive/3-5-0/sr-iov-live-migration)可对照产品条件，不能代替论文的实验。 |
| 2025 | [Dandelion · 作者 PDF，§6.2](https://anakli.inf.ethz.ch/papers/dandelion_sosp25.pdf) | 新函数复用 microVM 结构，省下引导通用客户机内核的时间。应用需要适配函数接口。 |
| 2025 | [Tai Chi · 作者 PDF，框架目标与图 1](https://dibang2008.github.io/files/SOSP2025-0917.pdf) | SmartNIC 在卡上的 vCPU 运行控制面，收到 I/O 时把物理核让回给数据面。 |
| 2026 | [You Can Always Get What You Want: CPU Virtualization Made Fast and Free · 论文](https://doi.org/10.1145/3830418.3843895) | 恢复旧条目。DDVM 把空闲核借出去，也处理原 vCPU 被抢占后的中断等待。[作者摘要的索引记录](https://api.openalex.org/works/W7214709955) |
| 2026 | [bpf_fault · 作者实现](https://github.com/bpf-fault/bpf-fault) | Firecracker 做在线快照时，宿主内核就地处理部分缺页，省去一次用户态往返。 |
| 2026 | [VoliStorM · 作者摘要](https://www.janadoing.science/publications) | 硬件虚拟化隔开两个内核，独立的 I/O 内核管理缓存和写入顺序。[实验制品](https://zenodo.org/records/22132413) |

本站收录本轮 SOSP 2025 的四篇、SOSP 2026 的三篇。其中六篇首次收录，DDVM 一篇恢复旧条目。七篇都保留为普通论文。

编辑随后撤下了两篇已核过原文的论文：讨论 Android pKVM 测试规格的 *Ghost in the Android Shell*，以及介绍 RISC-V 固件监控器 Miralis 的 *The Design and Implementation of a Virtual Firmware Monitor*。筛选记录保留原 ID、来源和移除原因。

### 同批恢复的 ATC 论文

2026-11-10 筛选时，五篇只有已核实的录用标题；其正式出版日期为 2026-11-14。此次把 [ATC 正式议程](https://atc.sigops.org/2026/schedule.html)、出版 DOI 与对应的作者摘要逐一匹配，沿用五个原 ID。下表的「摘要」链接为 OpenAlex 索引的作者摘要，不是独立评测或全文复现。

| 论文 | 恢复依据 |
| --- | --- |
| [FM²](https://doi.org/10.1145/3828523.3857062) | 迁移前在共享 CXL 池维护 Guest 内存副本，并非消除了所有内存复制。[摘要](https://api.openalex.org/works/W7229651790) · [制品](https://zenodo.org/records/23147401) |
| [PAVE](https://doi.org/10.1145/3828523.3857031) | 为嵌套 microVM 预建二级地址翻译；同源快照的只读映射与私有写入分开。[摘要](https://api.openalex.org/works/W7230136763) |
| [Joseph](https://doi.org/10.1145/3828523.3857060) | 从 RDMA 后端直接读入 Guest 目标页，等数据到位后再安装 EPT 映射。[摘要](https://api.openalex.org/works/W7230039808) |
| [Chameleon](https://doi.org/10.1145/3828523.3857025) | 分离式虚机内存的回收粒度与二级地址翻译失效开销有直接取舍。[摘要](https://api.openalex.org/works/W7229991617) · [制品](https://zenodo.org/records/23093805) |
| [Courier](https://doi.org/10.1145/3828523.3857111) | 题名中的安全容器实际由嵌套 microVM 承载；影子 IOMMU 与队列路径是收录依据。[摘要](https://api.openalex.org/works/W7227419253) |

### 容易误收的条目

逐篇决定及来源保存在 [筛选记录](../virt_report/content/paper_screening.json)。本轮特别记录六个未收录的近似条目：

- [Oasis](https://www.yuhong-zhong.com/papers/Oasis_SOSP25.pdf) 的容器 NIC 原型借鉴 VirtIO；[Coyote v2](https://arxiv.org/abs/2504.21538) 的 vFPGA 是 FPGA 内应用分区。原论文演示的接口与从 Guest 交接直通设备不同。
- [μFork](https://arxiv.org/abs/2509.09439) 复制的是单地址空间 OS 中的进程；[PhoenixOS](https://ipads.sjtu.edu.cn/_media/publications/phos-sosp25.pdf) 保存 GPU 应用状态。二者都不是此目录所讲的虚机快照。
- [NEX/DSim](https://dslab.epfl.ch/pubs/nex.pdf) 以原生执行配合加速器仿真；与 QEMU/KVM 做对照不等于贡献新的系统 VMM。[Skill VM](https://github.com/SJTU-IPADS/SkVM) 是语言运行时，不运行 Guest OS。

此前明确排除的七项仍未恢复：δSandbox、DVLA、SG-IOV、Kamino 与 DVM 的主要贡献不在本站所选的 Guest/VMM 边界；Working Set 与 Lottery Scheduling 是此前明确移除的内核条目，并非本轮证据不足的待选论文。
