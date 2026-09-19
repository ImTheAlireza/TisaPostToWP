#!/usr/bin/env bash
# آماده‌سازی سایتِ تستِ قرارداد (همانی که docker-compose.yml بالا می‌آورد).
#
# چه کاری می‌کند — همه‌اش از داخل کانتینر، با wp-cli:
#   1. wp-cli را نصب می‌کند (اگر نباشد) و WooCommerce را نصب/فعال می‌کند؛
#   2. یک کاربر admin می‌سازد و Application Password برای Media API؛
#   3. اگر .env.contract باشد، کلیدهای REST را از همان‌جا برمی‌دارد و تمام.
#
# چه کاری نمی‌کند و چرا: **ساخت consumer key/secret**. ووکامرس این کلیدها را فقط از
# صفحهٔ «WooCommerce › Settings › Advanced › REST API» می‌سازد (و فرم‌های admin بدون
# nonce و بدون ورودِ مرورگر کار نمی‌کنند). یک‌بار در مرورگر زدنش ارزان‌تر و صادقانه‌تر
# از آن است که این اسکریپت با INSERT مستقیم در جدولِ wc_api_keys، وابسته به نسخه،
# دادهٔ نیمه‌سالم بچیند. پس اسکریپت آدرس را چاپ می‌کند و توقف می‌کند.
#
#   docker compose up -d wordpress && bash deploy/bootstrap-wordpress.sh
set -euo pipefail

cd "$(dirname "$0")/.."
COMPOSE=${COMPOSE:-"docker compose -f docker-compose.yml"}
ADMIN_USER=${TISA_TEST_ADMIN_USER:-tisa-admin}
ADMIN_PASSWORD=${TISA_TEST_ADMIN_PASSWORD:-tisatest123}
TESTER_USER=${TISA_TEST_WP_USER:-tisa-tester}
SITE_URL=${TISA_TEST_SITE_URL:-http://127.0.0.1:8080}
ENV_FILE=.env.contract

wp() { $COMPOSE exec -T -u root wordpress wp --allow-root --path=/var/www/html "$@"; }

echo "→ wp-cli"
if ! $COMPOSE exec -T -u root wordpress test -x /usr/local/bin/wp 2>/dev/null; then
  $COMPOSE exec -T -u root wordpress bash -c \
    'curl -fsSL https://raw.githubusercontent.com/wp-cli/builds/gh-pages/phar/wp-cli.phar -o /usr/local/bin/wp && chmod +x /usr/local/bin/wp'
fi

echo "→ صبر تا وردپرس بالا بیاید"
for _ in $(seq 1 60); do
  if wp core is-installed 2>/dev/null; then break; fi
  sleep 2
done
wp core is-installed >/dev/null 2>&1 || \
  wp core install --url="$SITE_URL" --title="TISA CONTRACT" \
    --admin_user="$ADMIN_USER" --admin_password="$ADMIN_PASSWORD" --admin_email=tisa@example.com

echo "→ WooCommerce"
wp plugin is-active woocommerce >/dev/null 2>&1 || wp plugin install woocommerce --activate

echo "→ کاربر تستی (نقش admin تا Media API کار کند)"
wp user get "$TESTER_USER" >/dev/null 2>&1 || \
  wp user create "$TESTER_USER" "$TESTER_USER@example.com" --role=administrator --porcelain | tail -1 | xargs -r -I{} \
  wp user update {} --user_pass="$ADMIN_PASSWORD"

echo "→ Application Password برای ${TESTER_USER}"
APP_PASS=""
if ! wp user application-password list "$TESTER_USER" --field=application_password_id --porcelain 2>/dev/null | grep -qx tisa-contract; then
  wp user application-password delete "$TESTER_USER" --all >/dev/null 2>&1 || true
  APP_PASS=$(wp user application-password create "$TESTER_USER" tisa-contract --porcelain 2>/dev/null || true)
fi

if [ -f "$ENV_FILE" ] && grep -q '^TISA_TEST_WOO_KEY=..' "$ENV_FILE"; then
  echo "✅ کلیدهای REST از $ENV_FILE خوانده می‌شود؛ آماده‌ست:"
  echo "   docker compose run --rm contract"
  exit 0
fi

cat <<EOF
⚠️  یک کار دستی باقی است (ووکامرس کلید REST را با CLI نمی‌سازد):
   1. $SITE_URL/wp-admin/admin.php?page=wc-settings&tab=advanced&section=keys
   2. Add key — نقش Read/Write — و کپی کن:
        TISA_TEST_WOO_KEY=ck_...
        TISA_TEST_WOO_SECRET=cs_...
   3. در همین فایل $ENV_FILE بگذار (در .gitignore است)${APP_PASS:+ همراه با TISA_TEST_WP_APP_PASSWORD="$APP_PASS"}، بعد:
        docker compose run --rm contract
EOF
exit 1
