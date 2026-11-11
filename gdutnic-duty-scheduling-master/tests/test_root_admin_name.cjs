const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

// schedule.js binds browser globals at load time, so extract just the helper.
const source = fs.readFileSync(`${__dirname}/../web/schedule.js`, 'utf8');
const helper = source.slice(source.indexOf('function syncRootNameField('),
                            source.indexOf('if (typeof module ===', source.indexOf('function syncRootNameField(')));
const context = {};
vm.createContext(context);
vm.runInContext(helper, context);
const {syncRootNameField} = context;

function field() {
  const label = {hidden: false};
  return {value: 'typed', required: true, placeholder: '', closest: () => label, label};
}

test('first setup lets the operator type a root name', () => {
  const input = field();
  syncRootNameField(input, false);
  assert.equal(input.label.hidden, false);
  assert.equal(input.required, false, 'a blank name is allowed and falls back to the built-in one');
  assert.equal(input.placeholder, '原始管理员');
  assert.equal(input.value, '', 'the field starts empty so the placeholder shows');
});

test('logging in keeps the name required and preserves what the user typed', () => {
  const input = field();
  syncRootNameField(input, true);
  assert.equal(input.required, true);
  assert.equal(input.placeholder, ' ');
  assert.equal(input.value, 'typed');
});

test('re-entering first setup never leaves a stale name behind', () => {
  const input = field();
  syncRootNameField(input, true);
  syncRootNameField(input, false);
  assert.equal(input.value, '');
});
