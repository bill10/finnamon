// Talk to Finnamon's server half (talk.js) with a fake pty, a fake whisper and say, and a transcript in a scratch
// directory: no microphone, no model, no `say`, and never the household's own session.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync, appendFileSync, mkdirSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createTalk, readTurn, stepPhrase, looksLikeEcho, overPlaybackEcho, transcriptPath, typeInto, whisperSetup, VOICE_TAG } from '../talk.js';
import { buildApp } from '../server.js';
import { plainForSpeech, chunkForSpeech } from '../public/speech.js';

const user = (content) => ({ type: 'user', message: { role: 'user', content } });
const said = (stop, ...content) => ({ type: 'assistant', message: { role: 'assistant', stop_reason: stop, content } });
const bash = (description) => ({ type: 'tool_use', name: 'Bash', input: { command: 'finnamon budget', description } });
const text = (t) => ({ type: 'text', text: t });

test('a turn is found by its line, reports each step, and ends at end_turn with what was said after the last tool', () => {
  const p = `${VOICE_TAG} how are the budgets`;
  const before = [user('earlier question'), said('end_turn', text('earlier answer'))];
  assert.deepEqual(readTurn(before, p), { started: false, status: '', reply: '', done: false }, 'nothing before the line counts');
  const working = [...before, user(p), said('tool_use', text('Let me check the budgets.')), said('tool_use', bash('Show this month\'s budgets'))];
  assert.deepEqual(readTurn(working, p), { started: true, status: 'Show this month\'s budgets', reply: '', done: false });
  const ended = [...working, user([{ type: 'tool_result', content: 'rows' }]), said('end_turn', text('Dining is **over** by $40.'))];
  assert.deepEqual(readTurn(ended, p), { started: true, status: 'Show this month\'s budgets', reply: 'Dining is **over** by $40.', done: true });
  const quiet = [...working, user([{ type: 'tool_result', content: 'rows' }]), said(null, text('All fine.'))];
  assert.equal(readTurn(quiet, p).done, false, 'no end_turn yet');
  assert.equal(readTurn(quiet, p, true).done, true, 'a quiet session after it said something is done');
  const cut = [...working, user('stop, something else')];
  assert.equal(readTurn(cut, p).done, true, 'the next prompt ends it');
  assert.equal(readTurn([...working, user('<channel source="telegram">hi</channel>')], p).done, false, 'a channel message is not the owner\'s next prompt');
});

test('progress phrases: only a clean Bash description, said whole; nothing derived from tools, commands or prose', () => {
  assert.equal(stepPhrase(bash('Checking dining against the budget')), 'Checking dining against the budget');
  assert.equal(stepPhrase(bash('Checking dining against the budget.')), 'Checking dining against the budget', 'a final period is dropped');
  for (const bad of ['', undefined, 'Checking', 'Reading /tmp/x.sql now', 'Running finnamon --json query', 'Querying budgets.db', 'SELECT * FROM tx',
    'Fetching https://x.com now', 'Checking `budgets` now', 'Comparing this month with the last three months of dining spending', 'Checking dinin\u2026', '(cd x) && ls', '3 rows found today'])
    assert.equal(stepPhrase(bash(bad)), '', `skipped: ${bad}`);
  assert.equal(stepPhrase({ type: 'tool_use', name: 'Bash', input: { command: 'finnamon budget' } }), '', 'no description, no line');
  assert.equal(stepPhrase({ type: 'tool_use', name: 'Read', input: { file_path: '/x' } }), '', 'a tool with no description is silent');
  assert.equal(stepPhrase({ type: 'tool_use', name: 'mcp__plugin_telegram_telegram__reply', input: {} }), '');
  assert.equal(stepPhrase(text('Looking at the budgets now.')), '');
  assert.equal(stepPhrase({ type: 'thinking', thinking: 'hmm' }), '');
});

test('speech text: markdown, tables and links become sentences, cut into pieces under the limit', () => {
  assert.equal(plainForSpeech('**Dining**: $412\n- Chipotle\n- [site](https://x.com)'), 'Dining: $412. Chipotle. site.');
  assert.equal(plainForSpeech('| a | b |\n|---|---|\n| 1 | 2 |'), 'a, b. 1, 2.');
  const pieces = chunkForSpeech('One. '.repeat(100), 50);
  assert.ok(pieces.length > 1 && pieces.every(p => p.length <= 50));
});

test('an echo is a run of six of the reply\'s words, or nothing but a shorter run; words of the owner\'s own are the owner', () => {
  const reply = 'You spent about four hundred dollars on dining this month.';
  assert.ok(looksLikeEcho('you spent about four hundred dollars on dining', reply));
  assert.ok(looksLikeEcho('Four hundred dollars.', reply), 'a fragment of the speaker');
  assert.ok(!looksLikeEcho('four hundred dollars, really?', reply));
  assert.ok(!looksLikeEcho('', reply));
  assert.ok(overPlaybackEcho('you', [reply]), 'a lone word over playback is noise');
  assert.ok(!overPlaybackEcho('Stop!', [reply]), 'unless it is a word that cuts in');
  assert.ok(overPlaybackEcho('checking the budgets', ['', 'Checking the budgets']), 'a progress line is a reference too');
  assert.ok(!overPlaybackEcho('what about groceries', [reply]));
});

