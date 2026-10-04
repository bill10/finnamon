// Talk to Finnamon: a hands-free call with the household's assistant, from the intercom's phone button (the server half
// is web/talk.js; the approach and much of the code are agent-007's "Talk to Billion", MIT). One tap, then turns: the
// voice detector (Silero VAD, served by the dashboard from /vendor) hears an utterance end, its audio goes to the server
// once (an id per utterance: a retry is never a second message), the words are typed into the session, and its answer is
// spoken; while it works, its newest progress line is (the first after PROGRESS_QUIET_MS, then each change, newest only).
// Speaking while it talks ducks the playback (a phone's loudspeaker reaches its own mic): the utterance is transcribed and
// compared with what is being spoken, and only the owner's words stop it; Interrupt always does. Never the assistant's
// work: the reply stays in the terminal either way.
//
// Audio stays on the Finnamon computer when it has whisper.cpp (and `say` for the voice). Without whisper.cpp the
// browser's own speech recognition is the fallback, after a one-time notice that it may send audio to the browser's maker;
// without `say`, the browser's speechSynthesis speaks.
import { plainForSpeech, chunkForSpeech, pickVoice } from './speech.js';

const CONSENT_KEY = 'finnamon-talk-browser-stt';   // localStorage: the one-time notice was accepted
export const PROGRESS_QUIET_MS = 800;
export const STATUS_FINISH_MS = 3000;   // the longest an answer waits for a progress phrase to finish
export const IDLE_END_MS = 10 * 60 * 1000;
const RETRY_FOR_MS = 2 * 60 * 1000;
const SAMPLE_RATE = 16000;
// The detector's bar for speech: higher and longer while Finnamon speaks, so what is left of it after echo cancellation
// rarely counts as the owner.
const LISTEN = { positiveSpeechThreshold: 0.5, minSpeechMs: 300 }, LISTEN_PLAYING = { positiveSpeechThreshold: 0.85, minSpeechMs: 400 };
export const DUCK = 0.3;   // playback volume while an utterance heard over it is checked for echo
const MIC = { audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true } };
const OUTPUT_KEY = 'finnamon-talk-output';   // localStorage: 'speaker' (the default) or 'earpiece'

// ---- pure helpers, exported for tests ----------------------------------------------------------------------------

// 16 kHz float samples → a 16-bit mono WAV file, what whisper.cpp reads.
export function encodeWav(samples, rate = SAMPLE_RATE) {
  const buf = new ArrayBuffer(44 + samples.length * 2);
  const v = new DataView(buf);
  const str = (at, s) => { for (let i = 0; i < s.length; i++) v.setUint8(at + i, s.charCodeAt(i)); };
  str(0, 'RIFF'); v.setUint32(4, 36 + samples.length * 2, true); str(8, 'WAVE');
  str(12, 'fmt '); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  str(36, 'data'); v.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    v.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return buf;
}

// How long until this progress phrase may be spoken (0: now), or null when it never should: nothing to say, or what was
// said last. p: { since: when the turn was sent, said }.
export function progressWait(phrase, p, now) {
  if (!phrase || phrase === p.said) return null;
  return Math.max(0, p.since + PROGRESS_QUIET_MS - now);
}

// The line above the call bar: the consent question, a notice, or where the audio goes during a call.
export function noteText({ consent, note, inCall, mode, tts }) {
  if (consent) return 'whisper.cpp is not set up on the Finnamon computer (`finnamon voice setup` there), so this browser\'s own speech recognition would hear you. It may send your audio to the browser\'s maker (Chrome: Google).';
  if (note || !inCall) return note;
  return `${mode === 'browser' ? 'Your browser recognises your speech and may send the audio to its maker.' : 'Your voice is transcribed on the Finnamon computer; no audio leaves it.'} ${tts === 'say' ? 'Replies are spoken by that computer.' : 'Replies are spoken by this browser.'}`;
}

// The one word the bar shows.
export function talkState(s) {
  if (!s.on) return 'off';
  if (s.consent) return 'consent';
  if (s.starting) return 'starting';
  if (!s.connected) return 'disconnected';
  if (s.playing) return 'speaking';
  if (s.muted) return 'muted';
  if (s.hidden) return 'paused';
  if (s.uploads || s.awaiting) return 'thinking';
  return 'listening';
}
const LABELS = { consent: 'Before you talk', starting: 'Starting the microphone…', disconnected: 'Reconnecting…', speaking: 'Speaking',
                 muted: 'Muted', paused: 'Paused (tab hidden)', thinking: 'Thinking…', listening: 'Listening…' };

