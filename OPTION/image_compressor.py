from __future__ import annotations

from pathlib import Path

from config import IMAGE_QUALITY
from PIL import Image, ImageOps

SUPPORTED = {"jpg", "jpeg", "png", "webp", "bmp", "tiff"}


def compress_image(src: Path, dest_dir: Path) -> Path:
    """Re-encode an image as JPEG with high visual quality and lower file size.

    Output is always .jpg. Dimensions are preserved. EXIF orientation is applied so
    the image is not rotated incorrectly. Alpha is composited onto white because
    JPEG does not support transparency.
    """
    suffix = src.suffix.lower().lstrip(".")
    if suffix not in SUPPORTED:
        return src

    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / f"{src.stem}_compressed.jpg"

    quality = max(1, min(100, int(IMAGE_QUALITY)))

    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im)

        if "A" in im.getbands():
            rgba = im.convert("RGBA")
            background = Image.new("RGB", rgba.size, (255, 255, 255))
            background.paste(rgba, mask=rgba.getchannel("A"))
            save_im = background
        else:
            save_im = im.convert("RGB")

        save_im.save(
            out,
            format="JPEG",
            quality=quality,
            optimize=True,
            progressive=True,
            subsampling="4:2:0",
        )

    return out