test('the transcript lives where Claude Code keeps it: the cwd with every non-alphanumeric as a dash', () => {
  assert.equal(transcriptPath('/Users/x/.finnamon/assistant', 'abc', { CLAUDE_CONFIG_DIR: '/c' }), '/c/projects/-Users-x--finnamon-assistant/abc.jsonl');
});

test('typing is a bracketed paste in short pieces, then Enter; control characters cannot end the paste early', async () => {
  const out = [];
  typeInto((d) => out.push(d), `${'a'.repeat(250)}\x1b[201~\rrm`, { gap: 1, enter: 1 });
  await new Promise(r => setTimeout(r, 20));
  assert.equal(out.length, 3);
  assert.ok(out[0].startsWith('\x1b[200~') && out[0].endsWith('\x1b[201~') && out[0].length === 200 + 12);
  assert.ok(!out[1].slice(6, -6).includes('\x1b') && !out[1].includes('\r'), 'the escape and the CR were blanked');
  assert.equal(out[2], '\r');
});

test('whisper is found on PATH (or WHISPER_CPP_BIN) with a model in FINNAMON_HOME/whisper; missing either says how to fix it', () => {
  const d = mkdtempSync(join(tmpdir(), 'finnamon-talk-'));
  try {
    mkdirSync(join(d, 'bin')); writeFileSync(join(d, 'bin', 'whisper-cli'), '');
    assert.match(whisperSetup({ PATH: join(d, 'bin') }, d).missing, /finnamon voice setup/);
    mkdirSync(join(d, 'whisper')); writeFileSync(join(d, 'whisper', 'ggml-base.en.bin'), '');
    assert.deepEqual(whisperSetup({ PATH: join(d, 'bin') }, d), { bin: join(d, 'bin', 'whisper-cli'), model: join(d, 'whisper', 'ggml-base.en.bin') });
  } finally { rmSync(d, { recursive: true, force: true }); }
});

// A whole call through the routes: the utterance is transcribed once, typed once, its progress and reply broadcast, and the reply spoken.
test('a call: utterance → one typed line → progress → reply → audio; a retry is never a second line, and an echo is never sent', async () => {
  const d = mkdtempSync(join(tmpdir(), 'finnamon-talk-'));
  const file = join(d, 'session.jsonl');
  writeFileSync(file, JSON.stringify(user('old')) + '\n');
  mkdirSync(join(d, 'whisper')); writeFileSync(join(d, 'whisper', 'ggml-base.en.bin'), '');
  mkdirSync(join(d, 'bin')); writeFileSync(join(d, 'bin', 'whisper-cli'), ''); writeFileSync(join(d, 'bin', 'say'), '');
  const typed = [], sent = [], spoken = [];
  let heard = 0;
  const talk = createTalk({
    write: () => {}, type: (_w, line) => typed.push(line), transcript: () => file, broadcast: (m) => sent.push(m),
    env: { PATH: join(d, 'bin'), FINNAMON_HOME: d }, platform: 'darwin', pollMs: 5,
    stt: async () => { heard++; return 'how are the budgets'; }, tts: async (t) => { spoken.push(t); return Buffer.from('m4a'); },
  });
  const app = buildApp({ token: () => 'k'.repeat(64), cli: async () => ({}), allowHost: () => true, talk });
  const srv = app.listen(0, '127.0.0.1');
  await new Promise(r => srv.once('listening', r));
  const base = `http://127.0.0.1:${srv.address().port}`, cookie = { cookie: `finnamon_token_8888=${'k'.repeat(64)}` };
  const wav = Buffer.alloc(3244);
  const say = (id, extra = {}) => fetch(`${base}/api/talk/utterance`, { method: 'POST', headers: { ...cookie, 'content-type': 'audio/wav', 'x-utterance-id': id, ...extra }, body: wav }).then(r => r.json());
  const wait = (ok) => new Promise((res) => { const t = setInterval(() => { if (ok()) { clearInterval(t); res(); } }, 5); });
  try {
    assert.deepEqual(await fetch(`${base}/api/talk`, { headers: cookie }).then(r => r.json()), { stt: 'whisper', tts: 'say' });
    assert.equal((await fetch(`${base}/api/talk`)).status, 401, 'behind the dashboard key like everything else');
    const r = await say('utterance-1');
    assert.deepEqual(r, { ok: true, id: 'utterance-1', transcript: 'how are the budgets' });
    assert.deepEqual(await say('utterance-1'), { ...r, duplicate: true }, 'a retry is the line already typed');
    assert.deepEqual(typed, [`${VOICE_TAG} how are the budgets`]);
    assert.equal(heard, 1);

    appendFileSync(file, [user(typed[0]), said('tool_use', bash('Show the budgets'))].map(e => JSON.stringify(e)).join('\n') + '\n');
    await wait(() => sent.length);
    assert.deepEqual(sent[0], { type: 'talk', id: 'utterance-1', status: 'Show the budgets' });
    assert.equal((await fetch(`${base}/api/talk/audio/utterance-1/status`, { headers: cookie })).status, 200);
    assert.equal(spoken.at(-1), 'Show the budgets');

    appendFileSync(file, JSON.stringify(said('end_turn', text('All **on track**: you spent about four hundred dollars on dining this month.'))) + '\n');
    await wait(() => sent.some(m => 'reply' in m));
    const reply = sent.find(m => 'reply' in m);
    assert.deepEqual(reply, { type: 'talk', id: 'utterance-1', reply: 'All on track: you spent about four hundred dollars on dining this month.', pieces: 1 });
    const a = await fetch(`${base}/api/talk/audio/utterance-1/0`, { headers: cookie });
    assert.equal(a.headers.get('content-type'), 'audio/mp4'); assert.equal(a.headers.get('x-pieces'), '1');
    assert.equal((await fetch(`${base}/api/talk/audio/utterance-1/1`, { headers: cookie })).status, 404);
    assert.equal((await fetch(`${base}/api/talk/audio/nope-nope-nope/0`, { headers: cookie })).status, 404);

    // The mic caught the reply while it played: not sent.
    const n = typed.length;
    const text2 = { utterance: 'utterance-3', text: 'how are the budgets now' };
    assert.equal(talk.heard('you spent about four hundred dollars on dining', { utterance: 'utterance-4', echoOf: 'utterance-1' }).echo, true);
    assert.equal(talk.heard('show the budgets', { utterance: 'utterance-5', echoOf: 'utterance-1' }).echo, true, 'its progress line too');
    assert.equal(talk.heard(text2.text, { utterance: text2.utterance }).ok, true);
    assert.equal(typed.length, n + 1, 'the echo typed nothing; the next real line did');
    assert.equal((await fetch(`${base}/api/talk/text`, { method: 'POST', headers: { ...cookie, 'content-type': 'application/json' }, body: JSON.stringify({ utterance: 'x', text: 'hi' }) })).status, 400, 'a bad id');
  } finally { talk.stop(); srv.close(); rmSync(d, { recursive: true, force: true }); }
});

