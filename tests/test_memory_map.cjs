'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const model = require('../virt_report/render/assets/memory-map.js');

test('default view splits RAM around two MMIO leaves without copying RAM', () => {
  assert.deepEqual(model.flatten(model.topology()).map(r => [r.start, r.end, r.id, r.offset]), [
    [0, 0x2000, 'ram', 0], [0x2000, 0x3000, 'regs-a', 0],
    [0x3000, 0x4000, 'ram', 0x3000], [0x4000, 0x5000, 'regs-b', 0],
    [0x5000, 0x8000, 'ram', 0x5000]
  ]);
});
test('alias translates to leaf offset, holes fall back to RAM', () => {
  assert.deepEqual(model.inspect({}, '0x2004').hit, {id:'regs-a', kind:'mmio', offset:4});
  const gap = model.inspect({}, '0x3004');
  assert.equal(gap.hit.id, 'ram'); assert.equal(gap.hit.offset, 0x3004);
  assert.ok(gap.trace.some(line => line.includes('空洞')));
  assert.equal(model.inspect({}, '0x8000').hit, null);
});
test('disabled or lower priority window exposes a single continuous RAM range', () => {
  for (const config of [{enabled:false}, {priority:-1}]) {
    assert.deepEqual(model.flatten(model.topology(config)), [{start:0, end:0x8000, id:'ram', kind:'ram', offset:0}]);
    assert.equal(model.inspect(config, '0x2004').hit.offset, 0x2004);
  }
});
test('alias_offset moves visibility and clips devices outside the window', () => {
  assert.equal(model.inspect({offset:0}, '0x3004').hit.id, 'regs-a');
  assert.equal(model.inspect({offset:0}, '0x5004').hit.id, 'regs-b');
  assert.equal(model.inspect({offset:0x2000}, '0x3004').hit.id, 'regs-b');
  assert.ok(!model.flatten(model.topology({offset:0x2000})).some(r => r.id === 'regs-a'));
});
test('all configurations produce sorted, disjoint ranges with correct lookup offsets', () => {
  for (const offset of [0,0x1000,0x2000]) for (const priority of [2,-1]) for (const enabled of [true,false]) {
    const tree=model.topology({offset,priority,enabled}), ranges=model.flatten(tree);
    for (let i=0;i<ranges.length;i++) {
      const r=ranges[i]; assert.ok(r.end>r.start);
      if(i) assert.ok(ranges[i-1].end<=r.start);
    }
    for(let addr=0;addr<model.LIMIT;addr+=4) {
      const hit=model.resolve(tree,addr).hit, range=ranges.find(r=>r.start<=addr&&addr<r.end);
      assert.equal(Boolean(hit),Boolean(range));
      if(hit) {assert.equal(hit.id,range.id);assert.equal(hit.offset,range.offset+addr-range.start);}
    }
  }
});
test('query direction changes callback, input validation rejects partial parses', () => {
  assert.ok(model.inspect({},'0x2004','write').trace.at(-1).includes('MemoryRegionOps.write'));
  assert.equal(model.parseAddress('8196'),0x2004);
  for(const value of ['', '0x', '0x2004junk', '-4','1.2','0x2002','0x10000','Infinity']) assert.throws(()=>model.parseAddress(value),RangeError);
  assert.throws(()=>model.inspect({},0,'execute'),RangeError);
  assert.throws(()=>model.normalize({offset:17}),RangeError);
});

class Element {
  constructor(tag = '') { this.tag = tag; this.children = []; this.dataset = {}; this.listeners = {}; this.attributes = {}; this.hidden = true; this.value = ''; this.textContent = ''; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  setAttribute(key,value) { this.attributes[key] = value; }
  removeAttribute(key) { delete this.attributes[key]; }
  addEventListener(name,fn) { this.listeners[name] = fn; }
  querySelector(tag) { return this.children.find(child => child.tag === tag); }
  fire(name,event = {}) { this.listeners[name]?.(event); }
}
function fixture() {
  const elements = new Map(), lab = new Element();
  const get = name => { if(!elements.has(name)) elements.set(name,new Element()); return elements.get(name); };
  lab.querySelector = selector => get(selector.match(/^\[data-memory-map-(.*)\]$/)[1]);
  for(const [name,value] of Object.entries({enabled:'on',priority:'2',offset:'4096',address:'0x2004',operation:'read'})) get(name).value=value;
  const controller=model.mount(lab,{document:{createElement:tag=>new Element(tag)}});
  return {get,controller};
}
test('mount reveals controls, reports default mapping and preserves address buttons on updates', () => {
  const {get,controller}=fixture();
  assert.equal(get('controls').hidden,false); assert.equal(get('fallback').hidden,true);
  assert.match(get('status').textContent,/regs-a/);
  const selected=get('strip').children[4]; selected.fire('click');
  assert.equal(controller.state().address,0x4004); assert.equal(controller.state().hit.id,'regs-b');
  assert.equal(get('strip').children[4],selected);
  assert.equal(selected.attributes['aria-pressed'],'true');
  get('enabled').value='off';get('enabled').fire('change');
  assert.equal(controller.state().hit.id,'ram'); assert.equal(get('strip').children[4],selected);
});
test('invalid input hides stale results and corrected query restores them', () => {
  const {get}=fixture(); let prevented=false;
  get('address').value='0x2002';get('query').fire('submit',{preventDefault(){prevented=true;}});
  assert.equal(prevented,true);assert.equal(get('error').hidden,false);
  assert.equal(get('scene').hidden,true);assert.equal(get('address').attributes['aria-invalid'],'true');
  get('address').value='0x9000';get('query').fire('submit',{preventDefault(){}});
  assert.match(get('status').textContent,/未映射/);assert.equal(get('scene').hidden,false);
  assert.equal(get('error').hidden,true);assert.equal(get('address').attributes['aria-invalid'],undefined);
});
test('reset restores layout, operation and query together', () => {
  const {get,controller}=fixture();
  get('enabled').value='off';get('priority').value='-1';get('offset').value='0';get('operation').value='write';get('address').value='0x9000';
  controller.refresh();get('reset').fire('click');
  assert.equal(controller.state().hit.id,'regs-a');assert.equal(controller.state().operation,'read');
  assert.deepEqual(controller.state().config,{offset:0x1000,priority:2,enabled:true});
  assert.equal(get('address').value,'0x2004');
});
