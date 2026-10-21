## 在虚拟机中运行 Hypervisor {#preface}

嵌套虚拟化允许一台虚拟机继续运行 Hypervisor，并创建自己的虚拟机。在 Intel VMX 路径中，物理宿主上的 KVM 称为 L0；运行在客户机中的 Hypervisor 称为 L1；由 L1 管理的客户机称为 L2。它常用于虚拟化开发测试、云主机内的实验环境，以及需要虚拟化隔离的客户机软件。

L1 要向 L2 提供 CPU、内存与设备，同时受 L0 的资源与隔离规则约束。KVM 因此需要虚拟化 VMX 本身：向 L1 提供 VMX 指令和控制结构的语义，再把 L1 的运行要求转换成物理 CPU 可以执行的配置。

本文采用 Intel VMX，沿一个简化的 L1 Hypervisor 展开：它设置 L2 的初始指令地址和 I/O 拦截位，执行 `VMLAUNCH`；L2 随后执行一条 `OUT 0xe0, AL`。观察同一条指令在不同拦截配置下的处理路径，可以同时理解 VMCS 合成、真实 VM-Exit 和嵌套退出转发。端口 `0xe0` 是演示用的通知端口，写入值为 `0x41`，并不对应某个标准硬件设备。

```baselines
```

## 三个软件层次与三份控制结构 {#levels}

VMX 在处理器上区分 root 与 non-root 操作模式。L0 的 KVM 控制真实的 VMX root；L1 和 L2 在各自运行时，都使用真实的 VMX non-root。L1 从自己的视角拥有虚拟的 VMX root，可以管理 L2，但没有取得物理宿主的控制权。L0、L1、L2 表示软件管理层次，不能依次理解为三个硬件特权级；每个客户机内部仍有自己的用户态和内核态。

一次硬件 VM-entry 读取一份 VMCS。其中的 guest-state 保存即将运行的客户机状态，host-state 指定退出后恢复的宿主状态，控制字段规定事件注入、拦截和地址转换。嵌套执行把这些职责分配给三份结构：

| 结构 | 由谁准备 | guest-state 与 host-state | 用途 |
| --- | --- | --- | --- |
| VMCS01 | L0 | 客户机是 L1，真实退出回到 L0 | 物理 CPU 运行 L1 |
| VMCS12 | L1 通过虚拟 VMX 接口设置，L0 保存其表示 | 客户机是 L2，虚拟宿主是 L1 | 表达 L1 希望怎样运行和接管 L2 |
| VMCS02 | L0 按 L1 要求和自身约束构造 | 客户机是 L2，真实退出仍回到 L0 | 物理 CPU 直接运行 L2 |

