const {test} = require('node:test');
const assert = require('node:assert/strict');
const {parseCsv, rosterFromCsv, rosterCsv, splitNames, ROSTER_TEMPLATE, readRosterFile} = require('../web/weekly-import.js');

const header = '班次,星期一,星期二,星期三,星期四,星期五,星期六,星期日';
const row = n => `第${n}班,甲${n}/乙${n},丙,丁/戊,己,,庚,辛`;

test('slash-separated names become separate people per cell', () => {
  const rows = rosterFromCsv(`${header}\n${row(1)}`);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].label, '第1班');
  assert.deepEqual(rows[0].days[0], ['甲1', '乙1']);
  assert.deepEqual(rows[0].days[1], ['丙']);
  assert.deepEqual(rows[0].days[2], ['丁', '戊']);
  assert.deepEqual(rows[0].days[3], ['己']);
  assert.deepEqual(rows[0].days[4], []);
  assert.deepEqual(rows[0].days[6], ['辛']);
});

test('Excel BOM, CRLF, full-width slash and quoted commas are accepted', () => {
  const text = '﻿' + [header, '第1班,甲／乙,"丙,丁",戊,,,,己'].join('\r\n');
  const rows = rosterFromCsv(text);
  assert.deepEqual(rows[0].days[0], ['甲', '乙']);
  assert.deepEqual(rows[0].days[1], ['丙', '丁']);
  assert.deepEqual(rows[0].days[2], ['戊']);
  assert.deepEqual(rows[0].days[6], ['己']);
});

test('tab-separated spreadsheet exports are recognized', () => {
  const rows = rosterFromCsv([header.replaceAll(',', '\t'), row(1).replaceAll(',', '\t')].join('\n'));
  assert.deepEqual(rows[0].days[0], ['甲1', '乙1']);
});

test('a Monday-to-Friday roster leaves the weekend cells empty', () => {
  const weekdaysOnly = '班次,星期一,星期二,星期三,星期四,星期五';
  const rows = rosterFromCsv(`${weekdaysOnly}\n第1班,甲/乙,丙,,丁,`);
  assert.deepEqual(rows[0].days, [['甲', '乙'], ['丙'], [], ['丁'], [], [], []]);
});

test('weekday headers may be written as 周一 or 星期一', () => {
  const monday = header.replace('星期一', '周一').replace('星期二', '周二');
  assert.deepEqual(rosterFromCsv(`${monday}\n${row(2)}`)[0].days[1], ['丙']);
  assert.deepEqual(rosterFromCsv(`${monday}\n${row(2)}`)[0].days[0], ['甲2', '乙2']);
});

test('missing weekday columns and empty files report readable errors', () => {
  assert.throws(() => rosterFromCsv('班次,甲,乙'), /第一行需标明/);
  assert.throws(() => rosterFromCsv(''), /内容为空/);
  assert.throws(() => rosterFromCsv(header), /未找到排班数据/);
  // Rows numbered 1–4 are the only valid shift labels; extra numeric shift rows are capped.
  const five = ['1','2','3','4','5'].map(label => `${label},甲,,,,,,`).join('\n');
  assert.throws(() => rosterFromCsv(`${header}\n${five}`), /最多 4 个班次/);
});

test('the downloadable template round-trips through the parser', () => {
  const csv = rosterCsv();
  assert.ok(csv.startsWith('﻿'));
  const rows = rosterFromCsv(csv);
  assert.equal(rows.length, 4);
  assert.deepEqual(rows.map(r => r.label), ['第1班', '第2班', '第3班', '第4班']);
  // The shipped template starts blank; only the naming hint row follows the shifts.
  assert.ok(rows.every(r => r.days.every(names => names.length === 0)));
  assert.equal(ROSTER_TEMPLATE[6][0], '若多人值班需按照 A/B/C的格式标注');
});

test('names split on newlines, commas and Chinese separators as well as slash', () => {
  assert.deepEqual(splitNames('甲/乙、丙，丁\n戊;己'), ['甲', '乙', '丙', '丁', '戊', '己']);
});

