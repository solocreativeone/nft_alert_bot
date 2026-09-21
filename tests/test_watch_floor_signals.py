"""Focused coverage for /watch percentage floor signals and watchlist state."""
import asyncio

import checkpoint
import commands
import floor
import mint
import watchlist


CONTRACT = "0x" + "a" * 40


def _item(**overrides):
    item = {
        "name": "Signal Collection",
        "chain": "ethereum",
        "contract": CONTRACT,
        "slug": "signal-collection",
        "current_floor": None,
        "last_floor": None,
    }
    item.update(overrides)
    return item


async def _direct_to_thread(fn, *args, **kwargs):
    return fn(*args, **kwargs)


def _run_signal_check(monkeypatch, item, prices, sent):
    values = iter(prices)
    monkeypatch.setattr(floor, "get_watchlist", lambda: [item])
    monkeypatch.setattr(floor, "save_watchlist", lambda _items: None)
    monkeypatch.setattr(floor.asyncio, "to_thread", _direct_to_thread)
    monkeypatch.setattr(floor, "get_floor_and_image", lambda _slug: (next(values), None))

    async def fake_send(*args, **kwargs):
        sent.append(args)
    monkeypatch.setattr(floor, "send_floor_signal", fake_send)
    asyncio.run(floor.check_watched_floor_signals())


def test_watch_confirmation_uses_configured_signal_format_and_valid_url(monkeypatch):
    monkeypatch.setattr(commands, "get_eth_usd_price", lambda: 2000.0)
    text = commands._watch_confirmation(_item(current_floor=0.0012))
    assert "📊 Floor signal: ±10%" in text
    assert "Floor: 0.0012 ETH | $2.40" in text
    assert "https://opensea.io/collection/signal-collection" in text
    assert "Alert low" not in text and "Alert high" not in text


def test_watch_omits_unavailable_floor_and_invalid_opensea_url(monkeypatch):
    monkeypatch.setattr(commands, "get_eth_usd_price", lambda: 2000.0)
    text = commands._watch_confirmation(_item(chain="arc", slug="bad/slug", current_floor=None))
    assert "Floor:" not in text
    assert "opensea.io" not in text


def test_list_uses_floor_model_and_clean_full_contract_buttons():
    text, markup = commands.render_watchlist_message([
        _item(current_floor=0.5),
        _item(name="Arc Collection", chain="arc", slug="", current_floor=None),
    ], eth_usd_price=2000.0)
    assert "Floor: 0.5 ETH | $1,000" in text
    assert text.count("Floor:") == 1
    assert markup.inline_keyboard[0][0].text == "🗑 Unwatch Signal Collection"
    assert markup.inline_keyboard[0][0].callback_data == f"unwatch:{CONTRACT}"
    assert not markup.inline_keyboard[1][0].text.startswith("/unwatch")


def test_first_observation_and_subthreshold_or_same_change_send_no_signal(monkeypatch):
    item, sent = _item(), []
    floor.floor_last_alerted.clear()
    _run_signal_check(monkeypatch, item, [1.0], sent)
    _run_signal_check(monkeypatch, item, [1.05], sent)
    _run_signal_check(monkeypatch, item, [1.05], sent)
    assert sent == []
    assert checkpoint.get_floor(f"ethereum:{CONTRACT}") == 1.0


def test_threshold_movements_send_pump_and_dump_and_persist_baseline(monkeypatch):
    item, sent = _item(), []
    floor.floor_last_alerted.clear()
    monkeypatch.setattr(floor, "FLOOR_COOLDOWN_MINUTES", 0)
    _run_signal_check(monkeypatch, item, [1.0], sent)
    _run_signal_check(monkeypatch, item, [1.1], sent)
    _run_signal_check(monkeypatch, item, [0.99], sent)
    assert [call[3] for call in sent] == ["pump", "dump"]
    assert checkpoint.get_floor(f"ethereum:{CONTRACT}") == 0.99




