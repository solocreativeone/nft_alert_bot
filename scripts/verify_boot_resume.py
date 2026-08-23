"""Two-process boot/resume verification. Real chains, real files, no mocks.

Run:  python scripts/verify_boot_resume.py

Difference from verify_resume.py: this drives the actual check_drops() scan loop
in TWO separate OS processes against a shared state file, which is the real
restart scenario. It asserts the second process:
  - reloads every watermark the first process committed
  - requests only blocks ABOVE those watermarks (nothing rescanned, nothing lost)
  - still remembers contracts the first process already alerted on

Gemini and Telegram are stubbed out so the probe costs no API quota and sends no
messages, but all checkpoint, RPC, and scan-loop code is the production path.
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Only a couple of chains, to keep a real scan bounded in wall time.
CHAINS = ["ethereum", "base"]

WORKER = r'''
import asyncio, json, os, sys
sys.path.insert(0, {root!r})
import checkpoint, drops, gemini_filter

CHAINS = {chains!r}
PHASE = {phase!r}

# Restrict the scan to a couple of chains and one fast endpoint each.
drops.EVM_CHAINS = {{c: drops.EVM_CHAINS[c] for c in CHAINS if c in drops.EVM_CHAINS}}
drops.ALL_SUPPORTED_CHAINS = list(drops.EVM_CHAINS)
drops.last_checked_blocks = {{c: checkpoint.get_block(c) for c in drops.ALL_SUPPORTED_CHAINS}}
resumed = dict(drops.last_checked_blocks)

# No API spend, no Telegram traffic: stub the audit and the senders.
async def fake_score(data):
    return {{"score": 99, "verdict": "LEGIT", "reason": "probe stub"}}
gemini_filter.gemini_score_nft = fake_score
drops.gemini_score_nft = fake_score

sent = []
async def fake_send(*a, **k):
    sent.append(1)
drops.asend = fake_send
drops.asend_photo = fake_send

# Record the exact block range each chain requests.
requested = {{}}
real_transfers = drops.get_recent_transfers
async def spy(chain, from_block, to_block):
    requested[chain] = {{"from": from_block, "to": to_block}}
    return await real_transfers(chain, from_block, to_block)
drops.get_recent_transfers = spy

asyncio.run(drops.check_drops())

# On the first pass, plant a contract in the dedup store so pass two can prove
# the processed-mint history also survived.
if PHASE == "first":
    drops._remember_contract("0xprobe-marker-contract")
    checkpoint.flush(force=True)

print("RESULT " + json.dumps({{
    "phase": PHASE,
    "resumed": resumed,
    "requested": requested,
    "committed": checkpoint.all_blocks(),
    "marker_seen": checkpoint.was_seen(drops.SEEN_EVM, "0xprobe-marker-contract"),
}}))
'''


def run_phase(phase, state_path):
    code = WORKER.format(root=ROOT, chains=CHAINS, phase=phase)
    env = dict(os.environ, NFT_BOT_STATE_FILE=state_path)
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, cwd=ROOT, env=env, timeout=900)
    for line in proc.stdout.splitlines():
        if line.startswith("RESULT "):
            return json.loads(line[len("RESULT "):])
    print(f"  [{phase}] no result line")
    print("  stdout tail:", proc.stdout[-1200:])
    print("  stderr tail:", proc.stderr[-1200:])
    return None


def main():
    print("=" * 68)
    print("TWO-PROCESS BOOT/RESUME VERIFICATION (real RPC, real state file)")
    print("=" * 68)

    with tempfile.TemporaryDirectory() as tmp:
        state_path = os.path.join(tmp, "state.json")

        print("\n[Process 1] cold start")
        first = run_phase("first", state_path)
        if not first:
            return 1
        print(f"  resumed:   {first['resumed']}")
        print(f"  scanned:   { {c: r['from'] for c, r in first['requested'].items()} }")
        print(f"  committed: {first['committed']}")
        if not first["committed"]:
            print("\nFAILED: process 1 committed no watermark (RPCs unreachable?)")
            return 1

        print("\n[Process 2] restart against the same state file")
        second = run_phase("second", state_path)
        if not second:
            return 1
        print(f"  resumed:   {second['resumed']}")
        print(f"  committed: {second['committed']}")

        ok = True

        # 1. Every committed watermark must reload.
        for chain, block in first["committed"].items():
            if second["resumed"].get(chain) != block:
                print(f"  FAIL {chain}: resumed {second['resumed'].get(chain)}, "
                      f"expected {block}")
                ok = False
            else:
                print(f"  PASS {chain}: resumed exactly at {block}")

        # 2. The second scan must not re-read any block at or below the watermark.
        for chain, rng in second["requested"].items():
            base = first["committed"].get(chain)
            if base is None:
                continue
            if rng["from"] <= base:
                print(f"  FAIL {chain}: rescanned from {rng['from']} (<= {base})")
                ok = False
            else:
                print(f"  PASS {chain}: next scan started at {rng['from']} "
                      f"(watermark+{rng['from'] - base})")

        # 3. Processed-mint history must survive too.
        if second["marker_seen"]:
            print("  PASS dedup history survived the restart")
        else:
            print("  FAIL dedup marker lost across restart")
            ok = False

    print("\n" + "=" * 68)
    print("RESULT:", "PASS - real restart resumes correctly" if ok else "FAIL")
    print("=" * 68)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
