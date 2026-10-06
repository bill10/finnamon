// The dashboard's session on Codex (the `assistant` setting is codex): what server.js AGENTS.codex brings to the session in
// the corner, mirroring finnamon/codex.py and agent_runner.py on the Python side. Recorded against codex-cli 0.157
// (docs/designs/codex-spike.md; tests/fixtures/codex).
//
// - The seal: CODEX_HOME is FINNAMON_HOME/codex (generated config.toml, the shared login) and HOME an empty directory there,
//   because Codex reads ~/.agents/skills whatever CODEX_HOME says (codex.env()). The command line pins what keeps the seal
//   over whatever that config.toml says, as agent_runner.CODEX_LOCK does for exec, except that a person may be asked:
//   approval_policy on-request, the :workspace profile. FINNAMON_FROM_AGENT (the CLI's human-only gates) goes on what the
//   session runs, its shell commands and the finnamon tool, never on the process itself: its hooks inherit that, and the
//   PermissionRequest hook stays silent for a marked process (approval.ask: an unattended run), so the phone would never
//   be asked.
// - --no-daemon: 0.157's TUI otherwise hands the session to a shared background app-server that outlives the pty (and
//   whose socket path under a long FINNAMON_HOME is past SUN_LEN). The household's session lives and dies with this one.
// - There is no --session-id: Codex names its own. Its rollout, $CODEX_HOME/sessions/YYYY/MM/DD/rollout-<local start
//   time>-<id>.jsonl, is written once the first line is typed, not at start (live, 0.157), and its first line
//   (session_meta) names the cwd and the source: `cli` for the TUI, `exec` for the daemon's and triage's runs, which share
//   the directory and the home.

import { readdirSync, statSync, openSync, readSync, closeSync } from 'node:fs';
import { join } from 'node:path';
import { tailEntries } from './talk.js';

export const PROFILE = 'finnamon';   // finnamon/codex.py PROFILE
export const codexHome = (home) => join(home, 'codex');   // codex.home()
export const codexEnv = (home) => ({ CODEX_HOME: codexHome(home), HOME: join(codexHome(home), 'home'), FINNAMON_HOME: home });   // codex.env()

export function codexArgs(dir) {
  const lock = ['approval_policy="on-request"', 'web_search="disabled"', 'features.apps=false', 'check_for_update_on_startup=false',
    'features.hooks=true',   // the secret guard and the phone's permission hook
    `default_permissions="${PROFILE}"`, `permissions.${PROFILE}.extends=":workspace"`,
    `projects={${JSON.stringify(dir)}={trust_level="trusted"}}`,   // an inline table merges; a dotted key would split the path at its dots
    'shell_environment_policy.set.FINNAMON_FROM_AGENT="1"', 'mcp_servers.finnamon.env.FINNAMON_FROM_AGENT="1"'].flatMap(v => ['-c', v]);
  // inbound has no say: channel mode is refused on Codex (`finnamon channel on`), and the web is off in every mode
  return (_inbound, session = null) => [...lock, ...(session?.created && session.id ? ['resume', '--no-daemon', session.id] : ['--no-daemon'])];
}

// A rollout's first line, parsed, or null. Bounded: session_meta carries the base instructions (some 20 KB). A first
// line never changes, so each file's is read once: the id poll would otherwise reread every exec run of the day.
// ponytail: one entry per rollout this process has looked at; pruning codex/sessions (TODOS.md) bounds it.
const metas = new Map();
function meta(path, max = 256 * 1024) {
  if (metas.has(path)) return metas.get(path);
  let fd, m = null;
  try {
    fd = openSync(path, 'r');
    const b = Buffer.allocUnsafe(max), n = readSync(fd, b, 0, max, 0);
    const text = b.subarray(0, n).toString('utf8'), nl = text.indexOf('\n');
    if (nl < 0 && n < max) return null;   // still being written: asked again next time (a line past max is no session_meta we read)
    try { const e = JSON.parse(text.slice(0, nl)); m = e?.type === 'session_meta' ? e.payload : null; } catch {}   // a whole line that is not JSON: never one
  } catch { return null; } finally { if (fd !== undefined) closeSync(fd); }
  metas.set(path, m);
  return m;
}
// The rollouts under sessions/YYYY/MM/DD: every day, or (since) only the days from that start's to today's, local time
// as Codex names them, so a poll does not walk months of the daemon's exec runs.
const DAY_MS = 86_400_000;
function rollouts(codex, since = null) {
  const pad = (n) => String(n).padStart(2, '0');
  let dirs;
  if (since == null) {
    try { return readdirSync(join(codex, 'sessions'), { recursive: true }).filter(f => /(^|\/)rollout-[^/]*\.jsonl$/.test(f)).map(f => join(codex, 'sessions', f)); } catch { return []; }
  } else {
    dirs = [];
    for (let t = since - DAY_MS; t <= Date.now() + DAY_MS; t += DAY_MS) {   // a day either side: a start just past midnight, a clock change
      const d = new Date(t);
      dirs.push(join(codex, 'sessions', String(d.getFullYear()), pad(d.getMonth() + 1), pad(d.getDate())));
    }
  }
  return [...new Set(dirs)].flatMap(d => { try { return readdirSync(d).filter(f => /^rollout-.*\.jsonl$/.test(f)).map(f => join(d, f)); } catch { return []; } });
}

