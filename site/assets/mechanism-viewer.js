/* Local full-window reading: preserve demo DOM/state; zoom diagrams without clipping. */
(function () {
  'use strict';
  const dialog = document.createElement('dialog');
  if (typeof dialog.showModal !== 'function') return;
  dialog.className = 'mechanism-viewer';
  dialog.setAttribute('aria-labelledby', 'mechanism-viewer-title');
  const bar = document.createElement('div');
  bar.className = 'mechanism-viewer-bar';
  const title = document.createElement('strong');
  title.id = 'mechanism-viewer-title';
  const zoom = document.createElement('div');
  zoom.className = 'mechanism-viewer-zoom';
  zoom.setAttribute('role', 'group');
  zoom.setAttribute('aria-label', '图解缩放');
  const viewport = document.createElement('div');
  viewport.className = 'mechanism-viewer-viewport';
  viewport.tabIndex = 0;
  viewport.setAttribute('aria-label', '全屏内容，可滚动查看');
  const stage = document.createElement('div');
  stage.className = 'mechanism-viewer-stage';
  viewport.append(stage);
  function button(label, name, parent, action) {
    const node = document.createElement('button');
    node.type = 'button'; node.textContent = label;
    node.dataset.viewerAction = name;
    node.addEventListener('click', action);
    parent.append(node);
    return node;
  }
  let active = null, image = null, scale = 1, mode = 'fit', savedOverflow = '';
  const fit = button('适应窗口', 'fit', zoom, () => resizeImage('fit'));
  const width = button('适应宽度', 'width', zoom, () => resizeImage('width'));
  const minus = button('−', 'out', zoom, () => resizeImage('manual', scale / 1.25));
  minus.setAttribute('aria-label', '缩小图解');
  const percent = document.createElement('output');
  percent.setAttribute('aria-label', '缩放比例'); percent.setAttribute('aria-live', 'polite');
  zoom.append(percent);
  const plus = button('+', 'in', zoom, () => resizeImage('manual', scale * 1.25));
  plus.setAttribute('aria-label', '放大图解');
  button('100%', 'actual', zoom, () => resizeImage('manual', 1));
  bar.append(title, zoom);
  const close = button('退出全屏', 'close', bar, () => dialog.close());
  dialog.append(bar, viewport);

  function resizeImage(nextMode = mode, requested = scale) {
    if (!image || !image.naturalWidth || !dialog.open) return;
    const previous = scale;
    const x = (viewport.scrollLeft + viewport.clientWidth / 2) / previous;
    const y = (viewport.scrollTop + viewport.clientHeight / 2) / previous;
    mode = nextMode;
    const availableWidth = Math.max(1, viewport.clientWidth - 32);
    const availableHeight = Math.max(1, viewport.clientHeight - 32);
    scale = mode === 'fit' ? Math.min(availableWidth / image.naturalWidth, availableHeight / image.naturalHeight, 1)
      : mode === 'width' ? availableWidth / image.naturalWidth : Math.max(0.125, Math.min(3, requested));
    image.style.width = `${image.naturalWidth * scale}px`;
    image.style.height = `${image.naturalHeight * scale}px`;
    percent.value = `${Math.round(scale * 100)}%`;
    minus.disabled = scale <= 0.125; plus.disabled = scale >= 3;
    fit.setAttribute('aria-pressed', String(mode === 'fit'));
    width.setAttribute('aria-pressed', String(mode === 'width'));
    if (mode === 'manual') {
      viewport.scrollLeft = x * scale - viewport.clientWidth / 2;
      viewport.scrollTop = y * scale - viewport.clientHeight / 2;
    } else { viewport.scrollLeft = 0; viewport.scrollTop = 0; }
  }
  function pauseForLayout() {
    // Demo coordinates are computed from element bounds; stop an unfinished step.
    if (!active || active.kind !== 'lab') return;
    const streamPlay = active.target.querySelector('[data-stream-play][aria-pressed="true"]');
    if (streamPlay) streamPlay.click();
    window.dispatchEvent(new Event('resize'));
  }
  function open(target, opener) {
    if (active) return;
    const kind = target.dataset.mechanismView;
    const parent = target.parentElement;
    const marker = document.createComment('expanded mechanism position');
    target.before(marker);
    active = {target, opener, marker, kind, x: window.scrollX, y: window.scrollY};
    title.textContent = target.querySelector('[data-mechanism-title], h3')?.textContent || (kind === 'diagram' ? '图解' : '交互演示');
    dialog.dataset.kind = kind;
    zoom.hidden = kind !== 'diagram';
    savedOverflow = document.documentElement.style.overflow;
    document.documentElement.style.overflow = 'hidden';
    // Keep article typography without duplicating the interactive DOM or its IDs.
    parent.append(dialog);
    if (kind === 'diagram') {
      scale = 1; mode = 'fit';
      image = target.querySelector('img').cloneNode(false);
      image.removeAttribute('id'); image.removeAttribute('loading');
      image.className = 'mechanism-viewer-image'; image.draggable = false;
      stage.append(image);
      const openedImage = image;
      image.addEventListener('load', () => { if (image === openedImage) resizeImage(mode, scale); }, {once: true});
    } else {
      target.classList.add('is-expanded'); stage.append(target);
    }
    dialog.showModal();
    viewport.scrollTop = 0; viewport.scrollLeft = 0;
    if (image) resizeImage('fit');
    pauseForLayout();
    close.focus({preventScroll: true});
  }
  dialog.addEventListener('close', () => {
    if (!active) return;
    const previous = active;
    pauseForLayout();
    if (previous.kind === 'lab') {
      previous.marker.replaceWith(previous.target);
      previous.target.classList.remove('is-expanded');
    } else previous.marker.remove();
    stage.replaceChildren(); image = null;
    active = null; dialog.remove();
    document.documentElement.style.overflow = savedOverflow;
    window.dispatchEvent(new Event('resize'));
    previous.opener.focus({preventScroll: true});
    window.scrollTo({left: previous.x, top: previous.y, behavior: 'instant'});
  });
  window.addEventListener('resize', () => { if (image && mode !== 'manual') resizeImage(mode); });
  document.querySelectorAll('[data-mechanism-view]').forEach(target => {
    const opener = target.querySelector('[data-mechanism-expand]');
    if (!opener) return;
    opener.hidden = false;
    opener.setAttribute('aria-haspopup', 'dialog');
    opener.addEventListener('click', () => open(target, opener));
    if (target.dataset.mechanismView === 'diagram') {
      const picture = target.querySelector('img');
      picture.style.cursor = 'zoom-in';
      picture.addEventListener('click', () => open(target, opener));
    }
  });
})();
