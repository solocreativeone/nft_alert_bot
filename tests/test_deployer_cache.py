import os
import json
import asyncio
import pytest
import deployer_cache


@pytest.fixture
def temp_deployers_file(tmp_path):
    file_path = str(tmp_path / "deployers.json")
    return file_path


def test_empty_deployer_cache(temp_deployers_file):
    stats = deployer_cache.get_deployer_stats("0x123", temp_deployers_file)
    assert stats is None
    is_rug, _ = deployer_cache.is_known_serial_rugger("0x123", temp_deployers_file)
    assert not is_rug


def test_record_and_block_serial_rugger(temp_deployers_file):
    deployer = "0xDeployerBad123"

    # First launch - flagged as likely rug
    stats = deployer_cache.record_deployer_result(
        deployer_address=deployer,
        contract_address="0xContract1",
        score=10,
        verdict="LIKELY_RUG",
        filepath=temp_deployers_file,
    )

    assert stats["total_deployed"] == 1
    assert stats["rugs"] == 1
    assert stats["avg_score"] == 10.0
    assert stats["last_verdict"] == "LIKELY_RUG"

    # Check is_known_serial_rugger
    is_rug, reason = deployer_cache.is_known_serial_rugger(deployer, temp_deployers_file)
    assert is_rug is True
    assert "rug" in reason.lower()


def test_reputable_creator_stats(temp_deployers_file):
    deployer = "0xGoodCreator"

    # 1st good launch
    deployer_cache.record_deployer_result(
        deployer_address=deployer,
        contract_address="0xGoodContract1",
        score=85,
        verdict="LEGIT",
        filepath=temp_deployers_file,
    )

    # 2nd good launch
    stats = deployer_cache.record_deployer_result(
        deployer_address=deployer,
        contract_address="0xGoodContract2",
        score=95,
        verdict="LEGIT",
        filepath=temp_deployers_file,
    )

    assert stats["total_deployed"] == 2
    assert stats["rugs"] == 0
    assert stats["avg_score"] == 90.0
    assert stats["last_verdict"] == "LEGIT"

    is_rug, _ = deployer_cache.is_known_serial_rugger(deployer, temp_deployers_file)
    assert is_rug is False


# ── _to_int helper ──────────────────────────────────────────────────────────

def test_to_int_parses_decimal_hex_and_junk():
    assert deployer_cache._to_int("12345678") == 12345678
    assert deployer_cache._to_int("0x10") == 16
    assert deployer_cache._to_int(42) == 42
    assert deployer_cache._to_int(None) is None
    assert deployer_cache._to_int("") is None
    assert deployer_cache._to_int("not-a-number") is None


# ── get_contract_creation_info / get_contract_creator ────────────────────────
# These now resolve creation over RPC. The explorer-parsing tests were deleted with
# the explorer implementation; the RPC behaviour is covered exhaustively in
# tests/test_deployer_rpc.py. What remains here are the boundary cases that were the
# only thing the old tests actually cared about: missing config and unknown chains.

_RPC_CHAINS = {"testchain": {"rpcs": ["https://rpc.example"], "block_step": 60}}


def test_creation_info_empty_when_no_rpc_config(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("should not hit the network without an rpc")

    monkeypatch.setattr(deployer_cache, "_rpc_call", _boom)
    info = asyncio.run(
        deployer_cache.get_contract_creation_info("unknownchain", "0xCONTRACT", {})
    )
    assert info == {"creator": "", "tx_hash": "", "deploy_block": None, "deploy_ts": None}


def test_creation_info_returns_full_record_over_rpc(monkeypatch):
    """End-to-end with a stubbed RPC: creation resolved, creator lowercased."""
    def fake_rpc(rpcs, method, params, timeout=deployer_cache.RPC_TIMEOUT):
        if method == "eth_getCode":
            block = int(params[1], 16) if params[1] != "latest" else 10**9
            return "0x60806040" if block >= 100 else "0x"
        if method == "eth_blockNumber":
            return "0x64"  # 100
        if method == "eth_getBlockByNumber":
            return {"timestamp": "0x6553f100", "transactions": []}  # 1700000000
        return None

    monkeypatch.setattr(deployer_cache, "_rpc_call", fake_rpc)

    # Anchor the search at a known block so it does not walk from the head.
    info = asyncio.run(
        deployer_cache.get_contract_creation_info(
            "testchain", "0xCONTRACT", _RPC_CHAINS, known_block=200)
    )
    assert info["deploy_block"] == 100
    assert info["deploy_ts"] == 1700000000


def test_get_contract_creator_returns_bare_string(monkeypatch):
    async def fake(chain, contract, *a, **k):
        return {"creator": "0xdeployerabc", "tx_hash": "0xt",
                "deploy_block": 1, "deploy_ts": 2}

    monkeypatch.setattr(deployer_cache, "get_contract_creation_info", fake)
    creator = asyncio.run(
        deployer_cache.get_contract_creator("testchain", "0xCONTRACT")
    )
    assert creator == "0xdeployerabc"
