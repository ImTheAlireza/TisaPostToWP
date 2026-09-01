# -*- coding: utf-8 -*-
"""
ربات تلگرام: دریافت فایل سفارش (xlsx / csv / pdf) → خروجی tracking.csv + گزارش مشکلات

راه‌اندازی:
  1) از BotFather توکن بگیر
  2) توکن را تنظیم کن:
       export BOT_TOKEN=123456789:AA...      (لینوکس/مک)
       یا در ویندوز:  set BOT_TOKEN=123456789:AA...
       یا مقدار BOT_TOKEN در همین فایل را عوض کن
  3) python bot.py
"""

import asyncio
import io
import logging
import os
from pathlib import Path

from telegram import Update
from telegram.ext import (Application, CommandHandler, ContextTypes,
                          MessageHandler, filters)

import processor

# توکن ربات — از متغیر محیطی BOT_TOKEN یا اینجا
BOT_TOKEN = os.environ.get('BOT_TOKEN', '').strip() or 'TOKEN_RA_اینجا_بگذار'

ALLOWED_EXTS = {'.xlsx', '.csv', '.pdf'}

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

START_TEXT = (
    '🤖 ربات تبدیل فایل سفارش به CSV\n\n'
    'یک فایل با یکی از این فرمت‌ها بفرست:\n'
    '📊 اکسل (.xlsx) — خروجی جدول سفارش‌ها\n'
    '📄 CSV (.csv)\n'
    '📑 PDF (.pdf) — خروجی مستقیم سامانه تیساکیس / تیسا چاپ\n\n'
    'ربات این کارها را می‌کند:\n'
    '1️⃣ ستون‌های «بارکد» و «کد سفارش» را پیدا می‌کند\n'
    '2️⃣ مشکلات را گزارش می‌دهد (خالی، تکراری، فرمت اشتباه، …)\n'
    '3️⃣ فایل tracking.csv با ستون‌های order_id,tracking_code می‌سازد\n\n'
    '⚠️ کد سفارش‌های خالی در CSV خالی می‌مانند تا خودت تکمیل کنی.'
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(START_TEXT)


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    fname = doc.file_name or 'file'
    ext = Path(fname).suffix.lower()

    if ext not in ALLOWED_EXTS:
        await update.message.reply_text('❌ فقط فایل‌های xlsx / csv / pdf پشتیبانی می‌شوند.')
        return

    status = await update.message.reply_text('⏳ در حال پردازش…')
    tmp = None
    try:
        tg_file = await doc.get_file()
        tmp = Path('/tmp') / f'input_{doc.file_unique_id}{ext}'
        await tg_file.download_to_drive(str(tmp))

        # پردازش در thread جدا تا پیام‌های دیگر ربات مسدود نشوند
        csv_text, summary, problems_text = await asyncio.to_thread(
            processor.process_file, str(tmp), fname)

        await update.message.reply_document(
            document=io.BytesIO(csv_text.encode('utf-8')),
            filename='tracking.csv',
            caption=summary[:950],          # محدودیت کپشن تلگرام ~1024
        )
        if problems_text:
            await update.message.reply_document(
                document=io.BytesIO(problems_text.encode('utf-8')),
                filename='problems.txt',
                caption='📋 گزارش کامل مشکلات',
            )
        await status.delete()
    except Exception as e:
        logging.exception('processing failed')
        await update.message.reply_text(f'❌ خطا در پردازش:\n{e}')
    finally:
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass


def main():
    token = BOT_TOKEN
    if not token or token.startswith('TOKEN_RA'):
        print('⚠️  توکن ربات تنظیم نشده است.')
        print('    export BOT_TOKEN=123456789:AA...   (یا مقدار BOT_TOKEN داخل bot.py)')
        return

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    print('🤖 ربات فعال شد — منتظر دریافت فایل…')
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
