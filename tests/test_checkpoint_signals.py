"""Signal-handling tests for the checkpoint store.

These spawn real subprocesses and deliver real signals, because the bug they
guard against only appears under genuine signal delivery: the handler used to
raise KeyboardInterrupt for SIGTERM, which printed a spurious traceback from
inside the asyncio event loop and produced the wrong exit status.

Contract:
  - SIGTERM  -> state is flushed, process dies by SIGTERM (exit -15), no traceback
  - SIGINT   -> state is flushed, KeyboardInterrupt is raised as Python normally
                does, so `except KeyboardInterrupt` blocks (bot.py) still work
  - a pre-existing handler is chained, not clobbered
  - clean exit -> atexit flush persists state
"""
import json
import os
import signal
import subprocess
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Writes a watermark, signals readiness, then waits to be signalled.
WORKER = r"""
import os, sys, time
sys.path.insert(0, {root!r})
import checkpoint
checkpoint.load()
checkpoint.set_block("ethereum", 12345)   # dirty, not yet flushed
{extra}
with open({ready!r}, "w") as fh:
    fh.write("ready")
time.sleep(60)
"""


def _spawn(tmp_path, state_path, extra=""):
    ready = tmp_path / "ready"
    code = WORKER.format(root=ROOT, ready=str(ready), extra=extra)
    env = dict(os.environ, NFT_BOT_STATE_FILE=str(state_path))
    proc = subprocess.Popen(
        [sys.executable, "-c", code], env=env, cwd=ROOT,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    deadline = time.time() + 60
    while time.time() < deadline:
        if ready.exists():
            return proc
        if proc.poll() is not None:
            out, err = proc.communicate()
            pytest.fail(f"worker exited early: {err[-800:]}")
        time.sleep(0.1)
    proc.kill()
    pytest.fail("worker never became ready")


@pytest.mark.parametrize("sig,expected_returncode", [
    # SIGTERM: default disposition terminates the process by signal.
    (signal.SIGTERM, -signal.SIGTERM),
    # SIGINT: Python's default handler raises KeyboardInterrupt. Uncaught, the
    # interpreter re-raises it as the signal, so the status is -SIGINT (not 1).
    (signal.SIGINT, -signal.SIGINT),
])
def test_signal_flushes_state_and_exits_correctly(tmp_path, sig, expected_returncode):
    state_path = tmp_path / "state.json"
    proc = _spawn(tmp_path, state_path)

    proc.send_signal(sig)
    out, err = proc.communicate(timeout=60)

    assert proc.returncode == expected_returncode, (
        f"{sig.name}: expected returncode {expected_returncode}, got "
        f"{proc.returncode}. stderr: {err[-800:]}"
    )
    assert state_path.exists(), f"{sig.name} did not flush state to disk"
    data = json.loads(state_path.read_text())
    assert data["blocks"]["ethereum"] == 12345


def test_sigterm_produces_no_traceback(tmp_path):
    """The original handler raised KeyboardInterrupt, dumping a traceback."""
    state_path = tmp_path / "state.json"
    proc = _spawn(tmp_path, state_path)
    proc.send_signal(signal.SIGTERM)
    out, err = proc.communicate(timeout=60)

    assert "Traceback" not in err, f"SIGTERM produced a traceback:\n{err[-1200:]}"
    assert "KeyboardInterrupt" not in err


def test_sigint_still_reaches_bot_keyboardinterrupt_handler(tmp_path):
    """bot.py wraps asyncio.run in `except KeyboardInterrupt` to flush and print
    'Bot stopped.' - SIGINT must still deliver that, and the flush must happen."""
    state_path = tmp_path / "state.json"
    ready = tmp_path / "ready"
    code = r"""
import asyncio, os, sys, time
sys.path.insert(0, {root!r})
import checkpoint

async def main():
    checkpoint.load()
    checkpoint.set_block("ethereum", 5150)
    with open({ready!r}, "w") as fh:
        fh.write("ready")
    await asyncio.Event().wait()

try:
    asyncio.run(main())
except KeyboardInterrupt:
    checkpoint.flush(force=True)
    print("Bot stopped.", flush=True)
    sys.exit(0)
""".format(root=ROOT, ready=str(ready))

    env = dict(os.environ, NFT_BOT_STATE_FILE=str(state_path))
    proc = subprocess.Popen([sys.executable, "-c", code], env=env, cwd=ROOT,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    deadline = time.time() + 60
    while time.time() < deadline and not ready.exists():
        if proc.poll() is not None:
            _, err = proc.communicate()
            pytest.fail(f"worker exited early: {err[-800:]}")
        time.sleep(0.1)

    proc.send_signal(signal.SIGINT)
    out, err = proc.communicate(timeout=60)

    assert "Bot stopped." in out, (
        f"bot.py's KeyboardInterrupt handler never ran. stderr: {err[-800:]}"
    )
    assert proc.returncode == 0, f"clean shutdown expected, got {proc.returncode}"
    assert state_path.exists()
    assert json.loads(state_path.read_text())["blocks"]["ethereum"] == 5150


def test_preexisting_handler_is_chained_not_clobbered(tmp_path):
    """A handler installed before load() must still run."""
    state_path = tmp_path / "state.json"
    # Install a custom SIGTERM handler BEFORE checkpoint.load() runs.
    code = r"""
import os, signal, sys, time
sys.path.insert(0, {root!r})

def custom(signum, frame):
    print("CUSTOM_HANDLER_RAN", flush=True)
    sys.exit(7)

signal.signal(signal.SIGTERM, custom)

import checkpoint
checkpoint.load()
checkpoint.set_block("ethereum", 999)
with open({ready!r}, "w") as fh:
    fh.write("ready")
time.sleep(60)
""".format(root=ROOT, ready=str(tmp_path / "ready"))

    env = dict(os.environ, NFT_BOT_STATE_FILE=str(state_path))
    proc = subprocess.Popen([sys.executable, "-c", code], env=env, cwd=ROOT,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    deadline = time.time() + 60
    while time.time() < deadline and not (tmp_path / "ready").exists():
        if proc.poll() is not None:
            _, err = proc.communicate()
            pytest.fail(f"worker exited early: {err[-800:]}")
        time.sleep(0.1)

    proc.send_signal(signal.SIGTERM)
    out, err = proc.communicate(timeout=60)

    assert "CUSTOM_HANDLER_RAN" in out, "pre-existing handler was clobbered"
    assert proc.returncode == 7, f"custom handler's exit code lost: {proc.returncode}"
    # State must still have been flushed before delegating.
    assert state_path.exists()
    assert json.loads(state_path.read_text())["blocks"]["ethereum"] == 999


def test_clean_exit_flushes_via_atexit(tmp_path):
    """No signal at all: atexit must still persist a dirty store."""
    state_path = tmp_path / "state.json"
    code = (
        f"import sys; sys.path.insert(0, {ROOT!r})\n"
        "import checkpoint\n"
        "checkpoint.load()\n"
        "checkpoint.set_block('base', 777)\n"
    )
    env = dict(os.environ, NFT_BOT_STATE_FILE=str(state_path))
    proc = subprocess.run([sys.executable, "-c", code], env=env, cwd=ROOT,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-800:]
    assert state_path.exists(), "atexit flush did not run"
    assert json.loads(state_path.read_text())["blocks"]["base"] == 777
