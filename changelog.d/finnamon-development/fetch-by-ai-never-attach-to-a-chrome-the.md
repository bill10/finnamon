---
bump: patch
---
### Fixed
- **Fetch by AI always opens Finnamon's own Chrome window; it never takes over a Chrome on port 9222.** The dashboard used to pass `--attach=auto`, which opened the bank login in whatever Chrome answered on 9222, and on 2026-10-06 that was another program's automation browser, so the HSBC login landed in its profile. Attaching is now opt-in only: type `finnamon import --browser <bank> --attach [host:port]` yourself. The `auto` value is gone (it is refused, like any address that isn't a loopback port).
