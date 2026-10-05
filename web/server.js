// Finnamon's local dashboard. One process, three jobs:
//   1. serve the page (net worth with cash / investments / property / liabilities, budgets with a Manage mode, alerts,
//      a chart panel) from data the `finnamon` CLI already prints as JSON, and run the same commands a person would
//      for the page's edits (budgets, properties) and for Plaid Link in the page;
//   2. keep the household's Claude Code session alive in a PTY and bridge it to the page's intercom over a WebSocket
//      (node-pty ⇄ ws ⇄ xterm.js, the pattern lifted from agent-007);
//   3. watch ~/.finnamon/charts/current.json, the list of charts `finnamon chart` keeps, and push it to the page.
// Localhost only: it is a shell on this machine. Put Tailscale in front of it for the phone; never a public port.
// Four checks stand between anything else and that shell. Only loopback Host headers are served (DNS rebinding); the
// WebSocket only accepts the page's own Origin (cross-site WebSocket hijacking); the page's CSP runs no script but its own
// files and the pinned CDN ones (a chart spec built from bank strings cannot become script); and every request, the page,
// the API and the socket alike, has to carry the dashboard's key (the Jupyter model): a random token in ~/.finnamon/web-token
// that `finnamon open` puts in the address once, which this server swaps for an HttpOnly cookie. Without it, any local
// process, or any device on the tailnet once the page is served over Tailscale, could open the socket and type into the shell.

import express from 'express';
import { WebSocketServer } from 'ws';
import { spawn as spawnPty } from 'node-pty';
import { execFile, spawn as spawnChild } from 'node:child_process';
import { promisify } from 'node:util';
import { createServer } from 'node:http';
import { existsSync, readFileSync, writeFileSync, watch, mkdirSync, chmodSync, rmSync, readdirSync, openSync, closeSync } from 'node:fs';
import { randomUUID, randomBytes, createHash, timingSafeEqual } from 'node:crypto';
import { homedir } from 'node:os';
import { join, dirname, basename } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import { createTalk, createRelay, transcriptPath, MAX_UTTERANCE_BYTES } from './talk.js';

const here = dirname(fileURLToPath(import.meta.url));
const run = promisify(execFile);

const DEFAULT_PORT = 8888;   // finnamon/config.py DASHBOARD_PORT is the same number; PORT overrides
const config = {
  port: Number(process.env.PORT || DEFAULT_PORT),
  host: process.env.HOST || '127.0.0.1',
  finnamon: (process.env.FINNAMON_BIN || 'finnamon').split(/\s+/),   // may carry args, e.g. "python3 -m finnamon.cli" in development
  repo: process.env.FINNAMON_REPO || join(here, '..'),                 // the CLI runs here ("python3 -m finnamon.cli" needs the checkout)
  home: process.env.FINNAMON_HOME || join(homedir(), '.finnamon'),
  claude: process.env.CLAUDE_BIN || 'claude',
};
// The household's Claude session runs in the installed assistant bundle (CLAUDE.md, skills, settings; `finnamon install`
// writes it, finnamon/assistant.py), never in the checkout: the checkout is developer-only, and a wheel has none.
config.assistant = process.env.FINNAMON_ASSISTANT || join(config.home, 'assistant');
const chartsDir = join(config.home, 'charts');
const specPath = join(chartsDir, 'current.json');
const MAX_NAME = 80;   // budget and property names (finnamon.properties.MAX_NAME); a chart's merchant search term is cut to it
export const KINDS = ['checking', 'savings', 'credit', 'loan', 'investment'];   // finnamon.imports.KINDS; a parity test in web/test holds them together
const BANK_NAME = /^[\p{L}\p{N}][\p{L}\p{N} .&'-]{0,79}$/u;   // Fetch by AI reads the institution back out of the account, so both routes have to accept the same names
const IMPORT_LIMIT = '8mb';   // a bank's CSV export is kilobytes; this is headroom, not a target
const IMPORTS_KEPT = 20;   // uploaded CSVs kept under ~/.finnamon/imports: enough to see what landed lately, not an archive of the bank

// The imports directory holds what was loaded, nothing more: dry-run copies go at once, and only the newest IMPORTS_KEPT stay.
export function pruneImports(dir, keep = IMPORTS_KEPT) {
  let names;
  try { names = readdirSync(dir).filter(f => f.endsWith('.csv')).sort(); } catch { return; }   // the names start with an ISO timestamp, so sorted is oldest first
  for (const f of names.filter(f => f.endsWith('.dry-run.csv'))) rmSync(join(dir, f), { force: true });
  const kept = names.filter(f => !f.endsWith('.dry-run.csv'));
  for (const f of kept.slice(0, Math.max(0, kept.length - keep))) rmSync(join(dir, f), { force: true });
}
const require = createRequire(import.meta.url);
const VENDOR = [
  ['vad', '@ricky0123/vad-web', ['bundle.min.js', 'vad.worklet.bundle.min.js', 'silero_vad_v5.onnx']],
  ['ort', 'onnxruntime-web/wasm', ['ort.wasm.min.js', 'ort-wasm-simd-threaded.mjs', 'ort-wasm-simd-threaded.wasm']],
];
const SHUTDOWN_GRACE_MS = 3_000;   // long enough for claude to flush its transcript, short enough that a restart feels immediate
const SESSION_STABLE_MS = 60_000;   // a Claude session that lived this long was not part of a crash loop

// ---- the finnamon CLI is the data layer -------------------------------------------------------------------------

async function exec(...args) {
  const [bin, ...pre] = config.finnamon;
  const { stdout } = await run(bin, [...pre, ...args], { cwd: config.repo, env: env(), maxBuffer: 8 << 20 });
  return stdout;
}
const cli = async (...args) => JSON.parse(await exec(...args));

// The session must look like a person's terminal, not a Claude tool call: the CLI's human-only gates read these.
function env() {
  const e = { ...process.env, TERM: 'xterm-256color', FINNAMON_HOME: config.home };
  delete e.CLAUDECODE; delete e.FINNAMON_FROM_CLAUDE; delete e.FINNAMON_TRIAGE;
  delete e.FINNAMON_WEB_TOKEN;   // the dashboard's key, if the server's own environment carries it: the household session must not inherit it
  return e;
}

// Only the page's own addresses. Everything else is a browser that was talked into reaching localhost.
// FINNAMON_WEB_HOSTS and ~/.finnamon/web-hosts (what `finnamon remote` writes) add names a proxy in front of the page uses
// (`tailscale serve`: mac.tailnet.ts.net), https too. The file is read on every check, so `finnamon remote` needs no restart.
const norm = (h) => String(h || '').toLowerCase().replace(/^((?:https?:\/\/)?(?:\[[^\]]+\]|[^:\/]+)):(80|443)$/, '$1');   // browsers omit a default port; so do we
export const HOSTS_FILE = 'web-hosts';   // finnamon/config.py WEB_HOSTS_FILE
const hostsFile = () => {
  try { return readFileSync(join(config.home, HOSTS_FILE), 'utf8'); }
  catch (e) {   // missing is remote access off; anything else would answer every phone 421 with no reason in the log
    if (e.code !== 'ENOENT' && throttled(`cannot read ${HOSTS_FILE}`)) console.warn(`${stamp()} cannot read ${HOSTS_FILE}: ${e.message}; only localhost is let in`);
    return '';
  }
};
export function loopback(port, extra = process.env.FINNAMON_WEB_HOSTS || '', file = hostsFile) {
  const hosts = () => new Set([`127.0.0.1:${port}`, `localhost:${port}`, `[::1]:${port}`, ...`${extra},${file()}`.split(/[\s,]+/).filter(Boolean)].map(norm));
  return { host: (h) => hosts().has(norm(h)), origin: (o) => [...hosts()].some(h => [`http://${h}`, `https://${h}`].includes(norm(o))) };
}

