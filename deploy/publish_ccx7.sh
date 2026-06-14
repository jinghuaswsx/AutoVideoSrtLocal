#!/usr/bin/env bash
# CC-X7（美国 GCP 公网开发机，Claude Code 所在）→ 线上服务器 autovideosrt 发布脚本
# 与 deploy/publish.sh 的区别：publish.sh 走 `ssh -i CC.pem root@172.16.254.106` 直连，
# 是 Windows 内网开发机用的；CC-X7 在公网、只能走反向隧道、登录 cjh，故另立此脚本。
#
# 用法:
#   bash deploy/publish_ccx7.sh test                      # 只发测试 :8080
#   bash deploy/publish_ccx7.sh prod --confirm            # 两段不停：test 验证→自动发 prod :80
#   bash deploy/publish_ccx7.sh prod --confirm -m "msg"   # 工作区脏则先自动 commit 再发
#   bash deploy/publish_ccx7.sh <env> ... --dry-run       # 只打印将执行命令，不实际跑
#
# 触发口令（任意会话听到都执行这个）："提交代码，合并代码到master，发布线上生产环境"
#   => bash deploy/publish_ccx7.sh prod --confirm -m "<语义化提交信息>"
#   脚本会：① 脏树自动 commit ② fetch+rebase 安全合并 origin/master（防回退）+ push
#           ③ 发 test 验证 200/302 ④ 通过则自动发 prod（不中途停；test 起不来就拦在 test）
#
# 依赖:
#   - ssh 别名 avsl（~/.ssh/config）= 反向隧道登录线上机 cjh（cjh 免密 sudo）
#   - deploy key ~/.ssh/id_ed25519_autovideosrtlocal（对 GitHub 有 write）
# 设计: docs/superpowers/specs/2026-06-14-ccx7-tunnel-deploy-design.md
set -euo pipefail

# ---- 参数 ----
ENV="test"
if [[ $# -gt 0 && "$1" != -* ]]; then ENV="$1"; shift; fi
CONFIRM=0; DRYRUN=0; COMMIT_MSG=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --confirm) CONFIRM=1 ;;
    --dry-run) DRYRUN=1 ;;
    -m|--message) shift; COMMIT_MSG="${1:-}" ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
  shift
done
if [[ "$ENV" != "test" && "$ENV" != "prod" ]]; then
  echo "用法: bash deploy/publish_ccx7.sh test|prod [--confirm] [-m msg] [--dry-run]" >&2
  exit 2
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SSH_ALIAS="avsl"
GIT_PUSH_SSH="ssh -i $HOME/.ssh/id_ed25519_autovideosrtlocal -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"

log() { echo -e "\n\033[1;36m[publish_ccx7]\033[0m $*"; }

# 远端部署单个环境的脚本（单引号：本地不展开，变量经环境传入远端）
REMOTE_DEPLOY='
set -e
sudo git -C "$APP_DIR" pull origin master --ff-only
if [ -n "$UNIT_SRC" ] && [ -f "$APP_DIR/$UNIT_SRC" ] && ! sudo cmp -s "$APP_DIR/$UNIT_SRC" "/etc/systemd/system/$SVC.service"; then
  sudo cp "$APP_DIR/$UNIT_SRC" "/etc/systemd/system/$SVC.service"
  sudo systemctl daemon-reload
  echo "[unit] synced + daemon-reload"
fi
sudo systemctl restart "$SVC"
sleep 4
echo "[active] $(systemctl is-active "$SVC")"
CODE=$(curl -s -o /dev/null -w "%{http_code}" "$URL" || echo 000)
echo "[http] $URL -> $CODE"
echo "[head] $(sudo git -C "$APP_DIR" log -1 --format="%h %s")"
systemctl is-active --quiet "$SVC" || { echo "[FAIL] $SVC not active"; sudo journalctl -u "$SVC" -n 25 --no-pager; exit 1; }
case "$CODE" in 200|302) echo "[OK] $SVC active + http $CODE" ;; *) echo "[FAIL] health $CODE"; sudo journalctl -u "$SVC" -n 25 --no-pager; exit 1 ;; esac
'

