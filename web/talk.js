// Talk to Finnamon: the intercom's phone button (the page half is public/talk.js; the approach is agent-007's "Talk to
// Billion", MIT). Voice only turns the owner's speech into a line typed into the household's Claude session, and that
// session's reply into speech; the session stays the only brain, and the terminal shows the turn like any other.
//
// The audio stays on this machine. Each utterance arrives as 16 kHz WAV, is written to a temp file whisper.cpp reads and
// deleted at once; the reply is spoken by macOS `say`, its audio kept in memory only (a few pieces, for a replay). The
// words are kept nowhere but where the conversation already keeps them: the session's own transcript, which is also how
// the reply is found. Claude Code writes every turn of the session to <claude config>/projects/<cwd>/<session id>.jsonl;
// after a voice line is typed, that file says what the assistant is doing (each tool call: the progress line spoken while
// the owner waits) and, at end_turn, what it answered.

import { spawn } from 'node:child_process';
import { mkdtemp, writeFile, readFile, rm } from 'node:fs/promises';
import { existsSync, statSync, openSync, readSync, closeSync } from 'node:fs';
import { tmpdir, homedir } from 'node:os';
import { join, delimiter } from 'node:path';
import { plainForSpeech, chunkForSpeech } from './public/speech.js';

export const MODELS = ['ggml-base.en.bin', 'ggml-small.bin'];   // finnamon/voice.py MODELS; the first one there wins
const NAMES = ['whisper-cli', 'whisper-cpp'];
const EXTRA_PATH = ['/opt/homebrew/bin', '/usr/local/bin'];   // finnamon/voice.py EXTRA_PATH: a launchd PATH may lack Homebrew
export const VOICE_TAG = '[voice]';   // the bundle's CLAUDE.md: a line starting with it was spoken, so the answer is heard, not read
export const MAX_UTTERANCE_BYTES = 5 * 60 * 16000 * 2 + 44;   // five minutes of what the page sends
export const SPEECH_CHUNK_CHARS = 300;
export const TURN_TIMEOUT_MS = 10 * 60_000;   // a turn with no end_turn by then is let go; its answer stays in the terminal
const UTTERANCES_A_MINUTE = 30, AUDIO_A_MINUTE = 120, CACHE_PIECES = 24;
const ID = /^[\w-]{8,64}$/;

const which = (name, env = process.env) => {
  if (!name) return null;
  if (name.includes('/')) return existsSync(name) ? name : null;
  for (const dir of [...String(env.PATH || '').split(delimiter), ...EXTRA_PATH]) if (dir && existsSync(join(dir, name))) return join(dir, name);
  return null;
};

// { bin, model }, or { missing: why } (said on the page, with the fix).
export function whisperSetup(env = process.env, home = env.FINNAMON_HOME || join(homedir(), '.finnamon')) {
  const bin = which((env.WHISPER_CPP_BIN || '').trim(), env) || NAMES.map(n => which(n, env)).find(Boolean);
  const want = (env.WHISPER_MODEL || '').trim();
  const model = want ? (existsSync(want) ? want : null) : MODELS.map(m => join(home, 'whisper', m)).find(existsSync);
  if (!bin || !model) return { missing: `whisper.cpp is not set up on the Finnamon computer (run \`finnamon voice setup\` there).` };
  return { bin, model };
}

// Why `say` cannot speak here, or null.
export const speechUnavailable = (env = process.env, platform = process.platform) =>
  platform !== 'darwin' ? 'speaking needs macOS `say`' : which('say', env) ? null : '`say` is not on PATH';

function run(cmd, args, timeout = 5 * 60_000) {
  return new Promise((resolve, reject) => {
    let out = '', err = '';
    const child = spawn(cmd, args, { stdio: ['ignore', 'pipe', 'pipe'], timeout });
    child.stdout.setEncoding('utf8');
    child.stdout.on('data', d => { out += d; });
    child.stderr.on('data', d => { err += d; });
    child.on('error', e => reject(new Error(`${cmd}: ${e.message}`)));
    child.on('close', code => code === 0 ? resolve(out) : reject(new Error(`${cmd} exited ${code}: ${err.trim().split('\n').pop() || ''}`)));
  });
}
async function inTempDir(fn) {
  const dir = await mkdtemp(join(tmpdir(), 'finnamon-talk-'));
  try { return await fn(dir); } finally { await rm(dir, { recursive: true, force: true }); }
}