test('a dialog on the screen is never typed into: a spoken line\'s Enter would answer it Yes', () => {
  let asking = true;
  const talk = createTalk({ write: () => {}, type: () => assert.fail('typed into a dialog'), transcript: () => '/nonexistent', broadcast: () => {}, asking: () => asking });
  try {
    assert.match(talk.heard('allow it', { utterance: 'utterance-10' }).error, /asking something/);
    asking = false;
    assert.throws(() => talk.heard('allow it', { utterance: 'utterance-11' }), /typed into a dialog/, 'once it is gone, the line goes in');
  } finally { talk.stop(); }
});

test('without a session there is nothing to type into, and the routes say so', async () => {
  const talk = createTalk({ write: () => {}, type: () => assert.fail('typed'), transcript: () => null, broadcast: () => {} });
  assert.match(talk.heard('hello', { utterance: 'utterance-9' }).error, /not running/);
  const app = buildApp({ token: () => 'k'.repeat(64), cli: async () => ({}), allowHost: () => true });
  const srv = app.listen(0, '127.0.0.1');
  await new Promise(r => srv.once('listening', r));
  try {
    assert.equal((await fetch(`http://127.0.0.1:${srv.address().port}/api/talk`, { headers: { cookie: `finnamon_token_8888=${'k'.repeat(64)}` } })).status, 503);
  } finally { srv.close(); }
});

test('the page: the first progress line waits PROGRESS_QUIET_MS, the same line is never said twice, and the bar names the moment', async () => {
  const { progressWait, talkState, encodeWav, PROGRESS_QUIET_MS } = await import('../public/talk.js');
  assert.equal(progressWait('Checking budgets', { since: 1000, said: '' }, 1000), PROGRESS_QUIET_MS);
  assert.equal(progressWait('Checking budgets', { since: 1000, said: '' }, 5000), 0);
  assert.equal(progressWait('Checking budgets', { since: 1000, said: 'Checking budgets' }, 5000), null);
  assert.equal(progressWait('', { since: 0, said: '' }, 5000), null);
  assert.equal(talkState({ on: true, connected: true, playing: 'x', muted: true }), 'speaking');
  assert.equal(talkState({ on: true, connected: true, awaiting: 1 }), 'thinking');
  assert.equal(talkState({ on: true, connected: true }), 'listening');
  assert.equal(talkState({ on: true, connected: false }), 'disconnected');
  const wav = Buffer.from(encodeWav(new Float32Array([0, 1, -1])));
  assert.equal(wav.toString('ascii', 0, 4), 'RIFF'); assert.equal(wav.readUInt32LE(24), 16000); assert.equal(wav.length, 44 + 6);
  assert.deepEqual([wav.readInt16LE(44), wav.readInt16LE(46), wav.readInt16LE(48)], [0, 32767, -32768]);
});

