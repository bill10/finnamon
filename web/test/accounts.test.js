// The Accounts list's display helpers live in the browser module public/app.js, which touches the DOM at load, so the
// test lifts the pure block (KIND .. DEBT) out of the source and evaluates it on its own.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const src = readFileSync(new URL('../public/app.js', import.meta.url), 'utf8');
const block = src.slice(src.indexOf('const KIND ='), src.indexOf('const STALE_MS'));
const { kindOf, acctName, bankTotal, loadOpen, saveOpen } = new Function(`${block}; return { kindOf, acctName, bankTotal, loadOpen, saveOpen };`)();

test('kindOf: Plaid subtype reads as a plain label, falling back to the type', () => {
  assert.equal(kindOf({ subtype: 'cash management', type: 'depository' }), 'Cash management');
  assert.equal(kindOf({ subtype: 'IRA', type: 'investment' }), 'IRA');
  assert.equal(kindOf({ type: 'depository' }), 'Bank account');
  assert.equal(kindOf({ subtype: 'other', type: 'depository' }), 'Bank account');
  assert.equal(kindOf({ subtype: 'other', type: 'other' }), 'Other');
  assert.equal(kindOf({ subtype: 'crypto exchange', type: 'investment' }), 'Crypto exchange');
  assert.equal(kindOf({ subtype: 'constructor', type: 'toString' }), 'Constructor');   // never Object's own members
});

test('acctName: drops a trailing mask that repeats the shown mask, and title-cases shouted words', () => {
  assert.equal(acctName({ name: 'IRA portfolio - 5321', mask: '5321' }), 'IRA portfolio');
  assert.equal(acctName({ name: 'Plan 15321', mask: '5321' }), 'Plan 15321');   // the mask must be a whole trailing number
  assert.equal(acctName({ name: 'Card (x1234)', mask: '1234' }), 'Card');
  assert.equal(acctName({ name: '1234', mask: '1234' }), '1234');               // nothing left: keep the name
  assert.equal(acctName({ name: 'MANAGED CMA', mask: null }), 'Managed CMA');
  assert.equal(acctName({ name: 'TOTAL CHECKING ...9876', mask: '9876' }), 'Total Checking');
  assert.equal(acctName({ name: 'My SAVINGS', mask: '' }), 'My SAVINGS');
  assert.equal(acctName({ name: 'USAA CLASSIC CHECKING', mask: null }), 'USAA Classic Checking');   // known acronyms stay
  assert.equal(acctName({ name: 'ÉPARGNE', mask: null }), 'Épargne');
  assert.equal(acctName({ name: 'Card 12', mask: 'A12' }), 'Card 12');          // a lettered mask cuts nothing
  assert.equal(acctName({ name: 'Rewards / 4455', mask: '4455' }), 'Rewards');      // mixed case is the bank's own: untouched
});

const mem = (init) => { let v = init; return { getItem: () => v ?? null, setItem: (_, x) => { v = x; } }; };

test('bankTotal: assets minus debts in whole dollars; no balance counts nothing', () => {
  assert.equal(bankTotal([{ type: 'depository', balance: 1000.4 }, { type: 'credit', balance: 250.2 }]), 750);
  assert.equal(bankTotal([{ type: 'loan', balance: 100 }, { type: 'depository', balance: null }]), -100);
  assert.equal(bankTotal([{ type: 'credit', balance: 0.4 }]), 0);
  assert.equal(bankTotal([]), 0);
});

test('loadOpen/saveOpen: all closed by default and on junk or throwing storage; remembered otherwise', () => {
  assert.equal(loadOpen(mem()).size, 0);
  assert.equal(loadOpen(mem('{nope')).size, 0);
  assert.equal(loadOpen(mem('{"a":1}')).size, 0);
  assert.equal(loadOpen({ getItem() { throw new Error('blocked'); } }).size, 0);
  const st = mem(); saveOpen(new Set(['ins_1', 'Bank B']), st);
  assert.deepEqual([...loadOpen(st)], ['ins_1', 'Bank B']);
  saveOpen(new Set(), { setItem() { throw new Error('full'); } });   // must not throw
});

