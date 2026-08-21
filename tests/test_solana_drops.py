import pytest
from solana_drops import extract_solana_mint_info

def test_extract_solana_mint_info():
    tx_data = {
        "meta": {
            "err": None,
            "logMessages": [
                "Program cndy3Z4yapfJBmL3DGmm5pkydaxDoZSfhHJtuW6WzB invoke [1]",
                "Program log: Minting token with URI https://arweave.net/SampleTxId123",
                "Program cndy3Z4yapfJBmL3DGmm5pkydaxDoZSfhHJtuW6WzB success"
            ],
            "postTokenBalances": [
                {
                    "mint": "NFTMint1111111111111111111111111111111111112",
                    "uiTokenAmount": {"decimals": 0, "uiAmount": 1}
                }
            ]
        },
        "transaction": {
            "message": {
                "accountKeys": [
                    {"pubkey": "CreatorWallet1111111111111111111111111111", "signer": True},
                    {"pubkey": "NFTMint1111111111111111111111111111111111112", "signer": False}
                ]
            }
        }
    }

    info = extract_solana_mint_info(tx_data)
    assert info["mint_address"] == "NFTMint1111111111111111111111111111111111112"
    assert info["creator"] == "CreatorWallet1111111111111111111111111111"
    assert info["token_uri"] == "https://arweave.net/SampleTxId123"

def test_extract_failed_tx():
    tx_data = {
        "meta": {"err": "InstructionError"}
    }
    assert extract_solana_mint_info(tx_data) == {}
