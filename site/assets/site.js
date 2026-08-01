document.querySelectorAll('[data-pager]').forEach(root=>{
  const items=[...root.querySelectorAll('[data-page-item]')], controls=root.querySelector('[data-page-controls]');
  if(!controls||items.length<1)return;
  let page=0,size=Number(root.dataset.defaultSize||10);
  const status=controls.querySelector('[data-page-status]'),prev=controls.querySelector('[data-page-prev]'),next=controls.querySelector('[data-page-next]'),select=controls.querySelector('[data-page-size]');
  const draw=()=>{const pages=Math.max(1,Math.ceil(items.length/size));page=Math.min(page,pages-1);items.forEach((item,i)=>item.hidden=i<page*size||i>=(page+1)*size);status.textContent=`${page+1} / ${pages}`;prev.disabled=page===0;next.disabled=page===pages-1};
  prev.addEventListener('click',()=>{page--;draw()});next.addEventListener('click',()=>{page++;draw()});select.addEventListener('change',()=>{size=Number(select.value);page=0;draw()});draw();
});
document.querySelectorAll('[data-conference-browser]').forEach(root=>{
  const items=[...root.querySelectorAll('[data-conference-item]')],venue=root.querySelector('[data-conference-venue]'),year=root.querySelector('[data-conference-year]'),count=root.querySelector('[data-conference-count]'),status=root.querySelector('[data-conference-status]'),empty=root.querySelector('[data-conference-empty]'),prev=root.querySelector('[data-conference-prev]'),next=root.querySelector('[data-conference-next]'),sizeSelect=root.querySelector('[data-conference-size]');
  if(!items.length||!venue||!year||!prev||!next||!sizeSelect)return;
  const params=new URLSearchParams(window.location.search);if([...venue.options].some(option=>option.value===params.get('venue')))venue.value=params.get('venue');if([...year.options].some(option=>option.value===params.get('year')))year.value=params.get('year');
  let page=0,size=Number(root.dataset.defaultSize||10);
  const draw=()=>{const matching=items.filter(item=>(!venue.value||item.dataset.venue===venue.value)&&(!year.value||item.dataset.year===year.value));const pages=Math.max(1,Math.ceil(matching.length/size));page=Math.min(page,pages-1);items.forEach(item=>item.hidden=true);matching.slice(page*size,(page+1)*size).forEach(item=>item.hidden=false);count.textContent=`${matching.length} 个议题`;if(empty)empty.hidden=matching.length>0;status.textContent=`${page+1} / ${pages}`;prev.disabled=page===0;next.disabled=page===pages-1};
  [venue,year].forEach(control=>control.addEventListener('change',()=>{page=0;draw()}));sizeSelect.addEventListener('change',()=>{size=Number(sizeSelect.value);page=0;draw()});prev.addEventListener('click',()=>{page--;draw()});next.addEventListener('click',()=>{page++;draw()});draw();
});
document.querySelectorAll('[data-topic-group]').forEach(root=>{
  const toggle=root.querySelector('[data-topic-group-toggle]'),content=root.querySelector('[data-topic-group-content]');if(!toggle||!content)return;
  const draw=expanded=>{toggle.setAttribute('aria-expanded',String(expanded));toggle.title=`${expanded?'收起':'展开'}${toggle.textContent.trim()}`;content.hidden=!expanded;root.classList.toggle('is-collapsed',!expanded)};
  toggle.addEventListener('click',()=>draw(toggle.getAttribute('aria-expanded')!=='true'));draw(toggle.getAttribute('aria-expanded')==='true');
});
if(window.matchMedia('(max-width:620px)').matches){
  const currentNav=document.querySelector('.main-nav a.on');if(currentNav)currentNav.scrollIntoView({block:'nearest',inline:'center'});
}
