/* Progressive disclosure for the article index; links remain usable without JS. */
(function () {
  'use strict';
  const root = document.querySelector('.mechanisms-index');
  if (!root) return;
  const panels = new Map();
  function expand(topic, opened) {
    const panel = panels.get(topic);
    if (!panel) return;
    const {button, list} = panel;
    if (!opened && list.contains(document.activeElement)) button.focus({preventScroll:true});
    list.hidden = !opened;
    button.setAttribute('aria-expanded', String(opened));
    const action = opened ? '收起' : '展开';
    button.title = `${action}${button.dataset.topicTitle}`;
    button.setAttribute('aria-label', `${action}${button.dataset.topicTitle}的 ${button.dataset.articleCount} 篇细分文章`);
  }
  root.querySelectorAll('[data-mechanism-toggle]').forEach(button => {
    const topic = button.closest('.mechanism-topic');
    const list = document.getElementById(button.getAttribute('aria-controls'));
    if (!topic || !list || !topic.contains(list)) return;
    panels.set(topic, {button, list});
    button.addEventListener('click', () => expand(topic, list.hidden));
    expand(topic, false);
    button.hidden = false;
  });
  function target(hash) {
    let id;
    try { id = decodeURIComponent(hash.slice(1)); } catch (_) { return null; }
    const topic = document.getElementById(id)?.closest('.mechanism-topic');
    return topic && root.contains(topic) ? topic : null;
  }
  let pendingScroll;
  function reveal(hash, focus = false) {
    const topic = target(hash);
    if (!topic) return;
    expand(topic, true);
    if (focus) {
      topic.tabIndex = -1;
      topic.focus({preventScroll:true});
    }
    cancelAnimationFrame(pendingScroll);
    pendingScroll = requestAnimationFrame(() => {
      topic.scrollIntoView({block:'start', behavior:'instant'});
    });
  }
  root.querySelector('.mechanism-directory')?.addEventListener('click', event => {
    if (event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const link = event.target.closest('a[href^="#"]');
    if (!link || !target(link.hash)) return;
    event.preventDefault();
    // Native fragment history preserves back/forward navigation; same-hash clicks
    // still reopen a topic that the reader has subsequently collapsed.
    if (location.hash !== link.hash) location.hash = link.hash;
    reveal(link.hash, true);
  });
  window.addEventListener('hashchange', () => reveal(location.hash));
  window.addEventListener('pageshow', event => { if (event.persisted) reveal(location.hash); });
  reveal(location.hash);
  root.dataset.directoryInitialized = 'true';
}());