VMCS12 在 L1 的接口上是一份 VMCS 区域；L1 通过 `VMREAD`、`VMWRITE` 访问字段，不能依赖 KVM 内部结构的字节布局。Linux 用 `struct vmcs12` 保存和缓存它的内容。VMCS02 则是 L0 实际装载到处理器上的控制结构。VMCS12 的 host-state 需要在转发退出时解释为 L1 的恢复状态，不能直接成为真实处理器的宿主入口。[VMCS02 的宿主状态初始化](https://github.com/torvalds/linux/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/arch/x86/kvm/vmx/nested.c#L2285-L2346)

```diagram nested-vmx-controls
VMCS12 表达请求，VMCS02 承担实际执行
实线表示物理 CPU 使用的控制结构；虚线表示 L0 的状态合成与退出同步。VMCS01 和 VMCS02 在同一 vCPU 上切换使用。
```

## 从 VMLAUNCH 到 L2 的第一条指令 {#entry}

L1 先通过 `VMXON` 进入虚拟的 VMX 操作，使用 `VMCLEAR`、`VMPTRLD` 选择控制结构，再设置 L2 的 RIP、RSP、控制寄存器及执行控制。下面把这些准备视为已经完成，L2 的初始 RIP 为 `0x1000`，该处放置两字节指令 `OUT 0xe0, AL`。

L1 执行 `VMLAUNCH` 时，真实 CPU 退出到 L0。KVM 的 `nested_vmx_run()` 检查虚拟 VMX 权限、VMCS 启动状态和控制字段；检查通过后，进入 `nested_vmx_enter_non_root_mode()`，切换到 VMCS02，准备 L2 的状态与内存上下文。随后，外层 vCPU 运行循环执行真实 VM-entry，处理器开始执行 L2。[嵌套进入入口](https://github.com/torvalds/linux/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/arch/x86/kvm/vmx/nested.c#L3795-L3886)

### 控制字段按语义合成

VMCS02 必须保留 L0 的隔离约束，也要实现 L1 对 L2 承诺的行为。不同字段采用不同的处理方式：

| 字段或状态 | VMCS02 中的处理 | 原因 |
| --- | --- | --- |
| L2 的寄存器与执行状态 | 从 VMCS12 等嵌套状态中装入 | 恢复 L1 指定的 L2 执行上下文 |
| 真实 host-state | 由 L0 设置 | 任意真实退出都要先交回物理宿主 |
| I/O 端口访问 | Linux 6.18 对 L2 强制 I/O 退出，关闭 VMCS02 的 I/O bitmap 使用 | 退出后再判断该访问由 L1 还是 L0 处理 |
| 部分异常和控制寄存器拦截 | 合并两层要求，并处理掩码及读影子 | L1 放行某事件时，L0 仍可能必须拦截 |
| EPT、TSC 和部分扩展控制 | 转换或合成相应状态；不支持直接执行的能力由软件模拟 | 地址和时间均处在嵌套上下文中 |

以 I/O 为例，`prepare_vmcs02_early()` 设置 `CPU_BASED_UNCOND_IO_EXITING`，清除 `CPU_BASED_USE_IO_BITMAPS`。因此，即使 L1 的 I/O bitmap 放行了 `0xe0`，L2 的这条指令仍会真实退出到 L0。L1 的 bitmap 用于稍后的退出归属判断。其他控制位有只采用 L1 设置、按条件清除或单独计算的分支，不能把整份 VMCS 的构造概括成一次按位或。[执行控制的合成](https://github.com/torvalds/linux/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/arch/x86/kvm/vmx/nested.c#L2375-L2482)

### 进入失败保留原有边界

`VMLAUNCH` 和 `VMRESUME` 对 VMCS 启动状态有不同要求。对已启动的 VMCS 再执行 `VMLAUNCH`，会得到 VM 指令失败；非法控制字段也在检查时拒绝。另一些无效客户机状态按照 VM-entry 失败语义返回，不能简单当作一次正常的 L2 I/O 退出。只有完成进入检查，才会有后文的指令执行和退出处理。

## 一次 I/O 退出的两条处理路径 {#exit-path}

L2 执行 `OUT 0xe0, AL` 时，处理器根据 VMCS02 退出到 L0，并记录 I/O 退出原因、端口、宽度和指令长度。该指令尚未完成，L2 的 RIP 仍指向 `0x1000`。KVM 接着调用 `nested_vmx_reflect_vmexit()` 判断是否向 L1 转发：先识别必须由 L0 处理的事件，再判断 L1 的拦截要求。宿主中断和真实 EPT 缺页等有专门规则，不能把每次 L2 退出都原样传给 L1。[退出分派](https://github.com/torvalds/linux/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/arch/x86/kvm/vmx/nested.c#L6748-L6798)

### L1 拦截该端口

本例启用 VMCS12 的 I/O bitmap，并把 `0xe0` 对应位设为 1。KVM 根据退出信息检查 bitmap，确认 L1 要接管这次访问。随后把 L2 的执行状态同步到 VMCS12，填入 I/O 退出原因、端口方向等限定信息和指令长度，切回 VMCS01，并将 VMCS12 的虚拟 host-state 装成 L1 的恢复状态。

真实 CPU 再次 VM-entry 后，运行的是 L1 的退出处理程序。L1 通过 VMREAD 读取退出原因；示例中的小型 Hypervisor 直接处理这次通知，并把 VMCS12 的 L2 RIP 从 `0x1000` 改为 `0x1002`。L1 执行 `VMRESUME`，再次进入 L0 的嵌套进入路径，更新 VMCS02 后继续运行 L2。L1 也可以把设备模拟交给自己的用户态 VMM；这取决于 L1 的实现。

### L1 放行该端口

把 VMCS12 中同一位改为 0，硬件退出仍然发生，但 KVM 不向 L1 合成这次 I/O 退出，继续沿 L0 的普通 I/O 路径处理。为展示内核即可完成的情况，演示预先在外层 VM 注册一个匹配 `port = 0xe0`、`len = 1`、`datamatch = 0x41` 的 PIO ioeventfd。

L0 KVM 的 PIO 总线找到匹配项，在内核中通知 eventfd。写指令完成后，L2 的当前 RIP 前移两字节，仍使用 VMCS02 返回 L2。此处的 eventfd 通知完成表示门铃写已接收，后端实际工作可以稍后执行。VMCS12 没有因此出现新的 I/O 退出记录，L1 的退出处理程序也不运行。

若没有匹配的内核 I/O 处理对象，L0 可以通过 `KVM_EXIT_IO` 返回外层 QEMU，再由用户态模拟设备。对本例端口，外层 vCPU 线程再次调用 `KVM_RUN` 时，内核执行完成回调，推进 L2 RIP。这与向 L1 转发退出是两条独立路径。**硬件 VM-Exit、向 L1 转发嵌套退出、向外层 QEMU 返回 KVM_RUN，是三个不同的边界。**[PIO 内核与用户态分支](https://github.com/torvalds/linux/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/arch/x86/kvm/x86.c#L8381-L8432)、[OUT 完成回调与 RIP 更新](https://github.com/torvalds/linux/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/arch/x86/kvm/x86.c#L9604-L9640)、[ioeventfd 匹配与通知](https://github.com/torvalds/linux/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/virt/kvm/eventfd.c#L762-L814)

```diagram nested-vmx-exit-path
同一条 OUT 指令的退出归属
两种配置都先发生 L2 到 L0 的硬件退出。向 L1 转发时切换 VMCS，并恢复 L1 的虚拟宿主状态；留在 L0 处理时可经内核后端完成，也可按需交给外层 QEMU。
```

```component _nested_vmx_lab.html
```

## 嵌套地址转换与执行状态同步 {#memory}

运行 L2 还需要处理地址的层次。L2 页表将 GVA 转换为 L2 GPA；L1 提供的 EPT12 将 L2 GPA 转换为 L1 GPA；L0 再把 L1 GPA 对应到宿主内存。L0 为实际执行维护有效的转换，常用 EPT02 表示最终的 L2 GPA 到 HPA 映射。客户机页表本身也放在嵌套内存中，页表遍历同样受这些转换与权限约束。

```text
L2 页表       L1 的 EPT12        L0 的内存后备
GVA → L2 GPA →          L1 GPA →            HPA
      └──────── L0 维护有效 EPT02 ─────────┘
```

因此，L2 的一次 EPT violation 首先交给 L0 的 MMU 路径。如果只是 L0 尚未建立对应有效映射，KVM 完成映射后可以继续 L2；如果检查发现 L1 的 EPT12 缺少映射或权限不允许，则需要向 L1 呈现相应嵌套 EPT 退出。地址转换的缺失发生在哪一层，决定了后续处理者。`nested_vmx_l0_wants_exit()` 和 `nested_ept_inject_page_fault()` 体现了这一区分。[嵌套 EPT 缺页处理](https://github.com/torvalds/linux/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/arch/x86/kvm/vmx/nested.c#L409-L486)、[EPT 退出的 L0 分派](https://github.com/torvalds/linux/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/arch/x86/kvm/vmx/nested.c#L6558-L6578)

控制结构也采用按边界同步的方式。L2 正在运行时，VMCS02 和 vCPU 的运行状态可能比 VMCS12 的缓存更新；向 L1 转发退出、迁移或访问相应字段时，KVM 再同步必要内容。前面放行 I/O 的分支中，L2 已经继续执行，VMCS12 没有立刻得到一个新 RIP 快照，这符合它尚未向 L1 退出的状态。

## 源码中的进入、分派与恢复 {#implementation}

把前面的路径映射回代码，可以围绕四个边界阅读，避免在大段字段赋值中失去主线。

```text
L1: VMLAUNCH / VMRESUME
  nested_vmx_run()                  检查虚拟 VMX 状态与进入条件
    nested_vmx_enter_non_root_mode()
      vmx_switch_vmcs(vmcs02)
      prepare_vmcs02_early() / prepare_vmcs02()
  外层 vCPU 运行循环 → 真实 VM-entry → L2

L2: OUT → 真实 VM-Exit → L0
  nested_vmx_reflect_vmexit()
    nested_vmx_l0_wants_exit()      L0 必须处理的事件
    nested_vmx_l1_wants_exit()      L1 的拦截要求
      nested_vmx_exit_handled_io()  本例读取 VMCS12 的 I/O bitmap
    ├─ 转发：__nested_vmx_vmexit()
    │    同步 L2 状态与退出信息 → 切回 VMCS01 → 恢复 L1
    └─ 不转发：L0 普通 I/O 处理
         内核设备 / ioeventfd，或 KVM_EXIT_IO 返回外层 QEMU
```

| 阅读位置 | 对应的状态变化 |
| --- | --- |
| [`prepare_vmcs02_early()`](https://github.com/torvalds/linux/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/arch/x86/kvm/vmx/nested.c#L2375-L2482) | 合成执行控制，确定真实 CPU 将拦截哪些事件 |
| [`nested_vmx_enter_non_root_mode()`](https://github.com/torvalds/linux/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/arch/x86/kvm/vmx/nested.c#L3631-L3725) | 从 VMCS01 切向 VMCS02，准备 L2 运行上下文 |
| [`nested_vmx_check_io_bitmaps()`](https://github.com/torvalds/linux/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/arch/x86/kvm/vmx/nested.c#L6293-L6347) | 检查访问覆盖的端口位，决定 L1 是否接管 I/O |
| [`__nested_vmx_vmexit()`](https://github.com/torvalds/linux/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/arch/x86/kvm/vmx/nested.c#L5098-L5250) | 保存 L2、填充 VMCS12、切回 VMCS01 并恢复 L1 |
| [`sync_vmcs02_to_vmcs12()`](https://github.com/torvalds/linux/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/arch/x86/kvm/vmx/nested.c#L4675-L4766) | 把实际运行后的寄存器和必要执行状态带回 VMCS12 |

这里的 `enter_guest_mode()`、`is_guest_mode()` 属于 KVM 的嵌套状态标记。它们在这条路径上表示当前逻辑上运行 L2，不能单凭函数名判断处理器此刻已经处于 VMX non-root；准备这些状态的代码本身仍在 L0 执行。

## 从软件合成到减少嵌套开销 {#evolution}

2010 年的 [The Turtles Project](https://www.usenix.org/conference/osdi10/turtles-project-design-and-implementation-nested-virtualization) 系统讨论了在 Intel x86 上运行未修改的客户机 Hypervisor。其重要思路是组合多层的控制要求与地址转换，让 L2 的普通指令直接执行，同时在必要边界维护 L1 应当看到的虚拟机行为。此后嵌套 VMX 的优化逐步覆盖控制结构访问、状态同步、地址转换和迁移。

| 优化方向 | 减少的开销 | 仍需处理的约束 |
| --- | --- | --- |
| 软件模拟 VMX 与 VMCS 合成 | 为 L1 提供可运行的 VMX 接口，L2 普通指令使用硬件执行 | 嵌套进入、退出和状态转换有额外处理 |
| 硬件 VMCS shadowing | 允许部分 VMREAD / VMWRITE 访问影子 VMCS，减少 L1 到 L0 的退出 | 进入 L2 前仍需检查、同步与构造 VMCS02 |
| Enlightened VMCS 等协作接口 | 用共享格式及 clean-field 信息减少不必要的字段读取和同步 | 需要双方支持约定，能力与兼容性要共同协商 |
| 嵌套 EPT 和映射缓存 | 复用已经建立的有效地址转换 | 页表修改、失效和权限变化仍需跨层协调 |
| 嵌套状态保存接口 | 保存 L1 的 VMX 上下文及当前 L2 状态，支持恢复 | CPU 能力、VMX 控制和目标内核支持必须兼容 |

VMCS shadowing 加速的是 L1 访问控制字段的路径；VMCS02 仍负责实际运行 L2。Linux 在进入时根据配置从 shadow VMCS 或 enlightened VMCS 更新 VMCS12，随后完成合成；两者的角色不应混用。[进入时的同步](https://github.com/torvalds/linux/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/arch/x86/kvm/vmx/nested.c#L3832-L3842)、[影子 VMCS 关联](https://github.com/torvalds/linux/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/arch/x86/kvm/vmx/nested.c#L5917-L5930)

迁移同样不能只保存外层 RAM 和普通寄存器。QEMU 通过 `KVM_GET_NESTED_STATE` 取得嵌套状态，在恢复时通过 `KVM_SET_NESTED_STATE` 交还内核；KVM 再重建本机执行所需的状态。真实 VMCS02 属于当前宿主的运行资源，不应直接当作可跨机器搬运的硬件内存镜像。[QEMU 嵌套状态保存与恢复](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/target/i386/kvm/kvm.c#L5222-L5294)

回到最初的一次 `OUT`：L1 拦截时，增加了 L2 状态交接、恢复 L1、L1 处理和再次嵌套进入；L1 放行时，L0 仍要按自身设备模型完成访问。评价性能应区分 L2 纯计算、频繁 I/O、嵌套缺页与 L1 控制操作，分别观察退出频率、处理路径、内存缺页和延迟。减少 VMREAD / VMWRITE 的退出，并不能消除设备模拟或页表更新的成本。嵌套虚拟化提供了部署与测试上的灵活性，其性能取舍取决于负载实际跨越哪些边界。

## 参考资料 {#sources}

- [Linux 6.18：Nested VMX](https://docs.kernel.org/6.18/virt/kvm/x86/nested-vmx.html)：嵌套层次与 VMCS01 / VMCS12 / VMCS02 的术语。
- [Linux：嵌套客户机的运行与限制](https://docs.kernel.org/virt/kvm/x86/running-nested-guests.html)：适用场景、能力配置及迁移注意事项。
- [Linux 6.18：嵌套 VMX 实现](https://github.com/torvalds/linux/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/arch/x86/kvm/vmx/nested.c)与 [VMCS12 定义](https://github.com/torvalds/linux/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/arch/x86/kvm/vmx/vmcs12.h)。
- [OSDI 2010：The Turtles Project](https://www.usenix.org/conference/osdi10/turtles-project-design-and-implementation-nested-virtualization)：嵌套虚拟化的设计、实现与性能研究。
- [KVM vCPU 的运行与退出处理](kvm-vcpu-run.html)、[EPT 缺页与客户机内存映射](kvm-ept-fault.html)：外层执行循环与内存转换基础。