// Mute holds the mic open on iOS, silenced: iOS plays through the earpiece while a page records and through the loudspeaker
// once it stops, so a mic stopped by Mute would move the voice mid-call. The voice detector's pause and resume go
// through these; a track that ended (the tab was hidden, the OS took the mic) is asked for again.
export const pauseStream = (stream) => { stream?.getTracks().forEach(t => { t.enabled = false; }); };
export async function resumeStream(stream, getUserMedia) {
  if (stream?.getTracks().some(t => t.readyState === 'live')) { stream.getTracks().forEach(t => { t.enabled = true; }); return stream; }
  return getUserMedia();
}

// Speaker or earpiece, kept in this browser.
export function outputPref() { try { return localStorage.getItem(OUTPUT_KEY) === 'earpiece' ? 'earpiece' : 'speaker'; } catch { return 'speaker'; } }
export function saveOutputPref(value) { try { localStorage.setItem(OUTPUT_KEY, value); } catch {} }
// The audio outputs' ids for each choice, or null when this browser cannot choose (no earpiece listed). iOS lists the
// earpiece (receiver) once the mic is granted; the loudspeaker is either listed or the default, '' (which also clears a
// receiver chosen before, WebKit).
export function outputIds(devices) {
  const outs = devices.filter(d => d.kind === 'audiooutput' && d.deviceId && d.deviceId !== 'default');
  const ear = outs.find(d => /receiver|earpiece/i.test(d.label)) ?? outs.find(d => /^iphone$/i.test(d.label.trim()));
  if (!ear) return null;
  return { earpiece: ear.deviceId, speaker: outs.find(d => d !== ear && /speaker/i.test(d.label))?.deviceId ?? '' };
}
// Only on an iPhone or iPad (every browser there is WebKit): desktop browsers have one output for a call, and Android
// Chrome lists no earpiece.
const isIOS = () => /iP(hone|ad|od)/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);

// ---- state -------------------------------------------------------------------------------------------------------

let on = false, starting = false, consent = false, muted = false, hidden = false, connected = true;
let mode = null;          // 'whisper' (VAD + server) or 'browser' (SpeechRecognition)
let tts = 'say';          // 'say' (server) or 'browser' (speechSynthesis)
let vad = null, rec = null, uploads = 0;
let playing = null;       // the turn id whose reply is being spoken, 'status' for a progress update
let playingTurn = null;   // the turn whose reply or progress line is being spoken
let playGen = 0, stopPlay = null, fetches = null, replyQueue = [];
let echoOf = null;        // the turn being spoken when the current utterance started: its words are the echo reference
let hearing = false;      // the owner is speaking now: no update talks over them
let note = '', audioEl = null, audioCtx = null, gain = null, silentUrl = null, startGen = 0;
let idleTimer = null, progressTimer = null, statusCap = null, clockTimer = null, startedAt = 0;
let progress = { since: 0, said: '' };
let micStream = null;     // the voice detector's stream, held for the whole call
let outputs = null;       // outputIds() for this call, or null: no Speaker/Earpiece button
let output = 'speaker';   // where the voice goes now
const awaiting = new Map();   // turn id → its newest progress line ('' until one comes)
export const talkOn = () => on;
// Where the audio goes, once closed, stays closed in this browser until its wording changes. A notice closes for now;
// the consent question cannot be closed.
const NOTE_KEY = 'talk-note-dismissed';
const dismissed = () => { try { return localStorage.getItem(NOTE_KEY); } catch { return null; } };

// ---- the bar -----------------------------------------------------------------------------------------------------

const $ = (id) => document.getElementById(id);
const bar = () => $('talk-bar');

