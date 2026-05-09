#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/autovideosrt}"

cd "$APP_DIR"

install -d -m 755 -o cjh -g cjh /data/autovideosrt/browser/profiles/meta-ads
install -d -m 755 -o cjh -g cjh /data/autovideosrt/browser/profiles/mk-selection
install -d -m 755 -o cjh -g cjh /data/autovideosrt/browser/profiles/rjc-dianxiaomi
install -d -m 755 -o cjh -g cjh /data/autovideosrt/browser/runtime-meta-ads
install -d -m 755 -o cjh -g cjh /data/autovideosrt/browser/runtime-mk-selection
install -d -m 755 -o cjh -g cjh /data/autovideosrt/browser/runtime-rjc-dianxiaomi
install -d -m 755 -o cjh -g cjh /data/autovideosrt/browser/logs/meta-ads
install -d -m 755 -o cjh -g cjh /data/autovideosrt/browser/logs/mk-selection
install -d -m 755 -o cjh -g cjh /data/autovideosrt/browser/logs/rjc-dianxiaomi

chmod 755 "$APP_DIR/deploy/server_browser/run_visible_dxm_env.sh"
install -m 644 "$APP_DIR/deploy/server_browser/autovideosrt-dxm01-meta-vnc.service" /etc/systemd/system/autovideosrt-dxm01-meta-vnc.service
install -m 644 "$APP_DIR/deploy/server_browser/autovideosrt-dxm02-mk-vnc.service" /etc/systemd/system/autovideosrt-dxm02-mk-vnc.service
install -m 644 "$APP_DIR/deploy/server_browser/autovideosrt-dxm03-rjc-vnc.service" /etc/systemd/system/autovideosrt-dxm03-rjc-vnc.service
install -m 644 "$APP_DIR/deploy/server_browser/autovideosrt-cdp-environment-watchdog.service" /etc/systemd/system/autovideosrt-cdp-environment-watchdog.service
install -m 644 "$APP_DIR/deploy/server_browser/autovideosrt-cdp-environment-watchdog.timer" /etc/systemd/system/autovideosrt-cdp-environment-watchdog.timer
install -m 644 "$APP_DIR/deploy/server_browser/autovideosrt-dianxiaomi-order-freshness-watchdog.service" /etc/systemd/system/autovideosrt-dianxiaomi-order-freshness-watchdog.service
install -m 644 "$APP_DIR/deploy/server_browser/autovideosrt-dianxiaomi-order-freshness-watchdog.timer" /etc/systemd/system/autovideosrt-dianxiaomi-order-freshness-watchdog.timer
install -m 644 "$APP_DIR/deploy/server_browser/autovideosrt-roi-realtime-sync.service" /etc/systemd/system/autovideosrt-roi-realtime-sync.service
install -m 644 "$APP_DIR/deploy/server_browser/autovideosrt-meta-daily-final-sync.service" /etc/systemd/system/autovideosrt-meta-daily-final-sync.service
install -m 644 "$APP_DIR/deploy/server_browser/autovideosrt-meta-daily-final-check.service" /etc/systemd/system/autovideosrt-meta-daily-final-check.service
install -m 644 "$APP_DIR/deploy/server_browser/autovideosrt-shopifyid-sync.service" /etc/systemd/system/autovideosrt-shopifyid-sync.service
install -m 644 "$APP_DIR/deploy/server_browser/autovideosrt-xmyc-storage-sync.service" /etc/systemd/system/autovideosrt-xmyc-storage-sync.service

# Docs-anchor: docs/superpowers/specs/2026-05-09-roi-hourly-sync-lock-recovery.md
install -d -m 755 /etc/systemd/system/autovideosrt-roi-realtime-sync.service.d
install -m 644 "$APP_DIR/deploy/server_browser/autovideosrt-roi-realtime-sync.service.d/10-browser-lock.conf" /etc/systemd/system/autovideosrt-roi-realtime-sync.service.d/10-browser-lock.conf

systemctl daemon-reload
systemctl disable --now autovideosrt-browser.service autovideosrt-mk-browser.service autovideosrt-rjc-vnc.service >/dev/null 2>&1 || true
systemctl enable --now autovideosrt-dxm01-meta-vnc.service autovideosrt-dxm02-mk-vnc.service autovideosrt-dxm03-rjc-vnc.service
systemctl enable --now autovideosrt-cdp-environment-watchdog.timer
systemctl enable --now autovideosrt-dianxiaomi-order-freshness-watchdog.timer
systemctl list-timers autovideosrt-cdp-environment-watchdog.timer autovideosrt-dianxiaomi-order-freshness-watchdog.timer --no-pager
