"""Finnamon: your money, your financial AI. Infrastructure for an agent, not the agent."""

from pathlib import Path as _Path

_root = _Path(__file__).resolve().parent.parent   # the checkout root in an editable install; site-packages in a wheel
try:   # a checkout (the editable install everyone runs): the VERSION file, live, no reinstall after a pull
    if not (_root / "pyproject.toml").exists():   # a wheel: never read a stray site-packages/VERSION
        raise FileNotFoundError
    __version__ = (_root / "VERSION").read_text(encoding="utf-8-sig").strip()   # a BOM would reach the Plaid User-Agent
except OSError:   # a wheel: the metadata hatch stamped from the same file
    from importlib.metadata import PackageNotFoundError as _NotFound, version as _version
    try:
        __version__ = _version("finnamon")
    except _NotFound:   # a bare copy of the tree under a system python: still importable
        __version__ = "0"
