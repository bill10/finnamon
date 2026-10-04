# Contributing

Thanks for helping. Read `AGENTS.md` and `docs/DEVELOPMENT.md` first: they cover the layout and the rules that are not
obvious from the code (the sealed assistant spawn, read-only triage, detectors reviewed by a person).

## Setup and tests

```
uv sync            # or: pip install -e .
python3 -m pytest tests -q -m "not eval"
```

Web tests live under `web/` (`npm test` there). Evals (`python3 -m pytest tests/eval -q -m eval`) run a real Claude and
spend tokens; run them if you touch a prompt file (`finnamon/assistant_bundle/**`, `finnamon/triage.py`,
`finnamon/claude_runner.py`, `finnamon/daemon.py`).

Never point tests at your real `~/.finnamon/`: they use a scratch `FINNAMON_HOME`. Never put real bank data, tokens or
chat ids in a test, fixture or screenshot.

## Pull requests

- Title it `<type>: <summary>` (`feat`, `fix`, `docs`, ...), with no version prefix.
- **Do not edit `VERSION` or `CHANGELOG.md`.** Add `changelog.d/<branch-name>.md` instead
  (see `changelog.d/README.md`); the release workflow picks the version after merge.
- A detector needs hit / miss / baseline / dedup tests in `tests/test_detectors.py`.

By contributing you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md). Security problems: see
[SECURITY.md](SECURITY.md), not a public issue.
