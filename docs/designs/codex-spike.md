# Codex spike (card 1 of 6)

This spike settles the open points from the scope card ("Scope Codex support for finnamon", job-1791238571741-0d2527) on a real
Codex CLI, **codex-cli 0.157.0**, on 2026-10-05. The recorded payloads are in `tests/fixtures/codex/`.

## How it was run

- **Codex home.** A scratch `CODEX_HOME` held a copy of `~/.codex/auth.json`, deleted afterwards. The login had last
  refreshed 3 days earlier, so no token refresh could rotate the original.
- **Config.** The rest came from a config written in that scratch home:
  - hooks inline in `config.toml`;
  - an execpolicy file in `$CODEX_HOME/rules/`;
  - a one-tool stdio MCP server;
  - a trusted project.
- **Hook trust.** Pinned the way card 2 will do it:
  - `codex app-server` → `hooks/list` gave each hook's `key` and `currentHash`;
  - they were written back as `[hooks.state] "<key>" = { trusted_hash = "sha256:…" }`.
  - The keys look like `<config path>:pre_tool_use:0:0`.
- **Runs.** Every run was `codex exec --json --skip-git-repo-check -C <scratch>/work` with a toy prompt.
- **`--dangerously-bypass-hook-trust`.** One run went out with this flag. The model was at capacity, so it ran no tool; after that the
  flag was dropped.
- **Cost.** Four runs did work, at about 100k input tokens each, mostly cached.
- **Isolation.** Nothing was written to `~/.codex` or `~/.finnamon`.

## Findings

### 1. Does an execpolicy `prefix_rule(decision="allow")` run inside the sandbox or outside it?

The answer depends on how the sandbox is configured.

- **Legacy `sandbox_mode = "workspace-write"`: outside.**
  - Setup: a rule allowing `touch`.
  - `touch <dir outside the workspace>/by_rule` succeeded.
  - `cp /dev/null` into the same directory failed with "Operation not permitted".
- **A permission profile (`default_permissions = "<name>"`, `extends = ":workspace"`): inside.**
  - A rule-allowed `touch` outside the workspace failed, the same as the unruled `cp`.
  - A rule-allowed `head -1 secret/secret.txt` (`codex execpolicy check` confirms the rule matched) was refused by the profile's `deny`.

**For the plan:**
- Card 2 needs a permission profile, because only a profile can deny the secrets.
- Under a profile, an allow rule only skips the approval; the sandbox still applies.
- So a `finnamon` rule would run the CLI unable to reach its own database. **The `finnamon(argv)` MCP tool is required, not optional.**
- Card 2 must never generate the legacy `sandbox_mode`: under it, an allow rule escapes the sandbox completely.

### 2. Does a permission-profile filesystem `deny` block `apply_patch` and image reads, or only shell?

It blocks all three.

| Tool | What happened |
|---|---|
| Shell | `cat secret/secret.txt`: Operation not permitted |
| `apply_patch` | An update of the denied file failed with `apply_patch verification failed: Failed to read file … Operation not permitted` |
| `view_image` | On the denied directory: `unable to locate image … Operation not permitted`. The same tool read an image outside the denied directory. |

**For the plan:**
- The PreToolUse `secret-guard` becomes a second layer for these tools, not the only one.
- Not tested: an `apply_patch` "Add File" into a denied directory. Card 3 covers it with its live denied-write check.

### 3. What `tool_name` and `tool_input` does Codex send to a PreToolUse hook?

Recorded in `tests/fixtures/codex/hooks/`. Every payload carries:
- `session_id`
- `turn_id`
- `transcript_path`, the rollout file
- `cwd`
- `hook_event_name`
- `model`
- `permission_mode`: `"bypassPermissions"` under exec
- `tool_name`
- `tool_input`
- `tool_use_id` (PreToolUse only)

| Tool | `tool_name` | `tool_input` |
|---|---|---|
| Shell | `Bash` | `{"command": "<string>"}`. The same shape as Claude's Bash, not an argv list. |
| `apply_patch` | `apply_patch` | `{"command": "*** Begin Patch\n*** Add File: patched.txt\n+hello\n*** End Patch"}`. The paths are inside the patch text, relative to `cwd`. |
| Image | `view_image` | `{"path": "<absolute path>"}` |
| MCP tool | `mcp__<server>__<tool>` | The tool's arguments, for example `{"argv": ["finnamon", "status"]}` |

