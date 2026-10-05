"""The old name of agent_runner, kept so its callers and tests need not move: this *is* that module, so a monkeypatch
through either name patches both."""
import sys

from . import agent_runner

sys.modules[__name__] = agent_runner
