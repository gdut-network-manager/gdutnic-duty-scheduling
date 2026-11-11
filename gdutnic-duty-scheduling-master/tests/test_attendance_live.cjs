const {test} = require('node:test');
const assert = require('node:assert/strict');
const {createAttendanceLive} = require('../web/attendance-live.js');

function fixture({watching=false, healthy=true, timeout=90}={}) {
  let stamp=0, id=0, expired=0, updates=0;
  const timers=new Map(), calls=[], pending=[];
  const options={role:'member',now:()=>stamp,watching:()=>watching,
    setTimer:(fn,delay)=>{timers.set(++id,{fn,at:stamp+delay});return id;},
    clearTimer:key=>timers.delete(key),onExpired:()=>expired++,onAttendance:async()=>updates++,
    fetcher:async(path,init)=>{
      calls.push(path);
      if(path.includes('/heartbeat')) return {ok:healthy,status:healthy?200:503,json:async()=>({timeout_seconds:timeout})};
      return new Promise((resolve,reject)=>{
        pending.push(revision=>resolve({ok:true,status:200,json:async()=>({revision})}));
        init.signal.addEventListener('abort',()=>reject(new Error('aborted')),{once:true});
      });
    }};
  const controller=createAttendanceLive(options);
  const flush=async()=>{for(let i=0;i<10;i++) await Promise.resolve();};
  return {controller,calls,pending,flush,
    healthy:value=>healthy=value,watching:value=>watching=value,
    expired:()=>expired,updates:()=>updates,
    advance:async ms=>{const end=stamp+ms;await flush();for(;;){const next=[...timers].sort((a,b)=>a[1].at-b[1].at)[0];if(!next||next[1].at>end)break;stamp=next[1].at;timers.delete(next[0]);next[1].fn();await flush();}stamp=end;await flush();}};
}

test('global heartbeats run on every logged-in page and do not report user activity',async()=>{
  const f=fixture();f.controller.start();await f.advance(60000);
  assert.equal(f.expired(),0);
  assert.ok(f.calls.filter(path=>path.includes('/heartbeat')).length>=4);
  assert.equal(f.calls.some(path=>path.includes('/attendance/live')),false);
  assert.equal(f.calls.some(path=>path.includes('/activity')),false);
  f.controller.stop();
});

test('no responses for the configured duration expires once; logout stops all timers',async()=>{
  const f=fixture({timeout:120});f.controller.start();await f.flush();f.healthy(false);
  await f.advance(119999);assert.equal(f.expired(),0);
  await f.advance(1);assert.equal(f.expired(),1);
  const count=f.calls.length;await f.advance(120000);
  assert.equal(f.expired(),1);assert.equal(f.calls.length,count);
});

test('new revisions refresh local attendance without navigating or reloading',async()=>{
  const f=fixture({watching:true});f.controller.start();await f.flush();
  f.pending.shift()('one');await f.flush();await f.advance(0);
  assert.equal(f.updates(),1);
  f.pending.shift()('two');await f.flush();await f.advance(0);
  assert.equal(f.updates(),2);
  f.watching(false);f.pending.shift()('three');await f.flush();await f.advance(0);
  assert.equal(f.updates(),2);
  f.watching(true);await f.advance(2000);
  f.pending.shift()('three');await f.flush();
  assert.equal(f.updates(),3);
  f.controller.stop();
});

test('a failing long poll backs off instead of retrying every two seconds',async()=>{
  let stamp=0,id=0,fail=0;
  const timers=new Map(),delays=[];
  const options={role:'member',now:()=>stamp,watching:()=>true,
    setTimer:(fn,delay)=>{timers.set(++id,{fn,at:stamp+delay});return id;},
    clearTimer:key=>timers.delete(key),onExpired:()=>{},onAttendance:async()=>{},
    fetcher:async(path,init)=>{
      if(path.includes('/heartbeat')) return {ok:true,status:200,json:async()=>({timeout_seconds:90})};
      fail++;
      return {ok:false,status:502,json:async()=>({})};
    }};
  const controller=createAttendanceLive(options);
  const flush=async()=>{for(let i=0;i<10;i++) await Promise.resolve();};
  // Record each re-arm delay so the backoff sequence is visible.
  const original=options.setTimer;
  controller.start();await flush();
  for(let i=0;i<5;i++){
    const next=[...timers].filter(([,t])=>t.at>stamp).sort((a,b)=>a[1].at-b[1].at)[0];
    if(!next) break;
    delays.push(next[1].at-stamp);
    stamp=next[1].at;timers.delete(next[0]);next[1].fn();await flush();
  }
  const pollDelays=delays.filter(delay=>delay>=2000);
  assert.ok(pollDelays.length>=3,JSON.stringify(delays));
  assert.ok(pollDelays[2]>pollDelays[0],'poll delay must grow after repeated failures: '+JSON.stringify(pollDelays));
  controller.stop();
});