**For the plan:**
- `approval.protected_path` and `denied_by` already handle `Bash` with a string command, so they apply to Codex shell calls as they are.
- Card 3 adds two cases:
  - `apply_patch`: parse the `*** (Add|Update|Delete) File: <path>` lines;
  - `view_image`: `path`.

### 4. Does a PreToolUse hook that exits 2 block the call, and does a PermissionRequest `deny` hold?

- **Exit 2 blocks.** The call failed with `Command blocked by PreToolUse hook: <stderr>`, and the model was told why.
- **Exit 1 (a crashing hook) does not block: it fails OPEN.** `echo CRASHME > crashed.txt` ran.
  - So on Codex the guard must catch its own exceptions and exit 2.
  - Today `secret-guard` relies on Claude's behaviour; on Codex, a traceback lets the call through.
- **A PermissionRequest `deny` holds.**
  - Hook output: `{"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": {"behavior": "deny", "message": …}}}`.
  - The MCP call ended `status: failed` with the message as its error.
  - The hook fired under `exec` for an MCP tool with no `approval_mode`, although exec's `permission_mode` is `bypassPermissions`.

**For card 4:**
- Give the `finnamon` tool `approval_mode = "approve"`, or headless runs will ask the hook about every call.
- The hook must answer "no decision" for an unattended run, as `approval.ask` already does for `FINNAMON_FROM_AGENT`.

### 5. The rollout file

- **Path:** `$CODEX_HOME/sessions/YYYY/MM/DD/rollout-YYYY-MM-DDTHH-MM-SS-<thread id>.jsonl`.
  - The time is local.
  - The thread id is a UUIDv7.
  - It is the same id as `exec --json`'s `thread.started.thread_id` and every hook payload's `session_id`.
  - Hooks also get `transcript_path`, so `approval.ask` can read the rollout without searching for it.
- **Format: plain JSONL.**
  - The hook payload pointed at the `.jsonl` while the turn ran.
  - None of the 165 rollouts in this machine's `~/.codex/sessions` is `.zst` (listed only, not read).
- **Each line:** `{timestamp, ordinal, type, payload}`. The types:

| `type` | `payload` |
|---|---|
| `session_meta` | `session_id`, `id`, `cwd`, `cli_version`, `originator` (`codex_exec`), `source` (`exec`), `model_provider`, `base_instructions`, … |
| `event_msg` | One of: <ul><li>`task_started`</li><li>`item_completed {item}`, where `item.type` is `UserMessage`, `AgentMessage`, `CommandExecution` (command as an argv list, `exit_code`, output), `FileChange` or `McpToolCall`</li><li>`token_count`</li><li>`task_complete {last_agent_message}`</li></ul> |
| `response_item` | One of: <ul><li>`message` with `role` `developer`/`user`/`assistant` and `content[].type` `input_text`/`output_text`; the assistant's message has `phase` `commentary` or `final_answer`</li><li>`custom_tool_call` / `custom_tool_call_output`</li></ul> |
| `world_state`, `turn_context`, `token_usage_record` | Bookkeeping |

- **0.157 runs tools in code mode.** Each tool call is a `custom_tool_call` named `exec`, whose input is a JavaScript snippet (`tools.exec_command({cmd: …})`).
  - So a reader of `response_item`s cannot see which tool ran.
  - **Read the `event_msg` `item_completed` items instead.**
- **`exec --json` events** (`tests/fixtures/codex/exec.jsonl`):
  - `thread.started`
  - `turn.started`
  - `item.started` / `item.completed`, with items `agent_message`, `command_execution`, `file_change` and `mcp_tool_call`
  - `turn.completed {usage}`
  - The first `agent_message` is commentary; the last one is the reply. `agent_runner._codex_result` parses it.

### 6. Can a Finnamon-owned `CODEX_HOME` share the person's existing login?

Yes, with a symlink, and that is all it needs.

