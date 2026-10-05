import { test } from 'node:test';
import assert from 'node:assert/strict';
import { request } from 'node:http';
import { createHash } from 'node:crypto';
import { mkdtempSync, rmSync, writeFileSync, readFileSync, statSync, chmodSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { buildApp, loopback, allowSocket, importRunner, importCommand, frame, createSession, KINDS, csp, webToken, same, authOf, peer, guardKey, throttled, redeemPair, COOKIE as COOKIE_NAME, TOKEN_FILE, HOSTS_FILE, PAIR_FILE } from '../server.js';

// Every test here holds the dashboard's key: a fixed one injected into buildApp, sent as the page's cookie on each request.
const KEY = 'k'.repeat(64);
const COOKIE = { cookie: `${COOKIE_NAME}=${KEY}` };   // finnamon_token_8888: the port is in the name
const fetch = (url, init = {}) => globalThis.fetch(url, { ...init, headers: { ...COOKIE, ...init.headers } });
// fetch() will not send a forged Origin; a raw request will
const raw = (port, path, headers, method = 'POST') => new Promise((res, rej) => request({ host: '127.0.0.1', port, path, method, headers: { ...COOKIE, ...headers } }, (r) => { r.resume(); r.on('end', () => res(r.statusCode)); }).on('error', rej).end());
// the whole response: status and headers, for the redirect and the cookie it sets
const bare = (port, path, headers = {}) => new Promise((res, rej) => request({ host: '127.0.0.1', port, path, headers }, (r) => { let body = ''; r.on('data', d => body += d); r.on('end', () => res({ status: r.statusCode, headers: r.headers, body })); }).on('error', rej).end());

// The routes with a recording fake CLI, driven over real HTTP on a random port.
async function withApp(fn) {
  const calls = [];
  const cli = async (...args) => { calls.push(args); return { status: {}, budgets: [], ok: 1 }; };
  const exec = async (...args) => { calls.push(args); return ''; };
  const app = buildApp({ token: () => KEY, cli, exec, allowHost: (h) => h.startsWith('127.0.0.1:') });
  const srv = app.listen(0, '127.0.0.1');
  await new Promise(r => srv.once('listening', r));
  const base = `http://127.0.0.1:${srv.address().port}`;
  const post = (path, body, headers = {}) => fetch(base + path, { method: 'POST', headers: { 'content-type': 'application/json', ...headers }, body: JSON.stringify(body) });
  try { await fn({ base, post, calls }); } finally { srv.close(); }
}

test('bad input is refused before the CLI runs', async () => {
  await withApp(async ({ base, post, calls }) => {
    for (const body of [{ name: 'x', amount: 0 }, { name: '', amount: 5 }, { name: 'x', amount: 'abc' }, { amount: 5 }, { name: 'x'.repeat(81), amount: 5 }])
      assert.equal((await post('/api/budget', body)).status, 400, JSON.stringify(body));
    assert.equal((await post('/api/property', { value: 5 })).status, 400);
    assert.equal((await post('/api/property', { name: 'x'.repeat(81), value: 5 })).status, 400, 'over-long names are refused, not cut');
    assert.equal((await fetch(`${base}/api/property/${encodeURIComponent('x'.repeat(81))}`, { method: 'DELETE' })).status, 400);
    assert.equal((await post('/api/link/finish', { public_token: 'access-sandbox-1; rm -rf /' })).status, 400);
    assert.equal((await post('/api/chart', { name: '../x' })).status, 400);
    for (const id of ['..%2Fx', '-x', 'A', '--clear'])
      assert.equal((await fetch(`${base}/api/chart/${id}`, { method: 'DELETE' })).status, 400, id);
    assert.equal(calls.length, 0);
  });
});

test('names reach the CLI after a -- so they can never be options; a merchant search term is cut', async () => {
  await withApp(async ({ base, post, calls }) => {
    assert.equal((await post('/api/budget', { name: '--help', amount: 300 })).status, 200);
    assert.deepEqual(calls.at(-1), ['budget', 'set', '--', '--help', '300']);
    assert.equal((await post('/api/property', { name: ' Cabin,  Lake Tahoe ', value: '$44,000' })).status, 200);
    assert.deepEqual(calls.at(-1), ['property', 'set', '--', 'Cabin,  Lake Tahoe', '$44,000']);
    assert.equal((await fetch(`${base}/api/property/${encodeURIComponent('--version')}`, { method: 'DELETE' })).status, 200);
    assert.deepEqual(calls.at(-1), ['property', 'remove', '--', '--version']);
    assert.equal((await post('/api/chart', { name: 'merchant_history', arg: 'x'.repeat(200), months: 3 })).status, 200);
    assert.deepEqual(calls.at(-1), ['chart', '--spec', '--months', '3', '--', 'merchant_history', 'x'.repeat(80)]);
    assert.equal((await fetch(`${base}/api/chart/merchant_history-costco`, { method: 'DELETE' })).status, 200);
    assert.deepEqual(calls.at(-1), ['chart', '--remove', 'merchant_history-costco'], 'the id is its own argv slot and cannot start with -');
    const summary = await (await fetch(`${base}/api/summary`)).json();
    assert.deepEqual(calls.filter(c => c[0] === 'alerts'), [['alerts', '--sent', '--open'], ['alerts', '--sent', '--resolved', '--limit', '20']],
      'the panel lists what was sent or is queued to be, open apart from resolved (filtered by the CLI); triage notes stay with the CLI');
    assert.deepEqual(calls.find(c => c[1] === '--history'), ['networth', '--history', '--months', '12'], 'a year of net worth for the trend');
    assert.ok(Array.isArray(summary.history), 'the series reaches the page even when the CLI gave nothing usable');
    assert.equal(summary.delta, null, 'a non-array history yields no delta');
  });
});

test('a budget\'s picks and a transaction\'s category reach the CLI as values, never options', async () => {
  await withApp(async ({ base, post, calls }) => {
    assert.equal((await post('/api/budget', { name: 'eating out', amount: 400, categories: ['Restaurant', ' FOOD_AND_DRINK_COFFEE '], merchants: ['-rf'] })).status, 200);
    assert.deepEqual(calls.at(-1), ['budget', 'set', '--category=Restaurant', '--category=FOOD_AND_DRINK_COFFEE', '--merchant=-rf', '--', 'eating out', '400']);
    for (const bad of [{ categories: 'x' }, { categories: [''] }, { merchants: [3] }, { merchants: ['x'.repeat(81)] }, { categories: Array(41).fill('x') }])
      assert.equal((await post('/api/budget', { name: 'x', amount: 5, ...bad })).status, 400, JSON.stringify(bad));
    assert.equal((await post('/api/tx/category', { transaction_id: 'import:abc', category: 'Groceries' })).status, 200);
    assert.deepEqual(calls.at(-1), ['category', '--tx=import:abc', '--', 'Groceries']);
    assert.equal((await post('/api/tx/category', { transaction_id: 'tx1', category: 'transfer', every: true })).status, 200);
    assert.deepEqual(calls.at(-1), ['category', '--tx=tx1', '--every', '--', 'transfer']);
    for (const bad of [{ transaction_id: 'a b', category: 'x' }, { transaction_id: 'tx1' }, { transaction_id: 'tx1', category: 'x'.repeat(81) }])
      assert.equal((await post('/api/tx/category', bad)).status, 400, JSON.stringify(bad));
    await fetch(`${base}/api/categories`);
    assert.deepEqual(calls.at(-1), ['category', 'list', '--json']);
    await fetch(`${base}/api/summary`);
    assert.ok(calls.some(c => c[0] === 'budget' && c[1] === 'overall'), 'the Overall line counts a shared charge once');
  });
});

// Value: protects=the dashboard summary still loads when `budget overall` fails (an older CLI), with overall null so the page
// falls back to summing budgets; fails_when=the .catch on the overall call is removed; why_new=the picks test only checks the call is made; seam=none
test('a CLI without budget overall still serves the summary, with overall null', async () => {
  const cli = async (...args) => { if (args[0] === 'budget' && args[1] === 'overall') throw new Error('invalid choice: overall'); return args[0] === 'budget' ? [] : {}; };
  const app = buildApp({ token: () => KEY, cli, exec: async () => '', allowHost: (h) => h.startsWith('127.0.0.1:') });
  const srv = app.listen(0, '127.0.0.1');
  await new Promise(r => srv.once('listening', r));
  try {
    const r = await fetch(`http://127.0.0.1:${srv.address().port}/api/summary`);
    assert.equal(r.status, 200);
    assert.equal((await r.json()).overall, null);
  } finally { srv.close(); }
});

test('the board reaches the page as a list, whatever the file holds', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'finnamon-board-')), spec = join(dir, 'current.json');
  const app = buildApp({ token: () => KEY, cli: async () => ({}), allowHost: () => true, spec });
  const srv = app.listen(0, '127.0.0.1'); await new Promise(r => srv.once('listening', r));
  const get = async () => (await fetch(`http://127.0.0.1:${srv.address().port}/api/chart`)).json();
  try {
    assert.deepEqual(await get(), [], 'no file yet');
    writeFileSync(spec, JSON.stringify({ mark: 'bar' }));
    assert.deepEqual(await get(), [{ mark: 'bar' }], 'a pre-list file is a board of one');
    writeFileSync(spec, JSON.stringify([{ mark: 'bar' }, { mark: 'line' }]));
    assert.equal((await get()).length, 2);
    writeFileSync(spec, '[{"mark"');
    assert.deepEqual(await get(), [], 'a torn file is an empty board, not a 500');
  } finally { srv.close(); rmSync(dir, { recursive: true }); }
});

test('the board is recomputed on load: the page gets `chart --refresh`, the file only when the CLI fails (#76)', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'finnamon-board-')), spec = join(dir, 'current.json');
  writeFileSync(spec, JSON.stringify([{ mark: 'bar', data: { values: [{ a: 1 }] } }]));
  let fresh = [{ mark: 'bar', data: { values: [{ a: 2 }] }, usermeta: { finnamon: { id: 'x', as_of: '2026-10-01 12:00:00' } } }];
  const calls = [];
  const app = buildApp({ token: () => KEY, cli: async (...a) => { calls.push(a); if (!fresh) throw new Error('no db'); return fresh; }, allowHost: () => true, spec });
  const srv = app.listen(0, '127.0.0.1'); await new Promise(r => srv.once('listening', r));
  const get = async () => (await fetch(`http://127.0.0.1:${srv.address().port}/api/chart`)).json();
  try {
    assert.deepEqual(await get(), fresh); assert.deepEqual(calls.at(-1), ['chart', '--refresh']);
    fresh = null;
    assert.deepEqual((await get())[0].data.values, [{ a: 1 }], 'a failed refresh shows the file as it stands');
  } finally { srv.close(); rmSync(dir, { recursive: true }); }
});

