from pathlib import Path

from PIL import Image

from image_compressor import compress_image


def test_image_compression_reduces_size(tmp_path: Path):
    src = tmp_path / "test.jpg"
    with Image.new("RGB", (1800, 1200), (120, 140, 180)) as im:
        im.save(src, format="JPEG", quality=98)
    out = compress_image(src, tmp_path / "compressed")
    assert out.exists()
    assert out.suffix == ".webp"
    assert out.stat().st_size < src.stat().st_size


def test_transparency_is_preserved(tmp_path: Path):
    src = tmp_path / "transparent.png"
    with Image.new("RGBA", (100, 100), (255, 0, 0, 100)) as im:
        im.save(src, format="PNG")
    out = compress_image(src, tmp_path / "compressed")
    with Image.open(out) as im:
        assert "A" in im.getbands()
