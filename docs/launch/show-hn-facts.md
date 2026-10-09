# Show HN: facts sheet

**Not a post.** HN's guidelines don't allow AI-generated posts or comments, so the owner writes the post and every
reply in their own words. This page is the raw material: facts, links, the story beats, and answers to the questions
that will come up.

Suggested title shape (owner's call): `Show HN: Finnamon – turn Claude or Codex into your personal finance assistant`

## Links

- Repo: `<repo>` (README is the landing page)
- 1-minute demo video: https://github.com/bill10/finnamon/releases/download/pr-assets/finnamon-demo.mp4 (demo
  household, made-up data, sped up, AI voices)
- Install guide: `docs/INSTALL.md`; how it works: README "How it works"; positioning and sources:
  `docs/market-research-2026-10.md`

## The pitch, in the settled words

- Your money. Your financial AI. Turn Claude or Codex into your personal finance assistant.
- Like Mint, but it warns you when something's off, sorts your transactions with AI, and draws any chart you ask
  for. On your own computer (Mac, Linux or Windows via WSL2), with no ads and no data selling, so nobody can shut it down.

## Story beats (owner's voice, retell, don't paste)

1. Loved Mint: one clean place for every account and transaction, no ads.
2. Mint was shut down; nothing else fit; so built my own.
3. Mint rarely said when something was wrong, so: alerts.
4. Mint came before AI, so a lot of recategorising by hand. Now the AI does it, and I can just ask how we're doing.

## Facts

