// The dashboard's session on Codex (web/codex.js): argv and seal, the session id read off the rollout, the turn reader,
// dialog detection on a recorded 0.157 screen, and the relay round trip through the stub codex TUI in a real pty.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync, appendFileSync, mkdirSync, readFileSync, realpathSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { codexArgs, codexEnv, codexSessionId, rolloutPath, readCodexTurn, codexAsking, openCodexCall, codexTurnOver } from '../codex.js';
import { AGENTS, agentArgs, createSession, intercomSession, learnId, transcriptOf } from '../server.js';
const SESSION_STABLE_MS = 60_000;   // server.js: a session that lived this long was not part of a crash loop
import { createRelay, createTalk, typeInto, VOICE_TAG } from '../talk.js';

const fixtures = join(import.meta.dirname, '../../tests/fixtures/codex');
const stub = join(fixtures, 'bin/codex');
const ID = '01a10e2d-9a37-76c0-9e68-b563e3f90b15';
const lines = (f) => readFileSync(f, 'utf8').split('\n').filter(Boolean).map(l => JSON.parse(l));
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const until = async (fn, ms = 10_000) => { const end = Date.now() + ms; while (!fn()) { if (Date.now() > end) throw new Error('timed out'); await sleep(50); } };

test('codex argv: the seal pinned on the command line, a new session or `resume --no-daemon <id>`, never a marker on the process', () => {
  const args = codexArgs('/home/x/.finnamon/assistant');
  const fresh = args('session', { id: null, created: true }), resumed = args('session', { id: ID, created: true });
  const c = fresh.filter((_, i) => fresh[i - 1] === '-c');
  for (const v of ['approval_policy="on-request"', 'web_search="disabled"', 'features.apps=false', 'default_permissions="finnamon"',
    'permissions.finnamon.extends=":workspace"', 'projects={"/home/x/.finnamon/assistant"={trust_level="trusted"}}',
    'shell_environment_policy.set.FINNAMON_FROM_AGENT="1"', 'mcp_servers.finnamon.env.FINNAMON_FROM_AGENT="1"']) assert.ok(c.includes(v), v);
  assert.deepEqual(fresh.slice(-1), ['--no-daemon'], 'no shared background app-server: the session lives in this pty');
  assert.deepEqual(resumed.slice(-3), ['resume', '--no-daemon', ID]);
  assert.deepEqual(args('channel', { id: ID, created: false }).slice(-1), ['--no-daemon'], 'an id not yet started is not resumed');
  assert.equal(agentArgs('codex', 'session', { id: ID, created: true }).at(-1), ID);
  assert.ok(!('FINNAMON_FROM_AGENT' in AGENTS.codex.env), 'its hooks inherit the process env, and the permission hook stays silent for a marked one');
  assert.equal(AGENTS.codex.mintsId, false);
});

test('codex env mirrors finnamon/codex.py env(): the sealed CODEX_HOME and the empty HOME', () => {
  const home = mkdtempSync(join(tmpdir(), 'finnamon-codex-env-'));
  try {
    const py = JSON.parse(execFileSync('python3', ['-c', 'import json; from finnamon import codex; e = codex.env(); print(json.dumps({k: e[k] for k in ("CODEX_HOME", "HOME", "FINNAMON_HOME")}))'],
      { cwd: join(import.meta.dirname, '../..'), env: { ...process.env, FINNAMON_HOME: home } }).toString());
    assert.deepEqual(codexEnv(home), py);
  } finally { rmSync(home, { recursive: true, force: true }); }
});

