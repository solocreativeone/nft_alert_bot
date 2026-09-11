"""Test watermark catch-up behavior: bounded freshness horizon, short-outage recovery,
Solana stale signature recovery, and deduplication.
"""
import asyncio
import pytest

import checkpoint
import drops
import solana_drops


def test_test_a_small_backlog_normal_catchup(monkeypatch, tmp_path):
    """
    Test A — Small backlog:
    Simulate gap = 100 blocks, MAX_CATCHUP_BLOCKS = 2000.
    Expected:
    - Normal catch-up occurs.
    - Scans missed blocks from last_checked + 1 to current_block.
    - No blocks are unnecessarily skipped.
    """
    state_file = str(tmp_path / "state_test_a.json")
    checkpoint.use_path(state_file)

    chain = "robinhood"
    last_checked = 58_600_900
    current_block = 58_601_000  # gap = 100

    monkeypatch.setattr(drops, "ALL_SUPPORTED_CHAINS", [chain])
    monkeypatch.setattr(drops, "MAX_CATCHUP_BLOCKS", 2000)
    drops.last_checked_blocks[chain] = last_checked
    checkpoint.set_block(chain, last_checked, flush_now=True)

    async def fake_current_block(c):
        return current_block

    scanned_ranges = []

    async def fake_transfers(c, from_block, to_block):
        scanned_ranges.append((from_block, to_block))
        return []

    monkeypatch.setattr(drops, "get_current_block", fake_current_block)
    monkeypatch.setattr(drops, "get_recent_transfers_resilient", fake_transfers)
    monkeypatch.setattr(drops, "get_recent_transfers", fake_transfers)

    asyncio.run(drops.check_drops())

    # All 100 blocks scanned normally, without skipping
    assert scanned_ranges == [(58_600_901, 58_601_000)]
    assert drops.last_checked_blocks[chain] == 58_601_000
    assert checkpoint.get_block(chain) == 58_601_000


def test_test_b_large_backlog_stale_recovery(monkeypatch, tmp_path, capsys):
    """
    Test B — Large backlog:
    Simulate gap = 10,000,000 blocks, MAX_CATCHUP_BLOCKS = 2000.
    Expected:
    - Historical backlog is classified as stale.
    - Bot jumps to recent window (current_block - MAX_CATCHUP_BLOCKS).
    - Bot does NOT iterate through 10 million blocks.
    - Bot reaches live monitoring quickly in one cycle.
    - Logs clear skip notification with chain, backlog, horizon, starting block.
    """
    state_file = str(tmp_path / "state_test_b.json")
    checkpoint.use_path(state_file)

    chain = "robinhood"
    last_checked = 48_436_153
    current_block = 58_603_120  # gap = 10,166,967 > 2000

    monkeypatch.setattr(drops, "ALL_SUPPORTED_CHAINS", [chain])
    monkeypatch.setattr(drops, "MAX_CATCHUP_BLOCKS", 2000)
    drops.last_checked_blocks[chain] = last_checked
    checkpoint.set_block(chain, last_checked, flush_now=True)

    async def fake_current_block(c):
        return current_block

    scanned_ranges = []

    async def fake_transfers(c, from_block, to_block):
        scanned_ranges.append((from_block, to_block))
        return []

    monkeypatch.setattr(drops, "get_current_block", fake_current_block)
    monkeypatch.setattr(drops, "get_recent_transfers_resilient", fake_transfers)
    monkeypatch.setattr(drops, "get_recent_transfers", fake_transfers)

    asyncio.run(drops.check_drops())

    # Scans only the recent freshness window: current_block - MAX_CATCHUP_BLOCKS
    expected_start = current_block - 2000  # 58_601_120
    assert scanned_ranges == [(expected_start, current_block)]
    assert drops.last_checked_blocks[chain] == current_block
    assert checkpoint.get_block(chain) == current_block

    out = capsys.readouterr().out
    assert "backlog of 10,166,967 blocks exceeds freshness horizon (2,000)" in out
    assert f"starting at block {expected_start:,}" in out