const { ownerPlan } = new Function(`${block}; return { ownerPlan };`)();
const two = [{ owner: 'sam', display_name: 'Sam' }, { owner: 'alex', display_name: 'Alex' }];

test('ownerPlan: owner shows only with 2+ members; heading if one owner, rows if mixed', () => {
  const sy = [{ owner: 'sam' }, { owner: 'sam' }];
  assert.equal(ownerPlan(sy, [two[0]]).head, '');                       // a single member: nothing extra
  assert.equal(ownerPlan(sy, [two[0]]).rows, false);
  assert.deepEqual([ownerPlan(sy, two).head, ownerPlan(sy, two).rows], ['Sam', false]);
  const mixed = ownerPlan([{ owner: 'sam' }, { owner: 'alex' }], two);
  assert.deepEqual([mixed.head, mixed.rows, mixed.name({ owner: 'alex' })], ['', true, 'Alex']);
});

test('close button: an aria-labelled X in the Accounts header that closes the dialog', () => {
  assert.match(src, /id="banks-x" type="button" aria-label="Close"/);
  assert.match(src, /\$\('banks-x'\)\.addEventListener\('click', \(\) => \$\('banks-pop'\)\.close\(\)\)/);
  assert.match(readFileSync(new URL('../public/style.css', import.meta.url), 'utf8'), /\.pop \.x \{[^}]*width: 44px; height: 44px/);
});

const { groupAccounts, viewsFor, loadView, saveView } = new Function(`${block}; return { groupAccounts, viewsFor, loadView, saveView };`)();
const fx = [
  { type: 'credit', subtype: 'credit card', owner: 'alex', balance: 10 }, { type: 'loan', subtype: 'mortgage', owner: 'sam', balance: 900 },
  { type: 'depository', subtype: 'savings', owner: 'alex' }, { type: 'depository', subtype: 'checking', owner: 'sam' },
  { type: 'investment', subtype: 'ira', owner: 'alex' }, { type: 'depository', subtype: 'checking', owner: '' }, { type: 'investment', subtype: 'crypto exchange' },
];

test('groupAccounts type: assets first in the plain-kind order, unknown kinds after them, debts last', () => {
  const g = groupAccounts(fx, 'type', two);
  assert.deepEqual(g.map(x => x.name), ['Checking', 'Savings', 'IRA', 'Crypto exchange', 'Credit card', 'Mortgage']);
  assert.equal(g[0].rows.length, 2);
});

test('groupAccounts owner: one group per member in household order, then Joint/unassigned; empty groups left out', () => {
  assert.deepEqual(groupAccounts(fx, 'owner', two).map(x => [x.name, x.rows.length]), [['Sam', 2], ['Alex', 3], ['Joint/unassigned', 2]]);
  assert.deepEqual(groupAccounts(fx.slice(0, 2), 'owner', two).map(x => x.name), ['Sam', 'Alex']);
  assert.deepEqual(groupAccounts([{ owner: 'alex' }], 'owner', two).map(x => x.name), ['Alex']);
});

test('views: a single member hides Owner; the choice is remembered, defaults to Bank, falls back when Owner is gone', () => {
  assert.deepEqual(viewsFor([two[0]]), ['bank', 'type']);
  assert.deepEqual(viewsFor(two), ['bank', 'type', 'owner']);
  const st = mem(); assert.equal(loadView(two, st), 'bank');
  saveView('owner', st); assert.equal(loadView(two, st), 'owner');
  assert.equal(loadView([two[0]], st), 'bank');
  assert.equal(loadView(two, mem('junk')), 'bank');
  assert.equal(loadView(two, { getItem() { throw new Error('blocked'); } }), 'bank');
  saveView('type', { setItem() { throw new Error('full'); } });   // must not throw
});
