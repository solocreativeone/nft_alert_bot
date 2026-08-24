"""Regression tests for Ordinals alerts failing every photo send.

Observed in the first live smoke run on 2026-08-22: 13 of 13 Ordinal alerts
printed

    [Bitcoin] Photo send failed: Image_process_failed - falling back to text

Image_process_failed is Telegram's error, not ours. Root cause is in the
ordinals.com scraper, which fabricates metadata rather than reading it:

    "number": 0                 -> every alert titled "Ordinal #0"
    "content_type": "image/*"   -> a wildcard, never a real MIME type

"image/*" then flows into notifier._finalize_image as the content_type. It
contains no recognisable subtype, so _guess_extension falls through to magic-byte
sniffing, and when the inscription is not a bitmap at all (text/plain, HTML, SVG
fragments, video) the bytes get uploaded as image.png. Telegram tries to decode a
PNG, fails, and returns Image_process_failed.

The fix is not to send harder. It is to stop claiming a content type we never
read, and to refuse to build a photo out of bytes that are not a supported image.

Every alert in that run also carried a wrong title, since inscription numbers were
hardcoded to 0 instead of parsed.
"""
import notifier


# ── _finalize_image must not trust a wildcard content type ────────────────────

def test_wildcard_content_type_does_not_mint_a_png_extension():
    """'image/*' carries no subtype. Non-image bytes must not become image.png."""
    out = notifier._finalize_image(b"just some plain text, not an image",
                                   content_type="image/*")
    assert out is None, "unrecognisable bytes must not be offered to Telegram"


def test_html_error_page_is_rejected():
    """Gateways return HTML error pages with a 200. Those are not photos."""
    html = b"<!DOCTYPE html><html><body>429 Too Many Requests</body></html>"
    assert notifier._finalize_image(html, content_type="image/*") is None


def test_json_body_is_rejected():
    assert notifier._finalize_image(b'{"error":"not found"}',
                                    content_type="image/*") is None


def test_plain_text_inscription_is_rejected():
    """Many inscriptions are text, not images. Sending them as a photo fails."""
    assert notifier._finalize_image(b"Hello world, this is an inscription",
                                    content_type="text/plain") is None


def test_real_png_still_passes_with_wildcard_type():
    """A genuine PNG must survive even when the declared type is useless."""
    png = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    out = notifier._finalize_image(png, content_type="image/*")
    assert out is not None
    assert out.name.endswith(".png")


def test_real_jpeg_is_detected_by_magic_bytes():
    jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 64
    out = notifier._finalize_image(jpeg, content_type="image/*")
    assert out is not None
    assert out.name.endswith(".jpg")


def test_real_gif_is_detected():
    gif = b"GIF89a" + b"\x00" * 64
    out = notifier._finalize_image(gif, content_type="")
    assert out is not None
    assert out.name.endswith(".gif")


def test_real_webp_is_detected():
    webp = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 64
    out = notifier._finalize_image(webp, content_type="")
    assert out is not None
    assert out.name.endswith(".webp")


def test_svg_is_still_rasterized_not_rejected():
    """SVG is not a bitmap but IS renderable. It must be converted, not dropped."""
    svg = (b'<svg xmlns="http://www.w3.org/2000/svg" width="4" height="4">'
           b'<rect width="4" height="4" fill="blue"/></svg>')
    out = notifier._finalize_image(svg, content_type="image/svg+xml")
    assert out is not None, "SVG must be rasterized to PNG, not rejected"
    assert out.name.endswith(".png")
    head = out.read(8)
    assert head == b"\x89PNG\r\n\x1a\n"


def test_video_inscription_is_rejected():
    """Ordinals include mp4/webm. sendPhoto cannot take those."""
    mp4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
    assert notifier._finalize_image(mp4, content_type="video/mp4") is None


def test_empty_body_is_rejected():
    assert notifier._finalize_image(b"", content_type="image/png") is None


# ── the scraper must read metadata, not invent it ─────────────────────────────

def test_scraper_parses_real_inscription_numbers(monkeypatch):
    """Every alert in the smoke run said "Ordinal #0" because number was hardcoded."""
    import btc_ordinals

    html = """
    <html><body>
      <a href="/inscription/aaaa1111i0">Inscription 91234567</a>
      <a href="/inscription/bbbb2222i0">Inscription 91234568</a>
    </body></html>
    """

    class Res:
        status_code = 200
        text = html

    monkeypatch.setattr(btc_ordinals.requests, "get", lambda *a, **k: Res())
    out = btc_ordinals.fetch_recent_inscriptions(limit=2)
    assert len(out) == 2
    numbers = [item["number"] for item in out]
    assert numbers != [0, 0], "inscription numbers must be parsed, not hardcoded to 0"
    assert numbers == [91234567, 91234568]


def test_scraper_does_not_claim_a_wildcard_content_type(monkeypatch):
    """The scraper never reads the content type, so it must not assert one."""
    import btc_ordinals

    class Res:
        status_code = 200
        text = '<a href="/inscription/cccc3333i0">Inscription 5</a>'

    monkeypatch.setattr(btc_ordinals.requests, "get", lambda *a, **k: Res())
    out = btc_ordinals.fetch_recent_inscriptions(limit=1)
    assert out, "expected one inscription"
    assert out[0]["content_type"] != "image/*", (
        "'image/*' is not a MIME type; it defeats extension detection downstream"
    )
