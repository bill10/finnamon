# Changelog

All notable changes to Finnamon are recorded here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions are `MAJOR.MINOR.PATCH.MICRO`.

## [0.41.1.0] - 2026-10-05

### Added
- **`finnamon doctor` knows WSL.** It checks that systemd is PID 1 and repeats the keep-alive and dashboard-address guidance, since Windows-side settings cannot be read from inside Ubuntu.
- Evals for normal / dismiss / undo, category and alias, roundup replies, `/triage` and `detect --draft`.

### Changed
- **INSTALL says how to keep WSL awake and how to get an alarm when it stops** (`vmIdleTimeout`, a Task Scheduler check), and that spoken replies use the browser's speech on Linux and WSL.
- The assistant's notes now say which heredocs work: plain text for `triage set -` and `detect --draft -`; only JSON is refused.

## [0.41.0.0] - 2026-10-05

### Added
- **`finnamon init --plaid` replaces the Plaid keys** (Sandbox → Production): it prompts without echoing the secrets, keeps what you leave blank, verifies, and skips the other steps. `doctor`, link errors and INSTALL.md point to it; linking always uses Production.
- **A manual account with no balance says so, and takes one.** The dashboard shows "no balance yet: enter it" and saves the figure in place; `finnamon account balance "<name>" <amount>` does the same.
- **Escape closes the intercom** instead of interrupting Claude's answer; a Stop button (and Ctrl-C) still interrupts.

### Changed
- **Detector drafts live in `~/.finnamon/detector-drafts/`**, not the code checkout; drafts an older version left in `finnamon/detectors/pending/` move over on the next `update`, `detect --draft` or `--review`.
- **`finnamon update` works for a household that skipped scheduling**, runs `npm ci` in `web/` when the dashboard's package files changed, and works from a git worktree. `help update` no longer claims `--dry-run` pulls and migrates.
- **Setup hints name the assistant's directory** (`cd ~/.finnamon/assistant && claude --strict-mcp-config`) or the dashboard's intercom, not a bare `claude`.
- **A non-member writing in the household chat gets one reply a day** naming `finnamon owner add <name> --user-id <id>`.

### Fixed
- **`link` with no keys, `link --update <unknown>` and `owner add` with no bot print one line and the next step**, not a traceback; `link --update` also takes an institution name when only one bank has it.
- **Link account in the dashboard checks the keys first** (and says the demo cannot link banks) before asking whose bank it is.
- **A Bank of America checking export imports**: a summary block above the header is skipped.

## [0.40.0.0] - 2026-10-05

### Added
- **Subscription price changes alert directly: "Netflix went from $15.49 to $17.99".** A new `recurring_price` rule compares a subscription's latest charge with the two before it (which must agree, so a bill that varies every month stays quiet). The old check against Plaid's average almost never fired, because the average already includes the new price. "It's normal" on one acknowledges that subscription only.
- **The dashboard's alert list shows "Show N more"** when more than 8 are open, instead of stopping at 8.

### Changed
- **Three identical charges are one alert: "Charged 3 times".** A run of repeats (each within the duplicate window of the one before) is one alert on its latest charge, not one per pair (three Shell charges sent two messages). A run of three or more still inside the window when you update may be told once more, as one message.
- **Low balance alerts once per dip,** not every week while the balance stays low; it alerts again only after the account recovers above the threshold and drops again.
- **A bank that stops syncing is one alert per problem, not one a day, and it closes by itself** once the bank syncs again (or a newer problem at that bank replaces it; the dashboard says "Replaced by a newer alert"). "Hasn't synced" and sync errors now say what to do: `finnamon doctor` on the Finnamon box; a bank that wants a login offers the re-login.
- **Card payments and transfers between your own accounts are no longer anomaly candidates** (both sides of every card payment used to come up as "can't tell what this is" each month), and two charges at a new merchant on one day are one "first time here", not two.

### Fixed
- **`link --remove` closes that bank's alerts,** sent ones included, so they leave the dashboard and `alerts --open`.

## [0.39.0.0] - 2026-10-05

