const test = require('node:test');
const assert = require('node:assert/strict');
const layout = require('../web/push-layout.js');
test('enabled columns stay ahead of disabled columns, including after dragging', () => {
  const cols = [{key:'name',enabled:true},{key:'count',enabled:false},{key:'dates',enabled:true}];
  assert.deepEqual(layout.normalize(cols).map(c=>c.key), ['name','dates','count']);
  assert.deepEqual(layout.move(cols,'dates','name').map(c=>c.key), ['dates','name','count']);
  assert.equal(layout.move(cols,'count','name'), cols);
});
test('short release toggles sorting; long hold and scrolling do not sort', () => {
  assert.equal(layout.release(349,false,false),'sort');
  assert.equal(layout.release(350,false,false),'none');
  assert.equal(layout.release(120,true,false),'none');
  assert.equal(layout.release(800,true,true),'drag');
  const asc = layout.sort({key:'count',direction:'desc'},'name');
  assert.deepEqual(asc,{key:'name',direction:'asc'});
  assert.deepEqual(layout.sort(asc,'name'),{key:'name',direction:'desc'});
});