export function initTalk() {
  $('talk-btn')?.addEventListener('click', () => (on && !consent ? endTalk() : startTalk()));
  $('talk-mute')?.addEventListener('click', () => setMuted(!muted));
  $('talk-out')?.addEventListener('click', () => setOutput(output === 'speaker' ? 'earpiece' : 'speaker', true));
  $('talk-end')?.addEventListener('click', () => endTalk());
  $('talk-skip')?.addEventListener('click', () => interrupt());
  $('talk-go')?.addEventListener('click', () => startTalk());
  $('talk-note-x')?.addEventListener('click', () => {   // a notice goes for now; where the audio goes stays dismissed in this browser
    if (note) note = ''; else try { localStorage.setItem(NOTE_KEY, $('talk-note').textContent); } catch {}
    paint();
  });
  document.addEventListener('visibilitychange', onVisibility);
  paint();
}

const clock = (ms) => `${Math.floor(ms / 60000)}:${String(Math.floor(ms / 1000) % 60).padStart(2, '0')}`;
function paint() {
  const b = bar();
  if (!b) return;
  const s = talkState({ on, consent, starting, connected, playing, muted, hidden, uploads, awaiting: awaiting.size });
  const inCall = on && !consent;
  b.dataset.state = s;
  const text = noteText({ consent, note, inCall, mode, tts });
  const shown = consent || note || text !== dismissed() ? text : '';
  b.hidden = !on && !shown;
  $('talk-call').hidden = !inCall;
  $('talk-state').textContent = inCall ? LABELS[s] : '';
  $('talk-skip').hidden = s !== 'speaking';
  $('talk-mute').setAttribute('aria-pressed', String(muted));
  $('talk-mute').lastElementChild.textContent = muted ? 'Unmute' : 'Mute';
  const out = $('talk-out');
  if (out) {
    out.hidden = !outputs;
    out.dataset.output = output;
    out.setAttribute('aria-label', output === 'speaker' ? 'Speaker: tap for the earpiece' : 'Earpiece: tap for the speaker');
    out.lastElementChild.textContent = output === 'speaker' ? 'Speaker' : 'Earpiece';
  }
  $('talk-go').hidden = !consent;
  $('talk-note').textContent = shown;
  $('talk-note-x').hidden = consent || !shown;
  const btn = $('talk-btn');
  if (btn) {
    btn.setAttribute('aria-pressed', String(on));
    btn.title = on ? 'Hang up' : 'Talk to Finnamon: speak, hear its reply, speak again';
    btn.setAttribute('aria-label', on ? 'Hang up' : 'Talk to Finnamon');
  }
  clearInterval(clockTimer); clockTimer = null;
  if (inCall) { const tick = () => { $('talk-timer').textContent = clock(Date.now() - startedAt); }; tick(); clockTimer = setInterval(tick, 1000); }
}
function setNote(text) { note = text; paint(); }

// ---- start and end -----------------------------------------------------------------------------------------------

const recognitionCtor = () => window.SpeechRecognition || window.webkitSpeechRecognition || null;

// The tap on the phone (or Continue). Audio must be unlocked inside the tap, so that happens before anything awaits.
export async function startTalk() {
  if (on && !consent) return;
  unlockAudio();
  if (consent) {
    try { localStorage.setItem(CONSENT_KEY, '1'); } catch {}
    consent = false; note = '';
    return ready();
  }
  note = '';
  if (!window.isSecureContext) return setNote('Talking needs HTTPS or localhost: on a phone, open the dashboard through `tailscale serve`.');
  if (!navigator.mediaDevices?.getUserMedia) return setNote('This browser has no microphone API.');
  const gen = ++startGen;
  const current = () => on && gen === startGen;
  on = true; starting = true; startedAt = Date.now(); muted = false; hidden = document.hidden;
  // Play-and-record for the whole call (Safari and WebKit browsers since iOS 16.4), so the route does not change when
  // the mic does; WebKit gives that session the loudspeaker unless the earpiece is chosen.
  try { if (navigator.audioSession) navigator.audioSession.type = 'play-and-record'; } catch {}
  paint();
  let setup;
  try {
    const r = await fetch('/api/talk');
    setup = await r.json();
    if (!r.ok) throw new Error(setup.error || `HTTP ${r.status}`);
  } catch (e) { return current() && failStart(`Could not start: ${e.message}`); }
  if (!current()) return;
  tts = setup.tts === 'say' ? 'say' : 'browser';
  if (tts === 'browser' && !window.speechSynthesis) return failStart(`Finnamon cannot speak here: ${setup.ttsMissing}, and this browser has no speech synthesis.`);
  if (setup.stt === 'whisper') {
    try {
      await startVad(current);
      if (!current()) return;
      mode = 'whisper'; starting = false;
      return ready();
    } catch (e) {
      console.warn('[talk] voice detector failed:', e);
      if (!current()) return;
      const v = vad; vad = null; v?.destroy?.().catch?.(() => {}); releaseMic();
      if (/NotAllowed|Permission|denied/i.test(`${e?.name} ${e?.message}`)) return failStart('Microphone access denied: allow it in the browser\'s settings, then press the phone again.');
      if (!recognitionCtor()) return failStart('The voice detector could not start in this browser.');
    }
  }
  if (!recognitionCtor()) return failStart(`${setup.sttMissing || 'Talking needs whisper.cpp on the Finnamon computer.'} This browser has no speech recognition to fall back on.`);
  mode = 'browser'; starting = false;
  let accepted = false;
  try { accepted = localStorage.getItem(CONSENT_KEY) === '1'; } catch {}
  if (!accepted) { consent = true; return paint(); }
  ready();
}

