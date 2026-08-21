import os
import json
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
