# Shopify Image Localizer Linux Wine 打包发布

- **创建时间**：2026-05-09
- **状态**：active
- **承接**：[`AutoVideoSrtLocal/CLAUDE.md` "Shopify Image Localizer 发布打包"](../../../CLAUDE.md)、[`tools/shopify_image_localizer/build_exe.py`](../../../tools/shopify_image_localizer/build_exe.py)

## 背景

Shopify Image Localizer 桌面端历来由 admin 在 Windows 开发机上跑 `python -m tools.shopify_image_localizer.build_exe --version <ver>` 生成 EXE，再手工上传到 `/opt/autovideosrt/web/static/downloads/tools/`、写 `system_settings.shopify_image_localizer_release` JSON。每发版要切上下文到 Windows 机器，且 prod server 上的 Claude Code agent 无法独立闭环。

AUT-18 决定把发布能力前移到 prod server（Ubuntu 24.04 / `172.30.254.14`）：

1. 用 Wine 跑 Windows Python 3.12 + PyInstaller 出 EXE；
2. 桥接到现有 `build_exe.py` 不重写打包逻辑；
3. helper 脚本一条命令完成 build → 拷贝到下载目录 → 写 DB → 自检；
4. Windows 旧路径完全保留，作为 fallback。

可行性已通过 v3.2-wine-spike 在线上跑通，admin 在 Windows 真机已确认 wine-built EXE 可用（AUT-18 评论 "可以用"，2026-05-09T09:32 UTC）。

## 设计

### 工具链拓扑

| 组件 | 版本 / 路径 | 说明 |
|------|------------|------|
| Wine | **WineHQ stable 11.0**（`deb https://dl.winehq.org/wine-builds/ubuntu noble main`） | Ubuntu 自带 9.0 不行：`ucrtbase.dll` 没实现 `crealf`，numpy import 会撞 `unimplemented function`。 |
| Wine prefix | `/home/cjh/wine-shopify-build/`（`WINEARCH=win64`） | 隔离 prefix，不污染默认 `~/.wine`；失败可 `rm -rf` 整目录回滚。 |
| Windows Python | **3.12.10**（amd64，`C:\Python312`） | 必须 ≥ 3.12.10。3.12.7 自带 Tcl 8.6.13，PyInstaller bundle 后跑 EXE 撞 `_tkinter.TclError: Can't find a usable init.tcl`；3.12.10 升到 Tcl 8.6.15 才行。 |
| pip 依赖 | `pyinstaller`、`requests`、`playwright`、`numpy>=1.24`、`scikit-image`、`Pillow`、`ImageHash`、`websocket-client` | 与 [`requirements.txt`](../../../requirements.txt) / [`requirements-browser.txt`](../../../requirements-browser.txt) 中实际被打包路径用到的子集对齐。新增桌面端依赖时同步补这里。 |
| Xvfb | `xvfb-run --auto-servernum` | PyInstaller 的 WiX bootloader 必须能创建窗口才能跑（headless wine 直跑会撞 `Failed to create main window 0x800736b7`）。 |
| 系统额外包 | `xvfb`, `cabextract`, `winbind`, `wine32:i386`（`dpkg --add-architecture i386` 后） | wine32 只在 Python installer bootstrapper 阶段需要；之后纯 64-bit 跑。 |

### 产物路径

| 路径 | 内容 |
|------|------|
| `~/shopify-builds/_build/` | PyInstaller 中间产物（每次 build 前清空）。 |
| `~/shopify-builds/ShopifyImageLocalizer/` | PyInstaller `--distpath` 中间目录。 |
| `~/shopify-builds/ShopifyImageLocalizer-<version>/` | 发布目录（含 `ShopifyImageLocalizer.exe`、`_internal/`、`shopify_image_localizer_config.json`、`run_shopify_image_localizer.bat`、`release_version.txt`）。 |
| `~/shopify-builds/ShopifyImageLocalizer-portable-<version>.zip` | 绿色包，~105 MB。 |

### `build_exe.py` 的最小改动

[`tools/shopify_image_localizer/build_exe.py`](../../../tools/shopify_image_localizer/build_exe.py) 当前默认 `--output-root = G:\ShopifyRelease`（Windows 路径）。本 spec 只改默认值的 platform-aware 行为，保留 Windows 旧默认：

