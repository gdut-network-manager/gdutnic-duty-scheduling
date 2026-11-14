/* SVG geometry uses real instants in Shanghai time; one pixel scale for all marks. */
(() => {
  'use strict';
  const MINUTE = 60000, HALF_WINDOW = 120 * MINUTE;
  const STYLE = Object.freeze({axisY:36, dutyY:45, stroke:2, majorTick:14,
    minorTick:6, markerGap:7, markerHeight:10, fontSize:12, labelWidth:48});
  const STATUS_COLOR = Object.freeze({late:'#b98100', absent:'#c62828', normal:'#235779', pending:'#235779'});
  const clock = new Intl.DateTimeFormat('en-GB', {
    timeZone:'Asia/Shanghai', hour:'2-digit', minute:'2-digit', hour12:false
  });
  const format = value => clock.format(value);

  function interval(shift) {
    if (!shift || !/^\d{4}-\d{2}-\d{2}$/.test(shift.day) ||
        !/^\d{2}:\d{2}$/.test(shift.start) || !/^\d{2}:\d{2}$/.test(shift.end)) return null;
    const start = Date.parse(`${shift.day}T${shift.start}:00+08:00`);
    let end = Date.parse(`${shift.day}T${shift.end}:00+08:00`);
    if (!Number.isFinite(start) || !Number.isFinite(end) || start === end) return null;
    if (end < start) end += 24 * 60 * MINUTE;
    const members = Array.isArray(shift.members) ? shift.members.filter(m => m && m.name) : [];
    return {start, end,
      names: members.map(m => ({name: m.name, status: m.status || 'normal'}))};
  }

  function geometry(width, now, shifts) {
    const left = now - HALF_WINDOW, right = now + HALF_WINDOW;
    const pixelsPerMs = width / (2 * HALF_WINDOW);
    const x = value => (value - left) * pixelsPerMs;
    // A small overscan keeps ticks and complete endpoints moving through the fade.
    const overscan = 30 * MINUTE;
    const ranges = shifts.map(interval).filter(Boolean)
      .filter(r => r.end >= left - overscan && r.start <= right + overscan)
      .sort((a,b) => a.start-b.start || a.end-b.end);
    const stacked = ranges.some(r => (r.end-r.start)*pixelsPerMs < STYLE.labelWidth+8);
    const laneStep = stacked ? 58 : 44;
    // Duty bars sit close under the axis; the names are drawn beside the start cap,
    // on the same line as the bar, so no extra row is reserved above it.
    const baseDutyY = STYLE.dutyY;
    const ends = [];
    const shared = new Set();
    const labels = [];
    ranges.forEach(range => {
      // Adjacent shifts share a cap/label; genuinely overlapping intervals get lanes.
      let lane = ends.findIndex(end => end === range.start ||
        (range.start-end)*pixelsPerMs >= STYLE.labelWidth+8);
      if (lane < 0) lane = ends.length;
      ends[lane] = range.end;
      range.lane = lane;
      range.y = baseDutyY + lane*laneStep;
      range.x1 = x(range.start); range.x2 = x(range.end);
      [range.start, range.end].forEach((time, index) => {
        const key = `${lane}:${time}`;
        if (shared.has(key)) return;
        shared.add(key);
        labels.push({time, x:x(time), y:range.y+STYLE.majorTick+15 +
          (index === 1 && (range.end-range.start)*pixelsPerMs < STYLE.labelWidth+8 ? 14 : 0)});
      });
    });
    const lastDutyY = baseDutyY + Math.max(0,ends.length-1)*laneStep;
    // Measure edge-to-edge, including half the blue stroke width.
    const tipY = lastDutyY + STYLE.stroke/2 + STYLE.markerGap;
    // The current-time label must clear both endpoint labels and the duty names below them.
    // Duty names sit on the same line as the start time label, immediately to its right,
    // so they read as "20:27 · 张三". Space is reserved for three names; the rest collapse.
    const nameRight = range => range.x1 + STYLE.labelWidth / 2 + 6;
    const names = ranges.filter(range => range.names.length).map(range => ({
      x: nameRight(range), y: range.y + STYLE.majorTick + 15, anchor: 'start',
      parts: range.names.slice(0, 3), more: Math.max(0, range.names.length - 3),
    }));
    const currentLabelY = Math.max(tipY+41, ...labels.map(l=>l.y+20), ...names.map(n=>n.y+20));
    const ticks = [];
    for (let t=Math.ceil((left-overscan)/(10*MINUTE))*10*MINUTE; t<=right+overscan; t+=10*MINUTE) {
      ticks.push({time:t, x:x(t), major:t % (60*MINUTE) === 0});
    }
    const references = [];
    const candidates = ticks.filter(t=>t.time % (30*MINUTE) === 0 &&
      t.x >= -STYLE.labelWidth/2 && t.x <= width+STYLE.labelWidth/2);
    // Axis labels depend only on the clock grid, never on duty endpoints.
    // Give whole-hour references priority, filling half hours when space permits.
    candidates.sort((a,b)=>Number(b.major)-Number(a.major) || a.time-b.time);
    for (const tick of candidates) {
      if (references.every(other=>Math.abs(other.x-tick.x)>=64)) references.push(tick);
    }
    return {width, now, left, right, x, pixelsPerMs, ranges, labels, names, ticks, references,
      tipY, currentLabelY, height:currentLabelY+14};
  }

  // The same pure geometry is exercised by Node tests without a DOM or test clock in production.
  if (typeof module === 'object' && module.exports) module.exports = {interval, geometry, STYLE, format};
  if (typeof document === 'undefined') return;
  window.ShiftTimeline = {interval};
  const host = document.getElementById('shiftTimeline');
  if (!host) return;
  const NS = 'http://www.w3.org/2000/svg';
  const node = (tag, attrs={}, text) => {
    const result = document.createElementNS(NS,tag);
    for (const [key,value] of Object.entries(attrs)) result.setAttribute(key,String(value));
    if (text !== undefined) result.textContent = text;
    return result;
  };
  const svg = node('svg', {role:'img', 'font-weight':300, 'font-size':STYLE.fontSize});
  host.replaceChildren(svg);
  const prefix = host.id;
  let shifts=[], model, moving, currentLabel, endpointLabels=[], nameLabels=[], frameId=0, lastLabel='';
  let lastMotion=0;
  const canDraw = () => !document.hidden && host.clientWidth > 0;

  function rebuild(now) {
    const width = host.clientWidth;
    // A missing or absurd clock value (e.g. a pinned date far away) must not break the SVG.
    if (!width || !Number.isFinite(now)) return;
    model = geometry(width,now,shifts);
    svg.setAttribute('viewBox',`0 0 ${width} ${model.height}`);
    svg.setAttribute('height',model.height);
    const defs = node('defs');
    const gradient = node('linearGradient',{id:`${prefix}-gradient`});
    [[0,0],[.08,1],[.92,1],[1,0]].forEach(([offset,opacity]) =>
      gradient.append(node('stop',{offset,'stop-color':'white','stop-opacity':opacity})));
    const mask = node('mask',{id:`${prefix}-fade`,maskUnits:'userSpaceOnUse',x:0,y:0,width,height:model.height});
    mask.append(node('rect',{width,height:model.height,fill:`url(#${prefix}-gradient)`}));
    const clip = node('clipPath',{id:`${prefix}-clip`});
    clip.append(node('rect',{width,height:model.height}));
    defs.append(gradient,mask,clip);
    const axis = node('g',{mask:`url(#${prefix}-fade)`,'aria-hidden':'true'});
    axis.append(node('path',{d:`M0 ${STYLE.axisY}H${width}`,stroke:'#b8dcec',
      'stroke-width':STYLE.stroke,fill:'none'}));
    const ticks = node('g');
    for (const tick of model.ticks) ticks.append(node('path',{
      d:`M${tick.x} ${STYLE.axisY}v-${tick.major?STYLE.majorTick:STYLE.minorTick}`,
      stroke:'#b8dcec','stroke-width':STYLE.stroke,fill:'none'}));
    for (const reference of model.references) ticks.append(node('text',{
      x:reference.x,y:12,'text-anchor':'middle',fill:'#7292a1'},format(reference.time)));
    axis.append(ticks);
    // Duty rows fade at both edges exactly like the axis, without a hard clip boundary.
    const duty = node('g',{mask:`url(#${prefix}-fade)`,'aria-hidden':'true'});
    const ranges = node('g');
    for (const range of model.ranges) {
      // Only these actual endpoints turn downward; clipping never creates false caps.
      ranges.append(node('path',{
        d:`M${range.x1} ${range.y+STYLE.majorTick}V${range.y}H${range.x2}V${range.y+STYLE.majorTick}`,
        stroke:'#235779','stroke-width':STYLE.stroke,'stroke-linejoin':'miter',fill:'none'}));
    }
    endpointLabels = model.labels.map(label => {
      const element = node('text',{
        x:label.x,y:label.y,'text-anchor':'middle',fill:'#235779',
        'data-endpoint':label.time},format(label.time));
      ranges.append(element);
      return {element, x:label.x, anchor:'middle'};
    });
    nameLabels = model.names.map(label => {
      const element = node('text',{
        x:label.x,y:label.y,'text-anchor':label.anchor || 'end','data-duty-names':''});
      label.parts.forEach((part, index) => {
        const last = index === label.parts.length - 1 && !label.more;
        const tspan = node('tspan',{fill:STATUS_COLOR[part.status] || STATUS_COLOR.normal},
          part.name + (last ? '' : '、'));
        element.append(tspan);
      });
      if (label.more) element.append(node('tspan',{fill:'#7292a1'}, `等${label.parts.length + label.more}人`));
      ranges.append(element);
      return {element, x:label.x, anchor:label.anchor || 'end', baseAnchor:label.anchor || 'end'};
    });
    duty.append(ranges);
    const current = node('g',{fill:'#932b37','aria-hidden':'true'});
    current.append(node('path',{
      d:`M${width/2} ${model.tipY}l-6 ${STYLE.markerHeight}h12Z`,'data-current-marker':''}));
    currentLabel = node('text',{x:width/2,y:model.currentLabelY,'text-anchor':'middle'});
    current.append(currentLabel);
    svg.replaceChildren(defs,axis,duty,current);
    moving = [ticks,ranges];
    lastLabel='';
    move(now);
  }

  function move(now) {
    if (!model || !Number.isFinite(now)) return;
    const dx = -(now-model.now)*model.pixelsPerMs;
    if (!Number.isFinite(dx)) return;
    for (const group of moving) group.setAttribute('transform',`translate(${dx} 0)`);
    // Keep a visible endpoint's label inside the viewport without moving its cap.
    rangesLabels(dx);
    const value = format(now);
    if (value !== lastLabel) {
      currentLabel.textContent=value;
      const visible=model.ranges.filter(r=>r.end>=now-HALF_WINDOW && r.start<=now+HALF_WINDOW);
      svg.setAttribute('aria-label',`当前时间 ${value}，左右各120分钟；${
        visible.map(r=>`${format(r.start)}至${format(r.end)}`).join('，') || '此时间范围内无排班'}`);
      lastLabel=value;
    }
  }

  function rangesLabels(dx) {
    for (const label of endpointLabels) {
      const x = label.x+dx;
      const edge = x<STYLE.labelWidth/2?'start':x>model.width-STYLE.labelWidth/2?'end':label.anchor;
      if (label.anchor!==edge) { label.element.setAttribute('text-anchor',edge); label.anchor=edge; }
    }
    // Names sit left of their start cap; near the left edge they flip to the right so
    // they are not clipped, and near the right edge they clamp to end-anchored.
    for (const label of nameLabels) {
      const x = label.x+dx;
      const edge = x < 4 ? 'start' : x > model.width - 4 ? 'end' : label.baseAnchor;
      if (label.anchor!==edge) { label.element.setAttribute('text-anchor',edge); label.anchor=edge; }
    }
  }

  // Uses the page clock when available so a pinned system time moves the axis too.
  const shiftNow = () => (typeof window.clockNow === 'function' ? window.clockNow() : Date.now());

  function frame() {
    frameId=0;
    if (!canDraw()) return;
    const now=shiftNow();
    // Rebuild infrequently; only two group transforms move between rebuilds.
    if (!model || Math.abs(now-model.now)>30*1000) rebuild(now);
    else if ((document.documentElement.dataset.motion !== 'reduced') || now-lastMotion>=1000) { move(now); lastMotion=now; }
    frameId=requestAnimationFrame(frame);
  }
  function refresh() {
    if (frameId) cancelAnimationFrame(frameId);
    frameId=0;
    if (!canDraw()) return;
    rebuild(shiftNow());
    frameId=requestAnimationFrame(frame);
  }
  window.updateShiftTimeline = data => { shifts=Array.isArray(data)?data:[]; refresh(); };
  // A clock change (manual system time) must rebuild, not just move by a few pixels.
  window.refreshShiftTimeline = () => { model = undefined; refresh(); };
  new ResizeObserver(refresh).observe(host);
  document.addEventListener('visibilitychange',refresh);
})();
