"""RPC endpoint health probing and ordering.

Context for these tests: the bot shipped with Alchemy prepended to the front of
five chains' RPC lists on the assumption that a configured key means a working
endpoint. In practice three chains were over quota (HTTP 429) and two had never
been enabled for the app at all (HTTP 403), so the first endpoint tried on every
cycle could not succeed. Two public endpoints (mainnet.base.org,
mainnet.optimism.io) were returning HTTP 500 from first position as well.

The rule these tests enforce: an endpoint that fails a health probe must never
occupy first position. Ordering is decided by what answers, not by configuration.
"""
import drops


# ── probe_rpc_endpoint: what counts as healthy ────────────────────────────────
#
# The probe issues TWO calls: eth_blockNumber to find the tip, then eth_getLogs
# over the last block_step blocks. Mocks must answer both, keyed on method.

def _mock_post(responses):
    """Build a requests.post stub that dispatches on the JSON-RPC method name.

    `responses` maps method name -> (status_code, body) or a callable raising.
    """
    def post(url, json=None, timeout=None, **kw):
        method = (json or {}).get("method", "")
        entry = responses.get(method)
        if entry is None:
            raise AssertionError(f"unexpected RPC method probed: {method}")
        if callable(entry):
            return entry()
        status, body = entry

        class Res:
            status_code = status
            @staticmethod
            def json():
                if isinstance(body, Exception):
                    raise body
                return body
        return Res()
    return post


_TIP_OK = (200, {"jsonrpc": "2.0", "id": 1, "result": "0x18a5f2b"})


def test_probe_accepts_endpoint_that_serves_getlogs(monkeypatch):
    monkeypatch.setattr(drops.requests, "post", _mock_post({
        "eth_blockNumber": _TIP_OK,
        "eth_getLogs": (200, {"jsonrpc": "2.0", "id": 1, "result": [{"address": "0xabc"}]}),
    }))
    assert drops.probe_rpc_endpoint("https://good.example") is True


def test_probe_accepts_empty_log_result(monkeypatch):
    """Zero logs in the window is a valid answer, not a failure."""
    monkeypatch.setattr(drops.requests, "post", _mock_post({
        "eth_blockNumber": _TIP_OK,
        "eth_getLogs": (200, {"jsonrpc": "2.0", "id": 1, "result": []}),
    }))
    assert drops.probe_rpc_endpoint("https://quiet-chain.example") is True


def test_probe_rejects_backend_response_too_large(monkeypatch):
    """The mainnet.base.org / mainnet.optimism.io case, and the reason this
    probe tests eth_getLogs instead of eth_blockNumber.

    These endpoints answer eth_blockNumber with a clean HTTP 200 and then fail
    the real query with HTTP 500 "backend response too large". Probing only the
    cheap method promoted them to first position, where base burned 50.9s per
    cycle before failing over.
    """
    monkeypatch.setattr(drops.requests, "post", _mock_post({
        "eth_blockNumber": _TIP_OK,
        "eth_getLogs": (500, {"error": {"message": "backend response too large"}}),
    }))
    assert drops.probe_rpc_endpoint("https://mainnet.base.org") is False


def test_probe_rejects_alchemy_quota_exhausted(monkeypatch):
    """HTTP 429 'Monthly capacity limit exceeded' - the real ethereum/base/arbitrum case."""
    monkeypatch.setattr(drops.requests, "post", _mock_post({
        "eth_blockNumber": (429, {"error": {"code": 429,
                                            "message": "Monthly capacity limit exceeded."}}),
    }))
    assert drops.probe_rpc_endpoint("https://eth-mainnet.g.alchemy.com/v2/k") is False


def test_probe_rejects_network_not_enabled(monkeypatch):
    """HTTP 403 'MATIC_MAINNET is not enabled for this app' - the polygon/optimism case.

    Distinct from 429: this endpoint never worked and never will without a
    dashboard change, so retrying it is pure waste.
    """
    monkeypatch.setattr(drops.requests, "post", _mock_post({
        "eth_blockNumber": (403, {"error": {"code": 403,
                                            "message": "MATIC_MAINNET is not enabled for this app."}}),
    }))
    assert drops.probe_rpc_endpoint("https://polygon-mainnet.g.alchemy.com/v2/k") is False


