// The board's table panels render in public/app.js, which touches the DOM at load, so the test lifts the pure block
// (esc, and the table helpers) out of the source and evaluates it on its own.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const src = readFileSync(new URL('../public/app.js', import.meta.url), 'utf8');
const escLine = src.split('\n').find(l => l.startsWith('function esc('));
const block = src.slice(src.indexOf('const MONTHS ='), src.indexOf('// ---- end table panels'));
const { shortDate, tableCell, tableNote, tableHtml } = new Function(`${escLine}\n${block}; return { shortDate, tableCell, tableNote, tableHtml };`)();

test('shortDate: this year drops the year, another keeps it, anything else passes through', () => {
  assert.equal(shortDate('2026-09-08', 2026), 'Sep 8');
  assert.equal(shortDate('2025-12-31', 2026), 'Dec 31, 2025');
  assert.equal(shortDate('soon', 2026), 'soon');
  assert.equal(shortDate('2026-13-01', 2026), '2026-13-01');
});

test('tableCell: money out is negative and flagged for the debt colour; money in is not', () => {
  assert.deepEqual(tableCell(52.1, 'money'), { text: '-$52.10', out: true });
  assert.deepEqual(tableCell(-6800, 'money'), { text: '$6,800.00', out: false });
  assert.deepEqual(tableCell(null, 'money'), { text: '' });
  assert.deepEqual(tableCell(1234567, 'number'), { text: '1,234,567' });
  assert.deepEqual(tableCell('x', 'money'), { text: 'x' });
});

test('tableNote: says when the board shows fewer rows than the query found', () => {
  assert.equal(tableNote({ total: 312 }, 50), 'showing 50 of 312');
  assert.equal(tableNote({ total: 5000, more: true }, 50), 'showing 50 of over 5,000');
  assert.equal(tableNote({ total: 1 }, 1), '1 row');
  assert.equal(tableNote({ total: 0 }, 0), 'no rows match');
});

test('tableHtml: every bank string is escaped, columns align as asked', () => {
  const html = tableHtml({ title: 'A <b>', table: { columns: [{ field: 'm', label: 'Merchant', format: 'text', align: 'left' }, { field: 'a', label: 'Amt', format: 'money', align: 'right' }] },
    data: { values: [{ m: '<img src=x onerror=alert(1)>', a: 9 }] } }, 2026);
  assert.ok(!html.includes('<img') && !html.includes('<b>'));
  assert.ok(html.includes('&lt;img src=x onerror=alert(1)&gt;'));
  assert.ok(html.includes('<td class="r out">-$9.00</td>') && html.includes('<th scope="col" class="r">Amt</th>'));
});

test('tableHtml: a hand-edited entry with null rows or columns draws what it can, never throws', () => {
  const html = tableHtml({ title: 't', table: { columns: [null, { field: 'a', label: 'A', format: 'text' }] }, data: { values: [null, { a: 'x' }] } }, 2026);
  assert.ok(html.includes('>x</td>'));
  assert.ok(tableHtml({ title: 't', table: { columns: [] }, data: { values: { a: 1 } } }, 2026).includes('<tbody></tbody>'));
});
