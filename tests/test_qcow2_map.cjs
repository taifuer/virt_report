'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const model = require('../virt_report/render/assets/qcow2-map.js');

test('ordinary L2 address decomposition handles both L1 tables and boundaries', () => {
  assert.deepEqual(model.splitAddress('0x20031000'), {address:0x20031000, cluster:8195, l1:1, l2:3, within:0x1000});
  for (const [address,l1,l2,within] of [[0,0,0,0],[0x1ffff000,0,8191,0xf000],[0x20000000,1,0,0],[0x3ffff000,1,8191,0xf000]]) {
    const actual=model.splitAddress(address);
    assert.equal(actual.l1,l1); assert.equal(actual.l2,l2); assert.equal(actual.within,within);
    assert.equal((actual.l1*8192+actual.l2)*65536+actual.within,address);
  }
});
test('refcount lookup indexes host clusters, including the second refcount block', () => {
  assert.deepEqual(model.refcountIndex(0x90000),{cluster:9,table:0,block:9});
  assert.deepEqual(model.refcountIndex(0x40000),{cluster:4,table:0,block:4});
  const boundary=model.REF_ENTRIES*model.CLUSTER;
  assert.deepEqual(model.refcountIndex(boundary-1),{cluster:32767,table:0,block:32767});
  assert.deepEqual(model.refcountIndex(boundary),{cluster:32768,table:1,block:0});
  for(const value of [-1,1.5,NaN,Infinity,Number.MAX_SAFE_INTEGER+1,'65536']) assert.throws(()=>model.refcountIndex(value),RangeError);
});
test('local shared reads preserve every refcount and mapping', () => {
  for (const scenario of ['private','shared','both']) {
    const state=model.inspect({scenario},'0x20031000','read');
    assert.equal(state.resultOffset,0x91000); assert.equal(state.source,'local');
    assert.deepEqual(state.before,state.after); assert.ok(state.references.every(ref=>ref.before===ref.after));
    assert.equal(state.allocated,false); assert.equal(state.metadataCow,false); assert.equal(state.dataCow,false);
    assert.ok(state.cells.every(value=>value==='local'));
  }
});
test('unallocated reads fall through to the backing guest offset, explicit zero never does', () => {
  const backed=model.inspect({scenario:'unallocated'},'0x20031000');
  assert.equal(backed.source,'backing'); assert.equal(backed.resultOffset,0x20031000);
  assert.equal(backed.before.data,null); assert.equal(backed.references.length,1);
  const empty=model.inspect({scenario:'unallocated',backing:false});
  assert.equal(empty.source,'zero'); assert.equal(empty.resultOffset,null);
  for(const backing of [true,false]) {
    const zero=model.inspect({scenario:'zero',backing});
    assert.equal(zero.source,'zero'); assert.equal(zero.resultOffset,null);
    assert.ok(!zero.events.some(event=>event.kind==='read-backing'));
  }
});
test('exclusive ordinary data writes in place without copying or touching L2', () => {
  const state=model.inspect({scenario:'private'},'0x20031000','write');
  assert.deepEqual(state.before,state.after); assert.equal(state.resultOffset,0x91000);
  assert.equal(state.allocated,false); assert.equal(state.preservedBytes,0);
  assert.ok(state.references.every(ref=>ref.before===ref.after));
  assert.equal(state.cells.filter(value=>value==='new').length,1);
  assert.equal(state.cells[1],'new'); assert.equal(state.cells.filter(value=>value==='local').length,15);
  assert.ok(!state.events.some(event=>event.kind==='data-link'));
});
test('data COW keeps the old shared content and replaces only the active data pointer', () => {
  const state=model.inspect({scenario:'shared'},'0x20031000','write');
  assert.equal(state.dataCow,true); assert.equal(state.metadataCow,false);
  assert.equal(state.before.l2,state.after.l2); assert.equal(state.before.data,0x90000);
  assert.equal(state.after.data,0x110000); assert.equal(state.resultOffset,0x111000);
  assert.equal(state.preservedBytes,60*1024);
  assert.deepEqual(state.references.map(ref=>[ref.role,ref.before,ref.after]),[['原 L2 表',1,1],['原数据簇',2,1],['新数据簇',0,1]]);
  const events=state.events.map(event=>event.kind);
  for(const [first,second] of [['data-allocate','data-prepare'],['data-prepare','data-dependency'],['data-dependency','data-link'],['data-link','data-release']]) assert.ok(events.indexOf(first)<events.indexOf(second));
});
test('shared metadata is prepared and linked before old-table release and data replacement', () => {
  const state=model.inspect({scenario:'both'},'0x20031000','write');
  assert.equal(state.metadataCow,true); assert.equal(state.after.l2,0x100000);
  assert.deepEqual(state.references.map(ref=>[ref.role,ref.before,ref.after]),[['原 L2 表',2,1],['原数据簇',2,1],['新 L2 表',0,1],['新数据簇',0,1]]);
  const events=state.events.map(event=>event.kind);
  for(const [first,second] of [['metadata-allocate','metadata-copy'],['metadata-copy','metadata-link'],['metadata-link','metadata-release'],['metadata-release','data-allocate']]) assert.ok(events.indexOf(first)<events.indexOf(second));
  assert.notEqual(state.before.l2,state.after.l2); assert.notEqual(state.before.data,state.after.data);
});
test('first writes preserve backing or zero semantics and never decrease nonexistent data refs', () => {
  for(const scenario of ['unallocated','zero']) for(const backing of [true,false]) {
    const state=model.inspect({scenario,backing},'0x2003f000','write');
    const source=scenario==='unallocated'&&backing?'backing':'zero';
    assert.equal(state.source,source); assert.equal(state.allocated,true); assert.equal(state.dataCow,false);
    assert.equal(state.after.kind,'normal'); assert.equal(state.after.data,0x110000);
    assert.equal(state.cells[15],'new'); assert.ok(state.cells.slice(0,15).every(value=>value===source));
    assert.equal(state.references.length,2); assert.ok(!state.events.some(event=>event.kind==='data-release'));
    assert.equal(state.preservedBytes,61440);
  }
});
test('all scenarios and within-cluster offsets preserve all bytes outside the request', () => {
  for(const scenario of model.SCENARIOS) for(const backing of [true,false]) for(let slot=0;slot<16;slot++) {
    const config=Object.freeze({scenario,backing}), address=0x20030000+slot*4096;
    const read=model.inspect(config,address), write=model.inspect(config,address,'write');
    for(let i=0;i<16;i++) assert.equal(write.cells[i],i===slot?'new':read.cells[i]);
    assert.ok(write.references.every(ref=>ref.after>=0));
    assert.deepEqual(model.inspect(config,address),read); // Requests do not accumulate mutations.
  }
});
test('strict inputs reject partial parses, alignment errors and unknown modes', () => {
  assert.equal(model.parseAddress(' 0X20031000 '),0x20031000);
  assert.equal(model.parseAddress('536870912'),0x20000000);
  for(const value of ['', '0x', '0x1000junk', '-4096', '1.5','0x1001','0x40000000','Infinity','1e3','0b1000']) assert.throws(()=>model.parseAddress(value),RangeError);
  assert.throws(()=>model.inspect({scenario:'compressed'}),RangeError);
  assert.throws(()=>model.inspect({backing:'yes'}),RangeError);
  assert.throws(()=>model.inspect({},0,'discard'),RangeError);
});

