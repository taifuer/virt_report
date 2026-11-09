/* Offline editorial search. Community report queries remain server-side. */
(function () {
  'use strict';
  const clean = value => String(value || '').replace(/\0/g, ' ').trim().replace(/\s+/gu, ' ');
  function find(catalogue, input) {
    const query = clean(input), result = {query, articles: [], demos: [], papers: [], versions: [], error: ''};
    if (!query) return result;
    if ([...query].length < 2 || [...query].length > 100) {
      result.error = [...query].length < 2 ? '请至少输入 2 个字符。' : '搜索关键词请控制在 100 个字符以内。';
      return result;
    }
    const groups = [...new Set(query.toLowerCase().split(' '))].slice(0, 8)
      .map(term => [term, ...(Object.hasOwn(catalogue.aliases, term) ? catalogue.aliases[term] : [])]);
    const matches = [];
    for (const doc of catalogue.documents) {
      const fields = [[doc.title, 8], [doc.summary, 4], [doc.headings.join(' '), 2], [doc.keywords, 1]];
      const scores = groups.map(terms => Math.max(0, ...fields.filter(([text]) =>
        terms.some(term => text.toLowerCase().includes(term))).map(([, weight]) => weight)));
      if (scores.some(score => !score)) continue;
      const terms = groups.flat();
      const matched_headings = doc.headings.filter(text => terms.some(term => text.toLowerCase().includes(term))).slice(0, 2);
      matches.push({score: scores.reduce((a, b) => a + b, 0), doc: {...doc, matched_headings}});
    }
    matches.sort((a, b) => b.score - a.score).forEach(({doc}) => result[doc.kind].push(doc));
    return result;
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {find};
  if (typeof document === 'undefined') return;
  const root = document.querySelector('[data-editorial-results]');
  if (!root || root.dataset.static !== 'true') return; // Dynamic responses already include results without JS.
  const catalogue = JSON.parse(document.querySelector('#editorial-search-data').textContent);
  const form = document.querySelector('[data-search-form]'), input = form.elements.q;
  const clear = form.querySelector('[data-search-clear]');
  const notice = document.querySelector('[data-editorial-error]');
  const status = document.querySelector('[data-editorial-status]');
  const element = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  function update() {
    const result = find(catalogue, input.value);
    notice.textContent = result.error;
    notice.hidden = !result.error;
    root.hidden = !result.query || !!result.error;
    clear.hidden = !input.value;
    const examples = form.querySelector('.search-examples');
    if (examples) examples.hidden = !!result.query;
    for (const kind of ['articles', 'demos', 'papers', 'versions']) {
      const section = root.querySelector(`[data-editorial-kind="${kind}"]`);
      section.querySelector('[data-editorial-count]').textContent = String(result[kind].length);
      const list = section.querySelector('[data-editorial-list]');
      list.replaceChildren();
      section.querySelector('[data-editorial-empty]').hidden = !!result[kind].length;
      let more;
      for (const [i, item] of result[kind].entries()) {
        if (i === 5 && ['papers', 'versions'].includes(kind)) {
          more = element('details', '', 'editorial-more');
          more.append(element('summary', `其余 ${result[kind].length - 5} 条结果`));
          list.append(more);
        }
        const card = element('article', '', 'search-result');
        const heading = element('h3', '', 'search-result-title');
        const link = element('a', item.title);
        link.setAttribute('href', item.href);
        heading.append(link);
        card.append(heading);
        if (item.meta) card.append(element('p', item.meta, 'editorial-match'));
        card.append(element('p', item.summary, 'search-summary'));
        if (item.matched_headings.length) card.append(element('p', (item.heading_label || '相关章节 / 场景') + '：' + item.matched_headings.join(' · '), 'editorial-match'));
        (more || list).append(card);
      }
    }
    status.textContent = result.error || (result.query ? `原理讲解 ${result.articles.length} 篇，交互演示 ${result.demos.length} 篇，论文 ${result.papers.length} 篇，版本与规范 ${result.versions.length} 条。` : '');
  }
  function fromURL() {
    input.value = new URL(location.href).searchParams.get('q') || '';
    update();
  }
  form.addEventListener('submit', event => {
    event.preventDefault();
    const url = new URL(form.action);
    if (input.value.trim()) url.searchParams.set('q', input.value.trim());
    history.pushState(null, '', url);
    update();
  });
  input.addEventListener('input', () => { clear.hidden = !input.value; });
  clear.addEventListener('click', () => { input.value = ''; form.requestSubmit(); input.focus(); });
  addEventListener('popstate', fromURL);
  fromURL();
})();
