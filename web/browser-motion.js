// Motion is selected by OS capability policy, independently of browser/preferences.
(function () {
  'use strict';
  function major(value) {
    return typeof value === 'string' && /^\d+(?:[._]\d+)*$/.test(value)
      ? Number(value.split(/[._]/)[0]) : null;
  }
  function motionForOS(nav, hints) {
    const ua = nav.userAgent || '';
    const ios = ua.match(/(?:CPU (?:iPhone )?OS|iPhone OS) ([\d_]+)/);
    if (ios) return major(ios[1]) >= 26 ? 'full' : 'reduced';
    // iPad desktop mode often masks its OS as macOS; do not guess its version.
    if (/Macintosh/.test(ua) && nav.maxTouchPoints > 1) return 'reduced';
    const platform = hints?.platform;
    const version = major(hints?.platformVersion);
    if (platform && version !== null) {
      const threshold = {Windows: 13, macOS: 26, iOS: 26, iPadOS: 26, Android: 13}[platform];
      // Windows UA-CH 13.0.0 is the first Windows 11 platform version.
      return threshold !== undefined && version >= threshold ? 'full' : 'reduced';
    }
    const android = ua.match(/Android ([\d.]+)/);
    if (android) return major(android[1]) >= 13 ? 'full' : 'reduced';
    const mac = ua.match(/Mac OS X ([\d_.]+)/);
    if (mac) return major(mac[1]) >= 26 ? 'full' : 'reduced';
    return 'reduced'; // Windows NT 10.0 cannot distinguish Windows 10 from 11.
  }
  if (typeof module === 'object' && module.exports) module.exports = {motionForOS};
  if (typeof document === 'undefined') return;
  const root = document.documentElement;
  root.dataset.motion = motionForOS(navigator);
  if (navigator.userAgentData?.getHighEntropyValues) {
    navigator.userAgentData.getHighEntropyValues(['platform', 'platformVersion'])
      .then(hints => { root.dataset.motion = motionForOS(navigator, hints); })
      .catch(() => { /* Keep the conservative UA result if permission is denied. */ });
  }
})();