test('the page: the note line says where the audio goes in a call, a notice wins, and nothing shows outside a call', async () => {
  const { noteText } = await import('../public/talk.js');
  assert.match(noteText({ consent: true, note: 'x' }), /whisper\.cpp is not set up/);
  assert.equal(noteText({ note: 'Mic blocked', inCall: true, mode: 'browser' }), 'Mic blocked');
  assert.equal(noteText({ note: '', inCall: false }), '');
  assert.match(noteText({ note: '', inCall: true, mode: 'browser', tts: 'say' }), /^Your browser recognises.*that computer\.$/);
});

test('the page: Mute silences the mic and keeps it open (iOS would move the voice), and resume asks again only for an ended one', async () => {
  const { pauseStream, resumeStream } = await import('../public/talk.js');
  const track = { enabled: true, readyState: 'live', stopped: false, stop() { this.stopped = true; this.readyState = 'ended'; } };
  const stream = { getTracks: () => [track] };
  let asked = 0;
  const ask = async () => { asked++; return 'new stream'; };
  pauseStream(stream);
  assert.deepEqual([track.enabled, track.stopped], [false, false], 'muted, not stopped');
  assert.equal(await resumeStream(stream, ask), stream, 'the same stream comes back');
  assert.deepEqual([track.enabled, asked], [true, 0]);
  track.stop();
  assert.equal(await resumeStream(stream, ask), 'new stream', 'a hidden tab stopped it: asked for again');
  assert.equal(asked, 1);
});

test('the page: Speaker or Earpiece is remembered in this browser, Speaker by default, and offered only where an earpiece is listed', async () => {
  const { outputPref, saveOutputPref, outputIds } = await import('../public/talk.js');
  const store = new Map();
  const had = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, String(v)) } });
  try {
    assert.equal(outputPref(), 'speaker');
    saveOutputPref('earpiece'); assert.equal(outputPref(), 'earpiece');
    saveOutputPref('speaker'); assert.equal(outputPref(), 'speaker');
    store.set('finnamon-talk-output', 'junk'); assert.equal(outputPref(), 'speaker');
  } finally { if (had) Object.defineProperty(globalThis, 'localStorage', had); else delete globalThis.localStorage; }
  const out = (deviceId, label) => ({ kind: 'audiooutput', deviceId, label });
  assert.equal(outputIds([out('default', 'Default'), out('a', 'MacBook Pro Speakers')]), null, 'no earpiece: no button');
  assert.equal(outputIds([{ kind: 'audioinput', deviceId: 'm', label: 'iPhone Microphone' }]), null);
  assert.deepEqual(outputIds([out('r', 'Receiver'), out('s', 'Speaker')]), { earpiece: 'r', speaker: 's' });
  assert.deepEqual(outputIds([out('r', 'iPhone')]), { earpiece: 'r', speaker: '' }, 'no speaker listed: the default');
  assert.equal(outputIds([out('p', 'iPad')]), null, 'an iPad has no earpiece');
});