test('a CLI failure comes back as its stderr, not a crash', async () => {
  const cli = async () => { const e = new Error('spawn'); e.stderr = 'a property value is zero or more dollars\n'; throw e; };
  const app = buildApp({ token: () => KEY, cli, allowHost: () => true });
  const srv = app.listen(0, '127.0.0.1'); await new Promise(r => srv.once('listening', r));
  const r = await fetch(`http://127.0.0.1:${srv.address().port}/api/property`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ name: 'House', value: -1 }) });
  assert.equal(r.status, 400); assert.deepEqual(await r.json(), { error: 'a property value is zero or more dollars' });
  srv.close();
});

test('only loopback Host headers are served (DNS rebinding), and only the page origin may open the socket', async () => {
  const lo = loopback(8888);
  assert.ok(lo.host('127.0.0.1:8888') && lo.host('localhost:8888') && lo.host('[::1]:8888'));
  assert.ok(!lo.host('attacker.example:8888') && !lo.host('127.0.0.1:8889') && !lo.host(''));
  assert.ok(lo.origin('http://localhost:8888') && lo.origin('http://127.0.0.1:8888'));
  assert.ok(!lo.origin('http://attacker.example') && !lo.origin('http://attacker.example:8888') && !lo.origin('http://localhost:8889') && !lo.origin('null'));
  const app = buildApp({ token: () => KEY, cli: async () => ({}), allowHost: lo.host, allowOrigin: lo.origin });
  const srv = app.listen(0, '127.0.0.1'); await new Promise(r => srv.once('listening', r));
  const r = await fetch(`http://127.0.0.1:${srv.address().port}/api/summary`, { headers: { host: 'attacker.example:8888' } });
  assert.equal(r.status, 421);
  assert.equal(await raw(srv.address().port, '/api/link/token', { host: 'localhost:8888', origin: 'http://attacker.example' }), 403, 'a cross-site bodiless POST carries its Origin and is refused');
  assert.equal(await raw(srv.address().port, '/api/link/token', { host: 'localhost:8888', origin: 'http://localhost:8888' }), 200);
  assert.equal(await raw(srv.address().port, '/api/link/token', { host: 'localhost' }), 421, 'a Host without the port is not ours unless the port is a default one');
  srv.close();
  const sock = allowSocket(lo, () => KEY);   // with the key held; without it nothing gets in (the socket test below)
  assert.ok(sock({ origin: undefined, req: { headers: { host: 'localhost:8888', ...COOKIE } } }), 'no Origin: a client on this machine');
  assert.ok(sock({ origin: 'http://localhost:8888', req: { headers: { host: 'localhost:8888', ...COOKIE } } }));
  assert.ok(!sock({ origin: 'http://evil.example', req: { headers: { host: 'localhost:8888', ...COOKIE } } }));
  assert.ok(!sock({ origin: 'http://localhost:8888', req: { headers: { host: 'evil.example:8888', ...COOKIE } } }));
  assert.ok(!sock({ origin: 'null', req: { headers: { host: 'localhost:8888', ...COOKIE } } }));
  const eighty = loopback(80);
  assert.ok(eighty.host('localhost') && eighty.host('localhost:80') && eighty.origin('http://localhost'));
  assert.ok(!lo.host('localhost:8888:443') && !lo.origin('http://localhost:8888:80'), 'only a lone default port is stripped');
  const ts = loopback(8888, 'mac.tailnet.ts.net, other.example:8443');
  assert.ok(ts.host('mac.tailnet.ts.net') && ts.origin('https://mac.tailnet.ts.net') && ts.host('other.example:8443') && ts.origin('https://other.example:8443'));
  assert.ok(!ts.host('mac.tailnet.ts.net.evil') && !ts.origin('https://evil.example'));
  let file = '';   // `finnamon remote` writes ~/.finnamon/web-hosts; it counts from the next request, no restart
  const fromFile = loopback(8888, '', () => file);
  assert.ok(!fromFile.host('mini.tail1.ts.net'));
  file = 'mini.tail1.ts.net\n';
  assert.ok(fromFile.host('mini.tail1.ts.net') && fromFile.origin('https://mini.tail1.ts.net') && !fromFile.origin('https://evil.example') && fromFile.host('localhost:8888'));
});

test('every response carries a strict CSP: no inline or eval script, the socket on the page host only, Plaid the one third party', async () => {
  await withApp(async ({ base }) => {
    for (const path of ['/', '/api/summary']) {   // fetch() sends the real Host: the socket directive names the address the page was opened on
      const r = await fetch(base + path);
      assert.equal(r.headers.get('content-security-policy'), csp(new URL(base).host));
    }
    const refused = await new Promise((res, rej) => request({ host: '127.0.0.1', port: new URL(base).port, path: '/api/summary', headers: { host: 'attacker.example:8888' } }, (r) => { r.resume(); r.on('end', () => res(r)); }).on('error', rej).end());   // fetch() will not forge a Host
    assert.ok(refused.statusCode === 421 && refused.headers['content-security-policy'].startsWith("default-src 'self'"), 'a refusal carries it too');
  });
  const p = csp('mac.tailnet.ts.net').split('; ').map(d => d.split(' '));
  const dir = Object.fromEntries(p.map(([k, ...v]) => [k, v]));
  const html = readFileSync(join(import.meta.dirname, '../public/index.html'), 'utf8');
  const cdn = [...html.matchAll(/<script[^>]*src="(https:\/\/cdn\.jsdelivr\.net\/[^"]+)"/g)].map(m => m[1]);
  assert.equal(cdn.length, 7);
  assert.deepEqual(dir['script-src'], ["'self'", "'wasm-unsafe-eval'", ...cdn, 'https://cdn.plaid.com/link/'], 'the exact CDN files the page names, never a registry');
  assert.deepEqual(dir['style-src'], ["'self'", "'unsafe-inline'", 'https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/css/xterm.min.css', 'https://fonts.googleapis.com']);
  assert.ok(!dir['script-src'].includes("'unsafe-eval'") && !dir['script-src'].includes("'unsafe-inline'"), 'no eval, no inline script: a chart spec is bank text (WebAssembly compiles, for Talk\'s voice detector)');
  assert.deepEqual(dir['media-src'], ["'self'", 'blob:'], 'Talk plays its replies from blob URLs');
  assert.deepEqual(dir['connect-src'], ["'self'", 'ws://mac.tailnet.ts.net', 'wss://mac.tailnet.ts.net', 'https://*.plaid.com'], 'the intercom socket on the page host, Plaid Link');
  for (const h of ['[::1]:8888', "x; script-src 'unsafe-eval'", '']) assert.ok(csp(h).includes("connect-src 'self' https://*.plaid.com"), `${h}: only a plain host:port is spliced in; self covers the socket`);
  assert.deepEqual(dir['frame-src'], ['https://*.plaid.com']);
  for (const k of ['object-src', 'base-uri', 'frame-ancestors', 'form-action']) assert.deepEqual(dir[k], ["'none'"], k);
  assert.deepEqual(dir['default-src'], ["'self'"]);
  assert.ok(!/<script(?![^>]*\bsrc=)/.test(html) && !/\son[a-z]+=/.test(html), 'no inline script or handler in the page');
  for (const [tag, src] of html.matchAll(/<script[^>]*src="(https:[^"]+)"[^>]*>/g)) {
    assert.ok(/integrity="sha384-/.test(tag) || src === 'https://cdn.plaid.com/link/v2/stable/link-initialize.js', `${src} is pinned by SRI`);   // Plaid's loader is unversioned, updated in place: SRI cannot apply
  }
  assert.ok(html.includes('vega-interpreter@') && readFileSync(join(import.meta.dirname, '../public/app.js'), 'utf8').includes('expr: vega.expressionInterpreter'), 'charts run on the interpreter, never new Function');
});

test('the net worth chart dates its axis and answers a hover with the date and every curve shown (#67)', () => {
  const html = readFileSync(join(import.meta.dirname, '../public/index.html'), 'utf8'), app = readFileSync(join(import.meta.dirname, '../public/app.js'), 'utf8');
  assert.ok(html.includes('id="nw-tip"'), 'the tooltip sits beside the chart');
  assert.ok(app.includes("{ month: 'short', year: 'numeric' }"), 'the first axis label and each January carry the year');
  for (const ev of ['pointermove', 'pointerdown', 'pointerleave']) assert.ok(app.includes(`$('nw-svg').addEventListener('${ev}'`), ev);
  assert.ok(/day: 'numeric', year: 'numeric'/.test(app) && /keys\.length > 1 \? .*name/.test(app), 'the date, and series names only when more than one curve shows');
});

test('the last link token is kept for an OAuth return that lands in a new tab', async () => {
  const cli = async (...a) => a[1] === '--token' ? { link_token: 'link-sandbox-abc' } : {};
  const app = buildApp({ token: () => KEY, cli, allowHost: () => true });
  const srv = app.listen(0, '127.0.0.1'); await new Promise(r => srv.once('listening', r));
  const base = `http://127.0.0.1:${srv.address().port}`;
  assert.deepEqual(await (await fetch(base + '/api/link/token')).json(), { link_token: null, owner: null });
  await fetch(base + '/api/link/token', { method: 'POST' });
  assert.deepEqual(await (await fetch(base + '/api/link/token')).json(), { link_token: 'link-sandbox-abc', owner: null });
  srv.close();
});

test('the OAuth return is the page address only when it is served over HTTPS; plain http sends no redirect', async () => {
  const calls = [];
  const cli = async (...a) => { calls.push(a); return { link_token: 'lt' }; };
  const app = buildApp({ token: () => KEY, cli, allowHost: () => true });
  const srv = app.listen(0, '127.0.0.1'); await new Promise(r => srv.once('listening', r));
  const port = srv.address().port;   // raw: fetch() replaces a Host header, and the recorded redirect is built from it
  try {
    assert.equal(await raw(port, '/api/link/token', { host: 'localhost:8888' }), 200);
    assert.deepEqual(calls.at(-1), ['link', '--token'], 'loopback http: Plaid refuses a non-HTTPS redirect, so OAuth banks open a popup');
    assert.equal(await raw(port, '/api/link/token', { host: 'localhost:8888', 'x-forwarded-proto': 'http' }), 200);
    assert.deepEqual(calls.at(-1), ['link', '--token'], 'a proxy that says http is still http');
    assert.equal(await raw(port, '/api/link/token', { host: 'mac.tailnet.ts.net', 'x-forwarded-proto': 'https' }), 200);
    assert.deepEqual(calls.at(-1), ['link', '--token', 'https://mac.tailnet.ts.net/'], 'tailscale serve terminates TLS: the page address is the registered return');
    assert.equal(await raw(port, '/api/link/token', { host: 'mac.tailnet.ts.net:443', 'x-forwarded-proto': 'https, https' }), 200);
    assert.deepEqual(calls.at(-1), ['link', '--token', 'https://mac.tailnet.ts.net/'], 'a proxy chain and an explicit default port still produce the registered URI');
  } finally { srv.close(); }
});

