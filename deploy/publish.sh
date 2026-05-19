#!/usr/bin/env bash
# AutoVideoSrt 一键发布脚本
# 用法: bash deploy/publish.sh [commit message]
# 依赖: ~/.ssh/CC.pem（LocalServer 内网 SSH key）
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

KEY="${SSH_KEY:-$HOME/.ssh/CC.pem}"
SERVER_USER="root"
SERVER_HOST="172.30.254.14"
SERVER_PORT="22"
APP_DIR="/opt/autovideosrt"
SERVICE="autovideosrt"

if [[ ! -f "$KEY" ]]; then
  echo "[ERROR] SSH key not found: $KEY" >&2
  echo "请确认 ~/.ssh/CC.pem 存在，或通过 SSH_KEY 环境变量指定路径。" >&2
  exit 1
fi

# Linux/macOS 需要 600 权限；Windows Git Bash 下 chmod 无效但不影响 ssh
chmod 600 "$KEY" 2>/dev/null || true

# 1) 本地提交 & 推送（如有变更）
if [[ -n "$(git status --porcelain)" ]]; then
  MSG="${1:-chore: 发布更新}"
  echo "[1/3] 本地有变更，提交: $MSG"
  git add -A
  git commit -m "$MSG"
fi
echo "[1/3] 推送到远程..."
git push

# 2) SSH 到服务器拉取 & 重启
# 同步 systemd unit 文件后再 daemon-reload，避免 deploy/autovideosrt.service
# 改动（如 TimeoutStopSec / 环境变量）发布后不生效。cmp 在内容一致时不更新 mtime，
# 避免无谓 daemon-reload。
echo "[2/3] 远端 pull + sync unit + restart..."
ssh -i "$KEY" -p "$SERVER_PORT" -o StrictHostKeyChecking=accept-new \
  "$SERVER_USER@$SERVER_HOST" \
  "set -e
   cd $APP_DIR && git pull
   if ! cmp -s $APP_DIR/deploy/autovideosrt.service /etc/systemd/system/$SERVICE.service; then
     cp $APP_DIR/deploy/autovideosrt.service /etc/systemd/system/$SERVICE.service
     systemctl daemon-reload
     echo 'systemd unit synced + daemon-reload'
   fi
   systemctl restart $SERVICE
   systemctl status $SERVICE --no-pager | head -n 15"

# 3) 健康检查
echo "[3/3] 健康检查..."
ssh -i "$KEY" -p "$SERVER_PORT" "$SERVER_USER@$SERVER_HOST" \
  "curl -s -o /dev/null -w 'HTTP %{http_code}\n' http://127.0.0.1/ || true"

echo "发布完成：http://$SERVER_HOST/"
