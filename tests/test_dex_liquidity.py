import dex_liquidity


def test_parse_dex_data_empty():
    res = dex_liquidity.parse_dex_data({})
    assert res["has_liquidity"] is False
    assert res["liquidity_usd"] == 0.0
    assert res["formatted_line"] == ""


def test_parse_dex_data_with_liquidity():
    mock_payload = {
        "pairs": [
            {
                "dexId": "uniswap",
                "pairAddress": "0xPairAddress123",
                "baseToken": {"name": "Test NFT Token", "symbol": "TEST"},
                "quoteToken": {"symbol": "ETH"},
                "priceUsd": "1.25",
                "liquidity": {"usd": 24500.0},
                "volume": {"h24": 12300.0},
            },
            {
                "dexId": "uniswap",
                "pairAddress": "0xPairAddressLow",
                "baseToken": {"name": "Test NFT Token", "symbol": "TEST"},
                "quoteToken": {"symbol": "USDC"},
                "liquidity": {"usd": 500.0},
                "volume": {"h24": 100.0},
            }
        ]
    }

    res = dex_liquidity.parse_dex_data(mock_payload)
    assert res["has_liquidity"] is True
    assert res["liquidity_usd"] == 24500.0
    assert res["volume_24h"] == 12300.0
    assert res["dex_id"] == "Uniswap"
    assert "💧 Liquidity: <b>$24.5K</b>" in res["formatted_line"]
    assert "24h Vol: <b>$12.3K</b>" in res["formatted_line"]


# DexScreener's /tokens/<address> endpoint returns pairs for the same address on
# EVERY chain it knows. Confirmed live: USDC's Ethereum address returns 29
# PulseChain pairs (max $10.7M) alongside 1 Ethereum pair ($884K), so an
# unfiltered "highest liquidity wins" sort reports the wrong chain's pool.
_CROSS_CHAIN_PAYLOAD = {
    "pairs": [
        {
            "chainId": "pulsechain",
            "dexId": "pulsex",
            "pairAddress": "0xPulseWhale",
            "baseToken": {"name": "Wrapped Thing", "symbol": "WTHING"},
            "quoteToken": {"symbol": "WPLS"},
            "liquidity": {"usd": 10_673_191.0},
            "volume": {"h24": 43_383.0},
        },
        {
            "chainId": "ethereum",
            "dexId": "uniswap",
            "pairAddress": "0xEthPair",
            "baseToken": {"name": "Real Token", "symbol": "REAL"},
            "quoteToken": {"symbol": "WETH"},
            "liquidity": {"usd": 884_085.0},
            "volume": {"h24": 1_000.0},
        },
    ]
}


def test_chain_filter_ignores_other_chains():
    res = dex_liquidity.parse_dex_data(_CROSS_CHAIN_PAYLOAD, "ethereum")
    assert res["liquidity_usd"] == 884_085.0, "must not report the PulseChain pool"
    assert res["dex_id"] == "Uniswap"
    assert res["pair_address"] == "0xEthPair"


def test_chain_filter_is_case_insensitive():
    res = dex_liquidity.parse_dex_data(_CROSS_CHAIN_PAYLOAD, "Ethereum")
    assert res["pair_address"] == "0xEthPair"


def test_chain_with_no_pairs_reports_no_liquidity():
    """zora/robinhood aren't on DexScreener; never quote another chain's pool."""
    res = dex_liquidity.parse_dex_data(_CROSS_CHAIN_PAYLOAD, "zora")
    assert res["has_liquidity"] is False
    assert res["liquidity_usd"] == 0.0
    assert res["formatted_line"] == ""


def test_empty_chain_keeps_cross_chain_behaviour():
    """Backward compatible: no chain given means the old highest-anywhere sort."""
    res = dex_liquidity.parse_dex_data(_CROSS_CHAIN_PAYLOAD)
    assert res["liquidity_usd"] == 10_673_191.0
