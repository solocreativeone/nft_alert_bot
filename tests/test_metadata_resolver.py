import pytest
import base64
import json
from metadata_resolver import resolve_uri, parse_data_uri_json, _normalize_metadata

def test_resolve_uri_ipfs():
    uri = "ipfs://QmXoypizjW3WknFiJnKLwHCnL72vedxjQkDDP1mXWo6uco/1.json"
    resolved = resolve_uri(uri)
    assert resolved.startswith("https://ipfs.io/ipfs/QmXoypizjW3WknFiJnKLwHCnL72vedxjQkDDP1mXWo6uco/1.json")

def test_resolve_uri_arweave():
    uri = "ar://tJ2aD3vPj5s6d7f8g9"
    resolved = resolve_uri(uri)
    assert resolved == "https://arweave.net/tJ2aD3vPj5s6d7f8g9"

def test_parse_data_uri_json():
    data = {"name": "OnChain NFT #1", "image": "ipfs://QmImage123"}
    encoded = base64.b64encode(json.dumps(data).encode("utf-8")).decode("utf-8")
    uri = f"data:application/json;base64,{encoded}"
    
    parsed = parse_data_uri_json(uri)
    assert parsed["name"] == "OnChain NFT #1"
    assert parsed["image"] == "ipfs://QmImage123"

def test_normalize_metadata():
    raw = {
        "name": "Cool Ape",
        "description": "A very cool ape",
        "image": "ipfs://QmApeImage"
    }
    normalized = _normalize_metadata(raw)
    assert normalized["name"] == "Cool Ape"
    assert normalized["image_url"].startswith("https://ipfs.io/ipfs/QmApeImage")