def test_probe_rejects_200_carrying_jsonrpc_error(monkeypatch):
    """Some providers return HTTP 200 with an error body. Status alone is not enough.

    rpc.mevblocker.io does exactly this with 'query returned more than 10000 results'.
    """
    monkeypatch.setattr(drops.requests, "post", _mock_post({
        "eth_blockNumber": _TIP_OK,
        "eth_getLogs": (200, {"jsonrpc": "2.0", "id": 1,
                              "error": {"code": -32005,
                                        "message": "query returned more than 10000 results"}}),
    }))
    assert drops.probe_rpc_endpoint("https://rpc.mevblocker.io") is False


def test_probe_rejects_non_list_result(monkeypatch):
    monkeypatch.setattr(drops.requests, "post", _mock_post({
        "eth_blockNumber": _TIP_OK,
        "eth_getLogs": (200, {"jsonrpc": "2.0", "id": 1, "result": "not-a-list"}),
    }))
    assert drops.probe_rpc_endpoint("https://weird.example") is False


def test_probe_rejects_unparseable_body(monkeypatch):
    monkeypatch.setattr(drops.requests, "post", _mock_post({
        "eth_blockNumber": (200, ValueError("not json")),
    }))
    assert drops.probe_rpc_endpoint("https://html-error-page.example") is False


def test_probe_swallows_connection_error(monkeypatch):
    """rpc.builder0x69.io raised ConnectionError in the live probe.

    Unreachable is inconclusive, not a verdict: see test_drops_rpc_verdicts.py.
    """
    def boom():
        raise drops.requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr(drops.requests, "post", _mock_post({"eth_blockNumber": boom}))
    assert drops.probe_rpc_endpoint("https://rpc.builder0x69.io") is None


def test_probe_survives_dns_failure(monkeypatch):
    """A phone/laptop losing DNS must not crash startup, and must not be recorded
    as a verdict against the endpoint."""
    def boom():
        raise drops.requests.exceptions.ConnectionError(
            "Failed to resolve 'mainnet.optimism.io'")

    monkeypatch.setattr(drops.requests, "post", _mock_post({"eth_blockNumber": boom}))
    assert drops.probe_rpc_endpoint("https://mainnet.optimism.io") is None


def test_probe_never_raises_on_placeholder_url():
    assert drops.probe_rpc_endpoint("") is False
    assert drops.probe_rpc_endpoint("https://eth.example/v2/YOUR_KEY") is False


def test_probe_uses_the_chains_block_step(monkeypatch):
    """A chain's configured block_step must drive the probe window, so the probe
    exercises the same range size the scanner will actually request."""
    seen = {}

    def post(url, json={}, timeout=None, **kw):
        method = json.get("method")
        if method == "eth_blockNumber":
            class R:
                status_code = 200
                @staticmethod
                def json():
                    return {"result": hex(1000)}
            return R()
        seen["from"] = int(json["params"][0]["fromBlock"], 16)
        seen["to"] = int(json["params"][0]["toBlock"], 16)

        class R2:
            status_code = 200
            @staticmethod
            def json():
                return {"result": []}
        return R2()

    monkeypatch.setattr(drops.requests, "post", post)
    drops.probe_rpc_endpoint("https://x.example", block_step=60)
    assert seen["to"] - seen["from"] == 60


def test_probe_clamps_negative_from_block(monkeypatch):
    """A near-genesis tip must not produce a negative fromBlock."""
    def post(url, json={}, timeout=None, **kw):
        if json.get("method") == "eth_blockNumber":
            class R:
                status_code = 200
                @staticmethod
                def json():
                    return {"result": hex(5)}
            return R()
        assert int(json["params"][0]["fromBlock"], 16) >= 0

        class R2:
            status_code = 200
            @staticmethod
            def json():
                return {"result": []}
        return R2()

    monkeypatch.setattr(drops.requests, "post", post)
    assert drops.probe_rpc_endpoint("https://new-chain.example", block_step=60) is True


