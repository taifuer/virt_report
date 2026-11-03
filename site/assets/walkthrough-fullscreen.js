/* Shared fullscreen controls. Move no DOM, preserve scenario and playback state. */
(function () {
  'use strict';
  const labs = [...document.querySelectorAll('[data-walkthrough-lab]')];
  if (!labs.length) return;
  let active = null;
  const attrNames = ['role', 'aria-modal', 'aria-label'];

  function notify(lab, type) {
    lab.dispatchEvent(new CustomEvent(type, {bubbles: true}));
  }

  function sizeWorkspace(lab) {
    if (!lab.classList.contains('walkthrough-expanded')) return;
    const workspace = lab.querySelector('.walkthrough-workspace');
    if (!workspace) return;
    // Step rows vary with scenario, view, viewport and font size. Measure the
    // actual control area instead of assuming a single fixed-height strip.
    const top = workspace.getBoundingClientRect().top - lab.getBoundingClientRect().top + lab.scrollTop;
    const padding = parseFloat(getComputedStyle(lab).paddingBottom) || 0;
    const remaining = lab.clientHeight - top - padding;
    // On short screens keep a readable diagram and let the expanded page scroll.
    const height = window.innerWidth >= 1100 && remaining >= 260 ? `${Math.floor(remaining)}px` : 'none';
    lab.style.setProperty('--walkthrough-workspace-height', height);
  }

  const scheduled = new WeakSet();
  function scheduleLayout(lab) {
    if (typeof requestAnimationFrame !== 'function' || scheduled.has(lab)) return;
    scheduled.add(lab);
    requestAnimationFrame(() => { scheduled.delete(lab); sizeWorkspace(lab); });
  }

  function focusable(lab) {
    return [...lab.querySelectorAll('button, a[href], input, select, textarea, [tabindex]')]
      .filter(node => !node.disabled && node.tabIndex >= 0 && !node.closest('[inert]')
        && node.getClientRects().length);
  }

  function setButtons(record, expanded) {
    record.buttons.forEach(button => {
      button.setAttribute('aria-pressed', String(expanded));
      button.textContent = expanded ? '退出全屏' : '全屏演示';
    });
  }

  function restoreOutside(record) {
    for (const [node, original] of record.outside) {
      if (original === null) node.removeAttribute('inert');
      else node.setAttribute('inert', original);
    }
    record.outside = [];
  }

  function finish(record) {
    if (active !== record) return;
    active = null;
    record.lab.classList.remove('walkthrough-expanded');
    record.lab.style?.removeProperty('--walkthrough-workspace-height');
    record.lab.removeAttribute('data-walkthrough-expanded');
    restoreOutside(record);
    if (record.mode === 'fallback') {
      for (const [name, value] of record.attributes) {
        if (value === null) record.lab.removeAttribute(name);
        else record.lab.setAttribute(name, value);
      }
      if (!record.hadModalClass) document.documentElement.classList.remove('walkthrough-modal-open');
    }
    setButtons(record, false);
    notify(record.lab, 'walkthrough:resize');
    if (record.opener?.isConnected && !record.opener.closest('[inert]')) {
      record.opener.focus({preventScroll: true});
    }
    window.scrollTo({left: record.scrollX, top: record.scrollY, behavior: 'instant'});
  }

  function display(record, mode) {
    if (active !== record || record.mode) return;
    record.mode = mode;
    record.lab.classList.add('walkthrough-expanded');
    record.lab.setAttribute('data-walkthrough-expanded', mode);
    if (mode === 'fallback') {
      record.attributes = attrNames.map(name => [name, record.lab.getAttribute(name)]);
      record.lab.setAttribute('role', 'dialog');
      record.lab.setAttribute('aria-modal', 'true');
      if (!record.lab.hasAttribute('aria-label') && !record.lab.hasAttribute('aria-labelledby')) {
        record.lab.setAttribute('aria-label', '交互演示');
      }
      record.hadModalClass = document.documentElement.classList.contains('walkthrough-modal-open');
      document.documentElement.classList.add('walkthrough-modal-open');
      // Each ancestor remains usable; only siblings outside the active lab are inert.
      let branch = record.lab;
      while (branch.parentElement) {
        const parent = branch.parentElement;
        for (const sibling of parent.children) {
          if (sibling !== branch) {
            record.outside.push([sibling, sibling.getAttribute('inert')]);
            sibling.setAttribute('inert', '');
          }
        }
        if (parent === document.body) break;
        branch = parent;
      }
    }
    setButtons(record, true);
    notify(record.lab, 'walkthrough:resize');
    scheduleLayout(record.lab);
    record.buttons[0]?.focus({preventScroll: true});
  }

  async function close(record) {
    if (active !== record || record.closing) return;
    record.closing = true;
    if (record.mode === 'native' && document.fullscreenElement === record.lab) {
      try {
        await document.exitFullscreen();
      } catch (error) {
        // Keep the visible controls in sync if the browser refuses an exit.
        record.closing = false;
        return;
      }
      if (document.fullscreenElement === record.lab) { record.closing = false; return; }
    }
    finish(record);
  }

  async function open(lab, buttons, opener) {
    if (active) {
      if (active.lab === lab && active.mode) await close(active);
      return;
    }
    // Do not replace another component's active native fullscreen session.
    if (document.fullscreenElement) return;
    const record = {lab, buttons, opener, mode: null, closing: false,
      outside: [], attributes: [], hadModalClass: false,
      scrollX: window.scrollX, scrollY: window.scrollY};
    active = record;
    notify(lab, 'walkthrough:expand');
    try {
      if (!lab.requestFullscreen) throw new Error('Fullscreen unavailable');
      await lab.requestFullscreen();
      if (active !== record) return;
      if (document.fullscreenElement === lab) display(record, 'native');
      else display(record, 'fallback');
    } catch (error) {
      if (active === record) display(record, 'fallback');
    }
  }

  document.addEventListener('fullscreenchange', () => {
    if (!active) return;
    if (document.fullscreenElement === active.lab) display(active, 'native');
    else if (active.mode === 'native') finish(active);
  });
  document.addEventListener('keydown', event => {
    const record = active;
    if (!record?.mode) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      close(record);
    } else if (event.key === 'Tab') {
      const controls = focusable(record.lab);
      const first = controls[0], last = controls.at(-1);
      if (!first) { event.preventDefault(); return; }
      if (!record.lab.contains(document.activeElement)) {
        event.preventDefault(); (event.shiftKey ? last : first).focus();
      } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault(); last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault(); first.focus();
      }
    }
  }, true);
  document.addEventListener('focusin', event => {
    if (active?.mode === 'fallback' && !active.lab.contains(event.target)) {
      (focusable(active.lab)[0] || active.buttons[0])?.focus({preventScroll: true});
    }
  });
  labs.forEach(lab => {
    if (typeof ResizeObserver === 'function') {
      const observer = new ResizeObserver(() => scheduleLayout(lab));
      observer.observe(lab);
      lab.querySelectorAll('.walkthrough-selection, .walkthrough-toolbar, .walkthrough-progress')
        .forEach(node => observer.observe(node));
    }
    lab.addEventListener('walkthrough:resize', () => scheduleLayout(lab));
    lab.addEventListener('change', () => scheduleLayout(lab));
    const buttons = [...lab.querySelectorAll('[data-walkthrough-expand]')];
    buttons.forEach(button => {
      button.hidden = false;
      button.setAttribute('aria-pressed', 'false');
      button.addEventListener('click', () => open(lab, buttons, button));
    });
  });
})();