test('the session id is the newest TUI rollout in the assistant directory since the start; exec runs and other folders are not it', () => {
  const codex = mkdtempSync(join(tmpdir(), 'finnamon-codex-ids-'));
  const d = new Date(), pad = (n) => String(n).padStart(2, '0');
  const day = join(codex, 'sessions', String(d.getFullYear()), pad(d.getMonth() + 1), pad(d.getDate()));   // the start's day, as Codex names it
  mkdirSync(day, { recursive: true });
  const put = (id, meta, nl = '\n') => { const f = join(day, `rollout-2026-10-05T18-00-00-${id}.jsonl`); writeFileSync(f, JSON.stringify({ type: 'session_meta', payload: { id, timestamp: new Date().toISOString(), ...meta } }) + nl); return f; };
  try {
    const since = Date.now();
    assert.equal(codexSessionId(codex, '/a', since), null, 'nothing yet');
    put('11111111-1111-7111-8111-111111111111', { cwd: '/a', source: 'exec' });
    put('22222222-2222-7222-8222-222222222222', { cwd: '/b', source: 'cli' });
    assert.equal(codexSessionId(codex, '/a', since), null, 'the daemon\'s exec run and another folder\'s session are not the intercom');
    put('66666666-6666-7666-8666-666666666666', { cwd: '/a', source: 'cli', timestamp: new Date(since - 86_400_000).toISOString() });
    assert.equal(codexSessionId(codex, '/a', since), null, 'an older thread resumed by hand here (written now, started yesterday) is not this session');
    put('77777777-7777-7777-8777-777777777777', { cwd: '/a', source: 'vscode' });
    assert.equal(codexSessionId(codex, '/a', since), null, 'only the TUI (source cli) is the intercom');
    const tui = put('33333333-3333-7333-8333-333333333333', { cwd: '/a', source: 'cli' }, '');
    assert.equal(codexSessionId(codex, '/a', since), null, 'a first line still being written names nothing yet');
    appendFileSync(tui, '\n');
    assert.equal(codexSessionId(codex, '/a', since), '33333333-3333-7333-8333-333333333333');
    put('88888888-8888-7888-8888-888888888888', { cwd: '/a', source: 'cli', timestamp: new Date(Date.now() + 5_000).toISOString() });
    assert.equal(codexSessionId(codex, '/a', since), '33333333-3333-7333-8333-333333333333', 'a thread started later (one the session spawned, or a codex by hand) is not it');
    assert.equal(codexSessionId(codex, '/a', since + 60_000), null, 'a rollout from before this start is an older session');
    assert.equal(rolloutPath(codex, '33333333-3333-7333-8333-333333333333'), tui);
    assert.equal(rolloutPath(codex, '44444444-4444-7444-8444-444444444444'), null);
    const late = put('44444444-4444-7444-8444-444444444444', { cwd: '/a', source: 'cli' });
    assert.equal(rolloutPath(codex, '44444444-4444-7444-8444-444444444444'), null, 'a miss is not looked up again on every poll');
    assert.equal(rolloutPath(codex, '44444444-4444-7444-8444-444444444444', Date.now() + 6_000), late, 'but is a few seconds later');
    assert.equal(rolloutPath(codex, null), null);
  } finally { rmSync(codex, { recursive: true, force: true }); }
});

