# tools/shopify_image_localizer/

Shopify Image Localizer：自动换图工具（GUI + Playwright/CDP 在 EZ Product Image Translate / Translate & Adapt 上跑）。

## Commands
- 开发运行：`python -m tools.shopify_image_localizer.main`
- 语法自检：
  ```bash
  python -c 'import py_compile; [py_compile.compile(p, doraise=True) for p in ["tools/shopify_image_localizer/main.py","tools/shopify_image_localizer/gui.py","tools/shopify_image_localizer/controller.py","tools/shopify_image_localizer/browser/orchestrator.py"]]; print("ok")'
  ```
- 改代码后至少跑：`pytest tests/test_shopify_image_localizer_batch_cdp.py tests/test_shopify_image_localizer_gui.py -q`

## EZ/CDP 回归防护

**已知事故 2026-04-25**：调整"停止"按钮时把 EZ 等待循环里的 Playwright `page.wait_for_timeout(...)` 改成了 `cancellation.cancellable_sleep(...)` → Playwright/CDP 同步 API 不再持续刷新页面事件和 frame 列表 → `_wait_plugin_frame` 长时间看不到 iframe，"开始替换"卡死，点击"停止"才唤醒。

- **禁止回改**：`tools/shopify_image_localizer/rpa/ez_cdp.py` 中等待 EZ iframe / 弹窗关闭 / 上传后 UI 刷新的所有点，必须保留 Playwright 页面级等待（`page.wait_for_timeout(...)` 或 frame/page locator wait）。**不要**用 `time.sleep` / `cancellation.cancellable_sleep` 替代。
- 支持停止只能在 Playwright 等待**前后**插入 `cancellation.throw_if_cancelled(...)`。

## 登录按钮（2026-05-09 事故修订）

- **「登录shopify店铺」按钮只打开 Shopify 主入口** `https://admin.shopify.com/`（不带 `/store/<slug>/`），由用户登录后人工选择目标店铺。
- **Chrome 必须用普通模式 `session.start_chrome` 启动（无 `--remote-debugging-port`）**：admin.shopify.com 主入口前置 Cloudflare 反 bot 检测，CDP 标志会让人机验证码长时间不出现，用户根本登不进去。
- slug 抓取走「**已登录**」按钮（`confirm_shopify_login_capture_slug`）：用户登录到目标店铺主页后**手动**点这个按钮，程序读 `Default/History` SQLite（`mode=ro&immutable=1`）取 `last_visit_time DESC LIMIT 1` 的 admin store URL，提取 slug 写入 `shopify_image_localizer_config.json` 的 `shopify_domain_store_slugs`。**不要**回到 daemon thread 自动监听——thread 启动太早会抓到旧 URL（如硬编码 slug 时代留下的 `/store/omurio/products`）。
- 后续 `settings.shopify_store_slug_for_domain(domain)` 优先用缓存，缺失才回退内置 dict / 默认 slug。**不要**在登录按钮里加 CDP 端口、硬编码 store slug、或直接跳应用页（EZ / TAA），会绕过 slug 捕获或破坏 Cloudflare 验证。

## 轮播图 slot 跳过判定

EZ 替换前必须判断每个 slot 是否已经有目标语言标签（语言名称来自 `run_product_cdp.LANGUAGE_LABELS`，例如 `de -> German`）：
- 已有目标语言标签 → 结果 `skipped`，**不要**点击 `Remove {language}`、不删除重传，避免浪费时间和引入失败。

## TAA reload 校验降级（2026-05-11）

- 详情图成功口径以 TAA 当前会话上传、保存、读回 HTML 为准；保存后额外 reload 校验只作诊断。
- 若 reload 校验重连 `127.0.0.1:7777` 遇到 `WinError 10061` / connection refused，记录 `verify.reload_error`，**不要**把已保存成功的任务判失败。

## 批量 asyncio running loop 污染（2026-05-11 事故）

