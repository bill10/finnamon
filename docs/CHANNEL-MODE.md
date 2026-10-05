# Who answers Telegram: daemon, session, or channel

| `finnamon channel …` | Reads the bot | Answers in | One conversation with the dashboard? | Needs |
|---|---|---|---|---|
| `session` (**recommended; the default for a new install** with a bot and the dashboard) | the daemon | the dashboard's intercom session | yes | the dashboard (`finnamon install` with Node) |
| `on` (`channel`, an option) | Claude Code's Telegram plugin, in the dashboard's session | the same | yes | the plugin, Bun, the token copied to `~/.claude/channels/telegram/.env` |
| `off` (`daemon`, the legacy mode) | the daemon | its own `claude -p --resume` session | no: the chat has its own thread | nothing more |

An existing install keeps the mode it has; `finnamon update` mentions `finnamon channel session` once if it is on `channel` or `daemon`.

## Session mode: one conversation, no plugin

```
finnamon channel session      # the daemon keeps polling; prints the next step
finnamon update --no-pull     # restarts the dashboard, whose session now takes the chat
```

The daemon stays the bot's only `getUpdates` reader, with every check it has in daemon mode (household chat only,
enrolled members only, bots and service messages dropped, the enrolment code dance still works). Each message goes to
the dashboard (`POST /api/telegram/turn` on loopback, with the key from `~/.finnamon/web-token` as a bearer), which types
it into the live intercom session as a bracketed paste, `[telegram · jane; replying to alert 1841] it is normal`, control
characters stripped as Talk strips them, and reads the reply off the session's own transcript at the end of the turn.
The daemon sends it to the chat as in daemon mode (a chart path becomes a photo, `NO_REPLY` sends nothing). One
message at a time, in order; the dashboard shows each turn in the intercom like any other. If a turn runs past
`claude_timeout_seconds` (default 120) the chat is told the answer will be in the intercom; if the dashboard is down or its
session is still starting, the chat is told to resend. A dashboard started in another mode refuses the hand-off until it is
restarted. `finnamon doctor` shows the mode, and fails it when session mode has no dashboard to type into. Switching is
safe in any direction: from `channel`, the plugin's last batch is not answered twice (`inbound_off_at`), and only one
program reads the bot in either of the daemon-polled modes.

The session runs in ask mode, so anything off the allow list, a web search or fetch included, asks first. During a
phone turn the request also goes to the chat (`finnamon hook permission`): one line naming the command, the site or the
path, with Allow and Deny buttons that only household members in that chat can press. The first answer, phone or
dashboard, wins; ten minutes with neither is a deny, and the chat is told.

What differs from `channel`: who may talk is decided in code, not by the plugin's allow list, and a reply to an alert
carries its id. What differs from `daemon`: what is said in the chat is in the dashboard's thread and vice versa, and a
dashboard restart mid-turn loses that turn (the chat is told to resend).

# Experiment: let Claude Code run the Telegram side

Claude Code has its own Telegram channel plugin (research preview). With it, the bot's messages land in a
Claude Code session you keep open, and Claude replies through the bot; Finnamon's daemon then only syncs,
detects, and sends alerts. One Telegram bot allows one reader, so you switch, not stack:

```
finnamon channel on                                   # daemon stops polling Telegram within a minute; prints these steps
finnamon install                                      # registers the plugin for ~/.finnamon/assistant (so does `update`); by hand:
                                                      #   cd ~/.finnamon/assistant && claude plugin install telegram@claude-plugins-official --scope local
# write the bot token (the one in ~/.finnamon/secrets.toml) by hand, mode 0600, never through a Claude session:
#   ~/.claude/channels/telegram/.env  ->  TELEGRAM_BOT_TOKEN=...
cd ~/.finnamon/assistant && claude --permission-mode dontAsk --channels plugin:telegram@claude-plugins-official --disallowedTools WebSearch WebFetch
                                                      # keep running (tmux); no web: the session reads bank memos unattended
                                                      # with the web dashboard installed, skip this: its intercom session attaches the channel
  /telegram:access pair <code>                        # after DMing the bot once
  /telegram:access group add <group id> --no-mention --allow <user ids>
  /telegram:access policy allowlist
finnamon owner add jane --user-id <telegram user id>  # in another terminal (not inside the Claude session): who is speaking
```

Use the same group the daemon already sends alerts to (`finnamon status` shows its chat id); alerts keep going
there regardless. `finnamon channel off` (stop the channel session first) goes back to the daemon's own
conversation loop. If two programs end up reading the bot, the daemon says so in the chat after a few
conflicts and `finnamon status` shows `inbound_conflict_at`. If that session stops reading the chat for any other
reason, the daemon notices within a few minutes and tells you in the chat itself (it can still send), and the
dashboard's status pill says so too; `finnamon update --no-pull` restarts it.

While channel mode is on, **start any other `claude` in `~/.finnamon/assistant` with `--strict-mcp-config`**. The
plugin is installed against that directory, so a second session there starts a second copy of its server and
the channel session's copy dies about a second later with no reconnect: the chat goes quiet, while that
session keeps answering the dashboard normally. Restarting the dashboard (`finnamon update`, or the web job)
brings it back. Everything Finnamon starts by itself (triage, the conversation worker, `import --browser`,
the eval lane) already passes the flag. (A checkout where the plugin was registered before the assistant moved
has the same hazard until `claude plugin uninstall telegram@claude-plugins-official --scope local` is run there.)

What channel mode gives up: the daemon drops messages from anyone not in the
owners table before Claude sees them, but in channel mode the plugin's allow list decides who gets through
and the assistant is *told* to refuse strangers (the skill says an unknown id gets no state changes), which
is a prompt, not code. What stays in code: a hook (`finnamon hook reply-guard`, wired in the assistant's `.claude/settings.json`)
blocks the plugin's `reply` tool unless the chat is the household group or a member's private chat and any
attachment is a chart under `~/.finnamon/charts/`. The web is off in that session too, the same
`--disallowedTools WebSearch WebFetch` every `claude -p` Finnamon starts carries, because it reads bank memos with
nobody watching; so a property lookup ("what's my house worth?") happens from the dashboard while Telegram runs
through the daemon, or from `claude --strict-mcp-config` in `~/.finnamon/assistant`, and the assistant says so when asked.
Conversation isn't written to the `feedback` table, and
the bot token lives in two places (rotate both). Treat the experiment as one for a household you trust to be
alone in the group.

`--permission-mode dontAsk` matters: without it the plugin relays every non-allow-listed command to paired
phones as Allow/Deny buttons, and a tap would approve whatever a prompt-injected assistant proposed; with it
the session behaves like the headless daemon (allow list only). What you gain: Claude Code owns the session
(compaction, resume). What you lose for now: the channel doesn't say which alert a reply quoted, so "it is
normal" applies to the latest alert unless you say which. Requires Bun (`finnamon install` puts it on the jobs' PATH and warns when it cannot find it) and a claude.ai (Pro/Max) or Console
login.