test('readCodexTurn: the recorded 0.157 rollout is one turn, its reply last_agent_message; the next prompt or an abort ends one', () => {
  const entries = lines(join(fixtures, `rollout-2026-10-05T15-27-12-${ID}.jsonl`));
  const r = readCodexTurn(entries, 'This is a tooling test.');
  assert.deepEqual(r, { started: true, status: '', reply: '1. Succeeded.\n2. Failed.\n3. Failed.\n4. Succeeded.\n5. Failed.', done: true });
  assert.equal(readCodexTurn(entries, 'something never typed').started, false);
  const ev = (payload) => ({ type: 'event_msg', payload });
  const user = (text) => ev({ type: 'item_completed', item: { type: 'UserMessage', content: [{ type: 'text', text }] } });
  const agent = (text) => ev({ type: 'item_completed', item: { type: 'AgentMessage', content: [{ type: 'Text', text }] } });
  const call = ev({ type: 'item_completed', item: { type: 'McpToolCall', server: 'finnamon', tool: 'finnamon', arguments: { argv: ['finnamon', 'status'] } } });
  const mid = [user('[telegram · bill] hi'), agent('Looking.'), call, agent('All synced.')];
  assert.deepEqual(readCodexTurn(mid, '[telegram · bill] hi'), { started: true, status: '', reply: 'All synced.', done: false }, 'still running: what it said after the last call');
  assert.equal(readCodexTurn(mid, '[telegram · bill] hi', true).done, true, 'gone quiet after saying something');
  assert.deepEqual(readCodexTurn([...mid, user('next')], '[telegram · bill] hi'), { started: true, status: '', reply: 'All synced.', done: true }, 'a newer prompt cut it');
  assert.equal(readCodexTurn([user('[telegram · bill] hi'), ev({ type: 'turn_aborted' })], '[telegram · bill] hi').done, true);
  const started = { type: 'response_item', payload: { type: 'custom_tool_call', call_id: 'c' } };
  assert.deepEqual(readCodexTurn([user('[telegram · bill] hi'), agent('I will run a sync.'), started], '[telegram · bill] hi', true),
    { started: true, status: '', reply: '', done: false }, 'a call still waiting (on the phone) leaves only preamble: not the reply, not done');
});

test('a Codex dialog is read off the screen: the recorded approval prompt is a QUESTION until it is answered, a plain turn never is', () => {
  // the recorded 0.157 output through createSession's own strip and tail, chunk by chunk
  const states = (file) => {
    let feed;
    const fake = () => ({ onData: (cb) => { feed = cb; }, onExit() {}, write() {}, kill() {}, resize() {} });
    const t = createSession({ spawn: fake, asking: AGENTS.codex.asking, log: { warn() {}, error() {} } });
    const seen = [];
    for (const [, data] of lines(file)) {
      if (!String(data).startsWith('EXIT')) feed(data);
      const a = AGENTS.codex.asking(t.session);
      if (a !== seen.at(-1)) seen.push(a);
    }
    t.stop();
    return seen;
  };
  assert.deepEqual(states(join(fixtures, 'pty-approval.jsonl')), [false, true, false], 'asked, then answered (y) and working again');
  assert.ok(!codexAsking('• Done. Would you like to make a budget for dining next?   6:41 PM Tip: Use /copy'),
    'a finished reply that asks a question is no dialog: alone it would hold an idle session in QUESTION for good');
  assert.ok(codexAsking('› 1. Trust and continue  2. Quit'));
  assert.ok(codexAsking('Hooks need review … Press enter to confirm or esc to go back'));
  assert.ok(!codexAsking('Would you like to run the following command? … esc to cancel ✔ You approved codex to run ls • Working(2s • esc to interrupt)'));
  assert.ok(!codexAsking('• green tea please'));
});

test('an open Codex call: a call line with no output yet, from this session, in a turn still running', () => {
  const d = mkdtempSync(join(tmpdir(), 'finnamon-codex-open-'));
  const f = join(d, 'rollout.jsonl');
  const at = (s) => new Date(Date.UTC(2026, 9, 6, 1, 47, s)).toISOString();
  const ri = (s, payload) => JSON.stringify({ timestamp: at(s), type: 'response_item', payload });
  const ev = (s, payload) => JSON.stringify({ timestamp: at(s), type: 'event_msg', payload });
  const since = Date.parse(at(0));
  const write = (...l) => writeFileSync(f, l.join('\n') + '\n');
  try {
    assert.equal(openCodexCall(f, since), false, 'no file yet');
    // the hook's wait as 0.157 wrote it: exec, "Script running" after ~31 s, then a `wait` on the cell
    write(ev(1, { type: 'task_started' }), ri(2, { type: 'custom_tool_call', call_id: 'a', name: 'exec' }));
    assert.equal(openCodexCall(f, since), true, 'asked, and nobody has answered');
    assert.equal(openCodexCall(f, since + 60_000), false, 'from before this session started: a crash left it');
    write(ev(1, { type: 'task_started' }), ri(2, { type: 'custom_tool_call', call_id: 'a' }), ri(33, { type: 'custom_tool_call_output', call_id: 'a' }),
      ri(35, { type: 'function_call', call_id: 'b', name: 'wait' }));
    assert.equal(openCodexCall(f, since), true, 'waiting on it again');
    write(ev(1, { type: 'task_started' }), ri(2, { type: 'custom_tool_call', call_id: 'a' }), ev(9, { type: 'turn_aborted' }));
    assert.equal(openCodexCall(f, since), false, 'an interrupted turn has nothing waiting');
  } finally { rmSync(d, { recursive: true, force: true }); }
});

