/* Presentation and navigation shared by the CRM and Command Center. */
window.WorkspaceShell=(()=>{
 const key=document.documentElement.dataset.themeKey||'crm.workspace.theme';
 function theme(value){
  if(!['light','gray','dark'].includes(value))value='light';
  document.documentElement.dataset.theme=value;
  document.querySelectorAll('[data-workspace-theme]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.workspaceTheme===value)));
 }
 function update(){
  const current=document.querySelector('.ws-nav [aria-current="page"]');
  if(current?.closest('.ws-nav-more'))current.closest('details').open=true;
  document.body.dataset.workspaceView=new URLSearchParams(location.search).get('view')||'today';
  window.WorkspaceFavorites?.update();
 }
 document.addEventListener('DOMContentLoaded',()=>{try{theme(localStorage.getItem(key)||localStorage.getItem('crm.theme')||'light');}catch{theme('light');}update();});
 document.addEventListener('click',event=>{const b=event.target.closest('[data-workspace-theme]');if(!b)return;theme(b.dataset.workspaceTheme);try{localStorage.setItem(key,b.dataset.workspaceTheme);}catch{}b.closest('details').open=false;});
 window.addEventListener('storage',event=>{if(event.key===key)theme(event.newValue);});
 return {update};
})();
