#!/usr/bin/env python3
"""ساخت `tisa-product-importer.zip` از روی `plugin/` — بایت‌به‌بایت قابل تکرار.

فایل زیپِ افزونه، چیزی است که مالک سایت در وردپرس آپلود می‌کند؛ تا دیروز فقط همان
زیپ در رپو بود و کدِ PHP جای دیگری نوشته/منتقل می‌شد. یعنی ویرایشِ افزونه در رپو
نمایان نبود و هیچ‌کس نمی‌توانست بگوید این زیپ از کدام سورس ساخته شده. حالا سورس در
`plugin/tisa-product-importer/` است و زیپ، خروجیِ این اسکریپت:

    python scripts/build_plugin_zip.py          # ساخت
    python scripts/build_plugin_zip.py --check  # فقط بررسی هم‌زمانی (در CI)

ساخت «قطعی» است: نام‌ها مرتب می‌شوند، تاریخِ همهٔ رکوردها روی کمینهٔ فرمت قفل می‌شود
و مجوزها ثابت‌اند. پس دو بار ساختن از یک سورس، دو فایل هم‌بایت می‌دهد و اگر کسی PHP
را عوض کرد و زیپ را بازنساخت، `--check` (و تست `tests/test_release.py`) همان لحظه
می‌گوید. zlib می‌تواند بین دو بیلدِ پایتون خروجیِ deflate را کمی فرق بدهد، پس شرطِ
 CI «هم‌بایت بودن با یک بازساختِ هم‌تفسیر» + «CRC و نامِ رکوردها با زیپِ commit‌شده یکی
باشد» است، نه بایت‌های خامِ بین‌نسخه‌ای.
"""

from __future__ import annotations

import argparse
import io
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SOURCE_DIR = REPO / "plugin"
ROOT_NAME = "tisa-product-importer"
ZIP_PATH = REPO / "tisa-product-importer.zip"
#: کمینهٔ تاریخیِ قالب zip؛ هر بیلدی همین را می‌نویسد تا خروجی ثابت بماند.
_STAMP = (1980, 1, 1, 0, 0, 0)
_FILE_MODE = 0o644 << 16
_DIR_MODE = (0o755 << 16) | 0x10


def sources(root: Path | None = None) -> list[Path]:
    """همهٔ فایل‌های سورس، مرتب و با مسیرِ نسبیِ پایدار."""
    base = root or SOURCE_DIR
    return sorted(p for p in base.rglob("*") if p.is_file() and not p.name.startswith("."))


def build_bytes(root: Path | None = None) -> bytes:
    """زیپ را در حافظه می‌سازد (تا `--check` لازم نباشد فایل را بنویسد)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=False) as archive:
        directory = zipfile.ZipInfo(f"{ROOT_NAME}/", date_time=_STAMP)
        directory.external_attr = _DIR_MODE
        directory.compress_type = zipfile.ZIP_STORED
        archive.writestr(directory, b"")
        for path in sources(root):
            relative = path.relative_to(root or SOURCE_DIR).as_posix()
            info = zipfile.ZipInfo(relative, date_time=_STAMP)
            info.external_attr = _FILE_MODE
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
    return buffer.getvalue()


def fingerprints(raw: bytes) -> list[tuple[str, int, int]]:
    """(نام، اندازه، CRC) هر رکورد — همان چیزی که بین دو بیلد فرق نمی‌کند."""
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        return [(info.filename, info.file_size, info.CRC) for info in archive.infolist()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="build_plugin_zip.py", description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="فقط بگو زیپ با سورس هم‌خوان است یا نه")
    parser.add_argument("--out", default=str(ZIP_PATH), help="مسیر فایل خروجی")
    # --root برای تست‌هاست (یک سورسِ جعلی در temp) و برای کسی که می‌خواهد از یک
    # checkout دیگر بیلد بگیرد؛ پیش‌فرض همان plugin/ است.
    parser.add_argument("--root", default=str(SOURCE_DIR), help="پوشهٔ سورس (پیش‌فرض: plugin)")
    args = parser.parse_args(argv)

    root = Path(args.root)
    if not root.is_dir():
        print(f"❌ پوشهٔ سورس نیست: {root}", file=sys.stderr)
        return 2
    built = build_bytes(root)
    out = Path(args.out)
    if args.check:
        if not out.exists():
            print(f"❌ {out.name} وجود ندارد؛ با `python scripts/build_plugin_zip.py` بساز.")
            return 1
        current = out.read_bytes()
        if current == built:
            print(f"✅ {out.name} با سورسِ {root.name}/ هم‌خوان است.")
            return 0
        old, new = fingerprints(current), fingerprints(build_bytes(root))
        if sorted(old) == sorted(new):
            # Not a source drift: only the compression stream differs (another zlib).
            print(f"⚠️  محتوای {out.name} یکی است ولی بایت‌ها فرق دارند (تفاوتِ zlib، نه سورس).")
            return 0
        changed = [name for name, size, crc in old if (name, size, crc) not in new]
        added = [name for name, size, crc in new if (name, size, crc) not in old]
        print(
            f"❌ {out.name} قدیمی است.\n"
            f"   تغییر کرده / حذف شده: {', '.join(changed) or '—'}\n"
            f"   تازه: {', '.join(added) or '—'}\n"
            "   با `python scripts/build_plugin_zip.py` بازبساز و commit کن.",
            file=sys.stderr,
        )
        return 1
    out.write_bytes(built)
    print(f"📦 {out.name} ساخته شد ({len(sources())} فایل، {len(built)} بایت).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