// Value: protects=the phone and Talk not locked out by a dialog's text left on screen after the turn ended (a quoted memo, a dead session's screen); fails_when=codexTurnOver misreads the turn events; why_new=codexAsking alone can match echoed text; seam=none
test('a dialog left on screen after the turn ended is no dialog; one inside a running turn is', () => {
  const d = mkdtempSync(join(tmpdir(), 'finnamon-codex-over-'));
  const f = join(d, 'rollout.jsonl');
  const ev = (type) => JSON.stringify({ type: 'event_msg', payload: { type } });
  try {
    assert.equal(codexTurnOver(''), false, 'no rollout yet: the trust and hook prompts at start still count');
    writeFileSync(f, [ev('task_started')].join('\n') + '\n');
    assert.equal(codexTurnOver(f), false, 'mid-turn: an approval is real');
    writeFileSync(f, [ev('task_started'), ev('task_complete')].join('\n') + '\n');
    assert.equal(codexTurnOver(f), true);
    writeFileSync(f, [ev('task_started'), ev('turn_aborted'), ev('task_started')].join('\n') + '\n');
    assert.equal(codexTurnOver(f), false, 'the next turn began');
  } finally { rmSync(d, { recursive: true, force: true }); }
  let feed;
  const t = createSession({ spawn: () => ({ onData: (cb) => { feed = cb; }, onExit() {}, write() {}, kill() {}, resize() {} }), asking: AGENTS.codex.asking, log: { warn() {}, error() {} } });
  feed('Would you like to run the following command? 1. Yes, proceed (y) Press enter to confirm or esc to cancel');
  assert.ok(AGENTS.codex.asking(t.session));
  t.stop();
});

test('typing into Codex: a bracketed paste, then Enter no sooner than 150 ms later (sooner, Codex reads it as a newline in the paste)', async () => {
  const out = [];
  const t0 = Date.now();
  typeInto((d) => out.push([Date.now() - t0, d]), 'hello codex');
  await until(() => out.length === 2, 2000);
  assert.equal(out[0][1], '\x1b[200~hello codex\x1b[201~');
  assert.equal(out[1][1], '\r');
  assert.ok(out[1][0] - out[0][0] >= 145, `Enter came ${out[1][0] - out[0][0]} ms after the paste`);
});

