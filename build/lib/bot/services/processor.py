"""
processor.py — هسته‌ی پردازش فایل سفارش برای ربات تلگرام

ورودی: فایل .xlsx / .csv / .pdf  (جدول سفارش‌های تیساکیس / تیسا چاپ)
       ستون‌های جدول: ردیف | بارکد | تاریخ ثبت | نام گیرنده | کد سفارش | مقصد | نام فروشگاه | آدرس | وزن
       (فایل‌هایی با هدر انگلیسی order_id / tracking_code هم پشتیبانی می‌شوند)

خروجی: (csv_text, summary_text, problems_text, fix_text)
  - csv_text      : فایل tracking.csv با ستون‌های order_id,tracking_code
                    (فقط ردیف‌هایی که بارکدشان معتبر است)
  - summary_text  : خلاصه‌ی گزارش برای نمایش در چت
  - problems_text : گزارش کامل مشکلات (یا None اگر مشکلی نبود)
  - fix_text      : ردیف‌های نیازمند اصلاح به‌صورت CSV (یا None)
"""

import os
import re
from typing import Any

import pandas as pd

from bot.config import settings
from bot.services import barcodes

# ---------------------------------------------------------------------------
# نرمال‌سازی متن (ارقام فارسی/عربی → انگلیسی، حذف فاصله/نیم‌فاصله/کاما)
# ---------------------------------------------------------------------------
_FA2EN = {
    '۰': '0', '۱': '1', '۲': '2', '۳': '3', '۴': '4',
    '۵': '5', '۶': '6', '۷': '7', '۸': '8', '۹': '9',
    '٠': '0', '١': '1', '٢': '2', '٣': '3', '٤': '4',
    '٥': '5', '٦': '6', '٧': '7', '٨': '8', '٩': '9',
    'ي': 'ی', 'ك': 'ک', 'ة': 'ه', 'ۀ': 'ه',
}
# str.translate نیاز به جدول ordinal دارد — دیکشنری str→str بی‌اثر است
_FA2EN_TABLE = str.maketrans(_FA2EN)


def _norm(s) -> str:
    """نرمال‌سازی برای مقایسه‌ی نام ستون‌ها (هدرها)"""
    if s is None:
        return ''
    if isinstance(s, float) and s.is_integer():
        s = int(s)
    s = str(s).translate(_FA2EN_TABLE)
    s = re.sub(r'[\s\u00a0\u200c\u200f\u202a\u202b,]', '', s)
    return s.lower()


def _clean(v) -> str:
    """نرمال‌سازی مقدار یک سلول (بدون lower کردن — برای داده)"""
    if v is None:
        return ''
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, float) and pd.isna(v):
        return ''
    s = str(v)
    s = re.sub(r'[\s\u00a0\u200c\u200f\u202a\u202b,]', '', s)
    return s.translate(_FA2EN_TABLE)


# ---------------------------------------------------------------------------
# نام ستون‌ها (بعد از نرمال‌سازی — فاصله‌ها حذف شده‌اند)
# ---------------------------------------------------------------------------
_BARCODE_HEADERS = {'بارکد', 'باركد', 'barcode', 'trackingcode', 'tracking_code', 'tracking'}
_CODE_HEADERS = {'کدسفارش', 'کدسفارشگیرنده', 'orderid', 'order_id', 'order', 'کد'}
_ROW_HEADERS = {'ردیف', 'رديف', 'ردی', 'شماره', 'no'}
# ستون نام گیرنده — وقتی ستون «کد سفارش» جدا وجود ندارد، کد داخل همین ستون است
# (مثل «امیرحسین عاشوری ۳۰۶۱۷۶»)
_NAME_HEADERS = {'نامگ', 'نامگیرنده', 'گیرنده', 'نامونامخانوادگیگیرنده', 'recipient'}

_RE_BC = re.compile(r'^\d{24}$')      # بارکد: دقیقاً ۲۴ رقم
_RE_CODE = re.compile(r'^\d{6}$')     # کد سفارش: دقیقاً ۶ رقم
_RE_CODE5 = re.compile(r'^\d{5}$')    # کد ۵ رقمی (هشدار)
_RE_DATE = re.compile(r'14\d{2}/\d{2}/\d{2}')
# کد ۵-۶ رقمی داخل متن (مثلاً چسبیده به نام گیرنده)
_RE_CODE_IN_TEXT = re.compile(r'(?<!\d)(\d{5,6})(?!\d)')


