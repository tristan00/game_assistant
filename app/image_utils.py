import io
from pathlib import Path

from PIL import Image


def downscale_to_jpeg(png_path: Path, max_edge: int = 1568, quality: int = 85) -> bytes:
    with Image.open(png_path) as image:
        image = image.convert("RGB")
        image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=quality, optimize=True)
        return buffer.getvalue()
