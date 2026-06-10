#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/autovideosrt}"
SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"

units=(
  autovideosrt-mingkong-product-library-sync.service
  autovideosrt-mingkong-product-library-sync.timer
  autovideosrt-mingkong-sku-backfill-plan.service
  autovideosrt-mingkong-sku-backfill-plan.timer
  autovideosrt-mingkong-sku-backfill-ready.service
  autovideosrt-mingkong-sku-backfill-ready.timer
  autovideosrt-mingkong-sku-backfill-base.service
  autovideosrt-mingkong-sku-backfill-base.timer
  autovideosrt-mingkong-sku-backfill-retry.service
  autovideosrt-mingkong-sku-backfill-retry.timer
)

for unit in "${units[@]}"; do
  install -m 644 "$APP_DIR/deploy/server_browser/$unit" "$SYSTEMD_DIR/$unit"
done

systemctl daemon-reload
systemctl enable --now \
  autovideosrt-mingkong-product-library-sync.timer \
  autovideosrt-mingkong-sku-backfill-plan.timer \
  autovideosrt-mingkong-sku-backfill-ready.timer \
  autovideosrt-mingkong-sku-backfill-base.timer \
  autovideosrt-mingkong-sku-backfill-retry.timer

systemctl list-timers \
  autovideosrt-mingkong-product-library-sync.timer \
  autovideosrt-mingkong-sku-backfill-plan.timer \
  autovideosrt-mingkong-sku-backfill-ready.timer \
  autovideosrt-mingkong-sku-backfill-base.timer \
  autovideosrt-mingkong-sku-backfill-retry.timer \
  --no-pager
