(() => {
  'use strict';
  const card = document.querySelector('#memberAuth, #authCard, body.auth-pending > .admin-board');
  if (!card) return;

  const background = document.createElement('canvas');
  background.className = 'auth-background';
  background.setAttribute('aria-hidden', 'true');
  document.body.prepend(background);
  const context = background.getContext('2d');
  if (!context) return;

  const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const phase = Array.from({ length: 8 }, () => Math.random() * Math.PI * 2);
  const histogram = new Uint32Array(512);
  let pixels, field, frame = 0, last = 0, elapsed = 0;

  function resize() {
    // A small texture gives soft contours without a full-resolution canvas each frame.
    const ratio = Math.max(.25, Math.min(4, innerWidth / Math.max(1, innerHeight)));
    background.width = Math.round(160 * Math.sqrt(ratio));
    background.height = Math.round(160 / Math.sqrt(ratio));
    pixels = context.createImageData(background.width, background.height);
    field = new Float32Array(background.width * background.height);
    draw();
  }

  function draw() {
    const { width, height } = background;
    const t = elapsed / 18000;
    histogram.fill(0);
    const lobes = [0, 1, 2].map(i => ({
      x: .5 + .42 * Math.cos(t * .31 + phase[i]),
      y: .5 + .42 * Math.sin(t * .27 + phase[i + 3]),
      cos: Math.cos(t * .17 + phase[i + 2]),
      sin: Math.sin(t * .17 + phase[i + 2]),
      stretch: 1.2 + .4 * Math.sin(t * .23 + phase[i + 1]),
    }));
    for (let y = 0, p = 0; y < height; y++) {
      for (let x = 0; x < width; x++, p++) {
        const u = x / width, v = y / height;
        let value = 0;
        for (const lobe of lobes) {
          const dx = u - lobe.x, dy = v - lobe.y;
          const a = dx * lobe.cos + dy * lobe.sin;
          const b = dy * lobe.cos - dx * lobe.sin;
          value += Math.exp(-9 * (a * a / lobe.stretch + b * b * lobe.stretch));
        }
        // Moving waves bend the contours into ribbons and irregular islands.
        value += .18 * Math.sin(u * 8 + Math.sin(v * 6 + t * .3) + phase[6]);
        value += .12 * Math.cos(v * 9 - u * 3 + t * .21 + phase[7]);
        field[p] = value;
        histogram[Math.max(0, Math.min(511, Math.floor((value + .3) * 120)))]++;
      }
    }
    // Median normalization holds the visibly colored region near 50% at every frame,
    // including after a resize; the transition around that boundary remains soft.
    let count = 0, bin = 0;
    while (bin < 511 && count + histogram[bin] < field.length / 2) count += histogram[bin++];
    const median = (bin + .5) / 120 - .3;
    for (let p = 0; p < field.length; p++) {
      const amount = Math.max(0, Math.min(1, (field[p] - median) / .65 + .25));
      const blend = .5 + .5 * Math.sin((p % width) / width * 3 + t * .15 + phase[0]);
      const offset = p * 4;
      pixels.data[offset] = 146 + blend * 7;
      pixels.data[offset + 1] = 205 + blend * 8;
      pixels.data[offset + 2] = 213 + blend * 32;
      pixels.data[offset + 3] = Math.round(amount * 235);
    }
    context.putImageData(pixels, 0, 0);
  }

  function tick(now) {
    if (now - last >= 40) {
      elapsed += last ? Math.min(now - last, 100) : 0;
      last = now;
      draw();
    }
    frame = requestAnimationFrame(tick);
  }
  function sync() {
    background.hidden = card.hidden;
    cancelAnimationFrame(frame);
    last = 0;
    if (!card.hidden && !document.hidden && !motion.matches) frame = requestAnimationFrame(tick);
  }
  new MutationObserver(sync).observe(card, { attributes: true, attributeFilter: ['hidden'] });
  document.addEventListener('visibilitychange', sync);
  motion.addEventListener('change', sync);
  window.addEventListener('resize', resize);
  resize();
  sync();
})();
