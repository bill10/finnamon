---
bump: minor
---
### Added
- **Codex can run the household's assistant.** `finnamon settings set assistant codex` now works once `finnamon init` has set Codex up and `finnamon doctor`'s Codex lines are clear (it is refused before that, and while channel mode is on); `claude` switches back. The switch restarts the dashboard onto the new CLI, and `doctor` and the dashboard's Settings show which assistant runs.
- **The dashboard's intercom on Codex.** The corner terminal runs `codex` under Finnamon's own Codex home, sharing your Codex login, with approvals on request, no web search and the `finnamon` permission profile pinned on its command line. It resumes the same conversation after a restart (`codex resume`). Talk to Finnamon and session-mode Telegram work as they do on Claude: a phone message is typed into the session and answered from it, and a permission prompt during a phone turn goes to the chat with Allow / Deny.

### Changed
- **A Claude household is unchanged**, except that `finnamon doctor` now has an "Assistant" line and Settings an "Assistant" row.
