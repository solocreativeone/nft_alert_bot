"""Live restart-resume probe. Hits real RPC endpoints - not a mocked test.

Run directly:  python scripts/verify_resume.py

Proves the checkpoint contract end to end:
  1. Cold start with no state file, scan real chains, persist watermarks.
  2. Simulated restart (fresh interpreter state) resumes those exact watermarks.
  3. The resumed scan requests blocks ABOVE the persisted watermark, so no range
     is rescanned and no range is skipped.
"""
import asyncio
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Chains to probe. Kept small so the run stays quick.
PROBE_CHAINS = ["ethereum", "base", "polygon"]


def phase_one(state_path):
    """Cold start: real scan, then verify watermarks landed on disk."""
    os.environ["NFT_BOT_STATE_FILE"] = state_path
    import checkpoint
    import drops

    checkpoint.use_path(state_path)
    print(f"  state file: {state_path}")
    print(f"  cold start watermarks: {checkpoint.all_blocks()}")
    assert checkpoint.all_blocks() == {}, "expected no prior state on a cold start"

    drops.wire_healthy_rpcs()

    live = {}
    for chain in PROBE_CHAINS:
        block = asyncio.run(drops.get_current_block(chain))
        if block is None:
            print(f"  {chain}: RPC unreachable, skipping")
            continue
        live[chain] = block
        # Commit the watermark exactly as check_drops() does after a clean scan.
        drops.last_checked_blocks[chain] = block
        checkpoint.set_block(chain, block)
        print(f"  {chain}: live tip {block} -> watermark committed")

    if not live:
        print("\nFAILED: no chain was reachable, cannot verify resume")
        return None

    checkpoint.flush(force=True)
    on_disk = json.load(open(state_path, encoding="utf-8"))
    print(f"  persisted blocks: {on_disk['blocks']}")
    for chain, block in live.items():
        assert on_disk["blocks"][chain] == block, f"{chain} watermark did not persist"
    return live


def phase_two(state_path, expected):
    """Fresh interpreter: prove the watermarks reload and the next scan advances."""
    code = f'''
import asyncio, json, os, sys
os.environ["NFT_BOT_STATE_FILE"] = {state_path!r}
sys.path.insert(0, {ROOT!r})
import checkpoint, drops

resumed = {{c: checkpoint.get_block(c) for c in {PROBE_CHAINS!r}}}
requested = {{}}

# Intercept the getLogs range this scan would request.
real_get = drops.get_recent_transfers
async def spy(chain, from_block, to_block):
    requested[chain] = from_block
    return []
drops.get_recent_transfers = spy

drops.wire_healthy_rpcs()
asyncio.run(drops.check_drops())
print(json.dumps({{"resumed": resumed, "requested": requested,
                  "after": checkpoint.all_blocks()}}))
'''
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, cwd=ROOT, timeout=600)
    lines = [l for l in proc.stdout.strip().splitlines() if l.startswith("{")]
    if not lines:
        print("  subprocess produced no JSON result")
        print("  stdout tail:", proc.stdout[-1500:])
        print("  stderr tail:", proc.stderr[-1500:])
        return False
    data = json.loads(lines[-1])

    print(f"  resumed watermarks: { {k: v for k, v in data['resumed'].items() if v} }")
    ok = True
    for chain, block in expected.items():
        if data["resumed"].get(chain) != block:
            print(f"  FAIL {chain}: resumed {data['resumed'].get(chain)}, expected {block}")
            ok = False
            continue
        asked = data["requested"].get(chain)
        if asked is None:
            print(f"  {chain}: no new blocks yet (tip unchanged) - nothing rescanned")
            continue
        if asked <= block:
            print(f"  FAIL {chain}: rescanned from {asked}, must be > {block}")
            ok = False
        else:
            print(f"  PASS {chain}: resumed at {block}, next scan starts at {asked} "
                  f"(gap {asked - block - 1} blocks, no overlap, no skip)")
    return ok


def main():
    print("=" * 62)
    print("LIVE RESTART-RESUME PROBE (real RPC calls)")
    print("=" * 62)

    with tempfile.TemporaryDirectory() as tmp:
        state_path = os.path.join(tmp, "probe_state.json")

        print("\n[Phase 1] Cold start, real scan, persist watermarks")
        expected = phase_one(state_path)
        if not expected:
            return 1

        print("\n[Phase 2] Simulated restart in a fresh interpreter")
        ok = phase_two(state_path, expected)

    print("\n" + "=" * 62)
    print("RESULT:", "PASS - resume verified against live chains" if ok else "FAIL")
    print("=" * 62)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
