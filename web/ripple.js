// M3 press state: 150ms touch intent delay, 450ms growth, 225ms minimum visibility.
// Input/release lifecycle follows Material Web; see docs/motion.md for sources.
(() => {
  const selector = 'button,.btn,.ga-nav-item,.segment,.history-item,.md-select-option,.status-option';
  const presses = new Map();
  let keyboard = null;
  function start(node, point) {
    if (node.disabled || node.getAttribute('aria-disabled') === 'true' || !window.AppMotion?.full()) return null;
    const rect = node.getBoundingClientRect();
    if (!rect.width || !rect.height) return null;
    if (getComputedStyle(node).position === 'static') node.style.position = 'relative';
    node.style.overflow = 'hidden';
    const x = point ? point.clientX-rect.left : rect.width/2, y = point ? point.clientY-rect.top : rect.height/2;
    const radius = Math.hypot(Math.max(x,rect.width-x),Math.max(y,rect.height-y));
    const surface = document.createElement('span'), ink = document.createElement('span');
    surface.className = 'm3-ripple-surface'; surface.setAttribute('aria-hidden','true');
    ink.className = 'm3-ripple-ink';
    Object.assign(ink.style,{width:`${radius*2}px`,height:`${radius*2}px`,left:`${x-radius}px`,top:`${y-radius}px`});
    surface.append(ink);node.append(surface);
    window.AppMotion.play(ink,[{transform:'scale(.2)'},{transform:'scale(1)'}],{duration:450,easing:'cubic-bezier(.2,0,0,1)'});
    const created = performance.now();
    let finished = false;
    const release = () => {
      if (finished) return;
      finished = true;
      const delay = Math.max(0,225-(performance.now()-created));
      const animation = window.AppMotion.play(surface,[{opacity:1},{opacity:0}],{duration:150,delay,easing:'linear',fill:'forwards'});
      if (animation) animation.finished.catch(()=>{}).then(()=>surface.remove());
      else surface.remove();
    };
    setTimeout(()=>{release();setTimeout(()=>surface.remove(),400);},5000);
    return release;
  }
  document.addEventListener('pointerdown',event=>{
    if (event.button !== 0) return;
    const node=event.target.closest(selector); if(!node || node.disabled)return;
    const press={node,event,release:null,timer:null};
    if(event.pointerType==='touch')press.timer=setTimeout(()=>{press.release=start(node,event);},150);
    else press.release=start(node,event);
    presses.set(event.pointerId,press);
  },{passive:true});
  function end(event,cancel=false) {
    const press=presses.get(event.pointerId);if(!press)return;
    clearTimeout(press.timer);presses.delete(event.pointerId);
    if(!cancel && !press.release)press.release=start(press.node,press.event);
    press.release?.();
  }
  document.addEventListener('pointerup',event=>end(event),{passive:true});
  document.addEventListener('pointercancel',event=>end(event,true),{passive:true});
  document.addEventListener('pointermove',event=>{
    const press=presses.get(event.pointerId);
    if(press && event.pointerType==='touch' && Math.hypot(event.clientX-press.event.clientX,event.clientY-press.event.clientY)>10)end(event,true);
  },{passive:true});
  document.addEventListener('keydown',event=>{
    if(event.repeat || !['Enter',' '].includes(event.key))return;
    const node=event.target.closest(selector);if(node)keyboard=start(node);
  });
  document.addEventListener('keyup',event=>{if(['Enter',' '].includes(event.key)){keyboard?.();keyboard=null;}});
  window.addEventListener('blur',()=>{
    for(const press of presses.values()){clearTimeout(press.timer);press.release?.();}presses.clear();keyboard?.();keyboard=null;
  });
})();
