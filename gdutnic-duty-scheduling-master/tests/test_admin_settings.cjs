const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync(`${__dirname}/../web/settings.js`, 'utf8');
const helper = source.slice(source.indexOf('function applySettingsPermissions()'), source.indexOf('window.applySettingsPermissions ='));

for (const granted of [['sso'], ['checkin_manage'], ['sso','checkin_manage']]) {
  test(`merged settings only show and load authorized sections: ${granted.join(', ')}`, () => {
    const nodes = Object.fromEntries(['rulesForm','sessionTimeoutForm','clockForm','smtpForm','checkinSettingsSection','ssoSettingsSection'].map(id=>[id,{hidden:true}]));
    nodes.settingsPage = {hidden:false};
    const calls = [];
    const context = {$:id=>nodes[id],adminPermissions:granted,
      canRules:false,canClock:false,canSmtp:false,clockTimer:0,
      clearInterval(){},setInterval(){},
      loadSmtp(){calls.push('smtp');},loadRules(){calls.push('rules');},
      loadSessionTimeout(){calls.push('session');},loadClock(){calls.push('clock');},
      loadSsoSettings(){calls.push('sso');},loadCheckinAccess(){calls.push('checkin_manage');}};
    context.window=context;
    vm.createContext(context);
    vm.runInContext(helper,context);
    context.applySettingsPermissions();
    assert.equal(nodes.ssoSettingsSection.hidden,!granted.includes('sso'));
    assert.equal(nodes.checkinSettingsSection.hidden,!granted.includes('checkin_manage'));
    assert.deepEqual(calls.sort(),[...granted].sort());
    for (const id of ['rulesForm','sessionTimeoutForm','clockForm','smtpForm']) assert.equal(nodes[id].hidden,true);
  });
}
