---
bump: patch
---
### Added
- **`finnamon doctor` knows WSL.** It checks that systemd is PID 1 and repeats the keep-alive and dashboard-address guidance, since Windows-side settings cannot be read from inside Ubuntu.
- Evals for normal / dismiss / undo, category and alias, roundup replies, `/triage` and `detect --draft`.

### Changed
- **INSTALL says how to keep WSL awake and how to get an alarm when it stops** (`vmIdleTimeout`, a Task Scheduler check), and that spoken replies use the browser's speech on Linux and WSL.
- The assistant's notes now say which heredocs work: plain text for `triage set -` and `detect --draft -`; only JSON is refused.
