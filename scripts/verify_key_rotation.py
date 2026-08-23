"""Live-ish Gemini rotation probe: real module code, faked HTTP quota errors.

Run:  python scripts/verify_key_rotation.py

We cannot burn 500 real requests to prove daily-quota rotation, so this drives the
real gemini_filter rotation path (real select_key, real _trip_cooldown, real
checkpoint persistence) while the transport layer raises the exact 429 payload
Google returns. Everything except the network hop is production code.
"""
import asyncio
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Isolate state before importing the modules that read it.
_tmp = tempfile.mkdtemp()
STATE = os.path.join(_tmp, "rotation_state.json")
os.environ["NFT_BOT_STATE_FILE"] = STATE

import checkpoint
import gemini_filter

checkpoint.use_path(STATE)

# The literal error Google's API returns when a free-tier key is out of daily quota.
QUOTA_429 = (
    "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': "
    "'You exceeded your current quota', 'status': 'RESOURCE_EXHAUSTED', "
    "'details': [{'@type': 'type.googleapis.com/google.rpc.QuotaFailure', "
    "'violations': [{'quotaMetric': 'generate_content_free_tier_requests', "
    "'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier'}]}, "
    "{'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '31s'}]}}"
)

POOL = ["KEY_ALPHA", "KEY_BRAVO", "KEY_CHARLIE"]


def main():
    print("=" * 68)
    print("GEMINI KEY ROTATION PROBE (real rotation logic, faked 429 transport)")
    print("=" * 68)

    gemini_filter._key_pool = POOL
    gemini_filter._active_index = 0
    gemini_filter._clients = {k: f"client-{k}" for k in POOL}
    gemini_filter.GEMINI_DAILY_LIMIT = 500
    gemini_filter._score_cache.clear()

    exhausted = set()
    attempts = []

    async def fake_transport(client, prompt, api_key=None):
        attempts.append(api_key)
        state = gemini_filter._key_state(api_key)
        state["count"] = int(state.get("count", 0)) + 1
        gemini_filter._save_key_state(api_key, state)
        if api_key in exhausted:
            raise Exception(QUOTA_429)

        class R:
            text = '{"score":91,"verdict":"LEGIT","reason":"organic mint spread"}'
        return R()

    gemini_filter._rate_limited_generate = fake_transport
    gemini_filter.get_client = lambda api_key=None: f"client-{api_key}"

    print("\n[1] All keys healthy - should use the first key only")
    attempts.clear()
    res = asyncio.run(gemini_filter.gemini_score_nft({"contract": "0xcase1"}))
    print(f"    attempts: {attempts}")
    print(f"    verdict: {res['verdict']} ({res['score']}/100)")
    assert attempts == ["KEY_ALPHA"], attempts
    assert res["verdict"] == "LEGIT"
    print("    PASS")

    print("\n[2] KEY_ALPHA hits daily quota - should rotate to KEY_BRAVO and succeed")
    exhausted.add("KEY_ALPHA")
    attempts.clear()
    res = asyncio.run(gemini_filter.gemini_score_nft({"contract": "0xcase2"}))
    print(f"    attempts: {attempts}")
    print(f"    verdict: {res['verdict']} ({res['score']}/100)")
    assert attempts == ["KEY_ALPHA", "KEY_BRAVO"], attempts
    assert res["verdict"] == "LEGIT", "alert must still go out via the next key"
    print("    PASS - alert survived a key running dry")

    print("\n[3] Next scan should START on KEY_BRAVO (no wasted retry on ALPHA)")
    attempts.clear()
    res = asyncio.run(gemini_filter.gemini_score_nft({"contract": "0xcase3"}))
    print(f"    attempts: {attempts}")
    assert attempts == ["KEY_BRAVO"], attempts
    print("    PASS - exhausted key is not retried")

    print("\n[4] BRAVO also dies - should land on KEY_CHARLIE")
    exhausted.add("KEY_BRAVO")
    attempts.clear()
    res = asyncio.run(gemini_filter.gemini_score_nft({"contract": "0xcase4"}))
    print(f"    attempts: {attempts}")
    print(f"    verdict: {res['verdict']}")
    assert res["verdict"] == "LEGIT", res
    print("    PASS")

    print("\n[5] Whole pool exhausted - must fail CLOSED (RATE_LIMITED, no alert)")
    exhausted.add("KEY_CHARLIE")
    attempts.clear()
    res = asyncio.run(gemini_filter.gemini_score_nft({"contract": "0xcase5"}))
    print(f"    attempts: {attempts}")
    print(f"    verdict: {res['verdict']}")
    print(f"    worth alerting: {gemini_filter.is_worth_alerting(res, 40)}")
    assert res["verdict"] == "RATE_LIMITED", res
    assert gemini_filter.is_worth_alerting(res, 40) is False
    print("    PASS - stays quiet rather than sending unvetted drops")

    print("\n[6] Quota state persisted across a restart")
    checkpoint.flush(force=True)
    on_disk = json.load(open(STATE, encoding="utf-8"))
    print(f"    fingerprinted key entries: {len(on_disk['gemini'])}")
    for key in POOL:
        assert key not in json.dumps(on_disk), f"{key} LEAKED into state.json"
    print("    no raw key present in state.json (fingerprints only)")

    checkpoint._state = None
    checkpoint._seen_order = {}
    gemini_filter._active_index = 0
    assert gemini_filter.select_key() is None, "restart must respect exhausted keys"
    print("    PASS - after restart all keys still known-exhausted, no hammering")

    print("\n[7] Status snapshot")
    for row in gemini_filter.pool_status()["keys"]:
        print(f"    key #{row['index']} fp={row['fingerprint']} "
              f"used={row['used_today']}/{row['limit']} available={row['available']}")

    print("\n" + "=" * 68)
    print("RESULT: PASS - rotation, fail-closed, persistence and secrecy verified")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