test('the session store: a Codex id is learnt once, off the rollout; switching CLI starts fresh and keeps the old id as prev', async () => {
  let saved = null;
  const s = intercomSession({ home: '/nope', read: () => saved, write: (v) => { saved = v; }, uuid: () => null, kind: 'codex' });
  s.started();
  assert.deepEqual(s.get(), { id: null, created: true, kind: 'codex' });
  assert.equal(s.learn('not-a-uuid').id, null);
  s.learn(ID);
  assert.deepEqual(saved, { id: ID, created: true, kind: 'codex' });
  s.learn('22222222-2222-7222-8222-222222222222');
  assert.equal(s.get().id, ID, 'learnt once');
  const back = intercomSession({ home: '/nope', read: () => saved, write: (v) => { saved = v; }, uuid: () => '55555555-5555-4555-8555-555555555555', kind: 'claude' });
  assert.deepEqual(back.get(), { id: '55555555-5555-4555-8555-555555555555', created: false, prev: ID }, 'a Codex id is never handed to claude --resume');
  const fromClaude = intercomSession({ home: '/nope', read: () => ({ id: '55555555-5555-4555-8555-555555555555', created: true }), write: () => {}, uuid: () => null, kind: 'codex' });
  assert.deepEqual(fromClaude.get(), { id: null, created: false, kind: 'codex', prev: '55555555-5555-4555-8555-555555555555' }, 'a Claude id is never handed to codex resume');
  fromClaude.started(); fromClaude.noteExit(100); fromClaude.started(); fromClaude.noteExit(100);
  assert.equal(fromClaude.get().prev, '55555555-5555-4555-8555-555555555555', 'fast exits with no id yet keep the replaced conversation\'s id');
  const old = intercomSession({ home: '/nope', read: () => ({ id: ID, created: true }), write: () => {}, uuid: () => null, kind: 'claude' });
  assert.deepEqual(old.get(), { id: ID, created: true }, 'a record from before the field is a Claude one, and stays as it was');
  // the poll: the id lands when the rollout does
  let found = null;
  const store = intercomSession({ home: '/nope', read: () => null, write: () => {}, uuid: () => null, kind: 'codex' });
  learnId({ learnId: () => found }, store, Date.now(), { every: 5, log: { log() {} } });
  await sleep(20);
  found = ID;
  await until(() => store.get().id === ID, 1000);
});

test('relay round trip through the stub codex TUI: the id is learnt, a phone line is typed and answered; an approval holds it until answered', async () => {
  const home = realpathSync(mkdtempSync(join(tmpdir(), 'finnamon-codex-pty-')));
  const dir = join(home, 'assistant'), codex = join(home, 'codex');
  mkdirSync(dir, { recursive: true }); mkdirSync(join(codex, 'home'), { recursive: true });
  const log = join(home, 'argv.jsonl');
  const intercom = intercomSession({ home, uuid: () => null, kind: 'codex' });
  const agent = { ...AGENTS.codex, transcriptPath: (_cwd, id) => rolloutPath(codex, id), learnId: (since) => codexSessionId(codex, dir, since) };
  let polls = 0;
  const term = createSession({ cmd: stub, cwd: dir, args: () => codexArgs(dir)('session', intercom.get()), asking: agent.asking,
    extraEnv: { ...codexEnv(home), CODEX_FAKE_LOG: log }, onStart: () => { intercom.started(); learnId({ learnId: (s) => (polls++, agent.learnId(s)) }, intercom, Date.now(), { every: 50, log: { log() {} } }); },
    onExit: ({ uptimeMs }) => intercom.noteExit(uptimeMs), log: { warn() {}, error() {} } });
  const relay = createRelay({ write: (d) => term.write(d), idle: () => term.session.state === 'WAITING', asking: () => term.session.state === 'QUESTION',
    transcript: () => transcriptOf(term, intercom, agent, dir), read: agent.readTurn, open: agent.open, pollMs: 50 });
  try {
    await until(() => term.session.pty && polls > 2);
    assert.equal(intercom.get().id, null, 'no rollout until the first line: nothing to learn yet');
    assert.equal(transcriptOf(term, intercom, agent, dir), '', 'running, with no file yet: the relay still types');
    const first = lines(log)[0];
    assert.equal(first.CODEX_HOME, codex);
    assert.equal(first.HOME, join(codex, 'home'));
    assert.equal(first.FINNAMON_FROM_AGENT, null, 'the process is unmarked: its permission hook must still reach the phone');
    assert.deepEqual(await relay.ask({ from: 'bill', text: 'how much on dining?' }, 20_000), { reply: 'stub reply: [telegram · bill] how much on dining?' },
      'the first line of a new session: its rollout appears only once typed');
    assert.ok(intercom.get().id, 'and the id is learnt off it');
    assert.deepEqual(JSON.parse(readFileSync(join(home, 'intercom.json'), 'utf8')), { id: intercom.get().id, created: true, kind: 'codex' });
    const pending = relay.ask({ from: 'bill', text: 'please approve a touch' }, 20_000);
    await until(() => term.session.state === 'QUESTION');
    await sleep(300);
    assert.equal(term.session.state, 'QUESTION', 'the dialog holds: nothing typed, no Enter to answer it');
    assert.ok(openCodexCall(rolloutPath(codex, intercom.get().id), term.session.startedAt), 'and the rollout has the call open');
    term.write('y');
    assert.deepEqual(await pending, { reply: 'stub reply: [telegram · bill] please approve a touch' });
    const rollout = lines(rolloutPath(codex, intercom.get().id));
    assert.equal(rollout.filter(e => e.payload?.item?.type === 'CommandExecution').length, 1);
  } finally { await term.stop(1000); rmSync(home, { recursive: true, force: true }); }
});

