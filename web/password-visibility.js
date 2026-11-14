// Animate only the visibility symbol; the password input changes type immediately.
document.querySelectorAll('#memberLoginForm input[name="password"], #authForm input[name="password"]').forEach((input, index) => {
  const field = input.closest('.outlined-field');
  if (!field) return;
  field.classList.add('password-field');
  if (!input.id) input.id = `login-password-${index}`;
  const toggle = document.createElement('button');
  toggle.type = 'button'; toggle.className = 'password-visibility';
  toggle.setAttribute('aria-controls',input.id);
  const icon = name => {
    const node = document.createElement('span');
    node.className = 'password-visibility-icon';node.dataset.icon=name;
    node.setAttribute('aria-hidden','true');return node;
  };
  const top = ['polygon(0 0,0 0,0 0,0 0,0 0)',
    'polygon(0 0,100% 0,100% 0,0 100%,0 100%)',
    'polygon(0 0,100% 0,100% 100%,100% 100%,0 100%)'];
  const bottom = ['polygon(100% 100%,100% 100%,100% 100%,100% 100%,100% 100%)',
    'polygon(100% 100%,100% 0,100% 0,0 100%,0 100%)',
    'polygon(100% 100%,100% 0,0 0,0 0,0 100%)'];
  let activeIcon = null, animations = [], generation = 0;
  function stop() { generation++;animations.forEach(a=>a.cancel());animations=[]; }
  function render(visible, animate = false) {
    stop();
    const token = generation, name = visible ? 'eye-off-outline' : 'eye-outline';
    const start=input.selectionStart,end=input.selectionEnd,direction=input.selectionDirection,scroll=input.scrollLeft;
    input.type=visible?'text':'password';
    try { if(start!==null)input.setSelectionRange(start,end,direction); } catch (_) {}
    input.scrollLeft=scroll;
    const label=visible?'隐藏密码':'显示密码';
    toggle.dataset.icon=name;toggle.title=label;toggle.setAttribute('aria-label',label);
    toggle.setAttribute('aria-pressed',String(visible));
    const old = activeIcon, incoming=icon(name);
    toggle.replaceChildren(...(old?[old]:[]),incoming);
    activeIcon=incoming;
    const finish=()=>{if(generation===token){toggle.replaceChildren(incoming);animations.forEach(a=>a.cancel());animations=[];}};
    if(!animate || !old || document.documentElement.dataset.motion!=='full' || !incoming.animate){finish();return;}
    const profile=window.AppMotion?.profiles?.effects;
    const options={duration:Math.max(250,profile?.duration||300),easing:profile?.easing||'cubic-bezier(.2,0,0,1)',fill:'both'};
    const entering=visible?top:bottom, leaving=[...(visible?bottom:top)].reverse();
    animations=[incoming.animate(entering.map(clipPath=>({clipPath})),options),old.animate(leaving.map(clipPath=>({clipPath})),options)];
    Promise.all(animations.map(a=>a.finished.catch(()=>{}))).then(finish);
  }
  render(false);
  toggle.addEventListener('mousedown',event=>event.preventDefault());
  toggle.addEventListener('click',event=>{event.preventDefault();render(input.type==='password',true);});
  input.form?.addEventListener('reset',()=>render(false));
  new MutationObserver(()=>{
    if(document.documentElement.dataset.motion!=='full')render(input.type==='text');
  }).observe(document.documentElement,{attributes:true,attributeFilter:['data-motion']});
  field.append(toggle);
});