def test_last_floor_never_overrides_a_missing_checkpoint_baseline(monkeypatch):
    item, sent = _item(last_floor=0.8, current_floor=0.8), []
    floor.floor_last_alerted.clear()
    _run_signal_check(monkeypatch, item, [1.0], sent)
    assert sent == []
    assert checkpoint.get_floor(f"ethereum:{CONTRACT}") == 1.0
    assert item["last_floor"] == 1.0

def test_cooldown_keeps_checkpoint_baseline_until_the_delayed_signal(monkeypatch):
    item, sent = _item(), []
    key = f"ethereum:{CONTRACT}"
    floor.floor_last_alerted.clear()
    _run_signal_check(monkeypatch, item, [1.0], sent)

    # Simulate a just-sent prior signal: the next qualifying movement must not
    # advance the baseline while cooldown suppresses its alert.
    floor.floor_last_alerted[key] = float("inf")
    _run_signal_check(monkeypatch, item, [1.2], sent)
    assert sent == []
    assert checkpoint.get_floor(key) == 1.0
    assert item["last_floor"] == 1.0
    assert item["current_floor"] == 1.2

    # Once cooldown expires, the unchanged qualifying floor emits one signal
    # against the original checkpoint baseline, then becomes the new baseline.
    floor.floor_last_alerted[key] = 0
    _run_signal_check(monkeypatch, item, [1.2], sent)
    assert len(sent) == 1
    assert sent[0][1:4] == (1.0, 1.2, "pump")
    assert checkpoint.get_floor(key) == 1.2

    _run_signal_check(monkeypatch, item, [1.2], sent)
    assert len(sent) == 1

def test_previous_floor_survives_checkpoint_reload(monkeypatch, tmp_path):
    key = f"ethereum:{CONTRACT}"
    checkpoint.set_floor(key, 1.0, flush_now=True)
    checkpoint.load(force=True)
    item, sent = _item(), []
    floor.floor_last_alerted.clear()
    monkeypatch.setattr(floor, "FLOOR_COOLDOWN_MINUTES", 0)
    _run_signal_check(monkeypatch, item, [1.1], sent)
    assert len(sent) == 1
    assert sent[0][1:4] == (1.0, 1.1, "pump")


def test_arc_uses_same_polling_path_without_fabricated_floor(monkeypatch):
    item, sent = _item(name="Arc Collection", chain="arc", slug=""), []
    floor.floor_last_alerted.clear()
    _run_signal_check(monkeypatch, item, [None], sent)
    assert sent == []
    assert checkpoint.get_floor(f"arc:{CONTRACT}") is None


def test_watched_collection_suppresses_raw_mint_alert(monkeypatch):
    monkeypatch.setattr(watchlist, "get_watchlist", lambda: [_item()])
    called = []
    async def fake_asend(*args, **kwargs):
        called.append(args)
    monkeypatch.setattr(mint, "asend", fake_asend)
    asyncio.run(mint.send_mint_alert(_item(), "1", "0xabc", None, "ethereum"))
    assert called == []


def test_unwatch_removes_case_insensitively_clears_baseline_and_rewatch_is_fresh(tmp_path, monkeypatch):
    path = tmp_path / "watchlist.json"
    monkeypatch.setattr(watchlist, "WATCHLIST_FILE", str(path))
    monkeypatch.setattr(watchlist, "_watchlist_cache", None)
    monkeypatch.setattr(watchlist, "lookup_contract", lambda contract, chain: _item(contract=contract.lower(), chain=chain, current_floor=1.0))
    ok, _ = watchlist.add_to_watchlist(CONTRACT.upper().replace("0X", "0x"))
    assert ok
    checkpoint.set_floor(f"ethereum:{CONTRACT}", 1.0, flush_now=True)
    ok, _ = watchlist.remove_from_watchlist(CONTRACT.upper().replace("0X", "0x"))
    assert ok and watchlist.get_watchlist() == []
    assert checkpoint.get_floor(f"ethereum:{CONTRACT}") is None
    assert commands.render_watchlist_message(watchlist.get_watchlist())[0].endswith("No collections currently being watched.")
    ok, watched = watchlist.add_to_watchlist(CONTRACT)
    assert ok and watched["last_floor"] is None
    assert checkpoint.get_floor(f"ethereum:{CONTRACT}") is None