test('a restarted Codex session resumes the learnt id; a gone one starts fresh with the old id kept', async () => {
  const home = realpathSync(mkdtempSync(join(tmpdir(), 'finnamon-codex-resume-')));
  const dir = join(home, 'assistant'), codex = join(home, 'codex');
  mkdirSync(dir, { recursive: true });
  const log = join(home, 'argv.jsonl');
  writeFileSync(join(home, 'intercom.json'), JSON.stringify({ id: ID, created: true, kind: 'codex' }));
  const intercom = intercomSession({ home, uuid: () => null, kind: 'codex' });
  let exits = 0;
  const term = createSession({ cmd: stub, cwd: dir, args: () => codexArgs(dir)('session', intercom.get()), extraEnv: { ...codexEnv(home), CODEX_FAKE_LOG: log },
    onStart: () => intercom.started(), onExit: ({ uptimeMs }) => { exits++; intercom.noteExit(uptimeMs); }, log: { warn() {}, error() {} } });
  try {
    await until(() => exits === 1);
    assert.deepEqual(lines(log)[0].argv.slice(-3), ['resume', '--no-daemon', ID]);
    assert.deepEqual(intercom.get(), { id: null, created: false, kind: 'codex', prev: ID }, 'no rollout for it: the next start is a new session');
  } finally { await term.stop(500); rmSync(home, { recursive: true, force: true }); }
});

// Value: protects=Talk to Finnamon on a Codex session whose rollout does not exist yet (typed, then the reply found once the file appears) and Talk honouring the CLI's open-call reader; fails_when=createTalk treats '' as not running again, drops t.find, or ignores its open seam; why_new=only the relay was driven through '' and AGENTS.codex.open; seam=none
test('Talk on Codex: a first line with no rollout yet is typed and answered once the file appears; an open Codex call refuses a quiet session', async () => {
  const d = mkdtempSync(join(tmpdir(), 'finnamon-codex-talk-'));
  const f = join(d, 'rollout.jsonl');
  let path = '';
  const typed = [], sent = [];
  const talk = createTalk({ write: () => {}, type: (_w, line) => typed.push(line), transcript: () => path, broadcast: (m) => sent.push(m),
    read: AGENTS.codex.readTurn, open: AGENTS.codex.open, pollMs: 5 });
  const ev = (payload) => JSON.stringify({ timestamp: new Date().toISOString(), type: 'event_msg', payload });
  try {
    assert.deepEqual(talk.heard('how much on dining', { utterance: 'utterance-1' }), { ok: true, id: 'utterance-1', transcript: 'how much on dining' });
    assert.deepEqual(typed, [`${VOICE_TAG} how much on dining`], 'running, with no file yet: typed all the same');
    await sleep(30);
    path = f;   // Codex writes its rollout at the first line
    writeFileSync(f, [ev({ type: 'task_started' }), ev({ type: 'item_completed', item: { type: 'UserMessage', content: [{ type: 'text', text: typed[0] }] } }),
      ev({ type: 'task_complete', last_agent_message: 'About four hundred dollars.' })].join('\n') + '\n');
    await until(() => sent.some(m => 'reply' in m), 2000);
    assert.deepEqual(sent.find(m => 'reply' in m), { type: 'talk', id: 'utterance-1', reply: 'About four hundred dollars.', pieces: 1 });
    writeFileSync(f, [ev({ type: 'task_started' }), JSON.stringify({ timestamp: new Date().toISOString(), type: 'response_item', payload: { type: 'custom_tool_call', call_id: 'a' } })].join('\n') + '\n');
    const quiet = createTalk({ write: () => {}, type: () => assert.fail('typed while the permission hook asks the phone'), transcript: () => f, broadcast: () => {},
      idle: () => true, read: AGENTS.codex.readTurn, open: AGENTS.codex.open });
    assert.match(quiet.heard('and groceries', { utterance: 'utterance-2' }).error, /asking something/);
    assert.equal(createTalk({ write: () => {}, type: () => assert.fail('typed'), transcript: () => null, broadcast: () => {} }).heard('hi', { utterance: 'utterance-3' }).error,
      'The assistant is not running yet; try again in a moment.', 'null is still not running');
    assert.match(createTalk({ write: () => {}, type: () => assert.fail('typed into a running Codex turn: Enter would steer it'), transcript: () => f, broadcast: () => {},
      busy: () => true }).heard('hi', { utterance: 'utterance-4' }).error, /middle of a turn/);
  } finally { talk.stop(); rmSync(d, { recursive: true, force: true }); }
});

