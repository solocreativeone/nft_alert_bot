import pytest
from price_utils import shorten_address, format_floor_display, format_usd, format_eth

def test_shorten_address():
    assert shorten_address("0x34f4a3b7b3b06b5") == "0x34f4...06b5"
    assert shorten_address("0x1234567") == "0x1234567"
    assert shorten_address("") == ""
    assert shorten_address(None) == ""

def test_format_eth():
    assert format_eth(0.0500) == "0.05"
    assert format_eth(1.0) == "1"
    assert format_eth(0.000000) == "0"
    assert format_eth(None) == ""

def test_format_usd():
    assert format_usd(200.0) == "$200"
    assert format_usd(2474.62) == "$2,474.62"
    assert format_usd(1.156) == "$1.16"
    assert format_usd(None) == ""

def test_format_floor_display():
    # Free mint
    assert format_floor_display(floor=None, is_free_mint=True) == "🆓 Free Mint"
    assert format_floor_display(floor=0.0, is_free_mint=True) == "🆓 Free Mint"
    assert format_floor_display(floor=0.05, is_free_mint=True) == "🆓 Free Mint"

    # Floor with USD
    assert format_floor_display(floor=0.05, eth_usd_price=4000.0, is_alert=True) == "💰 Floor: 0.05 ETH | $200"
    assert format_floor_display(floor=0.05, eth_usd_price=4000.0, is_alert=False) == "Floor: 0.05 ETH | $200"

    # Floor without USD
    assert format_floor_display(floor=0.05, eth_usd_price=None, is_alert=True) == "💰 Floor: 0.05 ETH"
    assert format_floor_display(floor=0.05, eth_usd_price=0.0, is_alert=True) == "💰 Floor: 0.05 ETH"

    # Missing floor
    assert format_floor_display(floor=None, is_free_mint=False) is None
    assert format_floor_display(floor=0.0, is_free_mint=False) is None
