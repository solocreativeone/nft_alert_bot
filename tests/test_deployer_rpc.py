"""Contract creation lookup over RPC instead of block explorer APIs.

The explorer path never worked. Probed live: deployer_cache built
`https://etherscan.io/api?...` and got

    {"status":"0","message":"NOTOK","result":"Invalid API URL endpoint,
     use api.etherscan.io"}

and with the host corrected it still failed with "You are using a deprecated V1
endpoint, switch to Etherscan V2". Zora's Blockscout returned HTTP 404 for the same
endpoint. So every drop alert carried creator="" and deploy_ts=None, which is why
Gemini kept reporting "unranked deployer" and Ethos never had an address to look up.

Rather than patch five hostnames plus a V2 migration plus five API keys, resolve
creation over the RPC endpoints the bot already has working on every chain.

Verified against Etherscan ground truth for BAYC
(0xbc4ca0eda7647a8ab7c2061c2e118a18a936f13d): deploy block 12287507, creator
0xaba7161a7fb69c88e16ed9f455ce62b791ee4d03. The RPC method found both exactly.

Design notes that the tests below pin down:

- Search is BOUNDED, not a full binary search over all history. A full search cost
  25 eth_getCode calls and 11s for one contract. The bot only alerts on contracts
  newer than MAX_CONTRACT_AGE_HOURS (48h, roughly 14,400 ethereum blocks), and
  drops.py already knows the block of the first mint it saw. Creation is at or
  before that block, so the window is small and known.

- eth_getCode at a historical block needs archive state. Three of four ethereum
  endpoints answered correctly (empty code at block 1); a pruned node that returns
  code for a pre-deployment block would corrupt the result, so the search validates
  its lower bound instead of trusting it.

- Absence of data must stay absent. Returning a wrong creator is worse than
  returning "" because it feeds a reputation lookup and an AI verdict.
"""
import asyncio

import pytest

import deployer_cache


# ── the shape of the result never changes ────────────────────────────────────

EMPTY = {"creator": "", "tx_hash": "", "deploy_block": None, "deploy_ts": None}


def test_missing_contract_address_returns_empty():
    out = asyncio.run(deployer_cache.get_contract_creation_info("ethereum", ""))
    assert out == EMPTY


def test_unknown_chain_returns_empty_without_raising():
    out = asyncio.run(
        deployer_cache.get_contract_creation_info("not-a-chain", "0xabc", {}))
    assert out == EMPTY


# ── finding the creation block ───────────────────────────────────────────────

def _code_at(deploy_block):
    """Fake eth_getCode: contract exists at or after deploy_block."""
    def getter(chain, address, block, rpcs=None):
        if block is None:
            return "0x60806040"
        return "0x60806040" if block >= deploy_block else "0x"
    return getter


def test_finds_creation_block_within_a_bounded_window(monkeypatch):
    """The search must locate the exact block the code first appears."""
    monkeypatch.setattr(deployer_cache, "_get_code_at_block", _code_at(1_000_050))

    found = deployer_cache.find_creation_block(
        chain="ethereum", contract="0xabc",
        known_block=1_000_100, max_lookback=200, rpcs=["https://x"])
    assert found == 1_000_050


def test_search_is_bounded_by_max_lookback(monkeypatch):
    """A contract older than the window must report None, not scan all history.

    The bot does not alert on contracts older than MAX_CONTRACT_AGE_HOURS, so an
    unbounded search would burn calls establishing a fact that changes nothing.
    """
    monkeypatch.setattr(deployer_cache, "_get_code_at_block", _code_at(500_000))

    found = deployer_cache.find_creation_block(
        chain="ethereum", contract="0xabc",
        known_block=1_000_000, max_lookback=1_000, rpcs=["https://x"])
    assert found is None


def test_search_call_count_is_logarithmic(monkeypatch):
    """Binary search over the window, not a linear walk.

    A linear scan of a 14,400 block window would be 14,400 calls per contract.
    """
    calls = {"n": 0}
    inner = _code_at(1_000_050)

    def counting(chain, address, block, rpcs=None):
        calls["n"] += 1
        return inner(chain, address, block, rpcs)

    monkeypatch.setattr(deployer_cache, "_get_code_at_block", counting)
    deployer_cache.find_creation_block(
        chain="ethereum", contract="0xabc",
        known_block=1_014_400, max_lookback=14_400, rpcs=["https://x"])
    assert calls["n"] <= 20, f"expected ~log2(14400)=14 calls, made {calls['n']}"


def test_search_returns_none_when_rpc_cannot_answer(monkeypatch):
    """An RPC failure is unknown, not block zero."""
    def failing(chain, address, block, rpcs=None):
        return None

    monkeypatch.setattr(deployer_cache, "_get_code_at_block", failing)
    found = deployer_cache.find_creation_block(
        chain="ethereum", contract="0xabc",
        known_block=1_000_000, max_lookback=1_000, rpcs=["https://x"])
    assert found is None


