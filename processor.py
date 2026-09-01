# -*- coding: utf-8 -*-
"""
processor.py — هسته‌ی پردازش فایل سفارش برای ربات تلگرام

ورودی: فایل .xlsx / .csv / .pdf  (جدول سفارش‌های تیساکیس / تیسا چاپ)
       ستون‌های جدول: ردیف | بارکد | تاریخ ثبت | نام گیرنده | کد سفارش | مقصد | نام فروشگاه | آدرس | وزن
       (فایل‌هایی با هدر انگلیسی order_id / tracking_code هم پشتیبانی می‌شوند)

خروجی: (csv_text, summary_text, problems_text)
  - csv_text      : فایل tracking.csv با ستون‌های order_id,tracking_code
  - summary_text  : خلاصه‌ی گزارش برای نمایش در چت
  - problems_text : گزارش کامل مشکلات (یا None اگر مشکلی نبود)
"""

import os
import re

import pandas as pd

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


def _norm(s) -> str:
    """نرمال‌سازی برای مقایسه‌ی نام ستون‌ها (هدرها)"""
    if s is None:
        return ''
    if isinstance(s, float) and s.is_integer():
        s = int(s)
    s = str(s).translate(_FA2EN)
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
    return s.translate(_FA2EN)


# ---------------------------------------------------------------------------
# نام ستون‌ها (بعد از نرمال‌سازی — فاصله‌ها حذف شده‌اند)
# ---------------------------------------------------------------------------
_BARCODE_HEADERS = {'بارکد', 'باركد', 'barcode', 'trackingcode', 'tracking_code', 'tracking'}
_CODE_HEADERS = {'کدسفارش', 'کدسفارشگیرنده', 'orderid', 'order_id', 'order', 'کد'}
_ROW_HEADERS = {'ردیف', 'رديف', 'ردی', 'شماره', 'no'}

_RE_BC = re.compile(r'^\d{24}$')      # بارکد: دقیقاً ۲۴ رقم
_RE_CODE = re.compile(r'^\d{6}$')     # کد سفارش: دقیقاً ۶ رقم
_RE_CODE5 = re.compile(r'^\d{5}$')    # کد ۵ رقمی (هشدار)
_RE_DATE = re.compile(r'14\d{2}/\d{2}/\d{2}')


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
    """پیدا کردن سطر هدر و ستون‌های بارکد / کد سفارش / ردیف"""
    for i in range(min(20, len(df))):
        cells = [_norm(c) for c in df.iloc[i].tolist()]
        bc_col = next((j for j, c in enumerate(cells) if c in _BARCODE_HEADERS), None)
        if bc_col is None:
            continue
        code_col = next((j for j, c in enumerate(cells) if c in _CODE_HEADERS), None)
        row_col = next((j for j, c in enumerate(cells) if c in _ROW_HEADERS), None)
        return i, bc_col, code_col, row_col
    return None, None, None, None


def _collect_rows(df, header_idx, bc_col, code_col, row_col):
    rows = []
    for r in range(header_idx + 1, len(df)):
        def get(col):
            if col is None:
                return ''
            v = df.iat[r, col]
            if v is None or (isinstance(v, float) and pd.isna(v)):
                return ''
            return v

        b_raw, c_raw, rn_raw = get(bc_col), get(code_col), get(row_col)
        b, c = _clean(b_raw), _clean(c_raw)
        rn = _clean(rn_raw) or str(len(rows) + 1)
        if not b and not c:
            continue  # سطر خالی یا سطر «جمع کل» — رد می‌شود
        numeric_b = isinstance(b_raw, (int, float)) and not isinstance(b_raw, bool)
        rows.append({'rownum': rn, 'barcode': b, 'code': c,
                     'barcode_numeric': numeric_b, 'sheet_row': r + 1})
    return rows


def _read_pdf(path):
    """استخراج جدول از PDF خروجی سامانه (چیدمان ثابت ستون‌ها)"""
    import pymupdf
    doc = pymupdf.open(path)
    rows = []
    for pno in range(len(doc)):
        page = doc[pno]
        d = page.get_text('rawdict')
        spans_by_y = {}
        for block in d.get('blocks', []):
            for line in block.get('lines', []):
                for span in line.get('spans', []):
                    x0, y0, x1 = span['bbox'][0], span['bbox'][1], span['bbox'][2]
                    text = ''.join(ch['c'] for ch in span.get('chars', [])).strip()
                    if not text:
                        continue
                    key = round(y0, 1)
                    spans_by_y.setdefault(key, []).append((x0, x1, text))
        for y, spans in spans_by_y.items():
            rec = {}
            for x0, x1, t in spans:
                if x0 > 535 and t.isdigit() and len(t) <= 4:
                    rec['n'] = t
                elif 420 <= x0 <= 535 and len(t) >= 20 and t.isdigit():
                    rec['bc'] = t
                elif 235 <= x0 <= 420 and x1 <= 425 and not t.isdigit():
                    rec.setdefault('mid', []).append(t)
            if 'bc' in rec and 'n' in rec:
                mid = ''.join(rec.get('mid', []))
                mid_clean = _RE_DATE.sub('', mid)          # حذف تاریخ
                m = re.search(r'(\d{5,6})', mid_clean)     # کد سفارش
                rows.append({'rownum': rec['n'], 'barcode': rec['bc'],
                             'code': m.group(1) if m else '',
                             'barcode_numeric': False, 'sheet_row': None})
    if len(rows) < 2:
        raise ValueError('ساختار PDF شناخته نشد (ستون‌های جدول پیدا نشد)')
    return rows


