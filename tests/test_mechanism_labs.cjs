const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('virt_report/render/assets/mechanism-labs.js', 'utf8');

class Element {
  constructor(tag = 'div') { this.tag = tag; this.dataset = {}; this.children = []; this.events = {}; }
  setAttribute(name, value) { this[name] = value; }
  addEventListener(name, callback) { this.events[name] = callback; }
  append(...children) { children.forEach(child => { child.parentElement = this; this.children.push(child); }); }
  prepend(child) { child.parentElement = this; this.children.unshift(child); }
  remove() { this.removed = true; }
  querySelector() { return this.focusTarget; }
}
function harness({search = '', fail = '', observer = true, names = ['model.js', 'ui.js']} = {}) {
  const lab = new Element(), resource = new Element(), loaded = [], observed = [], events = {};
  const config = {dataset: {assetRoot: '../assets/', assetVersion: 'test'}};
  const manifest = {content: {querySelectorAll: () => names.map(name => ({dataset: {labName: name}}))}};
  const document = {
    activeElement: null,
    querySelector: () => config,
    querySelectorAll: selector => selector === '[data-walkthrough-lab]' ? [lab] : [resource],
    getElementById: () => manifest,
    createElement: tag => new Element(tag),
    body: {append(node) {
      const name = new URL(node.src).pathname.split('/').pop();
      loaded.push(name);
      queueMicrotask(() => { if (fail === name) { fail = ''; node.onerror(); } else node.onload(); });
    }},
  };
  lab.focusTarget = {focus() { document.activeElement = this; }};
  let intersect, disconnected = false;
  class Observer {
    constructor(callback) { intersect = callback; }
    observe(node) { observed.push(node); }
    disconnect() { disconnected = true; }
  }
  const window = {addEventListener(name, callback) { events[name] = callback; }};
  if (observer) window.IntersectionObserver = Observer;
  const location = {href: 'https://site.test/mechanisms/article.html', origin: 'https://site.test', search, hash: ''};
  vm.runInNewContext(source, {document, window, location, URL, IntersectionObserver: Observer});
  return {lab, resource, loaded, observed, document, location, events,
    visible() { intersect([{isIntersecting: true}]); },
    disconnected: () => disconnected,
    button: lab.children[0]?.children[0], status: lab.children[0]?.children[1]};
}
const settle = () => new Promise(resolve => setImmediate(resolve));

test('viewport loading is ordered, single-use and includes preceding small widgets', async () => {
  const h = harness();
  assert.deepEqual(h.loaded, []);
  assert.deepEqual(h.observed, [h.lab, h.resource]);
  assert.equal(h.lab.dataset.labPending, 'true');
  h.visible(); await settle();
  assert.deepEqual(h.loaded, ['model.js', 'ui.js']);
  assert.equal(h.disconnected(), true);
  assert.equal(h.lab.dataset.labPending, undefined);
  assert.equal(h.lab.children[0].removed, true);
  await h.button.events.click();
  assert.deepEqual(h.loaded, ['model.js', 'ui.js']);
});

test('failed download restores fallback, retries only unfinished scripts and transfers focus', async () => {
  const h = harness({fail: 'ui.js', observer: false});
  h.document.activeElement = h.button;
  await h.button.events.click();
  assert.deepEqual(h.loaded, ['model.js', 'ui.js']);
  assert.equal(h.lab.dataset.labPending, undefined);
  assert.equal(h.button.disabled, false);
  assert.equal(h.button.textContent, '重试载入');
  assert.match(h.status.textContent, /仍可阅读/);
  await h.button.events.click();
  assert.deepEqual(h.loaded, ['model.js', 'ui.js', 'ui.js']);
  assert.equal(h.document.activeElement, h.lab.focusTarget);
});

test('scenario bookmarks initialize without waiting for scrolling', async () => {
  const h = harness({search: '?scenario=mmio'});
  await settle();
  assert.deepEqual(h.loaded, ['model.js', 'ui.js']);
  const later = harness(); later.location.hash = '#walkthrough';
  later.events.hashchange(); await settle();
  assert.deepEqual(later.loaded, ['model.js', 'ui.js']);
});

test('malformed asset names leave static content usable', async () => {
  const h = harness({names: ['../escape.js']}); await settle();
  assert.equal(h.lab.dataset.labPending, undefined);
  assert.deepEqual(h.loaded, []);
  assert.equal(h.lab.children.length, 0);
});
