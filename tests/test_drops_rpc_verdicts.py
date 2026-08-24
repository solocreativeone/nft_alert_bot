"""Regression tests for the startup probe promoting a known-dead endpoint.

Observed in the first live smoke run on 2026-08-22:

    [Drops] ⚠️  No RPC endpoint passed the health probe; keeping configured order
    [Drops] RPC base: preferring alchemy

Alchemy is over quota on base and answers HTTP 429, so it can never serve a
scan. It still ended up at index 0.

The chain: probe_rpc_endpoint returned a plain bool, so a timeout was recorded as
False, exactly the same verdict as an explicit 429. Under 12-way concurrency the
public base endpoints timed out at PROBE_TIMEOUT=6, so every candidate for that
chain looked False, reorder fell through to "keep configured order", and the
configured order is the one with Alchemy inserted at the front.

The fix separates two different facts:

    False -> the endpoint answered and refused (429/403/500, JSON-RPC error).
             A verdict. Must sink to the bottom.
    None  -> no answer at all (timeout, DNS, refused connection).
             Not evidence of anything. Keep configured position.

A definitive failure must outrank nothing: an endpoint we know is broken can
never be preferred over one we merely failed to reach.
"""
import drops


def _post_stub(responses):
    """requests.post stub dispatching on JSON-RPC method name."""
    def post(url, json={}, timeout=None, **kw):
        entry = responses.get(json.get("method", ""))
        if entry is None:
            raise AssertionError(f"unexpected method: {json.get('method')}")
        if callable(entry):
            return entry()
        status, body = entry

        class Res:
            status_code = status
            @staticmethod
            def json():
                return body
        return Res()
    return post


_TIP = (200, {"result": "0x18a5f2b"})


# ── tri-state probe verdicts ──────────────────────────────────────────────────

def test_timeout_is_inconclusive_not_unhealthy(monkeypatch):
    """A timeout says nothing about the endpoint. It must not read as a verdict."""
    def timeout():
        raise drops.requests.exceptions.ReadTimeout("timed out")

    monkeypatch.setattr(drops.requests, "post", _post_stub({"eth_blockNumber": timeout}))
    assert drops.probe_rpc_endpoint("https://slow.example") is None


def test_connection_error_is_inconclusive(monkeypatch):
    def refused():
        raise drops.requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr(drops.requests, "post", _post_stub({"eth_blockNumber": refused}))
    assert drops.probe_rpc_endpoint("https://down.example") is None


def test_quota_refusal_is_a_definitive_verdict(monkeypatch):
    """HTTP 429 is the endpoint telling us it will not serve us. That is a fact."""
    monkeypatch.setattr(drops.requests, "post", _post_stub({
        "eth_blockNumber": (429, {"error": {"message": "Monthly capacity limit exceeded."}}),
    }))
    assert drops.probe_rpc_endpoint("https://base-mainnet.g.alchemy.com/v2/k") is False


def test_network_not_enabled_is_a_definitive_verdict(monkeypatch):
    monkeypatch.setattr(drops.requests, "post", _post_stub({
        "eth_blockNumber": (403, {"error": {"message": "OPT_MAINNET is not enabled."}}),
    }))
    assert drops.probe_rpc_endpoint("https://opt-mainnet.g.alchemy.com/v2/k") is False


def test_response_too_large_is_a_definitive_verdict(monkeypatch):
    """mainnet.base.org: answers eth_blockNumber, refuses the real query."""
    monkeypatch.setattr(drops.requests, "post", _post_stub({
        "eth_blockNumber": _TIP,
        "eth_getLogs": (500, {"error": {"message": "backend response too large"}}),
    }))
    assert drops.probe_rpc_endpoint("https://mainnet.base.org") is False


def test_healthy_endpoint_still_returns_true(monkeypatch):
    monkeypatch.setattr(drops.requests, "post", _post_stub({
        "eth_blockNumber": _TIP,
        "eth_getLogs": (200, {"result": []}),
    }))
    assert drops.probe_rpc_endpoint("https://good.example") is True


def test_getlogs_timeout_after_healthy_tip_is_inconclusive(monkeypatch):
    """Reaching the tip then timing out on the real query is still no verdict."""
    def timeout():
        raise drops.requests.exceptions.ReadTimeout("timed out")

    monkeypatch.setattr(drops.requests, "post", _post_stub({
        "eth_blockNumber": _TIP,
        "eth_getLogs": timeout,
    }))
    assert drops.probe_rpc_endpoint("https://slow-logs.example") is None


# ── the production regression ─────────────────────────────────────────────────