def test_test_c_restart_after_stale_recovery(monkeypatch, tmp_path):
    """
    Test C — Restart after stale recovery:
    Restart the bot after the large-backlog recovery.
    Expected:
    - It does NOT recreate the old 10-million-block backlog.
    - It resumes from the newly persisted checkpoint.
    """
    state_file = str(tmp_path / "state_test_c.json")
    checkpoint.use_path(state_file)

    chain = "robinhood"
    last_checked = 48_436_153
    current_block = 58_603_120

    monkeypatch.setattr(drops, "ALL_SUPPORTED_CHAINS", [chain])
    monkeypatch.setattr(drops, "MAX_CATCHUP_BLOCKS", 2000)
    drops.last_checked_blocks[chain] = last_checked
    checkpoint.set_block(chain, last_checked, flush_now=True)

    async def fake_current_block(c):
        return current_block

    async def fake_transfers(c, from_block, to_block):
        return []

    monkeypatch.setattr(drops, "get_current_block", fake_current_block)
    monkeypatch.setattr(drops, "get_recent_transfers_resilient", fake_transfers)
    monkeypatch.setattr(drops, "get_recent_transfers", fake_transfers)

    # 1. Run stale recovery
    asyncio.run(drops.check_drops())
    checkpoint.flush(force=True)

    # 2. Simulate complete restart with fresh interpreter state from disk
    checkpoint.use_path(state_file)
    resumed_block = checkpoint.get_block(chain)
    assert resumed_block == 58_603_120
    drops.last_checked_blocks[chain] = resumed_block

    # Tip advances slightly (5 blocks)
    new_tip = current_block + 5
    monkeypatch.setattr(drops, "get_current_block", lambda c: asyncio.sleep(0, result=new_tip))

    next_scanned = []
    async def track_transfers(c, from_block, to_block):
        next_scanned.append((from_block, to_block))
        return []

    monkeypatch.setattr(drops, "get_recent_transfers_resilient", track_transfers)
    monkeypatch.setattr(drops, "get_recent_transfers", track_transfers)

    asyncio.run(drops.check_drops())

    # Only the 5 new blocks are scanned - the 10M block backlog is NOT recreated!
    assert next_scanned == [(58_603_121, 58_603_125)]
    assert checkpoint.get_block(chain) == new_tip


def test_test_d_solana_stale_signature_recovery(monkeypatch, tmp_path, capsys):
    """
    Test D — Solana stale signature:
    Provide a signature that cannot be reached within the pagination limit.
    Expected:
    - Pagination stops at MAX_SOLANA_SIGNATURE_PAGES.
    - Stale checkpoint is detected.
    - Bot moves to newest available position.
    - Bot resumes live monitoring with no infinite retry loop.
    """
    state_file = str(tmp_path / "state_test_d.json")
    checkpoint.use_path(state_file)

    program_id = solana_drops.METAPLEX_CORE_PROGRAM
    unreachable_sig = "unreachable-ancient-signature"

    checkpoint.set_signature(program_id, unreachable_sig, flush_now=True)
    solana_drops.last_signatures[program_id] = unreachable_sig

    # Simulate RPC returning 3 pages of 1,000 signatures, none containing unreachable_sig
    page_requests = []

    async def fake_get_recent_signatures(prog, limit=1000, before=None):
        page_requests.append(before)
        page_num = len(page_requests)
        # 3 pages available, total 3,000 signatures
        start = (page_num - 1) * 1000
        return [{"signature": f"sig-{start + i}"} for i in range(limit)]

    monkeypatch.setattr(solana_drops, "get_recent_signatures", fake_get_recent_signatures)
    monkeypatch.setattr(solana_drops, "MAX_SOLANA_SIGNATURE_PAGES", 3)
    monkeypatch.setattr(solana_drops, "PROGRAMS_TO_WATCH", [("Metaplex Core", program_id)])

    # Cycle 1: Stale signature detected
    asyncio.run(solana_drops.check_solana_drops())

    out = capsys.readouterr().out
    assert "Saved signature is outside the available RPC history." in out
    assert "Treating backlog as stale and resuming from newest available signature." in out
    assert len(page_requests) == 3  # bounded by MAX_SOLANA_SIGNATURE_PAGES

    # Newest available signature from page 1 was "sig-0"
    new_watermark = checkpoint.get_signature(program_id)
    assert new_watermark == "sig-0"
    assert solana_drops.last_signatures[program_id] == "sig-0"

    # Cycle 2: A new signature arrives; bot is live and does NOT retry the ancient signature
    page_requests.clear()

    async def fake_live_signatures(prog, limit=1000, before=None):
        page_requests.append(before)
        # Returns newest signature "sig-live-1" followed by our watermark "sig-0"
        return [{"signature": "sig-live-1"}, {"signature": "sig-0"}]

    monkeypatch.setattr(solana_drops, "get_recent_signatures", fake_live_signatures)
    monkeypatch.setattr(solana_drops, "get_parsed_transaction", lambda sig: asyncio.sleep(0, result=None))

    asyncio.run(solana_drops.check_solana_drops())

    # Watermark advanced to live tip! No infinite retry!
    assert checkpoint.get_signature(program_id) == "sig-live-1"
    assert solana_drops.last_signatures[program_id] == "sig-live-1"