function failStart(text) { endTalk(); setNote(text); }

// Hang up: the mic stops and nothing more is spoken. Replies still on their way land in the terminal as text.
export function endTalk({ notice = '' } = {}) {
  on = false; startGen++; starting = false; consent = false; muted = false; hearing = false;
  interrupt();
  clearTimeout(idleTimer); clearTimeout(progressTimer);
  const v = vad; vad = null; v?.destroy?.().catch?.(() => {});
  releaseMic();
  outputs = null;
  try { if (navigator.audioSession) navigator.audioSession.type = 'auto'; } catch {}
  const ctx = audioCtx; audioCtx = null; ctx?.close().catch(() => {});
  audioEl = gain = null;   // the element is tied to the closed context; the next call's tap makes both again
  stopRecognition();
  awaiting.clear();
  note = notice;
  paint();
}

// A tap's grace: one element, played silent inside the tap, plays the later replies (iOS).
function unlockAudio() {
  audioEl ??= Object.assign(new Audio(), { preload: 'auto' });
  try {
    silentUrl ??= URL.createObjectURL(new Blob([encodeWav(new Float32Array(160))], { type: 'audio/wav' }));
    audioEl.src = silentUrl;
    audioEl.play().catch(() => {});
  } catch {}
  try { window.speechSynthesis?.resume(); } catch {}
  try { audioCtx ??= new (window.AudioContext || window.webkitAudioContext)(); audioCtx.resume().catch(() => {}); } catch {}
  // Through a gain node, so ducking works on iOS too, where an audio element's volume is read-only.
  try { if (audioCtx && !gain) { const g = audioCtx.createGain(); audioCtx.createMediaElementSource(audioEl).connect(g).connect(audioCtx.destination); gain = g; } } catch {}
}
// Playback quieter while an utterance heard over it is checked, or back to full. The browser's voice cannot be ducked.
function duck(down) {
  const v = down ? DUCK : 1;
  if (gain) gain.gain.value = v; else if (audioEl) audioEl.volume = v;
}

function armIdle() {
  clearTimeout(idleTimer);
  idleTimer = setTimeout(() => { if (on) endTalk({ notice: 'Call ended: no speech for 10 minutes.' }); }, IDLE_END_MS);
}
function ready() { armIdle(); afterSpeech(); findOutputs(); }

// The Speaker/Earpiece button, where this browser can route the voice (iOS 26's setSinkId). Asked once the mic is
// granted, since iOS lists its outputs only then; the saved choice is applied straight away, and again on a tap if
// iOS wanted one for it.
async function findOutputs() {
  output = 'speaker';
  if (!isIOS() || !audioEl?.setSinkId || !navigator.mediaDevices?.enumerateDevices) return paint();
  const gen = startGen;
  const ids = outputIds(await navigator.mediaDevices.enumerateDevices().catch(() => []));
  if (!on || gen !== startGen) return;
  outputs = ids;
  await setOutput(outputPref(), false);   // the speaker too: iOS keeps a page's last output
  paint();
}
async function setOutput(value, tapped) {
  if (!outputs || !audioEl) return;
  try {
    await audioEl.setSinkId(outputs[value]);
    output = value;
    if (tapped) saveOutputPref(value);
  } catch (e) {
    console.warn('[talk] output switch failed', e);
    if (tapped) setNote(`Could not switch to the ${value}.`);
  }
  paint();
}