class Element {
  constructor(tag='') {this.tag=tag;this.children=[];this.dataset={};this.listeners={};this.attributes={};this.hidden=true;this.value='';this.textContent='';}
  append(...nodes) {this.children.push(...nodes);}
  replaceChildren(...nodes) {this.children=nodes;}
  setAttribute(key,value) {this.attributes[key]=value;}
  removeAttribute(key) {delete this.attributes[key];}
  addEventListener(name,fn) {this.listeners[name]=fn;}
  querySelector(tag) {return this.children.find(child=>child.tag===tag);}
  fire(name,event={}) {this.listeners[name]?.(event);}
}
function fixture() {
  const elements=new Map(), lab=new Element();
  const get=name=>{if(!elements.has(name)) elements.set(name,new Element());return elements.get(name);};
  lab.querySelector=selector=>get(selector.match(/^\[data-qcow2-map-(.*)\]$/)[1]);
  for(const [name,value] of Object.entries({address:'0x20031000',scenario:'shared',backing:'on',operation:'read'})) get(name).value=value;
  const controller=model.mount(lab,{document:{createElement:tag=>new Element(tag)}});
  return {get,controller};
}
test('mount exposes the model, preserves focused cell elements, and updates write evidence', () => {
  const {get,controller}=fixture();
  assert.equal(get('query').hidden,false); assert.equal(get('fallback').hidden,true);
  assert.match(get('status').textContent,/0x91000/);
  const cell=get('strip').children[15]; cell.fire('click');
  assert.equal(controller.state().mapping.within,0xf000); assert.equal(get('strip').children[15],cell);
  assert.equal(cell.attributes['aria-pressed'],'true');
  get('scenario').value='both';get('scenario').fire('change');
  get('operation').value='write';get('operation').fire('change');
  assert.match(get('status').textContent,/COW L2/);assert.equal(get('refcounts').children.length,4);
});
test('invalid input hides stale results and successful queries recover accessibility state', () => {
  const {get,controller}=fixture();let prevented=false;
  get('address').value='oops';get('query').fire('submit',{preventDefault(){prevented=true;}});
  assert.equal(prevented,true);assert.equal(controller.state(),null);assert.equal(get('scene').hidden,true);
  assert.equal(get('error').hidden,false);assert.equal(get('address').attributes['aria-invalid'],'true');
  get('address').value='0';get('query').fire('submit',{preventDefault(){}});
  assert.equal(get('scene').hidden,false);assert.equal(get('error').hidden,true);
  assert.equal(get('address').attributes['aria-invalid'],undefined);assert.equal(controller.state().mapping.address,0);
});
test('reset restores address, initial mapping, backing and read direction', () => {
  const {get,controller}=fixture();
  get('address').value='0x3000';get('scenario').value='unallocated';get('backing').value='off';get('operation').value='write';controller.refresh();
  get('reset').fire('click');
  assert.equal(controller.state().mapping.address,0x20031000);assert.equal(controller.state().operation,'read');
  assert.deepEqual(controller.state().config,{scenario:'shared',backing:true});
  assert.equal(get('address').value,'0x20031000');
});