- **现象**：批量多语言，第 1 个成功，第 2+ 全报 `Playwright Sync API inside the asyncio loop`。
- **根因**：Playwright `SyncBase._sync()` 每次操作后调 `asyncio._set_running_loop(self._loop)` 但不清理。同一线程反复 `sync_playwright()` 后残留 loop 被检出。
- **修复**：`gui.py` `_run_batch` 中每个语言跑在独立 `threading.Thread` 里（daemon），利用线程级 `_running_loop` 天然隔离。**不要**用 `asyncio._set_running_loop(None)` 手动清（Python 3.14 上有副作用导致整个程序不跑）。
- **禁止回改**：不要在批量循环里通过杀 Chrome 来间接重置；不要在主线程/同一线程连续跑多个语言。

## Wine 打包发布

详细设计：`docs/superpowers/specs/2026-05-09-shopify-image-localizer-linux-wine-build-design.md`

### 路径 A — Linux 服务器（默认，2026-05-09 起）
本机 prod server 已按 spec「首次环境初始化」装好 Wine 11.0 + Windows Python 3.12.10 + PyInstaller，prefix 在 `/home/cjh/wine-shopify-build/`。

```bash
bash scripts/build_shopify_image_localizer_wine.sh --version <ver> [--release-note "..."]
```

helper 串完：build EXE + zip → `sudo cp` 到 `/opt/autovideosrt/web/static/downloads/tools/` → 调 `appcore.shopify_image_localizer_release.set_release_info(...)` 写 DB → curl 探测可达。**不重启服务、不 commit / push**。

- 同版本 zip / 目录已存在脚本会报错退出，**禁止覆盖旧版**；升 `--version` + `tools/shopify_image_localizer/version.py` 的 `RELEASE_VERSION`。
- 失败立刻停下打印现状；任何一步报错都不要绕过去。

### 路径 B — Windows 开发机（fallback）
`python -m tools.shopify_image_localizer.build_exe --version <ver>`，产物默认 `G:\ShopifyRelease\ShopifyImageLocalizer-portable-<ver>.zip`，之后手工 `scp` + UI 改 release JSON。

### 已知坑
- Wine 必须 ≥ 11.0（Ubuntu 9.0 缺 `ucrtbase.crealf`）
- Windows Python ≥ 3.12.10（3.12.7 自带 Tcl 8.6.13 让 EXE 撞 `_tkinter.TclError: Can't find a usable init.tcl`）
- 不要拿 `wine ./ShopifyImageLocalizer.exe` 当 smoke——init.tcl 报错跟产物在真 Windows 上是否能跑无关。EXE 健康度交给 Windows 真机验收。
- 不要在 Wine 9 上 `winetricks vcrun2022`（SHA256 mismatch + 优先加载 builtin ucrtbase）。

### 前端展示
素材管理「下载自动换图工具」按钮、版本号、发布时间必须读 `system_settings.shopify_image_localizer_release` JSON（字段：`version` / `released_at` / `release_note` / `download_url` / `filename`），**不要**硬编码到模板。回滚：UI 改这条 JSON 指回老版 zip 即可，老 zip 一直保留在下载目录。

## 默认配置不能为空（2026-05-11）

详细锚点：`docs/superpowers/specs/2026-05-11-shopify-image-localizer-runtime-config-release-guard.md`

- v3.18 事故：发布包内 `shopify_image_localizer_config.json` 的 `api_key` 为空，点击「开始替换」报「高级设置里的 OpenAPI Key 和 Chrome 用户目录不能为空」。
- 禁止把生产 OpenAPI key 写进源码 / 文档；`DEFAULT_API_KEY` 只能来自 `SHOPIFY_IMAGE_LOCALIZER_API_KEY`。
- 发布包必须同时带 runtime config 和 `shopify_image_localizer_default_config.json`；两份配置的 `api_key` / `browser_user_data_dir` 为空时 build/helper 必须失败。
- 运行时若用户保留旧空 runtime config，`load_runtime_config()` 从 default config 补值并直接写回 runtime config；不要通过 `save_runtime_config()` 自修复，避免递归。
