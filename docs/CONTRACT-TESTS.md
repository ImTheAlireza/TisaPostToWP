# تست قرارداد با وردپرس واقعی (`TISA_CONTRACT`)

## این چه چیزی را ثابت می‌کند که بقیهٔ تست‌ها نمی‌توانند؟

تست‌های نوشتنِ محصول همه با یک دروغ‌گوِ با‌وفای (`MockTransport`) کار می‌کنند: پاسخ‌های
ساختگی، بدون شبکه، در دو ثانیه. آن‌ها می‌گویند **ربات چه می‌فرستد**. هیچ‌کدام نمی‌گویند
**ووکامرس چه می‌خواند**:

- آیا محصول واقعاً `draft` ساخته می‌شود و تعداد واریژن‌ها همان عددی است که در
  پیش‌نمایشِ تلگرام دیدیم (قاعدهٔ P0‑6؛ اگر جایی یک شمارشِ دستی تازه سر باز کند،
  اینجا می‌ترکد)؛
- آیا `attributes` و محدودیتِ رنگِ هر مدل روی سایت هم همان ماتریس ناقص را می‌سازد؛
- آیا SKU فقط روی والد است و هیچ واریژنی SKU نمی‌گیرد؛
- آیا `sale_price` / `manage_stock` / `stock_quantity` روی سایت می‌نشینند؛
- آیا `batch_id` تکراری (همان صفحهٔ بازمانده از کرش) محصول **دوم** نمی‌سازد؛
- آیا فایل PHP افزونهٔ ZIP بدون fatal error لود می‌شود و دسترسیِ مستقیم به‌اش منبع را
  لو نمی‌دهد (نگهبان `ABSPATH`).

پس این تست‌ها را نباید حذف کرد؛ ولی نباید به CI عمومی هم بست، چون به یک سایت زنده نیاز
دارند.

## اجرای محلی (چند دقیقه)

۱. Docker باید روی ماشین باشد (اینجا، در جعبهٔ توسعهٔ من، نیست — به همین دلیل این
   مرحله‌ها **هرگز اجرا نشده‌اند** و فقط به‌صورت کد+ساختار بازبینی شده‌اند).

۲. سایت تست را بالا بیاور:

   ```bash
   docker compose up -d --wait db wordpress
   bash deploy/bootstrap-wordpress.sh
   ```

   اسکریپت wp‑cli را نصب می‌کند، WooCommerce را فعال می‌کند، کاربر `tisa-tester` و
   Application Password را می‌سازد، و بعد آدرسی چاپ می‌کند که **یک بار در مرورگر** باید
   با آن دو کلید REST بگیری (ووکامرس کلید را با CLI نمی‌سازد؛ تنها کار دستیِ همین است).

۳. کلیدها را در فایل `.env.contract` بگذار (ترک‌نشده است):

   ```bash
   printf 'TISA_TEST_WOO_KEY=ck_...\nTISA_TEST_WOO_SECRET=cs_...\n' > .env.contract
   ```

۴. اجرا:

   ```bash
   docker compose run --rm contract        # کل فایل
   docker compose run --rm contract pytest -q tests/test_contract_wordpress.py::TestContractAgainstRealStore::test_draft_variable_product_matches_the_plan
   ```

۵. بستن و پاک کردن همه‌چیز:

   ```bash
   docker compose down -v
   ```

متغیرهایی که کانتینر تست می‌خواند (با مقادیر پیش‌فرضِ compose):
`TISA_CONTRACT=1`، `TISA_TEST_WOO_URL`، `TISA_TEST_WOO_KEY`، `TISA_TEST_WOO_SECRET`،
`TISA_TEST_WP_URL`، `TISA_TEST_WP_USER`، `TISA_TEST_WP_APP_PASSWORD`.

## روی GitHub

`.github/workflows/contract.yml` با `workflow_dispatch` و روی رانرِ خودساخته‌ای که
Docker دارد. سه secret لازم دارد: `TISA_CONTRACT_WOO_KEY`، `TISA_CONTRACT_WOO_SECRET`،
`TISA_CONTRACT_ADMIN_PASSWORD` (و اختیاری: `TISA_CONTRACT_WP_APP_PASSWORD`). گزینهٔ
`keep_site` سایت را بعد از اجرا بالا نگه می‌دارد تا با چشم نگاهش کنی.

## چه چیزی را **پوشش نمی‌دهد** (صادقانه)

- فرمِ آپلودِ افزونه در `wp-admin` (`admin-post.php` + nonce): تست‌های قرارداد HTTP‌اند و
  ورودِ مرورگر ندارند. مسیر ZIP در ربات با تست‌های `importer_contract` و فاز ۷ پوشش داده
  شده، نه اینجا.
- رفتار روی نسخه‌های قدیمی‌ترِ WooCommerce؛ کانتینر یک نسخهٔ مشخص دارد. وقتی سایتِ مالک
  نسخه‌اش عوض می‌شود، همین workflow را با ایمیجِ آن نسخه بزن.
- کشِ CDN/WAF جلوی سایت؛ تست فرض می‌کند REST بی‌واسطه جواب می‌دهد.

## چرا بیرون از این محیط «skip» است و نه «fail»

سوئیت اصلی (`pytest -q` / `python -m unittest discover -s tests`) باید روی سروری که Docker
و اعتبارنامه ندارد هم سبز باشد؛ این همان خصوصی است که فاز ۱ بابتش شادی می‌کرد. اگر
`TISA_CONTRACT=1` بگذاری ولی سایت پایین باشد، تست ** قرمز** می‌شود با پیامِ «سایت تست در …
جواب نداد» — یعنی خواستی اجرا کنی و نشد.