**Where Codex keeps credentials:**
- Source: `codex-rs/login/src/auth/storage.rs` at `rust-v0.157.0`.
- `cli_auth_credentials_store` defaults to `file`, which is `$CODEX_HOME/auth.json`.
- `keyring` keeps the login in the OS keychain, under the service `"Codex Auth"`. The account is `cli|` plus the first 16 hex digits of the sha256 of the canonical `CODEX_HOME`. So a keychain login belongs to one `CODEX_HOME` and is never shared between homes, link or no link.
- `auto` uses the keychain when one is available, so on macOS it means the keychain.
- The owner's config sets neither, and `security find-generic-password -s "Codex Auth"` finds no item. So on this machine the login lives only in `~/.codex/auth.json`.

**The test:** a scratch `CODEX_HOME` holding only a `config.toml`, a hook, a skill, and `auth.json` as a symlink to `~/.codex/auth.json`.
- `codex login status` printed "Logged in using ChatGPT".
- One `codex exec --json` turn ran.
- The scratch PreToolUse hook fired (trust pinned by hash).
- The scratch skill `finnamon-spike` was in the session.
- The `context7` and `shadcn` MCP servers from the owner's `~/.codex/config.toml` were absent from the rollout.
- So the symlink shares only the login.
- `~/.codex/auth.json` came out unchanged: same sha256, modification time, change time and link count.
- Afterwards the scratch home was deleted; it held only the link, never a copy.

