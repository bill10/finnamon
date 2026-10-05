// The PTY session without a PTY: a fake spawn drives restart, buffer and state logic.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createSession, claudeArgs, importCommand, intercomSession, INTERCOM_FILE, restartNotice, midTurn, RESTART_NOTICE_MS } from '../server.js';
import { mkdtempSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir, homedir } from 'node:os';
import { join } from 'node:path';

function fakeSpawn() {
  const procs = [];
  const spawn = (_cmd, _args, opts) => {
    spawn.env = opts?.env;
    const p = { data: null, exit: null, writes: [], size: null, killed: false,
      onData(cb) { this.data = cb; }, onExit(cb) { this.exit = cb; },
      write(d) { this.writes.push(d); }, resize(c, r) { this.size = [c, r]; }, kill() { this.killed = true; } };
    procs.push(p); return p;
  };
  return { spawn, procs };
}

test('claudeArgs: ask mode outside channel mode; the web is only taken away from the channel session', () => {
  assert.deepEqual(claudeArgs('daemon'), ['--permission-mode', 'default', '--setting-sources', 'project', '--strict-mcp-config'],
    'the daemon reads the bot: a Telegram plugin enabled in the person\'s config must not start here and poll it too');
  assert.deepEqual(claudeArgs('channel'), ['--permission-mode', 'dontAsk', '--channels', 'plugin:telegram@claude-plugins-official', '--disallowedTools', 'WebSearch', 'WebFetch'],
    'channel mode is left as it was: the plugin would relay every dialog to paired phones, so no dialogs and no web');
  assert.deepEqual(claudeArgs('session'), ['--permission-mode', 'default', '--setting-sources', 'project', '--strict-mcp-config'],
    'session mode: no dontAsk and no web ban; every fetch asks, on the dashboard and (a Telegram turn) on the phone');
  assert.ok(!claudeArgs('session').includes('dontAsk') && !claudeArgs('session').includes('--disallowedTools'));
  assert.ok(!claudeArgs('channel').includes('--strict-mcp-config') && !claudeArgs('channel').includes('--setting-sources'), 'channel mode is the one that loads the plugin');
});

test('the household session runs in the installed assistant bundle, never the checkout', () => {
  // Claude Code reads CLAUDE.md, .claude/settings.json and the skills from its cwd. The checkout is developer-only and a
  // wheel install has none of them; `finnamon install` writes them to ~/.finnamon/assistant (FINNAMON_ASSISTANT overrides).
  const seen = [];
  const { spawn } = fakeSpawn();
  process.env.FINNAMON_WEB_TOKEN = 'the-key';   // the server's own environment may carry the dashboard's key (a laptop's export); the household session must not
  createSession({ spawn: (cmd, args, opts) => { seen.push(opts); return spawn(); }, log: { warn() {}, error() {} } }).stop();
  delete process.env.FINNAMON_WEB_TOKEN;
  assert.ok(!('FINNAMON_WEB_TOKEN' in seen[0].env) && !('CLAUDECODE' in seen[0].env), 'the key never reaches the shell it guards');
  assert.equal(seen[0].cwd, process.env.FINNAMON_ASSISTANT || join(process.env.FINNAMON_HOME || join(homedir(), '.finnamon'), 'assistant'), 'the same rule server.js applies: an explicit FINNAMON_ASSISTANT, else FINNAMON_HOME/assistant');
  assert.ok(!seen[0].cwd.endsWith('web') && !seen[0].cwd.endsWith('finnamon'), 'not the checkout');
  const own = [];
  createSession({ spawn: (cmd, args, opts) => { own.push(opts.cwd); return spawn(); }, cwd: '/tmp/elsewhere', log: { warn() {}, error() {} } }).stop();
  assert.equal(own[0], '/tmp/elsewhere');
});

test('output is buffered for replay, capped, and forwarded; input and resize reach the pty', () => {
  const { spawn, procs } = fakeSpawn();
  const out = [];
  const s = createSession({ spawn, onOutput: (d) => out.push(d), log: { warn() {}, error() {} } });
  procs[0].data('hello ');
  procs[0].data('❯ ');
  assert.equal(s.replay(), 'hello ❯ ');
  assert.deepEqual(out, ['hello ', '❯ ']);
  procs[0].data('x'.repeat(300 * 1024));
  assert.ok(s.replay().length <= 300 * 1024 && !s.replay().startsWith('hello'), 'oldest chunks dropped past the cap');
  s.write('hi\r'); s.resize(120, 40);
  assert.deepEqual(procs[0].writes, ['hi\r']); assert.deepEqual(procs[0].size, [120, 40]);
  s.stop(); assert.ok(procs[0].killed);
});