// ---- listening: the voice detector -------------------------------------------------------------------------------

function loadScript(src) {
  return new Promise((resolve, reject) => {
    if (document.querySelector(`script[src="${src}"]`)) return resolve();
    const s = Object.assign(document.createElement('script'), { src, onload: resolve, onerror: () => reject(new Error(`could not load ${src}`)) });
    document.head.appendChild(s);
  });
}

async function startVad(current) {
  // A stream granted after the call ended is stopped at once; one granted while the tab hid is stopped once the detector
  // has it, like any hidden tab's.
  const keep = (stream) => {
    if (!current()) { stream?.getTracks().forEach(t => t.stop()); throw new Error('the call ended'); }
    if (hidden) setTimeout(() => { if (on && hidden) stopMic(true); });
    return (micStream = stream);
  };
  if (!window.ort) await loadScript('/vendor/ort/ort.wasm.min.js');
  if (!window.vad) await loadScript('/vendor/vad/bundle.min.js');
  const v = await window.vad.MicVAD.new({
    model: 'v5', baseAssetPath: '/vendor/vad/', onnxWASMBasePath: '/vendor/ort/',
    ortConfig: (ort) => { ort.env.logLevel = 'error'; ort.env.wasm.numThreads = 1; },
    ...LISTEN, negativeSpeechThreshold: 0.35, redemptionMs: 800,
    preSpeechPadMs: 600,   // room for the first word of a barge-in, heard late under the raised bar
    startOnLoad: false,
    getStream: async () => keep(await navigator.mediaDevices.getUserMedia(MIC)),
    // Held open only on iOS, where the route depends on it; elsewhere Mute stops it, and the browser's mic light goes out.
    pauseStream: async (s) => (isIOS() ? pauseStream(s) : s?.getTracks().forEach(t => t.stop())),
    resumeStream: async (s) => keep(await resumeStream(s, () => navigator.mediaDevices.getUserMedia(MIC))),
    ...(audioCtx ? { audioContext: audioCtx } : {}),
    onSpeechStart: () => { hearing = true; },   // no update starts over the owner, even if the real start never comes
    // A real start, a few frames in, while Finnamon speaks only ducks it: the words decide (submitted).
    onSpeechRealStart: () => {
      armIdle(); hearing = true;
      if (playing) { echoOf = playingTurn; duck(true); }
    },
    onVADMisfire: () => { echoOf = null; hearing = false; duck(false); },
    onSpeechEnd: (audio) => { hearing = false; if (on && !muted) sendAudio(audio); else duck(false); },
  });
  if (!current()) { v.destroy().catch(() => {}); return; }
  vad = v;
  if (!muted && !hidden) await v.start();
  if (!current() && vad !== v) v.destroy().catch(() => {});   // hung up while the mic was being granted
}

function startMic() {
  if (!on || muted || hidden || consent || starting) return;
  if (mode === 'whisper') vad?.start().catch(e => console.warn('[talk] mic restart failed', e));
  else if (mode === 'browser' && !playing) startRecognition();
}
// release: the mic itself stops (a hidden tab, where nothing plays); Mute only silences it.
function stopMic(release = false) {
  hearing = false; echoOf = null; duck(false);   // a paused detector ends the utterance with no event
  if (mode === 'whisper') vad?.pause().then(() => release && releaseMic()).catch(() => {});
  else stopRecognition();
}
function releaseMic() { micStream?.getTracks().forEach(t => t.stop()); micStream = null; }
export function setMuted(value) {
  if (!on) return;
  muted = value;
  if (muted) stopMic(); else startMic();
  paint();
}

// ---- listening: the browser's recognition (no whisper.cpp) -------------------------------------------------------

