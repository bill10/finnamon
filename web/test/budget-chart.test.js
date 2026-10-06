// The budget trend's picker lives in public/app.js, which touches the DOM at load, so the test lifts the pure block.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const src = readFileSync(new URL('../public/app.js', import.meta.url), 'utf8');
const escLine = src.split('\n').find(l => l.startsWith('function esc('));
const block = src.slice(src.indexOf('const BUDGET_KEY ='), src.indexOf('// ---- end budget trend'));
const { budgetChoice, saveBudgetChoice, budgetSpec, budgetPicker, isBudgetTrend } = new Function(`${escLine}\n${block}; return { budgetChoice, saveBudgetChoice, budgetSpec, budgetPicker, isBudgetTrend };`)();

const store = () => { const m = new Map(); return { getItem: (k) => m.get(k) ?? null, setItem: (k, v) => m.set(k, String(v)) }; };
const spec = (n = 2) => ({ title: 'Budget trend: Dining', transform: [{ filter: { field: 'budget', equal: 'dining' } }],
  usermeta: { finnamon: { chart: 'budgets', budget: 'dining', budgets: ['coffee', 'dining', 'groceries', 'shopping', 'travel', 'gifts', 'kids'].slice(0, n).map(name => ({ name, over: name === 'dining' })) } } });

test('budgetChoice: the spec\'s default until this browser picks one; the pick persists while it is still a budget', () => {
  const st = store();
  assert.equal(budgetChoice(spec(), st), 'dining');
  saveBudgetChoice(spec(), 'coffee', st);
  assert.equal(budgetChoice(spec(), st), 'coffee', 'remembered across draws (and reloads: it is localStorage)');
  const asked = spec(); asked.usermeta.finnamon.arg = 'dining';
  assert.equal(budgetChoice(asked, st), 'dining', 'a newer ask (finnamon chart --spec budgets --budget dining) wins over an older pick');
  saveBudgetChoice(asked, 'coffee', st);
  assert.equal(budgetChoice(asked, st), 'coffee', 'a pick made over that ask wins again');
  saveBudgetChoice(spec(), 'rent', st);
  assert.equal(budgetChoice(spec(), st), 'dining', 'a removed budget falls back to the default');
  st.setItem('finnamon.budgetChart', 'coffee');
  assert.equal(budgetChoice(spec(), st), 'dining', 'junk in storage reads as no pick');
  const broken = { getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); } };
  assert.equal(budgetChoice(spec(), broken), 'dining');
  assert.doesNotThrow(() => saveBudgetChoice(spec(), 'coffee', broken));
});

test('budgetSpec: swaps the filter and the title, leaves the data alone', () => {
  const s = spec(), b = budgetSpec(s, 'coffee');
  assert.equal(b.title, 'Budget trend: Coffee');
  assert.deepEqual(b.transform, [{ filter: { field: 'budget', equal: 'coffee' } }]);
  assert.equal(s.transform[0].filter.equal, 'dining', 'the board entry is not mutated');
});

test('budgetPicker: chips up to six with the current one pressed, a select beyond; over-limit ones say so', () => {
  const chips = budgetPicker(spec(6), 'coffee');
  assert.ok(chips.includes('data-budget="coffee" aria-pressed="true" class="active"') && chips.includes('data-budget="dining" aria-pressed="false"'));
  assert.ok(chips.includes('over its limit') && !chips.includes('<select'));
  const sel = budgetPicker(spec(7), 'kids');
  assert.ok(sel.startsWith('<select') && sel.includes('<option value="kids" selected>') && sel.includes('dining (over)'));
  assert.ok(isBudgetTrend(spec()) && !isBudgetTrend({ usermeta: { finnamon: { chart: 'budgets', budgets: [] } } }));
});