test('the panel: the call bar takes its own row and the terminal refits whenever the bar or a note changes its height', () => {
  const read = (f) => readFileSync(join(import.meta.dirname, '../public', f), 'utf8');
  assert.match(read('app.js'), /new ResizeObserver\(\(\) => \{ if \(opened\) \{ sendSize\(\); \(view === 'import' \? iterm : term\)\.scrollToBottom\(\); \} \}\);\s*refit\.observe\(\$\('terminal'\)\); refit\.observe\(\$\('terminal-import'\)\);/);
  assert.match(read('style.css'), /#terminal, #terminal-import \{ flex: 1; min-height: 0; overflow: hidden; \}/, 'xterm keeps its old rows until the fit: clipped, it never covers Hang up');
  assert.match(read('style.css'), /#talk-bar \{ flex: none;/);
});

test('the page: an answer waits for a speaking status line (3 s at most), queued statuses are dropped, owner speech stops it all', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'] });
  const audios = [], fetched = [], recs = [];
  class FakeAudio { pause() {} play() { audios.push(this); return Promise.resolve(); } }
  class FakeRecognition { constructor() { recs.push(this); } start() {} abort() {} }
  let nextId;
  const stub = { Audio: FakeAudio, document: { hidden: false, getElementById: () => null, querySelector: () => null },
    window: { isSecureContext: true, SpeechRecognition: FakeRecognition }, localStorage: { getItem: () => '1', setItem() {} },
    navigator: { mediaDevices: { getUserMedia() {} }, language: 'en-US' },
    fetch: async (url) => {
      if (url === '/api/talk') return { ok: true, json: async () => ({ tts: 'say', stt: 'none' }) };
      if (url === '/api/talk/text') return { ok: true, json: async () => ({ id: nextId }) };
      fetched.push(url);
      return { ok: true, headers: { get: () => '1' }, blob: async () => new Blob(['x']) };
    } };
  const saved = {};
  for (const [k, v] of Object.entries(stub)) { saved[k] = Object.getOwnPropertyDescriptor(globalThis, k); Object.defineProperty(globalThis, k, { value: v, configurable: true, writable: true }); }
  const talk = await import('../public/talk.js');
  const tick = async (ms = 0) => { t.mock.timers.tick(ms); for (let i = 0; i < 30; i++) await Promise.resolve(); };
  // A call with turns t1 and t2 out; t2 (the newest)'s status is being spoken.
  const call = async () => {
    fetched.length = audios.length = recs.length = 0;
    await talk.startTalk(); await tick();
    for (const id of ['t1', 't2']) {
      nextId = id;
      recs.at(-1).onresult({ resultIndex: 0, results: [{ isFinal: true, 0: { transcript: 'how are the budgets' } }] });
      await tick();
    }
    talk.talkMessage({ id: 't2', status: 'Checking budgets' }); await tick(1000);
    assert.deepEqual(fetched, ['/api/talk/audio/t2/status'], 'the status is speaking');
  };
  try {
    await call();
    talk.talkMessage({ id: 't2', status: 'Reading files' });
    talk.talkMessage({ id: 't2', reply: 'Dining is over.', pieces: 1 }); await tick(1000);
    assert.deepEqual(fetched, ['/api/talk/audio/t2/status'], 'the answer waits: the status is not cut off');
    audios[0].onended(); await tick();
    assert.deepEqual(fetched, ['/api/talk/audio/t2/status', '/api/talk/audio/t2/0'], 'the answer follows at once, the queued status dropped');
    talk.endTalk();

    await call();
    talk.talkMessage({ id: 't2', reply: 'Dining is over.', pieces: 1 }); await tick(2900);
    assert.equal(fetched.length, 1, 'still waiting just under the cap');
    await tick(200);
    assert.equal(fetched.at(-1), '/api/talk/audio/t2/0', 'a status that will not end is stopped after 3 s');
    talk.endTalk();

    await call();
    talk.talkMessage({ id: 't2', reply: 'Dining is over.', pieces: 1 }); await tick(500);
    talk.interrupt(); await tick(5000);
    assert.equal(fetched.length, 1, 'the owner speaking stops the status and drops the answer');
  } finally {
    talk.endTalk();
    for (const [k, d] of Object.entries(saved)) { if (d) Object.defineProperty(globalThis, k, d); else delete globalThis[k]; }
  }
});

test('the page on a loudspeaker: speech over the reply ducks it; its own echo is dropped and playback goes on, the owner\'s words stop it and are sent', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'] });
  const audios = [], fetched = [], posted = [], vadOptions = [];
  let vad, verdict, gain;
  class FakeContext { resume() { return Promise.resolve(); } close() { return Promise.resolve(); }
    createGain() { return (gain = { gain: { value: 1 }, connect: (n) => n }); } createMediaElementSource() { return { connect: (n) => n }; } }
  class FakeAudio { volume = 1; pause() { this.paused = true; } play() { this.paused = false; audios.push(this); return Promise.resolve(); } }
  class FakeVAD { constructor(o) { this.o = o; vadOptions.push({ ...o }); } start() { return Promise.resolve(); } pause() { return Promise.resolve(); }
    destroy() { return Promise.resolve(); } setOptions(u) { vadOptions.push(u); } }
  const stub = { Audio: FakeAudio, document: { hidden: false, getElementById: () => null, querySelector: () => null },
    window: { isSecureContext: true, ort: {}, AudioContext: FakeContext, vad: { MicVAD: { new: async (o) => (vad = new FakeVAD(o)) } } },
    localStorage: { getItem: () => '1', setItem() {} },
    navigator: { mediaDevices: { getUserMedia: (c) => ({ ...c, getTracks: () => [] }) }, language: 'en-US' },
    fetch: async (url, init) => {
      if (url === '/api/talk') return { ok: true, json: async () => ({ tts: 'say', stt: 'whisper' }) };
      if (url === '/api/talk/utterance') { posted.push(init.headers); return { ok: true, json: async () => verdict(init.headers) }; }
      fetched.push(url);
      return { ok: true, headers: { get: () => '1' }, blob: async () => new Blob(['x']) };
    } };
  const saved = {};
  for (const [k, v] of Object.entries(stub)) { saved[k] = Object.getOwnPropertyDescriptor(globalThis, k); Object.defineProperty(globalThis, k, { value: v, configurable: true, writable: true }); }
  const talk = await import('../public/talk.js');
  const tick = async (ms = 0) => { t.mock.timers.tick(ms); for (let i = 0; i < 30; i++) await Promise.resolve(); };
  const volume = () => gain.gain.value;   // through the gain node: an audio element's volume is read-only on iOS
  const speech = () => { vad.o.onSpeechStart(); vad.o.onSpeechRealStart(); vad.o.onSpeechEnd(new Float32Array(160)); };
  // A call whose turn t1 is being answered aloud.
  const call = async () => {
    audios.length = fetched.length = posted.length = 0;
    await talk.startTalk(); await tick();
    verdict = () => ({ ok: true, id: 't1' });
    speech(); await tick();
    talk.talkMessage({ id: 't1', reply: 'You spent about four hundred dollars on dining.', pieces: 1 }); await tick();
    assert.deepEqual(fetched, ['/api/talk/audio/t1/0']);
    posted.length = 0;
  };
  try {
    await call();
    for (const get of [vad.o.getStream, vad.o.resumeStream]) {
      assert.deepEqual((await get()).audio, { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true }, 'the mic asks for echo cancellation');
    }
    const track = () => ({ enabled: true, readyState: 'live', stop() { this.readyState = 'ended'; } });
    const desktop = track();
    await vad.o.pauseStream({ getTracks: () => [desktop] });
    assert.equal(desktop.readyState, 'ended', 'Mute on a desktop stops the mic, and its light goes out');
    const phone = track();
    globalThis.navigator.userAgent = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) CriOS/130';
    try { await vad.o.pauseStream({ getTracks: () => [phone] }); } finally { delete globalThis.navigator.userAgent; }
    assert.deepEqual([phone.enabled, phone.readyState], [false, 'live'], 'on an iPhone it only silences it: the voice stays where it was');
    assert.equal(vadOptions.at(-1).positiveSpeechThreshold, 0.85, 'a higher bar while Finnamon speaks');
    assert.equal(vadOptions.at(-1).minSpeechMs, 400);
    const playback = audios.at(-1);

    // Its own voice through the speaker: ducked while checked, then dropped; the playback never stopped.
    let checked;
    verdict = (h) => { checked = { volume: volume(), echoOf: h['X-Echo-Of'] }; return { ok: true, echo: true }; };
    speech(); await tick();
    assert.deepEqual(checked, { volume: talk.DUCK, echoOf: 't1' }, 'ducked, with the playing turn as the echo reference');
    assert.equal(volume(), 1, 'back to full');
    assert.equal(playback.paused, false, 'not stopped');
    assert.equal(fetched.length, 1, 'nothing else fetched or sent');

    // A misfire (too short to be speech) only restores the volume.
    vad.o.onSpeechStart(); vad.o.onSpeechRealStart(); assert.equal(volume(), talk.DUCK);
    vad.o.onVADMisfire(); assert.equal(volume(), 1); assert.equal(posted.length, 1);

    // Muted mid-utterance: the paused detector says nothing more, so the volume comes back here.
    vad.o.onSpeechStart(); vad.o.onSpeechRealStart(); assert.equal(volume(), talk.DUCK);
    talk.setMuted(true); assert.equal(volume(), 1); talk.setMuted(false);

    // The owner: stopped, and the turn is sent like any other.
    verdict = () => ({ ok: true, id: 't2' });
    speech(); await tick();
    assert.equal(posted.at(-1)['X-Echo-Of'], 't1');
    assert.equal(playback.paused, true, 'the owner\'s words stop it');
    assert.equal(vadOptions.at(-1).positiveSpeechThreshold, 0.5, 'the normal bar once nothing plays');
    talk.talkMessage({ id: 't2', reply: 'Groceries are fine.', pieces: 1 }); await tick();
    assert.equal(fetched.at(-1), '/api/talk/audio/t2/0', 't2 is a turn being waited on');
    talk.endTalk();

    // Speech with nothing playing carries no echo reference; no speech changes nothing.
    await call();
    audios.at(-1).onended(); await tick();
    verdict = () => ({ ok: true, id: 't3' });
    speech(); await tick();
    assert.equal(posted.at(-1)['X-Echo-Of'], undefined);
    await tick(5000);
    assert.equal(posted.length, 1);

    // Interrupt still cuts in at once.
    talk.talkMessage({ id: 't3', reply: 'Done.', pieces: 1 }); await tick();
    const p3 = audios.at(-1);
    talk.interrupt();
    assert.equal(p3.paused, true);
  } finally {
    talk.endTalk();
    for (const [k, d] of Object.entries(saved)) { if (d) Object.defineProperty(globalThis, k, d); else delete globalThis[k]; }
  }
});

