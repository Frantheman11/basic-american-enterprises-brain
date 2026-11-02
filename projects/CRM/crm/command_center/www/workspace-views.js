/* Per-person view preferences. Saved views sync to the account; scroll and last screen stay on this browser. */
window.WorkspaceViews=(()=>{
  function start(config){
    const key='crm.views.v1:'+document.baseURI+':'+config.kind+':'+config.person,serverKey=config.kind==='command'?'views.command':'views.crm';
    let saved={views:[]},timer,bar,synced=false,fill=()=>{},note={textContent:''};
    try{const value=JSON.parse(localStorage.getItem(key)||'null');if(value&&Array.isArray(value.views))saved=value;}catch{}
    function write(){try{localStorage.setItem(key,JSON.stringify(saved));return true;}catch{return false;}}
    const disclosures=()=>[...config.root.querySelectorAll('details[open]')].map(el=>el.id||el.dataset.group||el.dataset.dailyDisclosure||el.querySelector('summary')?.textContent).filter(Boolean);
    const route=()=>{const url=new URL(location.href);url.searchParams.delete('task');return url.pathname+url.search;};
    function persist(){saved.last={state:config.get(),y:scrollY,open:disclosures(),route:route()};write();}
    function restoreExtras(last){requestAnimationFrame(()=>{config.root.querySelectorAll('details').forEach(el=>{const id=el.id||el.dataset.group||el.dataset.dailyDisclosure||el.querySelector('summary')?.textContent;if((last.open||[]).includes(id))el.open=true;});window.scrollTo(0,Math.max(0,Number(last.y)||0));});}
    const initial=new URLSearchParams(location.search),explicit=initial.has('view')||initial.has('contact')||initial.has('company')||initial.has('task');
    if(saved.last?.state){config.set(saved.last.state,true);if(!initial.has('task')&&(!explicit||saved.last.route===route()))restoreExtras(saved.last);}
    const latest=()=>(window.WorkspacePrefs?.get(serverKey)?.value||saved.views).slice();
    // One change, applied to the newest list: the account first, this browser as the fallback.
    async function change(apply,message){
      const prefs=window.WorkspacePrefs,previous=saved.views.slice();
      let views=apply(previous);if(!views)return false;
      if(prefs&&synced&&prefs.available()){
        for(let attempt=0;attempt<2;attempt++){
          try{await prefs.set(serverKey,views);saved.views=views;write();fill();note.textContent=message;return true;}
          catch(error){if(error.code==='conflict'&&attempt===0){views=apply(latest());if(!views)return false;continue;}note.textContent='Could not save to your account: '+error.message+' Nothing was changed.';fill();return false;}
        }
      }
      saved.views=views;
      if(!write()){saved.views=previous;note.textContent='Browser storage is unavailable; view preferences could not be saved.';return false;}
      fill();note.textContent=message+(prefs?' This browser only until the account reconnects.':'');return true;
    }
    if(config.presets){
      bar=document.createElement('div');bar.className='workspace-views';bar.setAttribute('aria-label','Saved views');
      bar.innerHTML='<label>Saved view<select aria-label="Saved view"><option value="">Choose a view</option></select></label><details class="workspace-view-menu"><summary>Save / manage views</summary><div class="workspace-view-options"><label>View name<input aria-label="View name" maxlength="60" placeholder="Name these filters"></label><button type="button" data-save-view>Save view</button><button type="button" data-delete-view hidden>Remove saved view</button><p>Saved views are private to you and follow your account to every device.</p></div></details><small role="status" aria-live="polite"></small>';
      config.root.before(bar);
      const select=bar.querySelector('select'),name=bar.querySelector('input'),menu=bar.querySelector('details');note=bar.querySelector('[role=status]');
      fill=()=>{const current=select.value;select.replaceChildren(new Option('Choose a view',''));for(const [id,value] of Object.entries(config.presets))select.add(new Option(value.label,'preset:'+id));saved.views.slice(0,12).forEach((v,i)=>select.add(new Option(v.name,'saved:'+i)));if([...select.options].some(o=>o.value===current))select.value=current;bar.querySelector('[data-delete-view]').hidden=!select.value.startsWith('saved:');};
      fill();
      select.onchange=()=>{const [kind,id]=select.value.split(':');const choice=kind==='preset'?config.presets[id]:saved.views[Number(id)];if(!choice)return;config.set(choice.state,false);config.render();window.scrollTo(0,0);name.value=kind==='saved'?choice.name:'';bar.querySelector('[data-delete-view]').hidden=kind!=='saved';note.textContent='View loaded. Filters remain editable.';persist();};
      bar.querySelector('[data-save-view]').onclick=async()=>{
        const value=name.value.trim();if(!value){note.textContent='Enter a name for this view.';name.focus();return;}
        const row={name:value,state:config.get()};
        const ok=await change(list=>{const at=list.findIndex(v=>v.name===value);if(at>=0)list[at]=row;else{if(list.length>=12){note.textContent='Remove a saved view before adding another (12 maximum).';return null;}list.push(row);}return list;},'View saved to your account.');
        if(!ok)return;select.value='saved:'+saved.views.findIndex(v=>v.name===value);bar.querySelector('[data-delete-view]').hidden=false;menu.open=false;select.focus();
      };
      bar.querySelector('[data-delete-view]').onclick=async()=>{
        if(!select.value.startsWith('saved:'))return;const target=saved.views[Number(select.value.split(':')[1])]?.name;if(!target)return;
        const ok=await change(list=>list.filter(v=>v.name!==target),'Saved view removed; CRM records are unchanged.');
        if(!ok)return;name.value='';select.value='';bar.querySelector('[data-delete-view]').hidden=true;menu.open=false;select.focus();
      };
    }
    async function sync(){
      const prefs=window.WorkspacePrefs;if(!prefs)return;
      await prefs.ready();if(!prefs.available())return;
      const remote=prefs.get(serverKey);
      if(remote){saved.views=remote.value;write();}
      else if(saved.views.length){try{await prefs.set(serverKey,saved.views.slice(0,12));}catch{}}
      synced=true;fill();
    }
    document.addEventListener('prefs:updated',()=>{const remote=window.WorkspacePrefs?.get(serverKey);if(remote&&JSON.stringify(remote.value)!==JSON.stringify(saved.views)){saved.views=remote.value;write();fill();}});
    sync();
    for(const type of ['click','input','change','toggle'])document.addEventListener(type,()=>{clearTimeout(timer);timer=setTimeout(persist,180);},true);
    window.addEventListener('scroll',()=>{clearTimeout(timer);timer=setTimeout(persist,180);},{passive:true});
    window.addEventListener('pagehide',persist);
    return {persist};
  }
  return {start};
})();
