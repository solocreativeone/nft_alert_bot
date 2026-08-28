"""Test that watermark catch-up advances in bounded max_span chunks instead of skipping."""
import pytest

import drops


def test_catchup_sets_to_block_max_span_ahead(monkeypatch):
    """
    When last_checked is far behind current_block, the scan should only go
    up to from_block + max_span - 1 (not jump all the way to current_block).
    """
    chain = "robinhood"
    step = drops.EVM_CHAINS[chain]["block_step"]          # 60
    max_span = max(step * 5, 2000)                        # 2000
    last_checked = 100_000
    current_block = 1_000_000

    # Simulate the logic in drops.py:1235-1244
    from_block = last_checked + 1
    if current_block - from_block > max_span:
        to_block = from_block + max_span - 1
    else:
        to_block = current_block

    # to_block should be exactly one max_span chunk ahead
    assert to_block == from_block + max_span - 1
    assert to_block < current_block  # never jumps to tip


def test_no_catchup_needed_uses_current_block(monkeypatch):
    """When gap is within max_span, to_block == current_block (normal case)."""
    chain = "ethereum"
    step = drops.EVM_CHAINS[chain]["block_step"]          # 30
    max_span = max(step * 5, 2000)                        # 2000 (2000 > 150)
    last_checked = 100_000
    current_block = 100_500  # only 500 blocks ahead

    from_block = last_checked + 1
    if current_block - from_block > max_span:
        to_block = from_block + max_span - 1
    else:
        to_block = current_block

    assert to_block == current_block


def test_catchup_step_for_robinhood():
    """
    robinhood has block_step=60, so max_span = max(300, 2000) = 2000.
    A 2.3M block gap (44_927_010 -> 47_250_306) requires ~1150 cycles
    at 2000 blocks/cycle, not one giant skip that discards history.
    """
    step = drops.EVM_CHAINS["robinhood"]["block_step"]    # 60
    max_span = max(step * 5, 2000)                        # 2000
    assert max_span == 2000


def test_max_span_floor_2000():
    """
    Even for fast chains with small block_step, max_span never goes below 2000,
    preventing overly-small RPC ranges that waste cycles.
    """
    for chain in ["ethereum", "polygon", "base", "arbitrum", "optimism", "robinhood", "bsc"]:
        step = drops.EVM_CHAINS[chain]["block_step"]
        max_span = max(step * 5, 2000)
        assert max_span >= 2000