# ---------------------------------------------------------------------------
# خواندن فایل
# ---------------------------------------------------------------------------
def _read_excel_or_csv(path, ext):
    if ext == '.xlsx':
        return pd.read_excel(path, header=None, dtype=object)
    last_err = None
    for enc in ('utf-8-sig', 'utf-8', 'cp1256'):
        try:
            return pd.read_csv(path, header=None, dtype=object,
                               keep_default_na=False, encoding=enc)
        except (UnicodeDecodeError, pd.errors.ParserError) as e:
            last_err = e
    raise ValueError(f'فایل CSV قابل خواندن نیست: {last_err}')


def _find_header(df):
    """پیدا کردن سطر هدر و ستون‌های بارکد / کد سفارش / ردیف / نام گیرنده"""
    for i in range(min(20, len(df))):
        cells = [_norm(c) for c in df.iloc[i].tolist()]
        bc_col = next((j for j, c in enumerate(cells) if c in _BARCODE_HEADERS), None)
        if bc_col is None:
            continue
        code_col = next((j for j, c in enumerate(cells) if c in _CODE_HEADERS), None)
        row_col = next((j for j, c in enumerate(cells) if c in _ROW_HEADERS), None)
        name_col = next((j for j, c in enumerate(cells) if c in _NAME_HEADERS), None)
        return i, bc_col, code_col, row_col, name_col
    return None, None, None, None, None


def _cell(df, row_index, col):
    """یک سلول با احتساب ستونِ ناموجود و مقدار NaN."""
    if col is None:
        return ''
    v = df.iat[row_index, col]
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ''
    return v


def _collect_rows(df, header_idx, bc_col, code_col, row_col, name_col) -> list[dict[str, Any]]:
    """سطرهای داده.

    سلول‌ها با ``_cell`` خوانده می‌شوند (نه یک closure داخل حلقه): نسخهٔ قبلی
    متغیر حلقه را دیر-بند می‌کرد که با هر تغییر کوچک، مقدار سطرِ اشتباه می‌دهد.
    """
    rows: list[dict[str, Any]] = []
    for r in range(header_idx + 1, len(df)):
        get = lambda col, _r=r: _cell(df, _r, col)  # noqa: E731

        b_raw, c_raw, rn_raw = get(bc_col), get(code_col), get(row_col)
        b, c = _clean(b_raw), _clean(c_raw)

        # ستون «کد سفارش» جدا نبود یا خالی بود → کد را از داخل نام گیرنده بردار
        # (مثل «امیرحسین عاشوری ۳۰۶۱۷۶»)
        if not c and name_col is not None:
            m = _RE_CODE_IN_TEXT.search(_clean(get(name_col)))
            if m:
                c = m.group(1)

        rn = _clean(rn_raw) or str(len(rows) + 1)
        has_digits_b = any(ch.isdigit() for ch in b)
        has_digits_c = any(ch.isdigit() for ch in c)
        if not has_digits_b and not has_digits_c:
            continue  # سطر خالی یا سطر «جمع کل» — رد می‌شود
        numeric_b = isinstance(b_raw, (int, float)) and not isinstance(b_raw, bool)
        rows.append({'rownum': rn, 'barcode': b, 'code': c,
                     'barcode_numeric': numeric_b, 'barcode_raw': b_raw,
                     'sheet_row': r + 1})
    return rows


def _read_pdf(path) -> list[dict[str, Any]]:
    """استخراج جدول از PDF خروجی سامانه (چیدمان ثابت ستون‌ها)"""
    import pymupdf
    doc = pymupdf.open(path)
    rows: list[dict[str, Any]] = []
    for pno in range(len(doc)):
        page = doc[pno]
        d = page.get_text('rawdict')
        spans_by_y: dict[float, list[tuple[float, float, str]]] = {}
        for block in d.get('blocks', []):
            for line in block.get('lines', []):
                for span in line.get('spans', []):
                    x0, y0, x1 = span['bbox'][0], span['bbox'][1], span['bbox'][2]
                    text = ''.join(ch['c'] for ch in span.get('chars', [])).strip()
                    if not text:
                        continue
                    key = round(y0, 1)
                    spans_by_y.setdefault(key, []).append((x0, x1, text))
        for _y, spans in spans_by_y.items():
            # یک سطر می‌تواند هم سلول متنی و هم فهرست سلول‌های میانی داشته باشد
            rec: dict[str, Any] = {}
            for x0, x1, t in spans:
                if x0 > 535 and t.isdigit() and len(t) <= 4:
                    rec['n'] = t
                elif 420 <= x0 <= 535 and len(t) >= 20 and t.isdigit():
                    rec['bc'] = t
                elif 235 <= x0 <= 420 and x1 <= 425 and not t.isdigit():
                    rec.setdefault('mid', []).append(t)
            if 'bc' in rec and 'n' in rec:
                mid = ''.join(rec.get('mid') or [])
                mid_clean = _RE_DATE.sub('', mid)          # حذف تاریخ
                m = re.search(r'(\d{5,6})', mid_clean)     # کد سفارش
                rows.append({'rownum': rec['n'], 'barcode': rec['bc'],
                             'code': m.group(1) if m else '',
                             'barcode_numeric': False, 'barcode_raw': rec['bc'],
                             'sheet_row': None})
    if len(rows) < 2:
        raise ValueError('ساختار PDF شناخته نشد (ستون‌های جدول پیدا نشد)')
    return rows


