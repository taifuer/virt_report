/* Completion contracts layered onto the SAME reviewed R1 trace, not a second player.
 * F1 is an explicit later FLUSH request. FUA keeps R1 outstanding until durability.
 * Cache labels express guarantees, not simulated storage hardware timings. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.DiskCompletionModel = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const linux = '7d0a66e4bb9081d75c82ec4957c50034cb0ea449';
  const qemu = '698104725efad4b29079d857dfdebbd804e34c99';
  const definitions = {
    'sync-bio': ['Linux', 'block/fops.c', 28, 'dio_bio_write_op()'],
    'sync-fsync': ['Linux', 'block/fops.c', 617, 'blkdev_fsync()'],
    'sync-issue-flush': ['Linux', 'block/blk-flush.c', 468, 'blkdev_issue_flush()'],
    'sync-command': ['Linux', 'drivers/block/virtio_blk.c', 238, 'virtblk_setup_cmd()'],
    'sync-flush': ['QEMU', 'hw/block/virtio-blk.c', 334, 'virtio_blk_handle_flush()'],
    'sync-flush-done': ['QEMU', 'hw/block/virtio-blk.c', 135, 'virtio_blk_flush_complete()'],
    'sync-layers': ['QEMU', 'block/io.c', 3013, 'bdrv_co_flush()'],
    'sync-file': ['QEMU', 'block/file-posix.c', 1728, 'handle_aiocb_flush()'],
    'sync-file-fua': ['QEMU', 'block/file-posix.c', 2572, 'raw_co_prw()'],
    'sync-scsi-fua': ['Linux', 'drivers/scsi/sd.c', 1320, 'sd_setup_read_write_cmnd()'],
    'sync-scsi-detect': ['QEMU', 'hw/scsi/scsi-disk.c', 366, 'scsi_is_cmd_fua()'],
  };
  const copy = value => JSON.parse(JSON.stringify(value));
  function supported(backend, operation) {
    return operation !== 'write' ? ['ordinary'] : backend === 'local-blk'
      ? ['ordinary', 'flush'] : backend === 'local-scsi' ? ['ordinary', 'fua'] : ['ordinary'];
  }
  function extend(scene, original, mode) {
    const backend = scene.request.backend || 'local-blk';
    if (!supported(backend, scene.request.operation).includes(mode)) throw new TypeError('Unsupported completion contract');
    const frames = copy(original);
    if (scene.request.operation !== 'write') return {frames, mode};
    for (const [id, [project, path, line, symbol]] of Object.entries(definitions)) {
      scene.sources[id] = {project, path, line, symbol,
        url: `https://github.com/${project === 'Linux' ? 'torvalds/linux' : 'qemu/qemu'}/blob/${project === 'Linux' ? linux : qemu}/${path}#L${line}`};
    }
    const local = backend.startsWith('local-');
    const status = (cached, stable) => ({
      host: local ? (cached ? '本次数据已交给宿主文件后端' : '本次数据尚未交给后端') : '由所选后端管理，不套用本地页缓存模型',
      device: '未单独观测设备缓存的物理时序',
      stable: stable ? '本次数据已获持久化成功保证' : '本操作尚未确认持久化', stableConfirmed: stable,
    });
    frames.forEach(frame => { frame.durability = status(frame.state.dataReady, false); });
    const add = (base, id, title, description, detail, actor, phase, sourceIds, active, edge = null) => ({
      ...copy(base), id, title, description, detail, actor, phase, sourceIds, active, edge, changes: {},
    });
    if (mode === 'fua') {
      // A REQ_FUA block request is the honest boundary. Linux blkdev_write_iter
      // still calls generic_write_sync for O_DSYNC, so one FUA command alone is
      // not a complete O_DIRECT | O_DSYNC userspace trace.
      frames.splice(0, frames.findIndex(frame => frame.id === 'ready'));
      const returned = frames.findIndex(frame => frame.id === 'return-to-user');
      if (returned !== -1) frames.splice(returned, 1);
      scene.assumptions = scene.assumptions.filter(text =>
        !text.startsWith('Guest 用 O_DIRECT') && !text.startsWith('写入没有 O_SYNC') &&
        !text.includes('固定完成侧清理后恢复原线程'));
      scene.assumptions.push(
        '从已经携带 REQ_FUA 的 Guest 块请求 R1 开始：偏移 1 MiB、4 KiB、单 bio、单 SG 数据段，缓冲区已准备并有效；SCSI 设备已声明支持 FUA。',
        '仅跟踪这条请求至上层块请求完成，不模拟应用系统调用；不能用它代替完整 O_DSYNC 路径，后者可能还包含额外同步。',
        'Guest 无 vIOMMU，GPA 仅教学示例。步骤是已核验的因果阶段，不是实测轨迹；数据和请求完成后不再展示应用恢复。'
      );
      scene.summary = '已有 REQ_FUA 的 R1 被编码为 SCSI WRITE(10)；QEMU 文件后端落实同步后才发布完成，最后结束上层块请求。';
      scene.request.command = 'WRITE(10) · FUA=1';
      scene.phases = scene.phases.filter(phase => phase.id !== 'application');
      Object.assign(scene.phases.find(phase => phase.id === 'read'), {
        summary: '本次 WRITE 携带 FUA，后端完成数据写入与同步后，才允许发布命令成功。',
      });
      Object.assign(scene.phases.find(phase => phase.id === 'return'), {
        title: '请求完成', summary: 'SCSI 与块层结束 R1；本场景止于上层块请求完成，不展示应用系统调用返回。',
      });
      scene.callPaths = scene.callPaths.filter(path => path.id !== 'application-resume');
      Object.assign(scene.callPaths.find(path => path.id === 'guest-submission'), {
        title: '已有 FUA 请求进入 SCSI', context: 'Guest 内核 · 块层 / SCSI / virtio-scsi',
        flow: 'sequence', relations: [],
        sourceIds: ['scsi-linux-queue', 'scsi-linux-cdb', 'scsi-linux-rw10', 'scsi-linux-command',
          'scsi-linux-sg', 'scsi-linux-add', 'ring-add', 'ring-publish', 'kick-check', 'pci-notify'],
        boundary: 'R1 和有效输入页已存在；这里只展示 REQ_FUA 到 CDB 再到共享队列的路径，不推断上层调用方式。',
      });
      const completedPath = scene.callPaths.find(path => path.id === 'guest-scsi-completion');
      completedPath.sourceIds = completedPath.sourceIds.filter(id => id !== 'guest-wait-complete');
      completedPath.relations = (completedPath.relations || []).filter(relation =>
        completedPath.sourceIds.includes(relation.from) && completedPath.sourceIds.includes(relation.to));
      completedPath.boundary = 'Guest 消费 used 后解码 SCSI 响应，经块层完成 bio 并结束 R1；上层如何等待或继续执行不在本场景中。';
      scene.callPaths.push({id: 'fua-contract', title: 'R1 的 FUA 完成要求', context: 'Guest SCSI → QEMU 文件后端',
        flow: 'sequence',
        sourceIds: ['sync-scsi-fua', 'sync-scsi-detect', 'sync-file-fua', 'sync-layers', 'sync-file'],
        boundary: '已有 REQ_FUA 经 SCSI CDB 传递；本例 threads 文件后端在写入后同步。没有第二条 Guest FUA 命令，也不代表完整 O_DSYNC 应用路径。'});
      Object.assign(frames[0], {
        id: 'fua-ready', title: '已有 FUA 块请求 R1',
        description: '上层已提交携带 REQ_FUA 的 4 KiB 写请求；R1 的输入页有效，blk-mq 开始交给 SCSI 中间层。',
        detail: '从请求边界开始，不指定应用的系统调用或等待方式。设备支持 FUA，本次无拆分、重试或错误；队列还未发布。',
        changes: {}, active: ['driver'], edge: null,
      });
      frames.forEach(frame => {
        frame.state.syscallEntered = false;
        frame.state.userReturned = false;
        frame.active = frame.active.filter(id => id !== 'application');
        frame.sourceIds = frame.sourceIds.filter(id => id !== 'guest-wait-complete');
      });
      Object.assign(frames.find(frame => frame.id === 'complete-bio'), {
        title: 'SCSI 中间层完成 bio',
        description: '成功命令经过 SCSI 完成层进入 scsi_end_request，blk_update_request 结束本次 bio。',
        detail: 'bio_endio 调用提交方配置的完成处理；本场景不限定它使用同步等待还是异步通知。成功路径无重试，且不经过 virtblk_request_done。',
        edge: {from: 'reclaim', to: 'driver', kind: 'notification', label: 'bio 完成'},
      });
      Object.assign(frames.find(frame => frame.id === 'finish-request'), {
        id: 'fua-upper-complete', phase: 'return', title: '上层块请求完成',
        description: 'scsi_end_request 清理 SCSI 命令并结束 R1；该次写入已获得下层承诺的持久化保证。',
        detail: '演示止于块请求完成。FUA 约束的是这条命令的成功条件，不保证所有无关前序写入，也不能据此宣称某个应用系统调用已经返回。',
      });
      const cdb = frames.find(frame => frame.id === 'scsi-build-cdb');
      cdb.description = '支持 FUA 的 SCSI 设备把 REQ_FUA 编码为 WRITE(10) CDB 的 FUA 位。'; cdb.sourceIds.push('sync-scsi-fua');
      Object.assign(frames.find(frame => frame.id === 'submit-read'), {
        detail: 'SCSI CDB 的 FUA 位转为 BDRV_REQ_FUA，随写请求进入 BlockBackend、raw 与 file 节点。aio=threads 完成数据写入后仍须同步，不能在提交时就报告成功。',
      });
      Object.assign(frames.find(frame => frame.id === 'read-data'), {
        title: '写入与同步成功返回块层',
        description: '线程后端已消费 4096 B 输入数据并完成要求的同步，R1 的写入现在可以向上报告成功。',
        detail: 'raw_co_prw 等待写入后执行 FUA 要求的 bdrv_co_flush；成功返回后才触发后续 SCSI 完成。dataReady 表示这个数据 I/O 已满足完成条件，不是此刻才开始写入，也没有新数据写回 Guest。',
        sourceIds: ['sync-file-fua', 'sync-layers', 'sync-file'],
      });
      const at = frames.findIndex(frame => frame.changes.dataReady === true), prev = frames[at - 1];
      const cached = add(prev, 'fua-cache', '数据写入，R1 仍未完成', '线程后端已写入数据，继续落实 FUA 同步要求。',
        '对上层的写完成回调尚未发生，不能发布 status 或 used。', 'backend', 'read', ['sync-file-fua'], ['storage']);
      cached.durability = status(true, false);
      const sync = add(cached, 'fua-sync', '等待后端同步', '文件后端执行 fdatasync，等待下层兑现稳定存储保证。',
        '这是同一个 R1 的完成约束，不是第二个 Guest 请求。设备易失缓存如何排空由下层负责。', 'backend', 'read', ['sync-file-fua', 'sync-file'], ['storage']);
      const durable = add(sync, 'fua-stable', '同步成功，允许完成 R1', '本次写入已获得下层承诺的持久化保证。',
        '随后才能返回写完成、发布响应和 used；FUA 本身不保证所有无关前序写入。', 'backend', 'read', ['sync-file-fua', 'sync-file'], ['storage']);
      durable.durability = status(true, true);
      frames.splice(at, 0, cached, sync, durable);
      for (let n = at + 3; n < frames.length; n++) frames[n].durability = status(true, true);
    } else if (mode === 'flush') {
      scene.summary = '先完整跟踪普通写 R1；pwrite 返回后，应用再调用 fsync，独立 FLUSH 请求 F1 沿同一队列完成刷新。';
      scene.assumptions.push('R1 完成后应用调用块设备 fsync；writeback=on、no-flush=off，已协商 VIRTIO_BLK_F_FLUSH。F1 为独立无数据刷新命令。');
      scene.callPaths.push({id: 'flush-contract', title: '后续独立刷新 F1', context: 'Guest fsync / 块层 → QEMU 块层 / 文件后端 → Guest 完成',
        flow: 'sequence',
        entry: {kind: 'async', label: 'R1 已返回；应用随后发起独立 FLUSH 请求 F1，跨队列与异步后端完成'},
        sourceIds: ['sync-fsync', 'sync-issue-flush', 'sync-command', 'sync-flush', 'sync-layers', 'sync-file', 'sync-flush-done'],
        boundary: 'F1 与已返回的 R1 是两个请求。后端异步刷新成功后，才允许发布 F1 完成并返回 fsync。'});
      for (const [id, title] of [['flush-request', '提交刷新 F1'], ['flush-backend', '同步后端'], ['flush-completion', '刷新完成'], ['sync-return', '同步返回']]) {
        scene.phases.push({id, title, summary: title});
      }
      const flags = {prepared: false, filled: false, published: false, consumed: false,
        response: false, usedWritten: false, usedPublished: false, reclaimed: false};
      const events = [
        ['sync-call','应用发起 fsync','guest-user','flush-request',['sync-fsync','sync-issue-flush'],['application'],null,{},'R1 已返回；应用现在请求刷新先前写入，不重新提交 R1 的 4 KiB 数据。'],
        ['flush-prepare','准备独立命令 F1','guest','flush-request',['sync-issue-flush','guest-submit','sync-command','guest-sg','ring-add'],['driver','queue'],null,{prepared:true},'复用空闲 desc[0] → desc[1]，分别指向 16 B FLUSH 头与 1 B 设备可写状态；没有数据描述符。'],
        ['flush-fill-avail','填写第二个 available 环项','guest','flush-request',['ring-add'],['queue'],null,{filled:true},'avail.ring[1] = 0；此时 avail.idx 仍为 1。'],
        ['flush-publish','发布 F1','guest','flush-request',['ring-publish'],['queue'],null,{published:true},'发布屏障后 avail.idx = 2；R1 的旧槽位仍保留。'],
        ['flush-notify','门铃通知后端','kvm','flush-request',['kick-check','pci-notify','ioeventfd','host-notifier'],['notify','device'],{from:'notify',to:'device',kind:'notification',label:'F1 kick / ioeventfd'},{},'匹配的队列通知经 ioeventfd 唤醒后端；KVM 不搬运刷新数据。'],
        ['flush-consume','QEMU 取出 F1','qemu','flush-backend',['queue-handler','queue-pop','request-parse','sync-flush'],['device','queue'],null,{consumed:true},'设备消费第二个 available 项，解析 VIRTIO_BLK_T_FLUSH。'],
        ['flush-submit','异步刷新后端','qemu','flush-backend',['sync-flush','sync-layers'],['device','storage'],{from:'device',to:'storage',kind:'notification',label:'blk_aio_flush / bdrv_co_flush'},{},'QEMU 将刷新交给块图和文件协议；F1 在等待完成回调。'],
        ['flush-sync','等待文件同步','backend','flush-backend',['sync-file'],['storage'],null,{},'fdatasync 等待宿主文件和存储栈落实之前写入的稳定存储要求。'],
        ['flush-stable','后端同步成功','backend','flush-backend',['sync-file','sync-flush-done'],['storage'],null,{stable:true},'R1 的数据已获得稳定存储保证；Guest 的 F1 仍需走完成队列。'],
        ['flush-status','回写 F1 成功状态','qemu','flush-completion',['sync-flush-done','status-write'],['completion'],null,{response:true},'FLUSH 完成回调写入 status=OK，但 used.idx 尚未推进。'],
        ['flush-fill-used','填写 F1 完成项','qemu','flush-completion',['status-write','used-record'],['completion'],null,{usedWritten:true},'used.ring[1] = {id:0,len:1}；刷新无数据载荷，只有一个状态字节。'],
        ['flush-used','发布刷新完成','qemu','flush-completion',['used-publish'],['completion'],null,{usedPublished:true},'写屏障后 used.idx = 2，驱动现在可以消费 F1。'],
        ['flush-interrupt','触发 F1 irqfd','qemu','flush-completion',['notify-check','irqfd-signal'],['completion','interrupt'],{from:'completion',to:'interrupt',kind:'notification',label:'F1 完成通知'},{irqSignaled:true},'本例允许完成通知，触发 irqfd；此时 Guest 尚未接受中断。'],
        ['flush-pending','F1 向量进入 LAPIC pending','kvm','flush-completion',['irqfd-route','msi-route','lapic-accept','vmx-pending'],['interrupt'],null,{irqPending:true},'KVM 匹配 MSI 路由并记录待递送向量，仍要满足 Guest 接受条件。'],
        ['flush-inject','F1 中断成功注入','kvm','flush-completion',['event-inject','vmx-inject','vmx-entry'],['interrupt'],null,{irqInjected:true},'本例 IF 与优先级允许，成功 VM-entry 后中断递送给 Guest。'],
        ['flush-handler','Guest 处理中断','guest','flush-completion',['guest-irq','guest-done'],['reclaim'],{from:'interrupt',to:'reclaim',kind:'notification',label:'Guest 完成回调'},{irqHandled:true},'Guest 驱动开始检查 used 环，fsync 尚未返回。'],
        ['flush-reclaim','Guest 回收 F1','guest','flush-completion',['guest-reclaim','guest-done','guest-end','guest-bio-end','guest-wait-complete'],['reclaim','queue'],null,{reclaimed:true},'驱动消费第二个 used 项，回收两个描述符，再结束刷新等待。'],
        ['sync-return','fsync 返回成功','guest-user','sync-return',['sync-fsync'],['application'],null,{},'R1 的写入与后续 F1 刷新均成功；fsync 返回 0。不模拟掉电实验，也不承诺失效硬件的行为。'],
      ];
      let stable = false;
      for (const [id,title,actor,phase,sourceIds,active,edge,patch,detail] of events) {
        Object.assign(flags, patch); stable ||= Boolean(patch.stable);
        const frame = add(frames.at(-1), id, title, detail, 'F1 独立于 R1：同一队列上的第二次交接，环索引从 1 推进到 2。', actor, phase, sourceIds, active, edge);
        frame.durability = status(true, stable); frame.followup = copy(flags);
        const avail = Array(8).fill(null), used = Array(8).fill(null);
        avail[0] = 0; used[0] = {id:0,len:1};
        if (flags.filled) avail[1] = 0;
        if (flags.usedWritten) used[1] = {id:0,len:1};
        frame.queueState = {descriptors: flags.prepared ? [
          {index:0,addr:'0x4000',len:16,flags:['NEXT'],next:1,label:'F1 FLUSH 请求头',state:flags.reclaimed?'returned':'owned'},
          {index:1,addr:'0x5000',len:1,flags:['WRITE'],next:null,label:'F1 成功状态',state:flags.reclaimed?'returned':'owned'},
        ] : [], avail:{index:1+Number(flags.published),consumer:1+Number(flags.consumed),slots:avail},
        used:{index:1+Number(flags.usedPublished),consumer:1+Number(flags.reclaimed),slots:used},
        buffers:[{id:'F1',label:'刷新命令 · 无数据载荷',addr:'0x4000 / 0x5000',length:17,validBytes:flags.response?1:0,state:flags.response?'status=OK':'等待完成'}],
        freeDescriptors:flags.prepared&&!flags.reclaimed?6:8,
        changed: [
          ...(patch.prepared ? ['descriptors', 'buffers', 'freeDescriptors'] : []),
          ...(patch.filled ? ['avail.slots'] : []),
          ...(patch.published ? ['avail.index'] : []),
          ...(patch.consumed ? ['avail.consumer'] : []),
          ...(patch.response ? ['buffers'] : []),
          ...(patch.usedWritten ? ['used.slots'] : []),
          ...(patch.usedPublished ? ['used.index'] : []),
          ...(patch.reclaimed ? ['descriptors', 'buffers', 'used.consumer', 'freeDescriptors'] : []),
        ]};
        frames.push(frame);
      }
    }
    frames.forEach((frame, index) => { frame.index = index; });
    return {frames, mode};
  }
  return {supported, extend};
});