// Value: protects=the Claude household's Talk/relay transcript after main() moved to transcriptOf (the id's jsonl while running, null when stopped or idless); fails_when=transcriptOf returns '' or a Codex path for claude, or a path with no session running; why_new=transcriptOf was only exercised with the codex agent; seam=none
test('transcriptOf on Claude: the minted id\'s transcript while a session runs, null otherwise, never the Codex "" ', () => {
  const claude = { ...AGENTS.claude, transcriptPath: (cwd, id) => `${cwd}/${id}.jsonl` };
  const running = { session: { pty: {}, startedAt: 1 } };
  const store = (id) => ({ get: () => ({ id, created: true }), learn: () => assert.fail('claude mints its id: nothing to learn') });
  assert.equal(transcriptOf(running, store(ID), claude, '/a'), `/a/${ID}.jsonl`);
  assert.equal(transcriptOf({ session: { pty: null } }, store(ID), claude, '/a'), null, 'not running');
  assert.equal(transcriptOf(running, store(null), claude, '/a'), null, 'no id: not the running-without-a-file answer');
  assert.equal(transcriptOf(running, null, claude, '/a'), null);
});

// Value: protects=the harness check before every restart of the household session, not only the first; fails_when=createSession's exit handler respawns without asking gate, so a config.toml edited mid-session starts unchecked; why_new=main()'s first-start check was the only one; seam=none
test('a session that exits is restarted only once its gate passes again', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout', 'setInterval', 'Date'] });
  const exits = [], spawned = [];
  const fake = () => { const p = { onData() {}, onExit: (cb) => exits.push(cb), write() {}, kill() {}, resize() {} }; spawned.push(p); return p; };
  let ok = false, asked = 0;
  const s = createSession({ spawn: fake, gate: async () => { asked++; return ok; }, log: { warn() {}, error() {} } });
  assert.equal(spawned.length, 1, 'the first start has already passed it (main asks before creating the session)');
  t.mock.timers.tick(SESSION_STABLE_MS);
  exits[0]({ exitCode: 1 });
  t.mock.timers.tick(3_000);
  await Promise.resolve(); await Promise.resolve();
  assert.equal(asked, 1); assert.equal(spawned.length, 1, 'refused: no respawn');
  ok = true;
  t.mock.timers.tick(30_000);
  await Promise.resolve(); await Promise.resolve();
  assert.equal(asked, 2); assert.equal(spawned.length, 2, 'passed: respawned');
  s.stop();
});
