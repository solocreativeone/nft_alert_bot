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