test('an exited session restarts with backoff that escalates across a crash loop and resets after a stable run', (t) => {
  t.mock.timers.enable({ apis: ['setTimeout', 'setInterval', 'Date'] });
  const { spawn, procs } = fakeSpawn();
  const states = [];
  const s = createSession({ spawn, onState: (st) => states.push(st), log: { warn() {}, error() {} } });
  assert.equal(procs.length, 1);
  procs[0].exit({ exitCode: 1 });
  assert.equal(s.session.state, 'DOWN');
  assert.match(s.replay(), /session ended \(1\); restarting in 3s/);
  t.mock.timers.tick(3000); assert.equal(procs.length, 2, 'respawned after 3s');
  procs[1].exit({ exitCode: 1 });
  t.mock.timers.tick(3000); assert.equal(procs.length, 2, 'the second restart waits 6s, not 3s');
  t.mock.timers.tick(3000); assert.equal(procs.length, 3);
  t.mock.timers.tick(61_000);                                    // this one lives a minute: the loop is over
  procs[2].exit({ exitCode: 0 });
  t.mock.timers.tick(3000); assert.equal(procs.length, 4, 'a stable session restarts at 3s again');
  s.stop();
});

test('state: WORKING while output flows, then WAITING at the prompt or QUESTION when Claude asks; a failed spawn retries', (t) => {
  t.mock.timers.enable({ apis: ['setTimeout', 'setInterval', 'Date'] });
  const { spawn, procs } = fakeSpawn();
  const states = [];
  const s = createSession({ spawn, onState: (st) => states.push(st), log: { warn() {}, error() {} } });
  assert.equal(s.session.state, 'WORKING');
  procs[0].data('\x1b[32mdone.\x1b[0m\r\n❯ ');            // ANSI stripped, last non-empty line is the prompt
  assert.equal(s.session.lastLine, '❯');
  t.mock.timers.tick(3000);
  assert.equal(s.session.state, 'WAITING');
  procs[0].data('Do you want to proceed? (y/n)');
  t.mock.timers.tick(1000);
  assert.equal(s.session.state, 'WORKING', 'fresh output');
  t.mock.timers.tick(3000);
  assert.equal(s.session.state, 'QUESTION');
  // a permission dialog: a QUESTION at once, even while the title spinner keeps output coming (nothing may type into it)
  procs[0].data('\x1b[1C1.\x1b[1CYes\r\n3.\x1b[1CNo\r\n\r\nEsc\x1b[1Cto\x1b[1Ccancel\x1b[1C·\x1b[1CTab\x1b[1Cto\x1b[1Camend\r\n');
  procs[0].data('\x1b]0;◑ Touch RAN_IT\x07');
  procs[0].data('\x1b]0;◒ Touch RAN_IT\x1b\\');   // a title ended by ST, not BEL: still not a line of text
  assert.match(s.session.lastLine, /Esc\s*to\s*cancel/);
  t.mock.timers.tick(1000);
  assert.equal(s.session.state, 'QUESTION', 'the dialog footer wins over fresh output');
  procs[0].data('⏺ Bash(touch RAN_IT)\r\n');
  t.mock.timers.tick(1000);
  assert.equal(s.session.state, 'WORKING', 'answered: the dialog is gone');
  s.stop(); procs[0].exit({ exitCode: 0 });                 // a stopped session goes DOWN and is not restarted
  assert.equal(s.session.state, 'DOWN');
  t.mock.timers.tick(60_000);
  assert.equal(procs.length, 1);
  // spawn throws: no session, retry after 30s
  let calls = 0;
  const errors = [];
  const s2 = createSession({ spawn: () => { calls++; if (calls === 1) throw new Error('ENOENT'); return spawn(); }, log: { warn() {}, error: (m) => errors.push(m) } });
  assert.equal(s2.session.pty, null); assert.match(errors[0], /retrying in 30s/);
  t.mock.timers.tick(30_000);
  assert.equal(calls, 2); assert.ok(s2.session.pty, 'started on the retry');
  s2.stop();
});

