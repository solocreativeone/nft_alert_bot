"""Shared test fixtures.

Makes the project root importable so tests can `import notifier`, etc., and
redirects the persistent checkpoint store to a throwaway file for every test.
Without that redirect, any test exercising dedup/drops writes real scan state
into the repo's own state.json, which both pollutes the working tree and lets
one test's keys leak into another test's dedup checks.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))


@pytest.fixture(autouse=True)
def _isolate_checkpoint_state(tmp_path, monkeypatch):
    """Point checkpoint.py at a per-test temp file and reset its module globals."""
    import checkpoint

    monkeypatch.setattr(checkpoint, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(checkpoint, "_state", None)
    monkeypatch.setattr(checkpoint, "_seen_order", {})
    monkeypatch.setattr(checkpoint, "_dirty", False)
    monkeypatch.setattr(checkpoint, "_last_flush", 0.0)
    yield