# ── reorder_rpcs_by_health: dead endpoints lose first position ────────────────

def _fixed_health(mapping):
    return lambda url, **kw: mapping.get(url, False)


def test_probe_receives_block_step_from_chain_config(monkeypatch):
    """wire_healthy_rpcs must forward each chain's block_step down to the probe."""
    got = {}

    def spy(url, **kw):
        got[url] = kw.get("block_step")
        return True

    monkeypatch.setitem(
        drops.EVM_CHAINS, "testchain",
        {"rpcs": ["https://a.example"], "explorer": "https://x",
         "opensea_chain": None, "block_step": 42},
    )
    monkeypatch.setattr(drops, "ALCHEMY_API_KEY", "")
    drops.wire_healthy_rpcs(chains=["testchain"], probe=spy)
    assert got["https://a.example"] == 42


def test_dead_first_endpoint_is_demoted():
    """The core regression: a failing endpoint must not stay at index 0."""
    rpcs = ["https://dead.example", "https://alive.example"]
    out = drops.reorder_rpcs_by_health(
        rpcs, probe=_fixed_health({"https://alive.example": True})
    )
    assert out[0] == "https://alive.example"
    assert "https://dead.example" in out, "dead endpoints are kept as last-resort fallback"


def test_healthy_endpoints_keep_their_relative_order():
    """Ordering encodes a latency preference; probing must not shuffle the winners."""
    rpcs = ["https://a.example", "https://b.example", "https://c.example"]
    out = drops.reorder_rpcs_by_health(
        rpcs,
        probe=_fixed_health({"https://a.example": True, "https://c.example": True}),
    )
    assert out == ["https://a.example", "https://c.example", "https://b.example"]


def test_all_dead_preserves_original_list():
    """Never hand back an empty list: a wrong order still beats no endpoints."""
    rpcs = ["https://x.example", "https://y.example"]
    out = drops.reorder_rpcs_by_health(rpcs, probe=_fixed_health({}))
    assert out == rpcs


def test_reorder_is_idempotent():
    rpcs = ["https://dead.example", "https://alive.example"]
    health = _fixed_health({"https://alive.example": True})
    once = drops.reorder_rpcs_by_health(rpcs, probe=health)
    twice = drops.reorder_rpcs_by_health(once, probe=health)
    assert once == twice


def test_reorder_deduplicates():
    rpcs = ["https://a.example", "https://a.example", "https://b.example"]
    out = drops.reorder_rpcs_by_health(
        rpcs, probe=_fixed_health({"https://a.example": True})
    )
    assert out.count("https://a.example") == 1


# ── wire_healthy_rpcs: end-to-end over EVM_CHAINS ────────────────────────────

def test_wiring_promotes_only_responsive_endpoints(monkeypatch):
    monkeypatch.setitem(
        drops.EVM_CHAINS, "testchain",
        {"rpcs": ["https://dead.example", "https://alive.example"],
         "explorer": "https://x", "opensea_chain": None, "block_step": 10},
    )
    drops.wire_healthy_rpcs(
        chains=["testchain"], probe=_fixed_health({"https://alive.example": True})
    )
    assert drops.EVM_CHAINS["testchain"]["rpcs"][0] == "https://alive.example"


def test_wiring_leaves_unprobed_chains_untouched(monkeypatch):
    original = list(drops.EVM_CHAINS["ethereum"]["rpcs"])
    monkeypatch.setitem(
        drops.EVM_CHAINS, "testchain",
        {"rpcs": ["https://dead.example"], "explorer": "https://x",
         "opensea_chain": None, "block_step": 10},
    )
    drops.wire_healthy_rpcs(chains=["testchain"], probe=_fixed_health({}))
    assert drops.EVM_CHAINS["ethereum"]["rpcs"] == original