test('a history row reaches the page as the CLI shaped it', async () => {
  const row = { date: '2026-09-01', net_worth: 1, assets: 2, liabilities: 1 };
  const cli = async (...args) => args[1] === '--history' ? [row] : { status: {}, budgets: [] };
  const app = buildApp({ token: () => KEY, cli, exec: async () => '', allowHost: () => true });
  const srv = app.listen(0, '127.0.0.1'); await new Promise(r => srv.once('listening', r));
  try {
    const s = await (await fetch(`http://127.0.0.1:${srv.address().port}/api/summary`)).json();
    assert.deepEqual(s.history, [row]); assert.equal(s.delta, null, 'one point is no change');
  } finally { srv.close(); }
});

test('a failing history query costs the trend, not the summary', async () => {
  const cli = async (...args) => { if (args[1] === '--history') throw new Error('no snapshots table'); return { items: [] }; };
  const app = buildApp({ token: () => KEY, cli, allowHost: () => true });
  const srv = app.listen(0, '127.0.0.1'); await new Promise(r => srv.once('listening', r));
  try {
    const r = await fetch(`http://127.0.0.1:${srv.address().port}/api/summary`);
    assert.equal(r.status, 200); const s = await r.json();
    assert.deepEqual(s.history, []); assert.equal(s.delta, null);
  } finally { srv.close(); }
});

test('a CSV import lands under the home as a file the CLI is pointed at; the account name goes after --', async () => {
  const { mkdtempSync, readFileSync: read, readdirSync } = await import('node:fs');
  const { tmpdir } = await import('node:os');
  const { join: j } = await import('node:path');
  const home = mkdtempSync(j(tmpdir(), 'finnamon-web-'));
  const calls = [];
  const cli = async (...args) => { calls.push(args); return { added: 3, already: 1 }; };
  const app = buildApp({ token: () => KEY, cli, allowHost: () => true, home });
  const srv = app.listen(0, '127.0.0.1'); await new Promise(r => srv.once('listening', r));
  const base = `http://127.0.0.1:${srv.address().port}`;
  const csv = 'Date,Description,Amount\n09/15/2026,COSTCO,-142.17\n';
  try {
    let r = await fetch(`${base}/api/import?account=${encodeURIComponent('--help')}&balance=1200`, { method: 'POST', headers: { 'content-type': 'text/csv' }, body: csv });
    assert.equal(r.status, 200); assert.deepEqual(await r.json(), { ok: true, added: 3, already: 1 });
    const files = readdirSync(j(home, 'imports'));
    assert.equal(files.length, 1); assert.equal(read(j(home, 'imports', files[0]), 'utf8'), csv, 'the file is kept as sent');
    assert.deepEqual(calls.at(-1), ['import', '--balance', '1200', '--', '--help', j(home, 'imports', files[0])]);
    r = await fetch(`${base}/api/import?account=HSBC&flip=0&dry_run=false`, { method: 'POST', headers: { 'content-type': 'text/csv' }, body: csv });
    assert.equal(r.status, 200); assert.equal(calls.at(-1).includes('--flip'), false, 'flip=0 is not a flip'); assert.equal(calls.at(-1).includes('--dry-run'), false);
    await new Promise(res => setTimeout(res, 20));
    r = await fetch(`${base}/api/import?account=HSBC&balance=1.2k`, { method: 'POST', headers: { 'content-type': 'text/csv' }, body: csv });
    assert.equal(r.status, 400, 'a balance that is not a number is refused, not dropped');
    r = await fetch(`${base}/api/import?account=HSBC&balance=%241%2C200&flip=yes&dry_run=true`, { method: 'POST', headers: { 'content-type': 'text/csv' }, body: csv });
    assert.equal(r.status, 200); assert.deepEqual(calls.at(-1).slice(0, 5), ['import', '--flip', '--dry-run', '--balance', '1200'], 'a dollar sign and a thousands comma are stripped; yes and true are flags');
    await new Promise(res => setTimeout(res, 20));
    r = await fetch(`${base}/api/import?account=HSBC`, { method: 'POST', headers: { 'content-type': 'text/csv' }, body: 'x'.repeat(9 * 1024 * 1024) });
    assert.equal(r.status, 413); assert.match((await r.json()).error, /over 8mb/, 'the body limit answers in JSON like every other error');
    r = await fetch(`${base}/api/import`, { method: 'POST', headers: { 'content-type': 'text/csv' }, body: csv });
    assert.equal(r.status, 400, 'no account, no import');
    r = await fetch(`${base}/api/import?account=HSBC`, { method: 'POST', headers: { 'content-type': 'text/csv' }, body: '  \n' });
    assert.equal(r.status, 400, 'an empty body is refused before anything is written');
    assert.equal(calls.length, 3, 'three imports reached the CLI; the bad balance, the empty body and the oversized file never did');
  } finally { srv.close(); }
});

test('a browser import starts a session for a plausible bank name only, and a running one is reported, not replaced', async () => {
  const started = [];
  let busy = false;
  const startImport = (bank) => { if (busy) throw new Error('an import session is still running'); started.push(bank); if (bank === 'HSBC') busy = true; };
  const app = buildApp({ token: () => KEY, cli: async () => ({}), allowHost: () => true, startImport });
  const srv = app.listen(0, '127.0.0.1'); await new Promise(r => srv.once('listening', r));
  const base = `http://127.0.0.1:${srv.address().port}`;
  const post = (body) => fetch(`${base}/api/import/browser`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
  try {
    for (const bank of ['', 'x'.repeat(81), '$(rm -rf /)', '--allowedTools', 'a\nb']) assert.equal((await post({ bank })).status, 400, JSON.stringify(bank));
    assert.equal((await post({ bank: 'Société Générale' })).status, 200, 'an institution finnamon account add accepted is a bank here too');
    assert.equal((await post({ bank: 'HSBC' })).status, 200); assert.deepEqual(started, ['Société Générale', 'HSBC']);
    const r = await post({ bank: 'Ally Bank' }); assert.equal(r.status, 409); assert.match((await r.json()).error, /still running/);
    assert.deepEqual(started, ['Société Générale', 'HSBC']);
    const none = buildApp({ token: () => KEY, cli: async () => ({}), allowHost: () => true }).listen(0, '127.0.0.1'); await new Promise(r => none.once('listening', r));
    assert.equal((await fetch(`http://127.0.0.1:${none.address().port}/api/import/browser`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ bank: 'HSBC' }) })).status, 503);
    none.close();
  } finally { srv.close(); }
});

test('importCommand: the CLI launcher owns the claude argv; a bank name never becomes an option of its own', () => {
  const { cmd, args } = importCommand('HSBC');
  assert.ok(cmd); assert.deepEqual(args.slice(-4), ['import', '--browser', '--', 'HSBC']);
  assert.deepEqual(importCommand('--help').args.slice(-2), ['--', '--help']);
});

test('an import passes --flip through, ignores a blank or unparsable balance, and reports a CLI or a disk failure', async () => {
  const { mkdtempSync, writeFileSync: w } = await import('node:fs');
  const { tmpdir } = await import('node:os');
  const { join: j } = await import('node:path');
  const home = mkdtempSync(j(tmpdir(), 'finnamon-web-'));
  const calls = [];
  let fail = null;
  const cli = async (...args) => { calls.push(args); if (fail) throw fail; return { added: 1 }; };
  const app = buildApp({ token: () => KEY, cli, allowHost: () => true, home });
  const srv = app.listen(0, '127.0.0.1'); await new Promise(r => srv.once('listening', r));
  const base = `http://127.0.0.1:${srv.address().port}`;
  const csv = 'Date,Description,Amount\n09/15/2026,COSTCO,142.17\n';
  const post = (q) => fetch(`${base}/api/import?${q}`, { method: 'POST', headers: { 'content-type': 'text/csv' }, body: csv });
  try {
    assert.equal((await post('account=HSBC&flip=1&balance=')).status, 200);
    assert.deepEqual(calls.at(-1).slice(0, 3), ['import', '--flip', '--']);
    assert.equal((await post('account=HSBC&balance=abc')).status, 400, 'a balance that is not a number is refused, like a bad budget amount');
    assert.ok(!calls.at(-1).includes('--balance'), 'and nothing reached the CLI for it');
    fail = Object.assign(new Error('spawn'), { stderr: "error: no account 'HSBC'; `finnamon account list` shows them\n" });
    const r = await post('account=HSBC'); assert.equal(r.status, 400); assert.match((await r.json()).error, /no account 'HSBC'/);
    fail = null;
    const blocked = j(home, 'not-a-dir'); w(blocked, '');   // the imports directory cannot be created under a file
    const bad = buildApp({ token: () => KEY, cli, allowHost: () => true, home: blocked }).listen(0, '127.0.0.1'); await new Promise(r => bad.once('listening', r));
    const n = calls.length;
    const r2 = await fetch(`http://127.0.0.1:${bad.address().port}/api/import?account=HSBC`, { method: 'POST', headers: { 'content-type': 'text/csv' }, body: csv });
    assert.equal(r2.status, 500); assert.equal(calls.length, n, 'nothing was handed to the CLI without a file on disk');
    bad.close();
  } finally { srv.close(); }
});

test('the import runner refuses a second session while one is not DOWN, then replaces and stops the finished one', () => {
  const made = [];
  const make = (opts) => { const s = { session: { state: 'WORKING' }, opts, stopped: false, stop() { this.stopped = true; }, replay: () => '' }; made.push(s); return s; };
  const sent = [];
  const r = importRunner({ make, broadcast: (m) => sent.push(m) });
  r.start('HSBC');
  assert.deepEqual(made[0].opts.args.slice(-4), ['import', '--browser', '--', 'HSBC']); assert.equal(made[0].opts.once, true);
  for (const st of ['STARTING', 'WORKING', 'WAITING', 'QUESTION']) { made[0].session.state = st; assert.throws(() => r.start('Ally'), /still running/, st); }
  made[0].session.state = 'DOWN';
  r.start('Ally');
  assert.equal(made.length, 2); assert.ok(made[0].stopped, 'the finished session is released'); assert.equal(r.current, made[1]);
  made[1].opts.onOutput('hi'); made[1].opts.onState('WAITING');
  assert.deepEqual(sent, [{ type: 'output', session: 'import', data: 'hi' }, { type: 'state', session: 'import', state: 'WAITING' }], 'its traffic is tagged for the Import tab');
  made[0].opts.onState('DOWN');   // the replaced session's exit arrives late
  assert.equal(r.current, made[1], 'a stale DOWN does not drop the running session');
  made[1].session.state = 'DOWN'; made[1].opts.onState('DOWN');
  assert.equal(r.current, null, 'a finished session, with the bank pages in its transcript, is dropped rather than replayed to the next page');
  assert.ok(made[1].stopped);
});