// The id of the TUI session started at or after `since` in `cwd`: the first such rollout (a thread the session spawns
// later, or a `codex` someone starts there by hand, comes after it). Only a `cli` source: an exec run in the same
// directory is never it, and neither is an older thread someone resumed by hand there (`install`'s retire hint): its
// session_meta timestamp is its own start. Modification time only narrows the files to read (some filesystems report
// no birth time).
export function codexSessionId(codex, cwd, since = 0) {
  const fresh = rollouts(codex, since).map(path => { try { return { path, at: statSync(path).mtimeMs }; } catch { return null; } })
    .filter(r => r && r.at >= since - 2_000);
  const ours = fresh.map(({ path }) => meta(path))
    .filter(m => m && m.cwd === cwd && m.source === 'cli' && m.id && Date.parse(m.timestamp) >= since - 2_000)
    .sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp));
  return ours[0]?.id ?? null;
}

// Where a session's rollout is: the file named for its id. The date in the path is the start's, so it is looked up, once;
// a miss (not written yet, or pruned) is looked up again at most every MISS_MS, not on every poll.
const found = new Map(), missed = new Map(), MISS_MS = 5_000;
export function rolloutPath(codex, id, now = Date.now()) {
  if (!id) return null;
  const key = `${codex}\0${id}`;
  if (!found.has(key)) {
    if (now - (missed.get(key) ?? -Infinity) < MISS_MS) return null;
    const p = rollouts(codex).find(f => f.endsWith(`-${id}.jsonl`));
    if (!p) { missed.set(key, now); return null; }
    missed.delete(key);
    found.set(key, p);
  }
  return found.get(key);
}

const squash = (s) => String(s).replace(/\s+/g, ' ').trim();
const itemText = (item) => (item?.content || []).filter(c => c && /^text$/i.test(c.type)).map(c => c.text).join('\n');
const CALLS = new Set(['CommandExecution', 'FileChange', 'McpToolCall']);   // finnamon/approval.py CODEX_CALLS
const CALL_LINES = new Set(['function_call', 'custom_tool_call', 'local_shell_call']);   // a call's response_item, written as it starts

// talk.js readTurn's twin for a rollout: { started, status, reply, done } from the event_msg items written since the line
// was typed. The prompt is a UserMessage item; the reply is the turn's last_agent_message at task_complete, or what the
// assistant said after its last tool call; an interrupted turn (turn_aborted) or the next prompt ends it too. Codex calls
// carry no description written for people, so there is no progress phrase (talk.js stepPhrase derives none from commands).
export function readCodexTurn(entries, prompt, idle = false) {
  const want = squash(prompt);
  let started = false, tail = [], ended = false, cut = false, last = null;
  for (const e of entries) {
    if (started && e?.type === 'response_item' && CALL_LINES.has(e.payload?.type)) { tail = []; continue; }   // a call started: what came before is preamble, even while it waits on the phone
    const p = e?.type === 'event_msg' ? e.payload : null;
    if (!p) continue;
    const item = p.type === 'item_completed' ? p.item : null;
    if (item?.type === 'UserMessage') {
      const text = squash(itemText(item));
      if (!text) continue;
      if (started) { cut = true; break; }
      started = text.includes(want);
      continue;
    }
    if (!started) continue;
    if (item?.type === 'AgentMessage') tail.push(itemText(item));
    else if (CALLS.has(item?.type)) tail = [];
    else if (p.type === 'task_complete') { ended = true; last = p.last_agent_message ?? null; }
    else if (p.type === 'turn_aborted') ended = true;
  }
  const reply = last != null && last !== '' ? last : tail.join('\n\n');
  return { started, status: '', reply, done: cut || (started && (ended || (idle && tail.length > 0))) };
}

