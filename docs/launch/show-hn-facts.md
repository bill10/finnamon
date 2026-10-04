# Show HN: facts sheet

**Not a post.** HN's guidelines don't allow AI-generated posts or comments, so the owner writes the post and every
reply in their own words. This page is the raw material: facts, links, the story beats, and answers to the questions
that will come up.

Suggested title shape (owner's call): `Show HN: Finnamon – turn Claude into your personal finance assistant`

## Links

- Repo: `<repo>` (README is the landing page)
- 1-minute demo video: https://github.com/bill10/finnamon/releases/download/pr-assets/finnamon-demo.mp4 (demo
  household, made-up data, sped up, AI voices)
- Install guide: `docs/INSTALL.md`; how it works: README "How it works"; positioning and sources:
  `docs/market-research-2026-10.md`

## The pitch, in the settled words

- Your money. Your financial AI. Turn Claude into your personal finance assistant.
- Like Mint, but it warns you when something's off, sorts your transactions with AI, and draws any chart you ask
  for. On your own computer (Mac, Linux or Windows via WSL2), with no ads and no data selling, so nobody can shut it down.

## Story beats (owner's voice, retell, don't paste)

1. Loved Mint: one clean place for every account and transaction, no ads.
2. Mint was shut down; nothing else fit; so built my own.
3. Mint rarely said when something was wrong, so: alerts.
4. Mint came before AI, so a lot of recategorising by hand. Now the AI does it, and I can just ask how we're doing.

## Facts

- **What it is:** a daemon that syncs bank accounts through Plaid (your own Plaid keys) into one SQLite file on your
  computer every 6 hours, runs SQL detectors, and has Claude Code triage what they find. Alerts go to Telegram. A
  local web dashboard shows accounts, budgets and charts, with Claude Code built in as an "intercom".
- **Alerts:** rules (duplicate charge, new recurring charge, low balance, budget pace, sync health) are plain SQL,
  zero tokens. Anomaly candidates (first-ever merchant, amount outlier, new category, unmatched transfer, recurring
  charge changed, …) are found by SQL; Claude decides which are worth a message now, in Sunday's roundup, or never.
  It can only promote or suppress what SQL found; the triage run is read-only.
- **Talking to it:** reply in Telegram in plain words ("it's normal", "set dining 400", "how much did we spend on the
  dog this year"), or ask the dashboard. Claude draws charts on request ("dining by month", "everything over $500").
- **Household:** one chat, one set of budgets, joint accounts counted once.
- **Hackable:** a detector is one `.sql` file; the assistant is Claude Code with skills and an allow list of
  `finnamon` commands. It writes only through those commands.
- **Banks Plaid can't reach:** CSV import, or "Fetch by AI" (a Claude session drives a browser while you log in).
- **Try it without keys:** `finnamon demo` (four banks, six months of made-up transactions, budgets, alerts and the
  dashboard on a spare port; nothing touches Plaid, Telegram or a real household). `finnamon demo --stop` to stop.
- **Cost:** Finnamon free; a Claude Pro or Max subscription (or Console credit) is required; Plaid's Trial plan is
  free for 10 bank logins (US/Canada teams created on or after 2026-04-15); Telegram and Tailscale free.
- **No Finnamon server.** No company, no telemetry, no account.

## Honest caveats (say them before someone else does)

- **Technical households only for now:** terminal, a Plaid developer account, a Telegram bot, a computer that stays
  on. `finnamon init` walks through it and `finnamon doctor` prints the fix for each problem, but it is a setup.
- **Plaid Trial is 10 bank logins.** Enough for most households; the next tier is paid. US and Canada only.
- **Data does leave the machine, in three places:** Plaid (that's where transactions come from), Anthropic (what
  Claude reads when it triages or answers: merchant names, amounts, dates), Telegram (alerts and chat; bot chats
  aren't end-to-end encrypted). The README section "What leaves your machine" lists it all.
- **Claude-only.** No other model.
- **Mac, Linux or Windows via WSL2.** No native Windows build: on Windows it runs in Ubuntu under WSL2. Keeping WSL
  from shutting down when idle has a documented workaround that hasn't been tried on a real PC yet.
- **Licence: MIT.**

## Likely questions

**Why Plaid? Isn't that the thing people self-host to avoid?** It's the only practical way to get US/Canada bank data
without screen-scraping, and it's your own Plaid keys and account, not a shared one. You log in at Plaid or the bank;
Finnamon never sees bank passwords. For banks Plaid can't reach, CSV import.

**What does Anthropic see / keep?** What the assistant reads when it triages an alert or answers you: merchant names,
amounts, dates, under your Claude account's terms. With "Help improve Claude" on, Anthropic keeps that for 5 years;
with it off, 30 days. The README tells you to turn it off. Rules (duplicates, budgets, low balance) run as plain SQL
and send nothing.

**Why Claude only? Why not a local model?** Finnamon doesn't call a model API at all: it runs Claude Code, the
agent harness, with skills, an allow list and a sealed config. That's what lets it write budgets and rules safely and
draw charts. Swapping in another agent harness would be a port, not a setting.

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
comes out of your existing Claude subscription.

**What if you stop maintaining it?** It's on your computer, the data is one SQLite file, and nothing depends on a
Finnamon server. Nobody can shut it down.