test('uploaded CSVs are kept only when the CLI took them, dry runs are not kept, and the directory is capped', async () => {
  const { mkdtempSync, readdirSync: ls, writeFileSync: w } = await import('node:fs');
  const { tmpdir } = await import('node:os');
  const { join: j } = await import('node:path');
  const { pruneImports } = await import('../server.js');
  const home = mkdtempSync(j(tmpdir(), 'finnamon-web-'));
  let fail = null;
  const cli = async () => { if (fail) throw fail; return { added: 1 }; };
  const app = buildApp({ token: () => KEY, cli, allowHost: () => true, home });
  const srv = app.listen(0, '127.0.0.1'); await new Promise(r => srv.once('listening', r));
  const base = `http://127.0.0.1:${srv.address().port}`;
  const post = (q) => fetch(`${base}/api/import?${q}`, { method: 'POST', headers: { 'content-type': 'text/csv' }, body: 'Date,Description,Amount\n09/15/2026,COSTCO,142.17\n' });
  try {
    assert.equal((await post('account=HSBC&dry_run=1')).status, 200);
    await new Promise(r => setTimeout(r, 20));
    assert.deepEqual(ls(j(home, 'imports')), [], 'a dry run leaves no file behind');
    fail = Object.assign(new Error('spawn'), { stderr: 'error: no account\n' });
    assert.equal((await post('account=Nope')).status, 400);
    assert.deepEqual(ls(j(home, 'imports')), [], 'a refused file is not a record of anything');
    fail = null;
    assert.equal((await post('account=HSBC')).status, 200);
    await new Promise(r => setTimeout(r, 20));
    assert.equal(ls(j(home, 'imports')).length, 1, 'what landed is kept');
    for (let i = 0; i < 25; i++) w(j(home, 'imports', `2020-01-01T00-00-${String(i).padStart(2, '0')}-000Z-aaaaaa.csv`), 'x');
    pruneImports(j(home, 'imports'), 5);
    const left = ls(j(home, 'imports'));
    assert.deepEqual(left.slice(0, 4), [21, 22, 23, 24].map(i => `2020-01-01T00-00-${i}-000Z-aaaaaa.csv`), 'the oldest go first');
    assert.equal(left.length, 5); assert.ok(!left[4].startsWith('2020'), 'the real upload, newest, is kept');
  } finally { srv.close(); }
});

test('adding a manual account passes the bank and type through, and refuses a bad type', async () => {
  const calls = [];
  const cli = async (...a) => { calls.push(a); return { account_id: 'manual:hsbc:hsbc-savings' }; };
  const app = buildApp({ token: () => KEY, cli, allowHost: () => true });
  const srv = app.listen(0, '127.0.0.1'); await new Promise(r => srv.once('listening', r));
  const base = `http://127.0.0.1:${srv.address().port}`;
  const post = (body) => fetch(`${base}/api/account`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
  try {
    let r = await post({ name: 'HSBC Savings', institution: 'HSBC', type: 'savings' });
    assert.equal(r.status, 200);
    assert.deepEqual(calls.at(-1), ['account', 'add', '--institution', 'HSBC', '--type', 'savings', '--', 'HSBC Savings'], 'the name goes after --, so "--help" is a name');
    r = await post({ name: 'HSBC Checking', institution: 'HSBC' });
    assert.deepEqual(calls.at(-1).slice(4, 6), ['--type', 'checking'], 'no type means a checking account');
    r = await post({ name: 'x', institution: 'HSBC', type: 'crypto' });
    assert.equal(r.status, 400); assert.equal(calls.length, 2, 'a type the CLI would refuse never reaches it');
    r = await post({ name: '', institution: 'HSBC' });
    assert.equal(r.status, 400);
  } finally { srv.close(); }
});

test('an account with no bank or a name past the limit never reaches the CLI, and a duplicate comes back as the CLI said it', async () => {
  const calls = [];
  const cli = async (...a) => { calls.push(a); const e = new Error('spawn'); e.stderr = 'an account named HSBC Checking already exists\n'; throw e; };
  const app = buildApp({ token: () => KEY, cli, allowHost: () => true });
  const srv = app.listen(0, '127.0.0.1'); await new Promise(r => srv.once('listening', r));
  const post = (body) => fetch(`http://127.0.0.1:${srv.address().port}/api/account`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
  try {
    assert.equal((await post({ name: 'HSBC Checking' })).status, 400, 'the panel falls back to the first word of the name; a direct POST gets no such favour');
    assert.equal((await post({ name: 'x'.repeat(81), institution: 'HSBC' })).status, 400, 'over-long names are refused, not cut');
    assert.equal((await post({ name: 'HSBC Checking', institution: 'x'.repeat(81) })).status, 400);
    assert.equal((await post({ name: 'HSBC Checking', institution: 'HSBC', type: '' })).status, 400, 'a blank type is not the default, it is a typo');
    assert.equal(calls.length, 0, 'nothing the CLI would refuse ever started a process');
    const r = await post({ name: 'HSBC Checking', institution: 'HSBC' });
    assert.equal(r.status, 400);
    assert.deepEqual(await r.json(), { error: 'an account named HSBC Checking already exists' }, 'the panel shows the CLI\'s own words in its error line');
  } finally { srv.close(); }
});

test('the dashboard offers exactly the account types the CLI accepts, and hands back the name it stored', async () => {
  const { readFileSync } = await import('node:fs');
  const py = readFileSync(new URL('../../finnamon/imports.py', import.meta.url), 'utf8');   // the list lives in Python; this is the only thing holding the copy to it
  const block = py.slice(py.indexOf('KINDS = {'));
  const keys = [...block.slice(0, block.indexOf('}')).matchAll(/"([a-z]+)":/g)].map(m => m[1]);
  assert.deepEqual(keys, KINDS, 'finnamon.imports.KINDS changed: update web/server.js KINDS and the type <select> in web/public/app.js');

  const cli = async () => ({ account_id: 'manual:hsbc:hsbc-checking', name: 'HSBC Checking' });   // add_account collapses inner whitespace
  const app = buildApp({ token: () => KEY, cli, allowHost: () => true });
  const srv = app.listen(0, '127.0.0.1'); await new Promise(r => srv.once('listening', r));
  try {
    const r = await fetch(`http://127.0.0.1:${srv.address().port}/api/account`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ name: 'HSBC  Checking', institution: 'HSBC' }) });
    assert.equal((await r.json()).name, 'HSBC Checking', 'the panel selects the new account by this, not by what was typed');
  } finally { srv.close(); }
});

test('an account is refused a bank name that Fetch by AI would later reject, since nothing can rename it', async () => {
  const calls = [];
  const cli = async (...a) => { calls.push(a); return { account_id: 'x', name: 'Savings' }; };
  const app = buildApp({ token: () => KEY, cli, allowHost: () => true });
  const srv = app.listen(0, '127.0.0.1'); await new Promise(r => srv.once('listening', r));
  const post = (body) => fetch(`http://127.0.0.1:${srv.address().port}/api/account`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
  try {
    assert.equal((await post({ name: 'Savings', institution: 'HSBC (personal)' })).status, 400, 'parentheses would 400 on /api/import/browser forever');
    assert.equal((await post({ name: 'Savings', institution: '#1 Credit Union' })).status, 400, 'a leading symbol likewise');
    assert.equal(calls.length, 0, 'an account that could never fetch is not created');
    assert.equal((await post({ name: 'Savings', institution: "St. Mary's & Co" })).status, 200, "but .&'- and digits are fine");
  } finally { srv.close(); }
});

function fakeSpawn() {
  const procs = [];
  const spawn = () => {
    const p = { exits: [], writes: [], killed: null, onData() {}, onExit(cb) { this.exits.push(cb); }, write(d) { this.writes.push(d); }, resize() {},
      kill(sig = 'SIGHUP') { this.killed = sig; } };
    procs.push(p); return p;
  };
  return { spawn, procs };
}

test('Stop ends the import session with a grace period, says it was stopped, and clears it for the next Fetch by AI', async () => {
  const { spawn, procs } = fakeSpawn();
  const sent = [];
  const r = importRunner({ broadcast: (m) => sent.push(m), make: (o) => createSession({ ...o, spawn, log: { warn() {}, error() {} } }) });
  r.start('HSBC');
  const done = r.stop();
  assert.equal(procs[0].killed, 'SIGHUP', 'a polite kill first, not SIGKILL');
  for (const cb of procs[0].exits) cb({ exitCode: 129 });   // claude goes within the grace
  await done;
  assert.equal(r.current, null);
  assert.ok(sent.some(m => m.type === 'output' && /import session stopped/.test(m.data)), 'the tab is told a person stopped it');
  assert.ok(!sent.some(m => m.type === 'output' && /ended \(/.test(m.data)), 'not dressed up as a crash');
  assert.deepEqual(sent.filter(m => m.type === 'state').map(m => m.state), ['WORKING', 'DOWN'], 'one DOWN, no double stop');
  r.start('Ally');   // not refused as "still running"
  assert.equal(procs.length, 2);
  await r.stop(1);   // its state timer would hold the test runner open
});

test('an import that exits on its own says ended with its code, live, so it reads differently from a Stop', () => {
  const { spawn, procs } = fakeSpawn();
  const sent = [];
  const r = importRunner({ broadcast: (m) => sent.push(m), make: (o) => createSession({ ...o, spawn, log: { warn() {}, error() {} } }) });
  r.start('HSBC');
  for (const cb of procs[0].exits) cb({ exitCode: 1 });
  assert.ok(sent.some(m => m.type === 'output' && /import session ended \(1\)/.test(m.data)));
  assert.equal(r.current, null);
});

test('a stop whose pty never reports its exit still frees the runner', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const { spawn, procs } = fakeSpawn();
  const sent = [];
  const r = importRunner({ broadcast: (m) => sent.push(m), make: (o) => createSession({ ...o, spawn, log: { warn() {}, error() {} } }) });
  r.start('HSBC');
  const done = r.stop(3_000);
  t.mock.timers.tick(3_000);
  await done;
  assert.equal(procs[0].killed, 'SIGKILL'); assert.equal(r.current, null);
  assert.equal(sent.at(-1).state, 'DOWN', 'the tab is told, so its Stop goes away');
});

test('a stop frame reaches only the import session; one without it, or aimed anywhere else, never touches the household', () => {
  const term = { stops: 0, writes: [], stop() { this.stops++; }, write(d) { this.writes.push(d); }, resize() {} };
  const imports = { stops: 0, current: null, stop() { this.stops++; } };
  const replies = [];
  const ctx = { term, imports, reply: (m) => replies.push(m) };
  for (const session of [undefined, 'chat', 'household', '', null]) frame({ type: 'stop', session }, ctx);
  assert.equal(term.stops, 0); assert.equal(imports.stops, 0);
  frame({ type: 'stop', session: 'import' }, ctx);   // a page that missed the end (a reconnect) presses Stop
  assert.equal(imports.stops, 0); assert.deepEqual(replies.splice(0), [{ type: 'state', session: 'import', state: 'DOWN' }], 'told it is over, so the tab does not sit on "stopping…"');
  imports.current = { write() {}, resize() {} };
  frame({ type: 'stop', session: 'import' }, ctx);
  assert.equal(imports.stops, 1); assert.equal(term.stops, 0);
  imports.current = null;
  frame({ type: 'input', session: 'import', data: 'x' }, ctx);   // typing into an ended import
  assert.deepEqual(term.writes, [], 'keystrokes for the import never land in the household chat');
  assert.deepEqual(replies, [{ type: 'state', session: 'import', state: 'DOWN' }], 'the typist is told it is over');
  frame({ type: 'resize', session: 'import', cols: 80, rows: 24 }, ctx);
  assert.equal(replies.length, 1, 'a resize into an ended import is dropped quietly');
  frame({ type: 'input', data: 'hi' }, ctx);
  assert.deepEqual(term.writes, ['hi']);
  term.size = null; term.resize = (c, r) => { term.size = [c, r]; };
  frame({ type: 'resize', cols: 90, rows: 30 }, ctx);
  assert.deepEqual(term.size, [90, 30]);
  const live = { writes: [], size: null, write(d) { this.writes.push(d); }, resize(c, r) { this.size = [c, r]; } };
  imports.current = live;
  frame({ type: 'input', session: 'import', data: 'y' }, ctx); frame({ type: 'resize', session: 'import', cols: 100, rows: 40 }, ctx);
  assert.deepEqual(live.writes, ['y']); assert.deepEqual(live.size, [100, 40]); assert.deepEqual(term.writes, ['hi'], 'a live import takes its own keys');
});

test('Stop with no import running is a no-op', async () => {
  const r = importRunner({ broadcast: () => assert.fail('nothing to announce'), make: () => assert.fail('nothing to start') });
  await r.stop();
  assert.equal(r.current, null);
});

test('a pty that outlives its Stop says nothing into the next import', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const { spawn, procs } = fakeSpawn();
  const sent = [];
  const r = importRunner({ broadcast: (m) => sent.push(m), make: (o) => createSession({ ...o, spawn, log: { warn() {}, error() {} } }) });
  r.start('HSBC');
  const done = r.stop(3_000); t.mock.timers.tick(3_000); await done;
  r.start('Ally');
  const n = sent.length;
  for (const cb of procs[0].exits) cb({ exitCode: 137 });   // the old one finally reports
  assert.equal(sent.length, n, 'no stray line or DOWN in the new tab'); assert.equal(r.current.session.state, 'WORKING');
  const end = r.stop(1); t.mock.timers.tick(1); await end;
});

