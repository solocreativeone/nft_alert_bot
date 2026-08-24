"""Regression tests for the Ordinals scanner alerting on everything.

Third-run report: "Ordinal feels like noise what is the honest blocker here."

Verified against the live feed: 13 of 15 recent inscriptions were BRC-20 token
operations (content type text/plain, bodies like {"p":"brc-20","op":"mint"}), the
other 2 were plain text. Zero were images. The scanner had no content filter, so
every one was described to Gemini with identical hardcoded stats and alerted when
it scored >= GEMINI_MIN_SCORE, which they all did at 40-50 because Gemini was
scoring the same template every time.

BRC-20 is a fungible-token standard, not NFT art. An NFT alert bot should not be
forwarding token mints, and it certainly should not spend a Gemini call scoring
each one, especially with a 500/day quota that this exhausted in a single session.

The fix filters to image inscriptions BEFORE the Gemini call, so the noise is gone
and the quota is preserved.
"""
import btc_ordinals


def test_brc20_json_inscription_is_not_an_image():
    assert btc_ordinals.is_image_inscription("text/plain;charset=utf-8") is False
    assert btc_ordinals.is_image_inscription("text/plain") is False


def test_bitmap_text_inscription_is_not_an_image():
    """963863.bitmap arrived as text/plain in the live run."""
    assert btc_ordinals.is_image_inscription("text/plain") is False


def test_real_image_content_types_pass():
    for ct in ("image/png", "image/jpeg", "image/webp", "image/gif",
               "image/svg+xml", "image/avif"):
        assert btc_ordinals.is_image_inscription(ct) is True, ct


def test_unknown_or_empty_content_type_is_rejected():
    """When we could not read the type, do not assume it is an image: assuming so
    is exactly what flooded the feed."""
    assert btc_ordinals.is_image_inscription("") is False
    assert btc_ordinals.is_image_inscription(None) is False
    assert btc_ordinals.is_image_inscription("unknown") is False


def test_video_and_html_inscriptions_are_rejected():
    for ct in ("video/mp4", "text/html", "application/json", "audio/mpeg"):
        assert btc_ordinals.is_image_inscription(ct) is False


def test_content_type_matching_ignores_case_and_params():
    assert btc_ordinals.is_image_inscription("IMAGE/PNG") is True
    assert btc_ordinals.is_image_inscription("image/jpeg; charset=binary") is True


def test_non_image_inscriptions_never_reach_gemini(monkeypatch):
    """The filter must run before the Gemini call, or it saves no quota.

    This is the whole point: a 500/day Gemini budget was being burned on BRC-20
    token ops that cannot be meaningfully scored.
    """
    import asyncio

    feed = [
        {"id": "img001i0", "inscription_id": "img001i0", "number": 1,
         "content_type": "image/png", "tx_id": "img001",
         "image_url": "https://ordinals.com/content/img001i0"},
        {"id": "brc001i0", "inscription_id": "brc001i0", "number": 2,
         "content_type": "text/plain;charset=utf-8", "tx_id": "brc001",
         "image_url": "https://ordinals.com/content/brc001i0"},
        {"id": "txt001i0", "inscription_id": "txt001i0", "number": 3,
         "content_type": "text/plain", "tx_id": "txt001",
         "image_url": "https://ordinals.com/content/txt001i0"},
    ]

    scored = []

    async def fake_score(payload):
        scored.append(payload["contract"])
        return {"score": 90, "verdict": "LEGIT", "reasoning": "x"}

    async def fake_send(*a, **kw):
        return True

    monkeypatch.setattr(btc_ordinals, "fetch_recent_inscriptions",
                        lambda limit=15: feed)
    monkeypatch.setattr(btc_ordinals, "BTC_ORDINALS_ENABLED", True)
    monkeypatch.setattr(btc_ordinals, "gemini_score_nft", fake_score)
    monkeypatch.setattr(btc_ordinals, "asend", fake_send)
    monkeypatch.setattr(btc_ordinals, "asend_photo", fake_send)
    monkeypatch.setattr(btc_ordinals, "is_worth_alerting", lambda r, t: True)
    # Isolate dedup so the assertion is about filtering, not prior state.
    btc_ordinals.alerted_ordinals_set.clear()
    btc_ordinals.alerted_ordinals_deque.clear()
    monkeypatch.setattr(btc_ordinals.checkpoint, "was_seen", lambda *a: False)
    monkeypatch.setattr(btc_ordinals.checkpoint, "mark_seen", lambda *a: None)
    monkeypatch.setattr(btc_ordinals.checkpoint, "flush", lambda *a, **k: None)

    asyncio.run(btc_ordinals.check_btc_ordinals())

    assert scored == ["img001i0"], (
        f"only the image inscription may reach Gemini, got {scored}"
    )
