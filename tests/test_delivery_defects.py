"""Regression tests from the third live smoke run (2026-08-22).

User report: "All Bitcoin Ordinal link to magicEden return 404, Alert comes in
slow some shows in the terminal but never reaches TG."

Five defects, three of them introduced by earlier fixes in this series.

1. AVIF was made *worse*, not better. Telegram's sendPhoto accepts JPEG, PNG,
   GIF, WEBP, BMP and TIFF. AVIF is not on that list. Accepting AVIF bytes turned
   a clean "refuse and send text" into an upload Telegram rejects with
   Image_process_failed, which is exactly the symptom that returned:

       [Floor] Photo send failed: Image_process_failed - falling back to text

   The lesson: "is a real image" and "is a format Telegram accepts" are different
   questions, and only the second one matters at the send boundary.

2. "Alerted" was logged even when the send raised. drops.py guards the *photo*
   send but leaves the text fallback bare, so a Telegram failure propagates to the
   per-chain handler while the operator has no idea the alert never landed. That
   is the "shows in the terminal but never reaches TG" report.

3. Magic Eden Ordinals links 404. The item-details path expects an inscription
   NUMBER, not the inscription id, so every button was dead.

4. DexScreener is unreachable from this network at any timeout (5s, 15s and 30s
   all time out), so a 5s wait per contract is pure latency on every drop. That is
   the "alerts come in slow" half of the report.

5. A refusing endpoint reclaimed first place on base, printing
   "preferring alchemy (unverified)". The tri-state ordering is correct, but
   nothing forces a *known-refusing* endpoint below an unknown one when the whole
   chain went unprobed under a budget cutoff.
"""
import notifier


# ── Telegram-supported formats only (defect 1) ────────────────────────────────

TELEGRAM_PHOTO_FORMATS = {"jpg", "png", "gif", "webp"}


def test_avif_is_refused_because_telegram_cannot_render_it():
    """Telegram sendPhoto supports JPEG/PNG/GIF/WEBP/BMP/TIFF. AVIF is not one.

    Uploading it produces Image_process_failed, which is strictly worse than
    refusing it: refusing yields a clean text alert, uploading yields a failed
    send plus a fallback.
    """
    avif = b"\x00\x00\x00\x1cftypavif\x00\x00\x00\x00" + b"\x00" * 64
    assert notifier._finalize_image(avif, content_type="image/avif") is None


def test_every_accepted_format_is_one_telegram_renders():
    """Whatever _finalize_image accepts must be sendable, without exception."""
    samples = {
        "png": b"\x89PNG\r\n\x1a\n" + b"\x00" * 64,
        "jpg": b"\xff\xd8\xff\xe0" + b"\x00" * 64,
        "gif": b"GIF89a" + b"\x00" * 64,
        "webp": b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 64,
    }
    for expected, data in samples.items():
        out = notifier._finalize_image(data, content_type="")
        assert out is not None, f"{expected} must be accepted"
        ext = out.name.rsplit(".", 1)[-1]
        assert ext in TELEGRAM_PHOTO_FORMATS, f"{ext} is not a Telegram photo format"


def test_avif_is_still_identified_even_though_it_is_refused():
    """Detection and acceptance are separate concerns. Keeping detection means the
    log can say what the bytes actually were instead of "unknown"."""
    avif = b"\x00\x00\x00\x1cftypavif\x00\x00\x00\x00" + b"\x00" * 64
    assert notifier._sniff_image_kind(avif) == "avif"
    assert "avif" not in TELEGRAM_PHOTO_FORMATS


def test_mp4_and_heic_remain_refused():
    for data in (b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64,
                 b"\x00\x00\x00\x18ftypheic" + b"\x00" * 64):
        assert notifier._finalize_image(data, content_type="") is None


# ── never claim an alert was delivered when it was not (defect 2) ─────────────

def test_alert_is_not_logged_as_sent_when_telegram_rejects(capsys):
    """The "Alerted" line must follow a confirmed send, not merely an attempt.

    Live symptom: alerts printed in the terminal that never arrived in Telegram.
    """
    import asyncio
    import drops

    async def boom(*a, **kw):
        raise RuntimeError("Bad Request: message caption is too long")

    async def go():
        return await drops.deliver_alert(text="hello", reply_markup=None,
                                         image_url=None, send_text=boom)

    delivered = asyncio.run(go())
    assert delivered is False, "a failed send must report failure"
    out = capsys.readouterr().out
    assert "Alerted" not in out


def test_alert_reports_success_when_the_send_succeeds():
    import asyncio
    import drops

    calls = []

    async def ok(text, **kw):
        calls.append(text)

    async def go():
        return await drops.deliver_alert(text="hello", reply_markup=None,
                                         image_url=None, send_text=ok)

    assert asyncio.run(go()) is True
    assert calls == ["hello"]