// What the page may load and talk to, and nothing else. No inline or eval'd script: a chart spec is built from bank strings, and
// script in this page can type into the intercom, which is a shell. The CDN scripts and stylesheet are the exact files index.html
// names (read from it, so the two cannot drift; each is pinned by SRI there), never a whole registry. Plaid Link is the one third
// party: its loader (unversioned, so no SRI) plus the chunks it fetches under /link/, its frame and its API hosts. Inline *styles*
// stay allowed: xterm.js and Link both inject <style> elements, and CSS cannot reach the socket. 'wasm-unsafe-eval' compiles
// WebAssembly, never a string of script: Talk's voice detector (onnxruntime) runs on it; media-src blob: plays its replies. The host is the request's own,
// checked by allowHost next; here only a plain host:port shape is spliced in (a forged Host must not write directives, and an
// IPv6 literal has no CSP grammar), and 'self' covers the socket otherwise. index.html is read on every request, not at start:
// `finnamon update` restarts nothing for a release that only touches web/public/, and a CDN bump there must not leave the
// page under a policy naming the old files.
const pinned = (page, re) => [...page.matchAll(re)].map(m => m[1]).filter(u => !u.includes(';')).join(' ');
export function csp(host) {
  const page = readFileSync(join(here, 'public', 'index.html'), 'utf8');
  const scripts = pinned(page, /<script[^>]*src="(https:\/\/cdn\.jsdelivr\.net\/[^"]+)"/g), styles = pinned(page, /<link[^>]*href="(https:\/\/cdn\.jsdelivr\.net\/[^"]+\.css)"/g);
  const ws = /^[\w.-]+(:\d+)?$/.test(host || '') ? ` ws://${host} wss://${host}` : '';
  return ["default-src 'self'", `script-src 'self' 'wasm-unsafe-eval' ${scripts} https://cdn.plaid.com/link/`,
    `style-src 'self' 'unsafe-inline' ${styles} https://fonts.googleapis.com`, "font-src 'self' https://fonts.gstatic.com",
    `connect-src 'self'${ws} https://*.plaid.com`, "img-src 'self' data:", "media-src 'self' blob:", "frame-src https://*.plaid.com", "object-src 'none'", "base-uri 'none'", "frame-ancestors 'none'", "form-action 'none'"].join('; ');
}

// One line a person can read instead of "inbound daemon": what needs attention, if anything.
export function health(status, inbound = status.inbound) {
  if (status.demo) return { level: 'ok', label: 'Demo household: made-up data' };   // `finnamon demo`: no daemon, no bot, nothing to warn about
  if (status.inbound && inbound && status.inbound !== inbound) return { level: 'warn', label: 'Telegram mode changed: restart finnamon web' };
  // A dead daemon comes first: it is the only thing that clears channel_deaf_since, so a stamp left behind by one
  // would otherwise name the wrong process forever. It now also outranks a sync warning below, which is the right way
  // round: nothing syncs while the daemon is down anyway.
  if (status.daemon_alive === false) return { level: 'down', label: 'Daemon stopped' };
  // The session in the corner runs in ~/.finnamon/assistant; without its files or claude's trust in the directory it has
  // no allow list and answers nothing useful, and nothing else on this page would say why.
  if (Array.isArray(status.assistant_problems) && status.assistant_problems.length) return { level: 'down', label: 'Assistant directory not ready: finnamon install' };
  // This pill is the one surface a person is looking at while the chat goes unanswered. It said "All good" through two
  // real outages, so a chat nobody is reading outranks a sync warning.
  if (status.channel_deaf_since) return { level: 'down', label: 'Telegram is not being read: finnamon update --no-pull' };
  const bad = (status.items || []).find(i => i.status && i.status !== 'good');
  if (bad) return { level: 'warn', label: bad.status === 'ITEM_LOGIN_REQUIRED' ? `${bad.institution} needs a re-login` : `${bad.institution} sync error` };
  if ((status.items || []).length && status.last_run && Date.now() - new Date(status.last_run.replace(' ', 'T')).getTime() > 24 * 3600e3) return { level: 'warn', label: 'Sync overdue' };
  return { level: 'ok', label: 'All good' };
}

// This month's change: the latest daily net-worth row against the last row before the 1st of its month
// (null until there is such a row, i.e. for the first month).
export function delta(history) {
  if (!Array.isArray(history) || !history.length) return null;
  const last = history[history.length - 1], monthStart = String(last.date || '').slice(0, 7) + '-01';
  const prev = history.findLast(r => String(r.date || '') < monthStart);
  if (!prev) return null;
  const a = Number(last.net_worth), b = Number(prev.net_worth);
  if (!Number.isFinite(a) || !Number.isFinite(b)) return null;
  return { amount: Math.round(a - b), pct: b ? Math.round((a - b) / Math.abs(b) * 1000) / 10 : null };
}

// ---- the dashboard's key ----------------------------------------------------------------------------------------

// One random secret in FINNAMON_HOME, 0600 like secrets.toml, the same file `finnamon open` and `finnamon web token` read
// and (on a fresh box, whichever runs first) create. Read on every check rather than cached: `finnamon web token --rotate`
// has to lock every old cookie out on the next request, without a restart.
export const TOKEN_FILE = 'web-token';
const KEY = /^[0-9a-f]{64}$/;   // what both minters write; anything else in the file is a mistake to report, not a key to serve
export function webToken(home = config.home) {
  const file = join(home, TOKEN_FILE);
  const stored = () => {   // unreadable is an error to refuse on, never a key to overwrite; so is a hand-edited value neither side could carry in a cookie or a URL
    let t;
    try { t = readFileSync(file, 'utf8').trim(); } catch (e) { if (e.code === 'ENOENT') return ''; throw e; }
    if (t && !KEY.test(t)) throw new Error(`${file} is not a hex key; remove it and run \`finnamon open\``);
    return t;
  };
  const have = stored();
  if (have) return have;
  // Missing, or empty (a write that died halfway): mint one. `wx` loses to a `finnamon open` that got there first, whose key is
  // then read back; an empty file is overwritten, since a blank key would lock every request out with no way back in.
  const fresh = randomBytes(32).toString('hex');
  mkdirSync(home, { recursive: true, mode: 0o700 }); chmodSync(home, 0o700);   // mkdir's mode does not apply to a directory that already exists
  try { writeFileSync(file, fresh, { mode: 0o600, flag: 'wx' }); return fresh; }
  catch (e) { if (e.code !== 'EEXIST') throw e; }
  return stored() || (writeFileSync(file, fresh, { mode: 0o600 }), chmodSync(file, 0o600), fresh);   // `mode` only applies on create: the empty file keeps whatever bits it had
}
// Constant time, and length-blind: both sides are hashed first, so a guess of the wrong length fails exactly like a wrong byte.
const digest = (s) => createHash('sha256').update(String(s)).digest();
export const same = (a, b) => !!a && !!b && timingSafeEqual(digest(a), digest(b));
export const COOKIE = `finnamon_token_${config.port}`;   // named by port: cookies ignore ports, and a second dashboard on this host (a dev one on another PORT) must not overwrite the household's
// every cookie of that name: another localhost service (cookies ignore ports) could set a decoy with a longer Path, which browsers send first
const cookies = (req) => (req.headers.cookie || '').split(';').map(s => s.trim()).filter(s => s.startsWith(COOKIE + '=')).map(s => s.slice(COOKIE.length + 1));
const bearer = (req) => /^Bearer\s+(\S+)$/i.exec(req.headers.authorization || '')?.[1];
const proto = (req) => String(req.headers['x-forwarded-proto'] || '').split(',')[0].trim();   // a proxy chain appends: "https, https"
// How a request proved itself: the page's cookie, or `Authorization: Bearer` (the CLI's `--to` uploads, curl); null otherwise.
export const authOf = (req, token) => cookies(req).some(c => same(c, token)) ? 'cookie' : same(bearer(req), token) ? 'bearer' : null;
// SameSite=Lax, not Strict, on purpose: Plaid's OAuth return is a cross-site top-level navigation to /?oauth_state_id=…, and
// Strict would drop the cookie on exactly that request. Lax still withholds it from cross-site POSTs, subresources and
// WebSocket handshakes; every write here is a POST or DELETE behind the Origin check, and a GET only shows the person their own page.
const COOKIE_DAYS = 30;   // one `finnamon open` per browser a month, not per browser session; a rotate ends it sooner
export const setCookie = (req, res, token) => res.setHeader('Set-Cookie', `${COOKIE}=${token}; Path=/; Max-Age=${COOKIE_DAYS * 86400}; HttpOnly; SameSite=Lax${proto(req) === 'https' ? '; Secure' : ''}`);
const stamp = () => new Date().toISOString();

