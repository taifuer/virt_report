"""Shared fullscreen lifecycle without network, browser dependencies or live services."""
import shutil
import subprocess
from pathlib import Path

import pytest


ASSET = Path(__file__).resolve().parents[1] / "virt_report/render/assets/walkthrough-fullscreen.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required")
def test_fullscreen_lifecycle_restores_focus_attributes_and_background():
    # Small DOM fixture exercises native/fallback behavior; layout is checked by
    # the browser walkthrough regression scripts in addition to this state test.
    harness = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
class Node {
  constructor(tag='div', attrs={}) {
    this.tagName=tag; this.attrs=new Map(Object.entries(attrs)); this.children=[];
    this.parentElement=null; this.listeners={}; this.disabled=false; this.hidden=false;
    this.tabIndex=['button','input','select','textarea','a'].includes(tag)?0:-1;
    this.classList={values:new Set(),add(...xs){xs.forEach(x=>this.values.add(x))},
      remove(...xs){xs.forEach(x=>this.values.delete(x))},contains(x){return this.values.has(x)}};
  }
  append(...children){for(const child of children){this.children.push(child);child.parentElement=this;}}
  getAttribute(k){return this.attrs.has(k)?this.attrs.get(k):null;}
  hasAttribute(k){return this.attrs.has(k);}
  setAttribute(k,v){this.attrs.set(k,String(v));}
  removeAttribute(k){this.attrs.delete(k);}
  addEventListener(type,fn){(this.listeners[type] ||= []).push(fn);}
  dispatchEvent(e){e.target ||= this;(this.listeners[e.type]||[]).forEach(fn=>fn(e));return true;}
  contains(node){return node===this || this.children.some(child=>child.contains(node));}
  closest(selector){return selector==='[inert]'?(this.hasAttribute('inert')?this:this.parentElement?.closest(selector)||null):null;}
  querySelectorAll(selector){
    const all=this.children.flatMap(child=>[child,...child.querySelectorAll('*')]);
    if(selector==='*')return all;
    if(selector.startsWith('[data-'))return all.filter(node=>node.hasAttribute(selector.slice(1,-1)));
    return all.filter(node=>['button','input','select','textarea','a'].includes(node.tagName)||node.hasAttribute('tabindex'));
  }
  getClientRects(){return this.hidden?[]:[{}];}
  get isConnected(){return document.documentElement.contains(this);}
  focus(){document.activeElement=this;document.dispatchEvent({type:'focusin',target:this});}
}
class CustomEvent {constructor(type,options={}){this.type=type;Object.assign(this,options);}}
const document=new Node('document');
document.documentElement=new Node('html');document.body=new Node('body');document.documentElement.append(document.body);
document.querySelectorAll=(selector)=>document.documentElement.querySelectorAll(selector);
document.fullscreenElement=null;
const outside=new Node('button'), priorInert=new Node('div',{inert:'original'}), main=new Node('main');
const before=new Node('button'), after=new Node('a'), lab=new Node('div',{'data-walkthrough-lab':'',role:'region','aria-label':'已有名称'});
const expand=new Node('button',{'data-walkthrough-expand':''}), select=new Node('select'), last=new Node('button');
lab.append(expand,select,last);main.append(before,lab,after);document.body.append(outside,priorInert,main);
document.activeElement=outside;
const calls={expand:0,resize:0,scroll:0};
lab.addEventListener('walkthrough:expand',()=>calls.expand++);lab.addEventListener('walkthrough:resize',()=>calls.resize++);
const window={scrollX:12,scrollY:345,scrollTo(o){assert.equal(o.left,12);assert.equal(o.top,345);calls.scroll++;}};
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),{document,window,CustomEvent,console});
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const click=async()=>{expand.dispatchEvent({type:'click'});await tick();};
const key=(name,shift=false)=>{const e={type:'keydown',key:name,shiftKey:shift,preventDefault(){this.prevented=true}};document.dispatchEvent(e);return e;};
(async()=>{
  await click(); // no requestFullscreen: modal fallback
  assert.equal(lab.getAttribute('data-walkthrough-expanded'),'fallback');
  assert.equal(expand.getAttribute('aria-pressed'),'true');
  assert.equal(expand.textContent,'退出全屏');
  assert.equal(document.activeElement,expand);
  assert.equal(lab.getAttribute('role'),'dialog');assert.equal(lab.getAttribute('aria-modal'),'true');
  assert.equal(lab.getAttribute('aria-label'),'已有名称');
  assert(outside.hasAttribute('inert')&&before.hasAttribute('inert')&&after.hasAttribute('inert'));
  assert(!main.hasAttribute('inert')&&!lab.hasAttribute('inert'));
  assert(document.documentElement.classList.contains('walkthrough-modal-open'));
  last.focus();assert(key('Tab').prevented);assert.equal(document.activeElement,expand);
  assert(key('Tab',true).prevented);assert.equal(document.activeElement,last);
  outside.focus();assert.equal(document.activeElement,expand,'focus cannot escape fallback');
  key('Escape');await tick();
  assert.equal(expand.getAttribute('aria-pressed'),'false');assert.equal(expand.textContent,'全屏演示');
  assert(!lab.classList.contains('walkthrough-expanded'));assert(!lab.hasAttribute('data-walkthrough-expanded'));
  assert.equal(lab.getAttribute('role'),'region');assert(!lab.hasAttribute('aria-modal'));
  assert.equal(lab.getAttribute('aria-label'),'已有名称');
  assert(!outside.hasAttribute('inert')&&!before.hasAttribute('inert')&&!after.hasAttribute('inert'));
  assert.equal(priorInert.getAttribute('inert'),'original');
  assert(!document.documentElement.classList.contains('walkthrough-modal-open'));
  assert.equal(document.activeElement,expand);assert.equal(calls.expand,1);assert.equal(calls.resize,2);
  // Native failure uses the same fallback and preserves pre-existing root state.
  document.documentElement.classList.add('walkthrough-modal-open');
  lab.requestFullscreen=()=>Promise.reject(new Error('not allowed'));
  await click();await click();
  assert(document.documentElement.classList.contains('walkthrough-modal-open'));
  document.documentElement.classList.remove('walkthrough-modal-open');
  // Native enter/exit events may precede promise resolution.
  lab.requestFullscreen=async()=>{document.fullscreenElement=lab;document.dispatchEvent({type:'fullscreenchange'});};
  document.exitFullscreen=async()=>{document.fullscreenElement=null;document.dispatchEvent({type:'fullscreenchange'});};
  await click();assert.equal(lab.getAttribute('data-walkthrough-expanded'),'native');
  assert.equal(expand.getAttribute('aria-pressed'),'true');assert(!outside.hasAttribute('inert'));
  assert.equal(lab.getAttribute('role'),'region');assert(!lab.hasAttribute('aria-modal'));
  await click();assert.equal(expand.getAttribute('aria-pressed'),'false');assert.equal(document.activeElement,expand);
  // Browser Escape (fullscreenchange) also restores the button and focus.
  await click();document.fullscreenElement=null;document.dispatchEvent({type:'fullscreenchange'});
  assert.equal(expand.getAttribute('aria-pressed'),'false');assert.equal(document.activeElement,expand);
  // Rejected native exit leaves a recoverable expanded session.
  await click();document.exitFullscreen=()=>Promise.reject(new Error('stay fullscreen'));await click();
  assert.equal(expand.getAttribute('aria-pressed'),'true');
  document.exitFullscreen=async()=>{document.fullscreenElement=null;document.dispatchEvent({type:'fullscreenchange'});};
  await click();assert.equal(expand.getAttribute('aria-pressed'),'false');
  // Never replace another component's active fullscreen session.
  document.fullscreenElement=outside;await click();assert.equal(expand.getAttribute('aria-pressed'),'false');
  assert.equal(document.fullscreenElement,outside);
  console.log('native/fallback, rejected APIs, background isolation, keyboard and focus restoration passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run(
        ["node", "-e", harness, str(ASSET)], capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_shared_fullscreen_uses_public_hooks_and_no_external_requests():
    code = ASSET.read_text()
    assert "[data-walkthrough-lab]" in code and "[data-walkthrough-expand]" in code
    assert "walkthrough:expand" in code and "walkthrough:resize" in code
    assert "fetch(" not in code and "XMLHttpRequest" not in code
    assert "execution-lab" not in code and "data-vbr" not in code
