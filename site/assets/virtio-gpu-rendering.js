/* Source-backed teaching pictures; state comes from the shared walkthrough trace. */
(function () {
  'use strict';
  const lab = document.querySelector('[data-execution-lab]');
  if (!lab) return;
  const ns = 'http://www.w3.org/2000/svg';
  const phases = {
    empty: '未提交', encoded: '命令已编码', submitted: '已发布到 controlq',
    accepted: '宿主已接收', executed: 'GPU 工作已完成', complete: 'Guest 已确认完成'
  };
  const scenes = {
    'virgl-render': {
      input: ['顶点与绘制状态', 'vertices'],
      host: ['宿主颜色纹理', 'triangle'],
      output: ['可供合成的图像', 'triangle'],
      hostStatus: 'RGBA8 颜色目标已写入三角形',
      operation: '绘制三角形', boundary: '绘制结果可用，显示合成另行消费图像。'
    },
    'virgl-decode': {
      input: ['H.264 码流与帧参数', 'bitstream'],
      host: ['宿主 VA 解码表面', 'planes'],
      output: ['可供显示的 NV12 资源', 'planes'],
      hostStatus: 'VA surface 已解码为 NV12；纹理同步另行推进',
      operation: '解码一帧', boundary: '解码表面经过资源同步，再交给显示转换与合成。'
    },
    'virgl-encode': {
      input: ['NV12 图像与编码参数', 'planes'],
      host: ['宿主编码结果', 'bitstream'],
      output: ['Guest 可读取的压缩码流', 'bitstream'],
      hostStatus: 'VA coded buffer 已生成有效压缩数据',
      operation: '编码一帧', boundary: 'Guest 还需检查反馈状态、实际长度与输出容量。'
    }
  };
  let panel, previousScene, previousStep;
  const element = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  const svgNode = (name, attributes, text) => {
    const node = document.createElementNS(ns, name);
    Object.entries(attributes || {}).forEach(([key, value]) => node.setAttribute(key, value));
    if (text !== undefined) node.textContent = text;
    return node;
  };

  function picture(kind, available, title) {
    const svg = svgNode('svg', {viewBox: '0 0 240 104', role: 'img', 'aria-label': title});
    svg.classList.add('gpu-command-picture');
    svg.dataset.kind = kind;
    svg.dataset.ready = String(available);
    svg.append(svgNode('rect', {x: 6, y: 6, width: 228, height: 92, rx: 6, class: 'gpu-picture-frame'}));
    if (!available) {
      svg.append(svgNode('path', {d: 'M20 84 L58 46 L82 70 L126 26 L204 84', class: 'gpu-picture-placeholder'}));
      svg.append(svgNode('text', {x: 120, y: 58, 'text-anchor': 'middle', class: 'gpu-picture-empty'}, '等待本步资源'));
      return svg;
    }
    if (kind === 'triangle' || kind === 'vertices') {
      svg.append(svgNode('path', {
        d: 'M120 21 L179 83 L61 83 Z',
        class: kind === 'vertices' ? 'gpu-picture-vertices' : 'gpu-picture-triangle'
      }));
      if (kind === 'vertices') {
        [[120, 21], [179, 83], [61, 83]].forEach(([cx, cy], index) => {
          svg.append(svgNode('circle', {cx, cy, r: 4, class: 'gpu-picture-vertex'}));
          svg.append(svgNode('text', {x: cx + 10, y: cy - 3, class: 'gpu-picture-label'}, `V${index}`));
        });
      }
    } else if (kind === 'planes') {
      svg.append(svgNode('rect', {x: 27, y: 18, width: 120, height: 67, rx: 3, class: 'gpu-picture-y'}));
      svg.append(svgNode('text', {x: 87, y: 58, 'text-anchor': 'middle', class: 'gpu-picture-label'}, 'Y'));
      svg.append(svgNode('rect', {x: 156, y: 18, width: 57, height: 67, rx: 3, class: 'gpu-picture-uv'}));
      svg.append(svgNode('text', {x: 184, y: 58, 'text-anchor': 'middle', class: 'gpu-picture-label'}, 'UV'));
    } else {
      for (let row = 0; row < 3; row++) {
        for (let col = 0; col < 7; col++) {
          svg.append(svgNode('rect', {
            x: 27 + col * 27, y: 21 + row * 23, width: 20, height: 15, rx: 2,
            class: `gpu-picture-byte gpu-picture-byte-${(row + col) % 3}`
          }));
        }
      }
    }
    return svg;
  }

  function resource(position, specification, available, status, changed) {
    const node = element('section', undefined, 'gpu-command-resource');
    node.dataset.gpuResource = position;
    node.dataset.ready = String(available);
    node.dataset.changed = String(changed);
    const [title, kind] = specification;
    node.append(element('h5', title), picture(kind, available, `${title}：${available ? '已有本次数据' : '尚无本次结果'}`));
    node.append(element('p', status, 'gpu-command-resource-status'));
    return node;
  }

  function packet(label, value, key, changed) {
    const cell = element('section', undefined, 'gpu-command-record');
    cell.dataset.gpuPacket = key;
    cell.dataset.changed = String(changed);
    cell.append(element('h5', label), element('code', value || '—'));
    return cell;
  }

  function render(snapshot) {
    if (!snapshot || snapshot.demo.id !== 'virtio-gpu-rendering') return;
    const {scene, frame} = snapshot;
    const details = scenes[scene.id];
    const overview = lab.querySelector('[data-execution-overview]');
    if (!details || !overview) return;
    if (!panel) {
      panel = element('section', undefined, 'gpu-command-visual');
      panel.dataset.gpuCommandVisual = '';
      panel.setAttribute('aria-label', '命令格式与图像资源状态');
      overview.append(panel);
    }
    const state = frame.values;
    const moved = previousScene === scene.id && previousStep !== frame.id && frame.index > 0;
    const changed = (...keys) => moved && keys.some(key => frame.changed.includes(key));
    panel.dataset.scenario = scene.id;
    panel.dataset.commandPhase = state.commandPhase;
    panel.dataset.outputPhase = state.outputPhase;
    const heading = element('header', undefined, 'gpu-command-heading');
    heading.append(element('h4', '命令与图像资源'), element('span', phases[state.commandPhase] || '等待提交'));

    const commands = element('div', undefined, 'gpu-command-records');
    commands.append(
      packet('外层 · SUBMIT_3D 结构 · 32 B', state.submitHeader, 'header', changed('submitHeader')),
      packet('内层 · VirGL 命令批次', state.commandBatch, 'batch', changed('commandBatch')),
      packet('响应 · 控制头 · 24 B', state.response, 'response', changed('response'))
    );

    const resources = element('div', undefined, 'gpu-command-resources');
    const inputReady = state.inputPhase === 'ready';
    const hostReady = state.outputPhase === 'host-ready' || state.outputPhase === 'guest-ready';
    const guestReady = state.outputPhase === 'guest-ready';
    resources.append(resource('input', details.input, inputReady,
      inputReady ? state.inputResource : '尚未准备本次输入', changed('inputPhase', 'inputResource')));
    const firstArrow = element('div', undefined, 'gpu-command-resource-arrow');
    firstArrow.setAttribute('aria-hidden', 'true');
    firstArrow.append(element('span', '→'), element('small', details.operation));
    resources.append(firstArrow);
    resources.append(resource('host', details.host, hostReady,
      hostReady ? details.hostStatus : 'GPU 尚未产生本次结果', changed('outputPhase')));
    const secondArrow = element('div', undefined, 'gpu-command-resource-arrow');
    secondArrow.setAttribute('aria-hidden', 'true');
    secondArrow.append(element('span', '→'), element('small', '资源同步与完成通知'));
    resources.append(secondArrow);
    resources.append(resource('result', details.output, guestReady,
      guestReady ? (state.appResult === '待完成' ? '同步依赖已满足，等待应用消费' : state.appResult)
        : 'Guest 尚未确认结果可用', changed('outputPhase', 'appResult')));

    const completion = element('dl', undefined, 'gpu-command-completion');
    for (const [label, key] of [['fence', 'fenceId'], ['完成状态', 'fenceState'], ['结果', 'resultLabel']]) {
      const cell = element('div');
      cell.dataset.gpuCompletion = key;
      cell.dataset.changed = String(changed(key));
      cell.append(element('dt', label), element('dd', state[key] || '—'));
      completion.append(cell);
    }
    const note = element('p', `${details.boundary} 图形只标记资源状态，不表示像素布局、复制次数或执行耗时。`, 'gpu-command-note');
    panel.replaceChildren(heading, commands, resources, completion, note);
    previousScene = scene.id;
    previousStep = frame.id;
  }

  lab.addEventListener('execution:frame', event => render(event.detail));
  if (lab.executionSnapshot) render(lab.executionSnapshot);
})();
