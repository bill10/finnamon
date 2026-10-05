import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync, readFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { buildApp, updater, UPDATE_CHECK_MS, UPDATE_LOAD_MS, UPDATE_LOCK, UPDATE_LOG, COOKIE as COOKIE_NAME } from '../server.js';

const KEY = 'k'.repeat(64);
const quiet = { warn() {}, log() {} };
const AVAILABLE = { current: '0.23.0.0', latest: '0.24.0.0', ahead: 2, notes: 'Update from the page.' };

// A scratch home, a fake CLI that counts checks, a fake spawn that records its argv and never runs anything, a settable clock.
function rig({ check = AVAILABLE, alive = () => true } = {}) {
  const home = mkdtempSync(join(tmpdir(), 'finnamon-update-'));
  const checks = [], spawned = [];
  let t = 1_000_000;
  const u = updater({ home, log: quiet, now: () => t, alive: (pid) => alive(pid),
    call: async (...args) => { checks.push(args); if (check instanceof Error) throw check; return check; },
    spawn: (cmd, args, opts) => { spawned.push({ cmd, args, opts }); return { pid: 4242 + spawned.length, unref() {}, on() {} }; } });
  return { u, home, checks, spawned, tick: (ms) => { t += ms; }, done: () => rmSync(home, { recursive: true, force: true }) };
}

test('the check runs `finnamon update --check` once per half hour, not once per page load', async () => {
  const r = rig();
  try {
    assert.deepEqual(await r.u.status(), { ...AVAILABLE, running: false, last: null });
    await r.u.status(); r.tick(UPDATE_CHECK_MS - 1); await r.u.status();
    assert.deepEqual(r.checks, [['update', '--check']]);
    r.tick(2); await r.u.status();
    assert.equal(r.checks.length, 2, 'stale after half an hour');
    await Promise.all([r.u.status(), r.u.status()]);
    assert.equal(r.checks.length, 2);
  } finally { r.done(); }
});

test('a failed check is cached too, and says why', async () => {
  const r = rig({ check: Object.assign(new Error('x'), { stderr: 'error: no upstream branch\n' }) });
  try {
    assert.equal((await r.u.status()).error, 'error: no upstream branch');
    await r.u.status();
    assert.equal(r.checks.length, 1, 'a box with no upstream is not fetched on every load');
  } finally { r.done(); }
});

test('Update spawns `finnamon update` detached, as the person, and a lock refuses a second run', async () => {
  const r = rig();
  try {
    await r.u.status();
    assert.deepEqual(r.u.start(), { running: true, from: '0.23.0.0' });
    const [s] = r.spawned;
    assert.equal(s.cmd, '/bin/sh');
    assert.deepEqual(s.args.slice(-1), ['update'], 'the whole of `finnamon update`, no flags');
    assert.equal(s.opts.detached, true, 'it restarts this server, so it must not die with it');
    assert.equal(s.opts.env.CLAUDECODE, undefined); assert.equal(s.opts.env.FINNAMON_FROM_CLAUDE, undefined); assert.equal(s.opts.env.FINNAMON_FROM_AGENT, undefined);
    assert.deepEqual(JSON.parse(readFileSync(join(r.home, UPDATE_LOCK), 'utf8')).pid, 4243);
    assert.throws(() => r.u.start(), /already running/);
    assert.equal(r.spawned.length, 1);
    assert.equal((await r.u.status()).running, true);
  } finally { r.done(); }
});

test('the lock outlives the server (a restarted one reads it) and a stale one does not hold', async () => {
  const r = rig();
  try {
    r.u.start();
    const again = updater({ home: r.home, log: quiet, now: () => 1_000_001, alive: () => true, call: async () => AVAILABLE, spawn: () => assert.fail('locked') });
    assert.throws(() => again.start(), /already running/);
    const later = updater({ home: r.home, log: quiet, now: () => 1_000_000 + 3600e3, alive: () => true, call: async () => AVAILABLE, spawn: () => ({ pid: 9 }) });
    assert.deepEqual(later.start().running, true, 'a pid an hour old is a reused one');
  } finally { r.done(); }
});

test('a finished run reports its exit and last lines, and forces a fresh check', async () => {
  let up = true;
  const r = rig({ alive: () => up });
  try {
    await r.u.status(); r.u.start(); up = false;
    writeFileSync(join(r.home, UPDATE_LOG), 'Updating a..b\nerror: a sync is running right now\nfinnamon update exited 1\n');
    const s = await r.u.status();
    assert.equal(s.running, false);
    assert.deepEqual({ exit: s.last.exit, from: s.last.from, tail: s.last.tail }, { exit: 1, from: '0.23.0.0', tail: 'Updating a..b\nerror: a sync is running right now' });
    assert.equal(r.checks.length, 2, 'what there is to pull changed: look again');
    await r.u.status();
    assert.equal(r.checks.length, 2);
    rmSync(join(r.home, UPDATE_LOG));
    assert.equal((await r.u.status()).last.exit, null, 'killed before it could say: the page judges by the version');
  } finally { r.done(); }
});

async function withApp(update, fn) {
  const app = buildApp({ token: () => KEY, cli: async () => ({}), allowHost: (h) => h.startsWith('127.0.0.1:'), allowOrigin: (o) => /^http:\/\/127\.0\.0\.1:\d+$/.test(o), update });
  const srv = app.listen(0, '127.0.0.1');
  await new Promise(r => srv.once('listening', r));
  const base = `http://127.0.0.1:${srv.address().port}`;
  try { await fn(base); } finally { srv.close(); }
}

