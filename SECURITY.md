# Security policy

## Reporting a vulnerability

Please report it privately through GitHub: **Security → Report a vulnerability** on this repository (private
vulnerability reporting). Don't open a public issue or PR for it. Include what you found, how to reproduce it, and the
version (`finnamon --version`). There is no bounty; reports are handled on a best-effort basis by a small team.

Only the latest release is supported.

## What is in scope

Finnamon is self-hosted: it runs on your own machine and holds your bank data in a local SQLite database. In scope:

- Anything that exposes the dashboard beyond localhost plus its key cookie (it is meant to be reached on localhost, or
  over Tailscale; never Tailscale Funnel or the public internet).
- Escapes from the assistant's sealed permission set: the allow/deny list, the read-only headless triage, the
  reply-guard hook, or the rule that unattended runs have no web access.
- Getting past the dashboard session's permission prompts: an action off the allow list that runs without a person's
  Allow, a Telegram Allow / Deny press that counts though it is forged, replayed, out of time or from someone who is not
  a household member of that chat, or a phone prompt that shows something other than what would run.
- Prompt injection through untrusted data (bank memos, merchant names, imported files) that makes the assistant act
  outside what the household allowed.
- Leaks of secrets (`secrets.toml`, Plaid tokens, the bot token) to logs, messages, the database or the repository.

Out of scope: an attacker who already has shell access as your user, a compromised Plaid, Telegram or Anthropic
account, vulnerabilities in third-party software with no Finnamon-specific impact, and denial of service against your
own machine.