# ---------------------------------------------------------------------------
# بررسی مشکلات
# ---------------------------------------------------------------------------
def _analyze(rows):
    """برمی‌گرداند: list of (ردیف نمایشی, شدت ❌/⚠️, شرح, بارکد, کد)"""
    problems = []
    bc_count = {}
    code_count = {}

    for row in rows:
        n = row['rownum']
        b, c = row['barcode'], row['code']

        # --- بارکد ---
        if not b:
            problems.append((n, '❌', 'بارکد خالی', b, c))
        elif row['barcode_numeric'] or re.search(r'[eE]', b) or '.' in b:
            problems.append((n, '❌',
                             'بارکد به‌صورت عدد ذخیره شده و دقتش از بین رفته — از فایل متنی/PDF اصلی استفاده کن',
                             b, c))
        elif not _RE_BC.match(b):
            problems.append((n, '❌', f'بارکد نامعتبر: {len(b)} رقم (باید ۲۴ رقم باشد)', b, c))
        else:
            bc_count.setdefault(b, []).append(n)

        # --- کد سفارش ---
        if not c:
            problems.append((n, '⚠️', 'کد سفارش خالی (باید خودت تکمیل کنی)', b, c))
        elif _RE_CODE.match(c):
            code_count.setdefault(c, []).append(n)
        elif _RE_CODE5.match(c):
            problems.append((n, '⚠️', 'کد سفارش ۵ رقمی (احتمالاً یک رقم جا افتاده)', b, c))
        else:
            problems.append((n, '❌', f'کد سفارش نامعتبر: «{c}» (باید ۶ رقم باشد)', b, c))

    # --- تکراری‌ها ---
    for b, ns in bc_count.items():
        if len(ns) > 1:
            problems.append((' و '.join(ns), '❌', 'بارکد تکراری', b, ''))
    for c, ns in code_count.items():
        if len(ns) > 1:
            problems.append((' و '.join(ns), '⚠️', 'کد سفارش تکراری', '', c))

    return problems


# ---------------------------------------------------------------------------
# خروجی‌ها
# ---------------------------------------------------------------------------
def build_csv(rows) -> str:
    out = ['order_id,tracking_code']
    for row in rows:
        out.append(f"{row['code']},{row['barcode']}")
    return '\n'.join(out) + '\n'


def build_summary(rows, problems, fname) -> str:
    n = len(rows)
    b_valid = sum(1 for r in rows
                  if _RE_BC.match(r['barcode']) and not r['barcode_numeric'])
    c_valid = sum(1 for r in rows if _RE_CODE.match(r['code']))
    errs = [p for p in problems if p[1] == '❌']
    warns = [p for p in problems if p[1] == '⚠️']

    lines = [
        f'📄 فایل: {fname}',
        f'🔢 تعداد ردیف: {n}',
        f'✅ بارکد معتبر: {b_valid} از {n}',
        f'📦 کد سفارش معتبر: {c_valid} از {n}',
        f'❌ خطا: {len(errs)}   ⚠️ هشدار: {len(warns)}',
    ]
    if problems:
        lines.append('')
        lines.append('مشکلات (نمونه):')
        for p in problems[:8]:
            rn, sev, desc, b, c = p
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
        header_idx, bc_col, code_col, row_col = _find_header(df)
        if bc_col is None:
            raise ValueError(
                'ستون «بارکد» در فایل پیدا نشد. فایل باید جدول سفارش‌ها '
                '(ردیف / بارکد / تاریخ ثبت / نام گیرنده / کد سفارش / …) باشد.'
            )
        rows = _collect_rows(df, header_idx, bc_col, code_col, row_col)
        if not rows:
            raise ValueError('هیچ ردیف داده‌ای در فایل پیدا نشد.')
    else:
        raise ValueError(f'فرمت «{ext}» پشتیبانی نمی‌شود (فقط xlsx / csv / pdf).')

    problems = _analyze(rows)
    return build_csv(rows), build_summary(rows, problems, fname), build_problems(problems)
