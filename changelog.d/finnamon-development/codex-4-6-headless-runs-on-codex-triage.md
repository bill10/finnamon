---
bump: minor
---
### Added
- **Codex groundwork: triage and Telegram replies can run on Codex.** For a household whose assistant is Codex, the daemon's triage runs and its Telegram conversation now go through `codex exec`, keeping one conversation thread the way Claude's does. Every such run is locked down on its command line: no approvals, no web, nothing writable, and the household's data reachable only through the `finnamon` tool, which still refuses triage anything but writing verdicts. A busy or out-of-usage Codex is reported as busy, not as an expired login. Claude households are unchanged, and Codex still cannot be selected as the assistant.
