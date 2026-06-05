#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/autovideosrt}"

if [[ ! -d "$APP_DIR" ]]; then
  echo "APP_DIR does not exist: $APP_DIR" >&2
  exit 1
fi

bash "$APP_DIR/deploy/server_browser/install_dianxiaomi_sku_sync_timer.sh"
bash "$APP_DIR/deploy/server_browser/install_xmyc_storage_sync_timer.sh"
bash "$APP_DIR/deploy/server_browser/install_dianxiaomi_yuncang_sync_timer.sh"

echo "[sku-purchase-sync] timer status"
systemctl list-timers \
  autovideosrt-dianxiaomi-sku-sync.timer \
  autovideosrt-xmyc-storage-sync.timer \
  autovideosrt-dianxiaomi-yuncang-sync.timer \
  --no-pager
echo
echo "[sku-purchase-sync] install done"
