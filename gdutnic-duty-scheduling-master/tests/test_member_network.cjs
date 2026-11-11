const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

test('network restrictions survive check-in timer updates and recover after a network change', async () => {
  let access = {checkin:false, field:false};
  const context = {document:{hidden:false, addEventListener(){}},
    setInterval(){}, addEventListener(){}, dispatchEvent(){}, Event:class {},
    fetch:async()=>({ok:true,json:async()=>access})};
  context.window = context;
  vm.createContext(context);
  for (const name of ['member-network', 'checkin-window']) {
    vm.runInContext(fs.readFileSync(`${__dirname}/../web/${name}.js`, 'utf8'), context);
  }
  await context.refreshNetworkAccess();
  await new Promise(resolve=>setImmediate(resolve));
  const classes = new Set();
  const button = {disabled:false, classList:{
    toggle(name, value){value ? classes.add(name) : classes.delete(name);},
    add(name){classes.add(name);}
  }};
  const label = {textContent:''};
  const range = {start:0, end:10000}, limits = {early:0, late:1000, absent:2000};
  for (const now of [1, 100, 1000]) {
    context.syncCheckInButton(button,label,range,limits,now);
    assert.equal(button.disabled,true);
    assert.equal(label.textContent,'当前网络不可用');
    assert.equal(classes.has('network-blocked'),true);
  }
  access = {checkin:true, field:false};
  await context.refreshNetworkAccess();
  context.syncCheckInButton(button,label,range,limits,10);
  assert.equal(button.disabled,false);
  assert.equal(classes.has('network-blocked'),false);
  context.applyNetworkButton(button,'field',label);
  assert.equal(button.disabled,true);
  context.syncCheckInButton(button,label,range,limits,11000);
  assert.equal(button.disabled,true); // Allowed network cannot override the shift window.
  assert.equal(label.textContent,'未在签到时间内');
});

test('an open member page picks up manual 08:18 time and a changed 15-minute early window', async () => {
  const start = Date.parse('2026-11-10T08:30:00+08:00');
  const range = {start, end:start+105*60000};
  const label = {textContent:''};
  const classes = new Set();
  const button = {disabled:true, classList:{
    toggle(name, value){value ? classes.add(name) : classes.delete(name);},
    contains(name){return classes.has(name);}
  }};
  const form = {dataset:{shiftId:'1'},querySelector:selector=>selector==='button' ? button : label};
  const context = {
    admin:false,member:{username:'alice'},items:[{id:1}],rules:{early_minutes:10,late_minutes:10,absent_minutes:30},
    now:Date.parse('2026-11-10T20:00:00+08:00'),
    document:{hidden:false,querySelectorAll:()=>[form],addEventListener(){}},
    setInterval(){},addEventListener(){},$:()=>({hidden:false}),
    ShiftTimeline:{interval:()=>range},today:()=>'2026-11-10',
    api:async()=>({logged_in:true,now:{current:'2026-11-10T08:18:00+08:00'},
      rules:{early_minutes:15,late_minutes:10,absent_minutes:30}}),
  };
  context.applyMemberPermissions=granted=>context.memberPermissions=new Set(granted);
  context.clockNow=()=>context.now;
  context.syncClock=info=>context.now=Date.parse(info.current);
  context.window=context;
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(`${__dirname}/../web/checkin-window.js`,'utf8'),context);
  const schedule = fs.readFileSync(`${__dirname}/../web/schedule.js`,'utf8');
  vm.runInContext(schedule.slice(schedule.indexOf('function updateCheckInButtons()'),schedule.indexOf('function on(')),context);
  context.updateCheckInButtons();
  assert.equal(button.disabled,true);
  await context.refreshMemberCheckinState();
  assert.equal(context.rules.early_minutes,15);
  assert.equal(button.disabled,false);
  assert.equal(label.textContent,'确认签到');
  context.now=Date.parse('2026-11-10T08:14:59+08:00');
  context.updateCheckInButtons();
  assert.equal(button.disabled,true);
  context.now=Date.parse('2026-11-10T08:15:00+08:00');
  context.updateCheckInButtons();
  assert.equal(button.disabled,false);
});


test('missing action permissions keep the overview controls visible but disabled', async () => {
  const context = {document:{hidden:false,addEventListener(){}},setInterval(){},
    addEventListener(){},dispatchEvent(){},Event:class {},
    fetch:async()=>({ok:true,json:async()=>({checkin:true,field:true})})};
  context.window=context;
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(`${__dirname}/../web/member-network.js`,'utf8'),context);
  await new Promise(resolve=>setImmediate(resolve));
  const classes = new Set();
  const button = {disabled:false,classList:{toggle(name,value){value?classes.add(name):classes.delete(name);},add(name){classes.add(name);}}};
  const text = {textContent:''};
  context.memberPermissions=new Set([]);
  context.applyNetworkButton(button,'checkin',text);
  assert.equal(button.disabled,true);
  assert.equal(text.textContent,'暂无打卡权限');
  assert.equal(classes.has('permission-blocked'),true);
  context.memberPermissions=new Set(['duty']);
  button.disabled=false;
  context.applyNetworkButton(button,'checkin',text);
  assert.equal(button.disabled,false);
  assert.equal(classes.has('permission-blocked'),false);
  context.applyNetworkButton(button,'field',text);
  assert.equal(button.disabled,true);
  assert.equal(text.textContent,'暂无外勤权限');
});