// Telegram through the intercom (inbound=session): the daemon's hand-off, typed like Talk's lines, the reply read off the transcript.
test('relay: a Telegram turn is typed under its tag and answered from the transcript; two wait their turn; a slow one times out', async () => {
  const { createRelay, telegramPrompt } = await import('../talk.js');
  assert.equal(telegramPrompt({ from: 'sam', text: 'how much\x1b[201~ on dining?', note: 'replying to alert 7' }), '[telegram · sam; replying to alert 7] how much [201~ on dining?');
  assert.equal(telegramPrompt({ from: 'x] [voice', text: 'hi', note: 'a]b' }), '[telegram · x voice; ab] hi', 'the caller cannot close the tag early');
  const d = mkdtempSync(join(tmpdir(), 'finnamon-relay-'));
  const file = join(d, 'session.jsonl');
  writeFileSync(file, JSON.stringify(user('old')) + '\n');
  const typed = [];
  // the fake session: answers each typed line after a moment, in the order typed, the way the TUI queues them
  const relay = createRelay({ write: () => {}, transcript: () => file, pollMs: 5, type: (_w, line) => {
    typed.push(line);
    setTimeout(() => appendFileSync(file, [user(line), said('tool_use', bash('Check')), said('end_turn', text(`answer ${typed.length}`))].map(e => JSON.stringify(e)).join('\n') + '\n'), 20);
  } });
  try {
    const [a, b] = await Promise.all([relay.ask({ from: 'bill', text: 'first' }), relay.ask({ from: 'jane', text: 'second' })]);
    assert.deepEqual([a, b], [{ reply: 'answer 1' }, { reply: 'answer 2' }]);
    assert.deepEqual(typed, ['[telegram · bill] first', '[telegram · jane] second'], 'one at a time: the second was typed only after the first ended');
    const quiet = createRelay({ write: () => {}, transcript: () => file, pollMs: 5, type: () => {} });
    assert.deepEqual(await quiet.ask({ from: 'bill', text: 'slow' }, 1), { status: 504, error: 'timeout', started: false });
    let busy = true; const late = [];
    const waits = createRelay({ write: () => {}, transcript: () => file, pollMs: 5, idle: () => !busy, type: (_w, line) => late.push(line) });
    const pending = waits.ask({ from: 'bill', text: 'after the slow one' }, 2000);
    await new Promise(r => setTimeout(r, 40));
    assert.deepEqual(late, [], 'nothing is typed into a session still busy with an earlier turn');
    busy = false;
    await new Promise(r => setTimeout(r, 40));
    assert.deepEqual(late, ['[telegram · bill] after the slow one']);
    appendFileSync(file, [user(late[0]), said('end_turn', text('done'))].map(e => JSON.stringify(e)).join('\n') + '\n');
    assert.deepEqual(await pending, { reply: 'done' });
    const none = createRelay({ write: () => {}, transcript: () => null, type: () => assert.fail('typed') });
    assert.equal((await none.ask({ from: 'bill', text: 'hi' })).status, 503);
    assert.equal((await relay.ask({ from: 'bill', text: '\x07 ' })).status, 400);
  } finally { rmSync(d, { recursive: true, force: true }); }
});