### Added
- **`finnamon owner rename` and `owner remove`** (a person at a terminal; on the assistant's deny list). Rename moves every account, bank and merge record to the new name; remove refuses while the member still owns an account.
- **Dashboard: "Same account seen twice?"** in Accounts marks two copies of a joint account as one (with an Undo), and Add a manual account has an owner field (members or Joint). Link account offers Joint.
- **A joint account linked from the dashboard or Telegram is now marked joint automatically**, the same way the terminal's yes does it, so it is no longer counted twice. A re-link over a broken login is still left for you to decide.

### Changed
- **A mistyped owner is refused** (`account add --owner`, `link --owner`, `account owner`) and lists the members, instead of quietly becoming a new household member. Names match without regard to case. `joint` is allowed for accounts and reserved as a name.
- **`account merge` checks first**: different account types or very different balances are refused unless `--force`. `account unmerge` gives both accounts their own owners back.
- **The assistant may add a joint manual account**; `account owner` is human-only.
- **The dashboard lists a bank's Plaid link and its by-hand accounts as separate groups**, each with its own sync time, so a stale Plaid sync is no longer hidden.

## [0.38.0.0] - 2026-10-05

### Added
- **A budget can cover several categories and merchants.** `finnamon budget set dining 400 --category restaurants --category "fast food" --category coffee`, or `--merchant "Seattle Public Utilities" --merchant Recology` for a "water and trash" budget; each flag repeats and a charge several of them match counts once. `finnamon budget` keeps its `category` field and adds `categories`, `merchants` and `fixed`; the dashboard's budget card shows what each one covers. Existing budgets move over as they are.
- **`budget set … --fixed`** marks a budget that is one bill a month (childcare, the HOA): it is compared to its limit and never projected.
- **A merchant budget counts every charge of that merchant**: `--merchant` takes any name its charges show (as `finnamon category` does), typed in any case, covers the charges Plaid sent with and without its merchant id, refuses a name no charge has with what you probably meant, and says how many past charges each one matches.

### Fixed
- **A mortgage budget counts the mortgage.** It reported $0 after the payment posted; now the checking-side payment counts once (the loan account's side never doubles it), even when the bank filed it as a transfer, it is never projected, and `budget suggest` shows it as the mortgage. A budget on `LOAN_PAYMENTS` (all loan payments) now counts the mortgage too. Transfers and card payments still never count.
- **Pace no longer multiplies a monthly bill.** A charge of a monthly or yearly bill Plaid sees as recurring (the same merchant, within 25% of its amount) counts once and only the rest of the month's spending is projected, so a $1,500 daycare bill on the 3rd no longer reads as $15,000 on pace or raises a pace alert.
- **Changing a limit on the dashboard keeps the budget's categories.** It used to re-derive the category from the budget's name.

## [0.37.0.0] - 2026-10-05

### Fixed
- **Net worth history now counts property, so its last point equals `finnamon networth`.** Properties keep no value history, so every point uses the current value and says so (`property_basis`). The dashboard's chart no longer adds property a second time.
- **The dashboard's Import CSV shows a preview before saving.** Choosing a file shows the first five rows with whether each reads as spending or money in, and a "Flip signs" toggle for card exports that list purchases as positive. A file that parses to no rows shows an error instead of a success toast.

## [0.36.1.0] - 2026-10-05

### Fixed
- **Recategorizing or calling a merchant normal by any name you see now works, and never fakes success.** `finnamon category` and `finnamon normal "<merchant>"` take the display name, the raw bank text, an alias's name or its pattern, and say how many charges the rule covers. A name that matches no charge (a typo, a partial name) is refused with the merchant it probably meant ("0 charges match 'Amazn'; did you mean Amazon?"), a name shared by several merchants lists them, and no rule is written that covers nothing. `normal --kind` refuses a kind no rule can quiet and lists the valid ones.
- **"It's normal" works on low-balance, budget and stale-sync alerts.** The dashboard button and the assistant used to error; now it resolves that alert (Undo reopens it), writes no rule, and says when it will alert again and the fix to offer: a lower threshold, a new budget limit, or reconnecting the bank.

## [0.36.0.0] - 2026-10-05

### Changed
- **The dashboard's assistant asks instead of refusing.** The intercom session now runs in Claude Code's ask mode: the allow list still runs unasked and the deny list still wins (secrets, the database, the dashboard key, its own folder, every setup-changing command), and anything else, Python, another command, reading a file, shows an Allow / Deny dialog in the intercom. Channel mode is unchanged.
- **The web is back, one approved fetch at a time.** Web search and fetch are off the allow list, so each one asks and names the site, and the session that answers Telegram (`finnamon channel session`) no longer has them switched off. "What's my house worth?" works from the phone again. Unattended runs (triage, daemon-mode replies) still have no web.

### Added
- **The household's secret files are off limits to every tool, whoever approves.** The secrets, the database, the dashboard key, imported statements, the bot's token and the browser profiles are refused before any Allow / Deny is shown, for every tool (a shell command, a search, a file read), on the dashboard and the phone alike.
- **Permission prompts reach the phone.** When a Telegram message started the turn, the same request goes to the household chat: one line saying what the assistant wants (the command, the site, the path) with Allow and Deny buttons. The first answer wins, from the phone or the dashboard, and the other side is cleared. Only household members in that chat can press them. With no answer in 10 minutes the request is denied and the chat is told. Arrives with `finnamon update`, which installs the new hook and restarts the dashboard.

## [0.35.0.0] - 2026-10-05

### Added
- **Reconnect on the dashboard.** A bank that needs a re-login (or whose connection is about to expire) now has a
  Reconnect button on its alert, in place of "It's normal", and beside "needs a new login" in Accounts. It opens Plaid's
  login page for that bank in a new tab, with no trip through Telegram; a double click reuses the page it just opened.
  The alert on the page no longer says "Reply fix …"; the Telegram message is unchanged.

### Fixed
- **A finished re-login syncs right away.** After logging back in through Plaid (from the button or the chat's
  "fix Chase" link), the bank used to stay broken, and its alert open, until the next scheduled sync, up to 6 hours
  later. The daemon now notices within a minute, syncs that bank, resolves its alert as Reconnected, and says
  "Chase is reconnected." in the chat. A login page closed without logging in is dropped quietly.

## [0.34.0.0] - 2026-10-05

### Added
- **Delete a merchant's category rule, and list the rules.** `finnamon category <merchant> --clear` removes the rule so that merchant's charges fall back to the bank's category (it says what it removed, or that there was no rule); one-time `--tx` edits are left alone. `finnamon category --rules` lists every rule (merchant, category, when set, how many charges it covers) and the one-time edits. Ask the assistant "stop putting Costco in groceries" or "undo the Costco rule"; it no longer has to overwrite the rule with the bank's original category.

## [0.33.0.0] - 2026-10-05

### Added
- **Recategorize one transaction without touching the merchant.** `finnamon category --tx <transaction_id> <category>` is a one-time edit of that charge only (`--clear` undoes it); `finnamon category <merchant> <category>` stays the rule for every past and future charge of a merchant. The one-time edit wins over the rule in budgets, charts, spending totals and the detectors. Ask the assistant "that Costco charge was groceries" and it edits just that one; "Costco is always groceries" sets the rule; when it can't tell which you mean, it asks. An edit on a pending charge follows it when the bank posts it. The MCP `search_transactions` tool now returns `transaction_id`.

## [0.32.2.0] - 2026-10-05

### Fixed
- **One transaction is one alert, however many detectors fire on it.** An HSA deposit tripped both `no_source` and
  `unmatched_transfer` and the chat got the same line twice. Now every alert on one transaction goes out as one message
  (keeping the clearest reason), and all of them are marked sent together. A detector that fires later on a transaction
  already told folds into that message instead of sending another. An alert from a detector that recorded no
  transaction id is matched on account, date, amount and name.
- **Triage gives a transaction one verdict.** `finnamon triage set <key>` with any one candidate's key now stamps
  every candidate on that transaction, so one detector can no longer be promoted while the other is suppressed. A detector that
  fires after the transaction was judged (or resolved) takes that verdict (and that resolution).
- **The alerts list and the dashboard show one alert per transaction.** The others are listed under `folded`. Dismiss,
  `normal --alert` and Undo on it cover every alert on the transaction. A reply to the message counts as a reply to one alert.

## [0.32.1.0] - 2026-10-04

### Fixed
- **Telegram messages reach the intercom again in session mode.** The dashboard's intercom session now starts sealed
  (`--setting-sources project --strict-mcp-config`, the same seal every other `claude` Finnamon spawns carries) in session
  and daemon modes, so a Telegram plugin enabled in your Claude Code config no longer starts there and long-polls the bot
  beside the daemon (the "Another program is reading this bot's messages" 409 notice). Only channel mode loads the plugin.
- **`finnamon channel session` / `off` restart the dashboard themselves**, so the intercom picks up the new mode without a
  manual `finnamon update --no-pull`, and say so; without a dashboard installed they point at `finnamon install`.

## [0.32.0.0] - 2026-10-04

### Changed
- **Finnamon is now MIT-licensed.** `LICENSE` is the standard MIT text, `pyproject.toml` and `web/package.json` say MIT, the README comparison table and footer and the launch kit drop "licence TBD", and `THIRD_PARTY_NOTICES.md` lists the npm packages the Talk voice detector uses. Closes #108.

## [0.31.1.0] - 2026-10-04

### Changed
- **A tidier Settings window and header.** The Settings cog and the theme toggle are now a matched pair of outline icons,
  spaced as tightly as the health pill and Add account (phones keep 44 px targets; the space is padding inside them).
  Settings leads with the version and one update line with a status dot (up to date, version available, checking,
  updating, failed); Update appears only when there is something to install, Check for updates sits beside it, and
  Telegram, Remote access and Voice are a quiet list of label/value rows with a dot each.

## [0.31.0.0] - 2026-10-04

### Added
- **A Settings cog in the dashboard header, beside the theme button.** It shows the installed Finnamon version, a "Check for updates" button that always looks fresh, the result ("Up to date" or "Version X available" with what's new), and an Update button that runs the same detached `finnamon update` as before. It also lists Telegram mode, Remote access (on/off and the address) and Voice (whisper or browser speech), read-only. The "Update available" header pill is gone: a dot on the cog (labelled "Settings, update available") says an update is waiting. The demo shows "Demo household" and no Update.

### Changed
- **Update checks are much sooner.** The dashboard caches a check for 30 minutes instead of 6 hours, and a page load re-checks one older than 10 minutes, so a box several releases behind no longer sits unnoticed.

## [0.30.0.0] - 2026-10-04

### Changed
- **Telegram now shares the dashboard's conversation by default.** A fresh `finnamon init` with a bot token and the dashboard sets `inbound=session`: the chat and the dashboard are one conversation, with no plugin. Existing installs keep their mode; `finnamon update` mentions `finnamon channel session` once if you are on `channel` or `daemon`. `finnamon doctor` marks session "(default)", and the docs and `finnamon help channel` call `daemon` the legacy mode.

## [0.29.1.0] - 2026-10-04

### Changed
- **Talk to Finnamon only speaks progress lines that sound like sentences.** A line is read aloud only when it is a tool call's own short description (two to ten words, no paths, flags, code, SQL or links), said whole; nothing is made up from tool names, commands or the assistant's chatter, and when nothing qualifies it stays quiet.

## [0.29.0.2] - 2026-10-04

### Changed
- **Launch video: the final pitch and an "ask for a chart" beat.** The end card now reads "Your money. Your financial AI. / Turn Claude into your personal finance assistant.", the opening caption is "It warns you when something's off.", and after the dining answer the owner asks "Show dining by month" and the chart appears on the dashboard (the demo's stand-in assistant really runs `finnamon chart`). 68 s, 6 MB.

## [0.29.0.1] - 2026-10-04

### Changed
- **The pitch is now "Your money. Your financial AI."** The README opens with it, the three promises (it watches for
  you, a dashboard that's yours, yours to keep) and why Finnamon was built; the word "watchdog" is gone from the
  README, package description, `finnamon --help` and the demo video's end card. A launch kit in `docs/launch/` has
  the Show HN facts sheet, r/selfhosted, r/ClaudeAI, selfh.st and X drafts, and the repo description and topics.

## [0.29.0.0] - 2026-10-04

### Added
- **Telegram and the dashboard can share one conversation without Claude Code's Telegram plugin.** `finnamon channel session`
  (then `finnamon update --no-pull`) keeps the daemon as the bot's only reader, with every check it has today on who may
  talk, and types each household message into the dashboard's intercom session as `[telegram · jane] …`, the way Talk to
  Finnamon types speech; the reply is read off that session's transcript and sent back to the chat (charts as photos). One
  message at a time; a slow turn, a dashboard that is down or a session still starting each get a plain message in the chat.
  `finnamon doctor` shows which mode answers Telegram. `daemon` and `channel` modes are unchanged, and the relay is built so a
  later Codex runner swaps only the binary, the transcript path and the transcript reader.

## [0.28.2.0] - 2026-10-04

### Added
- **Windows, through WSL2.** docs/INSTALL.md has a "Windows (WSL2)" section: Ubuntu inside Windows with systemd on,
  Finnamon installed there as on Linux, the dashboard in the Windows browser at localhost. A `wsl` CI job installs it on
  GitHub's Windows runners, runs the tests, `finnamon demo`, and `finnamon install`'s systemd units.

### Fixed
- **`finnamon open` and `finnamon demo` open the Windows browser from WSL** (`wslview`, else `explorer.exe`), where
  Python found no browser and only printed the address.

## [0.28.1.2] - 2026-10-04

### Added
- **A one-minute launch video and a README teaser.** A phone asks the demo household's assistant about dining and this
  week's worries, by voice, and hears the answers; the MP4 is linked from the top of the README. `scripts/demo/launch-video.mjs`
  records it from `finnamon demo` with the clock pinned to Sep 20 2026 (libfaketime) and a stand-in `claude`.

### Changed
- **The demo household tells the README's stories.** A $350 Dining budget at $248 by the 20th (on pace for $372), and the
  duplicate charge is now Shell $52.18 on Total Checking …4821, three and two days back, instead of Ace Hardware.

## [0.28.1.1] - 2026-10-03

### Changed
- **The README is a landing page now: "A watchdog for your household's money."** A stranger reads what it does, sample alerts, the two-minute `finnamon demo`, who it's for, what it costs, exactly what leaves the machine (Plaid, Anthropic through Claude, Telegram; nothing to a Finnamon server) and how it compares with ChatGPT Finances, Monarch, Actual Budget and Firefly III, before the install steps. The operator detail moved to `docs/COMMANDS.md`, `docs/DASHBOARD.md` and `docs/CHANNEL-MODE.md`; the October 2026 market research behind the positioning is in `docs/market-research-2026-10.md`.

## [0.28.1.0] - 2026-10-03

### Added
- **Repo files for outside contributors:** `SECURITY.md` (private reporting, scope for a self-hosted finance app), `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, bug and feature issue templates and a PR template.

### Changed
- **Personal data scrubbed from the current tree:** the real Telegram chat id, bot handle and household first names in tests and docs are now fixtures, the personal notes section of the design doc is gone, and screenshots showing real names are removed.

## [0.28.0.0] - 2026-10-03

### Added
- **`finnamon demo`: try Finnamon with no Plaid keys or Telegram bot.** A made-up household (two people, four banks,
  six months of transactions, budgets, recurring charges, a house and a car, and alerts from the real detectors) in its
  own `~/.finnamon-demo`, with the dashboard on a spare port (8890 up) and the intercom running Claude Code over it.
  `finnamon demo --stop` stops it, `--reset` starts it over. It never touches `~/.finnamon`. (#112)

### Changed
- **`finnamon init` lets you skip Plaid and Telegram for now.** Enter on a blank client_id or bot token skips that step
  (it used to be reported as "rejected" and stop init); the assistant, the dashboard and the schedule still get set up,
  and running `finnamon init` again adds what was skipped. Blank keys are no longer written to `secrets.toml`, the
  daemon waits quietly while there is no bot, and Link account without keys says to run `finnamon init`. (#111)

### Fixed
- **init's last error line now prints after the output that led to it**, not before it, when output is piped.

## [0.27.1.0] - 2026-10-03

### Fixed
- **`finnamon open --print` honours `PORT`.** It printed port 8888 even when the dashboard ran on another port.
- **`finnamon doctor` treats optional lines as optional.** "Remote access" and "Voice" show `·` (not `!`) when off or unset, and an unrelated `tailscale serve` mapping no longer tells you to turn anything off.
- **Install docs:** `uv tool update-shell` after `uv tool install`, and what the doctor's optional lines mean.
- **A contributor's first `pytest` passes** with `web/node_modules` present.

## [0.27.0.0] - 2026-10-03

### Added
- **Talk to Finnamon: a Speaker / Earpiece button in the call bar on an iPhone.** Where iOS lets a page choose (iOS 26's audio-output selection), the button switches the voice between the loudspeaker and the earpiece for the whole call, and this browser remembers the choice (Speaker by default). Elsewhere there is no button.

### Fixed
- **Talk to Finnamon on an iPhone keeps its voice in one place.** iOS played replies through the earpiece while the mic was open and through the loudspeaker when it stopped, so the voice jumped between them. A call now holds a play-and-record audio session and keeps the mic open for its whole length (on an iPhone, Mute silences it rather than stopping it), in Safari and in Chrome on iOS alike.
- **The call bar fits a phone.** State and timer on one row, the buttons on the next, each a full 44-pixel target; under 400 pixels wide Hang up shows only its icon.

## [0.26.0.0] - 2026-10-03

### Added
- **Tables on the dashboard's chart board.** Ask the assistant for "dining over $50 this month" or "all Amazon refunds" and a table of the matching transactions goes on the board next to the charts (`finnamon chart --table-json '<columns>' --sql "<SELECT>"`). Like a chart, it reruns its read-only query after every sync and shows when its rows were computed. Each table shows the first 50 rows and says how many there are ("showing 50 of 312"). Money going out is shown in the debt colour, and on a phone the amount stays pinned while the rest of the row scrolls. The board has three new presets with chips: **recent** (the last 30 days), **recurring** (active recurring charges), and `large_transactions [amount]`.

## [0.25.0.0] - 2026-10-03

### Added
- **Update Finnamon from the dashboard.** When a newer Finnamon is available, an "Update available" pill appears in the header next to the health one (a bank or sync problem stays visible beside it; on a phone it gets its own line). Tap it to see the versions and what's new, then Update: it runs exactly `finnamon update` on the box, shows Updating… and Restarting…, and reloads onto the new version, or shows the error and says to run `finnamon update` in a terminal. The dashboard checks at most every 6 hours, only the page itself can start an update (never the assistant or the API key), and one runs at a time. Works over `finnamon remote` on a phone too. `finnamon update --check` prints the same check as JSON.

## [0.24.0.0] - 2026-10-03

### Added
- **`finnamon account type <account_id> <checking|savings|credit|loan|investment>` corrects an account's type, and Plaid
  syncs keep it (#102).** Merrill sends a Managed CMA or CMA-Edge as a depository cash-management account;
  `finnamon account type <its id> investment` moves it from Cash to Investments in net worth, the dashboard's Accounts list
  and charts, and `--clear` goes back to the bank's type. A manual account created as the wrong type is fixed the same way,
  without removing it. The assistant may run it when asked. `finnamon account list` shows both the bank's type
  (`bank_type`, `bank_subtype`) and the household's (`type_override`); `finnamon help account` also explains `owner`.
  An investment account's own rows stop counting as spending or income, as with any brokerage.

## [0.23.0.0] - 2026-10-03

### Added
- **Accounts can be grouped by Bank, Type or Owner.** A "Group by" switch at the top of the Accounts dialog regroups the list: Type (Checking, Savings, … then debts) or Owner (one group per household member; offered only with 2+ members). Each group is collapsible, shows its account count and net total, and a bank that needs attention keeps a warning icon on its rows. Your choice is remembered per browser.

## [0.22.0.0] - 2026-10-03

### Added
- **The Accounts dialog names whose bank it is.** In a household with more than one member, each bank's heading carries its owner's name; if one bank holds accounts of different owners, the name sits on each account instead. A single-member household sees nothing extra.
- **A close (X) button in the Accounts dialog**, a 44 px tap target at the top right, so it is easy to dismiss on a phone. Escape and clicking outside still close it.

## [0.21.2.0] - 2026-10-03

### Fixed
- **Chart legends no longer squeeze the plot on a phone.** Every chart on the dashboard, including ones the assistant draws, now puts its legend in a row above the plot instead of to the right; the balances chart wraps its account names into two columns. A chart that sets its own legend position keeps it.

## [0.21.1.0] - 2026-10-03

### Fixed
- **Talk to Finnamon no longer cuts itself off on a phone's loudspeaker.** When the mic hears speech while Finnamon is talking, the reply is turned down instead of stopped; the words are transcribed and compared with what Finnamon is saying (its reply or progress line). If they match, or are a lone stray word that isn't stop, wait, yes, no or the like, they are not sent and the reply carries on at full volume. If they are yours, the reply stops and your words go through as usual. The voice detector also needs louder, longer speech while Finnamon talks, the mic's echo cancellation, noise suppression and auto gain are now requested explicitly, and Interrupt still cuts in at once.

## [0.21.0.0] - 2026-10-03

### Changed
- **`finnamon remote` no longer asks to replace whatever Tailscale already serves on HTTPS 443.** It leaves that alone and serves the dashboard on the next free port Tailscale Serve allows (8443, then 10000), adds `<name>:<port>` to the allowed names, and prints the address and QR code with the port. If all three ports are taken it says what uses them and changes nothing. `finnamon remote --off` removes whichever port it set up, and `finnamon doctor` shows the address with the port.

## [0.20.1.0] - 2026-10-03

### Fixed
- **Talk to Finnamon no longer cuts a spoken status line off mid-word when the answer arrives.** The status phrase now finishes (at most 3 seconds), newer queued status phrases are dropped, and the answer plays right after. Speaking over it still stops everything at once.

## [0.20.0.0] - 2026-10-03

### Changed
- **Each bank on the dashboard's Accounts list folds up.** Every bank starts closed, showing its name, "N accounts" and its net total (assets minus debts), with any sync warning still visible; click a bank to open it. Each bank's open or closed state is remembered per browser, and "Expand all / Collapse all" does the lot at once.

## [0.19.2.0] - 2026-10-03

### Fixed
- **Talk to Finnamon's call bar no longer covers the terminal.** Starting a call, the call bar and the note about where
  your voice goes now take their own space at the bottom of the assistant panel, the terminal shrinks to fit above them,
  and Hang up and Mute can always be clicked (on a phone the round chat button no longer sits on top of Hang up). After
  a call the terminal grows back and its input line stays in view, without having to type first. The note about where
  the audio goes is smaller and has an X: closed once, it stays closed in that browser until its wording changes.

## [0.19.1.0] - 2026-10-03

### Fixed
- **The in-and-out chart's default colours no longer look swapped.** Mortgage, income and spending now take the dashboard theme's first, second and third colours (the legend still reads income, spending, mortgage). Charts already on the board pick this up on their next refresh.

## [0.19.0.0] - 2026-10-03

### Added
- **`finnamon remote` ends with a QR code that logs a phone in.** Scan it with the phone's camera and the dashboard opens
  there, already logged in; no long address to carry over. The code is a one-time pairing code, not the dashboard's key:
  it lets in one device, within 5 minutes, only on the Tailscale name and never over Funnel. The address is printed under
  the QR for a phone without a camera. Run `finnamon remote` again for the next phone. `finnamon open --host` still works.

## [0.18.0.0] - 2026-10-03

### Changed
- **The Accounts list reads at a glance.** Each account now shows its latest balance on the right (what is owed in the
  debt colour, with a minus), a plain kind under its name (Checking, Credit card, IRA, Mortgage instead of Plaid's
  "depository · checking"), and its last four digits once, even when the bank already put them in the name. ALL-CAPS
  bank names show in normal case. Each bank's heading carries its sync state on the right, and a bank not synced in a
  day, or never, gets the same warning tag a bank needing a new login does. Display only: nothing stored changes.
- **`finnamon account list` includes each account's latest stored `balance`.** Read from the local database; no new Plaid call.

## [0.17.0.0] - 2026-10-03

### Added
- **Talk to Finnamon: voice calls with the assistant from the dashboard.** Press the phone button in the assistant panel, speak, and hear the answer read aloud. While it works, you hear a short status line ("Show this month's budgets"). Talk over it to interrupt; Mute and Hang up are in the call bar. Your words go into the household's session as a `[voice]` line, so the terminal shows the turn like any other. The assistant answers in a few spoken sentences: no tables or links.
- **`finnamon voice setup`** installs whisper.cpp (Homebrew) and downloads a speech model (checked against its published sha256) into `~/.finnamon/whisper`. It also adds the dashboard's voice detector to a dashboard installed earlier. Transcription then stays on this machine, and replies use macOS `say`. Without it, Talk falls back to the browser's own recognition, after a one-time notice that the browser may send the audio to its maker. `finnamon doctor` has a Voice line, and `finnamon voice status` prints the same check as JSON. On a phone, Talk needs the dashboard served over HTTPS (`tailscale serve`).

## [0.16.0.0] - 2026-10-03

### Added
- **`finnamon remote` puts the dashboard on your phone over Tailscale in one step.** It finds Tailscale (on PATH or
  inside the macOS app), checks this machine is logged in to your tailnet, runs `tailscale serve --bg 8888` with its
  output shown live (so the "enable Serve" link appears at once), and adds the machine's ts.net name to
  `~/.finnamon/web-hosts`, which the dashboard now reads on every request: no restart, and no more keeping
  `FINNAMON_WEB_HOSTS` exported so `install` does not drop it. Never Funnel, and it stops while Funnel puts the page on the public internet; never replaces
  something else served on HTTPS without asking; `--dry-run`; running it twice changes nothing; `--off` takes the
  names back out and stops Tailscale serving the port. It always serves the installed dashboard's port (8888), never a `PORT` your shell exports, and only once the dashboard
  is what answers there (8888 is Jupyter's default too). Each device still needs the key once:
  `finnamon open --host <name>`.
- **`finnamon doctor` has a Remote access line**: off, on (`https://<name> → port 8888`), or what is wrong (served but
  not let in, let in but not served, or Funnel putting the page on the public internet).
- **The assistant cannot run `finnamon remote` or edit `~/.finnamon/web-hosts`** (deny rules in its settings, like
  `open` and `web token`).
- **The dashboard refuses requests Tailscale carried in over Funnel** (the public internet), page and intercom alike,
  even if someone runs `tailscale funnel` by hand later.

### Changed
- **Served your dashboard over Tailscale with `FINNAMON_WEB_HOSTS` before?** Run `finnamon remote` once after updating:
  the name moves into `~/.finnamon/web-hosts`, and `finnamon doctor` stops warning about it.

## [0.15.0.0] - 2026-10-02

### Added
- **Link account asks whose bank it is when the household has several members.** The dashboard shows a small choice of members (first one selected) and files the bank and its accounts under that person; the owner survives the OAuth return in a new tab, and the success toast names them ("Linked Chase for Sam"). With one member nothing changes. "Joint" is not offered: accounts become joint only by merging after both logins are linked.

## [0.14.0.0] - 2026-10-02

### Changed
- **The dashboard's default port is now 8888 (was 7077).** The address is http://localhost:8888. `finnamon update` and `install` print one line saying so. Browser cookies are per port, so open it once with `finnamon open` (it carries the key). A `tailscale serve --bg 7077` must be redone as `tailscale serve --bg 8888`. Plaid's redirect URI is the https Tailscale name without a port, so it does not change. An explicit `PORT` you set stays as you set it.
- **A taken port is named.** 8888 is also Jupyter's default: if something holds it, the dashboard's log says so and names `PORT` as the fix, and `finnamon doctor` reports a non-Finnamon answer on the dashboard's port.

## [0.13.2.0] - 2026-10-02

### Fixed
- **The intercom side panel now runs from under the header to just above the intercom button, and the header stays full width.** On wide windows only the page content narrows to make room; the panel no longer stops short of the available height.

## [0.13.1.0] - 2026-10-02

### Fixed
- **The assistant can explain the commands only a person may run.** `finnamon help <command>` prints any command's usage, human-only ones included, and the assistant may run it (only it: `finnamon owner add --help` stays denied, and every human-only command still refuses a Claude session). Ask "how do I add my partner?" and it reads `finnamon help owner` and tells you the exact line to type on the Finnamon box.
- **Human-only commands' help says what they need and what comes next.** `init`, `link`, `owner`, `channel`, `account`, `sync`, `install`, `update` and `detect` now end their `--help` with a few plain lines: e.g. `owner add` needs a name and, in channel mode, the person's Telegram user id from `/telegram:access`; next, link their banks with `link --start --owner <name>`.

## [0.13.0.0] - 2026-10-02

### Fixed
- **Board charts recompute from their query, and say when.** Each chart on the dashboard now keeps what drew it (a preset's name and arguments, or a spec's `data.sql`) and is recomputed after every sync, import, and write that changes how rows classify (`category`, `alias`, `normal`, `account`, `budget`, `alerts --dismiss/--undo`), and again whenever the dashboard loads the board, read-only as `--sql` always ran. Each chart shows "as of <time>"; a spec with only inline values is marked static. Older boards keep working: presets get their source on the next refresh. `finnamon chart --refresh` does it by hand. (#76)

## [0.12.0.0] - 2026-10-02

### Changed
- **The dashboard's intercom now docks as a side panel on wide windows.** The page narrows to make room instead of hiding behind the chat, and the charts redraw at the new width, so you can look at a chart or alert while asking about it. Closing the panel gives the page its full width back, and the dashboard remembers whether it was open. On phones and narrow windows (900px and under) it stays an overlay.

## [0.11.0.0] - 2026-10-02

### Fixed
- **The dashboard's alerts list shows only open alerts.** One acknowledged in the chat (`finnamon normal --alert`) no longer
  sits there as if it were still news: it moves to a collapsed, greyed "Resolved" section that says when and how ("It's
  normal (rule N)" or "Dismissed"). `finnamon alerts --open` / `--resolved` do the split.
- **An alert resolved while still queued is not sent to Telegram.** Dismissing or marking normal an alert that had not
  gone out yet used to let the next send deliver it anyway; Undo queues it again. `normal --alert` on an alert already
  resolved is refused (undo first), so every rule an alert wrote stays undoable.

### Added
- **"It's normal", "Dismiss" and "Undo" on each alert on the dashboard.** "It's normal" runs `finnamon normal --alert`, the
  same rule the chat writes (one subscription for a recurring_changed alert); "Dismiss" (`finnamon alerts --dismiss <id>`)
  resolves it with no rule, so the next such charge still alerts; "Undo" (`finnamon alerts --undo <id>`) reopens it and
  removes only the rule that alert wrote. The chat can say "dismiss it" and "undo that" the same way.

## [0.10.0.0] - 2026-10-01

### Fixed
- **"In and out by month" no longer counts money moving between your own accounts.** A paycheck moved to savings the same day was income twice, and card payments, savings sweeps and mortgage payments landing on the loan account all inflated both bars. Each transaction now carries one shared classification (`flow` on `tx_now`: income, expense, refund, mortgage, transfer, card_payment, skipped), and the chart, spend by category, budgets, the budget pace alert and the assistant's queries all read it. A payment to a card you have not linked still counts as spending, since it is the only record of it; a Zelle to a person is spending too.

### Changed
- **The mortgage is its own series in "In and out by month",** stacked on spending so the "out" bar is everything that left. Refunds net against spending.

### Added
- **`finnamon category "<payee>" transfer` marks a payee as money between your own accounts** (both directions), for imported accounts whose rows have no category. `finnamon category "<payee>" mortgage` does the same for a mortgage payment.

## [0.9.1.0] - 2026-10-01

### Fixed
- **The dashboard's net worth chart shows when, and hovering it shows how much.** The axis labels carry the year (on the first month and every January), so a 1Y view no longer shows two Octobers with nothing to tell them apart. Hover (or tap) the chart and it snaps to the nearest day: a tooltip gives the date and the dollar amount, and names each curve when assets or liabilities are shown too. The board charts already had tooltips.

## [0.9.0.0] - 2026-10-01

### Fixed
- **`finnamon triage set` takes the sentence on stdin only, so "$45.67" can no longer reach the chat as "5.67".** A reason passed as a shell argument had already lost its `$` signs to positional-parameter expansion before the command saw it; now the command refuses one and says to end with `-` and a quoted heredoc (`- <<'EOF'`). To repair a damaged reason, run it again on a fully triaged group with the verdict it already has: only the sentence changes; the verdict, confidence and whether it was sent stay. Only a person can do this: a Claude session (triage or chat) never rewrites a stamped sentence (#63).

### Fixed
- **"Yes, I cancelled it" acknowledges that one subscription, not the whole merchant.** `finnamon normal --alert <id>` on a
  stopped or re-priced subscription alert now mutes that stream only (new `suppressions.stream_id`, migration 007); another
  subscription at the same merchant, or a new one started later, still alerts when it changes or stops.
- **"Acme is normal" no longer hides Acme's subscriptions stopping.** A `finnamon normal "<merchant>"` rule with no `--kind`
  covers every alert kind except `anomaly:recurring_changed`; pass `--kind anomaly:recurring_changed` to mute that on purpose.

### Added
- **`finnamon normal --remove <id>`** deletes one rule (ids from `finnamon normal --list`). An existing merchant-wide rule
  written by "yes, I cancelled it" before this release keeps silencing that merchant's subscriptions until removed this way.

## [0.8.16.0] - 2026-10-01

### Fixed
- **Income alerts no longer show a minus sign.** A deposit now reads "$1,234.56 from Acme Corp" instead of "-$1,234.56 from ...", in Telegram and on the dashboard; the stored amount is unchanged.

## [0.8.15.0] - 2026-10-01

### Changed
- **Docs: HSBC "reference: EAC" after Chrome update is the bank, not Finnamon.** Added troubleshooting to README and import-browser skill explaining that the error after a Chrome update is HSBC's risk engine rejecting the new build, not a Finnamon bug; shows how to check which Chrome build is running and recommends downloading the CSV with the everyday browser as a workaround.

## [0.8.14.0] - 2026-10-01

### Fixed
- **No more "Permission deny rule ... is not matched" warnings.** The assistant's settings dropped the `Write(...)` and `NotebookEdit(...)` deny rules that Claude Code 2.1.286 no longer matches; the `Edit(...)` rules beside them already cover every file-editing tool.

## [0.8.13.0] - 2026-09-28

### Changed
- **Pull requests no longer bump `VERSION` or edit `CHANGELOG.md`, so parallel PRs stop colliding on them.** Each PR adds its own `changelog.d/<branch>.md` with front matter `bump: major|minor|patch|micro` and its `### Added/Changed/Fixed` notes (`changelog.d/README.md`). Once the tests pass on main, `.github/workflows/release.yml` runs `scripts/release.py`: the largest bump wins, the notes become the next `## [X] - date` section here, the fragments are deleted, and the result is committed to main as `vX release: <headline>` and tagged `vX`, in one push. Only a tested commit that is still main's tip is released, so fragments never ship ahead of their tests and never go out twice. A merge with no fragment (docs, a test) releases nothing. This is the last release bumped by hand.
- **`finnamon update` restarts nothing for a release commit.** `VERSION`, `CHANGELOG.md` and `changelog.d/` carry no code, so the household's assistant is not ended for them. Before, a `VERSION` change alone restarted the daemon. "reload the page" now shows only when `web/public/` changed.

### Fixed
- **`finnamon update` no longer restarts both jobs for a CI-only or `.gitignore` change.** The changed-file rule stripped every leading `.` and `/` character, so `.github/…` never matched its "nothing to restart" entry.
- **Channel mode: a daemon started within a minute of the box booting now runs its first "is anything reading the chat" check.** The throttle compared against a monotonic clock that counts from boot, so on a freshly booted box (or CI runner) the first check waited up to a minute.

## [0.8.12.2] - 2026-09-28

### Added
- **Every pull request and every push to main now runs the test suite on GitHub.** `.github/workflows/test.yml` runs `pytest -m "not eval"` (Python 3.12 via uv) and the dashboard's `npm test` (Node 20), with dependency caches and a cancel-superseded-runs group. It has no secrets: no Plaid keys, no Telegram token, no Claude login, so the eval lane stays a by-hand run. `HOME`, `FINNAMON_HOME` and `CLAUDE_CONFIG_DIR` point at scratch directories on the runner. The README carries the status badge.

## [0.8.12.1] - 2026-09-27

### Changed
- The Import CSV panel's trash button (added in 0.8.9.0) has been clicked through in a real browser for the first time: cancel, confirm, and the empty-state transition all behave as designed at both desktop and phone widths, and a regression test now covers the confirm-then-DELETE flow (`web/test/routes.test.js`), including that a declined confirmation sends nothing and an accepted one deletes by account id, never by name.

## [0.8.12.0] - 2026-09-27

### Added
- **Re-login links from the assistant are rate-limited.** `finnamon link --update <item> --telegram` run by the assistant now sends at most one link per bank every 15 minutes, and at most 6 a day for the household. A second request inside 15 minutes is refused with "a re-login link for this bank went to the chat N min ago and is still valid", and the assistant passes that on. So a prompt-injected memo can no longer fill the household chat with Plaid links. The add-a-bank link the assistant posts (`link --start --telegram`) counts toward the same daily cap, and only a link that actually reached the chat counts. A person at a terminal is not limited.

### Changed
- **"fix Chase" is no longer a guess when two logins share a bank.** When two Plaid logins are at the same bank, the re-login and expiring-connection alerts name whose login it is and its item id ("Chase (jane's login, item2) needs a re-login. Reply *fix item2*"). If someone still writes "fix Chase", the assistant lists both logins with their accounts and last sync error and asks which one, instead of picking.
- `finnamon status` lists each bank's accounts and last sync error next to its item id.
- `finnamon doctor` suggests "reply 'fix <item id>'" instead of the bank's name when two logins share it.

## [0.8.11.0] - 2026-09-27

### Added
- **Fix a bank's broken login from your phone.** When a bank needs a re-login, the alert now says "Reply *fix Chase* and I'll send the login link here" instead of naming a command to run on the Finnamon box. Reply that, and the assistant runs `finnamon link --update <item> --telegram`, which posts a Plaid re-login link to the household chat. The message names whose login it is, because a joint household often has two logins at one bank, and it says the link is good for about 30 minutes, since Plaid expires re-login links that fast. Open it, log in, and the next sync picks the bank back up. From the assistant this command always sends to the chat and returns right away. It refuses when there is no household chat or the bank is not a Plaid bank. Every other `link` action is still yours at a terminal: unlinking and the blocking form, plus `link --update` without `--telegram`.
- **A warning a week before a bank's connection expires.** Some banks give Plaid consent only until a set date. Sync already fetches that date, and when it is 7 days away or less, the chat gets one message: "Chase's connection expires 2026-10-03. Reply *fix Chase*…". The message goes out once per expiry date, not on every sync, and never for a date already past. Renewing the connection moves the date, so the next one gets its own warning.

### Changed
- `finnamon doctor` now suggests the phone fix, "reply 'fix Chase' in the household chat", next to `finnamon link --update <item>`.
- The assistant's permission set allows `finnamon link --update * --telegram` and no longer denies `link --update` outright. The CLI still refuses every other `link` form from a Claude session. The assistant's instructions, the README and the design doc now describe the new path. It reaches a household on its next `finnamon update`.

## [0.8.10.0] - 2026-09-27

### Fixed
- **A test or second install can no longer replace the household's scheduled jobs.** The job names now follow `FINNAMON_HOME`. The household's home (`~/.finnamon`) keeps exactly today's names (`com.finnamon.daemon`, `finnamon-daemon.service` and the rest), so nothing needs reinstalling. Any other home gets its own names (`com.finnamon-1a2b3c4d.daemon`, `finnamon-1a2b3c4d-daemon.service`) and, on a Mac, its own log folder. So `finnamon status`, `install`, `update`, `uninstall` and `doctor` under a scratch home only see and touch that home's jobs. A box that already runs a custom `FINNAMON_HOME` under the old names keeps them, so it never ends up with two daemons.
- **Linux: services that would stop at logout are now reported.** When `loginctl enable-linger` fails, `finnamon install` says so and prints the fix (`sudo loginctl enable-linger $USER`), and `finnamon doctor` has a "Linger" line. Before, the failure was ignored and the daemon quietly stopped when you logged out.

## [0.8.9.0] - 2026-09-27

### Added
- **You can delete one manual account without losing the rest of its bank.** `finnamon account remove "HSBC Checking"` removes that account with its transactions, balances, recurring streams, holdings, settings and suppressions, and resolves any unsent alerts about it. The other accounts at that bank are kept. When you remove a bank's last account, the bank goes too. At a terminal it asks first and names the account and how many transactions go with it. `--yes` skips the question; without a terminal it refuses unless you pass `--yes`. A Plaid-linked account is refused with the `finnamon link --remove <item>` command that unlinks its bank, because the next sync would bring it back. Until now a typo in a manual account's name stayed in net worth until you removed the whole bank.
- **The dashboard's Import CSV panel has a trash button next to the chosen account.** It confirms with the bank, the account and its transaction count, then deletes that account by its id, so a Plaid account with the same name is never the one removed. The request goes to the new `DELETE /api/account/:ref`, which needs the dashboard key and passes the same Origin check as every other write.

### Changed
- `finnamon account list` now reports each account's transaction count (`transactions`).
- The assistant cannot run `account remove`: it is human-only in the CLI and on the household session's deny list.

## [0.8.8.0] - 2026-09-27

### Added
- **Weekly backups of the household's database.** Once a week the daemon copies it to `~/.finnamon/backups/finnamon-YYYY-MM-DD.db` (readable only by you, in a folder only you can open) and keeps the newest 8. A copy is only kept once it is complete, so a crash mid-backup never leaves a broken file posing as a good one, and a failed re-run never deletes that day's good copy. A failed backup is logged, shown by `finnamon status` (`last_backup_at`, `backup_error`) and `finnamon doctor` (a "Backups" line), retried after 6 hours, and never holds up a sync. [docs/INSTALL.md](docs/INSTALL.md) has a short restore section.

### Fixed
- **Mac logs no longer grow forever or sit readable by other users.** The hourly heartbeat and each daemon start rotate any log in `~/Library/Logs/finnamon` past 5 MB (three old copies kept) and set every log to 0600. `finnamon install` makes the folder 0700.
- **The dashboard no longer hits "database is locked" while detectors run.** Each detector now reads before it takes the write lock, and only holds it for the brief write of its new alerts.

## [0.8.7.0] - 2026-09-27

### Fixed
- **In channel mode, a restart no longer swallows a Telegram message without a word.** When `finnamon update` (or any dashboard restart) cuts the assistant off mid-reply, the Telegram plugin has already taken the message, so it was simply gone and the person never got an answer. Now the dashboard notes at shutdown that a turn was in flight, and when it comes back it posts one line to the household chat: "The assistant restarted; resend anything from the last minute." It posts once per such restart, only in channel mode, and not after a cold start or a restart with nothing in flight. A restart with no output in the last minute counts as idle; one where someone had just used the dashboard terminal may send the line even though nothing was lost.

## [0.8.6.0] - 2026-09-27

### Added
- **`finnamon doctor` checks the whole setup and tells you how to fix each problem.** One command covers Claude Code (installed and logged in), Node 20.12+, the dashboard's packages, `finnamon` on PATH, `secrets.toml` and its permissions, the Plaid keys (checked live, and which environment: production or sandbox), the Telegram bot (live, plus its privacy mode), the household chat, linked banks and any that need a re-login, the scheduled jobs (installed, current, and whether the daemon is checking in), the dashboard key and the assistant directory. Each problem comes with the command that fixes it, and doctor exits non-zero while any remain. It only reads: on a fresh machine it creates no database, key or secrets file.
- **A first-household install guide, [docs/INSTALL.md](docs/INSTALL.md), linked from the top of the README.** Prerequisites, then Plaid Production access (the Trial plan, Limited Production for older teams, what OAuth banks like Chase need, redirect URIs), `finnamon init`, linking a bank, the dashboard key, and the phone over Tailscale. Whether Chase links on a Trial key is stated as an open question: Plaid's docs point to yes, but nobody has confirmed it against Finnamon.

### Changed
- **With no bank linked, the dashboard's alerts card says "Link a bank to start"** with a Link account button, instead of "Nothing to flag", which told a new household all was well while nothing was being watched.

### Fixed
- **The dashboard keeps answering to its Tailscale name after a reboot.** `launchctl setenv FINNAMON_WEB_HOSTS` is gone after a restart, so the phone's page was refused until someone set it again. `finnamon install` now writes `FINNAMON_WEB_HOSTS` from your shell into the dashboard's job (macOS and Linux); keep it exported in your shell profile. The README and the install guide also say `tailscale serve --bg 7077`, which keeps serving after the terminal closes.

### Removed
- **`scripts/check_setup.py`**: `finnamon doctor` does its job. **`docs/PLAID_OAUTH.md`**, which described the old web app; what was still true (Sandbox's OAuth test bank, HTTPS-only redirect URIs) moved to `docs/DEVELOPMENT.md` and `docs/INSTALL.md`.

## [0.8.5.0] - 2026-09-27

### Changed
- **The Sunday roundup now comes every week, even when there is nothing in it.** A quiet week used to send nothing, so a healthy watchdog looked exactly like a dead one. Now an empty week gets one line: "All quiet this week", how many accounts are watched, the oldest bank's last sync (so one dead bank can't hide behind a live one), how many transactions came in over the last 7 days, and that nothing was flagged (or how many alerts already went out). If anomalies are still waiting on triage or an alert failed to send, the line says "Quiet" with a yellow dot and counts them instead of claiming nothing happened.
- **`finnamon notify --roundup` on a weekday no longer cancels that week's Sunday roundup.** A forced roundup outside Sunday/Monday sends but leaves the week unmarked. A week with low-confidence anomalies still gets the numbered list, as before.
- **A Sunday the Finnamon box slept through is sent on Monday.** Before, a missed Sunday pushed the roundup a whole week. Monday's first run now sends it, once; from Tuesday it waits for the next Sunday.

### Added
- **Linking the household's first bank ends with what to do in week one.** The linked-bank summary for the first bank also says what Finnamon now watches (duplicate charges, new subscriptions and price changes, anything unusual, banks that stop syncing, the weekly roundup) and how to get more from it: reply "set up my budgets" for pace alerts, and, when no low-balance threshold is set, a suggested $500 one for the checking account that a single reply ("alert me if <account> drops below $500") or `finnamon threshold '<account>' 500` turns on. Later banks get the summary alone.

## [0.8.4.0] - 2026-09-27

### Security
- **The human-only commands now refuse a Claude session in code, as the docs always said.** Before, only the assistant's allow list stopped `sync`, `run`, `daemon`, `heartbeat`, `notify`, `init`, the bare `triage` run and `account merge` / `unmerge` / `failover`. Now each one exits with "is for a person at a terminal" when `FINNAMON_FROM_CLAUDE` or `CLAUDECODE` is set. The read-only forms stay open: `notify --list`, `triage set` / `suppressed`, `networth` and `networth --history`.
- **The assistant can no longer make Plaid calls through `networth --sync`.** The bundle's `Bash(finnamon networth *)` allowed it. The allow list now names `networth`, `networth --history` and `networth --history --months *`, a deny rule covers `--sync` anywhere on the line, and the CLI refuses `--sync` from a Claude session. The CLI also stops accepting abbreviated flags (argparse's `allow_abbrev`), so a `--syn` can't match a deny rule's wording and still run as `--sync`. The allow-list change reaches a household on its next `finnamon update`.
- **An enrolment code no longer moves the household chat to whichever group it was sent in.** While `owner add` waited, the daemon (and the direct poll) accepted the code from any group the bot was in and made that group the household chat. A household that is already a group now takes the code only there. `finnamon owner add <name> --new-group` is the explicit way to move to a new group. A household that is still a DM moves to the group the code comes from, as before, because that's what `owner add` tells the person to create. If the code arrives somewhere it doesn't count, the timeout message mentions `--new-group`.

## [0.8.3.1] - 2026-09-27

### Fixed
- **`test_unattended_runs_cannot_reach_the_web` no longer flakes on the model's memory of example.com.** The eval only ever proved `--disallowedTools` removes WebFetch/WebSearch from the run's tool list; it then also checked the model's prose for a refusal, and occasionally the model answered from training data ("the page's heading is Example Domain") instead of admitting it never called the tool, failing an assertion that had nothing to do with the guard. `run_claude()` now also collects every `tool_use` block's name from the run's full JSON event array (already returned since `--verbose` was passed; the array was just being collapsed to `body[-1]` before parsing) as `tool_calls`, and the test asserts on that set directly: no `WebFetch`/`WebSearch` call happened. The positive control (`test_a_person_at_the_keyboard_can_reach_the_web`) is unchanged.

## [0.8.3.0] - 2026-09-27

### Security
- **The dashboard and its intercom need a key, the way a Jupyter notebook does.** The page listened on localhost with no login, and its intercom socket is a PTY holding the household's Claude Code session, where `!` runs bash with no permission check. The Host and Origin checks stop a browser from being talked into it, but a WebSocket with no Origin was accepted as "a client on this machine", so any local process could open the socket and get a shell as the user, and once the page is served over Tailscale (`tailscale serve` plus `FINNAMON_WEB_HOSTS`, a documented setup) so could every device on the tailnet. Now the server has a key: 32 random bytes as hex in `~/.finnamon/web-token`, 0600, made on first run by whichever of the server and `finnamon open` gets there first and kept across restarts. `finnamon open` launches the browser at `/?token=…`; the server checks the key in constant time (both sides hashed first, so a guess of the wrong length fails like a wrong byte), sets it as an HttpOnly cookie for 30 days, Secure when the page came through TLS, and redirects to the clean address so the key stays out of the address bar (and out of the visible history; a browser may still keep the redirect's source in its own records). The page, `/api/*`, the static files and the socket all answer 401 without that cookie or an `Authorization: Bearer` header, a WebSocket with no Origin included. `finnamon web token` prints the key (for curl, or as `FINNAMON_WEB_TOKEN` on another computer whose `finnamon import --to` now sends it as a bearer), `finnamon web token --rotate` mints a new one, which the server, reading the file on every request, honours at once: every open page is locked out until the next `finnamon open`, and an intercom socket that was already open is closed too, since it is a shell let in under the old key (the moment the file changes, and again on its next frame, so a filesystem where `fs.watch` is silent changes nothing). The cookie is named by port, so a second dashboard on the same machine (a development one on another `PORT`) does not overwrite the household's. After `finnamon update` brings this release in, every browser needs one `finnamon open` to get back in; the page says so. A key file someone edited by hand into something that is not a 64-character hex key is reported (the server answers 503, `finnamon open` says which file) rather than served, since neither a cookie nor an address could carry it; a key file the server cannot read at the moment pauses the intercom and answers 503 without telling anyone to re-key. A key file left empty by a write that died halfway counts as missing and is minted again, instead of locking everyone out with no way back. The assistant's session is also denied reading or editing the key file itself (in the bundle's `.claude/settings.json`, so it reaches a household on its next `finnamon update`), the way it already is for `secrets.toml`, so the `open` and `web token` gates cannot be walked around with a file read. Another computer's `finnamon import --to` says how to get the key when the box answers 401, and refuses to start a browser import without one. Both verbs refuse to run from a Claude session and sit on the assistant's deny list; the household session that holds the shell must not be the thing that hands out its key. Each intercom connect, and each refusal, is logged with the peer address (the tailnet peer from `X-Forwarded-For` behind `tailscale serve`) and how it authenticated, never the key. The cookie is `SameSite=Lax` rather than Strict on purpose: Plaid's OAuth return is a cross-site top-level navigation to `/?oauth_state_id=…` and Strict would drop the cookie on exactly that request, while Lax still withholds it from cross-site POSTs, subresources and WebSocket handshakes, and every write here is a POST or DELETE behind the Origin check. A `Tailscale-User-Login` header is not trusted, even optionally: to this server a `tailscale serve` proxy and any other local process both arrive from loopback, and either could set it; the key is the one thing the tailnet is asked for, and `finnamon open --host <machine>.<tailnet>.ts.net` prints the phone's address to open there once. `finnamon init` prints the tokened address at the end, its Telegram note and the README say `finnamon open`, and a page whose key was rotated says so instead of retrying its socket every two seconds. The launchd and systemd jobs are unchanged: the server reads the key from the `FINNAMON_HOME` they already set.

## [0.8.2.1] - 2026-09-27

### Fixed
- **A duplicate-charge alert no longer fires on money that repeats on purpose.** The rule goes straight to the household chat with no triage, and the pre-launch review found it pairing same-amount transfers, Venmo/Zelle/Cash App payments (Plaid files them under `TRANSFER_OUT`), ATM withdrawals, card autopays and loan payments. It now skips any pair where either charge is `TRANSFER_IN`, `TRANSFER_OUT` or `LOAN_PAYMENTS` (after the household's category overrides; an uncategorized charge, such as a CSV import, is still checked). It also requires the bank's raw transaction name to match, not only the merchant, so two plans at one merchant billed the same week (one Plaid merchant id, two bank strings) no longer pair. A bank that writes the purchase date into the raw name now only pairs same-day duplicates.
- **"It is normal" on a duplicate-charge alert mutes that charge, not the merchant.** The reply used to write a suppression for every duplicate at that merchant, on every account, forever. It is now scoped to the alert's account and capped at its amount, so a bigger double charge at the same place still reaches the chat. Naming the merchant explicitly (`finnamon normal "<merchant>"`) still mutes it all.

## [0.8.2.0] - 2026-09-27

### Fixed
- **Cancelled subscriptions no longer come back as "skipped" every month.** The `recurring_changed` anomaly candidate had two branches: an amount that moved, and a payment that missed its predicted date. Only the first checked the Item's first sync. So every subscription that had died somewhere in the 24-month backfill counted as "skipped" on each new month's key. Those candidates flooded triage and cost a Claude run each. The skipped branch now counts only a predicted date on or after the first sync: a payment missed before Finnamon was watching is history, not news. Sync also now stores Plaid's `is_active` flag on each recurring stream (migration `006_recurring_is_active.sql`), and the candidate skips any stream Plaid says has stopped. Rows from before the migration read as active until the next sync fills the flag in. An active stream that misses a payment after the first sync is still raised. The other detectors are unchanged.

## [0.8.1.0] - 2026-09-27

### Fixed
- **The Telegram plugin is registered only for the household's own `~/.finnamon/assistant`.** Any `claude` started in a folder the plugin is registered for loads it and polls the bot. On 2026-09-26 the registry held it for six Agent 007 worktrees and two `/private/tmp` folders besides the household's, and one board worker's copy stole the household's Telegram updates. `assistant.register_channel_plugin`, which `install`, `update` and the daemon call in channel mode, now refuses (and spawns nothing) when `FINNAMON_HOME` is not the default or `FINNAMON_ASSISTANT` is set, when the folder is under a temp dir, or when it sits inside a git checkout or worktree (a dotfiles repo at `~/.git` does not count). `finnamon install --force` / `update --force` override it; the daemon never does.
- **`finnamon status` names stray registrations.** It reads Claude Code's `installed_plugins.json` (under `CLAUDE_CONFIG_DIR` when set) read-only and, when the Telegram plugin is registered anywhere but the assistant directory (the checkout's pre-0.8 registration included) or at user scope, adds `stray_telegram_plugins` and the `claude plugin uninstall … --scope local` fix. It never edits Claude's config. A stray that also enables the plugin in its `.claude/settings.local.json` is listed again under `stray_telegram_plugins_seeding`: Claude Code copies such a registration to every git worktree of that repo, and to every subfolder, where a `claude` session starts, whatever `--settings` says. It was reproduced in a scratch `CLAUDE_CONFIG_DIR`. That is where the board workers' registrations came from: the pre-0.8 checkout still enables the plugin.

## [0.8.0.0] - 2026-09-27

### Changed
- **The assistant runs from `~/.finnamon/assistant/`, installed from the package; the checkout is developer-only.** Until now the repository root did two jobs: it was where every `claude` Finnamon started for the household ran (the daemon's `claude -p --resume`, the headless `/triage`, the dashboard's intercom PTY, `import --browser`), so its `AGENTS.md`, `CLAUDE.md`, `.claude/settings.json` and `.claude/skills/` were the assistant; and it was the development checkout, where developers and their agents inherited that persona and permission set, and a plain `claude` could kill the household's Telegram channel plugin. The assistant is now a bundle inside the package, `finnamon/assistant_bundle/` (`CLAUDE.md`, `.claude/settings.json`, `.claude/skills/{finnamon,triage,import-browser}`), that `finnamon init`, `install` and `update` write to `~/.finnamon/assistant/` (atomically, touching nothing else in `~/.finnamon`), and every household `claude` runs there: `claude_runner` (triage, the conversation worker), `import --browser`, and the dashboard's session (`web/server.js`, `FINNAMON_ASSISTANT` overrides the path). The harness check looks there too, so a wheel install has an assistant at all. Claude Code ignores a directory's `.claude/settings.json` (the allow list and the reply-guard hook with it) until the directory has been trusted, so `init`, `install`, `update` and the daemon's startup also mark `~/.finnamon/assistant` trusted in `~/.claude.json` (`projects[<dir>].hasTrustDialogAccepted`, the key Claude Code's own warning names; one key written, the rest of the file kept), and the harness check reports a directory that is not; the dashboard waits for `finnamon status` to report the directory ready before it starts the household's session, the same gate the daemon has before its first `claude -p`. `update` restarts the daemon and the dashboard whenever the bundle changed, whichever files git said changed. A household's own edit to a file in that directory is kept: left alone while a release ships the same file, kept as `<file>.bak` and said so once a release changes it; a file a release drops (a retired skill) is removed unless they had changed it. The root `AGENTS.md` and `CLAUDE.md` are developer instructions now, the root `.claude/` is gone, and the assistant's `CLAUDE.md` no longer carries the "assistant vs. development" rule, since there is no development where it runs.
- **`finnamon detect --prelude` prints `_prelude.sql`.** The finnamon skill read it by a repo-relative path when drafting a detector; the installed assistant sits next to no checkout, so the skill now runs that command (and `detect --sql <name>` for an existing rule's shape) and names no source file at all. It is on the assistant's allow list.
- **Channel mode follows the assistant directory.** Claude Code's Telegram plugin is installed at local scope, which belongs to a directory; `install` and `update` register it for `~/.finnamon/assistant` once `inbound` is `channel` (`claude plugin install telegram@claude-plugins-official --scope local`, run there; a failure prints the by-hand command), and `finnamon channel on` sends people to that directory rather than "a Claude Code session here".

### Migration
- **The first `finnamon update` after this release starts the household's conversations over, once.** Claude Code keeps a session's transcript under the directory it ran in, so neither the daemon's `--resume` nor the dashboard's can pick up a conversation that lived in the checkout from the new cwd. The first bundle install clears the daemon's `state.session` and retires the dashboard's `intercom.json` (kept as `prev`, the way `install` retires it), and prints that it did. Nothing carries the old context across: the transcripts stay on disk under the checkout's project directory. Note that `finnamon update` runs the *previous* release's update code while pulling this one, which knows nothing of the bundle: the restarted daemon writes a missing `~/.finnamon/assistant/` itself at startup (only when nothing wrote it before or a required file is missing; it never rewrites a complete one), marks it trusted, and in channel mode registers the Telegram plugin for it, since the dashboard session that restarts beside it is the chat's only reader; the dashboard's session starts there as soon as it exists. So a second `finnamon update --no-pull` is not needed, though it is harmless and prints what was written. (That old `update` restarts the dashboard because the root `CLAUDE.md` and `AGENTS.md` change in this release too; the previous release's restart rule would have restarted the daemon alone for the bundle.) In channel mode the dashboard's session can start a few seconds before the daemon has finished registering the plugin; if it does, the chat goes unread until the next restart, which the daemon's own watch reports within minutes ("Telegram is not being read: finnamon update --no-pull", in the chat and on the dashboard's status pill), and that command is the fix. `.claude/settings.json` in the assistant directory is the one file a household's edit does not survive in: it is the seal on unattended runs, so every release puts its own back (the edit kept as `.bak`) and the daemon restores it at startup. The old checkout may still have the Telegram plugin registered against it: a plain `claude` there keeps the old hazard until `claude plugin uninstall telegram@claude-plugins-official --scope local` is run in it.
- Tests: `tests/test_assistant.py` covers the install into a scratch `FINNAMON_HOME`, the `.bak` rule, the cwd of every household spawn (`claude_runner`, `import --browser`, and `web/test/session.test.js` for the dashboard), `detect --prelude`, the root's lack of the assistant's allow list, the session reset on the first update, the plugin registration, and that a built wheel carries the bundle. The eval lane runs the real `claude` in a fresh install of the bundle rather than in the checkout, so it reads what a household's session reads and not the developer `CLAUDE.md` a parent directory would add.
## [0.7.16.0] - 2026-09-27

### Security
- **The dashboard runs no script but its own.** The page now ships with a strict Content-Security-Policy: script only from the page itself, the six pinned CDN files it names (each still checked by its SRI hash) and Plaid Link's loader; no inline script, no `eval`; the intercom's WebSocket only on the page's own host; frames and API calls only to Plaid; nothing may embed the page. This closes a chain the pre-launch review found: a bank memo is text anyone can write, the assistant turns memo text into a Vega-Lite chart spec, Vega's default evaluator compiled a spec's expressions into JavaScript on the page, and any script on that page can type into the intercom, where `!` runs a shell. The theme bootstrap that lived in an inline `<script>` moved to `theme.js`. Inline *styles* stay allowed on purpose: xterm.js and Plaid Link both inject `<style>` elements, and CSS cannot reach the socket. Plaid's loader is unversioned and updated in place, so it cannot carry an SRI hash; Plaid's own CSP guidance is followed instead, and Link was checked in a real browser under the new policy (it opens, with zero violations logged).
- **Charts are drawn by Vega's interpreter, and a spec may carry no expression at all.** The page embeds every chart with `ast: true` and `vega.expressionInterpreter` (vega-interpreter 1.2.1, SRI-pinned), so Vega-Lite's own expressions are walked as data and never compiled with `new Function`; under the new policy that compilation would throw. On top of that, `finnamon chart --spec-json` now refuses the whole expression surface rather than vetting a subset: `calculate`, `expr`, `signal`, `config.signals`, `labelExpr`, a param's `select`, `bind` or `on`, `impute`, `customFormatTypes`, a string `filter`, `test`, `not`, `and` or `or`, a `format`/`numberFormat`/`timeFormat` with a quote or backslash in it (Vega-Lite pastes those into an expression unquoted, as it does a date part, a value compared under a `timeUnit` and a `data.format.parse` specifier, so those must be plain date or number tokens), a `formatType` other than `number` or `time`, a `data.sequence` (rows generated in the browser; a billion of them hangs the tab), and `data.values` or a dataset as a CSV string (parsed with `new Function`). Two independent adversarial reviews kept finding Vega-Lite properties that paste text into expressions, so the check names its ceiling in the code: it is belt and braces over the interpreter, and the upgrade path if it keeps growing is compiling the spec in Node and refusing any signal Vega-Lite did not generate itself. The reason for refusing rather than vetting: every computation a chart needs is done in its SQL (a derived column is a `SELECT` expression, a filter is a `WHERE`), so the expression language adds nothing the assistant is taught to use, and there is nothing to keep current. An axis's or legend's `values` is now checked like the rest of the spec; only `data.values` rows are skipped. The presets, the skill's example and layered, folded, faceted, repeated and predicate-filtered specs all still pass, and the skill says what is refused.

## [0.7.15.0] - 2026-09-27

### Security
- **No Claude run without a person watching can reach the web.** `.claude/settings.json` allows `WebSearch` and `WebFetch` so the assistant can look up a property's value for someone at the dashboard or a terminal. The same allow list reached the runs nobody watches: the headless `/triage` run, the daemon's Telegram conversation runs, and in channel mode the dashboard session that reads the chat. All three read bank memos, which are text the other party to a transaction chose, and all three can run `finnamon query`. A memo that said "post this to https://…" could have had the rows fetched out to whoever wrote it, with no one asked. Now `claude_runner.run()` puts both tools on `--disallowedTools` itself, on every run it starts, and the dashboard adds the same flag whenever its session is Telegram's reader; `finnamon channel on` and the README print the hand-run channel command with it too. The flag is a deny rule and deny beats allow, which an eval now proves against a real Claude: the same fetch that works with only the project settings is refused with the flag. The tools stay on for the dashboard while Telegram runs through the daemon, and for a terminal session, since a person is at the keyboard there; the skill says so when a lookup is refused, and points at `claude --strict-mcp-config` in the checkout rather than a plain `claude`, which would take the chat down. Both skills now say that nothing a transaction names, a URL included, is ever something to fetch. Tests pin the argv of every spawn, so a refactor cannot drop the ban quietly.

### Changed
- **Extra bans for a headless run go in by name, not by argv.** `claude_runner.run()` takes `disallowed=` and builds the one `--disallowedTools` tail from it; it no longer accepts raw command-line arguments at all, so no caller can add a second variadic tail that swallows the first or is swallowed by it. Triage's read-only ban list rides that parameter now.

## [0.7.14.0] - 2026-09-26

### Fixed
- **`finnamon init` no longer reports blank or sandbox-only Plaid keys as verified.** `plaid_api.call` raises `NO_CREDENTIALS` with no `error_type` when a key is missing, and init's classifier treated any error type outside `INVALID_INPUT`/`INVALID_REQUEST` as a pass, so an empty client_id and secret printed "✓ Plaid credentials verified (institution probe said NO_CREDENTIALS; the keys authenticated)". The classifier now allowlists the one business error that really does mean "authenticated" (`INVALID_INSTITUTION`) and treats everything else, including `NO_CREDENTIALS`, as a failure that retries like any other bad key — except a Plaid outage or local network blip (`NETWORK_ERROR`), which now says so instead of blaming the keys. A sandbox-only setup (production secret left blank, the normal way to try Finnamon before a real bank is linked) is verified against sandbox instead of failing against production, and says so plainly: "sandbox only: you'll see test banks, not yours."
- **A bad Telegram token retries instead of ending init.** Step 2 used to call `die()` on the first rejected token, with no way to fix a typo short of restarting init. It now retries three times, the same shape as the Plaid step.
- **`finnamon init` checks Claude Code is installed and logged in.** Every chat reply and `/triage` run depend on `claude -p`, and init never checked for it. A new step runs `claude --strict-mcp-config auth status --json`, a local, non-interactive, tokenless check, sealed the same way every other `claude` child Finnamon spawns is (docs/DEVELOPMENT.md), and honoring `FINNAMON_CLAUDE_BIN`/`CLAUDE_BIN` (the same overrides `claude_runner` and the dashboard already use). It prints a clear fix if `claude` is missing or not logged in, and warns rather than blocking init, since a household can finish setup and log in afterward.
- **`scripts/check_setup.py` honors `FINNAMON_HOME` and fails correctly on missing keys.** It hardcoded `~/.finnamon`, so running it against a scratch `FINNAMON_HOME` (as tests and this fix's own verification do) silently read the real household's secrets file instead. It now resolves the same way the rest of Finnamon does. Separately, `--prod` with no `production_secret` printed "skipped" but never marked the run a failure, so a missing key under `--prod` still exited 0.

## [0.7.13.0] - 2026-09-26

### Fixed
- **A laptop that slept overnight no longer wakes up to a false "Last sync was…" alarm.** On wake, launchd runs the hourly heartbeat right away, before the daemon's catch-up sync has finished. The heartbeat used to report the stale sync on the spot, and because it sends at most one alert a day, a real outage later that day then went unreported. Now the heartbeat gives the daemon up to 2 minutes to show it is alive. A daemon still silent after that is dead, and that is reported immediately. A live daemon gets a grace period, which the next successful sync ends. The sync is reported only if it is still stale 50 minutes into the grace, meaning syncs that keep failing or one that has hung, and the alert then says the syncs aren't finishing rather than asking whether the daemon is running. A wake restarts the grace unless a sync attempt has finished and failed since it began, so a laptop that slept in the middle of a catch-up is not treated as an outage. A check that holds off never uses up the day's alert, and a healthy heartbeat no longer writes to the database at all.
- **A daemon that keeps crashing is reported, once.** Under launchd `KeepAlive` or systemd `Restart=always`, a daemon that dies at startup (a failed harness check, a thread that dies, any exception while starting) used to be restarted silently forever. The daemon now records its recent starts and its last error. A clean stop (the SIGTERM from `finnamon update` or `install`, or Ctrl-C) takes its own entry back, and 10 minutes of uptime clears the record. Three or more starts without that means a crash loop, and the heartbeat says so once per loop: "The daemon keeps crashing (N starts since …): <last error>", with where the full log is on a Mac and on Linux. It points at the log on purpose and never says "run `finnamon daemon`", because a second daemon on the same Claude session corrupts it.
- **A SIGTERM no longer looks like a crash to the daemon itself.** The worker threads leave their loops on a stop, and the main loop used to read that as "thread(s) died" and exit 1. It now checks for the stop first.

## [0.7.12.0] - 2026-09-25

### Fixed
- **Asking for a chart no preset covers puts one on the dashboard again.** Asked for "restaurant spending month by month", the assistant usually drew nothing. The skill taught it to pipe the Vega-Lite spec in through a heredoc, and Claude Code's shell guard refuses any heredoc whose body holds JSON ("Contains brace with quote character"). In the household's unattended session a refusal is a denial, so the assistant tried route after route until it gave up. Now `finnamon chart --spec-json` also takes the spec as an argument, and a new `--sql "..."` supplies its top-level `data.sql`. That gives one command with the SQL in double quotes and the JSON in single quotes, a shape the guard accepts, and the skill teaches exactly that. It also says how to write an apostrophe inside the JSON: `’` in a title, `\"` in an expression. `--spec-json -` from stdin still works. `--sql` goes through the same read-only checks as any `data.sql`, and `--sql` without `--spec-json` is now an error instead of being ignored.
- **The eval lane tests the checkout it runs in.** `finnamon` on PATH is an editable install of one checkout, so evals run from a worktree were calling another checkout's CLI. The harness now puts the repo first on `PYTHONPATH`.

## [0.7.11.0] - 2026-09-25

### Fixed
- **`finnamon import --browser` gets past the bank's login: the person logs in first, and the session attaches afterwards.** HSBC US would not let the import in. The logon reached `/security` and stopped there with "Something went wrong ... reference: EAC", every time, while the same credentials worked in the person's everyday Chrome. The page's own console named the cause: `AuthenticationError` out of `transmitpolyfills...js`, Transmit Security, HSBC's risk engine, with three `417`s behind it. Two things were wrong with the old flow, and only the second one mattered. The window agent-browser launched answered `navigator.webdriver` as `true`, which is the first bit any bot check reads; turning that off was not enough, because Transmit also sees the DevTools client itself, and driving a browser at all means a DevTools client. What it does not do is look again once the person is through. So Finnamon now opens Chrome itself, with the debugging port and no automation switch of any kind, and nothing is attached while the person logs in. The session asks them to log in, waits for them to say they are in, and only then attaches. Measured rather than guessed: a browser holding the port open but with no client attached reports `navigator.webdriver` as `false` and logs in fine, and attaching after the login leaves the session intact. The port is `--remote-debugging-port=0`, so the kernel picks a free one and a Chrome the person is already debugging on 9222 cannot collide, which it silently did; the real port is read back from the profile's `DevToolsActivePort` and probed on both `127.0.0.1` and `[::1]`, because which family Chrome binds varies and guessing the wrong one attached a session to the wrong browser. No automation switch also means Chrome stops showing its "unsupported command-line flag" banner.
- **The session stops waiting when the bank has refused it.** The old skill polled for ten minutes whatever the page did, so a refused login burned the full timeout before saying anything. It now stops as soon as the person says the page shows an error and offers the route that needs no browser at all: log in to the bank normally, download the CSV by hand, and `finnamon import` it.
- **The login page no longer goes into the transcript.** While waiting, the skill used to take an accessibility snapshot of whatever was on screen every five seconds, which on a login form with "Show Date of birth" turned on read the person's username and date of birth into the session's own transcript. It waits on the person now and takes no snapshot until the address has left the login and security pages.
- **`get text` is gone from the session's grammar.** agent-browser wants a selector after it and the grammar allows exactly one word, so the command could only ever error. `snapshot` is how the session reads a page.

### Security
- **The address of the browser stays in the environment and out of the command line.** The session may attach to exactly one window, and `finnamon hook browser-guard` pins it against `FINNAMON_IMPORT_CDP`: any other address is refused, and `open` and `--headed` are refused outright, at every bank, so the session can no longer point a browser anywhere itself. The address is not written into the `--allowedTools` list, because that list rides the exec'd argv and `ps` shows argv to every user on the machine while `environ` does not, and what the address opens is an unauthenticated DevTools endpoint into a window logged in to the bank. `FINNAMON_IMPORT_BANK` is gone with the `open` it used to gate. Chrome is launched as an argument list, never a shell string, and the three values in it, the executable, the profile and the bank's URL, are all fixed or operator-supplied before the sealed session exists.
- **The sealed session says so up front.** The guard blocks everything outside its grammar for the life of the session, `git` and `gh` included. That is intended, and the skill now states it, so a session asked mid-import to file an issue says plainly that it cannot instead of trying variants of a blocked command.

## [0.7.10.1] - 2026-09-24

### Changed
- **Each chart on the dashboard gets a row of its own, the full width of the page.** On a wide screen the board used to fill a row with as many 440px-or-wider panels as fit, so two or three charts sat side by side and each one was squeezed. The board is one column at every width now, which is what the phone layout already did, so the phone's own rule for it is gone. The presets keep their 300px height; only the width changes. No chart needed touching: presets, and any custom spec that gives no width, are `width: container`, and the page redraws every panel whenever the board changes or the window is resized, so charts already on the board stretch as well as new ones. A custom spec that sets a numeric width keeps that width, now inside a wider row (#33).

## [0.7.10.0] - 2026-09-24

### Fixed
- **The dashboard's Import tab has a Stop button.** Once Fetch by AI started a browser-import session there was no way to end it from the page: the only exit was typing `/exit` or Ctrl-C into the terminal, and the one place that said so was the text of the 409 a second Fetch by AI got back. A person who could not log in sat and waited out the skill's 10-minute login timeout. Stop sits next to the tab's status while a session is live and ends it through the session's own `stop()`, with a three-second grace before SIGKILL. The runner drops the session the way it does for any other exit, so the bank's pages are still never replayed. The terminal now says `import session stopped` for a Stop and `import session ended (<code>)` for anything else, live rather than only in a buffer nobody saw. Typing into an ended import says so in the status line instead of going nowhere. A stop frame reaches only the import session. One aimed anywhere else, or at nothing, is dropped, so it can never end the household's session and with it the chat's reader, and its promise is caught where every other bad frame is, because an unhandled rejection would exit the dashboard.

## [0.7.9.0] - 2026-09-24

### Fixed
- **`finnamon import --browser` opens real Google Chrome, and the bank remembers it between runs.** The session's one `open` had no `--profile` and no `--executable-path`, so agent-browser started its own Chrome for Testing in a throwaway context. Banks can spot that build: it reports itself as automated, has no DRM components, and cannot hold a passkey. And `--restore` brought back only cookies and localStorage, never the bank's device registration, so the person got a new 2FA code every run, the cost the skill said `--restore` saved them. The launcher now runs the person's installed Google Chrome on a profile of Finnamon's own, `~/.finnamon/chrome/`, created owner-only (0700). That profile is what keeps the known-device state, and it holds cookies, never a password. It is not the person's everyday profile for two reasons: a session that reads bank pages should not get every cookie they own, and Chrome locks a profile that is already open, so the import would fail whenever their own Chrome was running. `--restore` is gone, so there are no longer two stores of bank cookies that can disagree. If Chrome is not installed (or `FINNAMON_CHROME` names nothing that runs), the command stops with a message instead of quietly going back to the testing browser, since from the outside that fallback would look like everything worked.

### Security
- **The launcher picks the browser and profile. The model cannot.** Both values go in the environment of the `claude` the launcher execs, as `AGENT_BROWSER_EXECUTABLE_PATH` and `AGENT_BROWSER_PROFILE`. They are not on the `open` line and not in the guard's grammar. `--executable-path` is code execution, and `--profile Default` would hand over every cookie the person owns. The session's input is bank pages and transaction memos, text anyone can write, so it must not be able to set, change or drop either one. The guard still refuses every launch option (`--profile`, `--executable-path`, `--restore`, `--proxy`) and an env assignment in front of the command. Any other `AGENT_BROWSER_*` setting in the person's shell (auto-connect, a CDP URL, extra Chrome arguments) is left out of the session's environment, so it cannot attach to their everyday Chrome. `.claude/settings.json` blocks the household assistant from reading `~/.finnamon/chrome/`, the same way it blocks `~/.agent-browser/`. If a Finnamon browser is already open on that profile, the skill tells the person to close it rather than showing Chrome's lock error.

## [0.7.8.0] - 2026-09-24

### Added
- **The dashboard's "8 banks, 24 accounts" opens the list behind it.** The header gave you a count and no way to see what it was counting. Now the count is a button: it opens a window with a heading for each connected bank and that bank's accounts under it, each with its name, the last four digits and its type. The rows use the header's own filter, so the list always has as many rows as the number you clicked, and a joint account seen from both logins shows once, not twice. Each heading says when that bank last synced; a bank that needs a new login, or has stopped syncing, gets a warning tag in place of the time. A bank added by hand for CSV imports says so, with the time of its last import. An account whose bank has no connection record still goes on the list, in a group of its own at the end, so the numbers still match. With no bank connected there is no count, so there is nothing to click. Bank and account names come from the bank and are escaped before they reach the page. The window closes with Escape or a click outside it, and focus goes back to the count.

### Changed
- **On a phone the status line is back, on a row of its own under the header's controls.** It used to be hidden below 700px wide to make room. It can't be hidden now that the count is the only way to open the list, and the header row it shared with the controls was already 12px wider than a 375px screen. The logo and the controls keep the first row, spaced slightly tighter, and nothing on the page scrolls sideways any more.

## [0.7.7.0] - 2026-09-24

### Fixed
- **Add account in the dashboard's Import CSV window adds the account.** It failed every time with argparse's usage block and `unrecognized arguments: -- HSBC Checking`. The dashboard sends the flags first and the name last, after a `--`, so an account called `--help` stays a name; before Python 3.14, argparse fills every optional positional from the lone word before the first flag, so `account add` had taken `new` and `existing` as empty and the name arrived with nowhere to go. The assistant never hit it because it writes the name first. The fix is in the parser, not the dashboard, since moving a name someone typed ahead of the flags is the hole the `--` closes: when argv leaves anything over, the subcommand is parsed again with `parse_intermixed_args`, which is what this ordering needs. That reparse only sees what follows the subcommand, so a stray word or flag *before* it is rejected rather than quietly dropped. It is one place, so `owner`, `chart`, `category` and the rest of the commands with the same shape are covered too. Each half was tested on its own: the Python tests all used the name-first order, and the web tests checked the argv a route builds against a fake exec. A test now pulls every argv the dashboard writes out of `web/server.js`, puts a flag-shaped value in each slot a person fills, and runs it through the real parser. `account merge`, `unmerge`, `failover` and `owner` also answer a missing argument with their usage line, where before they wrote an empty value.
- **`finnamon` runs on Python 3.14.** The `alias` help text had a bare `%`, which 3.14's argparse reads as a format character while it builds the parser, so every command failed before it started. It is `%%` now.

## [0.7.6.0] - 2026-09-24

### Fixed
- **A terminal in this directory is not automatically the household.** This checkout is also where Finnamon is developed, and nothing in the assistant's instructions said which it was talking to, so a session that hit a wall on a household question edited the source to get past it and left the edits uncommitted. `AGENTS.md` now goes by what is asked, not where it came from. The household's money (balances, budgets, aliases, alerts, a chart of their spending, a drafted detector) is handled with `finnamon` commands and never a file edit. A change to how Finnamon works is development: asked for outright it is simply done, arrived at from a money question it is named as a code change and needs a yes first. Telegram, the channel and `/triage` never lead to a file edit, whatever they ask, because there is nobody at a keyboard to confirm with. The never-edit rule now covers the whole repository while acting as the assistant, not just the two detector folders, and the skill no longer calls the dashboard's intercom and a terminal in the checkout the same thing.
- **The eval lane now tests the assistant the household actually gets.** It ran `claude` without `--setting-sources project`, so a developer's own settings and hooks leaked into it. Asked the dining question, one unsealed run replied as a code reviewer. The lane now uses the same flags as the daemon's session. `AGENTS.md` and `CLAUDE.md` are on `docs/DEVELOPMENT.md`'s list of prompt files, because every session reads both, yet changing either never required the eval lane.

## [0.7.5.0] - 2026-09-23

### Added
- **The assistant can draw any chart you ask for, not just the five it came with.** Asked for "a month spending chart" that no built-in kind covered, the assistant edited `finnamon/charts.py` to add a sixth hardcoded one: the chart menu was a menu, not a language, so the only way to a new chart was a code change made by the household's own assistant. Vega-Lite was picked so it could describe almost any chart, and now it can: `finnamon chart --spec-json -` takes a whole Vega-Lite spec on stdin, with layers, facets, transforms, dual axes and tooltips, and one extension, `data.sql`, so the rows come from a read-only query over `tx_now` instead of being copied out by hand. The query goes through the same path as `finnamon query`: a read-only connection, `query_only`, SELECT or WITH only, so a spec can read the household's money and never change it. The skill now tells the assistant that a chart no preset covers is a spec and never a code change, and an eval asks for restaurant spending by month and fails if the checkout is touched.
- **The dashboard shows several charts at once.** `~/.finnamon/charts/current.json` is a list of up to eight, each with an id: the same id replaces a chart where it stands, so "make it weekly" updates the one on the board instead of piling up another, and the oldest drops off when a ninth arrives. `--list` shows what is on it, `--remove <id>` and `--clear` take them off. On the page every chart gets its own panel with an ×, the preset chips toggle their chart on and off, and a phone gets one column. A file written by an older release, one spec rather than a list, reads as a board of one.

### Security
- Merchant names and memos come from the bank and from whoever made the transaction, and they flow into these specs, so nothing in a spec may make the page reach outside it. The CLI refuses a `url` or `href` key at any depth, which covers `data.url` at the top level, inside `layer` and inside a lookup, and it refuses the image mark, the other way Vega-Lite loads a URL, and a param's `bind.element`, which would put its widget anywhere on the page. It also refuses any `$schema` but Vega-Lite v5, because full Vega brings signals and loaders, and it drops `usermeta.embedOptions`, which vega-embed merges over the page's own options: through its source-view action and `sourceHeader` a spec could otherwise put script on the dashboard's origin, the origin that hosts the intercom's shell. A spec over 256 KB is refused. As a second line of defence the page pins `mode: 'vega-lite'`, strips the same `usermeta`, and gives Vega a loader that loads and links nothing.

## [0.7.4.0] - 2026-09-23

### Fixed
- **A merchant you renamed or recategorised is that merchant everywhere now, not just to the detectors.** `finnamon category` and `finnamon alias` were honoured by the alert rules and by budgets, and ignored by `finnamon query`, the dashboard's chart panel, the MCP tools and part of `finnamon budget suggest`. The assistant answers category questions with `finnamon query`, so it read the stale Plaid label off the raw table and reported it as fact: asked what was in Rent and Utilities on 2026-09-22 it listed four 1st Security Bank mortgage payments, $14,423.96 of them, a night after the household had told it they were the mortgage. The resolution the detectors use is now a view, `tx_now`, so arbitrary SQL can select from it; that is what makes the assistant's own queries correct, since there is no call site to fix on a path where the assistant writes the SQL. It carries the prelude's rule exactly -- entity id over alias over Plaid's name over the bank string, the category override on top, joint-account mirrors dropped -- minus the `as_of` scoping, because a detector has to replay an alert as of the day it fired and a question about today's money wants today's aliases. Both files say they must stay in step and a test fails if they drift, in any column, not just the ones it reads. The second copy of the alias rule that lived in `budgets.py` is gone. `finnamon budget suggest` no longer shows a category total that none of its listed merchants add up to, because the merchants and the total now come from the same relation over the same months. Charts, the MCP tools and the linked-bank summary read it too, so the number the chat gives you, the number on the dashboard and the number a detector alerted on are one number. Searching a merchant's history finds it by the household's name for it, by Plaid's, or by the raw bank string, where before only Plaid's worked. Money that turns out to be a loan payment leaves the spend chart rather than sitting in the wrong bucket, which is the point of having said so.

## [0.7.3.0] - 2026-09-23

### Fixed
- **`finnamon install` starts a fresh assistant, which is what it already said it did.** The split `update` was built around is that `update` keeps the household's conversation and `install` starts over, but nothing ever retired the session: the id lives in `~/.finnamon/intercom.json`, not in the service files, and the dashboard resumes whatever it finds there on every start, however it was started. Rewriting the unit files and reloading every job therefore reloaded the same conversation. `install` now retires the stored id before it touches the jobs, and asks first, because the blast radius is the household's whole conversation and in channel mode that is the Telegram chat's thread too. It asks about the command rather than the session: there is no install-but-keep-it, that is `update`, so declining leaves the services alone as well. The retired id is not deleted but left behind as `prev`, and printed: the transcript is still on disk under `~/.claude/projects`, a reload that fails after the retire reads to a person as "nothing happened", and the id is the only thing that can reach that conversation again. The id the dashboard itself could not resume is kept and printed for the same reason. The printed command carries `--strict-mcp-config`, because the transcript lives under this directory and a plain `claude` here starts a second copy of the Telegram plugin's server and kills the household session's: reading what you lost must not cost you the chat as well (v0.7.1.0). `--dry-run` retires nothing and now says what it *would* retire, since `update` is what sends people to look at it; `--yes` skips the question; no terminal to ask in refuses rather than assuming yes; anything in that file that is not a record we wrote is left alone; and nothing to retire is never a reason not to install. `install` also refuses to run from a Claude session now, which `AGENTS.md` has always said it did.

## [0.7.2.0] - 2026-09-23

### Added
- **Finnamon notices when nothing is reading the chat.** In channel mode the Claude Code Telegram plugin is the bot's only `getUpdates` consumer, and when it dies the household sees nothing at all: the session stays up and answers the dashboard's own terminal, sending still works, and every status surface said "All good". On 2026-09-23 the chat was dead for twenty minutes and the only symptom was messages going unanswered. Telegram is the one place that knows, so the daemon asks it once a minute with `getWebhookInfo`, which reads the backlog without collecting it and so cannot take the one slot from a plugin that is alive. A backlog that outlives a five-minute grace raises a `channel_deaf` alert and sends it at once rather than waiting for the next sync cycle, which `sync_interval_hours` can put six hours away; sending still works while receiving is broken, which is the only reason this is reportable at all. The dashboard's status pill and `finnamon status` carry the same state, and a dead daemon is named ahead of it, because the daemon is the only thing that clears the stamp. Things that are deliberately not reported: an idle household (nothing is waiting when nobody has written), a reader taking its time over one answer (the grace is well past the slowest turn), and a reader that crash-loops, which begins a fresh outage every few minutes and is told about once every six hours instead of once a loop. Leaving channel mode clears the stamp, so `finnamon channel off` does not leave the dashboard blaming a dashboard that is no longer in the picture.

## [0.7.1.0] - 2026-09-23

### Fixed
- **The headless triage session is sealed against the person's own Claude settings.** It runs with `--setting-sources project`, the same seal `finnamon import --browser` already puts on its session and for the same reason: `~/.claude/settings.json` may allow `Bash` outright, and triage is a `-p` run with nobody at the keyboard, reading merchant names and memos that are text whoever made the transaction chose. The project allow list has every `finnamon` verb triage uses and no bare `Bash`; the repository's deny list protected `secrets.toml` only against the `Read` tool, never against `cat`.
- **Triage no longer takes Telegram down with it.** The channel plugin is installed local to this checkout, so every headless `claude` the daemon ran in it (triage after a sync, and the conversation worker outside channel mode) started a second copy of the plugin's server. The household session's copy dies about a second after a second one starts, and nothing reconnects it: in channel mode that session is the bot's only reader, so the chat went quiet until someone restarted the dashboard, while the dashboard's own terminal kept answering normally. Headless runs, the browser-import session and the eval lane now start with `--strict-mcp-config` and no `--mcp-config`, which is no MCP servers at all; they drive `finnamon` and `agent-browser` over Bash and never needed one. The eval lane mattered because `CLAUDE.md` makes it mandatory after a prompt change, so the required run reproduced the outage every time it was asked for. Measured on the box: the same probe run without the flag killed the household's server within a second, and with it the server stayed up untouched.

## [0.7.0.0] - 2026-09-23

### Added
- **`finnamon update`: pick up a release without losing the assistant.** It pulls (fast-forward only, refusing a tree with uncommitted changes to tracked files), waits for the run lock so it never migrates or restarts on top of a sync in flight, then restarts only what the pull made stale: a change under `finnamon/` restarts the daemon, `web/server.js` restarts the dashboard, and a change under `web/public/` restarts nothing at all, because the page is read off disk on every request and a reload is the whole update. Anything it cannot place restarts both, so a release that changes `.claude/` (the assistant's instructions and permission set) is adopted rather than silently ignored, and it says when a release does that. It names what it adopted and from where, checks that each service is still running afterwards rather than trusting that the restart command returned, and says which services already moved if a later one fails. `--no-pull` adopts a tree you pulled by hand, `--all` restarts both regardless, `--dry-run` fetches and reports without pulling, migrating or restarting. If a release changes the service files themselves, `update` stops before touching anything and sends you to `install`, and says so if the only difference is the PATH of the shell you ran it from.
- **The household's Claude session survives a dashboard restart.** Its id is minted once into `~/.finnamon/intercom.json` (0600, like the rest of that directory) and resumed on every later start, so the assistant comes back knowing the conversation instead of meeting you for the first time. In channel mode that session is also the one Telegram talks to, so this is the chat's context too, not just the page's. The terminal comes back without its scrollback either way: what is restored is the conversation, not the screen. A stop you asked for is never mistaken for a session claude refused, so two updates in a row cost nothing; when an id really is replaced the old one is kept alongside it, because a fast exit can equally mean an expired login and the transcript is still on disk. Shutting down waits for the old session to end before the next one starts, and the daemon takes its own `claude` children with it, so two processes can never hold one conversation.

### Changed
- `finnamon install` is now the reprovisioning command rather than the way to pick up code: it rewrites the unit files and reloads every job, which starts the assistant fresh. `finnamon update` is the routine one. Both stay for a person at a terminal.

## [0.6.1.0] - 2026-09-23

### Added
- **Add a manual account from the dashboard.** Import CSV now opens as a window over the page and carries an **Add a manual account** row: a name, the bank, and what kind of account it is. A bank Plaid can't reach no longer needs a terminal. One row per account and as many as the bank has, so HSBC Checking, HSBC Savings and HSBC Credit Card all sit under the one HSBC; the account you just added is the one selected for the import that follows.

### Fixed
- A linked bank's summary no longer prints **Recurring I can see:** with an empty bullet under it. A stream the bank left nameless is left out, and a section where every row is nameless loses its heading too. The name is looked for in the stream's description as well, so a stream Plaid hands over with an empty merchant name is listed under the name it does have instead of vanishing from the list and from the 90-day spend that ranks it. Same for the `new_recurring` and `recurring_changed` detectors, which build their alert from the same two columns.
- **Possible duplicate** and **New recurring charge** alerts say "an unnamed charge" and "an unnamed payee" where the bank sent no name, instead of leaving the slot blank. A name of nothing but whitespace (a tab or a non-breaking space out of a CSV export) now counts as no name everywhere a bank descriptor is rendered, not just in the linked-bank summary.
- Escape, the backdrop and Cancel all close the import window and put the keyboard back on the Add account control rather than at the top of the page. A click on the window's own padding no longer closes it and throws away the file you picked.
- The dashboard refuses an account whose bank name **Fetch by AI** would later refuse, because nothing renames one afterwards; and it offers exactly the account types the CLI accepts, with a test that fails if the two lists drift apart.
- Adding an account reports the name and bank actually stored rather than what was typed, so an import can't be aimed at an account that isn't there. If the page can't refresh itself afterwards it says so instead of leaving a dead button and an import pointed at nothing.

## [0.6.0.0] - 2026-09-21

### Added
- Banks Plaid can't reach (HSBC US personal banking shows in Plaid's search and then says "not supported"). `finnamon account add "HSBC Checking" --institution HSBC` (or ask the assistant) makes a manual account; its transactions come from the bank's own CSV export. `finnamon import "HSBC Checking" <file.csv>` reads any bank's export (header or not, one amount column or debit and credit, a running balance if there is one), imports a file only once however many times it is handed over, and stamps the balance into net worth. `--dry-run` shows a sample first; `--flip` for an export that shows money out as positive; `--balance` when the file carries none.
- **Add account** on the dashboard, one control in place of two: Link account (Plaid) or Import CSV. The Import CSV panel takes the file, or **Fetch by AI**: a separate Claude session opens a browser window on the box, you log in to the bank yourself, and it downloads and imports the export while you watch in the intercom's Import tab. The household's own session is never used for it. On another computer with a checkout, `finnamon import --browser hsbc --to https://<the box>` does the same and uploads the file; `finnamon import --to` and `finnamon account list --to` work the same way by hand.
- The browser-import session runs on a permission set of its own: a fixed grammar of browser verbs on one named session, the bank's login page, and the import command; a guard hook blocks anything else, the household's allow list never reaches it, and inside it the CLI refuses every other command and any upload target but the box it was started for. A bank page is data to it, never an instruction.
- A manual account nobody has imported into for `import_max_age_days` (35 by default) gets a nudge in the chat, the way a Plaid account that stopped syncing does.

### Changed
- Detectors know about manual accounts: an imported row (no merchant id, no clean name) is never a "can't tell what this is" anomaly, and a large transfer is not "unmatched" while a manual account's last import predates it. The first import is history, nothing in it is flagged; from the second on, the last week reaches the alerts.
- `finnamon account list` now shows each account's `source` (`plaid` or `manual`) and last sync or import.
- The dashboard's import route answers every error as JSON (an oversized file included), refuses a balance that is not a number, keeps only what actually landed under `~/.finnamon/imports/`, and drops a finished import session's transcript instead of replaying the bank's pages to the next visitor.

### Fixed
- A balance-history chart with an account that has no last-four digits no longer labels its line `None`.

## [0.5.4.0] - 2026-09-21

### Added
- Pattern aliases (issue #18): `finnamon alias "Loan Payment Confirmation#%" "Bank of America mortgage"` covers every payment of a payee whose bank string changes each month (a mortgage confirmation number, a Venmo memo). `%` matches any run of characters, `_` one, as SQLite `LIKE`. An exact alias wins over a pattern, a longer pattern over a shorter one; one alias per transaction. The prelude resolves it as of the run timestamp, so alerts still replay; the budgets, the suppression-from-alert path and `canonical_for` use the same rule (a test holds the two copies together). A pattern needs at least three literal characters, and `finnamon alias` reports how many past transactions it covers.

## [0.5.3.6] - 2026-09-21

### Fixed
- The Telegram channel plugin names its tools `mcp__plugin_telegram_telegram__reply` and `…__react`; the assistant's allow list and the reply-guard hook in `.claude/settings.json` named them `mcp__telegram__reply` / `…__react`, so in channel mode every reply was refused (dontAsk mode) and the guard never ran. Renamed.

## [0.5.3.5] - 2026-09-21

### Fixed
- The installed jobs' PATH includes bun (`~/.bun/bin`, or wherever the shell running `finnamon install` finds it), so the Telegram channel plugin can start inside the dashboard's intercom session. Before, the session showed the channel banner while nothing polled the bot, and a DM went unanswered. `finnamon channel on` and `finnamon install` now warn when Bun is missing, and `channel on` says that with the dashboard installed the next step is `finnamon install`, not a tmux session.

## [0.5.3.4] - 2026-09-21

### Changed
- The net-worth trend can show assets and liabilities beside net worth: click them in the legend (off by default, because on one scale a large stated property flattens the net-worth line; the choice is remembered in the browser). `finnamon networth --history` now reports assets and liabilities per day as well.
- The Property row's pencil sits beside the amount on hover instead of taking a column of its own on every row.

### Fixed
- The live net worth and its history classify accounts by one rule: credit and loan are liabilities, everything else the bank reports is an asset. Before, an account of Plaid's legacy `brokerage` type (or one with no type) counted in the trend but not in the figure above it.

## [0.5.3.3] - 2026-09-21

### Fixed
- The Property row's value sat one column left of the others in the net-worth ledger; every row now shares the same tracks, with the pencil slot empty where there is no pencil.

## [0.5.3.2] - 2026-09-21

### Changed
- The net-worth section is the page's summary now, open on the background above the cards: the figure and its monthly change, a ledger of cash, investments, property and liabilities with each one's share of assets, and beside them a trend of net worth over the last 3, 6 or 12 months drawn from the balances every sync records; the figure counts up to its value (set at once under reduced motion). The theme toggle is a plain icon.

### Fixed
- The net-worth trend and the "this month" change summed every sync's snapshot within a day, so a day with several syncs read as several times the real figure. Each account's last snapshot of the day counts now.
- Linking a bank no longer pushes the net-worth figure out of line: the link result is a toast in its own row under the header, centered, the page moving down for it (a success fades, a warning stays until closed, a spinner while the first sync runs), and the "N banks, M accounts" count sits in the header next to the sync time.

## [0.5.3.1] - 2026-09-21

### Changed
- The `VERSION` file is the one version source: `pyproject.toml` builds from it and `finnamon --version` / `finnamon status` read it live, so a `git pull` is enough and `status` no longer reports the 0.4.0 the package was first installed as.

## [0.5.3.0] - 2026-09-21

### Fixed
- **Recent alerts** on the dashboard shows what Finnamon sent to the household (newest first, last 30 days, up to 8), tagged open or resolved, plus anything queued for sending that has not gone out yet, tagged unsent. Triage's own notes on candidates it judged normal or not worth a message no longer appear there; ask the assistant "what did triage decide not to mention" for those. `finnamon alerts --sent` is that same list.
- Triage sentences kept losing their dollar amounts ("$12.14" arrived as "2.14"): the shell expanded `$1` inside the double-quoted argument. `finnamon triage set ... -` now reads the sentence from stdin (a quoted heredoc, which the triage skill uses), refuses an empty one, and never waits more than two seconds for it.
- A recurring-charge anomaly's headline showed a blank where the amount belonged; it now names the tracked charge ("$12.14 to Apple").

## [0.5.2.0] - 2026-09-21

### Fixed
- **Link account** in the dashboard failed with `INVALID_FIELD: redirect_uri must use HTTPS`: Plaid accepts HTTPS return addresses only outside Sandbox, and the page was sending `http://localhost:7077/`. The page now sends its own address only when it is served over HTTPS (tailscale serve; register `https://<machine>.<tailnet>.ts.net/` in the Plaid dashboard) and nothing on plain http, where banks that use OAuth open in a popup. Nothing to register for localhost any more; README, `finnamon init` and the design doc say so.
- `finnamon link --token` takes the redirect address as optional and is exclusive with `--start`/`--finish`; a bare `--token` is refused from a Claude session like the rest of the flag. Asking Plaid for a Hosted Link session with a redirect at the same time is now an error instead of a token with no URL.

## [0.5.1.0] - 2026-09-20

### Changed
- `finnamon init` now has four steps: after the Plaid keys and the Telegram bot it installs the web dashboard (npm install in `web/`, skipped with a pointer when Node 20+ is missing or the checkout has no `web/`), then schedules the daemon, the heartbeat and the dashboard together. It closes by pointing at `http://localhost:7077`, where Link account connects the banks and the intercom is the Claude session ("set up my budgets"), so there is nothing else to start in a terminal. The Telegram welcome says the same. The URL is only promised when the scheduler step actually ran; npm's output streams to the terminal and is bounded by a 15-minute timeout.
- Asking the assistant "what's my house worth?" now looks the value up on the web and confirms the number before it is recorded as a property (the session's allow list gains WebSearch and WebFetch).
- README: setup is `finnamon init`, then open the dashboard.

## [0.5.0.0] - 2026-09-20

### Added
- `finnamon property set "<name>" <value>` / `property remove` / `property list`: things you own that no bank reports (a house, a car), counted into net worth. `finnamon networth` now also splits cash, investments and property.
- A web dashboard (`web/`, `node web/server.js`, installed as an always-on job by `finnamon install`) in Finnamon's own look (the logo, cinnamon on gray and white, icons): a net-worth strip (cash, investments, property, liabilities) with a health pill that reads like a person would say it (click the Property tile to state what you own), a **Link account** button that runs Plaid Link in the page, budgets with pace, net worth, recent alerts, and a chart panel. An intercom button in the corner opens the household's Claude Code session in the page (xterm.js over a WebSocket, the pattern from agent-007); the session stays connected while the panel is closed and the button shows when the assistant is waiting or has answered.
- `finnamon chart --spec <name>` writes a Vega-Lite spec the dashboard renders; asking the intercom "show me dining by month" or "make it weekly" re-renders the panel. Telegram still gets PNGs.
- `finnamon link --token <redirect uri>` and `finnamon link --public-token <token>`: the two halves of in-page Plaid Link (server-side use; refused from a Claude session).
### Security
- The dashboard answers only to its own address: a foreign `Host` header gets 421 (DNS rebinding), a foreign `Origin` gets 403 and cannot open the intercom's WebSocket (cross-site hijacking); its xterm.js and Vega scripts are pinned with integrity hashes (Plaid's loader must stay live and is not); names it passes to the CLI come after `--`.

## [0.4.0.0] - 2026-09-20

### Added
- Experiment: `finnamon channel on` hands the Telegram conversation to Claude Code's own Telegram channel plugin. The daemon keeps syncing, detecting and sending alerts but stops polling the bot; a `claude --channels plugin:telegram@claude-plugins-official` session you keep open receives the household's messages and replies through the bot, with Claude Code managing the session and relaying permission prompts to your phone. `finnamon channel off` returns to the built-in loop. `finnamon owner add <name> --user-id <id>` records a member without the code dance (the plugin's pairing gates who may talk). README has the setup steps.
- The channel session runs with `--permission-mode dontAsk`: the plugin would otherwise turn every non-allow-listed command into Allow/Deny buttons on paired phones, and a tap could approve something a prompt-injected assistant proposed. The bot token goes into the plugin's `.env` by hand, never through a Claude session. When the daemon hands the bot to the plugin it first acknowledges the messages it already handled, so nothing is answered twice. If two programs end up reading the bot, the daemon backs off, tells the household once after a few conflicts, and `finnamon status` shows `inbound_conflict_at`. In channel mode the assistant treats a Telegram id that is not a household member as a stranger (no changes for them), and a hook (`finnamon hook reply-guard`) blocks the plugin's `reply` tool outside the household chats or with anything but a chart attached; after `finnamon channel off` the daemon ignores messages the plugin had already answered. README lists what the experiment gives up.

### Fixed
- `finnamon owner add` without a name could insert a nameless member; it now refuses. New members added by id get a display name like the code dance gives, and the output says when an existing member's Telegram id was replaced.

## [0.3.2.0] - 2026-09-20

### Fixed
- Telegram messages name merchants the way a person would: bank descriptors lose their confirmation numbers and ACH `DES:`/`ID:`/`INDN:`/`CO ID:` fields (`AMERICAN EXPRESS DES:ACH PMT ID:A4476 INDN:... WEB` reads `AMERICAN EXPRESS (ACH PMT)`), in alerts and in the post-link summary.
- The post-link summary lists recurring charges and top merchants one per line, and "top merchants" no longer includes transfers between your own accounts or payments onto your own loans and cards.

## [0.3.1.0] - 2026-09-20

### Fixed
- `finnamon install` (and the scheduler step of `finnamon init`) on a machine where the daemon is already running failed with `launchctl bootstrap ... exit status 5`; it now waits for launchd to drop the old service, retries briefly, and reports a real failure in one line. On Linux, reinstalling restarts the daemon so it runs the new code.

## [0.3.0.0] - 2026-09-20

### Added
- Ask the assistant to add a bank: "add my Chase account" in a Claude Code session (or on Telegram) runs `finnamon link --start`, which gives you the Plaid link where you asked (in the terminal, or in the chat with `--telegram`) and returns at once. You log into the bank on any device; the daemon finishes the link within a minute (or `finnamon link --finish` does), syncs, and posts the account summary. If a new account looks like one already linked from another login, the summary says so and names the `finnamon account merge` command rather than deciding for you.

### Changed
- `finnamon link` no longer posts the Plaid link to the household chat unless you pass `--telegram`; the chat is shared, and the link is on your screen.
- A bank someone asks the assistant for is recorded as theirs: the assistant passes `--owner <who asked>`, which must be an existing household member.
- Asking to add a bank twice reuses the open Plaid link instead of opening a second one; a link you closed still works if you open it again (the chat says so once); a link Plaid no longer recognises is dropped so a fresh one can be started.
- From a Claude session, `finnamon link` accepts only `--start` and `--finish`; re-login (`--update`), unlinking (`--remove`) and the blocking form stay with a person at a terminal, enforced in the CLI as well as the permission list.

### Fixed
- Finishing a link survives a crash after Plaid handed over the token (resumed, never exchanged twice, so no orphaned billed Item); a Telegram hiccup during the announcement no longer reads as a failed link; a pending link can never delay the regular sync.

## [0.2.0.0] - 2026-09-20

### Added
- `finnamon link --remove <item_id>` unlinks a bank: Plaid stops billing for it, its accounts and history are dropped, its token is forgotten. It asks first unless you pass `--yes`, and it works even if the Item was already removed on Plaid's side or revoked at the bank.
- Linking now asks Plaid for 24 months of history (the default was 90 days, too short for budget suggestions) and for investment holdings where the bank supports them, so IRA and brokerage positions show up in `finnamon networth`.
- Holdings refresh with every sync, not only on `finnamon networth --sync`; an account that sells everything shows no holdings rather than a stale value.

### Changed
- The Telegram message after linking is a list of accounts (name, last 4, type) with no balances or amounts; the chat is shared and scrolls back forever. Ask for the numbers when you want them.
- If you re-link a bank whose old connection is broken, the joint-account prompt now points you at `--remove` for the old one instead of hiding the new one.

### Fixed
- A first sync that takes Plaid a few minutes (24 months at a large bank) no longer reports the bank as broken; it waits up to two minutes, then the daemon finishes the backfill.
- A first sync that fails at link time no longer loses the joint-account prompt and the summary.
- One bank's malformed data can no longer stop every other bank from syncing.
- `finnamon sync` and `link --remove` take the same run lock as the daemon, so they cannot interleave with it.
- Unsent alerts about a removed bank are resolved instead of going out later pointing at accounts that no longer exist.

## [0.1.0.0] - 2026-09-19

Finnamon is rewritten from a web app into a household finance watchdog: a daemon that syncs your bank accounts through Plaid, runs deterministic detectors over the data, and messages the household on Telegram, with Claude Code as the assistant that answers replies and judges the odd-looking transactions.

### Added
- `finnamon init`: an interactive setup that takes your Plaid keys and Telegram bot token, verifies both, enrols the first owner with a one-time code, and installs the always-on service (launchd on macOS, systemd on Linux).
- `finnamon link`: connects a bank through Plaid Hosted Link in the browser and posts a one-time summary of the new accounts to the household chat; joint accounts linked by a second person are detected and merged so nothing is counted twice.
- Sync every six hours with cursor-based `/transactions/sync`, balances, recurring streams, and investment holdings; a heartbeat that messages you when the daemon has gone quiet.
- Hard-rule detectors that alert immediately: duplicate charges, new recurring charges, low balances, budget pace, and bank re-login or sync failures.
- Anomaly candidates (first-time merchant, amount outlier, new category, no identifiable source, unmatched transfer, changed recurring amount) triaged by the `/triage` Claude skill; high-confidence findings go out at once, low-confidence ones wait for the Sunday roundup.
- Budgets by Plaid category with pace alerts; `finnamon budget suggest` and the `finnamon` skill let Claude propose budgets from six months of history and apply your edits.
- Two-way Telegram: reply "it is normal" to an alert and the household's shared Claude Code session records it; ask any money question and get an answer, or a chart as a photo.
- `finnamon query` (read-only SQL), `finnamon networth`, `finnamon chart`, `finnamon status`, and an MCP server for other tools.
- Custom detectors: Claude can draft one from a "watch for X" request; it only goes live after a person reviews it at the terminal with `finnamon detect --review`.
- Ubuntu support alongside macOS.

### Changed
- Notifications go to one household chat; every member sees everything, and one identity answers.
- Operational settings (sync interval, timeouts) can only be changed by a person at a terminal, never from a Claude session.

### Fixed
- Three rounds of pre-landing and adversarial review: a re-login prompt when Plaid rejects keys during setup, a Telegram update that can't be parsed no longer wedges the daemon, a failed roundup send is resumed on the next run instead of being marked done, transient Plaid outages don't mark a bank as broken, an expired enrolment code can't be used later, and budget or suppression amounts must be finite and positive.

### Removed
- The previous web application (backend and frontend submodules, Docker Compose, deployment and contributing docs).