**A token refresh writes through the link, so both homes stay valid.** I read this from the source rather than forcing a refresh: a live refresh would rotate the owner's real refresh token.
- `FileAuthStorage::save` writes in place, with `OpenOptions::new().truncate(true).write(true).create(true)` and then `write_all`. It does not write a temporary file and rename it over `auth.json`. On Unix, opening a symlink follows it, so `~/.codex/auth.json` itself gets the new tokens and the link stays a link.
- `persist_tokens` loads the file, swaps in the new tokens, sets `last_refresh`, and saves.
- Before refreshing, `AuthManager::auth()` reloads from disk and refreshes only if the on-disk auth is unchanged (`TOKEN_REFRESH_INTERVAL` is 8 days). If the person's own `codex` has just refreshed, the Finnamon home picks up those tokens instead of refreshing again.
- On a 401, `UnauthorizedRecovery` also reloads from disk first, as long as the account id matches.
- This is the same mechanism several `codex` processes already rely on when they share `~/.codex` (Agent 007's workers do).
- What is left is no worse than two processes on one home today:
  - the refresh lock is per process (a `Semaphore`) and there is no file lock;
  - the truncate-and-write is not atomic.

**Constraints for card 2:**
- **Never run `codex logout` under Finnamon's `CODEX_HOME`.** Its `remove_file` would only remove the link, but logout also revokes the tokens (`auth/revoke.rs`). That would log the person out everywhere.
- **The link works only with the file store.** If `~/.codex/auth.json` is missing, because the person has no Codex login or uses `keyring`/`auto`, fall back to a separate `codex login` under Finnamon's home.
- Pin `cli_auth_credentials_store = "file"` in Finnamon's `config.toml`. Otherwise an `auto` default would let a later `codex login` there go to the keychain, and nothing would be shared.

**Not tried: a hard link.** Making one changes the original inode's change time and link count, which counts as modifying `~/.codex`. It would also work only until anything replaced the file, so a symlink is the right choice.

**The interactive `codex` (TUI) has no `--ignore-user-config`**; only `exec` has it. The equivalent is the same as above: `CODEX_HOME=<finnamon's>` plus `-c` flags. With the symlinked `auth.json`, the TUI shares the login the same way.

**Seen in passing:**
- The `~/.agents/skills` leak (below) showed up again in this run.
- Asked which MCP servers it had, the model named only `codex_apps`, Codex's built-in ChatGPT-apps server. Card 2 should check whether that can be turned off. Not verified in the rollout.

## Also found

- **The seal has a hole:** with a scratch `CODEX_HOME`, Codex still loaded the 42 personal skills in `~/.agents/skills`.
  - Codex reads that user skill root regardless of `CODEX_HOME`.
  - Card 2 must close it, with a config switch if there is one, or else a `HOME` the session cannot see past. Until then, a Codex household's assistant would see the person's own skills. The fixture has the list trimmed.
- **A capacity error** is a `task_complete` whose `error.message` reads "Selected model is at capacity. Please try a …".
  - `classify_error` (card 4) should file it, and the usage-limit wording, as transient rather than "login expired".
- **The default model** was `gpt-6-luna`.
- **Usage:** `token_count` events carry `rate_limits`, showing the plan's percentage used. Doctor could report it later.

## Card 2 notes (Codex 0.157)

- **The skills leak has no config switch.** `skip_host_skill_discovery` is listed as under development and changes nothing;
  `[[skills.config]] path = … enabled = false` disables only the skill files it names, not a root. Codex takes the user
  root from `HOME`, so every Codex process Finnamon starts gets `HOME = FINNAMON_HOME/codex/home`, an empty directory
  (`codex.env()`); `shell_environment_policy.set` hands tool commands the real `HOME` (and `FINNAMON_HOME`) back.
  `skills/list` under that env returns only the six system skills; doctor runs it and flags any user or admin skill.
- **`codex_apps` is `features.apps`.** `apps = false` turns the built-in ChatGPT-apps server off; `browser_use`,
  `browser_use_external`, `in_app_browser` and `computer_use` go off with it.
- **A profile makes the workspace writable** even when the directory itself is set to `read`; `":workspace_roots" = "read"`
  is what makes the assistant directory read-only. `codex sandbox -P finnamon -C <dir> -- <cmd>` runs a command under the
  profile with no model call; `tests/test_codex.py` uses it to check the denies.
- **Hook keys and hashes.** `hooks/list` needs stdin kept open until it answers (the server exits on EOF). The hash covers
  the hook's definition, not the `[hooks.state]` table, so pinning is: write the hooks, ask, write the hashes. A hook's
  `timeout` is in seconds (`timeoutSec` 660; the default is 600).
- **The refresh write-through stays a source-code argument** (finding 6): no throwaway ChatGPT login was available to force one.

## Card 3 notes (Codex 0.157)

- **The MCP server runs outside the sandbox and reaches the database:** live, under the generated config in a scratch
  `FINNAMON_HOME`, `finnamon(argv=["finnamon", "status"])` exited 0 with the household's status, and `["finnamon", "sync"]`
  came back refused from the deny list. A Bash `cat` and an `apply_patch` update of `secrets.toml` were both stopped by the
  PreToolUse `secret-guard` ("Command blocked by PreToolUse hook"), before the profile's deny was reached.
- **Config keys** (from `codex mcp get`): `enabled_tools`, `env_vars` (variables passed through from Codex's own environment;
  an MCP server otherwise gets only a short default set), and `[mcp_servers.<name>.tools.<tool>] approval_mode`
  (`auto`, `prompt`, `writes`, `approve`).
- **`env_vars` reaches the server:** a live `codex exec` with `FINNAMON_TRIAGE=1` in Codex's own environment had the tool
  refuse `alias --list` and `detect --draft` from triage's list. The tool's optional `stdin` argument carries what a
  `-` argument reads (`triage set … -`, `detect --draft -`), since there is no shell to pipe a heredoc. `tool_timeout_sec`
  sits above the tool's own 600 s, so Codex never retries a write that is still running.
- **`--ignore-user-config` was not used for the live runs:** it skips `$CODEX_HOME/config.toml`, which is the generated
  config under test. A scratch `CODEX_HOME` already keeps `~/.codex/config.toml` out.
- **The household's AGENTS.md refuses a test prompt** that asks it to read `secrets.toml` (no tool call at all); the guard runs
  were made from a scratch work directory under the same `CODEX_HOME`.

## Card 4 notes (Codex 0.157)

- **Live, under a scratch `FINNAMON_HOME` sharing the login:** `triage.run_if_needed` on a Codex household ran
  `codex exec --json … -- '$triage'`; the skill called the `finnamon` tool (`alerts --untriaged`, three `query`s) and wrote
  its verdict with `triage set t1 promote high -` and the sentence in `stdin`. One `exec resume <thread id>` continued the
  same thread. Both recorded: `tests/fixtures/codex/exec-triage.jsonl`, `exec-resume.jsonl`. About 90k input tokens each,
  mostly cached.
- **`exec resume` has no `-C`**; the process's cwd is the assistant directory. A gone thread fails before any model call:
  `thread/resume failed: no rollout found for thread id …` on stderr, exit 1 (`exec-resume-gone.stderr`), filed as
  "session lost" so the daemon starts a fresh thread.
- **Read-only:** `-c permissions.finnamon.extends=":read-only"` keeps the profile's denies and makes even `$TMPDIR` and
  `/tmp` unwritable (`codex sandbox -P finnamon` checked it). `-s read-only` would be the legacy `sandbox_mode`, never used.
- **The triage deny list blocks `finnamon normal --list`**, which the skill asks for (`normal *` is on it); the run said so
  in its verdict. The same holds on Claude (`TRIAGE_DISALLOWED`); left for a follow-up.
- **The lock also pins `default_permissions` and `features.apps=false`** (a config.toml selecting another profile would
  otherwise win), and the harness check runs `config_problems()`. A bad `-c` value fails before any model call with
  `Error loading config.toml: …` (filed as "bad config"); the real lock parsed and reached the thread lookup.
- **`exec resume` honours the lock:** live, a fresh `exec` and its `exec resume` were each asked to
  `touch "$TMPDIR/…"` through the shell; both were refused ("Operation not permitted") and no file appeared, so a resumed
  thread does not fall back to the config's `:workspace` profile.
- **A project `.codex/` in the assistant directory is a harness problem** (`config_problems`): Codex would layer it over
  the sealed config for a trusted project, past the command-line lock.
- **The login is not a harness problem:** the daemon refuses to start on one, and a lapsed shared login must not stop syncing.
- **Not recorded live:** a failed turn. `exec-failed.jsonl` is written by hand from the spike's capacity wording.

## What changes in cards 2–6

- **Card 2 (bundle, `CODEX_HOME`, install/doctor):**
  - **Share the login instead of a second `codex login`:** symlink `~/.finnamon/codex/auth.json` to `~/.codex/auth.json` and pin `cli_auth_credentials_store = "file"` (finding 6). A refresh writes through the link.
  - Doctor checks that the link exists and is intact.
  - A separate `codex login` under Finnamon's home is the fallback, only when there is no `~/.codex/auth.json` (no Codex login, or a keychain one).
  - Never run `codex logout` there: it revokes the shared tokens.
  - Generate a permission profile (`default_permissions`, `extends = ":workspace"`) and never `sandbox_mode`.
  - Pin hook trust with `hooks.state` hashes from `app-server` `hooks/list`.
  - Close the `~/.agents/skills` leak.
  - Doctor checks the version is at least 0.157.
- **Card 3 (tool surface and guards):**
  - The `finnamon(argv)` MCP tool is **required**, because finding 1 rules out using allow rules for this.
  - Prove live that the MCP server reaches the database under the profile. MCP servers are expected to run outside the sandbox; the spike's echo tool touched no files.
  - `secret-guard`: exit 2 on any internal error, and add the `apply_patch` and `view_image` cases.
  - Port the guard tests onto the recorded payloads.
- **Card 4 (headless):**
  - `codex exec --json` and `exec resume <id>`. `_codex_result` is written and tested against the recording; `_codex_cmd` is the stub to fill.
  - `approval_mode = "approve"` for the `finnamon` tool.
  - Codex wording in `classify_error`.
- **Card 5 (dashboard):**
  - The session id comes from the newest rollout whose `session_meta.cwd` is the assistant directory, or from a hook's `session_id`.
  - `intercomSession` already holds `{id: null}` for a CLI that names its own session.
  - The Codex turn reader works off `event_msg` items. Done is `task_complete`; the reply is `last_agent_message`; progress is the `CommandExecution` items.
  - Lift the `settings set assistant codex` refusal (`store.validate_setting`; card 4 already dropped `agent_runner.CODEX_PENDING`).
  - **Mark the Codex session as an agent.** Inside the dashboard's Claude session, the human-only gates are held only by the `CLAUDECODE` that Claude sets on its own tool calls (`web/server.js` `env()` strips the Finnamon markers). A Codex PTY sets nothing, so every human-only verb, `settings set assistant` included, would be open to it. Set `FINNAMON_FROM_AGENT` on the session itself, through `shell_environment_policy.set` and the MCP server's `env`, before this card lands.
- **Card 6:** unchanged.
