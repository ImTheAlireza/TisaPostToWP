<?php
/**
 * TisaCase product-description rules
 *
 * Use as a Code Snippets PHP snippet OR as a small plugin.
 * It updates old and new products, including products created manually or via REST.
 * Do not add the opening <?php tag when pasting into a snippet plugin.
 */

if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

if ( ! class_exists( 'Tisa_Product_Description_Rules' ) ) {
    final class Tisa_Product_Description_Rules {
        const ACTION = 'tisa_repair_product_descriptions';
        const NONCE  = 'tisa_repair_product_descriptions';
        const BATCH  = 100;

        public static function init() {
            // Covers manual saves, WooCommerce admin saves and REST-created products.
            add_action( 'woocommerce_new_product', array( __CLASS__, 'sync_product' ), 30, 1 );
            add_action( 'woocommerce_update_product', array( __CLASS__, 'sync_product' ), 30, 1 );
            add_action( 'save_post_product', array( __CLASS__, 'sync_post' ), 30, 3 );

            // Adds a one-click repair button to Products > All Products.
            add_action( 'admin_notices', array( __CLASS__, 'admin_notice' ) );
            add_action( 'manage_posts_extra_tablenav', array( __CLASS__, 'render_repair_button' ), 1, 1 );
            add_action( 'restrict_manage_posts', array( __CLASS__, 'render_filter_button' ), 99 );
            add_action( 'admin_post_' . self::ACTION, array( __CLASS__, 'repair_batch' ) );
        }

        /** Return the exact HTML description required for one product. */
        public static function description_for( $product ) {
            if ( ! $product || ! is_a( $product, 'WC_Product' ) ) {
                return '';
            }

            $sku    = strtoupper( trim( (string) $product->get_sku() ) );
            $title  = (string) $product->get_name();
            $is_printed = (bool) preg_match( '/^(?:CH|SB)(?:\d|$)/i', $sku );

            if ( $is_printed ) {
                return '<p>برای مشاهده محصولات چاپی بیشتر به سایت <strong><a href="https://TISACHAP.COM">TISACHAP.COM</a></strong> مراجعه کنید.</p>'
                    . '<p>آماده سازی و تولید محصولات چاپی 7 تا 18 روزکاری زمان بر خواهد بود؛ از صبوری شما متشکریم</p>';
            }

            if ( false !== mb_stripos( $title, 'قاب', 0, 'UTF-8' ) ) {
                return '<p><strong>⚠️ توجه: تصاویر صرفاً برای نمایش رنگ و طرح محصول هستند. ظاهر نهایی قاب (گرد یا تخت بودن لبه‌ها، میزان برجستگی محافظ دوربین، محل دکمه‌ها و...) متناسب با مدل گوشی انتخابی شما تولید و ارسال می‌شود</strong></p>';
            }

            return '';
        }

        public static function sync_product( $product_id ) {
            static $running = false;
            if ( $running || ! function_exists( 'wc_get_product' ) ) {
                return;
            }
            $product = wc_get_product( $product_id );
            if ( ! $product ) {
                return;
            }
            $running = true;
            $description = self::description_for( $product );
            if ( $product->get_description() !== $description ) {
                $product->set_description( $description );
                $product->save();
            }
            $running = false;
        }

        public static function sync_post( $post_id, $post, $update ) {
            if ( wp_is_post_revision( $post_id ) || 'auto-draft' === $post->post_status ) {
                return;
            }
            self::sync_product( $post_id );
        }

        private static function is_products_screen() {
            if ( ! is_admin() ) {
                return false;
            }
            $screen = function_exists( 'get_current_screen' ) ? get_current_screen() : null;
            return $screen && 'edit' === $screen->base && 'product' === $screen->post_type;
        }

        public static function admin_notice() {
            if ( ! self::is_products_screen() || ! current_user_can( 'edit_products' ) ) {
                return;
            }
            $page = isset( $_GET['tisa_desc_page'] ) ? absint( $_GET['tisa_desc_page'] ) : 0;
            $done = isset( $_GET['tisa_desc_done'] ) ? absint( $_GET['tisa_desc_done'] ) : 0;
            if ( $done ) {
                echo '<div class="notice notice-success is-dismissible"><p>توضیحات محصولات بررسی و اصلاح شد. تعداد اصلاح‌شده: <strong>' . esc_html( $done ) . '</strong></p></div>';
                return;
            }
            $url = add_query_arg(
                array(
                    'action'   => self::ACTION,
                    'page'     => 0,
                    '_wpnonce' => wp_create_nonce( self::NONCE ),
                ),
                admin_url( 'admin-post.php' )
            );
            echo '<div class="notice notice-info"><p><strong>قوانین توضیحات تیساکیس</strong> — برای بررسی محصولات قدیمی و جدید، <a class="button" href="' . esc_url( $url ) . '">اصلاح توضیحات همه محصولات</a></p></div>';
        }

        public static function render_repair_button( $which ) {
            if ( 'top' !== $which || ! self::is_products_screen() || ! current_user_can( 'edit_products' ) ) {
                return;
            }
            $url = add_query_arg(
                array(
                    'action'   => self::ACTION,
                    'page'     => 0,
                    '_wpnonce' => wp_create_nonce( self::NONCE ),
                ),
                admin_url( 'admin-post.php' )
            );
            echo '<span style="display:inline-block;margin:7px 8px 0 0;vertical-align:middle"><a class="button" href="' . esc_url( $url ) . '">اصلاح توضیحات همه محصولات</a></span>';
        }

        public static function render_filter_button() {
            if ( ! self::is_products_screen() || ! current_user_can( 'edit_products' ) ) {
                return;
            }
            $url = add_query_arg(
                array(
                    'action'   => self::ACTION,
                    'page'     => 0,
                    '_wpnonce' => wp_create_nonce( self::NONCE ),
                ),
                admin_url( 'admin-post.php' )
            );
            echo '<a class="button" style="margin-right:6px" href="' . esc_url( $url ) . '">اصلاح توضیحات همه محصولات</a>';
        }

        /** Process products in batches so a large catalog does not time out. */
        public static function repair_batch() {
            if ( ! current_user_can( 'edit_products' ) ) {
                wp_die( 'دسترسی کافی ندارید.' );
            }
            check_admin_referer( self::NONCE );

            $page = isset( $_GET['page'] ) ? absint( $_GET['page'] ) : 0;
            $ids = wc_get_products( array(
                'status'  => array( 'publish', 'draft', 'pending', 'private' ),
                'limit'   => self::BATCH,
                'page'    => $page + 1,
                'orderby' => 'ID',
                'order'   => 'ASC',
                'return'  => 'ids',
            ) );

            $changed = isset( $_GET['changed'] ) ? absint( $_GET['changed'] ) : 0;
            foreach ( $ids as $product_id ) {
                $before = wc_get_product( $product_id );
                $old = $before ? $before->get_description() : '';
                self::sync_product( $product_id );
                $after = wc_get_product( $product_id );
                if ( $after && $old !== $after->get_description() ) {
                    $changed++;
                }
            }

            if ( count( $ids ) === self::BATCH ) {
                $next = add_query_arg(
                    array( 'action' => self::ACTION, 'page' => $page + 1, 'changed' => $changed ),
                    admin_url( 'admin-post.php' )
                );
                $next = add_query_arg( '_wpnonce', wp_create_nonce( self::NONCE ), $next );
                wp_safe_redirect( $next );
                exit;
            }

            wp_safe_redirect( add_query_arg( 'tisa_desc_done', $changed, admin_url( 'edit.php?post_type=product' ) ) );
            exit;
        }
    }

    Tisa_Product_Description_Rules::init();
}
