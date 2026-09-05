const siteHeader=document.querySelector('.site-head');
const reportToc=document.querySelector('[data-report-toc]');
const syncStickyOffsets=()=>{
  if(siteHeader)document.documentElement.style.setProperty('--site-header-height',`${Math.ceil(siteHeader.getBoundingClientRect().height)}px`);
  if(reportToc)document.documentElement.style.setProperty('--report-toc-height',`${Math.ceil(reportToc.getBoundingClientRect().height)}px`);
};
syncStickyOffsets();
window.addEventListener('resize',syncStickyOffsets);
if('ResizeObserver'in window){const stickyResize=new ResizeObserver(syncStickyOffsets);if(siteHeader)stickyResize.observe(siteHeader);if(reportToc)stickyResize.observe(reportToc)}
if(reportToc){
  const links=[...reportToc.querySelectorAll('a[href^="#"]')];
  const sections=links.map(link=>document.getElementById(link.hash.slice(1))).filter(Boolean);
  let activeId='',frame=0,projectFilter='all',itemFilter='all';
  const drawToc=()=>{
    const mobile=window.matchMedia('(max-width:860px)').matches;
    const offset=(siteHeader?.getBoundingClientRect().height||0)+(mobile?reportToc.getBoundingClientRect().height:0)+24;
    const visible=sections.filter(section=>!section.hidden);
    let current=visible[0];
    for(const section of visible){if(section.getBoundingClientRect().top<=offset)current=section}
    const nextId=current?.id||'';
    links.forEach(link=>{
      const section=document.getElementById(link.hash.slice(1));link.hidden=!section||section.hidden;
      const active=!link.hidden&&link.hash===`#${nextId}`;link.classList.toggle('active',active);
      if(active){link.setAttribute('aria-current','location');if(mobile&&activeId!==nextId){const left=link.offsetLeft-reportToc.offsetLeft;reportToc.scrollTo({left:Math.max(0,left-(reportToc.clientWidth-link.offsetWidth)/2),behavior:'instant'})}}else link.removeAttribute('aria-current');
    });
    activeId=nextId;frame=0;
  };
  const requestTocDraw=()=>{if(!frame)frame=requestAnimationFrame(drawToc)};
  const applyFilters=()=>{
    document.querySelectorAll('.project-section').forEach(section=>{
      const projectMatch=projectFilter==='all'||section.dataset.project===projectFilter;let visible=0;
      section.querySelectorAll('.dyn').forEach(item=>{const match=itemFilter==='all'||item.dataset.category===itemFilter||(item.dataset.architectures||'').split(',').includes(itemFilter);item.hidden=!projectMatch||!match;if(!item.hidden)visible++});
      section.hidden=visible===0;
      const count=reportToc.querySelector(`a[href="#${section.id}"] .count`);if(count)count.textContent=visible;
    });
    requestTocDraw();
  };
  document.querySelectorAll('[data-project-filter]').forEach(button=>button.addEventListener('click',()=>{projectFilter=button.dataset.projectFilter;document.querySelectorAll('[data-project-filter]').forEach(x=>x.classList.toggle('active',x===button));applyFilters()}));
  document.querySelectorAll('[data-item-filter]').forEach(button=>button.addEventListener('click',()=>{itemFilter=button.dataset.itemFilter;document.querySelectorAll('[data-item-filter]').forEach(x=>x.classList.toggle('active',x===button));applyFilters()}));
  window.addEventListener('scroll',requestTocDraw,{passive:true});window.addEventListener('resize',requestTocDraw);window.addEventListener('hashchange',requestTocDraw);drawToc();
}
// Shared pagination state; server-rendered lists keep real navigation links.
const readPageState=()=>{
  const params=new URLSearchParams(location.search);
  const requested=Number(params.get('page')||1);
  return {page:Number.isSafeInteger(requested)?Math.max(0,requested-1):0,size:['10','20','30'].includes(params.get('per_page'))?Number(params.get('per_page')):10};
};
const writeListState=(values,hash)=>{
  const url=new URL(location.href);
  Object.entries(values).forEach(([key,value])=>{
    const isDefault=value===''||(key==='page'&&value===1)||(key==='per_page'&&value===10)||(key==='view'&&value==='compact');
    if(isDefault)url.searchParams.delete(key);else url.searchParams.set(key,value);
  });
  if(hash!==undefined)url.hash=hash;
  history.replaceState(null,'',`${url.pathname}${url.search}${url.hash}`);
};
const scrollList=element=>element?.scrollIntoView({block:'start',behavior:'instant'});
document.querySelectorAll('[data-pagination-server]').forEach(root=>{
  root.querySelector('[data-pagination-size]')?.addEventListener('change',event=>{
    const url=new URL(location.href);
    url.searchParams.set('per_page',event.target.value);
    url.searchParams.delete('page');
    url.hash=root.dataset.paginationAnchor||'';
    location.assign(url.href);
  });
});
document.querySelectorAll('[data-pager]').forEach(root=>{
  const items=[...root.querySelectorAll('[data-page-item]')],controls=root.querySelector('[data-page-controls]');
  if(!controls||!items.length)return;
  let {page,size}=readPageState();
  const status=controls.querySelector('[data-page-status]'),prev=controls.querySelector('[data-page-prev]'),next=controls.querySelector('[data-page-next]'),select=controls.querySelector('[data-page-size]');
  select.value=String(size);
  const draw=()=>{
    const pages=Math.max(1,Math.ceil(items.length/size));page=Math.max(0,Math.min(page,pages-1));
    items.forEach((item,i)=>item.hidden=i<page*size||i>=(page+1)*size);
    status.textContent=`${page+1} / ${pages}`;prev.disabled=page===0;next.disabled=page===pages-1;
    writeListState({page:page+1,per_page:size});
  };
  const move=delta=>{page+=delta;draw();scrollList(root)};
  prev.addEventListener('click',()=>move(-1));next.addEventListener('click',()=>move(1));
  select.addEventListener('change',()=>{size=Number(select.value);page=0;draw();scrollList(root)});draw();
});
document.querySelectorAll('[data-conference-browser]').forEach(root=>{
  const items=[...root.querySelectorAll('[data-conference-item]')],venue=root.querySelector('[data-conference-venue]'),year=root.querySelector('[data-conference-year]'),count=root.querySelector('[data-conference-count]'),status=root.querySelector('[data-conference-status]'),empty=root.querySelector('[data-conference-empty]'),prev=root.querySelector('[data-conference-prev]'),next=root.querySelector('[data-conference-next]'),sizeSelect=root.querySelector('[data-conference-size]');
  if(!venue||!year||!prev||!next||!sizeSelect)return;
  const form=root.querySelector('[data-conference-search]'),query=root.querySelector('[data-conference-query]'),clear=root.querySelector('[data-conference-clear]'),reset=root.querySelector('[data-conference-reset]'),topicFilter=root.querySelector('[data-conference-topic-filter]'),topicLabel=root.querySelector('[data-conference-topic-label]');
  const normalize=value=>value.normalize('NFKC').toLocaleLowerCase().replace(/\s+/g,' ').trim();
  const indexed=new Map(items.map(item=>[item,{text:normalize(item.textContent),topics:JSON.parse(item.dataset.topics||'[]')}]));
  const params=new URLSearchParams(location.search);
  if([...venue.options].some(option=>option.value===params.get('venue')))venue.value=params.get('venue');
  if([...year.options].some(option=>option.value===params.get('year')))year.value=params.get('year');
  if(query)query.value=(params.get('q')||'').slice(0,120);
  let topic=(params.get('topic')||'').slice(0,100);
  let {page,size}=readPageState();sizeSelect.value=String(size);
  const matchesText=item=>{
    const data=indexed.get(item),words=normalize(query?.value||'').split(' ').filter(Boolean);
    return (!topic||data.topics.includes(topic))&&words.every(word=>data.text.includes(word));
  };
  const matches=item=>matchesText(item)&&(!venue.value||item.dataset.venue===venue.value)&&(!year.value||item.dataset.year===year.value);
  const facetCounts=(select,key,otherKey,otherValue)=>{
    const available=items.filter(item=>matchesText(item)&&(!otherValue||item.dataset[otherKey]===otherValue));
    [...select.options].forEach(option=>{
      const optionCount=option.value?available.filter(item=>item.dataset[key]===option.value).length:available.length;
      option.textContent=`${option.dataset.optionLabel}（${optionCount}）`;
      option.disabled=Boolean(option.value)&&optionCount===0&&option.value!==select.value;
    });
  };
  const draw=()=>{
    facetCounts(venue,'venue','year',year.value);facetCounts(year,'year','venue',venue.value);
    const matching=items.filter(matches);
    const pages=Math.max(1,Math.ceil(matching.length/size));page=Math.max(0,Math.min(page,pages-1));
    items.forEach(item=>item.hidden=true);matching.slice(page*size,(page+1)*size).forEach(item=>item.hidden=false);
    count.textContent=`${matching.length} 篇论文`;if(empty)empty.hidden=matching.length>0;
    status.textContent=`${page+1} / ${pages}`;prev.disabled=page===0;next.disabled=page===pages-1;
    if(clear)clear.hidden=!query.value;
    if(topicFilter)topicFilter.hidden=!topic;
    if(topicLabel)topicLabel.textContent=topic;
    if(reset)reset.hidden=!(venue.value||year.value||topic||query?.value);
    root.querySelectorAll('[data-paper-topic]').forEach(link=>{const active=link.dataset.paperTopic===topic;link.classList.toggle('active',active);if(active)link.setAttribute('aria-current','true');else link.removeAttribute('aria-current')});
    writeListState({venue:venue.value,year:year.value,q:query?.value.trim()||'',topic,page:page+1,per_page:size});
  };
  const changed=()=>{page=0;writeListState({},'');draw()};
  [venue,year].forEach(control=>control.addEventListener('change',changed));
  form?.addEventListener('submit',event=>{event.preventDefault();changed()});
  query?.addEventListener('input',changed);
  clear?.addEventListener('click',()=>{query.value='';changed();query.focus()});
  reset?.addEventListener('click',()=>{venue.value='';year.value='';topic='';if(query)query.value='';changed()});
  root.querySelector('[data-conference-topic-clear]')?.addEventListener('click',()=>{topic='';changed()});
  root.querySelectorAll('[data-paper-topic]').forEach(link=>link.addEventListener('click',event=>{
    if(event.metaKey||event.ctrlKey||event.shiftKey||event.altKey||event.button!==0)return;
    event.preventDefault();topic=link.dataset.paperTopic;changed();scrollList(root);
  }));
  sizeSelect.addEventListener('change',()=>{size=Number(sizeSelect.value);changed();scrollList(root.querySelector('.paper-list'))});
  const move=delta=>{page+=delta;writeListState({},'');draw();scrollList(root.querySelector('.paper-list'))};
  const revealPaper=()=>{
    const target=items.find(item=>`#${item.id}`===location.hash);
    if(!target)return false;
    if(!matches(target)){venue.value='';year.value='';topic='';if(query)query.value=''}
    page=Math.floor(items.filter(matches).indexOf(target)/size);draw();
    requestAnimationFrame(()=>scrollList(target));return true;
  };
  window.addEventListener('hashchange',revealPaper);
  prev.addEventListener('click',()=>move(-1));next.addEventListener('click',()=>move(1));
  if(!revealPaper())draw();
});
document.querySelectorAll('[data-version-browser]').forEach(root=>{
  const items=[...root.querySelectorAll('[data-version-item]')],groups=[...root.querySelectorAll('[data-version-group]')],project=root.querySelector('[data-version-project]'),year=root.querySelector('[data-version-year]'),sizeSelect=root.querySelector('[data-version-size]'),prev=root.querySelector('[data-version-prev]'),next=root.querySelector('[data-version-next]'),status=root.querySelector('[data-version-status]'),controls=root.querySelector('[data-version-controls]'),viewButtons=[...root.querySelectorAll('[data-version-view]')];
  const params=new URLSearchParams(location.search);
  const defaultProject=root.dataset.defaultProject||'qemu';
  let {page,size}=readPageState(),view=params.get('view')==='detailed'?'detailed':'compact';
  project.value=defaultProject;
  [['project',project],['year',year]].forEach(([key,select])=>{const value=key==='project'&&params.get(key)==='all'?'':params.get(key);if([...select.options].some(option=>option.value===value))select.value=value});
  sizeSelect.value=String(size);
  const expandYears=()=>groups.forEach(group=>group.querySelector('[data-version-disclosure]').open=true);
  groups.forEach(group=>group.querySelector('summary').addEventListener('click',event=>{if(view==='detailed')event.preventDefault()}));
  const matchingItems=()=>items.filter(item=>(!project.value||item.dataset.project===project.value)&&(!year.value||item.dataset.year===year.value));
  const draw=(hash)=>{
    const matching=matchingItems(),pages=Math.max(1,Math.ceil(matching.length/size));
    page=view==='compact'?0:Math.max(0,Math.min(page,pages-1));
    const visible=new Set(view==='compact'?matching:matching.slice(page*size,(page+1)*size));
    items.forEach(item=>item.hidden=!visible.has(item));
    groups.forEach(group=>{
      const children=[...group.querySelectorAll('[data-version-item]')];
      group.hidden=!children.some(item=>visible.has(item));
      group.querySelector('[data-version-year-count]').textContent=`${children.filter(item=>matching.includes(item)).length} 个版本`;
      const disclosure=group.querySelector('[data-version-disclosure]'),summary=disclosure.querySelector('summary');
      if(view==='detailed'){disclosure.open=true;summary.tabIndex=-1;summary.setAttribute('aria-disabled','true')}
      else{summary.removeAttribute('tabindex');summary.removeAttribute('aria-disabled')}
    });
    root.dataset.view=view;viewButtons.forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.versionView===view)));
    controls.hidden=view==='compact'||!matching.length;
    root.querySelector('[data-version-count]').textContent=`${matching.length} 个功能版本`;
    root.querySelector('[data-version-empty]').hidden=matching.length>0;
    root.querySelector('[data-version-help]').textContent=view==='compact'?'精简显示所选范围内全部功能版本；切换详细视图可查看发布要点与相关统计。':'详细显示发布要点与相关统计，每页按功能版本计数。';
    status.textContent=`${page+1} / ${pages}`;prev.disabled=page===0;next.disabled=page===pages-1;
    writeListState({project:project.value===defaultProject?'':(project.value||'all'),year:year.value,view,page:page+1,per_page:size},hash);
  };
  [project,year].forEach(select=>select.addEventListener('change',()=>{page=0;expandYears();draw('')}));
  viewButtons.forEach(button=>button.addEventListener('click',()=>{view=button.dataset.versionView;page=0;expandYears();draw('')}));
  sizeSelect.addEventListener('change',()=>{size=Number(sizeSelect.value);page=0;draw('');scrollList(root.querySelector('.version-controls'))});
  const move=delta=>{page+=delta;draw('');scrollList(root.querySelector('.version-controls'))};
  prev.addEventListener('click',()=>move(-1));next.addEventListener('click',()=>move(1));
  const revealHash=()=>{
    let id;try{id=decodeURIComponent(location.hash.slice(1))}catch{return}
    const target=document.getElementById(id);if(!target||!root.contains(target))return;
    let entry=target.closest('[data-version-item]');
    if(!entry&&target.matches('[data-version-group]'))entry=[...target.querySelectorAll('[data-version-item]')].find(item=>!project.value||item.dataset.project===project.value)||target.querySelector('[data-version-item]');
    if(!entry)return;
    if(project.value&&project.value!==entry.dataset.project)project.value='';
    if(year.value&&year.value!==entry.dataset.year)year.value='';
    entry.closest('[data-version-disclosure]').open=true;
    page=Math.floor(matchingItems().indexOf(entry)/size);draw();
    requestAnimationFrame(()=>scrollList(target));
  };
  document.querySelectorAll('[data-version-jump]').forEach(link=>link.addEventListener('click',event=>{event.preventDefault();writeListState({},link.hash);revealHash()}));
  window.addEventListener('hashchange',revealHash);draw();if(location.hash)revealHash();
});
const topicDrawers=new Map();
document.querySelectorAll('[data-topic-group]').forEach(root=>{
  const toggle=root.querySelector('[data-topic-group-toggle]'),content=root.querySelector('[data-topic-group-content]');if(!toggle||!content)return;
  const draw=expanded=>{toggle.setAttribute('aria-expanded',String(expanded));toggle.title=`${expanded?'收起':'展开'}${toggle.textContent.trim()}`;content.hidden=!expanded;root.classList.toggle('is-collapsed',!expanded)};
  topicDrawers.set(root.id,draw);toggle.addEventListener('click',()=>draw(toggle.getAttribute('aria-expanded')!=='true'));draw(toggle.getAttribute('aria-expanded')==='true');
});
const expandTopicHash=hash=>{const draw=topicDrawers.get(hash.replace(/^#/,''));if(draw)draw(true)};
document.querySelectorAll('[data-topic-nav] a[href^="#"]').forEach(link=>link.addEventListener('click',()=>expandTopicHash(link.hash)));
window.addEventListener('hashchange',()=>expandTopicHash(window.location.hash));expandTopicHash(window.location.hash);
if(window.matchMedia('(max-width:620px)').matches){
  const currentNav=document.querySelector('.main-nav a.on');if(currentNav)currentNav.scrollIntoView({block:'nearest',inline:'center'});
}
const backToTop=document.querySelector('[data-back-to-top]');
if(backToTop){
  let backToTopFrame=0;const siteFooter=document.querySelector('.site-foot');
  const drawBackToTop=()=>{let footerOverlap=0;if(siteFooter){const footerRect=siteFooter.getBoundingClientRect();footerOverlap=Math.max(0,Math.min(footerRect.height,window.innerHeight-footerRect.top))}backToTop.style.setProperty('--footer-offset',`${Math.round(footerOverlap)}px`);const scrollRange=Math.max(0,document.documentElement.scrollHeight-window.innerHeight);const longPage=scrollRange>0;const revealAt=Math.min(Math.max(480,window.innerHeight*.75),scrollRange*.6);const pastThreshold=window.scrollY>revealAt;backToTop.classList.toggle('is-visible',longPage&&pastThreshold);backToTopFrame=0};
  const requestBackToTopDraw=()=>{if(!backToTopFrame)backToTopFrame=window.requestAnimationFrame(drawBackToTop)};
  backToTop.addEventListener('click',()=>window.scrollTo({top:0,behavior:window.matchMedia('(prefers-reduced-motion:reduce)').matches?'auto':'smooth'}));
  window.addEventListener('scroll',requestBackToTopDraw,{passive:true});window.addEventListener('resize',requestBackToTopDraw);if('ResizeObserver'in window)new ResizeObserver(requestBackToTopDraw).observe(document.body);drawBackToTop();
}