function startRecognition() {
  if (rec) return;
  const r = new (recognitionCtor())();
  rec = r;
  r.continuous = true; r.interimResults = false; r.lang = navigator.language || 'en-US';
  r.onresult = (event) => {
    if (rec !== r) return;
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const res = event.results[i];
      if (res.isFinal && res[0].transcript.trim()) sendText(res[0].transcript);
    }
  };
  r.onerror = (event) => {
    if (rec !== r) return;
    if (event.error === 'not-allowed' || event.error === 'service-not-allowed') failStart('Microphone or speech recognition access denied: allow it in the browser\'s settings.');
    else if (event.error === 'audio-capture') failStart('No microphone found.');
  };
  r.onend = () => { if (rec !== r) return; rec = null; setTimeout(startMic, 250); };   // browsers end recognition after a silence
  try { r.start(); } catch { rec = null; }
}
function stopRecognition() {
  const r = rec; rec = null;
  if (!r) return;
  r.onresult = r.onerror = r.onend = null;
  try { r.abort(); } catch {}
}

// ---- sending -----------------------------------------------------------------------------------------------------

const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));
// POST with the same utterance id until the server answers: the server keeps a retry from being a second message.
async function postOnce(url, init) {
  const until = Date.now() + RETRY_FOR_MS;
  for (let wait = 1000; on; wait = Math.min(wait * 2, 10000)) {
    try {
      const res = await fetch(url, init);
      return await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
    } catch {
      if (Date.now() > until) return { error: 'Could not reach the Finnamon computer; say it again once it is back.' };
      await sleep(wait);
    }
  }
  return null;
}
const newId = () => (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`);

function sendAudio(samples) {
  const echo = echoOf; echoOf = null;
  const headers = { 'Content-Type': 'audio/wav', 'X-Utterance-Id': newId(), ...(echo ? { 'X-Echo-Of': echo } : {}) };
  return submitted(postOnce('/api/talk/utterance', { method: 'POST', headers, body: encodeWav(samples) }), !!echo);
}
function sendText(text) {
  const body = JSON.stringify({ utterance: newId(), text });
  return submitted(postOnce('/api/talk/text', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body }));
}
// overPlayback: heard while Finnamon spoke. Its own voice (or a stray fragment) is dropped and the playback, which never
// stopped, comes back to full volume; the owner's words stop it.
async function submitted(request, overPlayback = false) {
  uploads++; paint();
  const result = await request;
  uploads--;
  if (overPlayback && !hearing) duck(false);   // not while another utterance over it is still being heard
  if (!on || !result) return paint();
  if (result.error) return setNote(result.empty ? '' : result.error);
  if (result.echo) return setNote('That sounded like Finnamon\'s own voice, so it was not sent. Say it again if it was you.');
  if (overPlayback) interrupt();
  note = '';
  if (!result.duplicate) awaiting.set(result.id, '');
  progress = { since: Date.now(), said: '' };
  clearTimeout(progressTimer);
  progressTimer = setTimeout(sayProgress, PROGRESS_QUIET_MS);
  paint();
}

// ---- what the server says about a turn (the socket's { type: 'talk' } frames, app.js) ----------------------------

export function talkMessage(msg) {
  if (!on || !awaiting.has(msg.id)) return;   // another page's call, or one hung up on
  if (typeof msg.status === 'string') { awaiting.set(msg.id, msg.status); return sayProgress(); }
  if (typeof msg.reply !== 'string') return;
  awaiting.delete(msg.id);
  if (!awaiting.size) clearTimeout(progressTimer);
  if (msg.reply.trim()) replyQueue.push({ id: msg.id, text: msg.reply, pieces: msg.pieces });
  if (playing === 'status') {   // the phrase being spoken finishes (playNext then speaks the answer), but not for long
    clearTimeout(progressTimer);   // no newer phrase queues behind it
    const gen = playGen;
    statusCap = setTimeout(() => {
      if (gen !== playGen || playing !== 'status') return;
      const queued = replyQueue; interrupt(); replyQueue = queued; if (!playing && !hidden) playNext();
    }, STATUS_FINISH_MS);
  }
  if (!playing && !hidden) playNext(); else paint();
}

// Whether the page's socket is up; the bar says so.
export function talkConnected(up) { connected = up; paint(); }

function playNext() {
  const m = replyQueue.shift();
  if (m) speak(m.id, m.text); else afterSpeech();
}
function afterSpeech() {
  audioCtx?.resume?.().catch(() => {});   // iOS can leave it suspended after a call or the background, and replies play through it
  if (!playing) vad?.setOptions?.(LISTEN);
  paint();
  if (on && !playing) { startMic(); sayProgress(); }
}

// The newest turn's newest progress line, once nothing else is playing; several changes meanwhile: only the newest.
function sayProgress() {
  clearTimeout(progressTimer);
  if (!on || !awaiting.size || hidden) return;
  const id = [...awaiting.keys()].at(-1), phrase = awaiting.get(id);
  const wait = progressWait(phrase, progress, Date.now());
  if (wait === null) return;
  if (wait > 0 || playing || uploads || hearing) { progressTimer = setTimeout(sayProgress, wait || 250); return; }
  progress = { ...progress, said: phrase };
  speak('status', phrase, id);
}

async function speak(id, text, statusOf) {
  const mine = ++playGen;
  playing = id; playingTurn = statusOf || id;
  if (mode === 'browser') stopRecognition();   // the browser's recognition would hear the reply
  vad?.setOptions?.(LISTEN_PLAYING);
  paint();
  try {
    if (tts === 'say' && !(await speakFromServer(statusOf || id, statusOf ? 'status' : 0, mine)) && mine === playGen && id !== 'status') {
      tts = 'browser';
      setNote('The Finnamon computer could not speak the reply; using the browser\'s voice.');
      await speakInBrowser(text, mine);
    } else if (tts === 'browser') await speakInBrowser(text, mine);
  } finally {
    if (mine === playGen) { playing = null; playNext(); }
  }
}

// The pieces from the server, each fetched while the one before plays. false when the first could not be had.
async function speakFromServer(id, first, mine) {
  fetches = new AbortController();
  const signal = fetches.signal;
  const piece = (i) => fetch(`/api/talk/audio/${encodeURIComponent(id)}/${i}`, { signal })
    .then(async res => (res.ok ? { blob: await res.blob(), count: Number(res.headers.get('X-Pieces')) || 1 } : null)).catch(() => null);
  if (first === 'status') { const got = await piece('status'); if (got && mine === playGen) await playBlob(got.blob, mine); return !!got; }
  let next = piece(0);
  for (let i = 0, count = 1; i < count; i++) {
    const got = await next;
    if (mine !== playGen) return true;
    if (!got) return i > 0;
    count = got.count;
    next = i + 1 < count ? piece(i + 1) : null;
    await playBlob(got.blob, mine);
  }
  return true;
}

function playBlob(blob, mine) {
  return new Promise(resolve => {
    if (mine !== playGen) return resolve();
    const url = URL.createObjectURL(blob);
    const done = () => { audioEl.onended = audioEl.onerror = null; URL.revokeObjectURL(url); if (stopPlay === done) stopPlay = null; resolve(); };
    stopPlay = done;
    audioEl.onended = done; audioEl.onerror = done;
    audioEl.src = url;
    audioEl.play().catch(done);
  });
}

function speakInBrowser(text, mine) {
  const synth = window.speechSynthesis;
  const voice = pickVoice(synth.getVoices());
  return chunkForSpeech(plainForSpeech(text)).reduce((before, chunk) => before.then(() => new Promise(resolve => {
    if (mine !== playGen) return resolve();
    const u = new SpeechSynthesisUtterance(chunk);
    if (voice) { u.voice = voice; u.lang = voice.lang; }
    const done = () => { if (stopPlay === done) stopPlay = null; resolve(); };
    stopPlay = done; u.onend = done; u.onerror = done;
    synth.speak(u);
  })), Promise.resolve());
}

// The owner spoke (or pressed Interrupt): playback stops now, and the pieces and replies behind it are dropped.
export function interrupt() {
  playGen++;
  duck(false);
  replyQueue = [];
  clearTimeout(statusCap);
  fetches?.abort(); fetches = null;
  try { audioEl?.pause(); } catch {}
  try { window.speechSynthesis?.cancel(); } catch {}
  const stop = stopPlay; stopPlay = null; stop?.();
  if (playing) { playing = null; afterSpeech(); }
}

// A hidden tab cannot show the mic is on: it stops, playback too, and both come back with the tab.
function onVisibility() {
  if (!on) return;
  hidden = document.hidden;
  if (hidden) { stopMic(true); interrupt(); paint(); }
  else afterSpeech();
}
