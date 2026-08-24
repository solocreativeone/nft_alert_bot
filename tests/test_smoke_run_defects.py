"""Regression tests from the second live smoke run (2026-08-22).

The RPC ordering fix held (all 9 chains reported "verified"), but the run exposed
four further defects:

1. floor.py passed the result of download_image_bytes straight to asend_photo
   without checking it for None, so a rejected image became
   "There is no photo in the request" instead of a clean text fallback. mint.py
   guards correctly; floor.py was the odd one out.

2. AVIF images were refused. i2c.seadn.io now serves OpenSea collection art as
   AVIF, which is a real bitmap Telegram accepts, but _sniff_image_kind had no
   magic-byte rule for it so every BAYC floor alert lost its image.

3. Ordinal titles still read "#0". The scraper parsed the number out of the
   listing link text, but those anchors are image tiles with empty text. The
   number only exists on the inscription detail page.

4. The listing HTML carries no content type either, so every Ordinal alert
   rendered "Type: " with nothing after it. That is also on the detail page.

Defects 3 and 4 share a cause: the listing page has the ids and nothing else.
"""
import notifier


# ── AVIF support (defect 2) ───────────────────────────────────────────────────

def test_avif_is_recognised_as_an_image():
    """i2c.seadn.io serves OpenSea collection art as AVIF. Telegram accepts it.

    Observed live:
      [Image] Not a supported image; refusing to send as photo
      (declared: image/avif, 6928 bytes, starts b'\\x00\\x00\\x00\\x1cftypavif...')
    """
    avif = b"\x00\x00\x00\x1cftypavif\x00\x00\x00\x00" + b"\x00" * 64
    assert notifier._sniff_image_kind(avif) == "avif"
    out = notifier._finalize_image(avif, content_type="image/avif")
    assert out is not None, "AVIF is a real image format and must not be refused"
    assert out.name.endswith(".avif")


def test_avif_variant_brands_are_recognised():
    """AVIF files carry several brand codes in the ftyp box."""
    for brand in (b"avif", b"avis"):
        data = b"\x00\x00\x00\x1cftyp" + brand + b"\x00" * 64
        assert notifier._sniff_image_kind(data) == "avif", f"brand {brand!r} missed"


def test_mp4_is_still_rejected_despite_sharing_the_ftyp_box():
    """mp4 and AVIF both start with an ftyp box. Only the brand distinguishes
    them, so a loose ftyp check would start sending videos as photos again."""
    mp4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
    assert notifier._sniff_image_kind(mp4) is None
    assert notifier._finalize_image(mp4, content_type="video/mp4") is None


def test_heic_is_not_mistaken_for_avif():
    """HEIC shares the ftyp container but Telegram does not accept it."""
    heic = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 64
    assert notifier._sniff_image_kind(heic) is None


def test_truncated_ftyp_header_does_not_crash():
    assert notifier._sniff_image_kind(b"\x00\x00\x00\x1cftyp") is None
    assert notifier._sniff_image_kind(b"\x00\x00") is None


# ── callers must never hand None to asend_photo (defect 1) ────────────────────

def test_floor_alert_falls_back_to_text_when_image_is_rejected(monkeypatch):
    """Live symptom: "[Floor] Photo send failed: There is no photo in the request".

    download_image_bytes returned None (correctly, the AVIF was refused) and
    floor.py passed that None straight into asend_photo.
    """
    import asyncio
    import floor

    calls = {"photo": 0, "text": 0}

    async def fake_photo(photo, **kw):
        calls["photo"] += 1
        assert photo is not None, "asend_photo must never receive None"

    async def fake_text(text, **kw):
        calls["text"] += 1

    async def fake_download(url):
        return None          # image was refused

    monkeypatch.setattr(floor, "asend_photo", fake_photo)
    monkeypatch.setattr(floor, "asend", fake_text)
    monkeypatch.setattr(floor, "download_image_bytes", fake_download)

    col = {"name": "Bored Ape Yacht Club", "slug": "boredapeyachtclub",
           "chain": "Ethereum", "floor_alert_high": 10.0, "floor_alert_low": 5.0}
    asyncio.run(floor.send_floor_alert(col, 8.229, "up", "https://example.com/art.avif"))

    assert calls["photo"] == 0, "must not attempt a photo send with no photo"
    assert calls["text"] == 1, "must fall back to a text alert"


