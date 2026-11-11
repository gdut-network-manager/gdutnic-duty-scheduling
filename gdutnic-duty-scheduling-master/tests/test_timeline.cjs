const {test}=require('node:test');
const assert=require('node:assert/strict');
const {geometry,interval,STYLE,format}=require('../web/shift-timeline.js');
const now=Date.parse('2026-11-03T15:25:00+08:00');
const sample={day:'2026-11-03',start:'14:00',end:'16:00'};
const near=(a,b)=>assert.ok(Math.abs(a-b)<1e-7,`${a} != ${b}`);

test('15:25 is exactly centered with 120 minutes on either side',()=>{
  const m=geometry(960,now,[sample]);
  near(m.x(now),480);near(m.x(now-120*60000),0);near(m.x(now+120*60000),960);
  near(m.ranges[0].x1,140);near(m.ranges[0].x2,620);
  near(m.ranges[0].x2-m.ranges[0].x1,480);
});
test('both blue strokes and the red marker have seven pixels of visible separation',()=>{
  const m=geometry(960,now,[sample]);
  near(STYLE.dutyY-STYLE.axisY-STYLE.stroke,7);
  near(m.tipY-(m.ranges[0].y+STYLE.stroke/2),7);
  assert.equal(STYLE.majorTick,14);
  assert.ok(m.currentLabelY>m.labels[0].y+12);
});
test('regular axis labels remain independent of coinciding duty endpoints',()=>{
  const m=geometry(960,now,[sample]);
  for(let i=1;i<m.ticks.length;i++)assert.equal(m.ticks[i].time-m.ticks[i-1].time,10*60000);
  const labels=m.references.map(t=>format(t.time));
  assert.ok(labels.includes('15:00'));assert.ok(labels.includes('17:00'));
  assert.ok(labels.includes('14:00'));assert.ok(labels.includes('16:00'));
  assert.deepEqual(m.references.map(t=>t.time),geometry(960,now,[]).references.map(t=>t.time));
  const offGrid=geometry(960,now,[{...sample,start:'14:07',end:'16:13'}]);
  assert.ok(!offGrid.references.some(t=>['14:07','16:13'].includes(format(t.time))));
  assert.deepEqual(m.labels.map(l=>format(l.time)),['14:00','16:00']);
});
test('every mark moves left continuously on the same time scale',()=>{
  const a=geometry(960,now,[sample]), b=geometry(960,now+1000,[sample]);
  near(a.ranges[0].x1-b.ranges[0].x1,960/(240*60));
  near(a.x(now)-b.x(now),960/(240*60));
});
test('mobile keeps exact proportions and separates close endpoint labels vertically',()=>{
  const m=geometry(280,now,[sample]);
  near(m.ranges[0].x2-m.ranges[0].x1,140);near(m.x(now),140);
  const short=geometry(280,now,[{...sample,start:'15:20',end:'15:30'}]);
  assert.ok(short.labels[1].y>short.labels[0].y);
  assert.ok(short.currentLabelY>=short.labels[1].y+20);
});
test('overnight shifts use real dates and remain centered after midnight',()=>{
  const shift={day:'2026-11-03',start:'23:00',end:'01:00'};
  const at=Date.parse('2026-11-04T00:25:00+08:00');
  const m=geometry(960,at,[shift]);
  near(m.ranges[0].x1,140);near(m.ranges[0].x2,620);
  assert.equal(interval(shift).end-interval(shift).start,2*60*60000);
  assert.deepEqual(m.labels.map(l=>format(l.time)),['23:00','01:00']);
});
test('overlapping shifts occupy separate rows and marker clears the lowest row',()=>{
  const m=geometry(960,now,[{...sample,start:'15:20',end:'16:30'},sample]);
  assert.equal(m.ranges[0].lane,0);assert.equal(m.ranges[1].lane,1);
  assert.ok(m.tipY>m.ranges[1].y+STYLE.stroke/2+6);
});
test('adjacent intervals share their boundary label',()=>{
  const m=geometry(960,now,[sample,{...sample,start:'16:00',end:'18:00'}]);
  assert.equal(m.ranges[0].lane,m.ranges[1].lane);
  assert.equal(m.labels.filter(l=>format(l.time)==='16:00').length,1);
});
test('empty, invalid and distant schedules never create fake duty ranges',()=>{
  assert.equal(geometry(390,now,[]).ranges.length,0);
  assert.equal(interval({...sample,end:'14:00'}),null);
  assert.equal(interval({...sample,end:'99:99'}),null);
  assert.equal(geometry(390,now,[{...sample,day:'2026-11-01'}]).ranges.length,0);
});