// A dialog on Codex's screen (agent-007 lib/helpers.js MESSAGE_PATTERNS, the Codex ones): a command, edit or permission
// approval; the trust and hook-review prompts the generated config should keep from ever showing; a picker. Each needs
// its question AND a footer or numbered answer of a dialog (FOOTER): a finished reply that merely asks "Would you like to
// run a sync?" stays in the tail of an idle session, and alone it would hold the session in QUESTION, refusing every
// phone message. Codex draws with cursor moves, so the text runs together: \s* between words. Only what came after the
// newest "esc to interrupt" (the working status, drawn again once an answer lets the turn go on) or interruption counts:
// an answered dialog's text stays in the tail until newer output pushes it out.
// A dialog's text that is left on screen after the turn ended (a reply quoting one, from a memo or a message) is no
// dialog: server.js asks codexTurnOver too, since a real approval only ever shows inside a running turn (the trust and
// hook prompts at start come before any turn).
const DIALOGS = [
  /Would\s*you\s*like\s*to\s*(run|make|grant|send|allow)\b/i,
  /Approve\s*app\s*tool\s*call\?/i,
  /\bTrust\s*and\s*continue\b/i,
  /\bHooks\s*need\s*review\b/i,
  /Press\s*t\s*to\s*trust\s*all/i,
  /enter\s*to\s*(confirm|select|submit)\b[^\n]{0,160}esc\s*to\s*(cancel|go\s*back)/i,
];
const FOOTER = /esc\s*to\s*(cancel|go\s*back)|Press\s*enter\s*to\s*(confirm|continue)|Press\s*t\s*to\s*trust|1\.\s*(Yes,\s*proceed|Trust\s*and\s*continue)/i;
const MOVED_ON = /esc\s*to\s*interrupt|Conversation\s*interrupted/gi;
export function codexAsking(tail) {
  const s = String(tail || '');
  let cut = 0;
  for (const m of s.matchAll(MOVED_ON)) cut = m.index + m[0].length;
  const rest = s.slice(cut);
  return FOOTER.test(rest) && DIALOGS.some(re => re.test(rest));
}

// talk.js openToolCall's twin: whether the rollout's newest tool call has no output yet. Codex writes a call's line as it
// starts and its *_output as it returns, and while its PermissionRequest hook asks the phone it shows no dialog at all
// ("Running hook", live 0.157), so this is how the relay knows the turn waits on a person. A long call in code mode comes
// back every ~30 s as "Script running" and the model `wait`s on it again: a gap of a second or two between, which is
// all the relay's clock runs in. A turn that ended, or a new one, has nothing open; a call from before `since` (the
// session now running) is one a crash left behind.
export function openCodexCall(path, since = 0, tailBytes = 512 * 1024) {
  const open = new Map();
  for (const e of tailEntries(path, tailBytes)) {
    const p = e?.payload;
    if (e?.type === 'event_msg' && ['task_started', 'task_complete', 'turn_aborted'].includes(p?.type)) open.clear();
    if (e?.type !== 'response_item' || !p?.call_id) continue;
    if (CALL_LINES.has(p.type)) open.set(p.call_id, Date.parse(e.timestamp) || Infinity);
    else if (p.type === 'function_call_output' || p.type === 'custom_tool_call_output') open.delete(p.call_id);
  }
  return [...open.values()].some(at => at >= since);
}

// Whether the rollout's newest turn has ended (task_complete or turn_aborted after its task_started). A session with no
// rollout yet, or one mid-turn, has not.
export function codexTurnOver(path, tailBytes = 64 * 1024) {
  let over = false;
  for (const e of path ? tailEntries(path, tailBytes) : []) {
    const t = e?.type === 'event_msg' ? e.payload?.type : null;
    if (t === 'task_started') over = false;
    else if (t === 'task_complete' || t === 'turn_aborted') over = true;
  }
  return over;
}
