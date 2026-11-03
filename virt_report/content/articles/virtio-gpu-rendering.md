## 图形与视频命令的执行路径 {#preface}

虚拟机中的图形程序调用 OpenGL 绘制画面，视频播放器把压缩码流还原成图像，录屏程序又把图像压缩为视频。这三类工作都可能使用宿主 GPU，但它们的输入、执行单元和完成条件各不相同。virtio-gpu 提供客户机与宿主之间的设备接口，具体的图形和视频命令由两端的软件栈共同解释。

本文以 Linux 客户机、x86/KVM 和 VirGL 为主线，先跟踪一个三角形的绘制，再分析一帧 H.264 的解码，以及一帧 NV12 图像的编码。主线包含应用调用、Mesa 编码、内核提交、QEMU 分派、宿主驱动执行，以及结果回收。基础二维资源的 CREATE、TRANSFER 和 FLUSH 过程见[二维资源与画面更新](virtio-gpu.html)。

```baselines
```

固定版本中的 **OpenGL 渲染路径已经接入 QEMU；VirGL 视频扩展还需要宿主程序额外接入**。virglrenderer 1.2.0 提供视频命令和 VA-API 后端，但 QEMU 10.2.0 的初始化没有启用该视频功能，也没有提供所需的 DRM fd 回调。下文将渲染的现有路径与视频扩展的执行路径分开，并在视频章节列出具体接入条件。

交互演示单独放在[virtio-gpu 渲染与编解码过程](virtio-gpu-execution.html)：可切换绘制、解码和编码，逐步观察命令与资源交接。本文保留协议、源码分析和技术演进，便于连续阅读。

## 两层命令与资源关联 {#transport}

| 工作 | 应用提供的内容 | 宿主侧执行接口 | 结果 |
| --- | --- | --- | --- |
| 三维渲染 | 顶点、纹理、着色器和绘制状态 | virglrenderer 调用 OpenGL，宿主图形驱动提交 GPU 工作 | 颜色、深度等图像资源 |
| 视频解码 | H.264 码流、图像参数和参考帧信息 | VirGL video 后端调用 VA-API | 解码表面，如 Y、UV 两个平面 |
| 视频编码 | 原始图像、编码参数和参考帧信息 | VirGL video 后端调用 VA-API | 压缩码流及实际编码长度 |

KVM 负责运行客户机 CPU 和递送虚拟设备事件。QEMU 负责虚拟设备、命令队列和显示输出。宿主渲染库及驱动负责把图形或视频操作转成硬件工作；使用软件渲染器时，对应计算也可能在宿主 CPU 上完成。

```diagram virtio-gpu-render-stack
应用、虚拟设备与宿主执行栈
图形和视频使用不同的宿主 API；两者可以共享 virtio-gpu 的命令传输与资源引用。视频分支以 VirGL video 已正确初始化为前提。
```

### Virtio 命令承载设备操作，VirGL 命令表达绘图工作

virtio-gpu 的 controlq 是一个请求与响应队列。创建渲染上下文、创建资源、关联 backing、提交三维命令都通过这个队列进入设备。cursorq 单独处理光标。视频扩展沿用 controlq 中的三维提交机制，不增加一条独立的“视频 virtqueue”。

一次三维提交有两层格式：外层 `VIRTIO_GPU_CMD_SUBMIT_3D` 指定上下文、命令缓冲区长度和同步信息；内层缓冲区保存一批 VirGL 命令，例如创建着色器、设置顶点缓冲区和 `VIRGL_CCMD_DRAW_VBO`。宿主先识别外层设备命令，再交给渲染器解释内层命令。

```text
controlq 描述符链
  设备可读：virtio_gpu_cmd_submit
      hdr.type = VIRTIO_GPU_CMD_SUBMIT_3D
      hdr.ctx_id = 客户机渲染上下文
      hdr.flags / fence_id = 本次提交的同步信息
      size = 后续命令流的字节数
  设备可读：VirGL 命令流
      [包头 | 状态参数] [包头 | DRAW_VBO 参数] ……
  设备可写：virtio_gpu_ctrl_hdr 响应
```