def test_unhealthy_alchemy_does_not_take_first_position(monkeypatch):
    """Reproduces the shipped bug directly.

    A configured key put Alchemy first even when that app was over quota (429)
    or the network was never enabled (403).
    """
    monkeypatch.setitem(
        drops.EVM_CHAINS, "testchain",
        {"rpcs": ["https://public-a.example", "https://public-b.example"],
         "explorer": "https://x", "opensea_chain": None, "block_step": 10},
    )
    alchemy = "https://testchain-mainnet.g.alchemy.com/v2/deadkey"
    monkeypatch.setitem(drops._ALCHEMY_SUBDOMAINS, "testchain", "testchain-mainnet")
    monkeypatch.setattr(drops, "ALCHEMY_API_KEY", "deadkey")

    drops.wire_healthy_rpcs(
        chains=["testchain"],
        probe=_fixed_health({"https://public-a.example": True,
                             "https://public-b.example": True}),
    )
    rpcs = drops.EVM_CHAINS["testchain"]["rpcs"]
    assert rpcs[0] != alchemy, "an unhealthy Alchemy endpoint must not be tried first"
    assert rpcs[0] == "https://public-a.example"


def test_healthy_alchemy_is_preferred(monkeypatch):
    """Self-healing: if the key starts working, Alchemy earns first position again."""
    monkeypatch.setitem(
        drops.EVM_CHAINS, "testchain",
        {"rpcs": ["https://public-a.example"], "explorer": "https://x",
         "opensea_chain": None, "block_step": 10},
    )
    alchemy = "https://testchain-mainnet.g.alchemy.com/v2/livekey"
    monkeypatch.setitem(drops._ALCHEMY_SUBDOMAINS, "testchain", "testchain-mainnet")
    monkeypatch.setattr(drops, "ALCHEMY_API_KEY", "livekey")

    drops.wire_healthy_rpcs(
        chains=["testchain"],
        probe=_fixed_health({alchemy: True, "https://public-a.example": True}),
    )
    assert drops.EVM_CHAINS["testchain"]["rpcs"][0] == alchemy


def test_no_alchemy_key_is_a_noop(monkeypatch):
    """Existing behavior preserved: no key means no Alchemy URL is ever built."""
    monkeypatch.setitem(
        drops.EVM_CHAINS, "testchain",
        {"rpcs": ["https://public-a.example"], "explorer": "https://x",
         "opensea_chain": None, "block_step": 10},
    )
    monkeypatch.setitem(drops._ALCHEMY_SUBDOMAINS, "testchain", "testchain-mainnet")
    monkeypatch.setattr(drops, "ALCHEMY_API_KEY", "")

    drops.wire_healthy_rpcs(
        chains=["testchain"], probe=_fixed_health({"https://public-a.example": True})
    )
    rpcs = drops.EVM_CHAINS["testchain"]["rpcs"]
    assert not any("alchemy" in u for u in rpcs)


def test_placeholder_alchemy_key_is_a_noop(monkeypatch):
    monkeypatch.setitem(
        drops.EVM_CHAINS, "testchain",
        {"rpcs": ["https://public-a.example"], "explorer": "https://x",
         "opensea_chain": None, "block_step": 10},
    )
    monkeypatch.setitem(drops._ALCHEMY_SUBDOMAINS, "testchain", "testchain-mainnet")
    monkeypatch.setattr(drops, "ALCHEMY_API_KEY", "YOUR_KEY_HERE")

    drops.wire_healthy_rpcs(
        chains=["testchain"], probe=_fixed_health({"https://public-a.example": True})
    )
    assert not any("alchemy" in u for u in drops.EVM_CHAINS["testchain"]["rpcs"])


# ── startup budget and concurrency ───────────────────────────────────────────
#
# The first cut probed serially with only a per-request `requests` timeout. That
# timeout is inter-byte, not total, so one endpoint held a probe open for 38.7s
# and the full startup probe cost 390s, delaying the first scan by 6.5 minutes.

