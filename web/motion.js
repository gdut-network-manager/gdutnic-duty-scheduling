// M3 motion tokens and lifecycle helpers. Sources and platform limits: docs/motion.md.
(function () {
  'use strict';
  function spring(stiffness, damping, samples = 80) {
    const w = Math.sqrt(stiffness), wd = w * Math.sqrt(1 - damping * damping);
    const value = t => damping === 1 ? 1 - (1 + w * t) * Math.exp(-w * t)
      : 1 - Math.exp(-damping * w * t) * (Math.cos(wd * t) + damping * w / wd * Math.sin(wd * t));
    let seconds = 0.1;
    // Settle position AND velocity, so a zero crossing cannot end a spring early.
    while (seconds < 2 && (Math.abs(1-value(seconds)) > .001 || Math.abs(value(seconds+.001)-value(seconds)) > .0001)) seconds += .01;
    const values = Array.from({length:samples+1}, (_,i) => i === samples ? 1 : value(seconds*i/samples));
    return {duration:Math.round(seconds*1000), easing:`linear(${values.map(v=>v.toFixed(5)).join(',')})`};
  }
  if (typeof module === 'object' && module.exports) module.exports = {spring};
  if (typeof document === 'undefined') return;
  const root = document.documentElement, running = new Set(), channels = new WeakMap(), profiles = {};
  for (const [name, stiffness, damping] of [['spatial-fast',800,.6],['spatial',380,.8],['spatial-slow',200,.8],['effects-fast',3800,1],['effects',1600,1],['effects-slow',800,1]]) {
    const spec = spring(stiffness,damping);
    profiles[name] = {...spec, easing:CSS.supports('animation-timing-function',spec.easing) ? spec.easing : 'cubic-bezier(.2,0,0,1)'};
    if (CSS.supports('animation-timing-function',spec.easing)) root.style.setProperty(`--motion-${name}`,spec.easing);
    root.style.setProperty(`--motion-${name}-duration`,`${spec.duration}ms`);
  }
  const full = () => root.dataset.motion === 'full';
  function play(element, frames, options = {}) {
    const {channel = 'default', ...timing} = options;
    let active = channels.get(element);
    if (!active) { active = new Map(); channels.set(element, active); }
    const previous = active.get(channel);
    // Retarget from the currently rendered frame before cancelling, avoiding a jump.
    if (previous?.playState === 'running' && Array.isArray(frames) && frames.length) {
      const current = getComputedStyle(element), first = {...frames[0]};
      for (const property of Object.keys(first)) {
        if (!['offset','easing','composite'].includes(property) && current[property]) first[property] = current[property];
      }
      frames = [first, ...frames.slice(1)];
    }
    previous?.cancel();
    if (!full() || document.hidden || !element.animate) return null;
    const animation = element.animate(frames,{...profiles.effects,...timing});
    active.set(channel,animation); running.add(animation);
    animation.finished.catch(()=>{}).finally(()=>{
      running.delete(animation);
      if(active.get(channel)===animation)active.delete(channel);
    });
    return animation;
  }
  function menu(surface, opening, upwards) {
    // Read bounds once before starting animations; animate only visible menu rows.
    const bounds = surface.getBoundingClientRect();
    const items = [...surface.querySelectorAll('.md-select-option:not([hidden])')].filter(item=>{
      const rect = item.getBoundingClientRect();
      return rect.bottom > bounds.top && rect.top < bounds.bottom;
    });
    if (upwards) items.reverse();
    const collapsed = upwards ? 'inset(65% 0 0 0)' : 'inset(0 0 65% 0)';
    const animation = play(surface, opening
      ? [{clipPath:collapsed},{clipPath:'inset(0)'}]
      : [{clipPath:'inset(0)'},{clipPath:collapsed}],
      {...profiles[opening ? 'spatial' : 'spatial-fast'],channel:'geometry'});
    play(surface,opening?[{opacity:0},{opacity:1}]:[{opacity:1},{opacity:0}],
      {...profiles[opening?'effects':'effects-fast'],channel:'effects'});
    items.forEach((item,i)=>play(item,opening?[{opacity:0},{opacity:1}]:[{opacity:1},{opacity:0}],
      {...profiles['effects-fast'],delay:opening?Math.min(i*20,120):0,fill:'both'}));
    return animation;
  }
  window.AppMotion = {full,play,menu,profiles};
  new MutationObserver(()=>{
    if (!full()) {
      for (const animation of document.getAnimations()) animation.cancel();
      for (const animation of running) animation.cancel();
    }
  })
    .observe(root,{attributes:true,attributeFilter:['data-motion']});
  // Page length must not change the entrance origin or move viewport-fixed controls.
  new MutationObserver(records=>{
    const entering = new Set(records.filter(record => record.oldValue !== null)
      .map(record=>record.target)
      .filter(target=>!target.hidden && target.matches('section[id$="Page"], #dutyPage')));
    for (const target of entering) {
      const scroller = target.closest('.ga-main-body');
      if (scroller) { scroller.scrollTop=0;scroller.scrollLeft=0; }
      // Opacity does not turn the page into a containing block for its fixed FAB.
      play(target,[{opacity:0},{opacity:1}],{...profiles.effects,channel:'effects'});
      const content = [...target.children].filter(child=>!child.hidden
        && getComputedStyle(child).position !== 'fixed');
      for (const child of content) {
        play(child,[{transformOrigin:'50% 0%',transform:'scale(.98)'},
          {transformOrigin:'50% 0%',transform:'none'}],{...profiles.spatial,channel:'geometry'});
      }
    }
  }).observe(document.body,{subtree:true,attributes:true,attributeOldValue:true,attributeFilter:['hidden']});
})();