```python
DEFAULT_OUTPUT_ROOT_WINDOWS = Path(r"G:\ShopifyRelease")
DEFAULT_OUTPUT_ROOT_POSIX = Path.home() / "shopify-builds"

def _default_output_root() -> Path:
    return DEFAULT_OUTPUT_ROOT_WINDOWS if os.name == "nt" else DEFAULT_OUTPUT_ROOT_POSIX
```

`argparse` 的 `--output-root` 默认改成 `str(_default_output_root())`；显式传 `--output-root` 仍然覆盖。所有 PyInstaller 调用 / 产物路径 / FileExistsError 校验逻辑保持不变。

### Helper 脚本

新文件：[`scripts/build_shopify_image_localizer_wine.sh`](../../../scripts/build_shopify_image_localizer_wine.sh)。

职责（一条命令做完）：

1. 校验 prereq（`wine`/`xvfb-run`/Wine prefix/`C:\Python312\python.exe` 都齐）；缺哪条直接退出并打印修复指引。
2. 校验 `--version <ver>` 不为空、且服务器下载目录里没同名 zip（避免覆盖旧版）。
3. 在 Wine 下跑 `python -m tools.shopify_image_localizer.build_exe --version <ver>` 出 zip。
4. `sudo cp` zip 到 `/opt/autovideosrt/web/static/downloads/tools/ShopifyImageLocalizer-portable-<ver>.zip`，`chmod 644`。
5. 跑一段嵌入式 Python，调 `appcore.shopify_image_localizer_release.set_release_info(version=..., released_at=now_utc, download_url=..., release_note=..., filename=...)` 写 DB（`release_note` 由 `--release-note` 显式指定或留空）。
6. `curl --range 0-99` 探测 `http://127.0.0.1/static/downloads/tools/<zip>` 应回 HTTP 200/206，校验 web 静态层能读到。
7. 全程 `set -euo pipefail`，任何一步失败就停下并把现状打印出来。

约束：

- **不重启 web 服务**——发布静态包 + 改 `system_settings` 不需要 restart。如果用户随后说 "线上发布"，再走标准流程。
- **不 git push、不 commit**——helper 只管 build + 上线静态产物 + 写 DB 配置。代码修改流程跟 helper 解耦。
- **不传 token / 凭据 / 环境变量给 Wine 子进程**——build 是离线打包，不需要业务凭据。
- helper 的失败回滚：拷 zip 失败 / DB 写入失败时，**不**自动删 zip（保留供人工排查），但不写 DB → web 前端继续指向旧版本。

### CLAUDE.md / AGENTS.md 更新

[`AutoVideoSrtLocal/CLAUDE.md`](../../../CLAUDE.md) 与 [`AutoVideoSrtLocal/AGENTS.md`](../../../AGENTS.md) "Shopify Image Localizer 发布打包" 小节并列两条流程：

- **路径 A — Linux 服务器（默认）**：`bash scripts/build_shopify_image_localizer_wine.sh --version <ver> [--release-note "..."]`，前提是 prefix 已经初始化好（首次按本 spec "首次环境初始化" 一次性装；之后只跑 helper）。
- **路径 B — Windows 开发机（fallback）**：原有 `python -m tools.shopify_image_localizer.build_exe --version <ver>` 流程，配上手工 `scp` + UI 改 release JSON。

### 首次环境初始化（一次性）

执行节点：prod server `cjh` 用户。

```bash
sudo dpkg --add-architecture i386
sudo apt-get update
sudo mkdir -p /etc/apt/keyrings
curl -fsSL https://dl.winehq.org/wine-builds/winehq.key | sudo tee /etc/apt/keyrings/winehq-archive.key > /dev/null
curl -fsSL https://dl.winehq.org/wine-builds/ubuntu/dists/noble/winehq-noble.sources | sudo tee /etc/apt/sources.list.d/winehq-noble.sources > /dev/null
sudo apt-get update
sudo apt-get install -y --install-recommends winehq-stable
sudo apt-get install -y --no-install-recommends xvfb cabextract winbind

mkdir -p /home/cjh/wine-shopify-build
WINEPREFIX=/home/cjh/wine-shopify-build WINEARCH=win64 wineboot --init

curl -fsSL -o /tmp/python-3.12.10-amd64.exe https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe
xvfb-run --auto-servernum env \
  WINEPREFIX=/home/cjh/wine-shopify-build WINEARCH=win64 \
  XDG_RUNTIME_DIR=/tmp/xdg-runtime-cjh \
  wine /tmp/python-3.12.10-amd64.exe /passive \
    InstallAllUsers=0 PrependPath=1 \
    Include_test=0 Include_doc=0 Include_launcher=0 Shortcuts=0 \
    TargetDir='C:\Python312'

xvfb-run --auto-servernum env \
  WINEPREFIX=/home/cjh/wine-shopify-build WINEARCH=win64 \
  XDG_RUNTIME_DIR=/tmp/xdg-runtime-cjh \
  wine 'C:\Python312\python.exe' -m pip install --upgrade pip

xvfb-run --auto-servernum env \
  WINEPREFIX=/home/cjh/wine-shopify-build WINEARCH=win64 \
  XDG_RUNTIME_DIR=/tmp/xdg-runtime-cjh \
  wine 'C:\Python312\python.exe' -m pip install \
    pyinstaller requests playwright 'numpy>=1.24' \
    scikit-image Pillow ImageHash websocket-client
```