# ---------------------------------------------------------------------------
# بررسی مشکلات
# ---------------------------------------------------------------------------
def _barcode_state(row):
    """وضعیت بارکد یک سطر: ('ok'|'warn'|'error', شرح)

    هر سه حالت در `problems` ثبت می‌شود، اما فقط `ok` وارد tracking.csv می‌شود:
    اکسل اعداد ۲۴ رقمی را به float تبدیل و خراب می‌کند و آن عدد خراب در فایل
    خروجی کاملاً معقول به‌نظر می‌رسد — پس نوشتنش خطرناک‌تر از خالی‌گذاشتنش است.
    """
    b = row['barcode']
    if not b:
        return 'error', 'بارکد خالی'
    raw = row.get('barcode_raw')
    if barcodes.looks_float_destroyed(raw, b):
        return 'error', ('بارکد به‌صورت عدد ذخیره شده و دقتش از بین رفته — '
                         'از فایل متنی/PDF اصلی استفاده کن')
    if not barcodes.barcode_is_valid(b):
        allowed = '/'.join(str(x) for x in sorted(settings.barcode_lengths))
        return 'error', f'بارکد نامعتبر: {len(b)} رقم (باید {allowed} رقم باشد)'
    if barcodes.number_is_suspicious(raw, b):
        return 'warn', 'بارکد در اکسل عددی است؛ اگر ۱۵ رقم را رد کند دقتش از بین می‌رود'
    return 'ok', ''


def _order_code_state(row):
    c = row['code']
    if not c:
        return 'warn', 'کد سفارش خالی (باید خودت تکمیل کنی)'
    if barcodes.order_code_is_valid(c):
        return 'ok', ''
    if barcodes.order_code_is_short(c):
        return 'warn', 'کد سفارش ۵ رقمی (احتمالاً یک رقم جا افتاده)'
    return 'error', f'کد سفارش نامعتبر: «{c}» (باید ۶ رقم باشد)'


def _analyze(rows: list[dict[str, Any]]) -> list[tuple[object, str, str, str, str]]:
    """برمی‌گرداند: list of (ردیف نمایشی, شدت ❌/⚠️, شرح, بارکد, کد)

    هر سطر هم برچسب می‌گیرد (`row['barcode_state']`, `row['code_state']`) تا
    `build_csv` بداند کدام داده قابل ارسال به سامانهٔ رهگیری است.
    """
    problems = []
    bc_count: dict[str, list[str]] = {}
    code_count: dict[str, list[str]] = {}

    for row in rows:
        n = row['rownum']
        b, c = row['barcode'], row['code']

        bc_state, bc_message = _barcode_state(row)
        row['barcode_state'] = bc_state
        if bc_message:
            problems.append((n, '❌' if bc_state == 'error' else '⚠️', bc_message, b, c))
        if bc_state == 'ok':
            bc_count.setdefault(b, []).append(n)

        code_state, code_message = _order_code_state(row)
        row['code_state'] = code_state
        if code_message:
            problems.append((n, '❌' if code_state == 'error' else '⚠️', code_message, b, c))
        if code_state == 'ok':
            code_count.setdefault(c, []).append(n)

    # --- تکراری‌ها -----------------------------------------------------------
    # بارکد تکراری هشدار است نه خطا: یک بسته ممکن است دو سفارش داشته باشد و
    # کدش معتبر است. حذف هر دو سطر از CSV سفارش درست را از دست می‌داد.
    for b, ns in bc_count.items():
        if len(ns) > 1:
            problems.append((' و '.join(ns), '⚠️',
                             f'بارکد تکراری در {len(ns)} سطر (در CSV می‌ماند؛ بررسی کن)', b, ''))
    for c, ns in code_count.items():
        if len(ns) > 1:
            problems.append((' و '.join(ns), '⚠️', 'کد سفارش تکراری', '', c))

    return problems