def test_probing_is_concurrent_not_serial(monkeypatch):
    """A slow endpoint must not serialize the whole startup probe."""
    import time as _time

    monkeypatch.setitem(
        drops.EVM_CHAINS, "testchain",
        {"rpcs": [f"https://slow-{i}.example" for i in range(8)],
         "explorer": "https://x", "opensea_chain": None, "block_step": 10},
    )
    monkeypatch.setattr(drops, "ALCHEMY_API_KEY", "")

    def slow_probe(url, **kw):
        _time.sleep(0.25)
        return True

    t0 = _time.monotonic()
    drops.wire_healthy_rpcs(chains=["testchain"], probe=slow_probe)
    elapsed = _time.monotonic() - t0
    assert elapsed < 1.4, f"8 x 0.25s probes took {elapsed:.2f}s; probing is serial"


def test_budget_cutoff_leaves_remaining_endpoints_untested(monkeypatch):
    """Blowing the budget must not silently mark unprobed endpoints unhealthy."""
    import time as _time

    urls = [f"https://e-{i}.example" for i in range(6)]
    monkeypatch.setitem(
        drops.EVM_CHAINS, "testchain",
        {"rpcs": list(urls), "explorer": "https://x",
         "opensea_chain": None, "block_step": 10},
    )
    monkeypatch.setattr(drops, "ALCHEMY_API_KEY", "")

    def crawling(url, **kw):
        _time.sleep(0.3)
        return True

    drops.wire_healthy_rpcs(chains=["testchain"], probe=crawling, budget=0)
    assert set(drops.EVM_CHAINS["testchain"]["rpcs"]) == set(urls), "no endpoint may be dropped"


def test_untested_endpoint_outranks_known_bad(monkeypatch):
    """An endpoint we never probed is untested, not unhealthy.

    If the budget expires we must not demote a possibly-good endpoint below one
    we positively know is broken.
    """
    monkeypatch.setitem(
        drops.EVM_CHAINS, "testchain",
        {"rpcs": ["https://known-bad.example", "https://never-probed.example"],
         "explorer": "https://x", "opensea_chain": None, "block_step": 10},
    )
    monkeypatch.setattr(drops, "ALCHEMY_API_KEY", "")

    def probe(url, **kw):
        if url == "https://known-bad.example":
            return False
        raise TimeoutError("would have been cut off by the budget")

    drops.wire_healthy_rpcs(chains=["testchain"], probe=probe)
    rpcs = drops.EVM_CHAINS["testchain"]["rpcs"]
    assert rpcs[0] == "https://never-probed.example"


def test_probe_exception_is_not_fatal(monkeypatch):
    """A probe that raises must not crash startup, and must not be treated as a
    verdict: an error means unknown health, so the endpoint keeps its position."""
    monkeypatch.setitem(
        drops.EVM_CHAINS, "testchain",
        {"rpcs": ["https://raises.example", "https://ok.example"],
         "explorer": "https://x", "opensea_chain": None, "block_step": 10},
    )
    monkeypatch.setattr(drops, "ALCHEMY_API_KEY", "")

    def probe(url, **kw):
        if url == "https://raises.example":
            raise RuntimeError("boom")
        return True

    drops.wire_healthy_rpcs(chains=["testchain"], probe=probe)
    rpcs = drops.EVM_CHAINS["testchain"]["rpcs"]
    assert set(rpcs) == {"https://raises.example", "https://ok.example"}
    assert "https://ok.example" in rpcs


def test_probe_budget_default_is_bounded():
    """Guards against a regression to unbounded serial probing.

    The budget must stay bounded but also allow a full pass: at PROBE_TIMEOUT=15
    with 6-way concurrency over ~33 endpoints, too tight a budget cuts off
    mid-sweep and the probe learns nothing. See test_total_budget_allows_a_full_pass.
    """
    assert drops.PROBE_TOTAL_BUDGET <= 120
    assert drops.PROBE_CONCURRENCY > 1


# ── import purity ────────────────────────────────────────────────────────────
def test_importing_drops_makes_no_network_calls(monkeypatch):
    """Probing must be an explicit startup call, never an import side effect.

    Network I/O at import time makes the whole suite slow and flaky and would
    stall `import drops` on a machine with no connectivity.
    """
    import importlib

    calls = []
    monkeypatch.setattr(drops.requests, "post",
                        lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(
                            AssertionError("network call during import")))
    importlib.reload(drops)
    assert calls == []
