"""Probe a real contract for which supply-cap getters it actually exposes.

Usage: python scripts/probe_supply_getters.py <address> [address...]

Brute-forces a list of candidate no-arg uint256 getters via eth_call against a
live RPC and prints which ones return data. Used to build the selector list in
drops.py from evidence rather than guesswork.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import drops

# Pure-Python Keccak-256 so we can derive selectors with no extra dependency.
_RC = [0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
       0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
       0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
       0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
       0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
       0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008]
_R = [[0, 36, 3, 41, 18], [1, 44, 10, 45, 2], [62, 6, 43, 15, 61],
      [28, 55, 25, 21, 56], [27, 20, 39, 8, 14]]
_M = (1 << 64) - 1


def _rol(x, n):
    return ((x << n) | (x >> (64 - n))) & _M


def _keccak_f(A):
    for rnd in range(24):
        C = [A[x][0] ^ A[x][1] ^ A[x][2] ^ A[x][3] ^ A[x][4] for x in range(5)]
        D = [C[(x - 1) % 5] ^ _rol(C[(x + 1) % 5], 1) for x in range(5)]
        A = [[A[x][y] ^ D[x] for y in range(5)] for x in range(5)]
        B = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                B[y][(2 * x + 3 * y) % 5] = _rol(A[x][y], _R[x][y])
        A = [[(B[x][y] ^ ((~B[(x + 1) % 5][y]) & B[(x + 2) % 5][y])) & _M
              for y in range(5)] for x in range(5)]
        A[0][0] ^= _RC[rnd]
    return A


def keccak256(data: bytes) -> str:
    rate = 136
    pad = bytearray(data)
    pad.append(0x01)
    while len(pad) % rate != rate - 1:
        pad.append(0x00)
    pad.append(0x80)
    A = [[0] * 5 for _ in range(5)]
    for off in range(0, len(pad), rate):
        blk = pad[off:off + rate]
        for i in range(rate // 8):
            A[i % 5][i // 5] ^= int.from_bytes(blk[i * 8:(i + 1) * 8], "little")
        A = _keccak_f(A)
    out = b""
    for i in range(4):
        out += A[i % 5][i // 5].to_bytes(8, "little")
    return out.hex()


def selector(sig: str) -> str:
    return "0x" + keccak256(sig.encode())[:8]


CANDIDATES = [
    # Current / minted supply
    "totalSupply()", "totalMinted()", "numberMinted()", "mintedSupply()",
    "currentTokenId()", "nextTokenId()", "counter()",
    # Generic caps
    "maxSupply()", "MAX_SUPPLY()", "maxTotalSupply()", "MAX_TOTAL_SUPPLY()",
    "MAX_TOKENS()", "maxTokens()", "MAX_MINT()", "collectionSize()",
    "totalSupplyLimit()", "supplyLimit()", "SUPPLY()", "maxAmount()",
    "maxNftSupply()", "MAX_NFT_SUPPLY()",
    # Project-specific constants seen on blue chips
    "MAX_APES()", "MAX_PENGUINS()", "MAX_ELEMENTS()", "MAX_TOKEN_SUPPLY()",
    "maxBatchSize()", "amountForDevs()", "collectionMaxSupply()",
]


def main():
    addresses = sys.argv[1:] or [
        "0xBC4CA0EdA7647A8aB7C2061c2E118A18a936f13D",  # BAYC
        "0xBd3531dA5CF5857e7CfAA92426877b022e612cf8",  # Pudgy Penguins
    ]
    drops.wire_healthy_rpcs(chains={"ethereum": drops.EVM_CHAINS["ethereum"]})

    for addr in addresses:
        print("=" * 62)
        print(f"{addr}")
        print("=" * 62)
        for sig in CANDIDATES:
            sel = selector(sig)
            value = asyncio.run(drops._call_uint("ethereum", addr, sel))
            if value is not None:
                print(f"  {sig:26s} {sel}  -> {value}")
        print()


if __name__ == "__main__":
    main()