test('relay route: bearer key only, only in session mode, and the relay\'s answer passes through', async () => {
  const key = 'k'.repeat(64), asked = [];
  const relay = { ask: async (m, ms) => { asked.push([m, ms]); return m.text === 'slow' ? { status: 504, error: 'timeout' } : { reply: 'ok' }; } };
  const serve = async (inbound) => { const s = buildApp({ token: () => key, cli: async () => ({}), allowHost: () => true, inbound, relay }).listen(0, '127.0.0.1'); await new Promise(r => s.once('listening', r)); return s; };
  const post = (srv, headers, body) => fetch(`http://127.0.0.1:${srv.address().port}/api/telegram/turn`, { method: 'POST', headers: { 'content-type': 'application/json', ...headers }, body: JSON.stringify(body) });
  const bearer = { authorization: `Bearer ${key}` };
  const session = await serve('session'), daemon = await serve('daemon');
  try {
    const r = await post(session, bearer, { from: 'bill', text: 'hi', note: '', timeout: 45 });
    assert.equal(r.status, 200); assert.deepEqual(await r.json(), { reply: 'ok' });
    assert.deepEqual(asked[0], [{ from: 'bill', text: 'hi', note: '' }, 45_000]);
    assert.equal((await post(session, bearer, { from: 'bill', text: 'slow' })).status, 504);
    assert.equal((await post(session, {}, { text: 'hi' })).status, 401, 'no key');
    assert.equal((await post(session, { cookie: `finnamon_token_8888=${key}` }, { text: 'hi' })).status, 403, 'the page has nothing to relay');
    assert.equal((await fetch(`http://127.0.0.1:${session.address().port}/api/telegram/turn`, { headers: bearer })).status, 404, 'POST only');
    const wrong = await post(daemon, bearer, { from: 'bill', text: 'hi' });
    assert.equal(wrong.status, 409); assert.match((await wrong.json()).error, /restart/);
    assert.equal(asked.length, 2);
  } finally { session.close(); daemon.close(); }
});