- **What it is:** a daemon that syncs bank accounts through Plaid (your own Plaid keys) into one SQLite file on your
  computer every 6 hours, runs SQL detectors, and has Claude Code (or OpenAI's Codex CLI) triage what they find. Alerts
  go to Telegram. A local web dashboard shows accounts, budgets and charts, with the assistant built in as an "intercom".
- **Alerts:** rules (duplicate charge, new recurring charge, low balance, budget pace, sync health) are plain SQL,
  zero tokens. Anomaly candidates (first-ever merchant, amount outlier, new category, unmatched transfer, recurring
  charge changed, …) are found by SQL; Claude decides which are worth a message now, in Sunday's roundup, or never.
  It can only promote or suppress what SQL found; the triage run is read-only.
- **Talking to it:** reply in Telegram in plain words ("it's normal", "set dining 400", "how much did we spend on the
  dog this year"), or ask the dashboard. Claude draws charts on request ("dining by month", "everything over $500").
- **Household:** one chat, one set of budgets, joint accounts counted once.
- **Hackable:** a detector is one `.sql` file; the assistant is Claude Code (or Codex) with skills and an allow list of
  `finnamon` commands. It writes only through those commands.
- **Two assistant CLIs:** Claude Code or OpenAI's Codex CLI (0.157+), picked at `finnamon init` and switchable with
  `finnamon settings set assistant codex|claude`. The same instructions, skills and allow list on both; on Codex the
  assistant reaches `finnamon` only through an MCP tool, under a sealed Codex home that shares your existing Codex login.
  **Claude-only for now:** web lookups (none on Codex), Telegram channel mode (Codex uses the daemon's relay, which is
  the default anyway), and the "Fetch by AI" import. Evals run on both CLIs.
- **Banks Plaid can't reach: Fetch by AI.** You log in to the bank's own site yourself in a Chrome window of
  Finnamon's own; a sealed Claude Code session then clicks through to the bank's CSV export and imports it. The AI
  makes no browser call while you log in and never types anything (a guard hook refuses typing, scripts, new tabs and
  any site but the bank's, and stops the session if the tab leaves the bank). HSBC US (not on Plaid) goes through the
  Claude in Chrome extension, because HSBC refuses any Chrome with a debugging port; it runs on your normal Claude
  login and pairs by itself on first use (Claude Code 2.1.292+). The owner ran it on a real HSBC account on
  2026-10-08. It runs when you start it (dashboard button or `finnamon import --browser <bank>`), not on a schedule.
  Claude Code only. Without AI: "Fetch without AI" opens the bank and imports the CSV you download, or plain CSV import.
- **Try it without keys:** `finnamon demo` (four banks, six months of made-up transactions, budgets, alerts and the
  dashboard on a spare port; nothing touches Plaid, Telegram or a real household). `finnamon demo --stop` to stop.
- **Cost:** Finnamon free; the assistant needs a Claude Pro or Max subscription (or Console credit), or a ChatGPT Plus
  plan or higher (or an OpenAI API key) for Codex. Codex is not free here: ChatGPT's free tier is not a supported plan; Plaid's Trial plan is
  free for 10 bank logins (US/Canada teams created on or after 2026-04-15); Telegram and Tailscale free.
- **No Finnamon server.** No company, no telemetry, no account.

## Honest caveats (say them before someone else does)

- **Technical households only for now:** terminal, a Plaid developer account, a Telegram bot, a computer that stays
  on. `finnamon init` walks through it and `finnamon doctor` prints the fix for each problem, but it is a setup.
- **Plaid Trial is 10 bank logins.** Enough for most households; the next tier is paid. US and Canada only.
- **Data does leave the machine, in three places:** Plaid (that's where transactions come from), Anthropic or OpenAI,
  whichever assistant you run (what it reads when it triages or answers: merchant names, amounts, dates; with Fetch by
  AI, also the bank pages it reads after you log in: account names, balances, transactions), Telegram (alerts and chat;
  bot chats aren't end-to-end encrypted). The README section "What leaves your machine" lists it all.
- **Fetch by AI is on demand and Claude-only.** You log in each time; it isn't a background sync. Tested on HSBC US on
  a Mac; not yet tried on Linux or WSL2 (it needs Linux Chrome there).
- **Claude or Codex only.** No other agent, no local model.
- **Mac, Linux or Windows via WSL2.** No native Windows build: on Windows it runs in Ubuntu under WSL2. Keeping WSL
  from shutting down when idle has a documented workaround that hasn't been tried on a real PC yet.
- **Licence: MIT.**

## Likely questions

**Why Plaid? Isn't that the thing people self-host to avoid?** It's the only practical way to get US/Canada bank data
without screen-scraping, and it's your own Plaid keys and account, not a shared one. You log in at Plaid or the bank;
Finnamon never sees bank passwords. For a bank Plaid can't reach, or one you'd rather not give Plaid: Fetch by AI (you
log in, Claude downloads the bank's own CSV export) or plain CSV import.

**You let an AI click around your bank?** Only after you've logged in yourself, only on the bank's own site, only
clicks and reads: a hook checks every browser call and refuses typing, JavaScript, new tabs and other sites, and ends
the session if the tab leaves the bank. Its shell runs exactly two `finnamon` commands (list accounts, import the
newest download). What it reads on those pages goes to Anthropic under your Claude account's terms, like any other
Claude session. If you'd rather not, "Fetch without AI" opens the bank with nothing attached and imports the file you
download.

**What does Anthropic (or OpenAI) see / keep?** What the assistant reads when it triages an alert or answers you: merchant names,
amounts, dates, under your Claude account's terms. With "Help improve Claude" on, Anthropic keeps that for 5 years;
with it off, 30 days. The README tells you to turn it off. On Codex the same data goes to OpenAI instead, under your
ChatGPT or OpenAI account's terms; the README says to turn off ChatGPT's "Improve the model for everyone" (API keys are
not used for training by default). Rules (duplicates, budgets, low balance) run as plain SQL and send nothing.

**Why Claude or Codex only? Why not a local model?** Finnamon doesn't call a model API at all: it runs an agent
harness (Claude Code or Codex CLI) with skills, an allow list and a sealed config. That's what lets it write budgets
and rules safely and draw charts. Codex support was that port: its own sealed home, a permission profile, the guards
on Codex's tool names, and an MCP tool for the CLI. Another harness would be another port, not a setting.

**Why not just use ChatGPT Finances? It's free now.** Q&A over your money is a commodity since 2026-10-02. ChatGPT
Finances lives in OpenAI's cloud, is single-user, and its only proactive alert is credit score. Finnamon's point is
the warnings, the household, and that the database is a file you own.

**Why not Actual Budget / Firefly III?** Both are good self-hosted ledgers; neither will message you when something's
off, and neither has AI Q&A or charts. Finnamon could sit beside them.

**Windows?** Yes, through WSL2: Finnamon runs in Ubuntu under WSL2, and CI checks the install, tests, demo and systemd
units there (`docs/INSTALL.md`, "Windows (WSL2)"). There is no native Windows build. WSL shuts its VM down when idle;
the workaround in the install guide hasn't been tried on a real PC yet, so say so.

**Licence?** MIT. See the repo's LICENSE file.

**Cost in tokens?** Rules are free. Triage runs only when SQL finds candidates, and chat is whatever you ask; it all
comes out of your existing Claude subscription, or your ChatGPT plan's Codex limits on Codex.

**What if you stop maintaining it?** It's on your computer, the data is one SQLite file, and nothing depends on a
Finnamon server. Nobody can shut it down.