// WAV bytes → the words. Markers like [BLANK_AUDIO] are not words.
export const transcribe = (wav, { bin, model }) => inTempDir(async (dir) => {
  const f = join(dir, 'utterance.wav');
  await writeFile(f, wav, { mode: 0o600 });
  const out = await run(bin, ['-m', model, '-f', f, '-nt', '-np'], 2 * 60_000);
  return out.replace(/\[[^\]]*\]|\([^)]*\)/g, ' ').replace(/\s+/g, ' ').trim();
});

// Text → AAC in M4A (what Safari and Chrome both play); `say` writes it itself, so no ffmpeg.
export const synthesize = (text, env = process.env) => inTempDir(async (dir) => {
  const txt = join(dir, 'say.txt'), m4a = join(dir, 'say.m4a');
  await writeFile(txt, text, { mode: 0o600 });
  const voice = (env.SAY_VOICE || '').trim();
  await run('say', [...(voice ? ['-v', voice] : []), '-o', m4a, '--file-format=m4af', '--data-format=aac', '-f', txt], 60_000);
  return readFile(m4a);
});

const words = (text) => String(text ?? '').toLowerCase().match(/[\p{L}\p{N}']+/gu) || [];
// What was being spoken, picked up by the mic: what was heard holds a run of at least six of its words in order, or is
// nothing but a shorter run of them. Anything with words of its own is the owner.
export function looksLikeEcho(heard, spoken, run = 6) {
  const said = words(spoken), got = words(heard), need = Math.min(run, got.length);
  for (let i = 0; i < got.length; i++) for (let j = 0; j < said.length; j++) {   // ponytail: O(n·m); a reply is a few hundred words
    let n = 0;
    while (got[i + n] !== undefined && got[i + n] === said[j + n]) n++;
    if (need && n >= need) return true;
  }
  return false;
}
// Heard while Finnamon spoke: a lone word is a fragment of the speaker or whisper's guess at noise ("you"), unless it is
// one a person says to cut in or to answer.
const CUT_IN = new Set(['stop', 'wait', 'pause', 'hold', 'enough', 'quiet', 'cancel', 'no', 'yes', 'yeah', 'yep', 'nope', 'ok', 'okay', 'sure', 'thanks']);
export const overPlaybackEcho = (heard, spoken) => {
  const got = words(heard);
  if (got.length === 1 && CUT_IN.has(got[0])) return false;
  return got.length <= 1 || spoken.some(s => looksLikeEcho(heard, s));
};

// Where Claude Code keeps a session's transcript: every character of the cwd that is not a letter or digit becomes '-'.
export const transcriptPath = (cwd, id, env = process.env) =>
  join((env.CLAUDE_CONFIG_DIR || '').trim() || join(homedir(), '.claude'), 'projects', cwd.replace(/[^a-zA-Z0-9]/g, '-'), `${id}.jsonl`);

// What a progress update says about one step: only a tool call's own description (Bash writes one), when it reads as a short plain sentence
// Claude wrote for people, said whole. Nothing is derived from tool names, commands or the assistant's text; '' otherwise.
const STATUS_MAX_WORDS = 10;
export function stepPhrase(block) {
  if (block?.type !== 'tool_use') return '';
  const t = String(block.input?.description ?? '').replace(/\s+/g, ' ').trim().replace(/\.$/, '');
  const w = t.split(' ');
  const clean = /^[A-Za-z][A-Za-z0-9 ,'’&%-]*$/.test(t) && !/(^|\s)-/.test(t) && !/\b[\w-]+\.[a-z][a-z0-9]{0,4}\b/i.test(t) &&
    !/\b(select|insert|from|where|join|sqlite3?|https?|www)\b/i.test(t);
  return clean && w.length >= 2 && w.length <= STATUS_MAX_WORDS ? t : '';
}

const userText = (e) => {
  const c = e?.message?.content;
  return typeof c === 'string' ? c : Array.isArray(c) ? c.filter(b => b?.type === 'text').map(b => b.text).join('\n') : '';
};
const squash = (s) => String(s).replace(/\s+/g, ' ').trim();

// One voice turn read off the transcript entries written since it was typed: { started, status, reply, done }.
// started: the session took the line (a busy one queues it first); status: the newest step's phrase; reply: what the
// assistant said after its last tool call. Done at end_turn, when the session has gone quiet (idle) after saying something,
// or when another prompt began first (the turn was interrupted; whatever it said so far is the reply).
export function readTurn(entries, prompt, idle = false) {
  const want = squash(prompt);
  let started = false, status = '', tail = [], ended = false, cut = false;
  for (const e of entries) {
    if (e?.isSidechain || e?.isMeta) continue;
    if (e?.type === 'user') {
      const text = squash(userText(e));
      if (!text || text.startsWith('<')) continue;   // tool results; tags are Claude Code's own (command output)
      if (started) { cut = true; break; }
      started = text.includes(want);
      continue;
    }
    if (!started || e?.type !== 'assistant') continue;
    for (const b of e.message?.content || []) {
      if (b?.type === 'tool_use') tail = [];
      else if (b?.type === 'text') tail.push(b.text);
      status = stepPhrase(b) || status;
    }
    if (e.message?.stop_reason === 'end_turn') ended = true;
  }
  return { started, status, reply: tail.join('\n\n'), done: cut || (started && (ended || (idle && tail.length > 0))) };
}

// Typed the way a person pastes: bracketed, in short pieces (Claude Code takes a long paste as an attachment), then
// Enter a moment later. Control characters go first: an ESC[201~ in the text would end the paste and type the rest as keys.
export function typeInto(write, text, { gap = 10, enter = 150 } = {}) {
  const chars = Array.from(String(text).replace(/[\x00-\x1f\x7f-\x9f]/g, ' '));
  const chunks = [];
  for (let i = 0; i < chars.length; i += 200) chunks.push(chars.slice(i, i + 200).join(''));
  const next = () => {
    if (chunks.length) { write(`\x1b[200~${chunks.shift()}\x1b[201~`); setTimeout(next, chunks.length ? gap : enter); }
    else write('\r');
  };
  next();
}

// Whatever the transcript gained since t.offset, parsed into t.entries; t.buf holds a line still being written. A turn
// typed before its session had a file (Codex writes its rollout at the first line) names it once it appears (t.find).
export function readNew(t) {
  if (!t.path) t.path = t.find?.() || '';
  if (!t.path) return;
  let fd;
  try { fd = openSync(t.path, 'r'); } catch { return; }
  try {
    const size = statSync(t.path).size;
    if (size < t.offset) t.offset = 0;   // a new file under the same name
    if (size === t.offset) return;
    const b = Buffer.alloc(size - t.offset);
    readSync(fd, b, 0, b.length, t.offset);
    t.offset = size;
    const lines = (t.buf + b.toString('utf8')).split('\n');
    t.buf = lines.pop();
    for (const l of lines) { try { t.entries.push(JSON.parse(l)); } catch {} }
  } finally { closeSync(fd); }
}

// Whether the transcript's newest tool call is still open (no result yet): the session is running it, or showing its
// permission dialog, which a typed line's Enter would answer Yes. The QUESTION state reads the screen; this reads the
// record, so a redraw that hides the dialog footer cannot open the way. Only the tail is read (the household's transcript
// only grows). since: when the session now running started; a call from before it is one a crash or restart left behind.
// Not an age: a dashboard turn's dialog has no hook timing it out and may wait for hours.
export function openToolCall(path, since = 0, tailBytes = 512 * 1024) {
  const open = new Map();
  for (const e of tailEntries(path, tailBytes)) {
    const c = e?.message?.content;   // a new prompt starts a new turn: a call a stream error left without a result is not waiting
    if (e?.type === 'user' && !e.isMeta && (typeof c === 'string' ? c.trim() && !c.startsWith('<') : Array.isArray(c) && c.some(b => b?.type === 'text' && !String(b.text).startsWith('<')))) open.clear();
    for (const blk of Array.isArray(e?.message?.content) ? e.message.content : []) {
      if (blk?.type === 'tool_use' && e.type === 'assistant') open.set(blk.id, Date.parse(e.timestamp) || Infinity);
      if (blk?.type === 'tool_result') open.delete(blk.tool_use_id);
    }
    if (e?.message?.stop_reason === 'end_turn') open.clear();   // a turn that ended has nothing left waiting
  }
  return [...open.values()].some(at => at >= since);
}

// The last tailBytes of a JSONL transcript, parsed (a line cut by the start is dropped); [] for a file that is not there.
export function tailEntries(path, tailBytes = 512 * 1024) {
  let fd;
  try { fd = openSync(path, 'r'); } catch { return []; }
  try {
    const size = statSync(path).size, start = Math.max(0, size - tailBytes);
    const b = Buffer.alloc(size - start);
    readSync(fd, b, 0, b.length, start);
    const lines = b.toString('utf8').split('\n');
    if (start) lines.shift();   // cut mid-line
    const out = [];
    for (const l of lines) { try { out.push(JSON.parse(l)); } catch {} }
    return out;
  } finally { closeSync(fd); }
}

// The server side of every call: the routes in server.js are thin wrappers over this.
// write: into the household session's pty; transcript(): its transcript's path, or null while there is no session;
// idle(): the session has gone quiet (its WAITING state); asking(): it shows a dialog (QUESTION), which a typed line would answer;
// broadcast: to every page (the same channel the terminal's output already takes); read: the CLI's turn reader (createRelay's).
export function createTalk({ write, transcript, broadcast, idle = () => false, asking = () => false, busy = () => false, since = () => 0, env = process.env, platform = process.platform, pollMs = 500, now = Date.now,
                             stt = transcribe, tts = synthesize, type = typeInto, read = readTurn, open = openToolCall } = {}) {
  const turns = new Map();   // utterance id → { prompt, offset, buf, entries, status, said, pieces, text, at, done }
  const results = new Map();   // utterance id → its answer, so a retry is the line already typed, never a second one
  const inflight = new Map();
  const cache = new Map();   // `${id}:${index}` → Promise<Buffer>
  const recent = { utterance: [], audio: [] };
  let timer = null;

  const allow = (kind, limit) => {
    const t = now();
    recent[kind] = recent[kind].filter(x => t - x < 60_000);
    if (recent[kind].length >= limit) return false;
    recent[kind].push(t);
    return true;
  };
  const remember = (id, result) => { results.set(id, result); while (results.size > 50) results.delete(results.keys().next().value); return result; };

  function setup() {
    const w = whisperSetup(env), say = speechUnavailable(env, platform);
    return { stt: w.missing ? null : 'whisper', ...(w.missing ? { sttMissing: w.missing } : {}), tts: say ? null : 'say', ...(say ? { ttsMissing: say } : {}) };
  }

  // The owner's words (whisper's, or the browser's own recognition): typed into the session once.
  function heard(transcriptText, { utterance, echoOf } = {}) {
    if (!ID.test(utterance || '')) return { error: 'Bad utterance id.' };
    if (results.has(utterance)) return { ...results.get(utterance), duplicate: true };
    const text = squash(String(transcriptText ?? '').replace(/[\x00-\x1f\x7f-\x9f]/g, ' '));
    if (!text) return { error: 'No words were heard.', empty: true };
    const playing = typeof echoOf === 'string' && turns.get(echoOf);   // its reply, or a progress line, was being spoken
    if (playing && overPlaybackEcho(text, [playing.text, ...playing.said])) return remember(utterance, { ok: true, echo: true });
    const path = transcript();   // '' : running, with no file yet (a Codex session's first line)
    if (path == null) return { error: 'The assistant is not running yet; try again in a moment.' };
    // A quiet session with an open tool call is a dialog whose footer a redraw hid; a running tool keeps output coming,
    // and a line typed then is queued by Claude Code. Codex would steer the running turn with it instead (busy): a phone
    // turn's answer would be cut short and its permission prompts would stop reaching the phone, so Talk waits there.
    if (busy()) return { error: 'The assistant is in the middle of a turn; say it again when it finishes.' };
    if (asking() || (idle() && open(path, since()))) return { error: 'The assistant is asking something on the screen; answer it there first.' };
    const prompt = `${VOICE_TAG} ${text}`;
    let offset = 0;
    try { offset = statSync(path).size; } catch {}   // a session that has not written yet starts at 0
    turns.set(utterance, { prompt, path, find: transcript, offset, buf: '', entries: [], status: '', said: [], pieces: [], text: '', at: now(), done: false });
    type(write, prompt);
    timer ??= setInterval(poll, pollMs);
    return remember(utterance, { ok: true, id: utterance, transcript: text });
  }

  // A recorded utterance: transcribed once, however many times the page retries it.
  function utterance(wav, { utterance: id, echoOf } = {}) {
    if (!ID.test(id || '')) return Promise.resolve({ error: 'Bad utterance id.' });
    if (results.has(id)) return Promise.resolve({ ...results.get(id), duplicate: true });
    if (inflight.has(id)) return inflight.get(id);
    if (!Buffer.isBuffer(wav) || wav.length <= 44) return Promise.resolve({ error: 'No audio arrived.' });
    const w = whisperSetup(env);
    if (w.missing) return Promise.resolve({ error: w.missing, noWhisper: true });
    if (!allow('utterance', UTTERANCES_A_MINUTE)) return Promise.resolve({ error: 'Too many utterances this minute; wait a moment.' });
    const job = (async () => {   // never rejects: Express 4 would not catch it
      let words;
      try { words = await stt(wav, w); } catch (e) { console.warn(`talk: could not transcribe: ${e.message}`); return { error: 'Could not transcribe that; say it again.' }; }
      return heard(words, { utterance: id, echoOf });
    })().finally(() => inflight.delete(id));
    inflight.set(id, job);
    return job;
  }

  function poll() {
    for (const [id, t] of turns) {
      if (t.done) { if (now() - t.at > TURN_TIMEOUT_MS) turns.delete(id); continue; }
      readNew(t);
      const r = read(t.entries, t.prompt, idle());
      if (r.status && r.status !== t.status && !r.done) { t.status = r.status; t.said.push(r.status); broadcast({ type: 'talk', id, status: r.status }); }
      if (r.done || now() - t.at > TURN_TIMEOUT_MS) {
        t.done = true; t.at = now(); t.entries = [];
        t.text = plainForSpeech(r.reply); t.pieces = chunkForSpeech(t.text, SPEECH_CHUNK_CHARS);
        broadcast({ type: 'talk', id, reply: t.text, pieces: t.pieces.length });
      }
    }
    if (![...turns.values()].some(t => !t.done) && timer) { clearInterval(timer); timer = null; }
  }

  // One spoken piece of a turn's reply, or (index 'status') its newest progress line: { audio, count } or { status, error }.
  async function audio(id, index) {
    const t = turns.get(id);
    const pieces = !t ? [] : index === 'status' ? (t.status ? [t.status] : []) : t.pieces;
    const i = index === 'status' ? 0 : Number(index);
    if (!(Number.isInteger(i) && i >= 0 && i < pieces.length)) return { status: 404, error: 'No such reply.' };
    const off = speechUnavailable(env, platform);
    if (off) return { status: 503, error: off };
    if (!allow('audio', AUDIO_A_MINUTE)) return { status: 429, error: 'Too much audio this minute.' };
    const key = index === 'status' ? `status:${pieces[0]}` : `${id}:${i}`;
    if (!cache.has(key)) {
      const made = tts(pieces[i], env);
      made.catch(() => cache.delete(key));
      cache.set(key, made);
      while (cache.size > CACHE_PIECES) cache.delete(cache.keys().next().value);
    }
    try { return { audio: await cache.get(key), count: pieces.length }; }
    catch (e) { console.warn(`talk: say could not speak: ${e.message}`); return { status: 503, error: 'say could not speak it' }; }
  }

  return { setup, heard, utterance, audio, poll, stop: () => { clearInterval(timer); timer = null; } };
}

// Telegram always goes through the intercom: the daemon owns the bot's one getUpdates
// slot and hands each household message here; it is typed into the same session as Talk's lines, under a tag naming the
// source and the sender, and the reply is read back off the transcript the same way. One turn at a time: the daemon
// already sends them in order, and a second caller with the key waits its turn rather than typing over the first.
// The runner seam: write/type are the pty, transcript() names the file a turn's entries land in, read() turns those entries
// into { started, reply, done } (readTurn: Claude Code's jsonl), open() says a tool call is still waiting. server.js AGENTS
// picks them per CLI, for Talk too: a Codex session brings its rollout file and codex.js readCodexTurn; the rest stays.
export const TELEGRAM_TAG = 'telegram';   // the bundle's CLAUDE.md: `[telegram · <owner>] ...` is a phone message, answered for a phone
export const RELAY_TIMEOUT_MS = 5 * 60_000;   // the most a caller may ask to wait; the daemon asks for claude_timeout_seconds
export const PERMISSION_WAIT_MS = 11 * 60_000;   // added at most, for time a turn spends on a permission dialog (finnamon/daemon.py waits as long)
const clean = (s, max) => squash(String(s ?? '').replace(/[\x00-\x1f\x7f-\x9f]/g, ' ')).slice(0, max);
export const telegramPrompt = ({ from, text, note }) => {
  const who = clean(from, 40).replace(/[\[\]·;]/g, ''), why = clean(note, 200).replace(/[\[\]]/g, '');   // the tag's own punctuation cannot come from the caller
  return `[${TELEGRAM_TAG} · ${who || 'someone'}${why ? `; ${why}` : ''}] ${clean(text, 4096)}`;
};

export function createRelay({ write, transcript, idle = () => true, asking = () => false, since = () => 0, type = typeInto, read = readTurn, pollMs = 500, now = Date.now, open = openToolCall } = {}) {
  let chain = Promise.resolve();
  const turn = (prompt, timeoutMs) => new Promise((resolve) => {
    const path = transcript();   // '' : running, with no file yet (a Codex session's first line)
    if (path == null) return resolve({ status: 503, error: 'The assistant session is not running yet.' });
    const t = { path, find: transcript, offset: 0, buf: '', entries: [] };
    let at = now(), paused = 0, last = now();
    let typed = false;
    // Typed only once the session is quiet: a turn an earlier message timed out on, or one typed at the dashboard, may
    // still be running, and a line typed into it can land inside that turn, whose answer would then be read as this one's.
    // ponytail: idle is the session's WAITING state, so a person who starts typing in the same instant can still interleave.
    // A permission dialog waits on a person (here or on the phone), not on the model: its time is not the turn's, up to
    // PERMISSION_WAIT_MS more. A dialog left open (a session that typed nothing in) also blocks the next phone message:
    // the open tool call keeps it from being typed into the dialog.
    const timer = setInterval(() => {
      const ts = now();
      // only once typed: a dashboard dialog this line waited behind is not this turn's, and must not spend its allowance
      // ponytail: an open call is a dialog or a tool still running (the screen cannot always tell them apart), so a slow tool
      // also delays the "taking longer" line by up to PERMISSION_WAIT_MS; the hook's own row would tell them apart.
      if (typed && paused < PERMISSION_WAIT_MS && (asking() || open(t.path, since()))) { const d = Math.min(ts - last, PERMISSION_WAIT_MS - paused); paused += d; at += d; }
      last = ts;
      if (!typed) {
        if (!idle() || open(t.path, since())) { if (ts - at > timeoutMs) { clearInterval(timer); resolve({ status: 504, error: 'timeout', started: false }); } return; }
        try { t.offset = t.path ? statSync(t.path).size : 0; } catch {}
        type(write, prompt); typed = true; return;
      }
      readNew(t);
      const r = read(t.entries, prompt, idle());
      if (r.done) { clearInterval(timer); resolve({ reply: r.reply }); }
      else if (now() - at > timeoutMs) { clearInterval(timer); resolve({ status: 504, error: 'timeout', started: r.started }); }
    }, pollMs);
  });
  return {
    // { reply } once the turn ends, or { status, error }; never rejects.
    ask(msg, timeoutMs = RELAY_TIMEOUT_MS) {
      if (!clean(msg?.text, 1)) return Promise.resolve({ status: 400, error: 'no text' });
      const prompt = telegramPrompt(msg), ms = Number(timeoutMs), wait = Math.min(Math.max(ms > 0 ? ms : RELAY_TIMEOUT_MS, 1_000), RELAY_TIMEOUT_MS);
      const p = chain.then(() => turn(prompt, wait));   // wait: what the caller asked for, between 1s and RELAY_TIMEOUT_MS
      chain = p.catch(() => {});
      return p.catch((e) => ({ status: 500, error: e.message }));
    },
  };
}
