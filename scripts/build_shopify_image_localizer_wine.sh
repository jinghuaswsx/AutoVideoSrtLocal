#!/usr/bin/env bash
# Linux Wine 打包发布 Shopify Image Localizer 桌面端 EXE。
#
# 详细设计：docs/superpowers/specs/2026-05-09-shopify-image-localizer-linux-wine-build-design.md
#
# 用法：
#   bash scripts/build_shopify_image_localizer_wine.sh \
#     --version 3.9 \
#     [--release-note "修复 EZ 提交按钮兼容性"]
#
# 这条命令完成：
#   1. 校验 Wine 11+ / Xvfb / Wine prefix / Windows Python 都齐
#   2. 校验 --version 不为空、且服务器下载目录里没同名 zip
#   3. 在 Wine 下跑 build_exe.py 出 portable zip
#   4. sudo cp zip 到 /opt/autovideosrt/web/static/downloads/tools/
#   5. 调 appcore.shopify_image_localizer_release.set_release_info(...) 写 DB
#   6. curl 探测 web 静态可达
#
# 不重启 web 服务、不 git commit/push。任何一步失败立刻停下并打印现状。

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WINEPREFIX_DEFAULT="/home/cjh/wine-shopify-build"
WINE_PYTHON_DEFAULT='C:\Python312\python.exe'
DOWNLOADS_DIR="/opt/autovideosrt/web/static/downloads/tools"
PROD_BASE_URL="http://127.0.0.1"

VERSION=""
RELEASE_NOTE=""
WINEPREFIX_PATH="${WINEPREFIX:-$WINEPREFIX_DEFAULT}"
WINE_PYTHON="${WINE_PYTHON:-$WINE_PYTHON_DEFAULT}"

usage() {
  cat <<'USAGE'
用法: bash scripts/build_shopify_image_localizer_wine.sh \
  --version <ver> [--release-note "..."] [--prefix /path/to/wine-prefix] [--wine-python 'C:\path\python.exe']

必填:
  --version <ver>          发布版本号（不带前缀 v）；同版本目录或 zip 已存在时直接退出。

可选:
  --release-note "..."     写入 system_settings.shopify_image_localizer_release.release_note。
  --prefix <path>          Wine prefix 路径，默认 /home/cjh/wine-shopify-build。
  --wine-python <path>     Wine 内 python.exe 的 Windows 路径，默认 C:\Python312\python.exe。
USAGE
}

err() { printf '\033[31m[error]\033[0m %s\n' "$*" >&2; }
info() { printf '\033[36m[info ]\033[0m %s\n' "$*"; }
ok()  { printf '\033[32m[ok   ]\033[0m %s\n' "$*"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) VERSION="${2:-}"; shift 2 ;;
    --release-note) RELEASE_NOTE="${2:-}"; shift 2 ;;
    --prefix) WINEPREFIX_PATH="${2:-}"; shift 2 ;;
    --wine-python) WINE_PYTHON="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) err "未识别的参数：$1"; usage; exit 2 ;;
  esac
done

if [[ -z "$VERSION" ]]; then
  err "--version 必须传"
  usage; exit 2
fi
if [[ "$VERSION" =~ [\\/:\*\?\"\<\>\|] ]]; then
  err "版本号 '$VERSION' 含非法文件名字符"
  exit 2
fi

# 1. prereq 校验
command -v wine >/dev/null 2>&1 || { err "wine 未安装。按 spec '首次环境初始化' 装 WineHQ stable 11.0+。"; exit 1; }
command -v xvfb-run >/dev/null 2>&1 || { err "xvfb-run 未安装。sudo apt install xvfb"; exit 1; }
command -v sudo >/dev/null 2>&1 || { err "sudo 未安装"; exit 1; }
command -v curl >/dev/null 2>&1 || { err "curl 未安装"; exit 1; }
command -v python3 >/dev/null 2>&1 || { err "python3 未安装（DB 写入步骤需要本机 python）"; exit 1; }

WINE_VERSION="$(wine --version 2>&1 | head -1 || true)"
case "$WINE_VERSION" in
  wine-1[1-9].*|wine-[2-9][0-9].*) ok "wine 版本: $WINE_VERSION" ;;
  *) err "wine 版本 '$WINE_VERSION' < 11.0；Ubuntu 自带 9.0 缺 ucrtbase.crealf。换 WineHQ stable 11+。"; exit 1 ;;
esac

if [[ ! -d "$WINEPREFIX_PATH" ]]; then
  err "Wine prefix 不存在: $WINEPREFIX_PATH。按 spec '首次环境初始化' 跑 wineboot --init。"
  exit 1
fi
ok "wine prefix: $WINEPREFIX_PATH"

# Windows 路径里反斜杠转 Unix 路径校验 python.exe 存在
WINE_PYTHON_LINUX="$WINEPREFIX_PATH/drive_c$(printf '%s' "$WINE_PYTHON" | sed -e 's|^[A-Za-z]:||' -e 's|\\|/|g')"
if [[ ! -f "$WINE_PYTHON_LINUX" ]]; then
  err "Wine Python 不存在: $WINE_PYTHON ($WINE_PYTHON_LINUX)。按 spec 装 Python 3.12.10+ 进 prefix。"
  exit 1
fi
ok "wine python: $WINE_PYTHON"

# 2. 同版本占位校验
ZIP_FILENAME="ShopifyImageLocalizer-portable-${VERSION}.zip"
TARGET_ZIP="${DOWNLOADS_DIR}/${ZIP_FILENAME}"
if [[ -f "$TARGET_ZIP" ]]; then
  err "目标 zip 已存在：$TARGET_ZIP（不要覆盖旧版本，请用更高 --version）。"
  exit 1
fi

OUTPUT_ROOT="${HOME}/shopify-builds"
RELEASE_DIR="${OUTPUT_ROOT}/ShopifyImageLocalizer-${VERSION}"
PORTABLE_ZIP="${OUTPUT_ROOT}/${ZIP_FILENAME}"
if [[ -d "$RELEASE_DIR" || -f "$PORTABLE_ZIP" ]]; then
  err "本地产物已存在（$RELEASE_DIR 或 $PORTABLE_ZIP），换更高 --version 或先手工清理。"
  exit 1
fi

mkdir -p "$OUTPUT_ROOT"
ok "output root: $OUTPUT_ROOT"

# 3. Wine + xvfb-run 跑 build_exe
info "开始打包（约 1-2 分钟）..."
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/tmp/xdg-runtime-${USER}}"
mkdir -p "$XDG_RUNTIME_DIR"

