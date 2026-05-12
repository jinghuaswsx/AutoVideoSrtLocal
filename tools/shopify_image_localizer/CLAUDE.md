# tools/shopify_image_localizer/

Shopify Image Localizer 是 Windows EXE 桌面工具，GUI + Playwright/CDP 自动替换 Shopify 图片。这个模块的发布事故成本很高，动手前先读本文件。

## Commands
- 开发运行：`python -m tools.shopify_image_localizer.main`
- Windows 本机打包：`python -m tools.shopify_image_localizer.build_exe --version <ver>`
- 改代码后至少跑：`pytest tests/test_shopify_image_localizer_build_exe.py tests/test_shopify_image_localizer_domains.py tests/test_shopify_image_localizer_batch_cdp.py tests/test_shopify_image_localizer_gui.py -q`

## EXE 发布门禁
- V4.5 事故：发布包配置被写成 `demo-key`，随后又因 PowerShell UTF-8 BOM 让 Python `utf-8` 读 JSON 失败，exe 运行时认为 OpenAPI Key 为空。不要再重复。
- 服务端鉴权读的是 DB：`llm_provider_configs.provider_code='openapi_materials'` 的 `api_key`，不是仓库 `.env` 的 `OPENAPI_MEDIA_API_KEY`。
- 取当前生效 key 必须走服务器 venv：`ssh -i C:\Users\admin\.ssh\CC.pem root@172.30.254.14 "cd /opt/autovideosrt && ./venv/bin/python - <<'PY' ... get_provider_config('openapi_materials') ... PY"`。
- 打包前设置 `SHOPIFY_IMAGE_LOCALIZER_API_KEY` 为上一步 key；禁止空值、`demo-key`、`changeme`、`your-api-key` 进入包。
- 发布目录根上的 `shopify_image_localizer_config.json` 和 `shopify_image_localizer_default_config.json` 必须同时存在，且 `api_key` 相同、非空、非占位；`browser_user_data_dir` 必须是 `C:\chrome-shopify-image`。
- JSON 写文件必须 UTF-8 无 BOM；代码读取已用 `utf-8-sig` 兼容旧包，但发布验证仍要确认 `HasBom=False`。
- 验证鉴权只能用受保护的 bootstrap：`POST http://172.30.254.14/openapi/medias/shopify-image-localizer/bootstrap`，用发布包 config 的 `X-API-Key` 必须 HTTP 200；用 `demo-key` 必须 HTTP 401。`languages` / `domains` 是公开接口，不能当 key 验证。
- zip 也要验：`ShopifyImageLocalizer-portable-<ver>.zip` 里的两份 config 必须和目录内一致，不能只修目录不重建 zip。
- 如果修改了已启动 exe 的配置文件，必须重启 exe 才生效；除非用户明确要求，不要替用户输入商品 ID、不要点击“开始替换”。
- 同版本目录或 zip 已存在时不要覆盖旧版；升 `version.py` 和 `--version`。

## 登录按钮
- “登录店铺”只打开 `https://admin.shopify.com/`，不用 CDP，不带 `/store/<slug>/`，让用户手动登录并选择店铺。
- “已登录”按钮才抓 slug：读 Chrome `Default/History` 中最新 admin store URL，提取 slug 写入 `shopify_domain_store_slugs`。
- 不要恢复 daemon thread 自动监听 slug；启动太早会抓旧 URL。

## EZ/CDP 等待
- 等 EZ iframe、弹窗关闭、上传后 UI 刷新，必须保留 Playwright 页面级等待：`page.wait_for_timeout(...)` 或 locator wait。
- 不要把这些等待替换成 `time.sleep` / `cancellation.cancellable_sleep`。取消逻辑只在等待前后插 `cancellation.throw_if_cancelled(...)`。
- `ensure_cdp_chrome` 复用 7777 前必须确认端口 Chrome 的 `--user-data-dir` 等于目标 profile；如果是旧 `C:/chrome`，必须杀掉重启。

## 批量任务
- 每个语言必须在独立 `threading.Thread` 里跑，避免 Playwright Sync API 污染同线程 asyncio running loop。
- 每个语言开始前清理一次对应 Chrome profile，避免前一个语言崩溃后复用坏 CDP。
- 轮播图 slot 已有目标语言标签时跳过，不要 remove 再上传。

## TAA 详情图
- 详情图成功以当前 TAA 会话上传、保存、读 HTML 为准。
- 保存后 reload 验证只作诊断；`127.0.0.1:7777` refused 不能把已保存成功的任务判失败。

## Linux Wine 发布
- 默认脚本：`bash scripts/build_shopify_image_localizer_wine.sh --version <ver> [--release-note "..."]`。
- Wine 必须 >= 11，Windows Python 必须 >= 3.12.10，build 必须 `xvfb-run`；不要用 `wine ./exe` 当 smoke。
- helper 做 build EXE + zip、复制到下载目录、写 release JSON、curl 探测；不重启服务，不自动 commit/push。

## 前端下载入口
- 素材管理“下载自动换图工具”必须读 `system_settings.shopify_image_localizer_release` JSON 的 `version/released_at/release_note/download_url/filename`。
- 不要把版本号或 zip 文件名硬编码进模板；回滚只改 release JSON 指回旧 zip。
