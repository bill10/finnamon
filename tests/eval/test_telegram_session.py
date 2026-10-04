"""Eval: a phone message typed into the dashboard's session (inbound=session) gets an answer written for a phone. Spends tokens.
Run: pytest tests/eval -m eval -s"""
import re

from tests.eval.test_budget_skill import fixture_home, pytestmark, run_claude  # noqa: F401 - the fixture and marks are shared


def test_a_telegram_prefixed_question_gets_a_short_answer(fixture_home):
    out = run_claude("[telegram · jane] how much did we spend at Chipotle in August?", {"FINNAMON_HOME": str(fixture_home)})
    text = out.get("result", "").strip()
    assert text and text != "NO_REPLY", text
    assert "$" in text, text
    assert len(text) <= 500 and text.count("\n") <= 4, f"not a phone-sized answer: {text!r}"
    assert not re.search(r"^\s*\|.*\|\s*$|^#+ |```", text, re.M), f"tables, headers or code in a phone answer: {text!r}"
