const {test} = require('node:test');
const assert = require('node:assert/strict');
const events = require('../virt_report/render/assets/walkthrough-events.js');

test('state differences retain zero/false and ignore unchanged structured values', () => {
  const before = {idx:0, pending:false, data:[1, 2], private:7};
  const after = {idx:1, pending:false, data:[1, 2], private:8};
  assert.deepEqual(events.changes(before, after, {idx:'avail.idx', pending:'pending', data:'data'}),
    [{key:'idx', label:'avail.idx', before:'0', after:'1'}]);
  assert.equal(before.idx, 0);
});
test('independent paths are separated instead of connected into a false total order', () => {
  const scene = {parallel:{after:'handoff', branches:[
    {id:'guest', title:'Guest', steps:['used','irq']}, {id:'host', title:'Host', steps:['send']}]}};
  const trace = ['initial', 'handoff', 'send', 'used', 'irq'].map((id, index)=>({id,index}));
  assert.deepEqual(events.groups(scene,trace).map(group=>[group.id,group.frames.map(f=>f.id)]),
    [['common',['initial','handoff']],['guest',['used','irq']],['host',['send']]]);
  assert.equal(events.groups(scene,trace)[1].frames[0].index,3);
});
test('shared RAM has its own lifeline and does not become another processor', () => {
  const scene = {layout:{domains:[{id:'guest',title:'Guest',nodes:['app','driver']}],
    memory:{title:'Guest RAM',nodes:['queue']}}};
  assert.deepEqual(events.lanes(scene),[
    {id:'guest',title:'Guest',nodes:['app','driver']},
    {id:'shared-memory',title:'Guest RAM',nodes:['queue']},
  ]);
});