# 把 Linux 路径 $OUTPUT_ROOT 翻译成 Wine Z: 风格的 Windows 路径
WIN_OUTPUT_ROOT="Z:$(printf '%s' "$OUTPUT_ROOT" | sed 's|/|\\|g')"

cd "$REPO_ROOT"
xvfb-run --auto-servernum env \
  WINEPREFIX="$WINEPREFIX_PATH" \
  WINEARCH=win64 \
  WINEDEBUG=-all \
  XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" \
  wine "$WINE_PYTHON" -m tools.shopify_image_localizer.build_exe \
    --version "$VERSION" \
    --output-root "$WIN_OUTPUT_ROOT"

if [[ ! -f "$PORTABLE_ZIP" ]]; then
  err "打包结束但 zip 缺失：$PORTABLE_ZIP"
  exit 1
fi
ZIP_SIZE_BYTES="$(stat -c %s "$PORTABLE_ZIP")"
ZIP_SIZE_MB=$(( ZIP_SIZE_BYTES / 1024 / 1024 ))
ok "zip 已生成：$PORTABLE_ZIP（${ZIP_SIZE_MB} MB）"
if (( ZIP_SIZE_MB < 50 )); then
  err "zip 大小异常（${ZIP_SIZE_MB} MB），预期 ~100+ MB；可能依赖打包不全。"
  exit 1
fi

# 4. sudo cp 到下载目录
info "拷贝到下载目录: $TARGET_ZIP"
sudo cp "$PORTABLE_ZIP" "$TARGET_ZIP"
sudo chmod 644 "$TARGET_ZIP"
ok "已上传"

# 5. 写 system_settings.shopify_image_localizer_release
DOWNLOAD_URL="/static/downloads/tools/${ZIP_FILENAME}"
RELEASED_AT="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"

info "写入 DB system_settings.shopify_image_localizer_release"
RELEASE_NOTE_ENV="$RELEASE_NOTE" \
  VERSION_ENV="$VERSION" \
  RELEASED_AT_ENV="$RELEASED_AT" \
  DOWNLOAD_URL_ENV="$DOWNLOAD_URL" \
  FILENAME_ENV="$ZIP_FILENAME" \
  python3 - <<'PYEOF'
import os, sys
sys.path.insert(0, os.environ.get("REPO_ROOT", "."))
# 通过 prod 部署目录里那一份的 .env 读 DB 凭据；worktree 没自己的 .env 也能跑通
import os.path as p
prod_env = "/opt/autovideosrt/.env"
if p.isfile(prod_env):
    with open(prod_env, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

from appcore import shopify_image_localizer_release as r
result = r.set_release_info(
    version=os.environ["VERSION_ENV"],
    released_at=os.environ["RELEASED_AT_ENV"],
    download_url=os.environ["DOWNLOAD_URL_ENV"],
    release_note=os.environ.get("RELEASE_NOTE_ENV", ""),
    filename=os.environ["FILENAME_ENV"],
)
print("DB updated:", result)
PYEOF
ok "DB 已更新"

# 6. HTTP 自检（206 / 200 都可，因为我们带 Range 头）
info "探测 ${PROD_BASE_URL}${DOWNLOAD_URL}"
HTTP_CODE="$(curl -s -o /dev/null -w '%{http_code}' --range 0-99 "${PROD_BASE_URL}${DOWNLOAD_URL}")" || HTTP_CODE="000"
case "$HTTP_CODE" in
  200|206) ok "HTTP $HTTP_CODE — 静态可达" ;;
  *) err "HTTP $HTTP_CODE — 静态不可达，检查 $TARGET_ZIP 权限 / web server"; exit 1 ;;
esac

cat <<RESULT

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ 发布完成

  版本：           $VERSION
  本地 zip：       $PORTABLE_ZIP
  线上 zip：       $TARGET_ZIP
  下载 URL：       ${PROD_BASE_URL}${DOWNLOAD_URL}
  released_at：    $RELEASED_AT
  release_note：   ${RELEASE_NOTE:-（空）}

下一步：
  - 在素材管理页确认「下载自动换图工具」按钮已切到 v${VERSION}。
  - 如需 push 代码改动到 master，走 CLAUDE.md 里的标准发布流程；本脚本不动 git 与 systemd。
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RESULT