test('an import that cannot spawn is DOWN at once, not a crash in the runner', () => {
  const r = importRunner({ broadcast: () => {}, make: (o) => createSession({ ...o, spawn: () => { throw new Error('ENOENT'); }, log: { warn() {}, error() {} } }) });
  r.start('HSBC');
  assert.equal(r.current.session.state, 'DOWN');
  r.start('HSBC');   // and the next Fetch by AI is not refused
});

test('the key: nothing is served without it, the tokened address becomes a cookie, a wrong key is refused, rotating locks old cookies out', async () => {
  let key = KEY;
  const app = buildApp({ cli: async () => ({}), allowHost: () => true, token: () => key });
  const srv = app.listen(0, '127.0.0.1'); await new Promise(r => srv.once('listening', r));
  const port = srv.address().port;
  try {
    // no key: the page, the API and a write all answer 401, and the CLI never ran (a 401 comes before the routes)
    assert.equal((await bare(port, '/')).status, 401);
    assert.match((await bare(port, '/', { accept: 'text/html' })).body, /finnamon open/, 'a browser is told what to run');
    const api = await globalThis.fetch(`http://127.0.0.1:${port}/api/summary`);
    assert.equal(api.status, 401); assert.match((await api.json()).error, /finnamon open/, 'a fetch() gets JSON that says what to run');
    assert.equal((await bare(port, '/api/summary')).status, 401);
    assert.equal(await raw(port, '/api/budget', { cookie: '' }), 401);
    assert.equal((await bare(port, '/app.js')).status, 401, 'static files too');
    // the right key in the address: a cookie, and a redirect to the clean address (the key leaves the history)
    const hit = await bare(port, `/?token=${KEY}&oauth_state_id=abc`);
    assert.equal(hit.status, 303); assert.equal(hit.headers.location, '/?oauth_state_id=abc', 'other parameters survive; the token does not');
    assert.equal(COOKIE_NAME, 'finnamon_token_8888', 'named by port: a dev dashboard on another PORT keeps its own cookie');
    assert.match(hit.headers['set-cookie'][0], /^finnamon_token_8888=k{64}; Path=\/; Max-Age=2592000; HttpOnly; SameSite=Lax$/, 'plain http: no Secure flag; 30 days');
    const tls = await bare(port, `/?token=${KEY}`, { 'x-forwarded-proto': 'https, https' });
    assert.match(tls.headers['set-cookie'][0], /; Secure$/, 'behind tailscale serve the cookie is Secure');
    assert.equal(tls.headers.location, '/');
    // a wrong key in the address, or in the cookie, or one of another length: refused, and no cookie is set
    for (const bad of ['j'.repeat(64), KEY.slice(1), KEY + 'k', '']) {
      const r = await bare(port, `/?token=${bad}`);
      assert.equal(r.status, 401, `token=${bad}`); assert.equal(r.headers['set-cookie'], undefined);
      assert.equal((await bare(port, '/api/summary', { cookie: `${COOKIE_NAME}=${bad}` })).status, 401);
    }
    assert.equal(await raw(port, '/api/budget', { cookie: '' }, 'POST'), 401);
    const posted = await new Promise((res, rej) => request({ host: '127.0.0.1', port, path: `/api/budget?token=${KEY}`, method: 'POST' }, (r) => { r.resume(); r.on('end', () => res(r)); }).on('error', rej).end());
    assert.equal(posted.statusCode, 401); assert.equal(posted.headers['set-cookie'], undefined, 'only a GET swaps the key for a cookie: a cross-site form cannot mint one');
    const wrongJson = await globalThis.fetch(`http://127.0.0.1:${port}/?token=nope`);
    assert.equal(wrongJson.status, 401); assert.match((await wrongJson.json()).error, /finnamon open/, 'a wrong key in the address answers in the {error} shape to a fetch()');
    // the cookie, or a bearer (the CLI's --to uploads), gets in
    assert.equal((await bare(port, '/api/summary', COOKIE)).status, 200);
    assert.equal((await bare(port, '/api/summary', { authorization: `Bearer ${KEY}` })).status, 200);
    assert.equal((await bare(port, '/api/summary', { authorization: `Bearer ${'j'.repeat(64)}` })).status, 401);
    assert.equal((await bare(port, '/api/summary', { cookie: `other=1; ${COOKIE_NAME}=${KEY}; more=2` })).status, 200, 'the cookie is found among others');
    assert.equal((await bare(port, '/api/summary', { cookie: `${COOKIE_NAME}=decoy; ${COOKIE_NAME}=${KEY}` })).status, 200, 'a decoy set by another localhost service (a longer Path is sent first) does not shadow the real one');
    // rotating the key: the cookie every open page holds stops working on the next request
    key = 'r'.repeat(64);
    assert.equal((await bare(port, '/api/summary', COOKIE)).status, 401);
    assert.equal((await bare(port, '/api/summary', { cookie: `${COOKIE_NAME}=${key}` })).status, 200);
  } finally { srv.close(); }
  // a key file that cannot be read refuses (503, and no path in the body) instead of throwing out of the request
  const broken = buildApp({ cli: async () => ({}), allowHost: () => true, token: () => { throw new Error('EACCES /home/x/.finnamon/web-token'); } });
  const bsrv = broken.listen(0, '127.0.0.1'); await new Promise(r => bsrv.once('listening', r));
  try {
    const r = await bare(bsrv.address().port, '/api/summary', COOKIE);
    assert.equal(r.status, 503); assert.ok(!r.body.includes('/home/x'), 'the file path stays in the log');
  } finally { bsrv.close(); }
  // the compare itself: constant-time on equal lengths, and never true across lengths or on an empty side
  assert.ok(same(KEY, KEY) && !same(KEY, KEY.slice(1)) && !same(KEY, KEY + 'k') && !same('', '') && !same(KEY, undefined) && !same(undefined, KEY));
});

test('the socket needs the key too: no Origin is a client on this machine only when it carries the cookie or a bearer', () => {
  const lo = loopback(8888);
  const sock = allowSocket(lo, () => KEY);
  const req = (headers) => ({ headers: { host: 'localhost:8888', ...headers } });
  assert.ok(!sock({ origin: undefined, req: req({}) }), 'origin-less and keyless: any local process; refused');
  assert.ok(!sock({ origin: 'http://localhost:8888', req: req({}) }), 'the right origin without the key is still refused');
  assert.ok(sock({ origin: undefined, req: req(COOKIE) }), 'a non-browser client with the cookie');
  assert.ok(sock({ origin: undefined, req: req({ authorization: `Bearer ${KEY}` }) }), 'or a bearer');
  const r = req(COOKIE); assert.ok(sock({ origin: 'http://localhost:8888', req: r })); assert.equal(r.auth, 'cookie', 'how it got in is left on the request for the connect log');
  assert.ok(!sock({ origin: undefined, req: req({ cookie: `${COOKIE_NAME}=${'j'.repeat(64)}` }) }));
  assert.ok(!sock({ origin: 'http://evil.example', req: req(COOKIE) }), 'a hostile tab that somehow had the cookie: the Origin check still holds');
  assert.ok(!sock({ origin: 'http://localhost:8888', req: { headers: { host: 'evil.example:8888', ...COOKIE } } }));
  assert.equal(authOf(req({}), KEY), null); assert.equal(authOf(req({ authorization: `bearer ${KEY}` }), KEY), 'bearer');
  // a socket let in under one key is re-checked on every frame: a rotate ends it even where fs.watch stayed silent
  const admitted = req(COOKIE); sock({ origin: undefined, req: admitted }); assert.equal(admitted.key, KEY, 'the key it was let in with rides the request');
  const closes = []; const fake = { key: KEY, close: (code, why) => closes.push([code, why]) };
  assert.ok(guardKey(fake, () => KEY)); assert.deepEqual(closes, []);
  assert.ok(!guardKey(fake, () => 'r'.repeat(64))); assert.deepEqual(closes, [[4001, 'key changed']]);
  closes.length = 0;
  assert.ok(!guardKey(fake, () => { throw new Error('gone'); }), 'an unreadable key file drops the frame');
  assert.deepEqual(closes, [], 'but does not close the socket: a read error is not a rotation, and must not tell the page to re-key');
  // the refusal log: one line a minute per peer and reason, then a count
  let t = 0; const lines = []; const log = (m) => lines.push(m);
  assert.ok(throttled('k', t, log)); assert.ok(!throttled('k', t += 1000, log)); assert.ok(!throttled('k', t += 1000, log)); assert.ok(throttled('other', t, log));
  assert.ok(throttled('k', t += 60_000, log)); assert.match(lines.at(-1), /and 2 more like it/);
  assert.ok(!allowSocket(lo, () => { throw new Error('EACCES'); })({ origin: undefined, req: req(COOKIE) }), 'and refuses a new socket instead of throwing out of the upgrade');
  // the connect log names the phone behind tailscale serve (the proxy's own entry, the last), never a header a remote client wrote itself
  assert.equal(peer({ headers: { 'x-forwarded-for': 'spoofed, 100.64.0.7' }, socket: { remoteAddress: '127.0.0.1' } }), '127.0.0.1 (for 100.64.0.7)', 'the socket address always stands; the proxy\'s entry is added');
  assert.equal(peer({ headers: {}, socket: { remoteAddress: '::1' } }), '::1');
  assert.equal(peer({ headers: { 'x-forwarded-for': '100.64.0.7' }, socket: { remoteAddress: '192.168.1.9' } }), '192.168.1.9', 'not from loopback: the header is not the proxy\'s');
});

