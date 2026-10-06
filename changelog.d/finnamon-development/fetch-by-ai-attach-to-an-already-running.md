---
bump: minor
---
### Added
- **Fetch by AI can use the Chrome you already run.** `finnamon import --browser hsbc --attach [host:port]` opens one new tab at the bank in a Chrome started with `--remote-debugging-port=9222` (loopback only) instead of launching Finnamon's own window, and the dashboard's Fetch by AI does it by itself whenever a Chrome answers on that port. You log in yourself as before; the session is pinned to that one tab (no listing, opening or switching tabs) and closes it at the end. It runs the build and the device your bank already knows; the trade-off is a debugging port on your everyday profile while it runs.

### Fixed
- **HSBC's "reference: EAC" after a Chrome update is explained on screen.** When Chrome on disk is newer than the windows you have open, `finnamon import --browser`, the dashboard's Import tab and `finnamon doctor` say so, name both builds, and give the three ways out: the CSV from your everyday browser, `--attach`, or retrying in a few days. The import session runs `finnamon import --chrome-check` to say the same when the bank refuses a login.
