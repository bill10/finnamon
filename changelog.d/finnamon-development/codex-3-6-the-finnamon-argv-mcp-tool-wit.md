---
bump: minor
---
### Added
- **Codex groundwork: the `finnamon` tool and the guards on Codex's tools.** `finnamon serve` now has a `finnamon(argv)` tool, the way a Codex assistant runs Finnamon commands. Codex's sandbox keeps its shell away from your data. The tool checks each command against the same allow and deny lists Claude Code uses, never prompts, and marks every call as the assistant's, so the human-only commands still refuse. The secret guard now also understands Codex's file edits and image reads, and blocks the call if it hits an error of its own. Phone approvals can read a Codex session's log. Codex still cannot be selected as the assistant; a Codex household needs the `mcp` extra (`uv tool install -e '.[mcp]'`), which `finnamon doctor` already checks.
