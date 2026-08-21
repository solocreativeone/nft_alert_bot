import pytest
from btc_ordinals import parse_inscription_item

def test_parse_inscription_item():
    raw_item = {
        "id": "6fb976373d7c77c688d61299634e83e2a5f57e4e1f72776c5b738102d9921473i0",
        "number": 1234567,
        "content_type": "image/png",
        "address": "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9h5uqvq9z48xz",
        "genesis_tx_id": "6fb976373d7c77c688d61299634e83e2a5f57e4e1f72776c5b738102d9921473",
        "genesis_timestamp": 1675800000,
    }

    parsed = parse_inscription_item(raw_item)
    assert parsed["inscription_id"] == "6fb976373d7c77c688d61299634e83e2a5f57e4e1f72776c5b738102d9921473i0"
    assert parsed["number"] == 1234567
    assert parsed["content_type"] == "image/png"
    assert parsed["creator"].startswith("bc1p")
    assert parsed["image_url"] == "https://ordinals.com/content/6fb976373d7c77c688d61299634e83e2a5f57e4e1f72776c5b738102d9921473i0"