test('an open tool call is a dialog nothing may type into, until its result, the turn\'s end, or a restart of the session', async () => {
  const { openToolCall, createRelay } = await import('../talk.js');
  const d = mkdtempSync(join(tmpdir(), 'finnamon-open-'));
  const file = join(d, 'session.jsonl');
  const now = Date.parse('2026-10-04T12:00:00Z');
  const call = (id, at = now) => ({ type: 'assistant', timestamp: new Date(at).toISOString(), message: { content: [{ type: 'tool_use', id, name: 'WebFetch', input: {} }] } });
  const result = (id) => ({ type: 'user', message: { content: [{ type: 'tool_result', tool_use_id: id, content: 'ok' }] } });
  const put = (...es) => writeFileSync(file, es.map(e => JSON.stringify(e)).join('\n') + '\n');
  try {
    assert.equal(openToolCall(join(d, 'none.jsonl'), now), false, 'no transcript, nothing open');
    put(user('[telegram · bill] hi'), call('a'));
    assert.equal(openToolCall(file, now), true);
    put(user('[telegram · bill] hi'), call('a'), result('a'));
    assert.equal(openToolCall(file, now), false, 'answered (run, or No at the dashboard)');
    put(user('hi'), call('a'), said('end_turn', text('done')));
    assert.equal(openToolCall(file, now), false, 'a turn that ended');
    put(call('a'), user('[telegram · jane] next question'));
    assert.equal(openToolCall(file, now), false, 'a new prompt: a call a stream error left without a result is not waiting');
    put(call('a'), user('<system-reminder>x</system-reminder>'));
    assert.equal(openToolCall(file, now), true, 'Claude Code\'s own tags are not a prompt');
    put(call('a', now - 1));
    assert.equal(openToolCall(file, now), false, 'from before this session started: left behind by a crash or restart');
    put(call('a', now - 6 * 3600_000));
    assert.equal(openToolCall(file, now - 7 * 3600_000), true, 'a dashboard dialog may wait for hours, and still blocks');

    // the relay: a quiet session with an open call is a dialog whose footer a redraw hid; the phone line waits
    put(user('[telegram · bill] first'), call('a'));
    const typed = [];
    let asking = false;
    const relay = createRelay({ write: () => {}, transcript: () => file, pollMs: 5, idle: () => true, asking: () => asking,
                                open: (p) => openToolCall(p, now), type: (_w, line) => typed.push(line) });
    const p = relay.ask({ from: 'jane', text: 'second' }, 1000);
    await new Promise(r => setTimeout(r, 40));
    assert.deepEqual(typed, [], 'never typed into the open dialog');
    put(user('[telegram · bill] first'), call('a'), result('a'));
    await new Promise(r => setTimeout(r, 40));
    assert.deepEqual(typed, ['[telegram · jane] second']);
    appendFileSync(file, [user(typed[0]), said('end_turn', text('ok'))].map(e => JSON.stringify(e)).join('\n') + '\n');
    assert.deepEqual(await p, { reply: 'ok' });

    // a dialog's time is not the turn's: a 1s turn outlives 1s while the session asks
    asking = true;
    const q = relay.ask({ from: 'jane', text: 'third' }, 1000);
    await new Promise(r => setTimeout(r, 1300));
    asking = false;
    appendFileSync(file, [user(typed[1]), said('end_turn', text('late'))].map(e => JSON.stringify(e)).join('\n') + '\n');
    assert.deepEqual(await q, { reply: 'late' });

    // and so is a dialog whose footer a redraw hid: quiet, with the turn's tool call open
    const r2 = createRelay({ write: () => {}, transcript: () => file, pollMs: 5, idle: () => true, asking: () => false,
                             open: (p) => openToolCall(p, now), type: (_w, line) => {
                               typed.push(line); appendFileSync(file, [user(line), call('h')].map(e => JSON.stringify(e)).join('\n') + '\n'); } });
    const h = r2.ask({ from: 'jane', text: 'fourth' }, 1000);
    await new Promise(r => setTimeout(r, 1300));
    appendFileSync(file, [result('h'), said('end_turn', text('after the dialog'))].map(e => JSON.stringify(e)).join('\n') + '\n');
    assert.deepEqual(await h, { reply: 'after the dialog' });

    // Talk: the same guard, but only for a quiet session (a running tool keeps output coming, and a line is queued)
    put(user('hi'), call('b', Date.now()));
    let idle = true;
    const talk = createTalk({ write: () => {}, type: () => assert.fail('typed into a dialog'), transcript: () => file, broadcast: () => {}, idle: () => idle, since: () => now });
    try {
      assert.match(talk.heard('yes', { utterance: 'utterance-20' }).error, /asking something/);
      idle = false;
      assert.throws(() => talk.heard('and another thing', { utterance: 'utterance-21' }), /typed into a dialog/, 'working: typed (queued) as before');
    } finally { talk.stop(); }
  } finally { rmSync(d, { recursive: true, force: true }); }
});
