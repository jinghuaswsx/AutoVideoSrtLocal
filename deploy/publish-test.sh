#!/usr/bin/env bash
# AutoVideoSrt 测试环境一键发布脚本
# 用法: bash deploy/publish-test.sh [branch]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

KEY="${SSH_KEY:-$HOME/.ssh/CC.pem}"
SERVER_USER="root"
SERVER_HOST="172.30.254.14"
SERVER_PORT="22"
APP_DIR="/opt/autovideosrt-test"
SERVICE="autovideosrt-test"
BRANCH="${1:-$(git branch --show-current)}"

if [[ ! -f "$KEY" ]]; then
  echo "[ERROR] SSH key not found: $KEY" >&2
  echo "请确认 ~/.ssh/CC.pem 存在，或通过 SSH_KEY 环境变量指定路径。" >&2
  exit 1
fi

chmod 600 "$KEY" 2>/dev/null || true

echo "[1/3] 推送当前分支到远程: $BRANCH"
git push -u origin "$BRANCH"

echo "[2/3] 测试环境切换到分支并重启服务"
ssh -i "$KEY" -p "$SERVER_PORT" -o StrictHostKeyChecking=accept-new \
  "$SERVER_USER@$SERVER_HOST" \
  "cd $APP_DIR && \
   git fetch origin $BRANCH && \
   git checkout -B $BRANCH origin/$BRANCH && \
   systemctl restart $SERVICE && \
   systemctl status $SERVICE --no-pager -l | head -n 15"

echo "[3/3] 测试环境 HTTP 检查"
ssh -i "$KEY" -p "$SERVER_PORT" "$SERVER_USER@$SERVER_HOST" \
  "curl -s -o /dev/null -w 'HTTP %{http_code}\n' http://127.0.0.1:8080/ || true"

echo "测试环境发布完成：http://$SERVER_HOST:8080/"
