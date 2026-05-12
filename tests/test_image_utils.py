import io

import pytest
from PIL import Image

from app.image_utils import downscale_to_jpeg


def _jpeg_size(data: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(data)) as img:
        assert img.format == "JPEG"
        return img.size


def test_returns_jpeg_bytes(make_png):
    path = make_png(size=(800, 600))
    data = downscale_to_jpeg(path)
    assert isinstance(data, bytes)
    assert len(data) > 0
    assert _jpeg_size(data) == (800, 600)  # smaller than max_edge, untouched


def test_downscales_when_larger_than_max(make_png):
    path = make_png(size=(3200, 1600))
    data = downscale_to_jpeg(path, max_edge=1568)
    w, h = _jpeg_size(data)
    assert max(w, h) <= 1568
    # Aspect ratio preserved (within 1px rounding).
    assert abs((w / h) - (3200 / 1600)) < 0.01


def test_portrait_orientation_downscale(make_png):
    path = make_png(size=(800, 2400))
    data = downscale_to_jpeg(path, max_edge=1568)
    w, h = _jpeg_size(data)
    assert max(w, h) <= 1568
    assert h > w  # still portrait


def test_rgba_png_is_converted_to_rgb(make_png):
    path = make_png(size=(100, 100), mode="RGBA", color=(255, 0, 0, 128))
    data = downscale_to_jpeg(path)
    with Image.open(io.BytesIO(data)) as img:
        assert img.mode == "RGB"


def test_quality_lower_produces_smaller_bytes(make_png):
    path = make_png(size=(500, 500))
    high = downscale_to_jpeg(path, quality=95)
    low = downscale_to_jpeg(path, quality=20)
    assert len(low) < len(high)


def test_custom_max_edge(make_png):
    path = make_png(size=(2000, 1000))
    data = downscale_to_jpeg(path, max_edge=400)
    w, h = _jpeg_size(data)
    assert max(w, h) <= 400
