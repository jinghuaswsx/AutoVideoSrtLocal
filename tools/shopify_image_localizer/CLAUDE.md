# tools/shopify_image_localizer/

Shopify Image Localizer 是 Windows EXE 桌面工具，GUI + Playwright/CDP 自动替换 Shopify 图片。这个模块的发布事故成本很高，动手前先读本文件。

## Commands
- 开发运行：`python -m tools.shopify_image_localizer.main`
- 打包发布前必须读：`docs/shopify-image-localizer-exe-release-standard.md`
- Windows 本机 fallback 打包：`python -m tools.shopify_image_localizer.build_exe --release-standard-read --version <ver>`
- 改代码后至少跑：`pytest tests/test_shopify_image_localizer_build_exe.py tests/test_shopify_image_localizer_domains.py tests/test_shopify_image_localizer_batch_cdp.py tests/test_shopify_image_localizer_gui.py -q`

## EXE 发布门禁
- 打包唯一规范：`docs/shopify-image-localizer-exe-release-standard.md`；不读规范、不传 `--release-standard-read`，打包入口必须失败。
- 只能在普通 `master` checkout 打包，禁止在 worktree/临时分支打包；`HEAD` 必须等于 `origin/master`，tracked 工作区必须干净。
- `tools/shopify_image_localizer/version.py` 的 `RELEASE_VERSION` 必须等于 `--version`，版本 bump 必须先 commit 到 master。
- V4.5 事故：发布包配置被写成 `demo-key`，随后又因 PowerShell UTF-8 BOM 让 Python `utf-8` 读 JSON 失败，exe 运行时认为 OpenAPI Key 为空。不要再重复。
- 服务端鉴权读的是 DB：`llm_provider_configs.provider_code='openapi_materials'` 的 `api_key`，不是仓库 `.env` 的 `OPENAPI_MEDIA_API_KEY`。
- 取当前生效 key 必须走服务器 venv：`ssh -i C:\Users\admin\.ssh\CC.pem root@172.16.254.106 "cd /opt/autovideosrt && ./venv/bin/python - <<'PY' ... get_provider_config('openapi_materials') ... PY"`。
- 打包前设置 `SHOPIFY_IMAGE_LOCALIZER_API_KEY` 为上一步 key；禁止空值、`demo-key`、`changeme`、`your-api-key` 进入包。
- 发布目录根上的 `shopify_image_localizer_config.json` 和 `shopify_image_localizer_default_config.json` 必须同时存在，且 `api_key` 相同、非空、非占位；`browser_user_data_dir` 必须是 `C:\chrome-shopify-image`。
- 2026-05-22 复发记忆：新打包 EXE 若又弹“高级设置里的 OpenAPI Key 和 Chrome 用户目录不能为空”，默认按发布包配置事故处理，不要让用户手填高级设置兜底；立刻解压线上 zip 和用户运行目录，核对两份 config 的 `api_key` / `browser_user_data_dir`、BOM、zip 是否重建。
- JSON 写文件必须 UTF-8 无 BOM；代码读取已用 `utf-8-sig` 兼容旧包，但发布验证仍要确认 `HasBom=False`。
- 验证鉴权只能用受保护的 bootstrap：`POST http://172.16.254.106/openapi/medias/shopify-image-localizer/bootstrap`，用发布包 config 的 `X-API-Key` 必须 HTTP 200；用 `demo-key` 必须 HTTP 401。`languages` / `domains` 是公开接口，不能当 key 验证。
- zip 也要验：`ShopifyImageLocalizer-portable-<ver>.zip` 里的两份 config 必须和目录内一致，不能只修目录不重建 zip。
- 如果修改了已启动 exe 的配置文件，必须重启 exe 才生效；除非用户明确要求，不要替用户输入商品 ID、不要点击“开始替换”。
- 同版本目录或 zip 已存在时不要覆盖旧版；升 `version.py` 和 `--version`。

## 登录按钮
- 程序启动 / “登录店铺”都走同一个 CDP Chrome profile：第一个 tab 固定 `https://www.google.com`，Shopify admin 登录页只开在后续 tab。
- “已登录”按钮才抓 slug：优先通过随 Chrome 启动的本地扩展桥接读取当前浏览器标签页 URL；扩展桥未连接时，只允许从当前 CDP 浏览器 `/json` 实时 tab 列表兜底，并确认 CDP profile 匹配当前域名 profile、优先匹配当前域名页面标题；提取 slug 写入 `shopify_domain_store_slugs`。不要读 `Default/History` 兜底，History 会滞后导致旧 slug。
- 自动读取仍失败时，报错弹窗必须在同一个窗口里提供 Shopify admin URL 手动输入/保存入口，粘贴 `https://admin.shopify.com/store/<slug>/...` 后解析并缓存，不要让用户只能反复点“已登录”。
- 不要恢复 daemon thread 自动监听 slug；启动太早会抓旧 URL。

## EZ/CDP 等待
- 等 EZ iframe、弹窗关闭、上传后 UI 刷新，必须保留 Playwright 页面级等待：`page.wait_for_timeout(...)` 或 locator wait。
- 不要把这些等待替换成 `time.sleep` / `cancellation.cancellable_sleep`。取消逻辑只在等待前后插 `cancellation.throw_if_cancelled(...)`。
- `ensure_cdp_chrome` 复用 7777 前必须确认端口 Chrome 的 `--user-data-dir` 等于目标 profile；如果是旧 `C:/chrome`，必须杀掉重启。

## 批量任务
- 每个语言必须在独立 `threading.Thread` 里跑，避免 Playwright Sync API 污染同线程 asyncio running loop。
- 批量多语言默认复用同一个 Chrome/CDP 会话；只有浏览器窗口被关、CDP 端口不可用、端口 profile 不匹配或任务异常恢复时才允许重启/清理 Chrome。
- 轮播图 slot 已有目标语言标签时跳过，不要 remove 再上传。

## TAA 详情图
- 详情图成功以当前 TAA 会话上传、保存、读 HTML 为准。
- 保存后 reload 验证只作诊断；`127.0.0.1:7777` refused 不能把已保存成功的任务判失败。

## Linux Wine 发布
- 默认脚本：`bash scripts/build_shopify_image_localizer_wine.sh --release-standard-read --version <ver> [--release-note "..."]`。
- Wine 必须 >= 11，Windows Python 必须 >= 3.12.10，build 必须 `xvfb-run`；不要用 `wine ./exe` 当 smoke。
- helper 做 build EXE + zip、复制到下载目录、写 release JSON、curl 探测；不重启服务，不自动 commit/push。

## 前端下载入口
- 素材管理“下载自动换图工具”必须读 `system_settings.shopify_image_localizer_release` JSON 的 `version/released_at/release_note/download_url/filename`。
- 不要把版本号或 zip 文件名硬编码进模板；回滚只改 release JSON 指回旧 zip。