def test_floor_alert_sends_photo_when_image_is_good(monkeypatch):
    import asyncio
    import io
    import floor

    calls = {"photo": 0, "text": 0}

    async def fake_photo(photo, **kw):
        calls["photo"] += 1

    async def fake_text(text, **kw):
        calls["text"] += 1

    async def fake_download(url):
        bio = io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
        bio.name = "image.png"
        return bio

    monkeypatch.setattr(floor, "asend_photo", fake_photo)
    monkeypatch.setattr(floor, "asend", fake_text)
    monkeypatch.setattr(floor, "download_image_bytes", fake_download)

    col = {"name": "Bored Ape Yacht Club", "slug": "boredapeyachtclub",
           "chain": "Ethereum", "floor_alert_high": 10.0, "floor_alert_low": 5.0}
    asyncio.run(floor.send_floor_alert(col, 8.229, "up", "https://example.com/art.png"))

    assert calls["photo"] == 1
    assert calls["text"] == 0


# ── scraper must read the detail page (defects 3 and 4) ───────────────────────

_LISTING = """
<html><body>
  <a href="/inscription/aaaa1111i0"><img src="/content/aaaa1111i0"></a>
  <a href="/inscription/bbbb2222i0"><img src="/content/bbbb2222i0"></a>
</body></html>
"""

_DETAIL = """
<html><body>
  <h1>Inscription 127212232</h1>
  <dl>
    <dt>id</dt><dd class=monospace>aaaa1111i0</dd>
    <dt>content type</dt><dd>text/plain;charset=utf-8</dd>
  </dl>
</body></html>
"""


def test_listing_anchors_have_no_text_so_number_must_come_from_detail(monkeypatch):
    """The live listing renders image tiles: every anchor's text is empty.

    Parsing the number from link text yielded 0 for all of them, which is why
    every alert was still titled "Ordinal #0" after the first fix.
    """
    import btc_ordinals

    def fake_get(url, **kw):
        class Res:
            status_code = 200
            text = _DETAIL if "/inscription/" in url else _LISTING
        return Res()

    monkeypatch.setattr(btc_ordinals.requests, "get", fake_get)
    out = btc_ordinals.fetch_recent_inscriptions(limit=1)
    assert out, "expected an inscription"
    assert out[0]["number"] == 127212232, "number must be read from the detail page"


def test_content_type_is_read_from_the_detail_page(monkeypatch):
    """Alerts rendered "Type: " with nothing after it because the listing has none."""
    import btc_ordinals

    def fake_get(url, **kw):
        class Res:
            status_code = 200
            text = _DETAIL if "/inscription/" in url else _LISTING
        return Res()

    monkeypatch.setattr(btc_ordinals.requests, "get", fake_get)
    out = btc_ordinals.fetch_recent_inscriptions(limit=1)
    assert out[0]["content_type"] == "text/plain;charset=utf-8"


def test_detail_fetch_failure_degrades_without_crashing(monkeypatch):
    """A failed detail fetch must still yield the inscription, just less complete."""
    import btc_ordinals

    def fake_get(url, **kw):
        if "/inscription/" in url:
            raise RuntimeError("detail page unavailable")

        class Res:
            status_code = 200
            text = _LISTING
        return Res()

    monkeypatch.setattr(btc_ordinals.requests, "get", fake_get)
    out = btc_ordinals.fetch_recent_inscriptions(limit=1)
    assert out, "a detail failure must not lose the inscription entirely"
    assert out[0]["id"] == "aaaa1111i0"


def test_detail_lookups_are_bounded(monkeypatch):
    """One HTTP call per inscription is acceptable; unbounded fan-out is not."""
    import btc_ordinals

    fetched = []

    def fake_get(url, **kw):
        fetched.append(url)

        class Res:
            status_code = 200
            text = _DETAIL if "/inscription/" in url else _LISTING
        return Res()

    monkeypatch.setattr(btc_ordinals.requests, "get", fake_get)
    btc_ordinals.fetch_recent_inscriptions(limit=2)
    detail_calls = [u for u in fetched if "/inscription/" in u]
    assert len(detail_calls) <= 2, "at most one detail fetch per inscription"


def test_non_image_inscription_keeps_its_real_content_type(monkeypatch):
    """brc-20 JSON inscriptions are text. Reporting that honestly lets the alert
    say so instead of attempting a doomed photo upload."""
    import btc_ordinals

    def fake_get(url, **kw):
        class Res:
            status_code = 200
            text = _DETAIL if "/inscription/" in url else _LISTING
        return Res()

    monkeypatch.setattr(btc_ordinals.requests, "get", fake_get)
    out = btc_ordinals.fetch_recent_inscriptions(limit=1)
    assert "text/plain" in out[0]["content_type"]