def test_contract_absent_at_known_block_reports_none(monkeypatch):
    """If there is no code even at the known block, we were given a bad input."""
    monkeypatch.setattr(deployer_cache, "_get_code_at_block",
                        lambda *a, **k: "0x")
    found = deployer_cache.find_creation_block(
        chain="ethereum", contract="0xabc",
        known_block=1_000_000, max_lookback=1_000, rpcs=["https://x"])
    assert found is None


# ── extracting creator and timestamp from the creation block ─────────────────

def test_creator_and_timestamp_come_from_the_creation_block(monkeypatch):
    """The creating tx is the one whose receipt names this contract."""
    target = "0xbc4ca0eda7647a8ab7c2061c2e118a18a936f13d"

    def fake_block(chain, block_number, rpcs=None):
        return {
            "timestamp": hex(1619060596),
            "transactions": [
                {"hash": "0xdead", "from": "0xsomeone", "to": "0xrecipient"},
                {"hash": "0x2219", "from": "0xABA7161A", "to": None},
            ],
        }

    def fake_receipt(chain, tx_hash, rpcs=None):
        if tx_hash == "0x2219":
            return {"contractAddress": target}
        return {"contractAddress": None}

    monkeypatch.setattr(deployer_cache, "_get_block_with_txs", fake_block)
    monkeypatch.setattr(deployer_cache, "_get_receipt", fake_receipt)

    out = deployer_cache.extract_creation_from_block(
        chain="ethereum", contract=target, block_number=12287507, rpcs=["https://x"])
    assert out["creator"] == "0xaba7161a", "creator must be lowercased"
    assert out["tx_hash"] == "0x2219"
    assert out["deploy_block"] == 12287507
    assert out["deploy_ts"] == 1619060596


def test_non_creation_transactions_are_ignored(monkeypatch):
    """Only txs with to=None can create a contract."""
    def fake_block(chain, block_number, rpcs=None):
        return {"timestamp": hex(1000), "transactions": [
            {"hash": "0xa", "from": "0x1", "to": "0xnotnull"},
        ]}

    monkeypatch.setattr(deployer_cache, "_get_block_with_txs", fake_block)
    monkeypatch.setattr(deployer_cache, "_get_receipt",
                        lambda *a, **k: pytest.fail("must not fetch receipts"))

    out = deployer_cache.extract_creation_from_block(
        chain="ethereum", contract="0xabc", block_number=1, rpcs=["https://x"])
    assert out["creator"] == ""


def test_timestamp_survives_when_creator_cannot_be_matched(monkeypatch):
    """Deploy age is useful even when the creator is unresolvable.

    The age gate (MAX_CONTRACT_AGE_HOURS) only needs the timestamp, so a partial
    result is still better than nothing.
    """
    def fake_block(chain, block_number, rpcs=None):
        return {"timestamp": hex(1619060596), "transactions": []}

    monkeypatch.setattr(deployer_cache, "_get_block_with_txs", fake_block)
    out = deployer_cache.extract_creation_from_block(
        chain="ethereum", contract="0xabc", block_number=999, rpcs=["https://x"])
    assert out["creator"] == ""
    assert out["deploy_ts"] == 1619060596
    assert out["deploy_block"] == 999


def test_unavailable_block_yields_the_empty_shape(monkeypatch):
    monkeypatch.setattr(deployer_cache, "_get_block_with_txs",
                        lambda *a, **k: None)
    out = deployer_cache.extract_creation_from_block(
        chain="ethereum", contract="0xabc", block_number=1, rpcs=["https://x"])
    assert out == EMPTY


# ── no explorer APIs anywhere ───────────────────────────────────────────────

def test_module_makes_no_explorer_api_calls():
    """The whole point of the change: no etherscan/blockscout dependency.

    Checks for the API surface actually being invoked, not the words: the module
    carries a comment recording exactly what those explorers returned, and that
    provenance is the reason the change was made.
    """
    import inspect

    src = inspect.getsource(deployer_cache)
    code_lines = [
        line for line in src.splitlines()
        if not line.strip().startswith("#")
    ]
    body = "\n".join(code_lines).lower()
    for marker in ("module=contract", "getcontractcreation", "/api?"):
        assert marker not in body, (
            f"{marker} found in executable code: the explorer path returned "
            "NOTOK/404 on every chain and required per-chain API keys"
        )


def test_creation_info_is_reachable_without_an_explorer_url(monkeypatch):
    """A chain config with no 'explorer' key must still resolve creation data.

    Previously an empty explorer_base short-circuited to the empty result, which is
    what made this silently return nothing.
    """
    chains = {"testchain": {"rpcs": ["https://rpc.example"], "block_step": 60}}

    monkeypatch.setattr(deployer_cache, "find_creation_block",
                        lambda **kw: 4242)
    monkeypatch.setattr(
        deployer_cache, "extract_creation_from_block",
        lambda **kw: {"creator": "0xfeed", "tx_hash": "0xbeef",
                      "deploy_block": 4242, "deploy_ts": 1700000000})

    out = asyncio.run(deployer_cache.get_contract_creation_info(
        "testchain", "0xabc", chains, known_block=4300))
    assert out["creator"] == "0xfeed"
    assert out["deploy_block"] == 4242
