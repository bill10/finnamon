# r/ClaudeAI draft ("Built with Claude" flair)

The flair's rules, as of this writing: say what you built, how Claude helped build it, and that it is free to try,
with no referral links. Re-check the sidebar on the day. Post from the owner's account.

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

**How Claude helped build it:** most of the code was written with Claude Code under my direction and review, with
unit tests and evals (real Claude over a fixture household) required after any prompt change.

**Free to try:** `finnamon demo` runs a made-up household and its dashboard with no keys. Using it for real needs a
Claude Pro or Max subscription you already have, plus Plaid's free Trial plan (US/Canada). Licence: MIT.

What leaves your machine is spelled out in the README: Plaid, what Claude reads (go turn off "Help improve Claude"),
and Telegram.

Repo + 1-minute demo: `<repo>`
