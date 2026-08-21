import watchlist

FAKE = {
    "name": "Foo",
    "slug": "foo",
    "contract": "0xabc",
    "floor_alert_low": 0.1,
    "floor_alert_high": 1.0,
    "current_floor": 0.5,
}


def _use_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(watchlist, "WATCHLIST_FILE", str(tmp_path / "watchlist.json"))


def test_add_duplicate_remove_cycle(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    monkeypatch.setattr(watchlist, "lookup_contract", lambda c, chain="ethereum": dict(FAKE, contract=c.lower(), chain=chain))

    ok, res = watchlist.add_to_watchlist("0xABC")
    assert ok and res["contract"] == "0xabc"

    ok_dup, _ = watchlist.add_to_watchlist("0xabc")
    assert not ok_dup

    assert len(watchlist.get_watchlist()) == 1

    ok_rm, _ = watchlist.remove_from_watchlist("0xABC")
    assert ok_rm
    assert watchlist.get_watchlist() == []


def test_remove_missing(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    ok, _ = watchlist.remove_from_watchlist("0xdeadbeef")
    assert not ok


def test_add_when_lookup_fails(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    monkeypatch.setattr(watchlist, "lookup_contract", lambda *args, **kwargs: None)
    ok, _ = watchlist.add_to_watchlist("0xdeadbeef")
    assert not ok