跑完之后 `wine 'C:\Python312\python.exe' -c "import numpy, skimage, playwright; print('ok')"` 应成功。

## 已知坑

1. **Wine 9.0 → 11.0 必换**：Wine 9.0 (Ubuntu noble repo) `ucrtbase.dll` 缺 `crealf`，numpy import 直接挂。**只能用 WineHQ 11.0+。**
2. **Python 必须 ≥ 3.12.10**：3.12.7 / 3.12.8 自带 Tcl 8.6.13，PyInstaller bundle 后跑 EXE 撞 `_tkinter.TclError`，跟 init.tcl 的 `package require -exact Tcl 8.6.13` 失配。3.12.10 升到 Tcl 8.6.15 才行。
3. **缺 `api-ms-win-*.dll` 不致命**：Win 出的 v3.2.zip 113 MB 含 ~40 个 UCRT 转发桩；wine 出的 105 MB 不含这些。Windows 10+ 自带这些桩，缺了不影响运行；若要兼容 Win 7/8.1，需要在 Wine prefix 内 `winetricks vcrun2022` 装 MS UCRT，再让 PyInstaller bundle 时拷过去（当前不支持 Win 7/8.1 业务，省略）。
4. **xvfb 包出来的 Wine GUI 跑这版 EXE 时仍会撞 `_tkinter.TclError`**：这是 Wine + PyInstaller `runw.exe` GUI bootloader + Xvfb 这条特定路径的渲染初始化问题，不影响产物在真 Windows 上启动。判定 EXE 健康度时**不用** `wine ./ShopifyImageLocalizer.exe` 这个 smoke——拿到 zip 就上传到下载目录，由 Windows 真机做最终验收。
5. **`build_exe.py` 路径硬编码**：默认 `--output-root` 写死 `G:\ShopifyRelease`，本 spec 改成 platform-aware；如果以后又新增 Windows-only 默认值，应同样套 `os.name == "nt"` 分支。
6. **不要 vcrun2022 + wine 9.0 配**：踩过坑，winetricks `vcrun2022` 在线上拉到的 vc_redist.x64.exe 跟内置 SHA256 mismatch（MS 改过包），即便 `--unattended` 也只能跑 silent install；最终 ucrtbase 装上但 Wine 仍优先加载 builtin，crealf 报错没解。直接换 Wine 11 性价比最高。

## 验证

- 修改 `build_exe.py` 后：`pytest tests/test_shopify_image_localizer_gui.py -q`（不含 build pipeline 直接 unit）以及 `pytest tests/test_shopify_image_localizer_release_web.py tests/test_media_pages_service.py -q`（如果改了 `appcore.shopify_image_localizer_release` 周边）。
- helper 脚本端到端：`bash scripts/build_shopify_image_localizer_wine.sh --version <next-ver> --release-note "test"`，期望最后一行打印 `PROD HTTP 200/206`，DB 中 `system_settings.shopify_image_localizer_release` 的 `version` 字段更新。
- 真机验收：admin 在 Windows 下载并双击 `ShopifyImageLocalizer.exe`，GUI 起来 + 跑一次 `打开 EZ 页面` / `开始替换` 链路。

## 全局复用

本 spec 同时落到全局技能档案：

- `~/.claude/skills/wine-windows-exe-packaging/`（reusable skill，跨项目）
- `~/.claude/CLAUDE.md` 与 `~/.claude/AGENTS.md` 各加一条「Linux Wine 出 Windows EXE」入口指引。

未来其他项目要在 Linux 上出 Windows EXE，按全局 skill 走相同的 Wine 11 + Python 3.12.10+ + PyInstaller + xvfb-run 模板，只替换业务侧 `build_exe.py` 等价物即可。
