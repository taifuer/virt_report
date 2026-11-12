/* Load interactive code when an embedded example approaches the viewport. */
(() => {
  'use strict';
  const config = document.querySelector('[data-lab-loader]');
  const manifest = document.getElementById('mechanism-lab-assets');
  const labs = [...document.querySelectorAll('[data-walkthrough-lab]')];
  if (!config || !manifest || !labs.length) return;
  const names = [...manifest.content.querySelectorAll('script[data-lab-name]')].map(node => node.dataset.labName);
  if (!Array.isArray(names) || !names.every(name => /^[a-z0-9-]+\.js$/.test(name))) return;
  const base = new URL(config.dataset.assetRoot, location.href);
  if (base.origin !== location.origin) return;
  let loading = null;
  const loaded = new Set(), placeholders = [];
  for (const lab of labs) {
    lab.dataset.labPending = 'true';
    const placeholder = document.createElement('div');
    placeholder.className = 'mechanism-lab-placeholder';
    const button = document.createElement('button');
    button.type = 'button'; button.textContent = '载入交互演示';
    button.addEventListener('click', load);
    const status = document.createElement('span');
    status.setAttribute('role', 'status');
    placeholder.append(button, status); lab.prepend(placeholder);
    placeholders.push({placeholder, button, status});
  }
  function script(name) {
    if (loaded.has(name)) return Promise.resolve();
    return new Promise((resolve, reject) => {
      const node = document.createElement('script');
      node.src = new URL(name, base).href + '?v=' + encodeURIComponent(config.dataset.assetVersion);
      node.onload = () => { loaded.add(name); resolve(); };
      node.onerror = () => { node.remove(); reject(new Error('Script unavailable')); };
      document.body.append(node);
    });
  }
  function load() {
    if (loading) return loading;
    placeholders.forEach(({button, status}) => { button.disabled = true; status.textContent = '正在载入…'; });
    loading = (async () => {
      try {
        for (const name of names) await script(name);
        labs.forEach(lab => { delete lab.dataset.labPending; });
        placeholders.forEach(({placeholder, button}) => {
          const lab = placeholder.parentElement;
          if (document.activeElement === button) lab.querySelector('select, [data-walkthrough-expand]')?.focus({preventScroll: true});
          placeholder.remove();
        });
      } catch (_) {
        labs.forEach(lab => { delete lab.dataset.labPending; });
        placeholders.forEach(({button, status}) => {
          button.disabled = false; button.textContent = '重试载入';
          status.textContent = '交互暂未载入，仍可阅读下方完整流程。';
        });
        loading = null;
      }
    })();
    return loading;
  }
  if ('IntersectionObserver' in window) {
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) { observer.disconnect(); load(); }
    }, {rootMargin: '350px'});
    // A small resource widget can precede the full execution example.
    // Its controls must become usable at its own position in the article.
    [...labs, ...document.querySelectorAll('[data-virtio-gpu-lab]')]
      .forEach(lab => observer.observe(lab));
  }
  // Shared scenario links and bookmarks must restore state before scrolling.
  if (location.search || location.hash) load();
  window.addEventListener('hashchange', () => { if (location.hash) load(); });
})();
