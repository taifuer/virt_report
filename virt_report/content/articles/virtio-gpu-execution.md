## 场景与边界 {#preface}

从 Guest 应用的一次图形或视频操作开始，分别观察 OpenGL 绘制三角形、H.264 解码一帧和 NV12 编码一帧。沿 Guest 用户态、Guest DRM 驱动、controlq、QEMU、virglrenderer、Host kernel 与物理 GPU，跟踪命令提交、资源变化和完成通知。

固定版本中的 OpenGL 渲染路径已经接入 QEMU。**QEMU 10.2.0 默认未启用 VirGL 视频扩展**；解码和编码演示以宿主程序完成视频初始化、提供 DRM fd、两端能力协商成功为前提，不表示默认 QEMU 配置即可运行。完整条件见[视频能力的协商与宿主接入](virtio-gpu.html#video-support)。

提交返回、GPU 执行结束、Guest 确认 fence、应用消费结果和显示交付分别推进。演示固定跟踪一批工作；真实应用可以有多批在途，资源复用仍需满足各自的同步依赖。示例地址、图像和播放间隔只用于讲解，不代表实际硬件布局或耗时。

```baselines
```

## 逐步演示 {#walkthrough}

```component _execution_lab.html
```

## 完成边界 {#completion}

渲染提交的 fence 确认相应 GPU 工作完成，不代表显示器已经呈现图像。解码还要完成 VA 表面与 VirGL 平面资源的同步；编码还要检查反馈状态、实际长度和输出容量。普通 controlq 响应也不能直接当作 GPU 完成信号。

## 源码与相关内容 {#sources}

源码视图按执行上下文列出 Guest Mesa / DRM、QEMU、virglrenderer 和完成回调的关键入口；每个链接固定到所核验的版本。硬件执行和异步通知不当作跨组件的同步函数调用。

- [virtio-gpu：资源、渲染与编解码](virtio-gpu.html)：从二维更新到三维与视频的完整原理；[二维小演示](virtio-gpu.html#interactive)可单独观察 CREATE、TRANSFER、FLUSH 和 scanout。
- [GPU 命令执行](gpu-execution.html)：VFIO 直通与 GVT-g 的另一组设备路径。
- [中断传递](interrupt-delivery.html)：设备通知、KVM 路由与 Guest 处理。
