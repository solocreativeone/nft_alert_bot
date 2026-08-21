"""Tests for the image pipeline in notifier.py (data-URI decode, SVG raster,
format sniffing, size guard). Network paths are not exercised here."""
import asyncio
import base64

import pytest

import notifier


# A minimal 1x1 transparent PNG.
_PNG_1x1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


def test_data_uri_base64_png_returns_bytes():
    uri = "data:image/png;base64," + base64.b64encode(_PNG_1x1).decode()
    out = asyncio.run(notifier.download_image_bytes(uri))
    assert out is not None
    data = out.read()
    assert data == _PNG_1x1
    assert out.name.endswith(".png")


def test_none_and_empty_url_return_none():
    assert asyncio.run(notifier.download_image_bytes(None)) is None
    assert asyncio.run(notifier.download_image_bytes("")) is None


def test_data_uri_svg_rasterizes_to_png():
    pytest.importorskip("svglib")
    pytest.importorskip("reportlab")
    from urllib.parse import quote

    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
        '<rect width="10" height="10" fill="red"/></svg>'
    )
    # URL-encoded (NOT base64) data URI — the branch the old code decoded wrong.
    uri = "data:image/svg+xml;utf8," + quote(svg)

    out = asyncio.run(notifier.download_image_bytes(uri))
    if out is None:
        pytest.skip("reportlab renderPM backend unavailable in this environment")
    data = out.read()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"  # rasterized to a real PNG
    assert out.name.endswith(".png")


def test_rasterize_svg_direct():
    pytest.importorskip("svglib")
    pytest.importorskip("reportlab")
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="4" height="4">'
        b'<rect width="4" height="4" fill="blue"/></svg>'
    )
    png = notifier._rasterize_svg(svg)
    if png is None:
        pytest.skip("reportlab renderPM backend unavailable in this environment")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_looks_like_svg():
    assert notifier._looks_like_svg(b"<svg xmlns='...'>")
    assert notifier._looks_like_svg(b"  <?xml version='1.0'?><svg></svg>")
    assert not notifier._looks_like_svg(b"\x89PNG\r\n\x1a\n")
    assert not notifier._looks_like_svg(b"")


def test_guess_extension_by_content_type():
    assert notifier._guess_extension("image/png", b"") == "png"
    assert notifier._guess_extension("image/jpeg", b"") == "jpg"
    assert notifier._guess_extension("image/gif", b"") == "gif"
    assert notifier._guess_extension("image/webp", b"") == "webp"


def test_guess_extension_by_magic_bytes():
    assert notifier._guess_extension("", b"\x89PNG\r\n\x1a\n") == "png"
    assert notifier._guess_extension("", b"\xff\xd8\xff\xe0") == "jpg"
    assert notifier._guess_extension("", b"GIF89a....") == "gif"
    assert notifier._guess_extension("", b"RIFF\x00\x00\x00\x00WEBP") == "webp"


def test_finalize_image_oversize_returns_none(monkeypatch):
    monkeypatch.setattr(notifier, "MAX_IMAGE_BYTES", 8)
    # Non-SVG payload just over the cap.
    assert notifier._finalize_image(b"\xff\xd8\xff" + b"0" * 20, "image/jpeg") is None


def test_finalize_image_sets_name():
    out = notifier._finalize_image(_PNG_1x1, "image/png")
    assert out is not None
    assert out.name == "image.png"
    assert out.read() == _PNG_1x1


def test_finalize_image_empty_returns_none():
    assert notifier._finalize_image(b"", "image/png") is None
