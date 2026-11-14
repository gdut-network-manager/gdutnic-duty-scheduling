(() => {
  'use strict';
  const root = document.documentElement;
  const system = window.matchMedia('(prefers-color-scheme: dark)');
  let preference = null;
  let activeTransition = null, activeReveal = null, changing = false;

  function apply() {
    const dark = (preference || (system.matches ? 'dark' : 'light')) === 'dark';
    root.dataset.theme = dark ? 'dark' : 'light';
    const label = dark ? '深色' : '浅色';
    document.querySelectorAll('[data-theme-toggle]').forEach(button => {
      button.dataset.icon = dark ? 'weather-night' : 'weather-sunny';
      const description = `当前${label}模式，切换为${dark ? '浅色' : '深色'}`;
      button.setAttribute('aria-label', description);
      button.setAttribute('aria-pressed', String(dark));
      button.title = description;
    });
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.content = dark ? '#0F1417' : '#1B6D88';
  }
  async function switchTheme(button) {
    if (changing) return;
    const next = root.dataset.theme === 'dark' ? 'light' : 'dark';
    const update = () => { preference = next; apply(); };
    if (root.dataset.motion !== 'full' || !document.startViewTransition || document.hidden) {
      update(); return;
    }
    changing = true;
    const controls = [...document.querySelectorAll('[data-theme-toggle]')].map(node=>[node,node.disabled]);
    controls.forEach(([node])=>{node.disabled=true;});
    const rect = button.getBoundingClientRect();
    const x = Math.max(0,Math.min(innerWidth,rect.left+rect.width/2));
    const y = Math.max(0,Math.min(innerHeight,rect.top+rect.height/2));
    const radius = Math.ceil(Math.hypot(Math.max(x,innerWidth-x),Math.max(y,innerHeight-y)))+2;
    root.style.setProperty('--theme-reveal-x',`${x}px`);
    root.style.setProperty('--theme-reveal-y',`${y}px`);
    root.setAttribute('data-theme-transition','');
    try {
      // The browser retains the old snapshot, renders the new theme, then exposes both.
      const transition = document.startViewTransition(update);
      activeTransition = transition;
      // Attach rejection handlers immediately, including when a hidden tab skips capture.
      const finished = transition.finished.catch(()=>{});
      transition.updateCallbackDone.catch(()=>{});
      try {
        await transition.ready;
        if (root.dataset.motion !== 'full' || document.hidden) transition.skipTransition();
        else {
          const profile = window.AppMotion?.profiles?.['effects-slow'];
          activeReveal = root.animate([
            {clipPath:`circle(0px at ${x}px ${y}px)`},
            {clipPath:`circle(${radius}px at ${x}px ${y}px)`},
          ],{
            duration:Math.max(450,profile?.duration || 500),
            easing:profile?.easing || 'cubic-bezier(.2,0,0,1)',
            pseudoElement:'::view-transition-new(root)',fill:'forwards',
          });
          activeReveal.finished.catch(()=>{});
        }
      } catch (_) {
        // Unsupported snapshots/animation or an interrupted capture still apply the theme.
        transition.skipTransition();
      }
      await finished;
    } catch (_) {
      update();
    } finally {
      activeReveal?.cancel();activeReveal=null;activeTransition=null;
      root.removeAttribute('data-theme-transition');
      root.style.removeProperty('--theme-reveal-x');
      root.style.removeProperty('--theme-reveal-y');
      controls.forEach(([node,disabled])=>{node.disabled=disabled;});
      changing=false;
    }
  }
  const stopTransition = () => {
    activeTransition?.skipTransition();
    activeReveal?.cancel();
  };
  new MutationObserver(()=>{if(root.dataset.motion !== 'full')stopTransition();})
    .observe(root,{attributes:true,attributeFilter:['data-motion']});
  window.addEventListener('resize',stopTransition);
  document.addEventListener('visibilitychange',()=>{if(document.hidden)stopTransition();});
  apply(); // Apply before the first paint, including the login screen.
  document.addEventListener('DOMContentLoaded', () => {
    apply();
    document.querySelectorAll('[data-theme-toggle]').forEach(button => {
      button.addEventListener('click', () => { switchTheme(button); });
    });
  });
  const followSystem = () => { if (!preference) apply(); };
  if (system.addEventListener) system.addEventListener('change', followSystem);
  else system.addListener(followSystem);
  window.addEventListener('pageshow', event => {
    if (event.persisted) {
      stopTransition();
      preference = null;
      apply();
    }
  });
})();
