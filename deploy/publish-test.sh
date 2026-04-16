#!/usr/bin/env bash
# AutoVideoSrt 测试环境一键发布脚本
# 用法: bash deploy/publish-test.sh [commit message]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

KEY="$REPO_ROOT/.server/openclaw-noobird.pem"
SERVER_USER="root"
SERVER_HOST="14.103.220.208"
SERVER_PORT="22"
APP_DIR="/opt/autovideosrt-test"
SERVICE="autovideosrt-test"

if [[ ! -f "$KEY" ]]; then
  echo "[ERROR] SSH key not found: $KEY" >&2
  echo "请把 openclaw-noobird.pem 放到 .server/ 目录下。" >&2
  exit 1
fi

chmod 600 "$KEY" 2>/dev/null || true

# 1) 本地提交 & 推送（如有变更）
if [[ -n "$(git status --porcelain)" ]]; then
  MSG="${1:-chore: 测试环境发布更新}"
  echo "[1/3] 本地有变更，提交: $MSG"
  git add -A
  git commit -m "$MSG"
fi
echo "[1/3] 推送到远程..."
git push

# 2) SSH 到服务器执行测试环境部署
echo "[2/3] 远端部署测试环境..."
ssh -i "$KEY" -p "$SERVER_PORT" -o StrictHostKeyChecking=accept-new \
  "$SERVER_USER@$SERVER_HOST" \
  "bash $APP_DIR/deploy/setup-test.sh"

# 3) 健康检查
echo "[3/3] 健康检查..."
ssh -i "$KEY" -p "$SERVER_PORT" "$SERVER_USER@$SERVER_HOST" \
  "curl -s -o /dev/null -w 'HTTP %{http_code}\n' http://127.0.0.1:9999/ || true"

echo "测试环境发布完成：http://$SERVER_HOST:9999"