def test_send_failure_is_logged_with_the_reason(capsys):
    import asyncio
    import drops

    async def boom(*a, **kw):
        raise RuntimeError("Forbidden: bot was blocked by the user")

    async def go():
        await drops.deliver_alert(text="x", reply_markup=None,
                                  image_url=None, send_text=boom)

    asyncio.run(go())
    out = capsys.readouterr().out
    assert "blocked by the user" in out, "the operator needs the real reason"


# ── Magic Eden Ordinals links (defect 3) ─────────────────────────────────────

def test_marketplace_link_avoids_the_dead_magiceden_path():
    """Every Ordinal alert's Magic Eden button 404'd.

    The correct Magic Eden URL shape could not be established: their WAF returns
    403/404 inconsistently for identical formats, so probing proves nothing. Until
    the real shape is confirmed in a browser, link somewhere verified working
    rather than ship a second guess.
    """
    import btc_ordinals

    url = btc_ordinals.magiceden_url(
        inscription_id="a2f0c1e329bc17dd848c0331abcdefi0", number=127212349)
    assert "magiceden" not in url, "do not link to an unverified URL shape"
    assert "ordinals.com/inscription/" in url
    assert "a2f0c1e329bc17dd848c0331abcdefi0" in url


def test_marketplace_link_works_without_a_number():
    import btc_ordinals

    url = btc_ordinals.magiceden_url(
        inscription_id="a2f0c1e329bc17dd848c0331abcdefi0", number=0)
    assert "ordinals.com" in url
    assert "a2f0c1e329bc17dd848c0331abcdefi0" in url


# ── unreachable third-party APIs must not tax every drop (defect 4) ──────────

def test_dexscreener_circuit_breaker_opens_after_repeated_timeouts(monkeypatch):
    """DexScreener is unreachable from some networks at any timeout (5s, 15s and
    30s all fail). Paying that wait on every contract is the "alerts are slow"
    complaint. After N consecutive timeouts, stop calling it for a while.
    """
    import dex_liquidity

    dex_liquidity.reset_breaker()
    calls = {"n": 0}

    def always_timeout(*a, **kw):
        calls["n"] += 1
        raise dex_liquidity.requests.exceptions.ReadTimeout("timed out")

    monkeypatch.setattr(dex_liquidity.requests, "get", always_timeout)

    for _ in range(10):
        dex_liquidity.fetch_dex_data("0xabc")

    assert calls["n"] <= dex_liquidity.BREAKER_THRESHOLD, (
        f"breaker must stop calling after {dex_liquidity.BREAKER_THRESHOLD} "
        f"consecutive failures, made {calls['n']} calls"
    )


def test_dexscreener_breaker_resets_on_success(monkeypatch):
    import dex_liquidity

    dex_liquidity.reset_breaker()
    state = {"fail": True}

    class Res:
        status_code = 200
        @staticmethod
        def json():
            return {"pairs": []}

    def flaky(*a, **kw):
        if state["fail"]:
            raise dex_liquidity.requests.exceptions.ReadTimeout("timed out")
        return Res()

    monkeypatch.setattr(dex_liquidity.requests, "get", flaky)
    dex_liquidity.fetch_dex_data("0xabc")
    state["fail"] = False
    dex_liquidity.reset_breaker()
    assert dex_liquidity.fetch_dex_data("0xabc") == {"pairs": []}


def test_missing_dex_data_never_blocks_an_alert(monkeypatch):
    """Liquidity is decoration. Its absence must not change the verdict."""
    import dex_liquidity

    dex_liquidity.reset_breaker()
    monkeypatch.setattr(dex_liquidity, "fetch_dex_data", lambda *a, **kw: {})
    res = dex_liquidity.parse_dex_data({})
    assert res["has_liquidity"] is False
    assert res["formatted_line"] == ""


# ── a refusing endpoint must never reclaim first place (defect 5) ────────────

def test_refusing_endpoint_stays_last_even_when_chain_is_unprobed(monkeypatch):
    """Live regression: "[Drops] RPC base: preferring alchemy (unverified)".

    A refusing endpoint (one that answered 429/403) must sink below endpoints we
    never got a verdict for, including when the budget cuts the sweep short. A
    refusal is a fact; an unprobed endpoint is only a maybe, and a maybe outranks
    a known no.
    """
    import drops

    refusing = "https://over-quota.example"
    monkeypatch.setitem(
        drops.EVM_CHAINS, "testchain",
        {"rpcs": [refusing, "https://public-a.example", "https://public-b.example"],
         "explorer": "https://x", "opensea_chain": None, "block_step": 60},
    )

    # The refusing endpoint answers fast with a verdict; the others never answer.
    def probe(url, **kw):
        return False if url == refusing else None

    drops.wire_healthy_rpcs(chains=["testchain"], probe=probe, budget=0)
    rpcs = drops.EVM_CHAINS["testchain"]["rpcs"]
    assert rpcs[0] != refusing
    assert rpcs[-1] == refusing