deploy_remote() {
  local app_dir="$1" svc="$2" url="$3" unit_src="${4:-}"
  if [[ "$DRYRUN" == "1" ]]; then
    echo "[dry-run] ssh $SSH_ALIAS  (APP_DIR=$app_dir SVC=$svc URL=$url UNIT_SRC=${unit_src:-<none>})"
    echo "$REMOTE_DEPLOY" | sed 's/^/    | /'
    return 0
  fi
  ssh "$SSH_ALIAS" "APP_DIR='$app_dir' SVC='$svc' URL='$url' UNIT_SRC='$unit_src' bash -s" <<< "$REMOTE_DEPLOY"
}

cd "$REPO_ROOT"

# ---- 1. 提交代码（脏树时按 -m 自动 commit）----
log "1/4 提交代码 (env=$ENV, dry-run=$DRYRUN, branch=$(git rev-parse --abbrev-ref HEAD))"
if [[ -n "$(git status --porcelain)" ]]; then
  if [[ -n "$COMMIT_MSG" ]]; then
    log "工作区有改动，自动提交: $COMMIT_MSG"
    if [[ "$DRYRUN" == "1" ]]; then echo "[dry-run] git add -A && git commit -m \"$COMMIT_MSG\""
    else git add -A && git commit -q -m "$COMMIT_MSG"; fi
  elif [[ "$DRYRUN" == "1" ]]; then
    log "注意(dry-run): 工作区脏，真实发布需 -m '<msg>' 自动提交或先手动 commit"
  else
    echo "[ERROR] 工作区有未提交改动：加 -m '<msg>' 自动提交，或先手动 commit" >&2
    git status --short >&2; exit 1
  fi
else
  log "工作区干净，无需提交"
fi

# ---- 2. 合并代码到 master：fetch + rebase 防回退 + 推送 ----
log "2/4 安全合并 origin/master 并推送（fetch+rebase 防回退）"
if [[ "$DRYRUN" == "1" ]]; then
  echo "[dry-run] git fetch origin master; 若落后/分叉则 git rebase origin/master; git push origin HEAD:master"
else
  GIT_SSH_COMMAND="$GIT_PUSH_SSH" git fetch origin master
  if ! git merge-base --is-ancestor origin/master HEAD; then
    log "本地落后/分叉于 origin/master，rebase 中..."
    git rebase origin/master || { echo "[ERROR] rebase 冲突，请手动解决后重发" >&2; exit 1; }
  fi
  GIT_SSH_COMMAND="$GIT_PUSH_SSH" git push origin HEAD:master
fi

# ---- 3. 发测试 :8080 ----
log "3/4 发测试 autovideosrt-test (:8080)"
deploy_remote "/opt/autovideosrt-test" "autovideosrt-test" "http://127.0.0.1:8080/"

# ---- 4. 生产闸门 + 发生产 :80 ----
if [[ "$ENV" == "test" ]]; then
  log "✅ 完成：仅测试环境已更新。"
  exit 0
fi
if [[ "$CONFIRM" != "1" ]]; then
  log "测试已通过。确认上线生产请重跑：  bash deploy/publish_ccx7.sh prod --confirm"
  exit 0
fi
log "4/4 发生产 autovideosrt (:80)"
if ! deploy_remote "/opt/autovideosrt" "autovideosrt" "http://127.0.0.1/" "deploy/autovideosrt.service"; then
  log "❌ 生产发布失败。回滚参考: ssh avsl 'sudo git -C /opt/autovideosrt reset --hard HEAD~1 && sudo systemctl restart autovideosrt'"
  exit 1
fi
log "✅ 生产上线完成：http://172.16.254.106/"