test('resize ignores what node-pty would throw on', () => {
  const { spawn, procs } = fakeSpawn();
  const s = createSession({ spawn, log: { warn() {}, error() {} } });
  const sizes = []; procs[0].resize = (c, r) => sizes.push([c, r]);
  s.resize(100, 30); s.resize(Infinity, 30); s.resize(NaN, 30); s.resize(-1, 30); s.resize(1e9, 30); s.resize(80.7, 24.2); s.resize(10, 30);
  assert.deepEqual(sizes, [[100, 30], [80, 24]]);
  s.stop();
});

test('a once session (the browser import) ends when claude exits instead of restarting, and says so in the transcript', (t) => {
  t.mock.timers.enable({ apis: ['setTimeout', 'setInterval', 'Date'] });
  const { spawn, procs } = fakeSpawn();
  const states = [];
  const s = createSession({ spawn, cmd: 'finnamon', args: ['import', '--browser', '--', 'hsbc'], once: true, onState: (st) => states.push(st), log: { warn() {}, error() {} } });
  procs[0].data('Log in to HSBC in the window that just opened\n');
  procs[0].exit({ exitCode: 0 });
  t.mock.timers.tick(120_000);
  assert.equal(procs.length, 1, 'no respawn');
  assert.equal(s.session.state, 'DOWN'); assert.match(s.replay(), /import session ended \(0\)/);
  assert.deepEqual(states, ['WORKING', 'DOWN']);
});

test('a once session that cannot start says so once and stays DOWN, instead of retrying every 30s', (t) => {
  t.mock.timers.enable({ apis: ['setTimeout', 'setInterval', 'Date'] });
  let tries = 0;
  const spawn = () => { tries++; throw new Error('ENOENT'); };   // eslint-disable-line no-unused-vars
  const states = [], errors = [];
  const s = createSession({ spawn, args: ['/import-browser hsbc'], once: true, onState: (st) => states.push(st), log: { warn() {}, error: (m) => errors.push(m) } });
  t.mock.timers.tick(120_000);
  assert.equal(tries, 1, 'no retry');
  assert.equal(s.session.state, 'DOWN'); assert.deepEqual(states, ['DOWN']);
  assert.match(s.replay(), /could not start claude: ENOENT/); assert.equal(errors.length, 1);
  s.stop();
});

test('the import session is the CLI launcher, so the claude argv lives in one place; a failed spawn leaves no timer behind', (t) => {
  t.mock.timers.enable({ apis: ['setTimeout', 'setInterval', 'Date'] });
  assert.deepEqual(importCommand('HSBC').args.slice(-4), ['import', '--browser', '--', 'HSBC']);
  const states = [];
  const s = createSession({ spawn: () => { throw new Error('ENOENT'); }, cmd: 'finnamon', args: [], once: true, onState: (st) => states.push(st), log: { warn() {}, error() {} } });
  assert.equal(s.session.state, 'DOWN'); assert.match(s.replay(), /could not start finnamon: ENOENT/);
  t.mock.timers.tick(5000);
  assert.deepEqual(states, ['DOWN'], 'no assess() ticks after a spawn that never happened');
});

