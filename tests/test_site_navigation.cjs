/* Exercise the shared header with a minimal DOM and deterministic timers. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const script = fs.readFileSync(path.join(__dirname, '../virt_report/render/assets/site.js'), 'utf8')
  .split('const reportToc=')[0];

function harness({ mobile = false, hover = true } = {}) {
  const listeners = new Map(), timers = new Map();
  let timerId = 0;
  function element(parent = null) {
    const classes = new Set(), attrs = new Map();
    const node = {
      parent, open: false,
      classList: {
        add: name => classes.add(name),
        contains: name => classes.has(name),
        toggle(name, value) { value ? classes.add(name) : classes.delete(name); },
      },
      setAttribute: (name, value) => attrs.set(name, value),
      getAttribute: name => attrs.get(name),
      contains(target) {
        for (let current = target; current; current = current.parent) if (current === node) return true;
        return false;
      },
      addEventListener(type, callback) {
        if (!listeners.has(node)) listeners.set(node, new Map());
        const events = listeners.get(node);
        events.set(type, [...(events.get(type) || []), callback]);
      },
      focus() { document.activeElement = node; },
      querySelector() { return null; },
      closest() { return null; },
    };
    return node;
  }
  const document = element(), header = element(), toggle = element(header);
  const navigation = element(header), reports = element(navigation), summary = element(reports);
  const link = element(reports), outside = element(), desktopLink = element(navigation);
  const mobileMedia = element(), hoverMedia = element();
  mobileMedia.matches = mobile;
  hoverMedia.matches = hover;
  document.activeElement = outside;
  document.querySelector = selector => selector === '.site-head' ? header : null;
  header.querySelector = selector => ({
    '[data-site-menu-toggle]': toggle, '#site-navigation': navigation, '[data-report-menu]': reports,
  })[selector];
  reports.querySelector = selector => selector === 'summary' ? summary : null;
  navigation.querySelector = () => desktopLink;
  const window = {
    matchMedia: query => query.includes('max-width') ? mobileMedia : hoverMedia,
    setTimeout: callback => { const id = ++timerId; timers.set(id, callback); return id; },
    clearTimeout: id => timers.delete(id),
    requestAnimationFrame: callback => callback(),
  };
  vm.runInNewContext(script, { document, window });
  function dispatch(node, type, data = {}) {
    const event = {
      target: node, pointerType: 'mouse', detail: 0, defaultPrevented: false,
      preventDefault() { this.defaultPrevented = true; }, ...data,
    };
    for (const listener of listeners.get(node)?.get(type) || []) listener(event);
    if (node === summary && type === 'click' && !event.defaultPrevented) reports.open = !reports.open;
    return event;
  }
  function flushTimers() {
    const pending = [...timers.values()];
    timers.clear();
    pending.forEach(callback => callback());
  }
  return { document, header, toggle, navigation, reports, summary, link, outside,
    mobileMedia, hoverMedia, dispatch, flushTimers };
}

test('desktop hover opens, tolerates the popup gap, and closes after leaving', () => {
  const h = harness();
  h.dispatch(h.reports, 'pointerenter');
  assert.equal(h.reports.open, true);
  h.dispatch(h.reports, 'pointerleave');
  assert.equal(h.reports.open, true);
  h.dispatch(h.reports, 'pointerenter');
  h.flushTimers();
  assert.equal(h.reports.open, true);
  h.dispatch(h.reports, 'pointerleave');
  h.flushTimers();
  assert.equal(h.reports.open, false);
});

test('the first click on a hover-opened summary does not close the choices', () => {
  const h = harness();
  h.dispatch(h.reports, 'pointerenter');
  assert.equal(h.dispatch(h.summary, 'click', { detail: 1 }).defaultPrevented, true);
  assert.equal(h.reports.open, true);
  h.dispatch(h.summary, 'click', { detail: 1 });
  assert.equal(h.reports.open, false);
});

test('leaving with a keyboard focus inside does not hide the active link', () => {
  const h = harness();
  h.dispatch(h.reports, 'pointerenter');
  h.link.focus();
  h.dispatch(h.reports, 'pointerleave');
  h.flushTimers();
  assert.equal(h.reports.open, true);
  h.outside.focus();
  h.dispatch(h.header, 'focusout');
  assert.equal(h.reports.open, false);
});

test('Escape, outside clicks and keyboard toggling remain available', () => {
  const h = harness();
  h.dispatch(h.reports, 'pointerenter');
  h.dispatch(h.document, 'keydown', { key: 'Escape' });
  assert.equal(h.reports.open, false);
  assert.equal(h.document.activeElement, h.summary);
  h.dispatch(h.summary, 'click');
  assert.equal(h.reports.open, true);
  h.dispatch(h.summary, 'click');
  assert.equal(h.reports.open, false);
  h.dispatch(h.summary, 'click');
  h.dispatch(h.document, 'click', { target: h.outside });
  assert.equal(h.reports.open, false);
});

test('mobile and non-hover devices retain native click disclosure', () => {
  for (const options of [{ mobile: true }, { hover: false }]) {
    const h = harness(options);
    h.dispatch(h.reports, 'pointerenter');
    assert.equal(h.reports.open, false);
    h.dispatch(h.summary, 'click', { detail: 1 });
    assert.equal(h.reports.open, true);
    h.dispatch(h.summary, 'click', { detail: 1 });
    assert.equal(h.reports.open, false);
  }
  const h = harness();
  h.dispatch(h.reports, 'pointerenter', { pointerType: 'touch' });
  assert.equal(h.reports.open, false);
});

test('switching to mobile clears hover state and pending closing timers', () => {
  const h = harness();
  h.dispatch(h.reports, 'pointerenter');
  h.dispatch(h.reports, 'pointerleave');
  h.mobileMedia.matches = true;
  h.dispatch(h.mobileMedia, 'change');
  assert.equal(h.reports.open, false);
  h.dispatch(h.summary, 'click', { detail: 1 });
  h.flushTimers();
  assert.equal(h.reports.open, true);
});
