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

class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


_CUSTOM_CHAINS = {"testchain": {"explorer": "https://explorer.example"}}


def test_creation_info_parses_full_record(monkeypatch):
    payload = {
        "status": "1",
        "message": "OK",
        "result": [
            {
                "contractAddress": "0xCONTRACT",
                "contractCreator": "0xDEPLOYERabc",
                "txHash": "0xTXHASH123",
                "blockNumber": "12345678",
                "timestamp": "1700000000",
            }
        ],
    }
    monkeypatch.setattr(
        deployer_cache.requests, "get", lambda *a, **k: _FakeResponse(payload)
    )

    info = asyncio.run(
        deployer_cache.get_contract_creation_info("testchain", "0xCONTRACT", _CUSTOM_CHAINS)
    )
    # Addresses/hashes are lowercased.
    assert info["creator"] == "0xdeployerabc"
    assert info["tx_hash"] == "0xtxhash123"
    assert info["deploy_block"] == 12345678
    assert info["deploy_ts"] == 1700000000


def test_creation_info_handles_alt_field_names(monkeypatch):
    # Some Blockscout variants use creatorAddress / creationTxHash / blockTimestamp.
    payload = {
        "status": "1",
        "result": [
            {
                "creatorAddress": "0xCreator2",
                "creationTxHash": "0xTx2",
                "blockNumber": "0x64",  # hex -> 100
                "blockTimestamp": "1699999999",
            }
        ],
    }
    monkeypatch.setattr(
        deployer_cache.requests, "get", lambda *a, **k: _FakeResponse(payload)
    )

    info = asyncio.run(
        deployer_cache.get_contract_creation_info("testchain", "0xCONTRACT", _CUSTOM_CHAINS)
    )
    assert info["creator"] == "0xcreator2"
    assert info["tx_hash"] == "0xtx2"
    assert info["deploy_block"] == 100
    assert info["deploy_ts"] == 1699999999


def test_creation_info_empty_when_no_explorer(monkeypatch):
    # An unknown chain with no custom explorer must short-circuit to empty without
    # making any HTTP call.
    def _boom(*a, **k):
        raise AssertionError("should not hit the network without an explorer")

    monkeypatch.setattr(deployer_cache.requests, "get", _boom)

    info = asyncio.run(
        deployer_cache.get_contract_creation_info("unknownchain", "0xCONTRACT")
    )
    assert info == {"creator": "", "tx_hash": "", "deploy_block": None, "deploy_ts": None}


def test_creation_info_empty_on_error_status(monkeypatch):
    payload = {"status": "0", "message": "NOTOK", "result": "No data found"}
    monkeypatch.setattr(
        deployer_cache.requests, "get", lambda *a, **k: _FakeResponse(payload)
    )
    info = asyncio.run(
        deployer_cache.get_contract_creation_info("testchain", "0xCONTRACT", _CUSTOM_CHAINS)
    )
    assert info["creator"] == ""
    assert info["deploy_ts"] is None


def test_get_contract_creator_returns_bare_string(monkeypatch):
    # Backward-compatible wrapper: still returns just the lowercased creator.
    payload = {
        "status": "1",
        "result": [{"contractCreator": "0xDEPLOYERabc", "txHash": "0xT", "blockNumber": "1", "timestamp": "2"}],
    }
    monkeypatch.setattr(
        deployer_cache.requests, "get", lambda *a, **k: _FakeResponse(payload)
    )
    creator = asyncio.run(
        deployer_cache.get_contract_creator("testchain", "0xCONTRACT", _CUSTOM_CHAINS)
    )
    assert creator == "0xdeployerabc"