`virtio_gpu_ctrl_hdr` 为 24 字节，加上 `size` 和 padding，外层提交结构为 32 字节。VirGL 内层包头为一个 32 位字：低 8 位表示命令，接下来的 8 位表示对象类型，高 16 位表示后续参数的 dword 数。外层长度按字节计，内层长度按 32 位字计；解析器需要分别检查边界。[外层结构](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/include/standard-headers/linux/virtio_gpu.h)、[VirGL 命令格式](https://gitlab.freedesktop.org/virgl/virglrenderer/-/blob/500b41d5c8638f9b80dd558f4044f3301c7457a4/src/virgl_protocol.h)

### 命令引用对象，数据保存在资源中

一个绘制命令需要顶点和纹理，一个视频命令需要码流、帧参数和图像平面。这些数据通常存放在资源中，命令携带资源编号、偏移、范围或对象句柄。驱动负责在使用前使资源内容可见，并保持资源存活，直到相关工作完成。

| 对象或字段 | 所在位置 | 职责 |
| --- | --- | --- |
| Guest GEM handle | 客户机进程与 DRM 驱动 | 引用客户机的缓冲区对象 |
| `drm_virtgpu_execbuffer.command` | 客户机 ioctl 参数 | 指向待复制的客户机用户态命令流 |
| `bo_handles` | 客户机提交参数 | 列出本次访问的 GEM 对象，供内核处理资源依赖 |
| `ctx_id`、`resource_id` | virtio-gpu 协议 | 在设备侧找到渲染上下文和资源 |
| VirGL object handle | 渲染上下文中的对象表 | 引用着色器、视图、视频 codec 等对象 |
| 宿主 GL texture、VA surface | 宿主渲染库与驱动 | 保存实际渲染图像或编解码表面 |
| `fence_id` | 命令及其响应 | 将客户机等待者与设备完成事件关联起来 |

这些编号属于不同的对象空间。Guest 用户指针不会直接成为宿主指针，GEM handle 也不等于物理显存地址。资源共享可以减少某些数据搬运，但编号转换、生命周期管理和同步仍需保留。

## 一次 OpenGL 绘制的完整路径 {#render}

### 建立上下文和绘制资源

设客户机程序要在一个 1280 × 720 的颜色缓冲区中绘制三角形。应用先通过 EGL 或 GLX 获得 OpenGL 上下文，Mesa 选择 virgl 驱动。驱动查询设备能力和 capset，建立对应的渲染上下文，再创建顶点缓冲区、着色器及颜色目标。颜色目标的一份紧密排列 RGBA8 图像占 3,686,400 字节，约 3.52 MiB；底层资源还可能有对齐、分块或其他布局开销。

capset 描述渲染协议及能力，例如支持哪些格式、功能和限制。它与 Virtio feature bits 分工不同：后者协商设备传输与资源机制，前者决定渲染上下文能理解哪些操作。应用创建上下文和资源时就需要适配这些能力。

客户机 GLSL 着色器经过 Mesa 的编译表示和 VirGL 编码。在这里选取的 Mesa 25.0.0 VirGL 路径中，NIR 转换为 TGSI，再交给宿主渲染器重建 GLSL；宿主 OpenGL 驱动继续编译为适合物理 GPU 的程序。这个设计让客户机使用统一的虚拟图形接口，同时保留宿主驱动对具体硬件的控制。[Mesa VirGL shader 路径](https://gitlab.freedesktop.org/mesa/mesa/-/blob/4fa244fddfebb21378042556862e197284ef65ac/src/gallium/drivers/virgl/virgl_context.c)、[宿主 shader 处理](https://gitlab.freedesktop.org/virgl/virglrenderer/-/blob/500b41d5c8638f9b80dd558f4044f3301c7457a4/src/vrend/vrend_renderer.c)

### 从 glDrawArrays 到 controlq

应用绑定顶点、着色器和颜色目标后，调用 `glDrawArrays(GL_TRIANGLES, 0, 3)`。Mesa 完成状态检查，通过 Gallium 的绘制接口进入 `virgl_draw_vbo()`，由 `virgl_encoder_draw_vbo()` 把绘制参数编码进命令缓冲区。此前的状态设置也可以保存在同一批命令里。

`glDrawArrays()` 返回时，GPU 工作可能尚未提交。显式 flush、命令缓冲区容量限制或后续同步会促使 Mesa 调用 `virgl_flush_eq()`，经 `virgl_drm_winsys_submit_cmd()` 发出 `DRM_IOCTL_VIRTGPU_EXECBUFFER`。因此，一次 ioctl 可以包含多条绘制和状态命令，不能按 OpenGL 调用次数推算 virtqueue 请求数。[绘制编码](https://gitlab.freedesktop.org/mesa/mesa/-/blob/4fa244fddfebb21378042556862e197284ef65ac/src/gallium/drivers/virgl/virgl_encode.c)、[Guest DRM winsys 提交](https://gitlab.freedesktop.org/mesa/mesa/-/blob/4fa244fddfebb21378042556862e197284ef65ac/src/gallium/winsys/virgl/drm/virgl_drm_winsys.c)

Guest 内核的 `virtio_gpu_execbuffer_ioctl()` 校验参数、取得资源及输入同步依赖，再由 `virtio_gpu_cmd_submit()` 构造 `SUBMIT_3D`。命令和响应缓冲区组成描述符链，驱动发布到 controlq 并按通知条件提示设备。这里交接的是客户机准备好的命令流及资源引用；客户机 CPU 继续由 KVM 执行。[Guest 提交处理](https://github.com/torvalds/linux/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/drivers/gpu/drm/virtio/virtgpu_submit.c)、[controlq 命令构造](https://github.com/torvalds/linux/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/drivers/gpu/drm/virtio/virtgpu_vq.c)

提交与完成沿不同的执行上下文推进。提交 ioctl 返回软件提交结果，后续完成由 fence 跟踪；宿主在另一个执行上下文处理 Q1。应用可以在 Q1 执行期间准备或提交使用其他资源的下一批工作。访问同一资源的操作继续服从依赖关系，批次之间还受队列容量、驱动调度和应用同步方式约束。交互演示跟踪一帧到显示交接，实际应用可以让多个批次同时在途。

### 从 QEMU 分派到宿主 GPU

QEMU 取出 controlq 请求，VirGL 后端的 `virgl_cmd_submit_3d()` 检查长度并复制命令流，然后调用 `virgl_renderer_submit_cmd()`。renderer 根据上下文进入解码器；`vrend_decode_draw_vbo()` 解析绘制参数，`vrend_draw_vbo()` 选择对应的宿主 `glDrawArrays*()` 或 `glDrawElements*()` 调用。[QEMU 提交分派](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/display/virtio-gpu-virgl.c)、[VirGL 解码器](https://gitlab.freedesktop.org/virgl/virglrenderer/-/blob/500b41d5c8638f9b80dd558f4044f3301c7457a4/src/vrend/vrend_decode.c)

宿主 OpenGL 驱动管理实际 GPU 资源、编译后的着色器和硬件命令，必要时通过 DRM 驱动提交作业。GPU 按图形流水线处理顶点、光栅化并执行片元着色，把结果写入宿主颜色纹理。QEMU 和 VirGL 的软件函数在宿主 CPU 上运行；随后执行这些硬件命令的是 GPU。

```diagram virtio-gpu-render-submit
一次绘制的提交、执行与显示
Mesa 批量编码后通过 controlq 交给 QEMU，virglrenderer 转成宿主图形调用。图示普通 GLsync 分支，带 fence 的完成响应沿原请求返回；显示输出另行消费图像资源。
```

### 完成绘制并交给显示端

本例采用带 fence 的提交。Guest 内核建立 fence，并把它关联到资源依赖或输出同步对象；`VIRTIO_GPU_FLAG_FENCE` 和 `fence_id` 就在同一条 `SUBMIT_3D` 的外层头中。QEMU 让 renderer 在已提交工作之后建立同步点，renderer 观察宿主执行完成后回调 QEMU，QEMU 返回对应 `fence_id` 的响应并发布 used。

Guest 驱动经 `virtio_gpu_dequeue_ctrl_func()` 消费响应，调用 `virtio_gpu_fence_event_process()`，最终通过 `dma_fence_signal_locked()` 使等待者观察到完成。等待者可以是后续渲染提交、缓冲区映射操作或需要读取结果的应用。普通、不带 FENCE 的命令响应可以早于 GPU 执行完成，需按实际响应类型判断。[QEMU fence 回调](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/display/virtio-gpu-virgl.c#L989)、[Guest fence 完成](https://github.com/torvalds/linux/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/drivers/gpu/drm/virtio/virtgpu_fence.c#L111)

窗口显示还要经过客户机窗口系统或合成器，以及 virtio-gpu 的 scanout 更新。最简单的显示路径把已经绘制好的资源绑定到 scanout，并通知更新；合成桌面可能先把多个窗口合成为另一个显示资源。QEMU 的 VirGL 后端可以把宿主纹理交给 GL 显示后端，避免为了显示而先把整帧复制回 Guest。窗口重绘、远程传输和显示器扫描继续遵循各自的同步机制；渲染 fence 只覆盖它所对应的工作。

本例在 F1 完成后提交该图像的显示更新，便于跟踪同一份结果。连续绘制时，应用可以轮换多个颜色目标，让下一帧准备、当前帧渲染和上一帧显示重叠。要复用某个目标，需要确认仍在访问它的渲染及显示操作各自已完成；仅收到 F1，也只能解除 F1 所覆盖的渲染依赖。

## 视频能力的协商与宿主接入 {#video-support}

视频播放器通常通过 VA-API 选择 codec profile 和入口类型，例如 H.264 解码的 VLD，或编码的 EncSlice。客户机安装 Mesa 的 VA state tracker 后，可以把这些请求转成 Gallium 视频接口，再由 virgl 视频驱动编码。宿主 renderer 对应的 VirGL video 实现则调用宿主 VA-API。

这条路径需要两端共同满足以下条件：

| 层次 | 本文固定版本的条件 |
| --- | --- |
| Guest 应用与 Mesa | 应用使用 VA-API，Mesa 构建包含 VA state tracker、virgl 及所需 codec 支持；Guest 查询到对应 profile/entrypoint |
| virglrenderer 构建 | `-Dvideo=true`，视频支持默认关闭 |
| 宿主程序初始化 | 调用 renderer 时设置 `VIRGL_RENDERER_USE_VIDEO`，并通过 `get_drm_fd` 提供可用的 DRM fd |
| 宿主 VA-API | 所用 driver 支持目标格式、profile、入口及尺寸；1.2.0 实现还检查 vendor 字符串包含 `Mesa Gallium` |
| QEMU 10.2.0 上游 | `virtio_gpu_virgl_init()` 未设置视频标志，回调表也未提供 `get_drm_fd`，因此这套默认接入不能直接运行下文的硬件视频链 |

这些门控分别出现在 [renderer 构建选项](https://gitlab.freedesktop.org/virgl/virglrenderer/-/blob/500b41d5c8638f9b80dd558f4044f3301c7457a4/meson_options.txt)、[renderer 初始化](https://gitlab.freedesktop.org/virgl/virglrenderer/-/blob/500b41d5c8638f9b80dd558f4044f3301c7457a4/src/virglrenderer.c)、[VA 后端初始化](https://gitlab.freedesktop.org/virgl/virglrenderer/-/blob/500b41d5c8638f9b80dd558f4044f3301c7457a4/src/vrend/virgl_video.c)和 [QEMU 接入](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/display/virtio-gpu-virgl.c#L1074)。仅安装宿主 VA-API 驱动或打开 QEMU 的 GL 显示选项，不能补齐缺少的初始化。

下面两节分析 **VirGL video 已正确接入、能力查询成功后的实现流程**。在不具备条件的环境中，应用需要使用软件 codec，或选择另一种明确提供视频加速能力的设备方案；应用是否自动回退取决于自身行为。

## 一帧 H.264 的解码与显示 {#decode}

### 准备码流、参数与目标表面

取一帧 1920 × 1080、8 位 4:2:0 的 H.264 IDR 图像作为例子，选择两端支持的 NV12 输出表面。播放器先在 Guest CPU 上完成容器解复用和必要的码流解析，向 VA-API 提供序列、图像、slice 参数及压缩数据。实际播放还要处理时间戳、显示顺序和音画同步；这些职责属于播放器。

Guest VA state tracker 创建解码 context 和目标 surface，再通过 Gallium 的视频接口进入 `virgl_video_create_codec()`、`virgl_video_create_buffer()`。内层协议分别使用 `CREATE_VIDEO_CODEC` 和 `CREATE_VIDEO_BUFFER`，后者将视频缓冲区与对应的图像平面资源联系起来。宿主建立对应的 VA context 和 VA surface，并保存两端句柄的映射。

解码得到的 NV12 图像有一个 Y 平面和一个交错 UV 平面。忽略 stride 和存储对齐时，1920 × 1080 的有效数据量为 `1920 × 1080 × 3 / 2 = 3,110,400 B`，约 2.97 MiB。压缩码流的长度由内容和编码参数决定，两者不能混用为传输长度。后续 P/B 图像还需要引用已经解码的参考帧；资源能否复用同时受显示消费和参考关系约束。

### 提交一帧压缩数据

一次解码按 `BEGIN_FRAME → DECODE_BITSTREAM → END_FRAME` 组织。Guest virgl 驱动准备保存码流和图像参数的资源，编码它们的句柄、长度以及目标帧句柄。资源上传与命令提交保持先后依赖，批量命令仍由外层 `SUBMIT_3D` 传输。

QEMU 只识别外层提交并交给 renderer。宿主 `vrend_video_decode_bitstream()` 查找 codec、目标 surface、码流及参数资源，把客户机参考句柄转换成宿主可识别的对象，然后调用 VA 后端。VA 层围绕目标 surface 组织 `vaBeginPicture()`、参数与码流 buffer 的 `vaRenderPicture()`，最终以 `vaEndPicture()` 提交。宿主 VA driver 继续转换为硬件视频作业，GPU 的视频解码单元执行对应工作。[Guest 视频提交](https://gitlab.freedesktop.org/mesa/mesa/-/blob/4fa244fddfebb21378042556862e197284ef65ac/src/gallium/drivers/virgl/virgl_video.c)、[宿主视频资源解析](https://gitlab.freedesktop.org/virgl/virglrenderer/-/blob/500b41d5c8638f9b80dd558f4044f3301c7457a4/src/vrend/vrend_video.c)、[宿主 VA 操作](https://gitlab.freedesktop.org/virgl/virglrenderer/-/blob/500b41d5c8638f9b80dd558f4044f3301c7457a4/src/vrend/virgl_video.c)

```diagram virtio-gpu-video-decode
压缩码流到可显示图像的解码路径
视频扩展已接入后，命令引用码流和参数资源，宿主 VA 后端执行解码。解码完成的表面还需同步到 VirGL 图像平面，再由渲染与显示路径消费。
```

### 解码表面转为可消费图像

`vaEndPicture()` 提交工作后，输出表面可能仍在写入。virglrenderer 的这一实现使用 `vaSyncSurface()` 等待表面就绪，再通过 DMA-BUF 导出表面，并以 EGLImage 等对象连接到 GL。解码完成回调把各平面内容复制到对应的 VirGL 纹理资源；`sync_dmabuf_to_video_buffer()` 中有实际的 `glCopyTexSubImage2D()` 调用。因此，即使使用了 DMA-BUF，共享句柄也不意味着整个视频链没有复制。[解码完成与平面复制](https://gitlab.freedesktop.org/virgl/virglrenderer/-/blob/500b41d5c8638f9b80dd558f4044f3301c7457a4/src/vrend/vrend_video.c#L149)

播放器或合成器随后可以把 Y、UV 作为纹理输入，完成颜色转换、缩放和界面合成，再交给前一节的显示路径。若应用需要在 Guest CPU 上访问解码像素，还要走相应的资源映射或读回路径。视频解码完成、平面资源可用、完成渲染和实际显示各有明确的依赖，画面才能正确交付。

## 一帧 NV12 图像的编码与取回 {#encode}

### 输入表面与编码参数

编码反向处理一帧原始图像。设应用提供上述 1920 × 1080 的 NV12 帧，并请求编码一帧 H.264 IDR 图像。CPU 产生的图像需要先上传到相应平面资源；GPU 渲染产生的图像则需要满足输入格式和同步要求，必要时增加颜色转换。

应用通过 VA-API 设置 profile、码率控制、图像及 slice 等参数，并准备接收压缩结果的 coded buffer。Guest Mesa 创建视频 codec 时分配可轮换使用的参数与反馈缓冲区；VA state tracker 将输出资源传给 `virgl_video_encode_bitstream()`。该函数等待相关缓冲区可复用，填写帧参数，将 feedback 初始化为 `NOT_STARTED` 和长度 0，再编码资源句柄。codec 对象保存编码会话，帧资源保存输入图像，feedback 保存本次完成结果和长度。

### 编码命令与宿主执行

客户机提交 `BEGIN_FRAME → ENCODE_BITSTREAM → END_FRAME`。`ENCODE_BITSTREAM` 引用输入图像、编码参数、压缩输出和反馈资源，随其他 VirGL 命令通过 `SUBMIT_3D` 进入宿主。

宿主查找这些资源，把 VirGL 输入平面同步到 VA surface，再设置 VA 编码参数，提交视频编码工作。固定实现中的输入同步也使用 GL 图像与 VA 表面间的平面复制。宿主 VA driver 和视频编码单元负责运动估计、预测、变换和码流输出等编码工作，具体硬件分工由驱动及所选 codec 决定。

```diagram virtio-gpu-video-encode
原始图像到压缩码流的编码路径
输入平面就绪后，宿主 VA 编码器生成压缩数据。完成回调填入输出资源与 feedback，客户机检查状态和有效长度，再把码流交给应用封装或发送。
```

### 取回有效码流

宿主完成编码并同步后，映射 VA coded buffer，取得一个或多个有效码流段。`vrend_video_encode_completed()` 将这些数据写入客户机可访问的输出资源，并更新 feedback 中的完成状态和 `coded_size`。这一步需要区分输出容量和本次实际编码长度；应用只能消费有效范围。

Guest 的 `virgl_video_get_feedback()` 等待对应工作，再读取反馈。VA state tracker 通过 coded buffer 接口把编码结果交给应用；应用接着封装容器、写文件或进行网络发送。编码器生成 H.264 数据、文件写入完成以及远端收到视频分别属于不同阶段。[宿主编码反馈](https://gitlab.freedesktop.org/virgl/virglrenderer/-/blob/500b41d5c8638f9b80dd558f4044f3301c7457a4/src/vrend/vrend_video.c#L296)、[Guest 编码与反馈](https://gitlab.freedesktop.org/mesa/mesa/-/blob/4fa244fddfebb21378042556862e197284ef65ac/src/gallium/drivers/virgl/virgl_video.c#L1058)

固定版本的输出复制按资源剩余容量限制每段写回量，并把已复制的字节数记入 `coded_size`；这个分支随后仍填写 SUCCESS。因此，成功状态本身不能证明输出缓冲区足够大。调用方需要按编码接口要求提供足够容量，并检查状态、有效长度和结果完整性，不能指望容量不足一定转换为失败响应。

一般的视频编码还可能使用多参考帧、重排序及缓冲策略，输入一帧和立即得到一帧输出不能作为通用假设。这里选择 IDR 单帧，目的是把资源与完成关系对应起来；完整视频序列继续由 codec 会话维护跨帧状态。

## 完成信号、缓冲区复用与等待 {#completion}

三条路径都包含 CPU 提交和异步硬件处理，但每一层的“完成”覆盖不同范围。

| 观察到的事件 | 可以确认的进度 | 后续仍需确认 |
| --- | --- | --- |
| Guest 图形 API 或提交 ioctl 返回 | 软件调用完成，或内核已接受提交 | 宿主是否执行到该工作，GPU 是否完成 |
| controlq 的 used 记录 | 设备返回了这条命令的响应，可以检查类型与状态 | 普通响应与带 fence 响应的完成保证不同 |
| 对应渲染 fence 完成 | 该同步点覆盖的渲染工作已经完成 | 合成、显示或远程传输是否完成 |
| VA surface 同步成功 | 视频操作对相应表面的执行已完成 | VirGL 平面复制、渲染使用和应用访问依赖 |
| 编码 feedback 成功且长度有效 | 可以按接口约定消费本次压缩输出 | 文件封装、磁盘持久化或网络交付 |
| 显示后端收到图像更新 | 输出端可以安排消费资源 | 实际呈现时间及远端客户端状态 |

错误也沿这些边界传播。无效资源句柄、无法支持的 profile 或宿主 API 失败需要检查响应和状态，输出容量还要满足前述约束。Guest 只有在相关访问完成、依赖解除后才能复用资源；参考帧、后续渲染任务和显示输出可能分别持有同一图像。

诊断卡顿时，可以沿着“Guest 等待提交 → QEMU/renderer 处理 → 宿主驱动排队 → GPU 执行 → 完成回传 → 显示消费”定位等待发生的位置。硬件视频单元降低 codec 计算负担，资源复制、颜色转换和同步等待仍可能占据主要时间。比较不同方案时应同时观察 CPU 占用、复制量、帧延迟和并发负载。

## VirGL、Venus 与资源共享的演进 {#evolution}

普通二维后端接收已经形成的像素，资源与更新关系简单，但复杂绘制主要由 Guest 软件完成。VirGL 把可移植的图形操作交给宿主图形栈，宿主可以使用真实 GPU；代价是命令验证、对象转换、着色器处理和两端能力兼容。

Venus 为 Vulkan 定义另一套序列化协议，使用不同的 capset 和上下文。客户机 Vulkan 实现把 API 操作编码为协议命令，宿主 Vulkan renderer 重建调用并交给宿主 Vulkan 驱动。其传输可以使用共享内存 ring，配合 virtio-gpu 完成上下文、资源及控制操作，不能逐条套用前面的 VirGL DRAW 命令格式。

这里涉及三种不同的队列：**virtqueue** 是 Guest 驱动与设备的请求通道；**Venus 共享 ring** 是协议命令传输机制；**Vulkan VkQueue** 是应用提交 GPU 工作的 API 对象。一次 Vulkan 调用、一次协议传输和一次物理 GPU 提交之间可能存在批处理与异步执行。[Venus 协议实现说明](https://gitlab.freedesktop.org/mesa/mesa/-/blob/4fa244fddfebb21378042556862e197284ef65ac/docs/drivers/venus.rst)

| 机制 | 解决的主要问题 | 需要保持的约束 |
| --- | --- | --- |
| VirGL 渲染 | 将客户机图形操作交给宿主 OpenGL 实现 | capset、上下文、资源格式与 shader 兼容 |
| blob / host-visible 资源 | 为资源共享和 CPU 映射提供更灵活的内存表示 | 缓存一致性、映射权限、同步及生命周期 |
| Venus | 为 Vulkan 提供协议和宿主执行路径 | Vulkan 扩展、外部内存、驱动和平台条件 |
| VirGL video | 把视频操作与帧资源连接到宿主 VA-API | 视频初始化、codec 能力、表面格式及完成反馈 |
| 独立渲染后端 | 将渲染处理放在独立进程边界 | 资源传递、隔离、错误恢复及同步管理 |

这些能力沿不同方向扩展。支持 OpenGL 渲染不自动推出视频 profile 可用，支持 Venus 也不能推出所需 Vulkan Video 扩展已经端到端接通。应用应查询实际能力；GPU 型号、宿主驱动和虚拟化后端需要共同满足条件。

远程桌面的编码则位于另一条链：客户机图像已经由显示端取得，远程显示服务再选择软件或硬件 codec 压缩画面，发送给客户端。它服务于桌面传输，与 Guest 应用请求的 VA-API 编码是两个独立功能。`virtio-video` 也属于独立的视频设备协议设计，应与这里承载在 virtio-gpu `SUBMIT_3D` 中的 VirGL 视频命令区分。

## 源码与进一步阅读 {#sources}

阅读源码时，可以按执行边界逐段跟踪。表中的箭头表示关键执行顺序；controlq、宿主 GPU 提交和完成回调是异步交接点。

| 位置 | 关键入口 | 重点观察 |
| --- | --- | --- |
| Guest Mesa VirGL | [`virgl_draw_vbo()`、`virgl_flush_eq()`](https://gitlab.freedesktop.org/mesa/mesa/-/blob/4fa244fddfebb21378042556862e197284ef65ac/src/gallium/drivers/virgl/virgl_context.c) | 状态编码、批量命令和 flush |
| Guest DRM | [`virtio_gpu_execbuffer_ioctl()`](https://github.com/torvalds/linux/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/drivers/gpu/drm/virtio/virtgpu_submit.c) | 输入依赖、资源对象及输出 fence |
| QEMU | [`virgl_cmd_submit_3d()`、fence 回调与初始化](https://github.com/qemu/qemu/blob/698104725efad4b29079d857dfdebbd804e34c99/hw/display/virtio-gpu-virgl.c) | SUBMIT_3D 解包、renderer 调用及实际启用的能力 |
| 宿主 VirGL | [`vrend_decode_draw_vbo()`](https://gitlab.freedesktop.org/virgl/virglrenderer/-/blob/500b41d5c8638f9b80dd558f4044f3301c7457a4/src/vrend/vrend_decode.c) | 协议参数校验和宿主绘制分派 |
| Guest 视频驱动 | [`virgl_video_decode_bitstream()`、`virgl_video_get_feedback()`](https://gitlab.freedesktop.org/mesa/mesa/-/blob/4fa244fddfebb21378042556862e197284ef65ac/src/gallium/drivers/virgl/virgl_video.c) | 码流、帧参数、平面资源及反馈 |
| 宿主视频桥接 | [`vrend_video.c`](https://gitlab.freedesktop.org/virgl/virglrenderer/-/blob/500b41d5c8638f9b80dd558f4044f3301c7457a4/src/vrend/vrend_video.c) | codec/buffer 句柄、GL 平面复制和输出资源 |
| 宿主 VA 实现 | [`virgl_video.c`](https://gitlab.freedesktop.org/virgl/virglrenderer/-/blob/500b41d5c8638f9b80dd558f4044f3301c7457a4/src/vrend/virgl_video.c) | VA 初始化、帧提交、同步及 coded buffer |

- [virtio-gpu 二维资源与画面更新](virtio-gpu.html)：backing、scanout、TRANSFER 与 FLUSH。
- [GPU 命令执行](gpu-execution.html)：VFIO 整卡直通与限定 GVT-g 实现，比较 Guest 厂商驱动直接提交的路径。
- [virtio 设备初始化与特性协商](virtio-device-initialization.html)：队列、特性和设备状态。
- [中断传递](interrupt-delivery.html)：完成通知到客户机处理，以及通知抑制和中断加速。
