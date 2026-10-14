/* Deterministic one-packet trace. Time and throughput are deliberately not modelled. */
(function (root) {
  'use strict';

  const INTERFACES = ['e1000', 'virtio'];
  const NOTIFICATIONS = ['ioeventfd', 'mmio', 'suppressed'];

  function normalizeOptions(options = {}) {
    const device = options.device || 'e1000';
    const notification = options.notification || 'ioeventfd';
    const queueStart = Number(options.queueStart ?? 0);
    if (!INTERFACES.includes(device)) throw new RangeError('Unknown network interface');
    if (!NOTIFICATIONS.includes(notification)) throw new RangeError('Unknown notification path');
    if (![0, 3, 65535].includes(queueStart)) throw new RangeError('Unknown queue index');
    return {device, notification: device === 'e1000' ? 'mmio' : notification,
      queueStart: device === 'e1000' ? 0 : queueStart};
  }

  function splitQueue(index, start) {
    const next = (start + 1) & 0xffff;
    const slot = start % 4;
    const available = Array(4).fill(null), used = Array(4).fill(null);
    const descriptors = Array(4).fill(null);
    if (index >= 2) {
      descriptors[0] = {addr: '0x1000', len: 12, flags: 1, next: 2};
      descriptors[2] = {addr: '0x2000', len: 60, flags: 0, next: 0};
      available[slot] = 0;
    }
    if (index >= 6) used[slot] = {id: 0, len: 0};
    return {size: 4, slot, descriptors, available, used,
      availIndex: index >= 2 ? next : start,
      backendIndex: index >= 4 ? next : start,
      usedIndex: index >= 6 ? next : start,
      driverUsedIndex: index >= 8 ? next : start,
      freeDescriptors: index >= 2 && index < 8 ? 2 : 4,
      ownership: index < 2 ? '驱动可填写描述符与缓冲区'
        : index < 6 ? '链 0 → 2 已提交；驱动保持描述符与报文有效'
          : index < 8 ? '设备已发布完成；等待驱动读取 used 并回收'
            : '驱动已回收链 0 → 2；可再次使用，环中的旧值无需清零'};
  }

  function buildTrace(options = {}) {
    const config = normalizeOptions(options);
    const virtio = config.device === 'virtio';
    const suppressed = virtio && config.notification === 'suppressed';
    const start = config.queueStart, nextIndex = (start + 1) & 0xffff, slot = start % 4;
    const frames = [
      {
        title: '准备发送', nodes: ['driver'], edges: [],
        description: '客户机驱动准备发送报文 P1。队列为空，缓冲区由驱动管理。' + (suppressed ? '本场景中 QEMU 已在处理该发送队列，并暂时关闭提交通知。' : ''),
        source: virtio ? '客户机 virtio-net 驱动 → 发送 virtqueue' : '客户机 e1000 驱动 → TX 描述符环',
      },
      {
        title: '准备报文缓冲区', nodes: ['driver', 'payload'], edges: [],
        description: '报文 P1 保存在客户机 RAM 中。驱动接下来用描述符记录设备可以读取的地址和长度。' + (virtio ? 'virtio-net 同时准备 virtio_net_hdr，描述校验和、分段等元数据；本例不启用这些卸载功能。' : ''),
        source: '客户机 RAM：报文数据与队列元数据分别保存',
      },
      {
        title: virtio ? '发布可用描述符链' : '填写发送描述符',
        nodes: ['driver', 'descriptor'], edges: [],
        description: virtio
          ? `驱动填写 desc[0] → desc[2]，将链首编号 0 写入 avail.ring[${slot}]，在内存屏障后把 avail.idx 从 ${start} 更新为 ${nextIndex}。两个描述符组成一个请求，avail.idx 只推进一次。`
          : '驱动填写描述符中的 buffer_addr、length，设置 EOP 与 RS，并保证描述符和报文内容在提交前可见。此时尚未写入 TDT。',
        source: virtio ? 'split virtqueue：desc[] → avail.ring[] → 内存屏障 → avail.idx' : 'e1000 TX：buffer_addr / length / EOP / RS',
      },
      {
        title: suppressed ? '省略本次 kick' : (virtio ? '通知发送队列' : '写入发送尾指针'),
        nodes: suppressed ? ['descriptor', 'worker'] : ['driver', 'notifier'], edges: suppressed ? [] : ['notify'],
        description: !virtio
          ? '驱动将 TDT 从 0 写为 1。QEMU 的 MMIO 回调更新寄存器，并调用 start_xmit() 处理新增的发送描述符。'
          : suppressed
            ? 'QEMU 正在消费队列，通知抑制条件允许驱动跳过本次 kick。avail.idx 已经发布，新请求仍会在后端继续检查队列时被发现。'
            : config.notification === 'ioeventfd'
              ? '驱动写 virtio PCI 通知地址。KVM 命中已注册的 ioeventfd，唤醒 QEMU 的队列事件处理；本次通知无需通过 KVM_RUN 返回用户态模拟 MMIO。'
              : '驱动写 virtio PCI 通知地址；QEMU 通过 MMIO 回调找到对应队列，调用 virtio_queue_notify()，安排发送处理。',
        source: !virtio ? 'e1000_mmio_write → set_tctl → start_xmit'
          : suppressed ? 'virtio_queue_set_notification：后端继续消费；驱动遵守通知抑制条件'
            : config.notification === 'ioeventfd' ? 'KVM ioeventfd → virtio_queue_host_notifier_read → handle_output'
              : 'virtio_pci_notify_write → virtio_queue_notify → handle_output',
      },
      {
        title: 'QEMU 读取描述符', nodes: ['descriptor', 'worker'], edges: ['descriptor'],
        description: virtio
          ? `virtqueue_pop() 读取 avail.ring[${slot}] 中的链首 0，沿 next 找到 desc[2]，建立 scatter-gather 列表。设备私有的 last_avail_idx 推进到 ${nextIndex}，used.idx 仍为 ${start}。`
          : 'start_xmit() 从 TDH 指定的位置读取 TX 描述符。process_tx_desc() 根据地址和长度访问报文缓冲区，按 EOP 识别完整报文。',
        source: virtio ? 'virtio_net_flush_tx → virtqueue_pop → VirtQueueElement.out_sg' : 'start_xmit → pci_dma_read(desc) → process_tx_desc',
      },
      {
        title: '把报文写入 TAP', nodes: ['payload', 'worker', 'tap'], edges: ['payload', 'tap'],
        description: virtio
          ? 'QEMU 从描述符链定位报文，整理 virtio-net 头和 iovec，调用网络后端。tap_write_packet() 通过 writev() 将帧交给宿主 TAP；本例 TAP 立即接受数据。'
          : 'e1000 模型通过 pci_dma_read() 读取客户机报文到内部发送缓冲区，经 qemu_send_packet() 进入网络后端，最后通过 writev() 写入 TAP。',
        source: virtio ? 'qemu_sendv_packet_async → tap_receive_iov → tap_write_packet → writev'
          : 'process_tx_desc → xmit_seg → e1000_send_packet → tap_receive → writev',
      },
      {
        title: '回写队列完成记录', nodes: ['worker', 'completion'], edges: ['completion'],
        description: virtio
          ? `本例发送已完成，virtqueue_push() 写 used.ring[${slot}] = {id: 0, len: 0}，再把 used.idx 更新为 ${nextIndex}。len 统计设备写入缓冲区的字节数，本例 TX 为 0。`
          : '由于描述符设置了 RS，txdesc_writeback() 写回 DD 状态位。start_xmit() 推进 TDH，发送环中这一项的处理完成。',
        source: virtio ? 'virtqueue_push(tx_vq, elem, 0) → used.ring / used.idx'
          : 'txdesc_writeback → pci_dma_write(DD)；start_xmit → TDH++',
      },
      {
        title: '通知客户机完成', nodes: ['driver', 'worker'], edges: ['interrupt'],
        description: virtio
          ? 'virtio_notify() 检查客户机的完成通知条件。本例允许中断，因此向客户机发出队列完成通知；驱动也可以配置抑制并自行检查 used ring。'
          : '设备模型设置发送相关的中断原因。本例中断未屏蔽；实际发送中断还受掩码、延时和合并条件控制。',
        source: virtio ? 'virtio_notify → virtio_should_notify → virtio_irq' : 'start_xmit → set_ics → 中断原因与掩码检查',
      },
      {
        title: '驱动回收缓冲区', nodes: ['driver', 'payload', 'completion'], edges: [],
        description: virtio
          ? '客户机驱动读取 used 项，回收描述符链和报文缓冲区。该完成记录表示本地队列已用完这些缓冲区；远端主机是否收到报文，需要网络协议另行确认。'
          : '客户机驱动读取 DD 后回收相应的描述符和报文缓冲区。DD 记录设备模型对本地描述符的处理结果，远端接收仍由网络协议确认。',
        source: virtio ? '客户机驱动：读取 used ring → 回收发送缓冲区' : '客户机驱动：检查 DD → 清理 TX 描述符',
      },
    ];
    return frames.map((frame, index) => ({
      ...frame, index, config,
      payloadPrepared: index >= 1,
      descriptorPrepared: index >= 2,
      published: virtio ? index >= 2 : index >= 3,
      consumed: index >= 4,
      tapAccepted: index >= 5,
      completed: index >= 6,
      completionNotified: index >= 7,
      reclaimed: index >= 8,
      kickSent: index >= 3 && !suppressed,
      head: virtio ? (index >= 6 ? nextIndex : start) : (index >= 6 ? 1 : 0),
      tail: virtio ? (index >= 2 ? nextIndex : start) : (index >= 3 ? 1 : 0),
      queue: virtio ? splitQueue(index, start) : null,
    }));
  }

  const api = {normalizeOptions, buildTrace};
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (!root || !root.document) return;

  function initialize(lab) {
    const get = name => lab.querySelector(`[data-tx-${name}]`);
    const text = (name, value) => { get(name).textContent = value; };
    const device = get('interface');
    const notification = get('notification');
    const queueStart = get('queue-start');
    const play = get('play');
    let trace = buildTrace(), index = 0, timer = null;
    let virtioNotification = 'ioeventfd';

    function pause() {
      if (timer !== null) root.clearInterval(timer);
      timer = null;
      lab.dataset.playing = 'false';
      play.setAttribute('aria-pressed', 'false');
      play.textContent = '播放';
    }

    function render() {
      const frame = trace[index];
      const virtio = frame.config.device === 'virtio';
      const suppressed = frame.config.notification === 'suppressed';
      text('status', `${index} / ${trace.length - 1} · ${frame.title}`);
      text('description', frame.description);
      text('source', frame.source);
      text('driver', virtio ? 'virtio-net 驱动' : 'e1000 驱动');
      text('driver-state', frame.reclaimed ? '缓冲区已回收，可再次使用' : frame.published ? '请求已提交，等待完成' : '准备本地发送请求');
      text('payload', frame.reclaimed ? 'P1 缓冲区已回收' : frame.payloadPrepared ? 'P1 已就绪，内容保持有效' : 'P1 尚未准备');
      text('queue-title', virtio ? 'Split virtqueue' : 'TX 描述符环');
      text('descriptor', !frame.descriptorPrepared ? '尚未填写描述符' : virtio
        ? 'desc[0]：net_hdr → desc[2]：P1' : 'desc[0]：P1 · EOP=1 · RS=1');
      text('completion', virtio ? (frame.completed ? `used[${frame.queue.slot}]: id=0, len=0` : 'used ring 暂无新项') : `desc[0].DD = ${frame.completed ? 1 : 0}`);
      text('head-label', virtio ? 'used.idx' : 'TDH');
      text('tail-label', virtio ? 'avail.idx' : 'TDT');
      text('head', String(frame.head)); text('tail', String(frame.tail));
      text('worker', virtio ? 'virtio-net 设备模型' : 'e1000 网卡模型');
      text('worker-state', frame.completed ? 'P1 队列处理完成' : frame.consumed ? '已读取描述符，处理 P1' : suppressed ? '正在处理发送队列' : '等待新的发送请求');
      text('notifier', !virtio ? 'MMIO 寄存器回调' : suppressed ? '后端已在处理，省略 kick'
        : frame.config.notification === 'ioeventfd' ? 'KVM ioeventfd → QEMU 事件处理' : 'virtio PCI MMIO 回调');
      text('notify-label', !virtio ? 'TDT 写入' : suppressed ? '无本次 kick' : '队列 kick');
      text('send-function', virtio ? 'qemu_sendv_packet_async' : 'qemu_send_packet');
      text('tap-state', frame.tapAccepted ? '已接受 P1' : '等待 P1');
      get('split-layout').hidden = !virtio;
      if (virtio) {
        const queue = frame.queue;
        text('queue-ownership', queue.ownership);
        text('queue-free', `${queue.freeDescriptors} / 4 个描述符可分配`);
        text('queue-avail', String(queue.availIndex)); text('queue-used', String(queue.usedIndex));
        text('queue-consumed', String(queue.backendIndex)); text('queue-reclaimed', String(queue.driverUsedIndex));
        text('queue-slot', `本次环槽 = ${frame.config.queueStart} % 4 = ${queue.slot}；链首为 desc[0]`);
        lab.querySelectorAll('[data-tx-desc-index]').forEach(row => {
          const desc = queue.descriptors[Number(row.dataset.txDescIndex)];
          ['addr', 'len', 'flags', 'next'].forEach(field => {
            row.querySelector(`[data-tx-desc-field="${field}"]`).textContent = !desc ? '—'
              : field === 'flags' ? (desc.flags ? 'NEXT (1)' : '0')
                : field === 'next' && !desc.flags ? '—' : String(desc[field]);
          });
          row.dataset.active = String(frame.descriptorPrepared && !frame.reclaimed);
        });
        for (const name of ['available', 'used']) {
          const container = get(`queue-${name}-slots`);
          container.replaceChildren();
          queue[name].forEach((entry, index) => {
            const cell = root.document.createElement('div');
            cell.className = 'device-tx-ring-slot';
            cell.dataset.selected = String(index === queue.slot);
            const label = root.document.createElement('small'); label.textContent = `ring[${index}]`;
            const value = root.document.createElement('strong');
            value.textContent = entry === null ? '—' : name === 'available' ? `head=${entry}` : `id=${entry.id}`;
            cell.append(label, value);
            if (name === 'used' && entry !== null) {
              const length = root.document.createElement('small'); length.textContent = `len=${entry.len}`; cell.append(length);
            }
            container.append(cell);
          });
        }
      }
      lab.querySelectorAll('[data-tx-node]').forEach(node => node.classList.toggle('is-active', frame.nodes.includes(node.dataset.txNode)));
      lab.querySelectorAll('[data-tx-edge]').forEach(edge => {
        edge.classList.toggle('is-active', frame.edges.includes(edge.dataset.txEdge));
        edge.classList.toggle('is-suppressed', suppressed && edge.dataset.txEdge === 'notify');
      });
      get('back').disabled = index === 0;
      get('next').disabled = index === trace.length - 1;
      play.disabled = index === trace.length - 1;
    }

    function reset() {
      pause(); index = 0;
      trace = buildTrace({device: device.value, notification: notification.value, queueStart: queueStart.value});
      notification.disabled = device.value === 'e1000';
      queueStart.disabled = device.value === 'e1000';
      text('option-note', device.value === 'e1000'
        ? 'e1000 通过写 TDT 寄存器提交；通知选项用于 virtio-net。'
        : notification.value === 'suppressed'
          ? '假定 QEMU 正在消费队列，通知抑制条件已满足。省略 kick 时仍须发布 avail 项。'
          : '两条通知路径最终都由 QEMU 消费队列，TAP 后端保持不变。');
      render();
    }
    get('back').addEventListener('click', () => { pause(); index = Math.max(0, index - 1); render(); });
    get('next').addEventListener('click', () => { pause(); index = Math.min(trace.length - 1, index + 1); render(); });
    get('reset').addEventListener('click', reset);
    device.addEventListener('change', () => {
      notification.value = device.value === 'virtio' ? virtioNotification : 'mmio';
      reset();
    });
    notification.addEventListener('change', () => { virtioNotification = notification.value; reset(); });
    queueStart.addEventListener('change', reset);
    play.addEventListener('click', () => {
      if (timer !== null) { pause(); return; }
      if (index === trace.length - 1) return;
      play.setAttribute('aria-pressed', 'true'); play.textContent = '暂停';
      lab.dataset.playing = 'true';
      timer = root.setInterval(() => {
        index = Math.min(trace.length - 1, index + 1);
        if (index === trace.length - 1) pause();
        render();
      }, 1800);
    });
    root.document.addEventListener('visibilitychange', () => { if (root.document.hidden) pause(); });
    root.addEventListener('resize', pause);
    root.addEventListener('pagehide', pause);
    get('controls').hidden = false;
    get('source').hidden = false;
    lab.dataset.initialized = 'true';
    reset();
  }
  root.document.querySelectorAll('[data-device-tx-lab]').forEach(initialize);
})(typeof window === 'undefined' ? null : window);
