"""Tests for minted-out detection and the persistent EVM dedup path in drops.py.

Minted-out detection did not exist before: the bot alerted on collections that
had already sold out, where there is nothing left for the user to mint. Detection
must also fail OPEN - a contract that exposes no supply getters must never be
blocked on missing data.
"""
import asyncio

import pytest

import checkpoint
import drops


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(checkpoint, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(checkpoint, "_state", None)
    monkeypatch.setattr(checkpoint, "_seen_order", {})
    monkeypatch.setattr(checkpoint, "_dirty", False)
    monkeypatch.setattr(checkpoint, "_last_flush", 0.0)
    yield


def _encode(value: int) -> str:
    return "0x" + f"{value:064x}"


def _stub_calls(monkeypatch, mapping):
    """Route rpc_post eth_calls to a {selector: uint or None} map."""
    def fake_rpc_post(chain, payload):
        data = payload["params"][0]["data"]
        selector = data[:10]
        if selector not in mapping or mapping[selector] is None:
            return {"result": "0x"}
        return {"result": _encode(mapping[selector])}

    monkeypatch.setattr(drops, "rpc_post", fake_rpc_post)


TOTAL_SUPPLY = "0x18160ddd"
TOTAL_MINTED = "0xa2309ff8"
MAX_SUPPLY = "0xd5abeb01"
MAX_SUPPLY_CONST = "0x32cb6b0c"
MAX_TOTAL_SUPPLY = "0x2ab4d052"


# ── uint decoding ────────────────────────────────────────────────────────────

def test_decode_uint_basic():
    assert drops._decode_abi_uint(_encode(10_000)) == 10_000


def test_decode_uint_zero():
    assert drops._decode_abi_uint(_encode(0)) == 0


def test_decode_uint_rejects_empty():
    assert drops._decode_abi_uint("0x") is None
    assert drops._decode_abi_uint("") is None
    assert drops._decode_abi_uint(None) is None


def test_decode_uint_rejects_uncapped_sentinel():
    """Contracts return 2**256-1 for 'no cap' - must not read as a real cap."""
    assert drops._decode_abi_uint("0x" + "f" * 64) is None


def test_decode_uint_rejects_non_hex():
    assert drops._decode_abi_uint("0xnothex") is None


# ── Minted-out decisions ─────────────────────────────────────────────────────

def test_sold_out_collection_is_flagged(monkeypatch):
    _stub_calls(monkeypatch, {TOTAL_SUPPLY: 10_000, MAX_SUPPLY: 10_000})
    info = asyncio.run(drops.get_supply_info("ethereum", "0xabc"))
    assert info["is_minted_out"] is True
    assert "10000/10000" in info["reason"]


def test_still_minting_collection_is_not_flagged(monkeypatch):
    _stub_calls(monkeypatch, {TOTAL_SUPPLY: 1_500, MAX_SUPPLY: 10_000})
    info = asyncio.run(drops.get_supply_info("ethereum", "0xabc"))
    assert info["is_minted_out"] is False
    assert "15.0%" in info["reason"]


def test_team_reserve_threshold_counts_as_minted_out(monkeypatch):
    """9,995/10,000 is effectively closed - the rest is usually team-reserved."""
    _stub_calls(monkeypatch, {TOTAL_SUPPLY: 9_995, MAX_SUPPLY: 10_000})
    assert asyncio.run(drops.get_supply_info("ethereum", "0xabc"))["is_minted_out"] is True


def test_just_below_threshold_is_not_minted_out(monkeypatch):
    _stub_calls(monkeypatch, {TOTAL_SUPPLY: 9_900, MAX_SUPPLY: 10_000})
    assert asyncio.run(drops.get_supply_info("ethereum", "0xabc"))["is_minted_out"] is False


def test_oversubscribed_supply_is_minted_out(monkeypatch):
    _stub_calls(monkeypatch, {TOTAL_SUPPLY: 10_050, MAX_SUPPLY: 10_000})
    assert asyncio.run(drops.get_supply_info("ethereum", "0xabc"))["is_minted_out"] is True


# ── Fail-open behaviour on missing data ──────────────────────────────────────

def test_no_cap_getter_is_not_minted_out(monkeypatch):
    """An open edition with no declared cap must stay alertable."""
    _stub_calls(monkeypatch, {TOTAL_SUPPLY: 5_000})
    info = asyncio.run(drops.get_supply_info("ethereum", "0xabc"))
    assert info["is_minted_out"] is False
    assert info["max_supply"] is None


def test_no_supply_getter_at_all_is_not_minted_out(monkeypatch):
    _stub_calls(monkeypatch, {})
    info = asyncio.run(drops.get_supply_info("ethereum", "0xabc"))
    assert info["is_minted_out"] is False
    assert info["minted"] is None


def test_zero_cap_is_treated_as_unconfigured(monkeypatch):
    """maxSupply() == 0 means 'not set', not 'sold out'."""
    _stub_calls(monkeypatch, {TOTAL_SUPPLY: 100, MAX_SUPPLY: 0})
    info = asyncio.run(drops.get_supply_info("ethereum", "0xabc"))
    assert info["is_minted_out"] is False
    assert info["max_supply"] is None


def test_rpc_failure_is_not_minted_out(monkeypatch):
    def dead_rpc(chain, payload):
        return None
    monkeypatch.setattr(drops, "rpc_post", dead_rpc)
    info = asyncio.run(drops.get_supply_info("ethereum", "0xabc"))
    assert info["is_minted_out"] is False


def test_rpc_exception_is_swallowed(monkeypatch):
    def boom(chain, payload):
        raise RuntimeError("connection reset")
    monkeypatch.setattr(drops, "rpc_post", boom)
    info = asyncio.run(drops.get_supply_info("ethereum", "0xabc"))
    assert info["is_minted_out"] is False


# ── Getter fallback chain ────────────────────────────────────────────────────

def test_falls_back_to_total_minted(monkeypatch):
    _stub_calls(monkeypatch, {TOTAL_MINTED: 500, MAX_SUPPLY: 500})
    assert asyncio.run(drops.get_supply_info("ethereum", "0xabc"))["is_minted_out"] is True


def test_falls_back_to_max_supply_constant(monkeypatch):
    _stub_calls(monkeypatch, {TOTAL_SUPPLY: 888, MAX_SUPPLY_CONST: 888})
    assert asyncio.run(drops.get_supply_info("ethereum", "0xabc"))["is_minted_out"] is True


def test_falls_back_to_max_total_supply(monkeypatch):
    _stub_calls(monkeypatch, {TOTAL_SUPPLY: 333, MAX_TOTAL_SUPPLY: 333})
    assert asyncio.run(drops.get_supply_info("ethereum", "0xabc"))["is_minted_out"] is True


def test_selectors_match_known_good_reference():
    """Guard against a typo silently disabling detection (a bad selector just
    returns 0x, which reads as 'no data' and fails open forever)."""
    assert dict(drops.SUPPLY_SELECTORS)["totalSupply"] == "0x18160ddd"
    assert dict(drops.MAX_SUPPLY_SELECTORS)["maxSupply"] == "0xd5abeb01"


# ── Persistent EVM dedup ─────────────────────────────────────────────────────

def test_remember_contract_persists_across_restart(monkeypatch):
    monkeypatch.setattr(drops, "alerted_contracts_set", set())
    monkeypatch.setattr(drops, "alerted_contracts_deque", drops.deque(maxlen=100))
    drops._remember_contract("0xdeadbeef")
    checkpoint.flush(force=True)

    # Simulate a restart: in-memory sets are wiped, disk state remains.
    monkeypatch.setattr(drops, "alerted_contracts_set", set())
    monkeypatch.setattr(checkpoint, "_state", None)
    monkeypatch.setattr(checkpoint, "_seen_order", {})
    assert checkpoint.was_seen(drops.SEEN_EVM, "0xdeadbeef") is True


def test_minted_out_contract_is_remembered(monkeypatch):
    """A sold-out contract must not be re-evaluated every scan cycle."""
    monkeypatch.setattr(drops, "alerted_contracts_set", set())
    monkeypatch.setattr(drops, "alerted_contracts_deque", drops.deque(maxlen=100))
    _stub_calls(monkeypatch, {TOTAL_SUPPLY: 10_000, MAX_SUPPLY: 10_000})

    async def unexpected(*args, **kwargs):
        raise AssertionError("must not reach Gemini for a minted-out collection")

    monkeypatch.setattr(drops, "gemini_score_nft", unexpected)
    monkeypatch.setattr(drops, "get_contract_data_batched", lambda *a, **k: _immediate({
        "age_hours": 1.0, "mint_count": 50, "standard": "ERC-721", "unique_minters": 30,
    }))

    sem = asyncio.Semaphore(1)
    asyncio.run(drops.evaluate_contract_drop("ethereum", "0xsoldout", [], sem))
    assert checkpoint.was_seen(drops.SEEN_EVM, "0xsoldout") is True


def _immediate(value):
    async def _coro():
        return value
    return _coro()


def test_already_seen_contract_is_skipped_before_any_rpc(monkeypatch):
    checkpoint.mark_seen(drops.SEEN_EVM, "0xseen", flush_now=True)
    monkeypatch.setattr(drops, "alerted_contracts_set", set())

    async def unexpected(*args, **kwargs):
        raise AssertionError("must not do any work for an already-processed contract")

    monkeypatch.setattr(drops, "get_contract_data_batched", unexpected)
    sem = asyncio.Semaphore(1)
    asyncio.run(drops.evaluate_contract_drop("ethereum", "0xseen", [], sem))


# ── Bogus-cap guard (found by the live probe) ────────────────────────────────
# A guessed selector collided with an unrelated function and returned 32 for a
# contract with 1.2M minted. Reading that as a cap would suppress live drops.

MAX_ELEMENTS = "0x3502a716"
MAX_APES = "0xbb8a16bd"


def test_absurd_cap_is_rejected_and_fails_open(monkeypatch):
    _stub_calls(monkeypatch, {TOTAL_SUPPLY: 1_216_862, MAX_SUPPLY: 32})
    info = asyncio.run(drops.get_supply_info("ethereum", "0xabc"))
    assert info["is_minted_out"] is False, "must not suppress an active collection"
    assert info["max_supply"] is None, "non-credible cap must be discarded"
    assert "not credible" in info["reason"]


def test_slight_overshoot_is_still_minted_out(monkeypatch):
    """Real sold-out collections can sit a hair above their cap."""
    _stub_calls(monkeypatch, {TOTAL_SUPPLY: 10_020, MAX_SUPPLY: 10_000})
    assert asyncio.run(drops.get_supply_info("ethereum", "0xabc"))["is_minted_out"] is True


def test_bayc_style_max_apes_getter(monkeypatch):
    """Verified live: BAYC exposes MAX_APES(), not maxSupply()."""
    _stub_calls(monkeypatch, {TOTAL_SUPPLY: 10_000, MAX_APES: 10_000})
    info = asyncio.run(drops.get_supply_info("ethereum", "0xbayc"))
    assert info["is_minted_out"] is True
    assert info["max_supply"] == 10_000


def test_pudgy_style_max_elements_getter(monkeypatch):
    """Verified live: Pudgy Penguins exposes MAX_ELEMENTS()."""
    _stub_calls(monkeypatch, {TOTAL_SUPPLY: 8_888, MAX_ELEMENTS: 8_888})
    assert asyncio.run(drops.get_supply_info("ethereum", "0xpudgy"))["is_minted_out"] is True


def test_erc721a_without_cap_getter_fails_open(monkeypatch):
    """Verified live: Azuki/Moonbirds/Milady expose no cap getter at all."""
    _stub_calls(monkeypatch, {TOTAL_SUPPLY: 10_000})
    info = asyncio.run(drops.get_supply_info("ethereum", "0xazuki"))
    assert info["is_minted_out"] is False
    assert info["max_supply"] is None


def test_all_cap_selectors_are_distinct():
    """A duplicated selector would mean a copy-paste error in the list."""
    sels = [s for _, s in drops.MAX_SUPPLY_SELECTORS]
    assert len(sels) == len(set(sels))
    for _, sel in drops.MAX_SUPPLY_SELECTORS + drops.SUPPLY_SELECTORS:
        assert sel.startswith("0x") and len(sel) == 10, sel


def test_live_verified_selectors_match_probe_results():
    """Pin the selectors the live probe actually confirmed on mainnet."""
    caps = dict(drops.MAX_SUPPLY_SELECTORS)
    assert caps["MAX_APES"] == "0xbb8a16bd"        # BAYC, confirmed live
    assert caps["MAX_ELEMENTS"] == "0x3502a716"    # Pudgy, confirmed live
    assert caps["MAX_SUPPLY"] == "0x32cb6b0c"      # Doodles, confirmed live