test('the new template ignores blank spacer rows and the naming hint row', () => {
  const csv = rosterCsv();
  const rows = rosterFromCsv(csv);
  assert.deepEqual(rows.map(r => r.label), ['第1班', '第2班', '第3班', '第4班']);
  assert.ok(rows.every(r => r.days.every(names => names.length === 0)));
  // Filling a row still parses, and the hint row never becomes a fifth shift.
  const filled = csv.replace('第2班,,,,,,,', '第2班,甲/B,,,,,,');
  const parsed = rosterFromCsv(filled);
  assert.equal(parsed.length, 4);
  assert.deepEqual(parsed[1].days[0], ['甲', 'B']);
});

test('title rows above the weekday header are skipped', () => {
  const csv = ['某某协会每周值班表', '制表：办公室', '班次,周一,周二,周三,周四,周五', '第1班,甲,,,,乙', '备注：多人用/分隔'].join('\n');
  const rows = rosterFromCsv(csv);
  assert.equal(rows.length, 1);
  assert.deepEqual(rows[0].days.slice(0, 5), [['甲'], [], [], [], ['乙']]);
});

test('a GBK-encoded file is decoded instead of showing mojibake', async () => {
  // Simulate what Chinese Excel saves: GBK bytes of the same header and names.
  const gbk = Uint8Array.from([0xB0, 0xE0, 0xB4, 0xCE, 0x2C, 0xD6, 0xDC, 0xD2, 0xBB, 0x0A,
    0xB5, 0xDA, 0x31, 0xB0, 0xE0, 0x2C, 0xD5, 0xC5, 0xC8, 0xFD]);
  const file = {arrayBuffer: async () => gbk.buffer.slice(gbk.byteOffset, gbk.byteOffset + gbk.byteLength)};
  const text = await readRosterFile(file);
  const rows = rosterFromCsv(text);
  assert.equal(rows.length, 1);
  assert.deepEqual(rows[0].days[0], ['张三']);
});

test('import maps shift labels to their slots when earlier rows are absent or reordered', async () => {
  const {importRoster} = require('../web/weekly-import.js');
  const inputs = new Map();
  global.$ = id => {
    if (!inputs.has(id)) inputs.set(id, {value: '旧人员'});
    return inputs.get(id);
  };
  try {
    for (const labels of [['第2班'], ['第四班', '2'], ['第1班', '第2班']]) {
      const text = [header, ...labels.map(label => `${label},${label === '第1班' ? '' : '乙'},,,,,,`)].join('\r\n');
      const buffer = new TextEncoder().encode(text);
      await importRoster({arrayBuffer: async () => buffer.buffer});
      assert.equal(inputs.get('weekly-members-0-0').value, '');
      assert.equal(inputs.get('weekly-members-1-0').value, '乙');
      assert.equal(inputs.get('weekly-members-2-0').value, '');
      assert.equal(inputs.get('weekly-members-3-0').value, labels.includes('第四班') ? '乙' : '');
    }
  } finally {
    delete global.$;
  }
});

test('duplicate and out-of-range shift numbers are rejected', () => {
  assert.throws(() => rosterFromCsv(`${header}\n第2班,甲\n第二班,乙`), /重复/);
  assert.throws(() => rosterFromCsv(`${header}\n5,甲`), /仅支持/);
});

test('script, formula and prototype payloads stay plain roster names', () => {
  globalThis.rosterExecuted = false;
  const payloads = ['<img onerror=rosterExecuted=true>', '=1+1', '__proto__', 'constructor'];
  for (const payload of payloads) {
    const rows = rosterFromCsv(`${header}\n第1班,${payload},,,,,,`);
    assert.deepEqual(rows[0].days[0], [payload]);
  }
  assert.equal(globalThis.rosterExecuted, false);
  assert.equal(Object.prototype.rosterExecuted, undefined);
  delete globalThis.rosterExecuted;
});