// `finnamon remote` ends with a QR code: https://<tailnet name>/?pair=<code>, a one-time code that stands in for the key on a
// phone, so the key itself never sits in a photo or a camera app's history. finnamon/remote.py pair_url() keeps one pending
// code in this file (its sha256 and expiry; a newer one replaces it); the first right GET spends it, here, synchronously, so
// two requests cannot both. 'ok', or why not: 'invalid' (wrong, replaced, none), 'used', 'expired'.
export const PAIR_FILE = 'web-pair';   // finnamon/config.py WEB_PAIR_FILE
export function redeemPair(code, home = config.home, now = Date.now()) {
  const file = join(home, PAIR_FILE);
  let rec;
  try { rec = JSON.parse(readFileSync(file, 'utf8')); } catch { return 'invalid'; }
  if (!same(createHash('sha256').update(String(code)).digest('hex'), rec?.sha256)) return 'invalid';
  if (rec.used) return 'used';
  if (!(now < Number(rec.expires) * 1000)) return 'expired';
  try { writeFileSync(file, JSON.stringify({ ...rec, used: true })); }
  catch (e) { console.warn(`${stamp()} pairing: cannot mark the code used: ${e.message}`); return 'unwritable'; }   // unspendable means unusable: refuse
  return 'ok';
}
const PAIR_REFUSED = {
  invalid: 'That pairing code is not valid here (a newer `finnamon remote` replaces the last one).',
  used: 'That pairing code was already used: each one logs in one device.',
  expired: 'That pairing code has expired (they last 5 minutes).',
  unwritable: 'The dashboard cannot update its pairing file; see the server log.',
};
// The clean address after a key or a code: one leading slash, so a path like /x/..//evil.com cannot become a protocol-relative redirect.
const clean = (url) => '/' + url.pathname.replace(/^\/+/, '') + url.search;
const LOOPBACK_HOST = /^(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$/i;
// Behind tailscale serve the socket is loopback and the phone is in X-Forwarded-For: the last entry, the one the proxy added
// (a client can prepend its own). A connection from elsewhere is named by its address; the header would be its to write.
const LOOPBACK = new Set(['127.0.0.1', '::1', '::ffff:127.0.0.1']);
export const peer = (req) => {
  const addr = req.socket?.remoteAddress || '?';
  const via = LOOPBACK.has(addr) ? String(req.headers['x-forwarded-for'] || '').split(',').map(s => s.trim()).filter(Boolean).at(-1) : '';
  return via ? `${addr} (for ${via})` : addr;   // both: a local process can write the header too, so the socket's own address always stands
};

// The WebSocket handshake: the page's own Host, its own Origin or none (a non-browser client on this machine), and the key
// either way: without it a process on this machine, or a device on the tailnet, would get a shell as the user.
// One line a minute per peer and reason: a tab from before this release, or one on a stale key, retries every two seconds.
const LOG_EVERY_MS = 60_000;
const refusals = new Map();
export function throttled(key, now = Date.now(), log = (m) => console.warn(m), every = LOG_EVERY_MS) {
  const r = refusals.get(key) || { at: -Infinity, muted: 0 };
  if (now - r.at < every) { r.muted++; refusals.set(key, r); return false; }
  if (r.muted) log(`${stamp()} ${key}: and ${r.muted} more like it in the last minute`);
  refusals.set(key, { at: now, muted: 0 });
  return true;
}
// Tailscale Serve marks requests that came in over Funnel (the public internet); `finnamon remote` never turns Funnel on and
// refuses while it is, but a later `tailscale funnel 8888` would put the shell on the internet behind the key alone. Any local
// process can send the header too: all it buys is a refusal.
export const funnel = (req) => req.headers['tailscale-funnel-request'] !== undefined;
export const allowSocket = (lo, token = webToken) => ({ origin, req }) => {
  let tok;
  try { tok = token(); } catch (e) { if (throttled('intercom refused: cannot read the key')) console.warn(`${stamp()} intercom refused: cannot read the key: ${e.message}`); return false; }   // an unreadable key file must refuse, not throw out of the upgrade and take the process down
  const auth = authOf(req, tok);
  const ok = lo.host(req.headers.host || '') && !funnel(req) && (!origin || lo.origin(origin)) && !!(req.auth = auth);
  if (!ok && req.socket) {
    const line = `intercom refused from ${peer(req)} (origin ${origin || 'none'}, ${auth ? 'wrong host or origin' : 'no or wrong key'})`;
    if (throttled(line)) console.warn(`${stamp()} ${line}`);
  }
  if (ok) req.key = tok;   // what this socket was let in with: guardKey compares it against the file on every frame
  return ok;
};
// A socket is a shell let in under one key. Every frame re-reads the file: a rotate ends the session on its next keystroke
// even where the directory watcher in main() did not fire (fs.watch is best-effort on some filesystems); broadcast() gates output the same way.
// Returns whether the frame (or the output) may pass. A key that cannot be read right now is not a key that changed: the
// frame is dropped and the socket kept, so a transient read error never tells every page to re-key.
export function guardKey(ws, token = webToken) {
  let tok;
  try { tok = token(); } catch (e) { if (throttled('intercom paused: cannot read the key')) console.warn(`${stamp()} intercom paused: cannot read the key: ${e.message}`); return false; }
  if (same(ws.key, tok)) return true;
  ws.close(4001, 'key changed');
  return false;
}

// ---- the household's Claude Code session ------------------------------------------------------------------------

export function claudeArgs(inbound, session = null) {
  // Ask mode (Claude Code's default, named so a person's own defaultMode cannot change it): the bundle's allow list runs
  // unasked, its deny list is refused outright, and anything else (the web, other Bash, reading a file) shows a dialog in
  // the terminal. When the turn came from Telegram, the bundle's PermissionRequest hook puts the same request in the chat
  // with Allow / Deny (finnamon/approval.py); the first answer wins. Channel mode stays on dontAsk: the plugin would relay
  // every dialog to paired phones with no household check, and that mode is on its way out.
  const args = ['--permission-mode', inbound === 'channel' ? 'dontAsk' : 'default'];
  // The household's session outlives this process. --session-id mints it the first time and is refused ever after
  // ("already in use"); --resume picks it back up. In channel mode this is the session Telegram talks to as well, so a
  // fresh one would cost the chat its context too, not just the page its scrollback.
  if (session) args.push(...(session.created ? ['--resume', session.id] : ['--session-id', session.id]));
  // Channel mode makes this session the phone's reader with no dialog anyone sees, and it reads bank memos, text the other
  // party to the transaction wrote. So the web goes, the way it is gone from every `claude -p` (claude_runner.UNATTENDED_DISALLOWED).
  // Session mode keeps it: every fetch and search asks first, naming the site, at the dashboard and on the phone.
  if (inbound === 'channel') args.push('--channels', 'plugin:telegram@claude-plugins-official', '--disallowedTools', 'WebSearch', 'WebFetch');
  // Outside channel mode the daemon is the bot's one reader. The Telegram plugin, enabled anywhere in the person's Claude
  // Code config, would start in this session too and long-poll the same bot: 409s, and the messages go to it instead of
  // the relay. So the seal every other claude Finnamon spawns carries (claude_runner.run): no MCP server, and only the
  // assistant directory's own settings, so no plugin is enabled at all.
  if (inbound !== 'channel') args.splice(2, 0, '--setting-sources', 'project', '--strict-mcp-config');
  return args;
}

// The id lives in a file rather than the database: the page's own writes all go through the CLI, and this one is the
// server's own bookkeeping, read and written before any CLI call could run.
export const INTERCOM_FILE = 'intercom.json';
const STARTUP_OK_MS = 15_000;   // a session that dies sooner than this never got as far as a prompt
// The id reaches claude's argv, and `--resume` takes an optional value: a stored id beginning with a dash would not be
// consumed as that value and would be read as a flag of its own on the household's session. Only a uuid is an id.
const SESSION_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

// Which id to start with, and what to do when a start does not take. `created` is our record that the id exists;
// claude refuses --session-id on an id it already knows and --resume on one it does not, so a wrong guess is
// recoverable but must not be repeated: an exit inside STARTUP_OK_MS flips or replaces the id rather than looping.
export function intercomSession({ home = config.home, read = null, write = null, uuid = randomUUID } = {}) {
  const file = join(home, INTERCOM_FILE);
  const load = read || (() => { try { return JSON.parse(readFileSync(file, 'utf8')); } catch { return null; } });
  // 0700/0600 like the rest of ~/.finnamon: the id is enough for any local process to resume the household's whole
  // financial conversation, and on a fresh box the dashboard may be what creates the directory secrets.toml lands in.
  const save = write || ((s) => {
    try { mkdirSync(home, { recursive: true, mode: 0o700 }); writeFileSync(file, JSON.stringify(s), { mode: 0o600 }); }
    catch (e) { console.warn(`intercom session: ${e.message}`); }
  });
  const stored = load();
  let interruptedAt = Number(stored?.interruptedAt) || 0;   // see restartNotice
  // prev rides along: the stamp below rewrites the file, and prev is the only handle left on a replaced transcript
  let state = stored && SESSION_ID.test(stored.id || '') ? { id: stored.id, created: !!stored.created, ...(stored.prev ? { prev: stored.prev } : {}) } : { id: uuid(), created: false };
  let resumed = false;   // what the start now running actually asked for; state.created has already moved on by the time it exits
  return {
    get: () => ({ ...state }),
    // Shutdown stamps it when the session was mid-turn; the next start takes it, once. Any later save drops it too.
    interrupted: (at = Date.now()) => save({ ...state, interruptedAt: at }),
    takeInterrupted: () => { const at = interruptedAt; interruptedAt = 0; if (at) save(state); return at; },
    // Called once the spawn succeeded: an id we asked claude to mint is one we must resume next time.
    started: () => { resumed = state.created; if (!state.created) { state = { ...state, created: true }; save(state); } },
    // uptime separates "claude refused the id and quit" from "the household closed a long conversation".
    // A fast exit is not proof the id was refused: an expired login, a missing plugin or an OOM kill die fast too. So the
    // id being replaced is kept as `prev` — the transcript is still on disk, and without its id nothing could reach it again.
    noteExit: (uptimeMs) => {
      if (uptimeMs >= STARTUP_OK_MS) return state;
      state = resumed ? { id: uuid(), created: false, prev: state.id }   // --resume found nothing: the conversation is gone, start one
                      : { ...state, created: true };                     // --session-id was refused: claude has the id, so resume it
      save(state);
      return state;
    },
  };
}

// In channel mode the session is the bot's only reader and the plugin acks a batch as it takes it, so a restart mid-turn
// loses the message it was answering: not at Telegram, not in any log. The restarted session has the context but not the
// message, so the chat is told to resend. Only after a restart that interrupted a live turn, and only while that is recent.
export const RESTART_NOTICE_MS = 10 * 60_000;
const BUSY_MS = 60_000;   // output this recent at shutdown counts as mid-turn
// ponytail: output is the only sign of a turn the server sees, so a turn typed at the dashboard or a resize in the last
// minute counts too (a needless notice, never a missed one); telling a Telegram turn apart needs the plugin to say so.
// The banner a fresh start prints does not: only output after STARTUP_OK_MS of uptime does.
export const midTurn = (s, now = Date.now()) => !!s?.pty && now - s.lastOutputAt < BUSY_MS && s.lastOutputAt - s.startedAt > STARTUP_OK_MS;
export async function restartNotice({ inbound, intercom, send, now = Date.now() }) {
  const at = intercom.takeInterrupted();   // taken in every mode, so a stamp never outlives the start after it
  if (inbound !== 'channel' || !at || now - at > RESTART_NOTICE_MS) return false;
  await send();
  return true;
}

// A browser import runs in its own Claude session (a person logs into the bank in the window it opens), never in the
// household's: that one would carry the whole transcript into every later chat and, in channel mode, keep Telegram waiting.
// The CLI owns that session's argv (`finnamon import --browser` execs claude on the skill with its allow list), so the
// server spawns the CLI rather than keeping a second copy of the list here.
export function importCommand(bank) {
  const [bin, ...pre] = config.finnamon;
  return { cmd: bin, args: [...pre, 'import', '--browser', '--', bank] };
}

// once: a session that ends when the process exits (the import), instead of one kept alive for the household (the intercom)
// args may be a function: the household session recomputes its argv on every start, because the id it minted the first
// time has to be resumed on the next one.
export function createSession({ spawn = spawnPty, inbound = 'daemon', cmd = config.claude, args = claudeArgs(inbound), cwd = config.assistant, once = false, onOutput = () => {}, onState = () => {}, onStart = () => {}, onExit = () => {}, log = console } = {}) {
  const session = { pty: null, buffer: [], bufferBytes: 0, state: 'STARTING', lastOutputAt: 0, lastLine: '', exits: 0, startedAt: 0, stopped: false };
  const MAX_BUFFER = 256 * 1024;   // scrollback replayed to a page that (re)connects
  let stateTimer = null;

  function push(data) {
    session.buffer.push(data); session.bufferBytes += data.length;
    while (session.bufferBytes > MAX_BUFFER && session.buffer.length > 1) session.bufferBytes -= session.buffer.shift().length;
  }
  function setState(s) { if (s !== session.state) { session.state = s; onState(s); } }
  // WORKING while output flows; once it goes quiet, the last non-empty line tells whether Claude is waiting for input
  // ("❯" prompt), asking something, or has just finished a reply. Simplified from agent-007's detectState.
  function assess() {
    if (session.stopped || !session.pty) return setState('DOWN');
    const line = session.lastLine;
    // A dialog (a permission prompt, a question) ends on "Esc to cancel", and is one even while the title spinner keeps
    // output flowing: nothing may be typed into it, since a relayed or spoken line's Enter would pick its first option, Yes.
    if (/Esc\s*to\s*cancel/i.test(line)) return setState('QUESTION');
    if (Date.now() - session.lastOutputAt < 2500) return setState('WORKING');
    if (/\(y\/n\)|\[Y\/n\]|Do\s*you\s*want\s*to/i.test(line)) return setState('QUESTION');
    setState('WAITING');
  }

  function start() {
    if (session.stopped) return;
    let pty;
    try {
      pty = spawn(cmd, typeof args === 'function' ? args() : args, { name: 'xterm-256color', cols: 100, rows: 30, cwd, env: env() });
    } catch (err) {
      if (once) { log.error(`could not start ${cmd}: ${err.message}`); push(`[finnamon: could not start ${cmd}: ${err.message}]\r\n`); session.stopped = true; setState('DOWN'); return; }
      log.error(`could not start ${cmd}: ${err.message}; retrying in 30s`);
      setTimeout(start, 30_000); return;
    }
    session.pty = pty; session.startedAt = Date.now(); setState('WORKING'); onStart();
    pty.onData((data) => {
      push(data); session.lastOutputAt = Date.now();
      const text = data.replace(/\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\r/g, '');
      const lines = text.split('\n').map(s => s.trim()).filter(Boolean);
      if (lines.length) session.lastLine = lines[lines.length - 1];
      onOutput(data);
    });
    pty.onExit(({ exitCode }) => {
      const uptime = Date.now() - session.startedAt;
      session.pty = null;
      if (once) {   // live as well as buffered: the tab must say whether a person stopped it or it ended on its own (a crash, the login timeout)
        const line = `\r\n[finnamon: import session ${session.stopped ? 'stopped' : `ended (${exitCode})`}]\r\n`;
        session.stopped = true; clearInterval(stateTimer); push(line); onOutput(line);
      }
      setState('DOWN');
      if (session.stopped) return;   // a deliberate stop is not claude refusing its argv: `finnamon update` twice in a row must not cost the session its id
      onExit({ exitCode, uptimeMs: uptime });
      if (Date.now() - session.startedAt >= SESSION_STABLE_MS) session.exits = 0;   // a long-lived session's exit is not a crash loop
      const delay = Math.min(60_000, 3_000 * 2 ** Math.min(session.exits++, 4));   // 3s, 6s, 12s, 24s, 48s, then 60s
      log.warn(`claude exited (${exitCode}); restarting in ${delay / 1000}s`);
      push(`\r\n[finnamon: session ended (${exitCode}); restarting in ${delay / 1000}s]\r\n`);
      setTimeout(start, delay);
    });
  }

  start();
  if (!session.stopped) stateTimer = setInterval(assess, 1000);   // a once session that could not spawn is already over: no timer to leak
  return {
    session,
    write: (data) => session.pty?.write(data),
    resize: (cols, rows) => {   // node-pty throws on anything but positive integers; the page sends what fit() measured
      cols = Math.trunc(cols); rows = Math.trunc(rows);
      if (session.pty && cols >= 20 && cols <= 500 && rows >= 5 && rows <= 300) session.pty.resize(cols, rows);
    },
    replay: () => session.buffer.join(''),
    // Resolves once the pty is actually gone (or the grace period lapses). The caller has to be able to wait: two claude
    // processes on one session id corrupt it, and a restart starts the next one as soon as this process is gone.
    stop: (graceMs = 0) => {
      session.stopped = true; clearInterval(stateTimer);
      const pty = session.pty;
      if (!pty) return Promise.resolve();
      const done = new Promise((resolve) => {
        if (!graceMs) return resolve();
        const timer = setTimeout(() => { try { pty.kill('SIGKILL'); } catch {} resolve(); }, graceMs);
        pty.onExit(() => { clearTimeout(timer); resolve(); });
      });
      pty.kill();
      return done;
    },
  };
}

// ---- updating Finnamon from the page ----------------------------------------------------------------------------

// The header's "Update available": `finnamon update --check` (a fetch, and what the pull would bring), at most every
// UPDATE_CHECK_MS (a page load re-checks one older than UPDATE_LOAD_MS; Settings' "Check for updates" always does). The Update button runs `finnamon update` itself, detached: it restarts this process (and the daemon),
// so it cannot run inside a request, and it must outlive us (its own session, so launchd's kill of our process group misses it).
// The lock file names its pid and survives our restart; the page polls GET /api/update across it. Output goes to
// UPDATE_LOG, whose last line is the exit status, so the restarted server can still say how it went.
// ponytail: under systemd, restarting the web unit kills its whole cgroup, this child included: the restart itself still
// happens (systemd owns the job), only the run's last lines are lost; the page then judges by the version alone.
export const UPDATE_CHECK_MS = 30 * 60e3, UPDATE_LOAD_MS = 10 * 60e3;
export const UPDATE_LOCK = 'update.lock', UPDATE_LOG = 'update.log';
const UPDATE_STALE_MS = 30 * 60_000;   // a lock older than this is a pid reused, not an update still running
const EXITED = /^finnamon update exited (\d+)$/m;
const pidAlive = (pid) => { try { process.kill(pid, 0); return true; } catch (e) { return e.code === 'EPERM'; } };
export function updater({ call = cli, spawn = spawnChild, home = config.home, now = Date.now, alive = pidAlive, log = console } = {}) {
  const lockFile = join(home, UPDATE_LOCK), logFile = join(home, UPDATE_LOG);
  let checked = null, at = -Infinity, pending = null, seen = null;   // seen: the finished run a check has been made after
  const readLock = () => { try { return JSON.parse(readFileSync(lockFile, 'utf8')); } catch { return null; } };
  const running = (l) => !!l && now() - l.at < UPDATE_STALE_MS && alive(l.pid);
  const check = async (force = false, ttl = UPDATE_CHECK_MS) => {
    if (!force && now() - at < ttl) return checked;
    if (force && pending) await pending;   // one already in flight may have started before the run ended: look again after it
    return (pending ||= call('update', '--check')
      .then((r) => { checked = r; }, (e) => { checked = { error: (e.stderr || e.message || String(e)).trim() }; log.warn(`${stamp()} update check: ${checked.error}`); })
      .then(() => { at = now(); pending = null; return checked; }));
  };
  return {
    async status({ fresh = false, load = false } = {}) {
      const l = readLock(), busy = running(l);
      let last = null;
      if (l && !busy) {   // the last run, finished: its exit line, and the lines before it for a failure
        let out = '';
        try { out = readFileSync(logFile, 'utf8'); } catch {}
        const m = EXITED.exec(out);
        last = { from: l.from, at: l.at, exit: m ? Number(m[1]) : null, tail: out.replace(EXITED, '').trim().split('\n').slice(-8).join('\n') };
      }
      const ended = !!last && seen !== l.at;   // a run that ended since the last look changed what there is to pull
      if (ended) seen = l.at;
      return { ...(await check(fresh || ended, load ? UPDATE_LOAD_MS : UPDATE_CHECK_MS)), running: busy, last };
    },
    start(by = 'the page') {
      if (running(readLock())) throw new Error('an update is already running');
      const [bin, ...pre] = config.finnamon;
      mkdirSync(home, { recursive: true, mode: 0o700 });
      const fd = openSync(logFile, 'w', 0o600);
      let child;
      try {
        child = spawn('/bin/sh', ['-c', '"$@"; echo "finnamon update exited $?"', 'sh', bin, ...pre, 'update'],
          { cwd: config.repo, env: env(), detached: true, stdio: ['ignore', fd, fd] });
      } finally { closeSync(fd); }
      if (!child?.pid) throw new Error('could not start finnamon update');
      child.on?.('error', (e) => log.warn(`${stamp()} finnamon update: ${e.message}`));
      child.unref?.();
      writeFileSync(lockFile, JSON.stringify({ pid: child.pid, from: checked?.current ?? null, at: now() }), { mode: 0o600 });
      log.log(`${stamp()} finnamon update started by ${by} (pid ${child.pid}); its output: ${logFile}`);
      return { running: true, from: checked?.current ?? null };
    },
  };
}

// ---- routes -----------------------------------------------------------------------------------------------------

// The routes alone, with the CLI and the host check injectable, so tests can drive them without a PTY or a shell.
export function buildApp({ cli: call = cli, exec: sh = exec, inbound = 'daemon', allowHost = loopback(config.port).host, allowOrigin = loopback(config.port).origin, token = webToken, spec = specPath, home = config.home, startImport = null, talk = null, relay = null, update = null } = {}) {
  let linkOwner = null;   // whose bank that token is for (null: the CLI's default owner); it must survive the OAuth round trip with the token
  let linkToken = null;   // the last Plaid Link token minted: an OAuth return (HTTPS only) may land in a new tab, whose sessionStorage is empty
  const app = express();
  app.use((req, res, next) => { res.setHeader('Content-Security-Policy', csp(req.headers.host)); next(); });   // every response, refusals included
  app.use((req, res, next) => allowHost(req.headers.host || '') ? next() : res.status(421).json({ error: 'this page is served on localhost only' }));
  app.use((req, res, next) => funnel(req) ? res.status(403).json({ error: 'this page is never served on the public internet' }) : next());
  // a cross-site page can fire a bodiless POST at the token route without a preflight; browsers always send its Origin
  app.use((req, res, next) => !req.headers.origin || allowOrigin(req.headers.origin) ? next() : res.status(403).json({ error: 'wrong origin' }));
  // The key. `finnamon open` lands here with ?token=…: a right one becomes the cookie and leaves the address bar with a redirect
  // (what the browser keeps of a typed or pasted address is its own); a wrong one is a 401 like any other request without the cookie or a bearer.
  // A browser gets a sentence; a fetch(), curl or the CLI gets the {error} shape every other refusal here has.
  const refuse = (req, res, status, text, json = text) => req.accepts(['json', 'html']) === 'html' ? res.status(status).type('text/plain').send(text) : res.status(status).json({ error: json });
  app.use((req, res, next) => {
    let tok;
    try { tok = token(); } catch (e) { console.warn(`${stamp()} cannot read the key: ${e.message}`); return refuse(req, res, 503, 'The dashboard cannot read its key file; see the server log.'); }   // never err.message: it names the file's path
    const url = new URL(req.url, 'http://x');
    // A pairing code only on a tailnet name (this machine has `finnamon open`), and never over Funnel (refused above). Only the
    // outcome is logged, never the code.
    if (req.method === 'GET' && url.searchParams.has('pair')) {
      if (LOOPBACK_HOST.test(req.headers.host || '')) return refuse(req, res, 403, 'Pairing codes are for a phone on the tailnet address. On this machine, run `finnamon open`.');
      const got = redeemPair(url.searchParams.get('pair'), home);
      console.log(`${stamp()} pairing ${got === 'ok' ? 'let a device in' : `refused (${got})`}: ${peer(req)}`);
      if (got !== 'ok') return refuse(req, res, got === 'unwritable' ? 503 : 401, `${PAIR_REFUSED[got]} Run \`finnamon remote\` on the Finnamon box for a fresh QR code.`);
      url.searchParams.delete('pair'); setCookie(req, res, tok);
      return res.redirect(303, clean(url));
    }
    if (req.method === 'GET' && url.searchParams.has('token')) {
      if (!same(url.searchParams.get('token'), tok)) return refuse(req, res, 401, 'That key is not this dashboard\'s. Run `finnamon open` on the Finnamon box for a fresh address.');
      url.searchParams.delete('token'); setCookie(req, res, tok);
      return res.redirect(303, clean(url));
    }
    if ((req.auth = authOf(req, tok))) return next();
    refuse(req, res, 401, 'This page needs its key. Run `finnamon open` on the Finnamon box; for a phone, `finnamon open --host <its tailnet name>` prints the address to open there.',
           'unauthorized: open the page with `finnamon open`, or send the key from `finnamon web token` as Authorization: Bearer');
  });
  app.use(express.json());
  app.use(express.static(join(here, 'public')));
  // Talk's voice detector, served from here, never a CDN: the Silero VAD bundle, its worklet and model, and the
  // onnxruntime-web build it runs on (web/node_modules; a dashboard installed before Talk lacks them until `finnamon voice setup`).
  for (const [route, pkg, names] of VENDOR) {
    let dir;
    try { dir = dirname(require.resolve(pkg)); } catch { continue; }
    app.get(`/vendor/${route}/:name`, (req, res, next) => names.includes(req.params.name) ? res.sendFile(join(dir, req.params.name), { headers: { 'Cache-Control': 'private, max-age=86400' } }) : next());
  }
  // Talk to Finnamon (talk.js): behind the same Host, Origin and key checks as everything above.
  const talking = (res) => talk || (res.status(503).json({ error: 'this server has no session to talk to' }), null);
  app.get('/api/talk', (_req, res) => talking(res) && res.json(talk.setup()));
  app.post('/api/talk/utterance', express.raw({ type: 'audio/wav', limit: MAX_UTTERANCE_BYTES }), async (req, res) => {
    if (!talking(res)) return;
    const r = await talk.utterance(Buffer.isBuffer(req.body) ? req.body : null, { utterance: req.get('X-Utterance-Id'), echoOf: req.get('X-Echo-Of') || undefined });
    res.status(r.error ? 400 : 200).json(r);
  });
  app.post('/api/talk/text', (req, res) => {   // the browser's own recognition, when whisper.cpp is not set up
    if (!talking(res)) return;
    const r = talk.heard(req.body?.text, { utterance: req.body?.utterance, echoOf: req.body?.echoOf });
    res.status(r.error ? 400 : 200).json(r);
  });
  app.get('/api/talk/audio/:id/:index', async (req, res) => {
    if (!talking(res)) return;
    const r = await talk.audio(req.params.id, req.params.index);
    if (r.error) return res.status(r.status).json({ error: r.error });
    res.set({ 'Content-Type': 'audio/mp4', 'X-Pieces': String(r.count), 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' }).send(r.audio);
  });

  // Telegram through this session (inbound=session): the daemon's hand-off, one phone message in, its reply out. The bearer
  // key only (the daemon reads the same web-token file), never the page's cookie: a page has nothing to relay. A dashboard
  // that started in another mode refuses rather than answer the chat from a session started for something else.
  app.post('/api/telegram/turn', async (req, res) => {
    if (req.auth !== 'bearer') return res.status(403).json({ error: 'only the Finnamon daemon relays Telegram messages' });
    if (inbound !== 'session') return res.status(409).json({ error: `this dashboard started with inbound=${inbound}; restart it (finnamon update --no-pull)` });
    if (!relay) return res.status(503).json({ error: 'this server has no session to relay to' });
    const { from, text, note, timeout } = req.body || {};
    const r = await relay.ask({ from, text, note }, Number(timeout) * 1000);
    res.status(r.status || 200).json(r);
  });

  const fail = (res, e, status = 400) => res.status(status).json({ error: (e.stderr || e.message || String(e)).trim() });
  const name = (v) => { const n = String(v ?? '').trim(); return n.length <= MAX_NAME ? n : ''; };   // the CLI's own limit; '' fails validation below
  // `--` ends option parsing: a name like "--help" is a name, not an argparse option
  const write = (res, args) => call(...args).then(out => res.json({ ok: true, ...out })).catch(e => fail(res, e));

  app.get('/api/summary', async (_req, res) => {
    try {
      const [status, accounts, budgets, networth, alerts, resolved, properties, history] = await Promise.all([
        call('status'), call('account', 'list'), call('budget'), call('networth'), call('alerts', '--sent', '--open'), call('alerts', '--sent', '--resolved', '--limit', '20'),
        call('property', 'list'), call('networth', '--history', '--months', '12').catch(() => [])]);
      const owners = await call('owner', 'list').catch(() => []);   // the Link account picker asks whose bank it is when there are several
      res.json({ owners: Array.isArray(owners) ? owners : [], status, accounts, budgets, networth, alerts, resolved, properties, inbound, health: health(status, inbound), delta: delta(history), history: Array.isArray(history) ? history : [] });
    } catch (e) { fail(res, e, 500); }
  });
  app.get('/api/chart', async (_req, res) => {   // the board: a list of specs; a pre-0.7.5 file holds one object
    // Recomputed from each chart's query on every load (#76: read-only, small) without rewriting the file, which the watch below
    // would answer with another load. If the CLI fails, the file as it stands, each chart still showing when its values were computed.
    let board = await call('chart', '--refresh').catch(() => null);
    if (!Array.isArray(board)) try { board = existsSync(spec) ? JSON.parse(readFileSync(spec, 'utf8')) : []; } catch { board = []; }   // mid-write or garbled: an empty board, not a 500
    res.json((Array.isArray(board) ? board : [board]).filter(e => e && typeof e === 'object'));
  });
  app.post('/api/chart', async (req, res) => {   // the page's own chart buttons; the intercom does the same through Claude
    const { name: chart, arg, months } = req.body || {};
    if (!/^[a-z_]+$/.test(chart || '')) return res.status(400).json({ error: 'bad chart name' });
    const args = ['chart', '--spec'];
    if (months) args.push('--months', String(Number(months) || 12));
    args.push('--', chart);
    if (arg) args.push(String(arg).slice(0, MAX_NAME));
    try { await sh(...args); res.json({ ok: true }); } catch (e) { fail(res, e); }
  });
  app.delete('/api/chart/:id', async (req, res) => {   // a panel's ×, or a lit chip clicked again; the id is finnamon.charts.ID
    if (!/^[a-z0-9][a-z0-9_-]{0,63}$/.test(req.params.id)) return res.status(400).json({ error: 'bad chart id' });
    try { await sh('chart', '--remove', req.params.id); res.json({ ok: true }); } catch (e) { fail(res, e); }
  });
  app.post('/api/budget', (req, res) => {
    const n = name(req.body?.name), amount = Number(req.body?.amount);
    if (!n || !(amount > 0)) return res.status(400).json({ error: `a budget needs a name of 1 to ${MAX_NAME} characters and a positive monthly amount` });
    write(res, ['budget', 'set', '--', n, String(amount)]);
  });
  app.delete('/api/budget/:name', (req, res) => name(req.params.name) ? write(res, ['budget', 'remove', '--', name(req.params.name)]) : res.status(400).json({ error: 'bad name' }));
  app.post('/api/property', (req, res) => {
    const n = name(req.body?.name);
    if (!n) return res.status(400).json({ error: `a property needs a name of 1 to ${MAX_NAME} characters` });
    write(res, ['property', 'set', '--', n, String(req.body?.value ?? '')]);
  });
  // A manual account (a bank Plaid can't reach): one per account, several under the same institution.
  app.post('/api/account', (req, res) => {
    const n = name(req.body?.name), inst = name(req.body?.institution), kind = req.body?.type ?? KINDS[0];
    if (!n || !inst) return res.status(400).json({ error: `an account needs a name and a bank of 1 to ${MAX_NAME} characters` });
    if (!BANK_NAME.test(inst)) return res.status(400).json({ error: `a bank name is letters, digits, spaces and .&'- up to ${MAX_NAME} characters` });   // an account whose bank fails this could never use Fetch by AI, and there is no rename
    if (!KINDS.includes(kind)) return res.status(400).json({ error: `type is one of ${KINDS.join(', ')}` });
    write(res, ['account', 'add', '--institution', inst, '--type', kind, '--', n]);
  });
  // One manual account and its transactions (its bank too, if it was the last); the CLI refuses a Plaid one. --yes: the page asked.
  // The page sends the account_id (manual:<bank>:<name>, up to 88 characters), so what it confirmed is what goes; a name works too.
  const MANUAL_ID = /^manual:[a-z0-9-]{1,40}:[a-z0-9-]{1,40}$/;
  app.delete('/api/account/:ref', (req, res) => {
    const ref = MANUAL_ID.test(req.params.ref) ? req.params.ref : name(req.params.ref);
    ref ? write(res, ['account', 'remove', '--yes', '--', ref]) : res.status(400).json({ error: 'bad name' });
  });
  app.delete('/api/property/:name', (req, res) => name(req.params.name) ? write(res, ['property', 'remove', '--', name(req.params.name)]) : res.status(400).json({ error: 'bad name' }));
  // An alert's buttons: the commands the chat runs, as the person (env() drops the Claude markers), so the page and the chat agree.
  const ALERT_ACTIONS = { normal: (id) => ['normal', '--alert', id], dismiss: (id) => ['alerts', '--dismiss', id], undo: (id) => ['alerts', '--undo', id] };
  app.post('/api/alert/:id/:action', (req, res) => {
    const act = Object.hasOwn(ALERT_ACTIONS, req.params.action) && ALERT_ACTIONS[req.params.action];
    if (!act || !/^[1-9][0-9]{0,9}$/.test(req.params.id)) return res.status(400).json({ error: 'bad alert or action' });
    write(res, act(req.params.id));
  });
  // Reconnect (a re-login alert, or a bank in Accounts that needs one): an update-mode Plaid Hosted Link for that bank, which
  // the page opens in a new tab. The CLI reuses a session it opened minutes ago, so a double click is one session; the daemon
  // syncs the bank once the login is done. A Plaid item_id is letters and digits (the guard keeps it from reading as an option).
  // The page only (cookie and Origin, as for /api/update): a bearer key is not a person, and this path has no rate limit.
  app.post('/api/item/:id/reconnect', (req, res) => {
    if (req.auth !== 'cookie' || !req.headers.origin) return res.status(403).json({ error: 'only the dashboard page can reconnect a bank' });
    if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$/.test(req.params.id)) return res.status(400).json({ error: 'bad bank id' });
    write(res, ['link', '--web', '--update', req.params.id]);
  });

  // A bank's CSV export into a manual account (`finnamon account add`, for a bank Plaid can't reach): the body is the file,
  // kept under ~/.finnamon/imports as the record of what was loaded, and `finnamon import` does the parsing and the writes.
  // The page posts it; so does the browser-import session on another computer (finnamon import --browser <bank> --to <this page>).
  const flag = (v) => ['1', 'true', 'yes'].includes(String(v ?? '').toLowerCase());   // "flip=0" means no flip, not a flipped file
  app.post('/api/import', express.text({ type: () => true, limit: IMPORT_LIMIT }), async (req, res) => {
    const acct = name(req.query.account), body = typeof req.body === 'string' ? req.body : '';
    if (!acct || !body.trim()) return res.status(400).json({ error: 'an import needs ?account=<manual account> and the CSV as the request body' });
    const balance = req.query.balance === undefined || req.query.balance === '' ? null : Number(String(req.query.balance).replace(/[$,\s]/g, ''));
    if (balance !== null && !Number.isFinite(balance)) return res.status(400).json({ error: 'balance must be a number' });
    const dir = join(home, 'imports'), stamp = `${new Date().toISOString().replace(/[:.]/g, '-')}-${Math.random().toString(36).slice(2, 8)}`;   // two uploads in one millisecond stay two files
    const file = join(dir, `${stamp}${flag(req.query.dry_run) ? '.dry-run' : ''}.csv`);
    try { mkdirSync(dir, { recursive: true }); writeFileSync(file, body); } catch (e) { return fail(res, e, 500); }
    const args = ['import'];
    if (flag(req.query.flip)) args.push('--flip');
    if (flag(req.query.dry_run)) args.push('--dry-run');
    if (balance !== null) args.push('--balance', String(balance));
    try {
      const out = await call(...args, '--', acct, file);
      res.json({ ok: true, ...out });
      pruneImports(dir);   // the record is what landed; a dry run's copy and anything past the last few are not kept
    } catch (e) { rmSync(file, { force: true }); fail(res, e); }   // a file the CLI refused (wrong account, no date column) is not a record of anything
  });
  // Fetch the CSV from the bank's site: a separate Claude session on the /import-browser skill, shown in the panel's Import tab.
  app.post('/api/import/browser', (req, res) => {
    const bank = String(req.body?.bank ?? '').trim();   // the account's institution, as `finnamon account add` accepted it (up to MAX_NAME); it becomes a prompt, never a shell string
    if (!BANK_NAME.test(bank)) return res.status(400).json({ error: `a bank name is letters, digits, spaces and .&'- up to ${MAX_NAME} characters` });
    if (!startImport) return res.status(503).json({ error: 'this server has no session runner' });
    try { startImport(bank); res.json({ ok: true }); } catch (e) { res.status(409).json({ error: e.message }); }
  });

  // Plaid Link in the page: a plain token now, the public token back when the person has logged into the bank.
  // Plaid takes an HTTPS redirect only, so the page's own address is sent as the OAuth return just when it is served over
  // TLS (tailscale serve; README says to register it); on plain http, OAuth banks open in a popup instead.
  app.post('/api/link/token', async (req, res) => {
    const args = ['link', '--token'];
    if (proto(req) === 'https') args.push(`https://${norm(req.headers.host)}/`);   // Plaid matches the registered URI exactly
    const owner = req.body?.owner == null ? null : String(req.body.owner);
    try {
      if (owner !== null) {
        const members = await call('owner', 'list');
        if (!Array.isArray(members) || !members.some(m => m.owner === owner)) return res.status(400).json({ error: 'that is not a household member' });
        args.push('--owner', owner);
      }
      const out = await call(...args); linkToken = out.link_token; linkOwner = owner; res.json(out);
    } catch (e) { fail(res, e, 500); }
  });
  app.get('/api/link/token', (_req, res) => res.json({ link_token: linkToken, owner: linkOwner }));
  app.post('/api/link/finish', async (req, res) => {
    const pt = String(req.body?.public_token || '');
    if (!/^public-[a-z]+-[0-9a-f-]+$/.test(pt)) return res.status(400).json({ error: 'bad public token' });
    const owner = linkOwner;   // the CLI re-reads the owner here: finish would otherwise file the bank under the default member
    try { const out = await call('link', '--public-token', pt, ...(owner ? ['--owner', owner] : [])); linkToken = linkOwner = null; res.json({ ...out, owner }); } catch (e) { fail(res, e, 500); }
  });
  // Update Finnamon (the header's Update available). A POST only from the page itself: its cookie and its Origin, which a
  // browser always sends on a fetch POST; the bearer key (the CLI's `--to`, curl) is refused, so nothing but a person's tap runs it.
  const updating = (res) => update || (res.status(503).json({ error: 'this server cannot update Finnamon' }), null);
  app.get('/api/update', async (req, res) => { if (updating(res)) res.json(await update.status({ fresh: req.query.fresh === '1', load: req.query.load === '1' })); });
  // Settings' read-only rows: what `finnamon doctor` and the existing APIs already know. The demo has no update, so the version comes from the checkout.
  app.get('/api/settings', (_req, res) => {
    let version = null, listed = '';
    try { listed = readFileSync(join(home, HOSTS_FILE), 'utf8'); } catch {}
    try { version = readFileSync(join(config.repo, 'VERSION'), 'utf8').trim(); } catch {}
    const hosts = listed.split(/[\s,]+/).filter(Boolean);
    res.json({ version, remote: { on: hosts.length > 0, hosts }, voice: talk ? talk.setup() : null });
  });
  app.post('/api/update', (req, res) => {
    if (!updating(res)) return;
    if (req.auth !== 'cookie' || !req.headers.origin) return res.status(403).json({ error: 'only the dashboard page can start an update; in a terminal, run finnamon update' });
    try { res.status(202).json(update.start(peer(req))); } catch (e) { res.status(409).json({ error: e.message }); }
  });
  // body-parser's own errors (a CSV over the limit) come back as JSON like every other error here, not an HTML page
  app.use((err, _req, res, _next) => res.status(err.status || 500).json({ error: err.type === 'entity.too.large' ? `the file is over ${IMPORT_LIMIT}` : err.message }));   // eslint-disable-line no-unused-vars
  return app;
}

// The browser-import session's owner: one at a time. Its transcript is the bank's own pages (balances, account
// numbers), so once the session ends it is dropped from memory rather than replayed to the next page that connects.
export function importRunner({ make = createSession, broadcast }) {
  let current = null;
  return {
    get current() { return current; },
    start(bank) {
      if (current && current.session.state !== 'DOWN') throw new Error('an import session is still running; finish it or press Stop in the Import tab first');
      current?.stop();
      // `let`, not `const`: the callbacks fire inside make() (the first WORKING, a spawn that fails) before `own` is assigned.
      // An orphan is a session stop() gave up waiting for: whatever it says later would land in the next import's tab.
      let own;
      // cwd: the checkout, not the assistant directory: this spawns the finnamon CLI (in development "python3 -m finnamon.cli",
      // which imports from the checkout), and cmd_import moves itself into the assistant directory before exec'ing claude.
      own = make({ ...importCommand(bank), cwd: config.repo, once: true, onOutput: (data) => own?.orphan || broadcast({ type: 'output', session: 'import', data }),
        onState: (state) => { if (own?.orphan) return; broadcast({ type: 'state', session: 'import', state }); if (state === 'DOWN' && own && current === own) { own.stop(); current = null; } } });
      current = own;
      return own;
    },
    // The Import tab's Stop. The exit's own DOWN drops `current` (onState above); the check after is for a pty whose exit
    // never reports, which would otherwise leave every later Fetch by AI refused as "still running".
    stop(graceMs = SHUTDOWN_GRACE_MS) {
      const own = current;
      if (!own) return Promise.resolve();
      return own.stop(graceMs).then(() => { if (current === own) { own.orphan = true; current = null; broadcast({ type: 'state', session: 'import', state: 'DOWN' }); } });
    },
  };
}

// One page frame to its session. Only the import session can be stopped: the household's restarts by design, and a stop
// that fell through to it would cost the chat its reader. Typing into, or stopping, an import that has ended gets its DOWN back, not silence.
export function frame(msg, { term, imports, reply }) {
  const over = () => reply({ type: 'state', session: 'import', state: 'DOWN' });   // the page missed the end (a reconnect): tell it
  if (msg.type === 'stop') return msg.session !== 'import' ? undefined : imports.current ? imports.stop() : over();
  const t = msg.session === 'import' ? imports.current : term;
  if (!t) return msg.session === 'import' && msg.type === 'input' ? over() : undefined;
  if (msg.type === 'input' && typeof msg.data === 'string') t.write(msg.data);
  else if (msg.type === 'resize') t.resize(Number(msg.cols), Number(msg.rows));
}

// ---- server -----------------------------------------------------------------------------------------------------

export async function main() {
  mkdirSync(chartsDir, { recursive: true });
  let inbound = 'daemon';
  try { inbound = (await cli('channel', 'status')).inbound; } catch (e) { console.warn(`finnamon channel status: ${e.message}`); }

  const lo = loopback(config.port);
  const clients = new Set();
  // Output too goes only to sockets whose key still stands: a rotate must not leave a quiet listener reading the transcript.
  const broadcast = (msg) => {
    const s = JSON.stringify(msg);
    let read;
    try { read = webToken(); } catch (e) { read = e; }   // read once per chunk, not once per client; an error is handed to guardKey to log and drop
    for (const c of clients) if (c.readyState === 1 && guardKey(c, () => { if (read instanceof Error) throw read; return read; })) c.send(s);
  };
  const imports = importRunner({ broadcast });   // the browser-import session, while one runs
  try { webToken(); }   // made now, so `finnamon open` a moment later finds it (either side may create it; both read it back)
  catch (e) { console.warn(`${stamp()} cannot read the key: ${e.message}; every request answers 503 until the file is fixed`); }   // and still listen: a crash here would loop under KeepAlive and take the chat's reader with it
  let term = null;   // created once the port is ours: a port we cannot take must never spawn Claude
  let intercom = null;
  // Talk to Finnamon, and in session mode Telegram, type into the household session and read the answer off its own transcript.
  const sessionTranscript = () => (term?.session.pty && intercom ? transcriptPath(config.assistant, intercom.get().id) : null);
  const talk = createTalk({ write: (d) => term?.write(d), broadcast, idle: () => term?.session.state === 'WAITING', asking: () => term?.session.state === 'QUESTION',
                            transcript: sessionTranscript });
  const relay = createRelay({ write: (d) => term?.write(d), idle: () => term?.session.state === 'WAITING', asking: () => term?.session.state === 'QUESTION',
                              transcript: sessionTranscript });
  const app = buildApp({ inbound, allowHost: lo.host, allowOrigin: lo.origin, startImport: (bank) => imports.start(bank), talk, relay, update: process.env.FINNAMON_DEMO ? null : updater() });   // a demo has nothing to update: `finnamon update` would act on the household
  const server = createServer(app);
  // A browser sends the page's Origin; a non-browser client on this machine (wscat, a test) sends none. Any other origin
  // is a hostile tab, whatever Host it managed to resolve to. Either way the handshake carries the key or is refused.
  const wss = new WebSocketServer({ server, path: '/ws', maxPayload: 64 * 1024, verifyClient: allowSocket(lo) });

  wss.on('connection', (ws, req) => {
    console.log(`${stamp()} intercom connect from ${peer(req)} via ${req.auth}`);   // who reached the shell, and how; never the key itself
    clients.add(ws);
    ws.send(JSON.stringify({ type: 'hello', state: term ? term.session.state : 'STARTING', inbound, replay: term ? term.replay() : '',
                             import: imports.current ? { state: imports.current.session.state, replay: imports.current.replay() } : null }));
    ws.key = req.key;
    ws.on('message', (raw) => {   // a bad frame is dropped; it must never take the session down
      if (!guardKey(ws)) return;   // the key changed under this socket: it is closed, and the frame goes nowhere
      // a stop's promise has to be caught here too: an unhandled rejection exits the process, and with it the chat's reader
      try { frame(JSON.parse(raw), { term, imports, reply: (m) => ws.send(JSON.stringify(m)) })?.catch?.((e) => console.warn(`ws frame dropped: ${e.message}`)); }
      catch (e) { console.warn(`ws frame dropped: ${e.message}`); }
    });
    ws.on('close', () => clients.delete(ws));
  });

  // `finnamon web token --rotate` replaced the key: a socket already open is a shell that was let in under the old one, so
  // it goes at once (the page then re-checks and says it is locked); guardKey above catches the same on the next frame where
  // fs.watch did not fire. Every other request already reads the file as it comes.
  try {
    watch(config.home, (_ev, file) => { if (file === TOKEN_FILE) for (const c of clients) guardKey(c); });
  } catch (e) { console.warn(`key watch: ${e.message}`); }
  // `finnamon chart` changed the board: say so. fs.watch on a dir is coarse; the page re-fetches /api/chart.
  let chartTimer = null;
  try {
    watch(chartsDir, (_ev, file) => {
      if (file !== basename(specPath)) return;
      clearTimeout(chartTimer); chartTimer = setTimeout(() => broadcast({ type: 'chart' }), 150);
    });
  } catch (e) { console.warn(`chart watch: ${e.message}`); }

  server.on('error', (e) => {   // e.g. EADDRINUSE: say so and leave slowly, so a KeepAlive job doesn't spin
    console.error(`finnamon web: cannot listen on ${config.host}:${config.port}: ${e.message}${e.code === 'EADDRINUSE' ? `; something else holds port ${config.port} (8888 is also Jupyter's): set PORT=<free port> in the service environment, or stop the other program` : ''}; exiting in 30s`);
    setTimeout(() => process.exit(1), 30_000);
  });
  server.listen(config.port, config.host, () => {
    intercom = intercomSession();   // the household's assistant survives this process: `finnamon update` restarts us, not the conversation
    // The same gate the daemon has before its first `claude -p`: the assistant directory must hold the bundle and be trusted,
    // or the session would run with no allow list and no deny list, under the person's own settings alone. Under the first
    // `finnamon update` across the bundle release the daemon restarting beside us writes it within seconds; until then, wait.
    const startTerm = async () => {
      try {
        const st = await cli('status');
        if (Array.isArray(st.assistant_problems) && st.assistant_problems.length) {
          console.warn(`assistant directory not ready, retrying in 30s: ${st.assistant_problems.join('; ')}`);
          setTimeout(startTerm, 30_000); return;
        }
      } catch (e) { console.warn(`finnamon status: ${e.message}; retrying in 30s`); setTimeout(startTerm, 30_000); return; }
      console.log(`finnamon web on http://${config.host}:${config.port}, key in ${join(config.home, TOKEN_FILE)}: \`finnamon open\` opens it (${intercom.get().created ? 'resuming' : 'new'} session in ${config.assistant}: claude ${claudeArgs(inbound, intercom.get()).join(' ')})`);
      restartNotice({ inbound, intercom, send: () => cli('notify', '--restarted') })
        .catch((e) => console.warn(`restart notice not sent: ${e.message}`));
      term = createSession({
        inbound, args: () => claudeArgs(inbound, intercom.get()),
        onStart: () => intercom.started(), onExit: ({ uptimeMs }) => intercom.noteExit(uptimeMs),
        onOutput: (data) => broadcast({ type: 'output', data }), onState: (state) => broadcast({ type: 'state', state }),
      });
    };
    startTerm();
  });
  // Wait for the session to actually end before leaving: launchd starts the replacement the moment this process is gone,
  // and it resumes the same session id. An overlap there is the one thing that corrupts a conversation.
  const shutdown = async () => {
    server.close(); talk.stop();
    if (term && midTurn(term.session)) intercom?.interrupted();
    await Promise.race([Promise.all([term?.stop(SHUTDOWN_GRACE_MS), imports.current?.stop(SHUTDOWN_GRACE_MS)]),
                        new Promise((r) => setTimeout(r, SHUTDOWN_GRACE_MS + 500))]);
    process.exit(0);
  };
  process.on('SIGINT', shutdown); process.on('SIGTERM', shutdown);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) main();
