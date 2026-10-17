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
  if (typeof module !== 'undefined' && module.exports) module.exports = { createState, advance, position, SIZE };
  if (typeof document === 'undefined') return;
  document.querySelectorAll('[data-packed-lab]').forEach((lab) => {
    let state = createState();
    const controls = lab.querySelector('.packed-lab-controls');
    const slots = lab.querySelector('[data-packed-slots]');
    const cursors = lab.querySelector('[data-packed-cursors]');
    const node = (tag, value) => { const element = document.createElement(tag); element.textContent = value; return element; };
    function render() {
      lab.querySelector('[data-packed-status]').textContent = state.message;
      cursors.replaceChildren();
      [['驱动下次发布', state.published], ['设备下次完成', state.completed], ['驱动下次回收', state.reclaimed]].forEach(([label, count]) => {
        const at = position(count);
        cursors.append(node('span', `${label}：槽 ${at.index} · wrap ${at.wrap}`));
      });
      slots.replaceChildren();
      state.slots.forEach((slot, index) => {
        const element = node('div', '');
        element.className = `packed-lab-slot is-${slot.state}`;
        element.append(node('strong', `槽 ${index}`), node('span', `A / U = ${slot.avail} / ${slot.used}`),
          node('span', `Buffer ID：${slot.id === null ? '—' : slot.id}`),
          node('span', slot.request === null ? '尚未写入' : `请求 ${slot.request} · len ${slot.length} B`),
          node('span', { free: '空闲，可重新发布', available: '已发布，等待设备', completed: '已完成，等待回收' }[slot.state]));
        slots.append(element);
      });
      lab.querySelector('[data-packed-action="publish"]').disabled = state.published - state.reclaimed === SIZE;
      lab.querySelector('[data-packed-action="complete"]').disabled = state.completed === state.published;
      lab.querySelector('[data-packed-action="reclaim"]').disabled = state.reclaimed === state.completed;
    }
    controls.hidden = false;
    controls.addEventListener('click', (event) => {
      const button = event.target.closest('[data-packed-action]');
      if (!button || button.disabled) return;
      state = advance(state, button.dataset.packedAction);
      render();
    });
    render();
  });
}());
