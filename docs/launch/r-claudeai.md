# r/ClaudeAI draft ("Built with Claude" flair)

The flair's rules, as noted 2026-10-05: say what you built, how Claude helped build it, and that it is free to try,
with no referral links. Re-check on 2026-10-08: Reddit refuses this agent's fetches (403), so the live sidebar was
**not** read; other projects' notes say "Built with Claude" posts were mod-approved as recently as 2026-07-21, and the
2025 contest under that flair also asked for screenshots or a demo and one prompt you used. Read the sidebar on the
day, and add a prompt if it asks for one. Post from the owner's account.

---

**Title:** I turned Claude into our household's personal finance assistant (self-hosted, free to try)

**What I built:** Finnamon. Your money. Your financial AI. It's like Mint, but it warns you when something's off,
sorts your transactions with AI, and draws any chart you ask for. On your own computer (Mac, Linux or Windows via WSL2), with no ads and
no data selling, so nobody can shut it down.

- **It watches for you:** messages you on Telegram only when something needs you (a duplicate charge, a new
  subscription, a budget about to go over).
- **A dashboard that's yours:** ask "dining by month" or "everything over $500" and Claude draws it.
- **Yours to keep:** your data lives on your machine, in SQLite, with your own Plaid keys and your own Claude.

**Why:** I loved Mint: one clean place for all my accounts and transactions, no ads. Then it was shut down, and nothing else
fit, so I built my own. Mint rarely told me when something was wrong, so I added alerts. And Mint came before AI, so I recategorized a lot by hand. Now the AI does
it, and I can just ask how we're doing.

**How Claude is in it:** Finnamon doesn't call a model API. It runs **Claude Code** as the assistant, in its own
directory with a `CLAUDE.md`, skills (`/triage`, `finnamon`) and an allow list of `finnamon` commands, so it can set
budgets and mark things normal but can't touch anything else. SQL detectors find candidates for free; a headless
`claude -p` triage run decides which deserve a message, and it's read-only apart from one command. Telegram replies
resume a Claude session; the dashboard embeds the same session as an "intercom". Every spawn is sealed
(`--strict-mcp-config`, `--setting-sources project`) so your own Claude Code settings never leak in.

**Banks Plaid can't reach, through Claude in Chrome:** HSBC US isn't on Plaid and refuses any Chrome with a debugging
port. So "Fetch by AI" opens a Chrome of Finnamon's own with the Claude in Chrome extension and no port, you log in to
the bank yourself, and a sealed `claude --chrome` session (your normal Claude login) clicks Download → CSV and imports
it. A guard hook (PreToolUse and PostToolUse) holds it to one tab on the bank's own site, clicks and reads only (no typing, no scripts, no new
tabs), and ends the session if the tab leaves the bank. Works on my real HSBC account.

**How Claude helped build it:** most of the code was written with Claude Code under my direction and review, with
unit tests and evals (real Claude over a fixture household) required after any prompt change.

**Free to try:** `finnamon demo` runs a made-up household and its dashboard with no keys. Using it for real needs a
Claude Pro or Max subscription you already have, plus Plaid's free Trial plan (US/Canada) or Fetch by AI for banks
Plaid can't reach. Licence: MIT.

What leaves your machine is spelled out in the README: Plaid, what Claude reads, the bank pages included when it
fetches (go turn off "Help improve Claude"), and Telegram.

Repo + 1-minute demo: `<repo>`
