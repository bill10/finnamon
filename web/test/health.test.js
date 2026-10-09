import { test } from 'node:test';
import assert from 'node:assert/strict';
import { health, delta, SIGNED_OUT } from '../server.js';

const now = new Date().toISOString().slice(0, 19).replace('T', ' ');

test('health reads like a person would say it', () => {
  assert.deepEqual(health({ items: [{ institution: 'Chase', status: 'good' }], last_run: now, daemon_alive: true }), { level: 'ok', label: 'All good' });
  assert.deepEqual(health({ items: [{ institution: 'Chase', status: 'ITEM_LOGIN_REQUIRED' }], last_run: now, daemon_alive: true }), { level: 'warn', label: 'Chase needs a re-login' });
  assert.deepEqual(health({ items: [{ institution: 'BoA', status: 'SYNC_ERROR' }], last_run: now, daemon_alive: true }), { level: 'warn', label: 'BoA sync error' });
  assert.deepEqual(health({ items: [{ institution: 'Chase', status: 'good' }], last_run: now, daemon_alive: false }), { level: 'down', label: 'Daemon stopped' });
  assert.deepEqual(health({ items: [{ institution: 'Chase', status: 'good' }], last_run: now, daemon_alive: true, assistant_problems: ['missing x'] }), { level: 'down', label: 'Assistant directory not ready: finnamon install' }, 'no allow list, no answers: the page says why');
  assert.deepEqual(health({ items: [{ institution: 'Chase', status: 'good' }], last_run: now, daemon_alive: true, assistant_problems: [] }), { level: 'ok', label: 'All good' });
  assert.deepEqual(health({ items: [{ institution: 'Chase', status: 'good' }], last_run: '2020-01-01 00:00:00', daemon_alive: true }), { level: 'warn', label: 'Sync overdue' });
  assert.deepEqual(health({ items: [], last_run: null }), { level: 'ok', label: 'All good' });
  assert.deepEqual(health({ items: [], last_run: now, daemon_alive: true, login_problem: SIGNED_OUT.claude }), { level: 'down', label: 'Sign in to Claude Code first: run `claude` in a terminal' },
    'a signed-out claude would show its first-run screens in the intercom: the pill says why it is held back instead of "All good"');   // nothing linked yet is not a fault
  assert.deepEqual(health({ items: [], demo: true, daemon_alive: false }), { level: 'ok', label: 'Demo household: made-up data' }, '`finnamon demo` runs no daemon');
});

test('a Telegram mode flipped after start-up asks for a restart', () => {
  assert.deepEqual(health({ items: [], last_run: now, inbound: 'channel' }, 'daemon'), { level: 'warn', label: 'Telegram mode changed: restart finnamon web' });
  assert.deepEqual(health({ items: [], last_run: now, inbound: 'daemon' }, 'daemon'), { level: 'ok', label: 'All good' });
  assert.deepEqual(health({ items: [], inbound: 'channel' }), { level: 'ok', label: 'All good' });   // no expectation given: no complaint
});

test('delta leaves out an account added this month', () => {
  assert.deepEqual(delta([{ date: '2026-08-31', net_worth: 1000 }, { date: '2026-09-05', net_worth: 6010, new_this_month: 5000 }]), { amount: 10, pct: 1 });
});

test('delta is this month against the end of last month, null in the first month', () => {
  assert.equal(delta([]), null);
  assert.equal(delta([{ date: '2026-09-03', net_worth: 100 }, { date: '2026-09-20', net_worth: 120 }]), null);   // no earlier month yet
  assert.deepEqual(delta([{ date: '2026-08-30', net_worth: 141078 }, { date: '2026-08-31', net_worth: 141078 }, { date: '2026-09-02', net_worth: 141500 }, { date: '2026-09-20', net_worth: 142318 }]),
    { amount: 1240, pct: 0.9 });
  assert.deepEqual(delta([{ date: '2026-08-31', net_worth: 0 }, { date: '2026-09-01', net_worth: 50 }]), { amount: 50, pct: null });
  assert.equal(delta([{ date: '2026-08-31', net_worth: 'x' }, { date: '2026-09-01', net_worth: 1 }]), null);
});

test('the pill says the chat is unread: the one surface a person watches while Telegram is silent', () => {
  const base = { items: [], daemon_alive: true, inbound: 'channel' };
  assert.deepEqual(health({ ...base }), { level: 'ok', label: 'All good' });
  const deaf = health({ ...base, channel_deaf_since: '2026-09-23 18:40:00' });
  assert.equal(deaf.level, 'down');
  assert.match(deaf.label, /Telegram is not being read/);
  // it outranks a sync warning: the assistant being deaf is what the person is actually experiencing
  assert.equal(health({ ...base, channel_deaf_since: '2026-09-23 18:40:00', items: [{ status: 'ITEM_LOGIN_REQUIRED', institution: 'Chase' }] }).level, 'down');
});

test('an unset deaf stamp is the null `finnamon status` prints, not a missing key', () => {
  assert.deepEqual(health({ items: [], daemon_alive: true, inbound: 'channel', channel_deaf_since: null }), { level: 'ok', label: 'All good' });
});

test('a dead daemon is named before a chat nobody is reading: only the daemon clears that stamp', () => {
  const s = { items: [], inbound: 'channel', daemon_alive: false, channel_deaf_since: '2026-09-23 18:40:00' };
  assert.equal(health(s).label, 'Daemon stopped', 'otherwise the pill blames the dashboard forever, and the stamp can never clear');
  // the same move puts it above a sync warning, which is right: down outranks warn
  assert.equal(health({ items: [{ institution: 'BoA', status: 'SYNC_ERROR' }], daemon_alive: false }).label, 'Daemon stopped');
});
