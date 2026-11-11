// CSV roster import for the weekly table: header row names the weekdays, first column names shifts.
const IMPORT_WEEKDAYS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];
const ROSTER_HEADER = ['班次', ...IMPORT_WEEKDAYS];
// Empty roster rows, a spacer line, then the naming hint; downloading writes a file in data/imports.
const ROSTER_HINT = '若多人值班需按照 A/B/C的格式标注';
const ROSTER_TEMPLATE = [
  ROSTER_HEADER,
  ...['第1班', '第2班', '第3班', '第4班'].map(label => [label, ...IMPORT_WEEKDAYS.map(() => '')]),
  Array(8).fill(''),
  [ROSTER_HINT, ROSTER_HINT, ...Array(6).fill('')],
];

// Normalize Excel variants: BOM, CRLF, full-width slash and full-width comma.
function normalizeRosterText(text) {
  return String(text || '')
    .replace(/^﻿/, '')
    .replace(/\r\n?/g, '\n')
    .replace(/／/g, '/')
    .replace(/，/g, ',');
}

// RFC 4180 parser with quote escapes; tabs are accepted when exporting from WPS/Excel.
function parseCsv(text) {
  const source = normalizeRosterText(text);
  let delimiter = ',';
  const firstLine = source.split('\n')[0] || '';
  if (firstLine.includes('\t') && !firstLine.includes(',')) delimiter = '\t';
  const rows = [];
  let row = [], field = '', quoted = false;
  for (let i = 0; i < source.length; i++) {
    const char = source[i];
    if (quoted) {
      if (char === '"' && source[i + 1] === '"') { field += '"'; i++; }
      else if (char === '"') quoted = false;
      else field += char;
    } else if (char === '"') quoted = true;
    else if (char === delimiter) { row.push(field); field = ''; }
    else if (char === '\n') { row.push(field); rows.push(row); row = []; field = ''; }
    else field += char;
  }
  if (field !== '' || row.length) { row.push(field); rows.push(row); }
  return rows.map(cells => cells.map(cell => cell.trim())).filter(cells => cells.some(cell => cell !== ''));
}

const splitNames = cell => cell.split(/[\/\n、,，;；]+/).map(n => n.trim()).filter(Boolean);

// Rows that name shifts; hint/footer rows from the template are ignored.
const SHIFT_LABEL = /^第?\s*[1-4一二三四]\s*班$/;
const isShiftRow = cells => SHIFT_LABEL.test((cells[0] || '').replace(/\s/g, '')) || /^\d+$/.test((cells[0] || '').trim());

// Returns {rows:[{label, days:[names×7]}]} or throws a readable error.
function rosterFromCsv(text, expectedLabels) {
  const rows = parseCsv(text);
  if (!rows.length) throw new Error('CSV 内容为空，请先下载模板并填写人员。');
  // Skip leading title rows so a decorated template still finds its weekday header.
  const headerIndex = rows.findIndex(cells => IMPORT_WEEKDAYS.some(day =>
    cells.some(cell => cell.replace(/\s/g, '') === day || cell.replace(/\s/g, '') === day.replace('周', '星期'))));
  if (headerIndex < 0) throw new Error('第一行需标明星期一至星期五，例如：班次,周一,周二,周三,周四,周五');
  const header = rows[headerIndex];
  // Accept both "星期一" and the shorter "周一" spellings.
  const alias = day => [day, day.replace('周', '星期'), day.replace('星期', '周')];
  // Missing columns (e.g. a Monday–Friday roster) are simply left blank for those days.
  const dayColumns = IMPORT_WEEKDAYS.map(day => {
    const names = alias(day).map(value => value.replace(/\s/g, ''));
    return header.findIndex(cell => names.includes(cell.replace(/\s/g, '')));
  });
  if (dayColumns.every(index => index < 0)) {
    throw new Error('第一行需标明星期一至星期五，例如：班次,星期一,星期二,星期三,星期四,星期五');
  }
  // Keep only real shift rows so blank spacer and hint rows never become shifts.
  const body = rows.slice(headerIndex + 1)
    .filter(cells => cells.some(cell => cell !== ''))
    .filter(isShiftRow);
  if (!body.length) throw new Error('未找到排班数据，请在第一列填写班次并补充人员。');
  if (body.length > 4) throw new Error(`最多 4 个班次，当前有 ${body.length} 行。`);
  const seen = new Set();
  return body.map((cells, index) => {
    const label = (cells[0] || '').replace(/\s/g, '') || (expectedLabels?.[index] || `第${index + 1}班`);
    const slot = shiftIndex(label);
    if (slot < 0) throw new Error('班次仅支持第1班至第4班');
    if (seen.has(slot)) throw new Error(`第${slot + 1}班重复，请每个班次只保留一行`);
    seen.add(slot);
    return { label, days: dayColumns.map(column => column < 0 ? [] : splitNames(cells[column] || '')) };
  });
}

function shiftIndex(label) {
  const number = String(label).replace(/\s/g, '').replace(/^第/, '').replace(/班$/, '');
  return ['1', '2', '3', '4'].includes(number) ? Number(number) - 1 : ['一', '二', '三', '四'].indexOf(number);
}

function csvCell(value) {
  const text = String(value ?? '');
  return /[",\n]/.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text;
}

function rosterCsv() {
  return '﻿' + ROSTER_TEMPLATE.map(row => row.map(csvCell).join(',')).join('\r\n') + '\r\n';
}

function downloadRosterTemplate() {
  const blob = new Blob([rosterCsv()], {type:'text/csv;charset=utf-8'});
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = '每周排班模板.csv';
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// Excel on Chinese Windows saves CSV as GBK; decode legacy files instead of showing mojibake.
async function readRosterFile(file) {
  const buffer = await file.arrayBuffer();
  try {
    return new TextDecoder('utf-8', {fatal: true}).decode(buffer);
  } catch (err) {
    return new TextDecoder('gb18030').decode(buffer);
  }
}

async function importRoster(file) {
  const rows = rosterFromCsv(await readRosterFile(file), Array.from({length:4}, (_, i) => `第${i + 1}班`));
  const values = Array.from({length:4}, (_, i) => ({label:`第${i + 1}班`, days:Array.from({length:7}, () => [])}));
  rows.forEach(row => { values[shiftIndex(row.label)] = row; });
  values.forEach((row, i) => row.days.forEach((names, day) => {
    const input = $(`weekly-members-${i}-${day}`);
    if (input) input.value = names.join('\n');
  }));
  return { shifts: rows.length, names: new Set(rows.flatMap(row => row.days.flat())).size };
}

if (typeof module === 'object' && module.exports) {
  module.exports = {parseCsv, rosterFromCsv, rosterCsv, splitNames, ROSTER_TEMPLATE, isShiftRow, readRosterFile, importRoster};
}

if (typeof $ === 'function' && $('importRoster') && typeof document !== 'undefined') {
  $('downloadRoster').onclick = downloadRosterTemplate;
  $('importRoster').onclick = () => $('rosterFile').click();
  $('rosterFile').onchange = async e => {
    const file = e.target.files && e.target.files[0];
    e.target.value = '';
    if (!file) return;
    const button = $('importRoster'); button.disabled = true;
    try {
      if (!weeklyLoaded) { drawWeekly(await api('/api/admin/weekly-roster')); weeklyLoaded = true; }
      const result = await importRoster(file);
      $('weeklyStatus').textContent = `已导入 ${result.shifts} 个班次、${result.names} 位人员，请核对后保存。`;
    } catch (err) {
      $('weeklyStatus').textContent = err.message;
    } finally { button.disabled = false; }
  };
}