def test_refusing_endpoint_never_preferred_over_unreachable_one(monkeypatch):
    """The exact smoke-run failure, reduced.

    Alchemy answers 429 (definitive). Every public endpoint times out
    (inconclusive). Alchemy must not be preferred.
    """
    monkeypatch.setitem(
        drops.EVM_CHAINS, "testchain",
        {"rpcs": ["https://public-a.example", "https://public-b.example"],
         "explorer": "https://x", "opensea_chain": None, "block_step": 60},
    )
    monkeypatch.setitem(drops._ALCHEMY_SUBDOMAINS, "testchain", "testchain-mainnet")
    monkeypatch.setattr(drops, "ALCHEMY_API_KEY", "overquota")
    alchemy = "https://testchain-mainnet.g.alchemy.com/v2/overquota"

    def probe(url, **kw):
        return False if url == alchemy else None

    drops.wire_healthy_rpcs(chains=["testchain"], probe=probe)
    rpcs = drops.EVM_CHAINS["testchain"]["rpcs"]
    assert rpcs[0] != alchemy, "an endpoint that answered 429 must never be tried first"
    assert rpcs[-1] == alchemy, "a definitive refusal belongs last"
    assert set(rpcs) == {alchemy, "https://public-a.example", "https://public-b.example"}


def test_all_inconclusive_keeps_configured_order(monkeypatch):
    """No information at all means no reordering. Do not invent a preference."""
    urls = ["https://a.example", "https://b.example", "https://c.example"]
    monkeypatch.setitem(
        drops.EVM_CHAINS, "testchain",
        {"rpcs": list(urls), "explorer": "https://x",
         "opensea_chain": None, "block_step": 60},
    )
    monkeypatch.setattr(drops, "ALCHEMY_API_KEY", "")

    drops.wire_healthy_rpcs(chains=["testchain"], probe=lambda url, **kw: None)
    assert drops.EVM_CHAINS["testchain"]["rpcs"] == urls


def test_all_definitively_bad_still_returns_every_endpoint(monkeypatch):
    """Nothing works. Keep them all anyway: runtime failover is the real net."""
    urls = ["https://a.example", "https://b.example"]
    monkeypatch.setitem(
        drops.EVM_CHAINS, "testchain",
        {"rpcs": list(urls), "explorer": "https://x",
         "opensea_chain": None, "block_step": 60},
    )
    monkeypatch.setattr(drops, "ALCHEMY_API_KEY", "")

    drops.wire_healthy_rpcs(chains=["testchain"], probe=lambda url, **kw: False)
    assert set(drops.EVM_CHAINS["testchain"]["rpcs"]) == set(urls)


def test_ordering_is_healthy_then_unknown_then_refusing(monkeypatch):
    verdicts = {
        "https://refuses.example": False,
        "https://unknown.example": None,
        "https://works.example": True,
    }
    monkeypatch.setitem(
        drops.EVM_CHAINS, "testchain",
        {"rpcs": ["https://refuses.example", "https://unknown.example",
                  "https://works.example"],
         "explorer": "https://x", "opensea_chain": None, "block_step": 60},
    )
    monkeypatch.setattr(drops, "ALCHEMY_API_KEY", "")

    drops.wire_healthy_rpcs(chains=["testchain"], probe=lambda url, **kw: verdicts[url])
    assert drops.EVM_CHAINS["testchain"]["rpcs"] == [
        "https://works.example", "https://unknown.example", "https://refuses.example",
    ]


# ── timeout budget must fit observed latency ───────────────────────────────────

def test_probe_timeout_accommodates_observed_latency():
    """base.drpc.org and base.gateway.tenderly.co both needed ~11.5s when probed
    under load. PROBE_TIMEOUT=6 marked them failed, which is what emptied the
    healthy list and let Alchemy through."""
    assert drops.PROBE_TIMEOUT >= 12, (
        "healthy public endpoints measured 11.3-11.7s under concurrency; "
        "a tighter timeout reports them as failures"
    )


def test_concurrency_does_not_exceed_endpoint_count_by_much():
    """33 endpoints at 12-way concurrency saturated a home connection and caused
    the timeouts. Keep the pool modest."""
    assert 4 <= drops.PROBE_CONCURRENCY <= 8


def test_total_budget_allows_a_full_pass():
    """Budget must cover ceil(endpoints/concurrency) * timeout, or the probe
    routinely cuts off mid-pass and learns nothing."""
    assert drops.PROBE_TOTAL_BUDGET >= drops.PROBE_TIMEOUT * 4
