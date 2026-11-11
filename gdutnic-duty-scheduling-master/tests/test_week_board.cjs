const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const context = vm.createContext({document: {body: {dataset: {}}}, $: () => ({})});
vm.runInContext(fs.readFileSync(require.resolve('../web/week-board.js'), 'utf8'), context);
const layout = days => JSON.parse(JSON.stringify(context.weekBoardRows(days)));

test('missing first shifts leave holes rather than promoting later shifts', () => {
  const shift = (id, slot, start) => ({id, weekly_slot: slot, start, end: '17:00'});
  const days = [
    {shifts: [shift(1, 0, '08:30')]},
    {shifts: [shift(2, 1, '10:15'), shift(3, 2, '13:30'), shift(4, 3, '15:15')]},
    {shifts: []},
  ];
  const rows = layout(days);
  assert.equal(rows.length, 4);
  assert.deepEqual(rows[0].cells.map(cell => cell.map(s => s.id)), [[1], [], []]);
  assert.deepEqual(rows[1].cells.map(cell => cell.map(s => s.id)), [[], [2], []]);
  assert.equal(rows[3].cells[1][0].id, 4);
});

test('special shifts form one optional row sorted independently within each day', () => {
  const a = {id: 1, special: 1, weekly_slot: null, start: '08:30', end: '10:15'};
  const b = {id: 2, special: 1, weekly_slot: null, start: '10:15', end: '12:00'};
  const rows = layout([{shifts: [b, a]}, {shifts: [b]}, {shifts: []}]);
  assert.equal(rows.length, 5);
  assert.equal(rows[4].label, '特殊排班');
  assert.equal(rows[4].special, true);
  assert.deepEqual(rows[4].cells.map(cell => cell.map(s => s.id)), [[1, 2], [2], []]);
  assert.ok(rows.slice(0, 4).every(row => row.cells.every(cell => cell.length === 0)));
  assert.equal(layout([{shifts: []}]).length, 4);
});

test('ordinary generated shifts without weekly links remain in their named slots', () => {
  const first = {id: 1, special: 0, note: '第1班', start: '08:30', end: '10:15'};
  const second = {id: 2, special: 0, note: '第2班', start: '10:15', end: '12:00'};
  const rows = layout([{shifts: [first]}, {shifts: [second]}]);
  assert.equal(rows.length, 4);
  assert.deepEqual(rows[0].cells.map(cell => cell.map(s => s.id)), [[1], []]);
  assert.deepEqual(rows[1].cells.map(cell => cell.map(s => s.id)), [[], [2]]);
});

test('only explicit special source flags create a special row', () => {
  const normal = {id: 1, weekly_slot: null, special: 0, start: '08:30', end: '10:15'};
  const special = {...normal, id: 2, special: 1, note: '第1班'};
  const rows = layout([{shifts: [normal, special]}]);
  assert.equal(rows[0].cells[0][0].id, 1);
  assert.deepEqual(rows[4].cells[0].map(s => s.id), [2]);
  assert.equal(layout([{shifts: [normal]}]).length, 4);
});