test('the household session id is minted once, then resumed, and a start that does not take is recovered', () => {
  let saved = null;
  const ids = ['11111111-1111-4111-8111-111111111111', '22222222-2222-4222-8222-222222222222'];
  const mk = (stored) => intercomSession({ home: '/nope', read: () => stored, write: (s) => { saved = s; }, uuid: () => ids.shift() });

  let s = mk(null);                                    // first boot ever
  assert.deepEqual(s.get(), { id: '11111111-1111-4111-8111-111111111111', created: false });
  assert.deepEqual(claudeArgs('daemon', s.get()).slice(5), ['--session-id', '11111111-1111-4111-8111-111111111111'], 'a new id is minted, not resumed');
  s.started();
  assert.deepEqual(saved, { id: '11111111-1111-4111-8111-111111111111', created: true }, 'once claude holds the id, the next boot has to resume it');

  s = mk({ id: '11111111-1111-4111-8111-111111111111', created: true });             // every boot after
  assert.deepEqual(claudeArgs('daemon', s.get()).slice(5), ['--resume', '11111111-1111-4111-8111-111111111111']);
  assert.deepEqual(claudeArgs('channel', s.get()).slice(2),
    ['--resume', '11111111-1111-4111-8111-111111111111', '--channels', 'plugin:telegram@claude-plugins-official', '--disallowedTools', 'WebSearch', 'WebFetch'],
    'channel mode resumes the same session: it is the one Telegram talks to, so it reads bank memos unattended and gets no web');
  assert.ok(!claudeArgs('daemon', s.get()).includes('--disallowedTools'), 'daemon mode is a person at the dashboard: the web stays for a property lookup');

  saved = null;
  s.started();                                         // every exit follows a start: onStart fires first in createSession
  s.noteExit(60_000);
  assert.equal(saved, null, 'a long-lived session that ends is not a failed start, and rewrites nothing');

  s.noteExit(500);                                     // --resume found nothing: the conversation is gone
  assert.deepEqual(saved, { id: '22222222-2222-4222-8222-222222222222', created: false, prev: '11111111-1111-4111-8111-111111111111' },
    'a fresh id rather than a crash loop, and the displaced one is kept: a fast exit is not proof the id was bad');

  s = mk({ id: '33333333-3333-4333-8333-333333333333', created: false });          // our record says new, but claude already knows the id
  s.started();                                         // the spawn takes, so created flips here; the recovery must not read it
  s.noteExit(500);
  assert.deepEqual(saved, { id: '33333333-3333-4333-8333-333333333333', created: true }, '"already in use" resumes the id rather than replacing the conversation');
});

test('a session whose args are a function recomputes its argv on every restart', (t) => {
  t.mock.timers.enable({ apis: ['setTimeout', 'setInterval', 'Date'] });
  const { spawn, procs } = fakeSpawn();
  const seen = [];
  const s = createSession({ spawn: (cmd, args, opts) => { seen.push(args); return spawn(cmd, args, opts); }, args: () => ['--argv', String(seen.length)], log: { warn() {}, error() {} } });
  assert.deepEqual(seen, [['--argv', '0']]);
  procs[0].exit({ exitCode: 1 });
  t.mock.timers.tick(3_000);
  assert.deepEqual(seen[1], ['--argv', '1'], 'the restart asked again instead of reusing the first argv');
  s.stop();
});

test('the id is a file in FINNAMON_HOME: a corrupt or unwritable one costs the memory, never the dashboard', () => {
  const dir = mkdtempSync(join(tmpdir(), 'finnamon-'));
  intercomSession({ home: dir, uuid: () => '11111111-1111-4111-8111-111111111111' }).started();
  assert.deepEqual(JSON.parse(readFileSync(join(dir, INTERCOM_FILE), 'utf8')), { id: '11111111-1111-4111-8111-111111111111', created: true });
  assert.deepEqual(intercomSession({ home: dir }).get(), { id: '11111111-1111-4111-8111-111111111111', created: true }, 'the next dashboard start resumes what this one minted');
  writeFileSync(join(dir, INTERCOM_FILE), 'not json');
  assert.equal(intercomSession({ home: dir, uuid: () => '22222222-2222-4222-8222-222222222222' }).get().id, '22222222-2222-4222-8222-222222222222');
  writeFileSync(join(dir, INTERCOM_FILE), JSON.stringify({ created: true }));
  assert.equal(intercomSession({ home: dir, uuid: () => '33333333-3333-4333-8333-333333333333' }).get().id, '33333333-3333-4333-8333-333333333333', 'a record with no id is no record');
  writeFileSync(join(dir, 'afile'), 'x');
  const warn = console.warn, warned = [];
  console.warn = (m) => warned.push(m);
  try { intercomSession({ home: join(dir, 'afile'), uuid: () => 'id-four' }).started(); } finally { console.warn = warn; }
  assert.equal(warned.length, 1, 'a home it cannot write to warns once instead of failing the boot');
  rmSync(dir, { recursive: true, force: true });
});