test('POST /api/update: the page only (cookie and Origin); never a bearer key, a GET, or a stranger', async () => {
  const r = rig();
  try {
    await withApp(r.u, async (base) => {
      const cookie = { cookie: `${COOKIE_NAME}=${KEY}` };
      assert.equal((await fetch(base + '/api/update', { headers: cookie })).status, 200);
      assert.equal(r.spawned.length, 0, 'a GET only looks');
      assert.equal((await fetch(base + '/api/update', { method: 'POST', headers: { authorization: `Bearer ${KEY}`, origin: base } })).status, 403, 'the bearer key is for the CLI and curl, never for this');
      assert.equal((await fetch(base + '/api/update', { method: 'POST', headers: cookie })).status, 403, 'no Origin: not the page');
      assert.equal((await fetch(base + '/api/update', { method: 'POST', headers: { ...cookie, origin: 'http://evil.example' } })).status, 403);
      assert.equal((await fetch(base + '/api/update', { method: 'POST', headers: { origin: base } })).status, 401);
      assert.equal(r.spawned.length, 0);
      assert.equal((await fetch(base + '/api/update', { method: 'POST', headers: { ...cookie, origin: base } })).status, 202);
      assert.equal((await fetch(base + '/api/update', { method: 'POST', headers: { ...cookie, origin: base } })).status, 409);
      assert.equal(r.spawned.length, 1);
    });
    await withApp(null, async (base) => assert.equal((await fetch(base + '/api/update', { headers: { cookie: `${COOKIE_NAME}=${KEY}` } })).status, 503));
  } finally { r.done(); }
});

test('the cache is 30 minutes, a page load re-checks one over 10 minutes old, a forced check always looks', async () => {
  assert.equal(UPDATE_CHECK_MS, 30 * 60e3); assert.equal(UPDATE_LOAD_MS, 10 * 60e3);
  const r = rig();
  try {
    await r.u.status();
    r.tick(UPDATE_LOAD_MS - 1); await r.u.status({ load: true });
    assert.equal(r.checks.length, 1, 'under ten minutes: the page load reuses it');
    r.tick(2); await r.u.status();
    assert.equal(r.checks.length, 1, 'the periodic look still trusts it');
    await r.u.status({ load: true });
    assert.equal(r.checks.length, 2, 'ten minutes: a page load looks again');
    await r.u.status({ fresh: true });
    assert.equal(r.checks.length, 3, 'Check for updates ignores the cache');
    await r.u.status({ fresh: true });
    assert.equal(r.checks.length, 4);
  } finally { r.done(); }
});

test('an up-to-date box says ahead 0, and a forced check picks up a release that landed since', async () => {
  let check = { current: '0.24.0.0', latest: '0.24.0.0', ahead: 0, notes: '' };
  const home = mkdtempSync(join(tmpdir(), 'finnamon-update-'));
  const u = updater({ home, log: quiet, call: async () => check, spawn: () => assert.fail('only a check') });
  try {
    assert.equal((await u.status()).ahead, 0);
    check = AVAILABLE;
    assert.equal((await u.status()).ahead, 0, 'cached');
    assert.equal((await u.status({ fresh: true })).ahead, 2);
  } finally { rmSync(home, { recursive: true, force: true }); }
});

test('GET /api/update?fresh=1 forces the check; GET /api/settings shows the version, remote access and voice (read-only)', async () => {
  const r = rig();
  const home = mkdtempSync(join(tmpdir(), 'finnamon-settings-'));
  try {
    const app = buildApp({ token: () => KEY, cli: async () => ({}), home, update: r.u, talk: { setup: () => ({ stt: null, sttMissing: 'x', tts: 'say' }) },
      allowHost: (h) => h.startsWith('127.0.0.1:'), allowOrigin: (o) => /^http:\/\/127\.0\.0\.1:\d+$/.test(o) });
    const srv = app.listen(0, '127.0.0.1'); await new Promise(x => srv.once('listening', x));
    const base = `http://127.0.0.1:${srv.address().port}`, headers = { cookie: `${COOKIE_NAME}=${KEY}` };
    try {
      await fetch(base + '/api/update', { headers }); await fetch(base + '/api/update', { headers });
      assert.equal(r.checks.length, 1);
      await fetch(base + '/api/update?fresh=1', { headers });
      assert.equal(r.checks.length, 2);
      assert.equal((await fetch(base + '/api/settings')).status, 401);
      let s = await (await fetch(base + '/api/settings', { headers })).json();
      assert.deepEqual({ on: s.remote.on, hosts: s.remote.hosts, stt: s.voice.stt }, { on: false, hosts: [], stt: null });
      assert.match(s.version, /^\d+\.\d+\.\d+\.\d+$/);
      writeFileSync(join(home, 'web-hosts'), 'mac.tail1234.ts.net\n');
      s = await (await fetch(base + '/api/settings', { headers })).json();
      assert.deepEqual(s.remote, { on: true, hosts: ['mac.tail1234.ts.net'] });
      assert.equal((await fetch(base + '/api/settings', { method: 'POST', headers: { ...headers, origin: base } })).status, 404, 'nothing here writes');
    } finally { srv.close(); }
  } finally { r.done(); rmSync(home, { recursive: true, force: true }); }
});
