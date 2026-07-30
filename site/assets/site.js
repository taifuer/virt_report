document.querySelectorAll('[data-pager]').forEach(root=>{
  const items=[...root.querySelectorAll('[data-page-item]')], controls=root.querySelector('[data-page-controls]');
  if(!controls||items.length<1)return;
  let page=0,size=Number(root.dataset.defaultSize||10);
  const status=controls.querySelector('[data-page-status]'),prev=controls.querySelector('[data-page-prev]'),next=controls.querySelector('[data-page-next]'),select=controls.querySelector('[data-page-size]');
  const draw=()=>{const pages=Math.max(1,Math.ceil(items.length/size));page=Math.min(page,pages-1);items.forEach((item,i)=>item.hidden=i<page*size||i>=(page+1)*size);status.textContent=`${page+1} / ${pages}`;prev.disabled=page===0;next.disabled=page===pages-1};
  prev.addEventListener('click',()=>{page--;draw()});next.addEventListener('click',()=>{page++;draw()});select.addEventListener('change',()=>{size=Number(select.value);page=0;draw()});draw();
});
if(window.matchMedia('(max-width:620px)').matches){
  const currentNav=document.querySelector('.main-nav a.on');if(currentNav)currentNav.scrollIntoView({block:'nearest',inline:'center'});
}