# ---------------------------------------------------------------------------
# خروجی‌ها
# ---------------------------------------------------------------------------
def build_csv(rows) -> str:
    """فایل tracking.csv — فقط داده‌ای که معتبر است.

    سطرهای دارای خطا (بارکد خراب‌شده/ناصحیح) اینجا نمی‌آیند و به
    `needs-fix.csv` می‌روند؛ کد سفارش خالی همچنان با سلول خالی می‌ماند تا
    خود کاربر تکمیلش کند.
    """
    out = ['order_id,tracking_code']
    for row in rows:
        if row.get('barcode_state') != 'ok':
            continue
        code = row['code'] if row.get('code_state') in ('ok', 'warn') else ''
        out.append(f"{code},{row['barcode']}")
    return '\n'.join(out) + '\n'


def build_fix_csv(rows) -> str | None:
    """سطرهای نیازمند اصلاح، با دلیل — برای اینکه گم نشوند."""
    lines = ['row,order_id,barcode,reason']
    kept = 0
    for row in rows:
        reason_parts = []
        if row.get('barcode_state') == 'error':
            reason_parts.append('بارکد نامعتبر')
        elif row.get('barcode_state') == 'warn':
            reason_parts.append('بارکد هشداردار')
        if row.get('code_state') == 'error':
            reason_parts.append('کد سفارش نامعتبر')
        if not reason_parts:
            kept += 1
            continue
        lines.append(f"{row['rownum']},{row['code']},{row['barcode']},{' + '.join(reason_parts)}")
    if len(lines) == 1:
        return None
    return '\n'.join(lines) + '\n'


def build_summary(rows, problems, fname) -> str:
    n = len(rows)
    usable = sum(1 for r in rows if r.get('barcode_state') == 'ok')
    dropped = n - usable
    c_valid = sum(1 for r in rows if barcodes.order_code_is_valid(r['code']))
    errs = [p for p in problems if p[1] == '❌']
    warns = [p for p in problems if p[1] == '⚠️']

    lines = [
        f'📄 فایل: {fname}',
        f'🔢 تعداد ردیف: {n}',
        f'✅ ردیف قابل استفاده در tracking.csv: {usable} از {n}',
        f'📦 کد سفارش معتبر: {c_valid} از {n}',
        f'❌ خطا: {len(errs)}   ⚠️ هشدار: {len(warns)}',
    ]
    if dropped:
        lines.append('')
        lines.append(f'🚫 {dropped} ردیف به‌دلیل بارکد نامعتبر در tracking.csv ننوشته شد'
                     ' (در needs-fix.csv با دلیل آمده).')
    if problems:
        lines.append('')
        lines.append('مشکلات (نمونه):')
        for entry in problems[:8]:
            rn, sev, desc = entry[0], entry[1], entry[2]
            lines.append(f'{sev} ردیف {rn}: {desc}')
        if len(problems) > 8:
            lines.append(f'… و {len(problems) - 8} مورد دیگر (در فایل problems.txt)')
        lines.append('')
        lines.append('📎 فایل tracking.csv ضمیمه شد — کدهای خالی را خودت تکمیل کن.')
    else:
        lines.append('')
        lines.append('🎉 هیچ مشکلی پیدا نشد! فایل tracking.csv آماده است.')
    return '\n'.join(lines)


def build_problems(problems):
    if not problems:
        return None
    lines = ['گزارش کامل مشکلات', '=' * 40]
    for rn, sev, desc, b, c in problems:
        lines.append(f'[{sev}] ردیف {rn} | {desc}')
        if b:
            lines.append(f'      بارکد: {b}')
        if c:
            lines.append(f'      کد سفارش: {c}')
    return '\n'.join(lines) + '\n'


# ---------------------------------------------------------------------------
# نقطه‌ی ورود اصلی
# ---------------------------------------------------------------------------
def process_file(path, fname=None):
    ext = os.path.splitext(str(path))[1].lower()
    fname = fname or os.path.basename(str(path))

    if ext == '.pdf':
        rows = _read_pdf(path)
    elif ext in ('.xlsx', '.csv'):
        df = _read_excel_or_csv(path, ext)
        header_idx, bc_col, code_col, row_col, name_col = _find_header(df)
        if bc_col is None:
            raise ValueError(
                'ستون «بارکد» در فایل پیدا نشد. فایل باید جدول سفارش‌ها '
                '(ردیف / بارکد / تاریخ ثبت / نام گیرنده / کد سفارش / …) باشد.'
            )
        rows = _collect_rows(df, header_idx, bc_col, code_col, row_col, name_col)
        if not rows:
            raise ValueError('هیچ ردیف داده‌ای در فایل پیدا نشد.')
    else:
        raise ValueError(f'فرمت «{ext}» پشتیبانی نمی‌شود (فقط xlsx / csv / pdf).')

    problems = _analyze(rows)
    return (
        build_csv(rows),
        build_summary(rows, problems, fname),
        build_problems(problems),
        build_fix_csv(rows),
    )