test('wired into the dashboard, a start that does not take is never handed the same argv again', (t) => {
  t.mock.timers.enable({ apis: ['setTimeout', 'setInterval', 'Date'] });
  const { spawn, procs } = fakeSpawn();
  const seen = [];
  const ids = ['11111111-1111-4111-8111-111111111111', '22222222-2222-4222-8222-222222222222'];
  const intercom = intercomSession({ home: '/nope', read: () => null, write: () => {}, uuid: () => ids.shift() });
  const s = createSession({
    spawn: (cmd, args, opts) => { seen.push(args); return spawn(cmd, args, opts); },
    args: () => claudeArgs('channel', intercom.get()), onStart: () => intercom.started(), onExit: ({ uptimeMs }) => intercom.noteExit(uptimeMs),
    log: { warn() {}, error() {} },
  });
  assert.deepEqual(seen[0].slice(2, 6), ['--session-id', '11111111-1111-4111-8111-111111111111', '--channels', 'plugin:telegram@claude-plugins-official']);
  assert.deepEqual(intercom.get(), { id: '11111111-1111-4111-8111-111111111111', created: true }, 'recorded at spawn: a dashboard killed outright still resumes next time');
  procs[0].exit({ exitCode: 1 });   // claude refused the id ("already in use") and quit before a prompt
  t.mock.timers.tick(3_000);
  assert.notDeepEqual(seen[1], seen[0], 'the restart never hands claude the argv it just refused');
  // The recovery has to read what the start ASKED for, not the state it left behind: onStart has already flipped
  // created, so branching on that mints a new id here and the household silently loses the conversation.
  assert.deepEqual(seen[1].slice(2, 4), ['--resume', '11111111-1111-4111-8111-111111111111'], 'an id claude already holds is resumed, not replaced');
  assert.equal(ids.length, 1, 'and no new id was minted for it');
  s.stop();
});

test('wired into the dashboard, a resume that finds nothing starts a new conversation rather than looping', (t) => {
  t.mock.timers.enable({ apis: ['setTimeout', 'setInterval', 'Date'] });
  const { spawn, procs } = fakeSpawn();
  const seen = [];
  const intercom = intercomSession({ home: '/nope', read: () => ({ id: '44444444-4444-4444-8444-444444444444', created: true }), write: () => {}, uuid: () => '55555555-5555-4555-8555-555555555555' });
  const s = createSession({
    spawn: (cmd, args, opts) => { seen.push(args); return spawn(cmd, args, opts); },
    args: () => claudeArgs('daemon', intercom.get()), onStart: () => intercom.started(), onExit: ({ uptimeMs }) => intercom.noteExit(uptimeMs),
    log: { warn() {}, error() {} },
  });
  assert.deepEqual(seen[0].slice(5), ['--resume', '44444444-4444-4444-8444-444444444444']);
  procs[0].exit({ exitCode: 1 });   // "No conversation found with session ID"
  t.mock.timers.tick(3_000);
  assert.deepEqual(seen[1].slice(5), ['--session-id', '55555555-5555-4555-8555-555555555555'], 'the conversation is gone, so one is started');
  s.stop();
});

test('a session id that is not a uuid never reaches claude argv, and a deliberate stop keeps the id', (t) => {
  t.mock.timers.enable({ apis: ['setTimeout', 'setInterval', 'Date'] });
  // --resume takes an optional value, so a stored id starting with a dash would be read as a flag of its own on the
  // household's session rather than consumed as the id.
  const hostile = intercomSession({ home: '/nope', read: () => ({ id: '--dangerously-skip-permissions', created: true }), write: () => {}, uuid: () => '66666666-6666-4666-8666-666666666666' });
  assert.deepEqual(claudeArgs('daemon', hostile.get()).slice(5), ['--session-id', '66666666-6666-4666-8666-666666666666'],
    'a non-uuid is discarded, not passed through');

  let saved = null;
  const { spawn, procs } = fakeSpawn();
  const intercom = intercomSession({ home: '/nope', read: () => ({ id: '77777777-7777-4777-8777-777777777777', created: true }), write: (s) => { saved = s; }, uuid: () => 'x' });
  const s = createSession({ spawn, args: () => claudeArgs('daemon', intercom.get()), onStart: () => intercom.started(), onExit: ({ uptimeMs }) => intercom.noteExit(uptimeMs), log: { warn() {}, error() {} } });
  s.stop();                                   // `finnamon update` twice in a row, or update then install
  procs[0].exit({ exitCode: 0 });
  assert.equal(saved, null, 'a stop we asked for is not claude refusing the id: the conversation must survive it');
});

