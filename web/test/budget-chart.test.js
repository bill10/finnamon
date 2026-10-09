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

test('budgetSpec: swaps the filter and drops the title (the picker is the title), leaves the data alone', () => {
  const s = spec(), b = budgetSpec(s, 'coffee');
  assert.equal(b.title, undefined);
  assert.deepEqual(b.transform, [{ filter: { field: 'budget', equal: 'coffee' } }]);
  assert.equal(s.transform[0].filter.equal, 'dining', 'the board entry is not mutated');
  assert.equal(s.title, 'Budget trend: Dining');
});

test('budgetPicker: one select in the title for any count, the current one selected; over-limit ones say so', () => {
  for (const n of [2, 7]) {
    const h = budgetPicker(spec(n), 'coffee');
    assert.ok(h.startsWith('<div class="btitle"><span>Budget trend:</span>') && h.includes('<option value="coffee" selected>Coffee</option>'));
    assert.ok(h.includes('Dining (over)') && !h.includes('class="over"') && !h.includes('chips') && !h.includes('data-budget="'));
  }
  assert.ok(budgetPicker(spec(), 'dining').includes('class="over"'), 'the chosen over-limit budget carries the dot');
  assert.ok(isBudgetTrend(spec()) && !isBudgetTrend({ usermeta: { finnamon: { chart: 'budgets', budgets: [] } } }));
});

test('Overall: first in the picker, labelled Overall, its rows picked by the filter, and remembered like any budget', () => {
  const s = spec(); s.usermeta.finnamon.budgets.unshift({ name: ' overall', over: false });
  const h = budgetPicker(s, ' overall');
  assert.ok(h.includes('<select data-budget-select aria-label="Budget to show"><option value=" overall" selected>Overall</option>'), 'the first option, with the select\'s label');
  assert.deepEqual(budgetSpec(s, ' overall').transform, [{ filter: { field: 'budget', equal: ' overall' } }]);
  const st = store();
  saveBudgetChoice(s, ' overall', st);
  assert.equal(budgetChoice(s, st), ' overall', 'Overall is remembered');
  saveBudgetChoice(s, 'coffee', st);
  assert.equal(budgetChoice(s, st), 'coffee', 'a saved budget name still opens that budget');
  saveBudgetChoice(s, ' overall', st);
  assert.equal(budgetChoice(spec(), st), 'dining', 'a board drawn before Overall existed falls back to its default');
});
