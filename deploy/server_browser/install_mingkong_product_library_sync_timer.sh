#!/usr/bin/env bash
set -euo pipefail

cp /opt/autovideosrt/deploy/server_browser/autovideosrt-mingkong-product-library-sync.service /etc/systemd/system/
cp /opt/autovideosrt/deploy/server_browser/autovideosrt-mingkong-product-library-sync.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now autovideosrt-mingkong-product-library-sync.timer
systemctl list-timers autovideosrt-mingkong-product-library-sync.timer --no-pager
