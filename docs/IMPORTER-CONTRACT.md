# قرارداد ربات ↔ افزونهٔ واردکنندهٔ وردپرس

> افزونهٔ WordPress داخل `tisa-product-importer.zip` است و **خارج از قلمرو این ریپو**
> (منبعش جای دیگری است و فایل داخل ریپو یک باینری ساخته‌شده — بند P2-7 سند
> `docs/CODE-REVIEW-AND-UPGRADE-PLAN.md`). برای همین اینجا فقط *قرارداد* نوشته می‌شود:
> ربات چه چیزی در `product.json` می‌گذارد و افزونه برای درست‌کارکردن به چه چیزی باید
> توجه کند. هیچ ویرایشی روی zip داخل این ریپو انجام نشده — چون فردا با یک build تازه
> از منبع، بی‌صدا از بین می‌رود.

## چیزی که ربات می‌نویسد

`product.json` داخل ZIP (همان‌طور که `bot/modules/product_flow.py` می‌سازد):

| کلید | نمونه | معنا |
|---|---|---|
| `batch_id` | `"719e372625cf"` | شناسهٔ ۱۲تاییِ «این بسته». از محتوا + چت ساخته می‌شود (`bot/services/publish_batch.py`): همان متن، همان قیمت‌ها، همان عکس‌ها، همان چت ⇒ همان شناسه. نسخهٔ ربات ≥ ۰.۹.۰ |
| `mode` | `"new"` / `"update"` | مسیر شارژ از ساخت تازه جداست |
| `title`, `price`, `prices`, `sku_prefix`, `models`, `attributes`, `categories`, `model_colors`, `description`, `product_type`, `image_mode` | — | مثل قبل |

همین `batch_id` در مسیر REST هم به‌عنوان متای محصول نوشته می‌شود:

- `tisa_batch_id` → همان شناسه (کلید مقایسه)
- `tisa_source` → JSON با `chat_id`, `thread_id`, `images`, `variations`, `bot_version`, `captured_at`

## کاری که افزونه باید بکند (قطعهٔ آماده)

هدف: آپلود دوبارهٔ **همان فایل** (یا فایلِ یک تلاش نیمه‌کارهٔ دیگر) محصول دومی نسازد.

داخل `import()`، بعد از اینکه `product.json` decoded شد و بررسی‌های `title/price/prefix/models`
رد شد، و **قبل از** `self::next_sku($prefix)`:

```php
// ♻️ idempotency: a package we already imported must not become a second product.
$batch = sanitize_text_field((string)($data['batch_id'] ?? ''));
if ($batch !== '') {
    $done = get_posts([
        'post_type' => 'product', 'post_status' => 'any', 'numberposts' => 1,
        'meta_key' => '_tisa_batch_id', 'meta_value' => $batch, 'fields' => 'ids',
    ]);
    if ($done) {
        self::remove_dir($dir);
        self::success('این بسته پیش‌تر وارد شده بود؛ محصول دومی ساخته نشد.', (int) $done[0]);
    }
}
```

و داخل بلوک ساخت محصول، کنار `update_post_meta($product_id, '_sku', $sku);`:

```php
if ($batch !== '') update_post_meta($product_id, '_tisa_batch_id', $batch);
```

هر دو تکه عقب‌نگر‌اند: ZIP های قدیمی که `batch_id` ندارند دقیقاً مثل قبل رفتار می‌کنند
(`$batch === ''` ⇒ هیچ جستجویی انجام نمی‌شود، هیچ متایی نوشته نمی‌شود).

## نکته‌ها

- اگر محصولِ نیمه‌کاره از تلاش قبلی پیدا شد، افزونه می‌تواند به‌جای «ساخت دوباره»،
  همان `product_id` را نشان بدهد و به کاربر بگوید «ادامه بده» — این را ربات در مسیر
  REST انجام می‌دهد (واریژن‌های جاافتاده را اضافه می‌کند)؛ برای ZIP فعلاً همین
  «وارد نشد، همین است» کافی و امن‌تر است.
- `_tisa_batch_id` (با underscore) متای داخلی WordPress است؛ `tisa_batch_id` همان چیز روی
  محصول ووکامرس از مسیر REST. دو اسم، دو مسیر، یک مقدار.
- ربات **هیچ‌وقت** ZIP را پاک نمی‌کند و چیزی روی سایت با ZIP کار نمی‌کند؛ پس اگر افزونه
  این تکه را نگذارد، فقط تشخیص تکراری در مسیر افزونه از دست می‌رود — مسیر REST سالم می‌ماند.
