"""Live minted-out probe against real mainnet contracts. Not a mocked test.

Run:  python scripts/verify_minted_out.py

Verifies get_supply_info() against collections whose supply state is public
knowledge, so a broken eth_call selector cannot pass unnoticed (a wrong selector
returns 0x, which reads as "no data" and silently fails open forever).

Expectations below were established by live probing, not assumption:
  - BAYC exposes MAX_APES(), Pudgy exposes MAX_ELEMENTS(), Doodles MAX_SUPPLY().
  - Azuki / Moonbirds / Milady (ERC721A) expose NO cap getter, so they correctly
    fall through to "unknown" and stay alertable. That is the intended fail-open
    behaviour, not a bug.

Uses a single known-fast public endpoint rather than the bot's failover list,
because probing reverting getters through every dead endpoint is very slow.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import drops

RPC = os.environ.get("PROBE_RPC", "https://ethereum-rpc.publicnode.com")

# (address, label, expect_minted_out, expect_cap_resolved)
CASES = [
    ("0xBC4CA0EdA7647A8aB7C2061c2E118A18a936f13D",
     "Bored Ape Yacht Club (sold out, cap via MAX_APES)", True, True),
    ("0xBd3531dA5CF5857e7CfAA92426877b022e612cf8",
     "Pudgy Penguins (sold out, cap via MAX_ELEMENTS)", True, True),
    ("0x8a90CAb2b38dba80c64b7734e58Ee1dB38B8992e",
     "Doodles (sold out, cap via MAX_SUPPLY)", True, True),
    ("0xED5AF388653567Af2F388E6224dC7C4b3241C544",
     "Azuki (ERC721A, no cap getter - must fail OPEN)", False, False),
    ("0xc36442b4a4522e871399cd717abdd847ab11fe88",
     "Uniswap V3 Positions (uncapped - must fail OPEN)", False, False),
]


def _pin_single_rpc():
    """Force one fast endpoint so reverting getters don't crawl the failover list."""
    drops.EVM_CHAINS["ethereum"]["rpcs"] = [RPC]


def main():
    print("=" * 68)
    print("LIVE MINTED-OUT PROBE (real mainnet eth_call)")
    print(f"RPC: {RPC}")
    print("=" * 68)
    _pin_single_rpc()

    passed = failed = unreachable = 0
    for addr, label, expect_out, expect_cap in CASES:
        info = asyncio.run(drops.get_supply_info("ethereum", addr))
        minted, cap, flag = info["minted"], info["max_supply"], info["is_minted_out"]
        print(f"\n{label}")
        print(f"   minted={minted}  cap={cap}  minted_out={flag}")
        print(f"   reason: {info['reason'] or '(no cap resolved - stays alertable)'}")

        if minted is None:
            print("   UNREACHABLE: RPC returned no totalSupply, cannot judge")
            unreachable += 1
            continue

        problems = []
        if flag != expect_out:
            problems.append(f"minted_out={flag}, expected {expect_out}")
        if (cap is not None) != expect_cap:
            problems.append(f"cap_resolved={cap is not None}, expected {expect_cap}")

        if problems:
            print("   FAIL: " + "; ".join(problems))
            failed += 1
        else:
            print(f"   PASS (minted_out={flag}, cap_resolved={cap is not None})")
            passed += 1

    print("\n" + "=" * 68)
    print(f"RESULT: {passed} passed, {failed} failed, {unreachable} unreachable")
    if failed:
        print("Minted-out detection is NOT behaving as specified.")
    print("=" * 68)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
