const {test} = require('node:test');
const assert = require('node:assert/strict');
const {createIdleSession} = require('../web/idle-session.js');

function fixture() {
  let clock = 1000000, expires = clock + 1800000, loggedIn = true, reloads = 0;
  let serial = 0;
  const timers = new Map(), requests = [];
  const controller = createIdleSession({role:'admin', now:()=>clock,
    setTimer:(fn, delay)=>{ const id=++serial; timers.set(id,{fn,at:clock+delay}); return id; },
    clearTimer:id=>timers.delete(id), hide:()=>{}, reload:()=>reloads++, notify:()=>{},
    fetcher:async (path, options)=>{
      requests.push(path);
      if (path.includes('/logout')) loggedIn=false;
      if (path.includes('/activity')) {
        if (!loggedIn || clock >= expires) return {ok:false,status:401};
        expires = JSON.parse(options.body).activity_at * 1000 + 1800000;
      }
      return {ok:true,status:200,json:async()=>({logged_in:loggedIn && clock<expires, server_now:clock/1000, expires_at:expires/1000})};
    }
  });
  return {controller, requests, reloads:()=>reloads, expires:()=>expires,
    jump:ms=>{clock+=ms;}, extend:ms=>{expires=clock+ms;},
    advance:async ms=>{
      const end=clock+ms;
      for (;;) {
        const due=[...timers].sort((a,b)=>a[1].at-b[1].at)[0];
        if (!due || due[1].at>end) break;
        clock=due[1].at; timers.delete(due[0]); await due[1].fn();
      }
      clock=end;
    }
  };
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));

test('idle deadline logs out and reloads exactly once',async()=>{
  const f=fixture(); await f.controller.sync(); await f.advance(1800000);
  assert.equal(f.reloads(),1);
  assert.equal(f.requests.filter(p=>p.includes('/logout')).length,1);
});
test('polling does not report user activity or extend timeout',async()=>{
  const f=fixture(); await f.controller.sync(); const initial=f.expires();
  for(let i=0;i<10;i++){await f.advance(30000);await f.controller.sync();}
  assert.equal(f.expires(),initial);
  assert.equal(f.requests.filter(p=>p.includes('/activity')).length,0);
});
test('real interaction extends the idle lease',async()=>{
  const f=fixture();await f.controller.sync();await f.advance(600000);
  f.controller.activity();await flush();
  assert.equal(f.expires(),3400000);
  await f.advance(1200000);assert.equal(f.reloads(),0);
  await f.advance(600000);assert.equal(f.reloads(),1);
});
test('wake from sleep checks server expiry and refreshes',async()=>{
  const f=fixture();await f.controller.sync();f.jump(3600000);await f.controller.sync();
  assert.equal(f.reloads(),1);
});
test('another tab activity prevents premature logout',async()=>{
  const f=fixture();await f.controller.sync();await f.advance(1700000);f.extend(1800000);
  await f.advance(100000);assert.equal(f.reloads(),0);
  await f.advance(1700000);assert.equal(f.reloads(),1);
});
test('manual logout stops the idle timer without refreshing again',async()=>{
  const f=fixture();await f.controller.sync();f.controller.stop();await f.advance(3600000);
  assert.equal(f.reloads(),0);
});
