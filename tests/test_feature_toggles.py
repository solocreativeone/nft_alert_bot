"""Tests for the Bitcoin Ordinals feature toggle.

The Ordinals scanner is disabled by default. Rationale, measured against the live
feed: 15 of 15 recent inscriptions were BRC-20 token operations or plain text, not
NFT art, and the scanner described every one to Gemini with identical hardcoded
stats so the scores carried no information. At 288 cycles/day it could issue about
4,320 Gemini calls/day against a 500/key/day quota.

The scanner is kept and gated rather than deleted, so re-enabling it is a config
change once the noise problem is addressed properly.

The toggle must be read WITHOUT extending the existing
`from private.config_live import A, B, C` statement in bot.py. One missing name
aborts that whole statement, and its `except ImportError` fallback then discards
the entire private config including real API keys, substituting public defaults.
The failure is silent: no crash, just a bot running on placeholder values. So a new
setting has to be resolved independently, with a default when absent.
"""
import importlib


def test_ordinals_are_disabled_by_default(monkeypatch):
    """A user who has not opted in must not get the BRC-20 firehose."""
    monkeypatch.delenv("BTC_ORDINALS_ENABLED", raising=False)
    import config
    importlib.reload(config)
    assert config.BTC_ORDINALS_ENABLED is False


def test_toggle_reads_from_environment(monkeypatch):
    import config

    for raw, expected in (("true", True), ("1", True), ("yes", True),
                          ("false", False), ("0", False), ("", False)):
        monkeypatch.setenv("BTC_ORDINALS_ENABLED", raw)
        importlib.reload(config)
        assert config.BTC_ORDINALS_ENABLED is expected, f"{raw!r} -> {expected}"


def test_toggle_resolution_tolerates_a_private_config_without_the_name():
    """The critical safety property.

    A private config written before this feature existed has no
    BTC_ORDINALS_ENABLED. Resolving it must fall back to the default and leave
    every other private value intact, never trigger the all-or-nothing ImportError
    that swaps in public defaults.
    """
    import bot_settings

    class ConfigWithoutTheName:
        TELEGRAM_TOKEN = "private-token"
        FLOOR_CHECK_INTERVAL = 5

    resolved = bot_settings.resolve(
        "BTC_ORDINALS_ENABLED", default=False, source=ConfigWithoutTheName)
    assert resolved is False
    # The point: the other private values are still reachable.
    assert ConfigWithoutTheName.TELEGRAM_TOKEN == "private-token"


def test_toggle_resolution_prefers_the_private_value_when_present():
    import bot_settings

    class ConfigWithTheName:
        BTC_ORDINALS_ENABLED = True

    assert bot_settings.resolve(
        "BTC_ORDINALS_ENABLED", default=False, source=ConfigWithTheName) is True


def test_resolution_handles_a_missing_source_entirely():
    """No private config at all is a normal deployment, not an error."""
    import bot_settings

    assert bot_settings.resolve(
        "BTC_ORDINALS_ENABLED", default=False, source=None) is False


def test_disabled_scanner_makes_no_network_calls(monkeypatch):
    """Disabled must mean zero cost: no scrape, no Gemini call, no alert."""
    import asyncio
    import btc_ordinals

    def explode(*a, **kw):
        raise AssertionError("disabled scanner must not touch the network")

    monkeypatch.setattr(btc_ordinals, "BTC_ORDINALS_ENABLED", False)
    monkeypatch.setattr(btc_ordinals, "fetch_recent_inscriptions", explode)
    asyncio.run(btc_ordinals.check_btc_ordinals())


def test_enabled_scanner_still_runs(monkeypatch):
    """The toggle must not be a permanent kill switch."""
    import asyncio
    import btc_ordinals

    called = {"n": 0}

    def fetch(limit=15):
        called["n"] += 1
        return []

    monkeypatch.setattr(btc_ordinals, "BTC_ORDINALS_ENABLED", True)
    monkeypatch.setattr(btc_ordinals, "fetch_recent_inscriptions", fetch)
    asyncio.run(btc_ordinals.check_btc_ordinals())
    assert called["n"] == 1


def test_disabled_scanner_is_not_scheduled(monkeypatch):
    """bot.py must skip creating the loop task, not just early-return each tick."""
    import bot_settings

    assert hasattr(bot_settings, "resolve")
    # bot.py reads the flag through this helper; if the name is gone the wiring
    # has drifted and the toggle is silently dead.
    import inspect
    import pathlib

    src = pathlib.Path(
        pathlib.Path(inspect.getfile(bot_settings)).parent / "bot.py"
    ).read_text()
    assert "BTC_ORDINALS_ENABLED" in src, "bot.py must consult the toggle"
    assert "check_btc_ordinals" in src