test('the key file: made once with 0600, 64 hex characters, kept across starts, and an existing one is read, never replaced', () => {
  const home = mkdtempSync(join(tmpdir(), 'finnamon-key-'));
  try {
    const a = webToken(home);
    assert.match(a, /^[0-9a-f]{64}$/);
    assert.equal(statSync(join(home, TOKEN_FILE)).mode & 0o777, 0o600);
    assert.equal(webToken(home), a, 'a restart finds the same key');
    const foreign = 'f'.repeat(64);
    writeFileSync(join(home, TOKEN_FILE), `${foreign}\n`);
    assert.equal(webToken(home), foreign, 'the CLI may have made it first; whitespace is trimmed');
    assert.equal(readFileSync(join(home, TOKEN_FILE), 'utf8'), `${foreign}\n`);
    writeFileSync(join(home, TOKEN_FILE), 'my-password\n');
    assert.throws(() => webToken(home), /not a hex key/, 'a hand-edited key is reported, not served (no cookie or address could carry it)');
    assert.equal(readFileSync(join(home, TOKEN_FILE), 'utf8'), 'my-password\n', 'and not overwritten');
    writeFileSync(join(home, TOKEN_FILE), '  \n'); chmodSync(join(home, TOKEN_FILE), 0o644);
    const again = webToken(home);
    assert.equal(statSync(join(home, TOKEN_FILE)).mode & 0o777, 0o600, 'a re-minted key does not keep the empty file\'s wider bits');
    chmodSync(join(home, TOKEN_FILE), 0o000);
    if (process.getuid?.() !== 0) assert.throws(() => webToken(home), /EACCES|EPERM/, 'an unreadable key file is an error to refuse on, never one to overwrite');
    chmodSync(join(home, TOKEN_FILE), 0o600);
    assert.equal(webToken(home), again, 'and the key was not touched');
    assert.match(again, /^[0-9a-f]{64}$/, 'a blank file (a write that died halfway) counts as missing: a blank key would lock everyone out');
    assert.equal(readFileSync(join(home, TOKEN_FILE), 'utf8'), again);
    const fresh = join(home, 'nested', 'home');
    assert.match(webToken(fresh), /^[0-9a-f]{64}$/, 'a home that does not exist yet is created');
    assert.equal(statSync(join(home, 'nested', 'home')).mode & 0o777, 0o700);
    // the same file name on both sides: `finnamon open` (config.py) and the server must read one key
    const py = readFileSync(join(import.meta.dirname, '..', '..', 'finnamon', 'config.py'), 'utf8');
    assert.equal(/WEB_TOKEN_FILE = "([^"]+)"/.exec(py)[1], TOKEN_FILE);
  } finally { rmSync(home, { recursive: true, force: true }); }
});

test('alerts with no bank linked say "Link a bank to start", never "Nothing to flag"', () => {
  const app = readFileSync(join(import.meta.dirname, '../public/app.js'), 'utf8');
  const html = readFileSync(join(import.meta.dirname, '../public/index.html'), 'utf8');
  assert.ok(html.includes('id="alerts-nolink"') && html.includes('Link a bank to start') && html.includes('id="nolink-btn"'));
  assert.ok(app.includes("$('nolink-btn').addEventListener('click', () => linkBank())"), 'the button is the same Link account the menu has');
  const src = /\nconst KIND_ICONS[\s\S]*?\nfunction renderAlerts\(s\) \{[\s\S]*?\n\}\n/.exec(app)[0];
  const els = {};
  const $ = (id) => (els[id] ||= { style: {}, innerHTML: '' });
  const renderAlerts = new Function('$', 'esc', 'icon', 'when', `${src}; return renderAlerts;`)($, String, () => '', String);
  const shown = (id) => els[id].style.display !== 'none';
  renderAlerts({ status: { items: [] }, alerts: [] });
  assert.ok(shown('alerts-nolink') && !shown('alerts-empty'), 'nothing linked: nothing is watched');
  renderAlerts({ status: { items: [{ item_id: 'i' }] }, alerts: [] });
  assert.ok(!shown('alerts-nolink') && shown('alerts-empty'), 'a bank and no alerts: all is well');
  renderAlerts({ status: { items: [{ item_id: 'i' }] }, alerts: [{ kind: 'duplicate', text: 'x', created_at: 't' }] });
  assert.ok(!shown('alerts-nolink') && !shown('alerts-empty'));
});

test('an alert\'s It\'s normal / Dismiss / Undo run the chat\'s commands, for a numeric id and a known action only, with the key', async () => {
  await withApp(async ({ base, post, calls }) => {
    for (const [act, argv] of [['normal', ['normal', '--alert', '12']], ['dismiss', ['alerts', '--dismiss', '12']], ['undo', ['alerts', '--undo', '12']]]) {
      assert.equal((await post(`/api/alert/12/${act}`, {})).status, 200, act);
      assert.deepEqual(calls.at(-1), argv);
    }
    const n = calls.length;
    for (const path of ['/api/alert/0/normal', '/api/alert/-1/dismiss', '/api/alert/1e3/undo', '/api/alert/12abc/undo', `/api/alert/${'9'.repeat(11)}/undo`,
                        '/api/alert/12/remove', '/api/alert/12/__proto__', '/api/alert/12/toString'])
      assert.equal((await post(path, {})).status, 400, path);
    const port = new URL(base).port;
    assert.equal((await globalThis.fetch(`${base}/api/alert/12/dismiss`, { method: 'POST' })).status, 401, 'no key, no write');
    assert.equal(await raw(port, '/api/alert/12/dismiss', { origin: 'http://evil.example' }), 403, 'another site\'s page cannot post here');
    assert.equal(calls.length, n);
  });
});

test('a CLI refusal (an alert already resolved) comes back as a 400 with its sentence', async () => {
  const cli = async () => { throw Object.assign(new Error('x'), { stderr: 'alert 3 is already resolved\n' }); };
  const app = buildApp({ token: () => KEY, cli, allowHost: (h) => h.startsWith('127.0.0.1:') });
  const srv = app.listen(0, '127.0.0.1'); await new Promise(r => srv.once('listening', r));
  try {
    const r = await fetch(`http://127.0.0.1:${srv.address().port}/api/alert/3/dismiss`, { method: 'POST' });
    assert.equal(r.status, 400);
    assert.deepEqual(await r.json(), { error: 'alert 3 is already resolved' });
  } finally { srv.close(); }
});

test('resolved alerts render apart, greyed, saying how; only an undoable one gets Undo', () => {
  const app = readFileSync(join(import.meta.dirname, '../public/app.js'), 'utf8');
  const html = readFileSync(join(import.meta.dirname, '../public/index.html'), 'utf8');
  assert.ok(/<details class="resolved" id="alerts-resolved" hidden>/.test(html), 'collapsed until opened');
  const src = /\nconst KIND_ICONS[\s\S]*?\nfunction renderAlerts\(s\) \{[\s\S]*?\n\}\n/.exec(app)[0];
  const els = {};
  const $ = (id) => (els[id] ||= { style: {}, innerHTML: '' });
  const renderAlerts = new Function('$', 'esc', 'icon', 'when', `${src}; return renderAlerts;`)($, String, () => '', String);
  renderAlerts({ status: { items: [{}] }, alerts: [{ id: 7, kind: 'duplicate_charge', text: 'dup', sent_at: 't' }], resolved: [
    { id: 5, text: 'a', resolved_at: 'r', resolution: 'normal', suppression_id: 9 }, { id: 4, text: 'b', resolved_at: 'r', resolution: 'dismissed' },
    { id: 3, text: 'c', resolved_at: 'r', resolution: null }, { id: 2, text: 'd', resolved_at: 'r', resolution: 'recovered' },
    { id: 1, text: 'e', resolved_at: 'r', resolution: 'unlinked' }] });
  const open = els['alerts-body'].innerHTML, done = els['alerts-resolved-body'].innerHTML;
  // Value: protects=Finnamon's own closes say why (bank synced again / bank removed); fails_when=the recovered/unlinked labels are dropped; why_new=only normal/dismissed/null were rendered; seam=none
  assert.ok(done.includes('Synced again · r') && done.includes('Bank removed · r'));
  assert.ok(open.includes('data-alert="7" data-act="normal"') && open.includes('data-alert="7" data-act="dismiss"'));
  assert.ok(!/onclick/i.test(open + done), 'no inline handlers: the CSP would refuse them');
  assert.ok(done.includes('It’s normal (rule 9)') && done.includes('Dismissed') && done.includes('Resolved · r'));
  assert.deepEqual([...done.matchAll(/data-alert="(\d+)" data-act="undo"/g)].map(m => m[1]), ['5', '4'], 'one Finnamon resolved itself has nothing to undo');
  assert.equal(els['alerts-resolved'].hidden, false);
  renderAlerts({ status: { items: [{}] }, alerts: [], resolved: [] });
  assert.equal(els['alerts-resolved'].hidden, true);
});

