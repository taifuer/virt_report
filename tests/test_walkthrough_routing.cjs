const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {route} = require('../virt_report/render/assets/walkthrough-routing.js');
const box = (x,y,w,h) => ({left:x,top:y,right:x+w,bottom:y+h});
const onEdge = (p,r) => ((p[0]===r.left || p[0]===r.right) && p[1]>=r.top && p[1]<=r.bottom)
  || ((p[1]===r.top || p[1]===r.bottom) && p[0]>=r.left && p[0]<=r.right);
function crosses(a,b,r) {
  if(a[0]===b[0])return a[0]>r.left && a[0]<r.right && Math.max(a[1],b[1])>r.top && Math.min(a[1],b[1])<r.bottom;
  if(a[1]===b[1])return a[1]>r.top && a[1]<r.bottom && Math.max(a[0],b[0])>r.left && Math.min(a[0],b[0])<r.right;
  return true;
}
function assertRoute(source,target,cards,bounds) {
  const original=JSON.stringify(cards), path=route(source,target,cards,bounds);
  assert(path && path.length>=2, `missing path ${JSON.stringify({source,target,bounds})}`);
  assert(onEdge(path[0],source)); assert(onEdge(path.at(-1),target));
  for(const p of path) assert(p[0]>=0 && p[1]>=0 && p[0]<=bounds.width && p[1]<=bounds.height);
  for(let i=1;i<path.length;i++) {
    const a=path[i-1],b=path[i];assert(a[0]===b[0] || a[1]===b[1],'orthogonal segment');
    assert(a[0]!==b[0] || a[1]!==b[1],'no zero-length segment');
    for(const card of cards)assert(!crosses(a,b,card),`route crosses card: ${JSON.stringify({path,card})}`);
  }
  assert.equal(JSON.stringify(cards),original);
  assert.deepEqual(route(source,target,cards,bounds),path,'deterministic route');
  return path;
}
const layouts=[
  ['two columns', {width:440,height:350}, [box(12,12,185,80),box(230,12,185,80),box(12,120,185,80),box(230,120,185,80),box(12,250,403,80)]],
  ['three execution domains and shared RAM', {width:720,height:400}, [box(12,12,210,95),box(250,12,210,95),box(488,12,210,95),box(12,135,210,95),box(250,135,210,95),box(12,300,686,80)]],
  ['four execution domains and wide memory', {width:960,height:460}, [box(12,12,210,90),box(250,12,210,90),box(488,12,210,90),box(726,12,210,90),box(12,134,210,90),box(488,134,210,90),box(12,350,924,90)]],
  ['five domains', {width:1180,height:410}, [box(12,12,210,100),box(246,12,210,100),box(480,12,210,100),box(714,12,210,100),box(948,12,210,100),box(12,150,210,100),box(12,300,1146,90)]],
  ['mobile wrapped two columns', {width:320,height:650}, [box(8,12,141,90),box(170,12,141,90),box(8,126,141,110),box(170,126,141,90),box(8,260,141,115),box(170,260,141,90),box(8,460,303,100)]],
  ['mobile single column', {width:285,height:680}, [box(8,12,269,90),box(8,128,269,90),box(8,244,269,90),box(8,360,269,90),box(8,476,269,90)]],
];
for(const [label,bounds,cards] of layouts)test(`${label}: every ordered pair avoids all cards`,()=>{
  for(const source of cards)for(const target of cards)if(source!==target)assertRoute(source,target,cards,bounds);
});
test('same-row neighboring cards keep a single direct segment',()=>{
  const cards=[box(10,20,100,50),box(140,20,100,50)];
  assert.deepEqual(assertRoute(cards[0],cards[1],cards,{width:250,height:100}),[[110,45],[140,45]]);
});
test('a non-adjacent same-row card routes around the intermediate card',()=>{
  const cards=[box(10,20,100,70),box(140,20,100,70),box(270,20,100,70)];
  const path=assertRoute(cards[0],cards[2],cards,{width:390,height:120});
  assert(path.length>=4);
});
test('a full-height barrier has no safe route',()=>{
  const cards=[box(10,20,70,70),box(110,0,50,150),box(190,20,70,70)];
  assert.equal(route(cards[0],cards[2],cards,{width:280,height:150}),null);
});
test('overlap and invalid geometry fail closed',()=>{
  const source=box(10,10,80,80),target=box(200,10,80,80),cover=box(0,0,100,100);
  assert.equal(route(source,target,[source,target,cover],{width:300,height:130}),null);
  assert.equal(route(source,source,[source],{width:300,height:130}),null);
  assert.equal(route({...source,right:NaN},target,[target],{width:300,height:130}),null);
  assert.equal(route(source,target,[source,target],{width:0,height:130}),null);
});
test('fractional CSS geometry stays orthogonal and inside the canvas',()=>{
  const cards=[box(8.125,12.25,139.875,91.5),box(169.5,12.25,142.375,91.5),box(8.125,144.75,303.75,90.125)];
  for(const source of cards)for(const target of cards)if(source!==target)assertRoute(source,target,cards,{width:320,height:250});
});
test('browser export has no DOM dependency',()=>{
  const context=vm.createContext({});
  vm.runInContext(fs.readFileSync(require.resolve('../virt_report/render/assets/walkthrough-routing.js'),'utf8'),context);
  assert.equal(typeof context.WalkthroughRouting.route,'function');
});