test('stop() waits for the pty to go, so a restart cannot start a second claude on the same session', async (t) => {
  const { spawn, procs } = fakeSpawn();
  const s = createSession({ spawn, log: { warn() {}, error() {} } });
  let settled = false;
  const done = s.stop(3_000).then(() => { settled = true; });
  await new Promise((r) => setImmediate(r));
  assert.equal(settled, false, 'still waiting: the old claude has not exited yet');
  procs[0].exit({ exitCode: 0 });
  await done;
  assert.equal(settled, true);
});

test('stop() gives up after the grace period rather than hanging the shutdown forever', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const { spawn, procs } = fakeSpawn();
  const s = createSession({ spawn, log: { warn() {}, error() {} } });
  const done = s.stop(3_000);
  t.mock.timers.tick(3_000);          // claude never exits
  await done;
  assert.ok(procs[0].killed, 'killed, and the shutdown proceeds');
});

test('a restart that cut a channel-mode turn short tells the chat to resend, once; a cold start and daemon mode say nothing', async () => {
  const id = '11111111-1111-4111-8111-111111111111', now = 1_000_000_000;
  const dir = mkdtempSync(join(tmpdir(), 'finnamon-'));
  writeFileSync(join(dir, INTERCOM_FILE), JSON.stringify({ id, created: true }));
  const boot = async (inbound, at = now) => { let sent = 0; await restartNotice({ inbound, intercom: intercomSession({ home: dir }), send: async () => { sent++; }, now: at }); return sent; };

  intercomSession({ home: dir }).interrupted(now - 5_000);                           // shutdown mid-turn
  assert.deepEqual(JSON.parse(readFileSync(join(dir, INTERCOM_FILE), 'utf8')), { id, created: true, interruptedAt: now - 5_000 });
  assert.equal(await boot('channel'), 1, 'restart with a turn in flight: one notice');
  assert.equal(await boot('channel'), 0, 'the next start is not another restart that lost a message');
  assert.deepEqual(JSON.parse(readFileSync(join(dir, INTERCOM_FILE), 'utf8')), { id, created: true }, 'the stamp is gone, the session id is not');

  assert.equal(await boot('channel'), 0, 'cold start, nothing in flight: nothing');
  intercomSession({ home: dir }).interrupted(now);
  assert.equal(await boot('daemon'), 0, 'daemon mode: the daemon answers, and nothing was acked and dropped');
  assert.equal(await boot('channel'), 0, 'and the daemon-mode start used the stamp up');
  intercomSession({ home: dir }).interrupted(now - RESTART_NOTICE_MS - 1);
  assert.equal(await boot('channel'), 0, 'a box that was down for longer has no "last minute" to resend');
  intercomSession({ home: dir }).interrupted(now);
  await assert.rejects(restartNotice({ inbound: 'channel', intercom: intercomSession({ home: dir }), send: async () => { throw new Error('telegram down'); }, now }));
  assert.equal(await boot('channel'), 0, 'a send that failed is not retried on every later boot');
  const prev = '22222222-2222-4222-8222-222222222222';
  writeFileSync(join(dir, INTERCOM_FILE), JSON.stringify({ id, created: true, prev }));
  intercomSession({ home: dir }).interrupted(now);
  await boot('channel');
  assert.equal(JSON.parse(readFileSync(join(dir, INTERCOM_FILE), 'utf8')).prev, prev, 'stamping and taking keep the replaced transcript\'s id');
  rmSync(dir, { recursive: true, force: true });

  const s = (startedAt, lastOutputAt) => ({ pty: {}, startedAt, lastOutputAt });
  assert.ok(midTurn(s(now - 600_000, now - 2_000), now), 'output in the last minute of a settled session is a turn');
  assert.ok(!midTurn(s(now - 600_000, now - 120_000), now), 'an idle session is not');
  assert.ok(!midTurn(s(now - 5_000, now - 1_000), now), 'the banner of a session that just started is not');
  assert.ok(!midTurn({ ...s(now - 600_000, now - 2_000), pty: null }, now), 'nor is a session that is not running');
});