test('setting a manual account\'s balance: its id only, a number only, the amount after --', async () => {
  await withApp(async ({ base, calls }) => {
    const post = (body) => fetch(`${base}/api/account/balance`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
    assert.equal((await post({ account: 'manual:amex:amex', balance: '$1,234.50' })).status, 200);
    assert.deepEqual(calls.at(-1), ['account', 'balance', '--', 'manual:amex:amex', '1234.5']);
    const n = calls.length;
    assert.equal((await post({ account: 'Amex', balance: '5' })).status, 400, 'a name or a Plaid id is not a manual account id');
    assert.equal((await post({ account: 'manual:amex:amex', balance: 'lots' })).status, 400);
    assert.equal((await post({ account: 'manual:amex:amex', balance: '' })).status, 400);
    assert.equal(calls.length, n, 'the CLI never ran');
  });
});

test('more than 8 open alerts: the first 8 and a Show N more that lists the rest', () => {
  const app = readFileSync(join(import.meta.dirname, '../public/app.js'), 'utf8');
  const html = readFileSync(join(import.meta.dirname, '../public/index.html'), 'utf8');
  assert.ok(html.includes('<button class="quiet" id="alerts-more" type="button" hidden>'));
  const src = /\nconst KIND_ICONS[\s\S]*?\nfunction renderAlerts\(s\) \{[\s\S]*?\n\}\n/.exec(app)[0];
  const els = {};
  const $ = (id) => (els[id] ||= { style: {}, innerHTML: '' });
  const [renderAlerts, showAll] = new Function('$', 'esc', 'icon', 'when', `${src}; return [renderAlerts, () => { alertsAll = true; }];`)($, String, () => '', String);
  const alerts = Array.from({ length: 11 }, (_, i) => ({ id: i + 1, kind: 'duplicate_charge', text: `a${i}`, sent_at: 't' }));
  renderAlerts({ status: { items: [{}] }, alerts, resolved: [] });
  assert.equal(els['alerts-body'].innerHTML.split('</li>').length - 1, 8);
  assert.equal(els['alerts-more'].hidden, false);
  assert.equal(els['alerts-more'].textContent, 'Show 3 more');
  showAll();
  renderAlerts({ status: { items: [{}] }, alerts, resolved: [] });
  assert.equal(els['alerts-body'].innerHTML.split('</li>').length - 1, 11);
  assert.equal(els['alerts-more'].hidden, true);
});

test('removing a manual account: the name after --, --yes (the page asked), and only with the key from this page\'s origin', async () => {
  await withApp(async ({ base, calls }) => {
    const port = new URL(base).port, path = `/api/account/${encodeURIComponent('--HSBC Checking')}`;
    assert.equal((await fetch(base + path, { method: 'DELETE' })).status, 200);
    assert.deepEqual(calls.at(-1), ['account', 'remove', '--yes', '--', '--HSBC Checking']);
    const id = `manual:${'h'.repeat(40)}:${'c'.repeat(40)}`;   // what the page sends: longer than a name may be
    assert.equal((await fetch(`${base}/api/account/${encodeURIComponent(id)}`, { method: 'DELETE' })).status, 200);
    assert.deepEqual(calls.at(-1), ['account', 'remove', '--yes', '--', id]);
    const n = calls.length;
    assert.equal((await fetch(`${base}/api/account/${'x'.repeat(81)}`, { method: 'DELETE' })).status, 400);
    assert.equal(await raw(port, path, { cookie: '' }, 'DELETE'), 401, 'no key');
    assert.equal(await raw(port, path, { origin: 'https://evil.example' }, 'DELETE'), 403, 'a cross-site page');
    assert.equal(calls.length, n, 'the CLI never ran');
  });
});

test('the Import CSV panel\'s trash button confirms with the name and transaction count, then DELETEs the account id, not the name', () => {
  const app = readFileSync(join(import.meta.dirname, '../public/app.js'), 'utf8');
  const src = /\nconst ON_BOX[\s\S]*?\nfunction renderImport\(s\) \{[\s\S]*?\n\}\n/.exec(app)[0];
  const els = {};
  const $ = (id) => (els[id] ||= { style: {}, innerHTML: '', value: '', listeners: {}, addEventListener(type, fn) { this.listeners[type] = fn; } });
  const calls = { confirm: [], api: [] };
  const confirm = (msg) => { calls.confirm.push(msg); return calls.confirmReturns.shift(); };
  const api = async (method, url) => { calls.api.push([method, url]); return { ok: true }; };
  const renderImport = new Function('$', 'esc', 'icon', 'when', 'plural', 'location', 'confirm', 'api', 'toast', 'loadSummary', 'toggleImport', 'showView', 'openIntercom', 'summary',
    `${src}; return renderImport;`)($, String, () => '', String, (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`, { hostname: 'localhost' }, confirm, api,
    () => {}, () => Promise.reject(new Error('offline')), () => {}, () => {}, () => {}, null);

  const accounts = [
    { name: 'HSBC Checking', institution: 'HSBC', account_id: 'manual:hsbc:hsbc-checking', transactions: 3, source: 'manual' },
    { name: 'Ally Savings', institution: 'Ally', account_id: 'manual:ally:ally-savings', transactions: 2, source: 'manual' },
  ];
  renderImport({ accounts });
  assert.ok(els['i-del'].listeners.click, 'a trash button with two manual accounts');
  assert.ok(els['import-pop'].innerHTML.includes('id="i-del"'), 'and it is actually in the rendered markup, not just a stub $ auto-vivifying it');

  $('i-acct').value = 'Ally Savings';
  calls.confirmReturns = [false];
  els['i-del'].listeners.click();
  assert.deepEqual(calls.confirm, ['Remove Ally · Ally Savings and its 2 transactions? This can\'t be undone.']);
  assert.deepEqual(calls.api, [], 'cancel: nothing sent');

  calls.confirmReturns = [true];
  return els['i-del'].listeners.click().then(() => {
    assert.deepEqual(calls.confirm, ['Remove Ally · Ally Savings and its 2 transactions? This can\'t be undone.', 'Remove Ally · Ally Savings and its 2 transactions? This can\'t be undone.'], 'the second click asked again: confirmation was not skipped on the accepted path');
    assert.deepEqual(calls.api, [['DELETE', `/api/account/${encodeURIComponent('manual:ally:ally-savings')}`]], 'the account_id, never the name: a Plaid account may share it');

    // no manual account at all: no trash button in the markup, and the render does not throw
    const els2 = {};
    const $2 = (id) => (els2[id] ||= { style: {}, innerHTML: '', value: '', listeners: {}, addEventListener(type, fn) { this.listeners[type] = fn; } });
    const renderImportEmpty = new Function('$', 'esc', 'icon', 'when', 'plural', 'location', 'confirm', 'api', 'toast', 'loadSummary', 'toggleImport', 'showView', 'openIntercom', 'summary',
      `${src}; return renderImport;`)($2, String, () => '', String, (n, w) => `${n} ${w}s`, { hostname: 'localhost' }, confirm, api, () => {}, () => Promise.resolve(), () => {}, () => {}, () => {}, null);
    renderImportEmpty({ accounts: [] });
    assert.ok(!els2['import-pop'].innerHTML.includes('id="i-del"'), 'no manual account: no trash button in the rendered markup');
  });
});

test('Link account files the bank under the chosen owner, through the OAuth round trip', async () => {
  const calls = [];
  const cli = async (...a) => { calls.push(a); return a[0] === 'owner' ? [{ owner: 'bill' }, { owner: 'sam' }] : a[1] === '--token' ? { link_token: 'lt' } : { ok: 1, institution: 'Chase' }; };
  const app = buildApp({ token: () => KEY, cli, allowHost: () => true });
  const srv = app.listen(0, '127.0.0.1'); await new Promise(r => srv.once('listening', r));
  const base = `http://127.0.0.1:${srv.address().port}`;
  const post = (path, body) => fetch(base + path, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
  try {
    assert.equal((await post('/api/link/token', { owner: 'nobody; rm' })).status, 400);
    assert.ok(!calls.some(c => c[1] === '--token'), 'an unknown owner never reaches `link --token`');
    assert.equal((await post('/api/link/token', { owner: 'joint' })).status, 200);
    assert.deepEqual(calls.at(-1), ['link', '--token', '--owner', 'joint'], 'joint is a choice, not a member');
    assert.equal((await post('/api/link/token', { owner: 'sam' })).status, 200);
    assert.deepEqual(calls.at(-1), ['link', '--token', '--owner', 'sam']);
    assert.deepEqual(await (await fetch(base + '/api/link/token')).json(), { link_token: 'lt', owner: 'sam' });
    const fin = await (await post('/api/link/finish', { public_token: 'public-sandbox-abc-1' })).json();
    assert.deepEqual(calls.at(-1), ['link', '--public-token', 'public-sandbox-abc-1', '--owner', 'sam']);
    assert.equal(fin.owner, 'sam');
    await post('/api/link/token', {});   // one member: the client sends no owner
    assert.deepEqual(calls.at(-1), ['link', '--token']);
  } finally { srv.close(); }
});

test('the Link account picker is asked only with more than one member', () => {
  const app = readFileSync(join(import.meta.dirname, '../public/app.js'), 'utf8'), html = readFileSync(join(import.meta.dirname, '../public/index.html'), 'utf8');
  assert.ok(html.includes('id="owner-pop"'));
  assert.ok(/members\.length < 2\) return Promise\.resolve\(null\)/.test(app), 'one member: no question');
  assert.ok(app.includes("const owner = await askOwner();") && app.includes("owner ? { owner } : undefined"), 'no owner is sent for one member');
  assert.ok(app.includes('${esc(m.display_name || m.owner)}') && app.includes("'' : ' checked'"), 'display name with the owner name as fallback; the first member is preselected');
});

test('a request Tailscale Funnel carried in from the public internet is refused, page and socket alike', async () => {
  const ts = loopback(8888, 'mini.tail1.ts.net', () => '');
  const app = buildApp({ token: () => KEY, cli: async () => ({}), allowHost: ts.host, allowOrigin: ts.origin });
  const srv = app.listen(0, '127.0.0.1'); await new Promise(r => srv.once('listening', r));
  const port = srv.address().port;
  assert.equal(await raw(port, '/api/summary', { host: 'mini.tail1.ts.net' }, 'GET'), 200, 'the tailnet: in, with the key');
  assert.equal(await raw(port, '/api/summary', { host: 'mini.tail1.ts.net', 'tailscale-funnel-request': '?1' }, 'GET'), 403);
  srv.close();
  const sock = allowSocket(ts, () => KEY);
  assert.ok(sock({ origin: 'https://mini.tail1.ts.net', req: { headers: { host: 'mini.tail1.ts.net', ...COOKIE } } }));
  assert.ok(!sock({ origin: 'https://mini.tail1.ts.net', req: { headers: { host: 'mini.tail1.ts.net', 'tailscale-funnel-request': '?1', ...COOKIE } } }));
});

test('the dashboard reads ~/.finnamon/web-hosts itself; an unreadable one lets only localhost in, and says why', () => {
  const file = join(process.env.FINNAMON_HOME, HOSTS_FILE);
  const lo = loopback(8888, '');
  const warned = [], warn = console.warn; console.warn = (m) => warned.push(m);
  try {
    assert.ok(!lo.host('mini.tail1.ts.net') && warned.length === 0, 'missing is off, and quiet');
    writeFileSync(file, 'Mini.Tail1.ts.net\n');
    assert.ok(lo.host('mini.tail1.ts.net') && lo.origin('https://mini.tail1.ts.net'));
    rmSync(file); mkdirSync(file);   // EISDIR: not "off", a broken file
    assert.ok(!lo.host('mini.tail1.ts.net') && lo.host('localhost:8888'));
    assert.equal(warned.length, 1); assert.match(warned[0], /cannot read web-hosts/);
  } finally { console.warn = warn; rmSync(file, { recursive: true, force: true }); }
});

test('a pairing code from `finnamon remote`: one device, once, before it expires, on the tailnet name only, never over Funnel', async () => {
  const home = mkdtempSync(join(tmpdir(), 'finnamon-pair-')), file = join(home, PAIR_FILE);
  const pend = (code, expires = Date.now() / 1000 + 300) => writeFileSync(file, JSON.stringify({ sha256: createHash('sha256').update(code).digest('hex'), expires }), { mode: 0o600 });
  const ts = loopback(8888, 'mini.tail1.ts.net', () => '');
  const app = buildApp({ token: () => KEY, cli: async () => ({}), allowHost: (h) => ts.host(h) || h.startsWith('127.0.0.1:'), allowOrigin: ts.origin, home });
  const srv = app.listen(0, '127.0.0.1'); await new Promise(r => srv.once('listening', r));
  const port = srv.address().port, TS = { host: 'mini.tail1.ts.net', 'x-forwarded-proto': 'https' };
  const logged = [], log = console.log; console.log = (m) => logged.push(m);
  try {
    pend('good-code');
    // the right code on the tailnet name: the key's cookie (never the code), Secure behind serve, and the clean address
    const hit = await bare(port, '/?pair=good-code&x=1', TS);
    assert.equal(hit.status, 303); assert.equal(hit.headers.location, '/?x=1');
    assert.match(hit.headers['set-cookie'][0], new RegExp(`^${COOKIE_NAME}=${KEY}; Path=/; Max-Age=2592000; HttpOnly; SameSite=Lax; Secure$`));
    // single use: the second scan is refused and says why
    const again = await bare(port, '/?pair=good-code', { ...TS, accept: 'text/html' });
    assert.equal(again.status, 401); assert.match(again.body, /already used/); assert.match(again.body, /finnamon remote/); assert.equal(again.headers['set-cookie'], undefined);
    // expired
    pend('old-code', Date.now() / 1000 - 1);
    const old = await bare(port, '/?pair=old-code', { ...TS, accept: 'text/html' });
    assert.equal(old.status, 401); assert.match(old.body, /expired/); assert.equal(old.headers['set-cookie'], undefined);
    // wrong code, and one replaced by a newer `finnamon remote`; a wrong guess does not spend the right one
    pend('new-code');
    for (const bad of ['good-code', 'nope', '']) {
      const r = await bare(port, `/?pair=${bad}`, { ...TS, accept: 'text/html' });
      assert.equal(r.status, 401, bad); assert.match(r.body, /not valid/); assert.equal(r.headers['set-cookie'], undefined);
    }
    // wrong host: localhost has `finnamon open`, an unlisted name is refused before anything; Funnel never
    assert.equal((await bare(port, '/?pair=new-code', { host: `127.0.0.1:${port}` })).status, 403);
    assert.equal((await bare(port, '/?pair=new-code', { host: 'evil.example' })).status, 421);
    assert.equal((await bare(port, '/?pair=new-code', { ...TS, 'tailscale-funnel-request': '?1' })).status, 403);
    const spent = await bare(port, '/x/..//evil.example/?pair=new-code', TS);
    assert.equal(spent.status, 303, 'none of those spent it'); assert.equal(spent.headers.location, '/evil.example/', 'never a protocol-relative redirect off the host');
    // a pairing file it cannot mark used: refused, never let in, and no path in the body
    pend('ro-code'); chmodSync(file, 0o400);
    const ro = await bare(port, '/?pair=ro-code', { ...TS, accept: 'text/html' });
    assert.equal(ro.status, 503); assert.equal(ro.headers['set-cookie'], undefined); assert.ok(!ro.body.includes(home));
    chmodSync(file, 0o600);
    // no pending code at all
    rmSync(file);
    assert.equal(redeemPair('new-code', home), 'invalid');
    assert.ok(!logged.some(l => /good-code|new-code|old-code/.test(l)) && logged.some(l => /pairing let a device in/.test(l)), 'the outcome is logged, never the code');
  } finally { console.log = log; srv.close(); rmSync(home, { recursive: true, force: true }); }
});

test('Reconnect runs link --web --update <item> for the page only (cookie and Origin) and a plain item id', async () => {
  const calls = [];
  const app = buildApp({ token: () => KEY, cli: async (...a) => { calls.push(a); return { url: 'https://hosted.plaid.com/x' }; },
    allowHost: (h) => h.startsWith('127.0.0.1:'), allowOrigin: (o) => /^http:\/\/127\.0\.0\.1:\d+$/.test(o) });
  const srv = app.listen(0, '127.0.0.1'); await new Promise(r => srv.once('listening', r));
  const base = `http://127.0.0.1:${srv.address().port}`, page = { cookie: `${COOKIE_NAME}=${KEY}`, origin: base };
  const post = (path, headers = page) => globalThis.fetch(base + path, { method: 'POST', headers });
  try {
    assert.equal((await post('/api/item/aBc_9-x/reconnect')).status, 200);
    assert.deepEqual(calls.at(-1), ['link', '--web', '--update', 'aBc_9-x']);
    for (const path of ['/api/item/-x/reconnect', '/api/item/--remove/reconnect', `/api/item/${'a'.repeat(101)}/reconnect`, '/api/item/a%20b/reconnect', '/api/item/a.b/reconnect'])
      assert.equal((await post(path)).status, 400, path);
    assert.equal((await post('/api/item/abc/reconnect', { authorization: `Bearer ${KEY}`, origin: base })).status, 403, 'a bearer key is not a person: this path has no rate limit');
    assert.equal((await post('/api/item/abc/reconnect', { cookie: page.cookie })).status, 403, 'no Origin: not the page');
    assert.equal((await post('/api/item/abc/reconnect', { ...page, origin: 'http://evil.example' })).status, 403);
    assert.equal((await post('/api/item/abc/reconnect', { origin: base })).status, 401);
    assert.equal(calls.length, 1);
  } finally { srv.close(); }
});

test('the Reconnect click opens the tab first, sends it only to an https address, and closes it on failure', async () => {
  const app = readFileSync(join(import.meta.dirname, '../public/app.js'), 'utf8');
  const src = /\nasync function reconnect\(b\) \{[\s\S]*?\n\}\n/.exec(app)[0];
  const run = async (answer, popup = true) => {
    const seen = { toasts: [], assigned: null, tab: null };
    const tab = { opener: 'page', location: { href: '' }, closed: false, close() { this.closed = true; } };
    const window = { open: () => { seen.opened = true; return popup ? tab : null; } };
    const api = async () => { seen.openedBeforeApi = !!seen.opened; return answer; };
    const location = { assign: (u) => { seen.assigned = u; } };
    const reconnect = new Function('window', 'api', 'toast', 'location', `${src}; return reconnect;`)(window, api, (t, o) => seen.toasts.push([t, o?.kind || 'ok']), location);
    const b = { dataset: { reconnect: 'item9' }, disabled: false };
    await reconnect(b);
    return { ...seen, tab, b };
  };
  let r = await run({ ok: true, url: 'https://hosted.plaid.com/link/1' });
  assert.ok(r.openedBeforeApi, 'opened on the click itself, or Safari blocks it');
  assert.equal(r.tab.opener, null); assert.equal(r.tab.location.href, 'https://hosted.plaid.com/link/1'); assert.equal(r.b.disabled, false);
  assert.equal(r.toasts[0][1], 'ok');
  r = await run({ ok: true, url: 'javascript:alert(1)' });
  assert.ok(r.tab.closed && r.tab.location.href === '' && r.toasts[0][1] === 'warn', 'nothing but https');
  r = await run({ ok: false, error: 'no token for item item9' });
  assert.ok(r.tab.closed); assert.deepEqual(r.toasts, [['no token for item item9', 'warn']]);
  r = await run({ ok: false, error: '' });                                             // a 401: lock() already said why
  assert.ok(r.tab.closed && r.toasts.length === 0);
  r = await run({ ok: true, url: 'https://hosted.plaid.com/link/2' }, false);       // popup blocked: this tab goes
  assert.equal(r.assigned, 'https://hosted.plaid.com/link/2');
});

test('a re-login alert shows Reconnect and Dismiss, never It\'s normal, and the page\'s own words; a reconnected one has no Undo', () => {
  const app = readFileSync(join(import.meta.dirname, '../public/app.js'), 'utf8');
  const src = /\nconst KIND_ICONS[\s\S]*?\nfunction renderAlerts\(s\) \{[\s\S]*?\n\}\n/.exec(app)[0];
  const els = {};
  const $ = (id) => (els[id] ||= { style: {}, innerHTML: '' });
  const renderAlerts = new Function('$', 'esc', 'icon', 'when', `${src}; return renderAlerts;`)($, String, () => '', String);
  renderAlerts({ status: { items: [{}] }, alerts: [
    { id: 4, kind: 'sync_health', text: 'Venmo needs a re-login. Reply <i>fix Venmo</i>', page_text: 'Venmo needs a re-login. Until then', reconnect: 'item9', sent_at: 't' },
    { id: 5, kind: 'duplicate_charge', text: 'dup', sent_at: 't' }],
    resolved: [{ id: 3, kind: 'sync_health', text: 'x', resolved_at: 't', resolution: 'reconnected' }] });
  const [relogin, dup] = els['alerts-body'].innerHTML.split('</li>');
  assert.ok(relogin.includes('data-reconnect="item9"') && relogin.includes('>Reconnect<') && relogin.includes('data-act="dismiss"'));
  assert.ok(!relogin.includes('data-act="normal"') && !relogin.includes('Reply'), 'the button replaces both the reply hint and It’s normal');
  assert.ok(dup.includes('data-act="normal"') && !dup.includes('data-reconnect'));
  assert.ok(els['alerts-resolved-body'].innerHTML.includes('Reconnected') && !els['alerts-resolved-body'].innerHTML.includes('data-act="undo"'));
});

test('a manual account carries its owner, and merge / unmerge reach the CLI with checked ids', async () => {
  const calls = [];
  const cli = async (...a) => { calls.push(a); return {}; };
  const app = buildApp({ token: () => KEY, cli, allowHost: () => true });
  const srv = app.listen(0, '127.0.0.1'); await new Promise(r => srv.once('listening', r));
  const base = `http://127.0.0.1:${srv.address().port}`;
  const post = (path, body) => fetch(`${base}${path}`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
  try {
    await post('/api/account', { name: 'HSBC Joint', institution: 'HSBC', owner: 'joint' });
    assert.deepEqual(calls.at(-1), ['account', 'add', '--institution', 'HSBC', '--type', 'checking', '--owner', 'joint', '--', 'HSBC Joint']);
    await post('/api/account', { name: 'HSBC Joint 2', institution: 'HSBC' });
    assert.ok(!calls.at(-1).includes('--owner'), 'no owner: the CLI picks the first member, as before');
    assert.equal((await post('/api/account/merge', { account: 'acc_2', same_as: 'acc_1' })).status, 200);
    assert.deepEqual(calls.at(-1), ['account', 'merge', '--', 'acc_2', 'acc_1']);
    assert.equal((await post('/api/account/merge', { account: 'acc 2; --force', same_as: 'acc_1' })).status, 400);
    assert.equal((await post('/api/account/merge', { account: 'acc_2' })).status, 400);
    assert.equal((await post('/api/account/unmerge', { account: 'acc_2' })).status, 200);
    assert.deepEqual(calls.at(-1), ['account', 'unmerge', '--', 'acc_2']);
    assert.equal((await post('/api/account/unmerge', {})).status, 400);
  } finally { srv.close(); }
});
