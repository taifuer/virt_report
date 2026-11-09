(function () {
  'use strict';
  const SIZE = 4;
  const position = (count) => ({ index: count % SIZE, wrap: 1 ^ (Math.floor(count / SIZE) % 2) });
  function createState() {
    return { published: 0, completed: 0, reclaimed: 0, freeIds: [2, 0, 3, 1],
      slots: Array.from({ length: SIZE }, () => ({ avail: 0, used: 0, id: null, length: 0, request: null, state: 'free' })),
      message: '初始环为 0/0，期望 wrap=1；没有可用项或完成项。' };
  }
  function advance(input, action) {
    if (action === 'reset') return createState();
    const state = { ...input, freeIds: input.freeIds.slice(), slots: input.slots.map((slot) => ({ ...slot })) };
    if (action === 'publish') {
      if (state.published - state.reclaimed === SIZE) return state;
      const at = position(state.published);
      const id = state.freeIds.shift();
      state.slots[at.index] = { avail: at.wrap, used: 1 - at.wrap, id, length: 256,
        request: state.published + 1, state: 'available' };
      state.published += 1;
      state.message = `发布请求 ${state.published}：槽 ${at.index}，Buffer ID=${id}，A/U=${at.wrap}/${1 - at.wrap}。先准备字段，再发布状态。`;
    } else if (action === 'complete') {
      if (state.completed === state.published) return state;
      const at = position(state.completed);
      const slot = state.slots[at.index];
      if (slot.avail !== at.wrap || slot.used === at.wrap) throw new Error('Descriptor is not available in this wrap');
      Object.assign(slot, { avail: at.wrap, used: at.wrap, length: 64, state: 'completed' });
      state.completed += 1;
      state.message = `设备完成请求 ${slot.request}：槽 ${at.index}，实际写入 64 B，A/U=${at.wrap}/${at.wrap}。等待驱动回收。`;
    } else if (action === 'reclaim') {
      if (state.reclaimed === state.completed) return state;
      const at = position(state.reclaimed);
      const slot = state.slots[at.index];
      if (slot.avail !== at.wrap || slot.used !== at.wrap) throw new Error('Descriptor is not completed in this wrap');
      state.freeIds.push(slot.id);
      slot.state = 'free';
      state.reclaimed += 1;
      state.message = `驱动回收 Buffer ID=${slot.id}，归还槽 ${at.index}。A/U 保留 ${slot.avail}/${slot.used}，无需清零；下一次使用由 wrap 区分。`;
    }
    return state;
  }
  function createSplitState() {
    return { published: 0, completed: 0, reclaimed: 0, freeIds: [2, 0, 3, 1],
      descriptors: Array.from({ length: SIZE }, () => ({ length: 0, request: null, state: 'free' })),
      available: Array(SIZE).fill(null), used: Array(SIZE).fill(null),
      message: 'avail.idx=0，used.idx=0；四个描述符空闲，没有新请求或完成项。' };
  }
  function advanceSplit(input, action) {
    if (action === 'reset') return createSplitState();
    const state = { ...input, freeIds: input.freeIds.slice(),
      descriptors: input.descriptors.map((desc) => ({ ...desc })),
      available: input.available.slice(), used: input.used.map((item) => item && { ...item }) };
    if (action === 'publish') {
      if (!state.freeIds.length) return state;
      const head = state.freeIds.shift(), index = state.published % SIZE;
      state.descriptors[head] = { length: 256, request: state.published + 1, state: 'available' };
      state.available[index] = head;
      state.published += 1;
      state.message = `准备 desc[${head}]，容量 256 B、WRITE；avail.ring[${index}]=${head}。保证内容可见后发布 avail.idx=${state.published % 65536}。`;
    } else if (action === 'complete') {
      if (state.completed === state.published) return state;
      const index = state.completed % SIZE, head = state.available[index];
      const desc = state.descriptors[head];
      if (desc.state !== 'available') throw new Error('Split descriptor has not been published');
      desc.state = 'completed';
      state.used[index] = { id: head, length: 64, request: desc.request };
      state.completed += 1;
      state.message = `设备读取链头 ${head}，写入 64 B，再填 used.ring[${index}]={id:${head}, len:64}；发布 used.idx=${state.completed % 65536}。desc.len 保持容量 256 B。`;
    } else if (action === 'reclaim') {
      if (state.reclaimed === state.completed) return state;
      const index = state.reclaimed % SIZE, item = state.used[index];
      const desc = state.descriptors[item.id];
      if (desc.state !== 'completed') throw new Error('Split request is not complete');
      desc.state = 'free';
      state.freeIds.push(item.id);
      state.reclaimed += 1;
      state.message = `驱动从 used.ring[${index}] 找回链头 ${item.id}，回收请求 ${item.request}；回收位置=${state.reclaimed % 65536}。描述符和环项旧值保留，资源现在可复用。`;
    }
    return state;
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = { createState, advance, position, SIZE, createSplitState, advanceSplit };
  if (typeof document === 'undefined') return;
  document.querySelectorAll('[data-packed-lab]').forEach((lab) => {
    let packedState = createState(), splitState = createSplitState(), format = 'split';
    const controls = lab.querySelector('.packed-lab-controls');
    const formats = lab.querySelector('.packed-lab-formats');
    const slots = lab.querySelector('[data-packed-slots]');
    const cursors = lab.querySelector('[data-packed-cursors]');
    const rings = lab.querySelector('[data-split-rings]');
    const node = (tag, value) => { const element = document.createElement(tag); element.textContent = value; return element; };
    function render() {
      const state = format === 'packed' ? packedState : splitState;
      formats.querySelectorAll('[data-queue-format]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.queueFormat === format)));
      lab.dataset.queueFormat = format;
      lab.querySelector('[data-packed-status]').textContent = state.message;
      cursors.replaceChildren();
      [['驱动下次发布', state.published], ['设备下次完成', state.completed], ['驱动下次回收', state.reclaimed]].forEach(([label, count]) => {
        const at = position(count);
        cursors.append(node('span', format === 'packed' ? `${label}：槽 ${at.index} · wrap ${at.wrap}` : `${label}：索引 ${count % 65536} · 环槽 ${at.index}`));
      });
      cursors.append(node('strong', `空闲描述符 ${state.freeIds.length} / ${SIZE}`));
      slots.replaceChildren();
      if (format === 'packed') state.slots.forEach((slot, index) => {
        const element = node('div', '');
        element.className = `packed-lab-slot is-${slot.state}`;
        element.append(node('strong', `槽 ${index}`), node('span', `A / U = ${slot.avail} / ${slot.used}`),
          node('span', `Buffer ID：${slot.id === null ? '—' : slot.id}`),
          node('span', slot.request === null ? '尚未写入' : `请求 ${slot.request} · len ${slot.length} B`),
          node('span', { free: '空闲，可重新发布', available: '已发布，等待设备', completed: '已完成，等待回收' }[slot.state]));
        slots.append(element);
      });
      else state.descriptors.forEach((desc, index) => {
        const element = node('div', '');
        element.className = `packed-lab-slot is-${desc.state}`;
        element.append(node('strong', `desc[${index}]`),
          node('span', desc.request === null ? 'addr / len 尚未准备' : `addr → B${index} · len ${desc.length} B`),
          node('span', desc.request === null ? 'flags：—' : 'WRITE · 无 NEXT'),
          node('span', desc.request === null ? '尚未写入' : `请求 ${desc.request}`),
          node('span', { free: '空闲，可重新发布', available: '已发布，等待设备', completed: '已完成，等待回收' }[desc.state]));
        slots.append(element);
      });
      rings.hidden = format !== 'split';
      rings.replaceChildren();
      if (format === 'split') {
        [['Available · 驱动发布', state.available, state.published, state.completed],
          ['Used · 设备发布完成', state.used, state.completed, state.reclaimed]].forEach(([label, entries, produced, consumed]) => {
          const group = node('section', ''), row = node('div', '');
          group.append(node('h4', `${label} · idx ${produced % 65536}`));
          row.className = 'split-lab-ring';
          entries.forEach((entry, index) => {
            const fresh = Array.from({ length: produced - consumed }, (_, offset) => (consumed + offset) % SIZE).includes(index);
            const element = node('div', '');
            element.className = `packed-lab-slot${fresh ? label.startsWith('Used') ? ' is-completed' : ' is-available' : ''}`;
            element.append(node('strong', `槽 ${index}`), node('span', entry === null ? '—' : typeof entry === 'number' ? `head ${entry}` : `id ${entry.id} · len ${entry.length}`),
              node('span', entry === null ? '尚未写入' : fresh ? '待消费' : '已消费的旧值'));
            row.append(element);
          });
          group.append(row);rings.append(group);
        });
      }
      lab.querySelector('[data-packed-action="publish"]').disabled = state.published - state.reclaimed === SIZE;
      lab.querySelector('[data-packed-action="complete"]').disabled = state.completed === state.published;
      lab.querySelector('[data-packed-action="reclaim"]').disabled = state.reclaimed === state.completed;
    }
    controls.hidden = false;
    formats.hidden = false;
    formats.addEventListener('click', (event) => {
      const button = event.target.closest('[data-queue-format]');
      if (!button) return;
      format = button.dataset.queueFormat;
      render();
    });
    controls.addEventListener('click', (event) => {
      const button = event.target.closest('[data-packed-action]');
      if (!button || button.disabled) return;
      packedState = advance(packedState, button.dataset.packedAction);
      splitState = advanceSplit(splitState, button.dataset.packedAction);
      render();
    });
    render();
  });
}());
