# Shopify Image Localizer Runtime Config Release Guard

日期：2026-05-11

## 背景

用户在 Shopify Image Localizer v3.18 点击「开始替换」后，弹窗报错：

`高级设置里的 OpenAPI Key 和 Chrome 用户目录不能为空`

该错误在多个发布版本里反复出现。2026-05-11 对线上下载目录核查发现：

- `ShopifyImageLocalizer-portable-3.18.zip` 内的 `shopify_image_localizer_config.json` 里 `api_key` 是空字符串。
- 历史包 `3.0`、`3.6` 也出现过同类空 key；`3.19`、`3.20` 当前包内 key 非空。
- v3.21 源码曾尝试把生产 key 写进 `settings.py` 默认值，但这违反仓库既有「生产 key 不进源码 / 文档」规则，也不能作为长期发布门禁。

2026-05-22 再次复发：用户在 Shopify Image Localizer v5.0 点击「开始替换」后又看到同一弹窗。以后看到这个症状，第一判断仍然是发布包或用户运行目录里的 runtime/default config 缺失、为空、含 BOM、zip 未重建或发布 JSON 指向旧 zip；不要把它当成用户没有填写高级设置的问题。

## 事实来源

- `tools/shopify_image_localizer/CLAUDE.md`：Shopify Image Localizer 发布与事故规则。
- `tests/test_project_docs.py`：仓库不允许把生产 OpenAPI key 写进源码、文档、JSON。
- `tools/shopify_image_localizer/build_exe.py`：发布包 runtime config 的生成入口。
- `scripts/build_shopify_image_localizer_wine.sh`：Linux/Wine 发布入口。

## 要求

1. 源码、文档、测试里不得硬编码生产 OpenAPI key。
2. 发布包必须同时包含可运行的 `shopify_image_localizer_config.json` 和只读兜底用的默认配置文件。
3. 这两个配置文件的 `api_key` 与 `browser_user_data_dir` 发布前必须校验为非空；缺任一项直接失败，不允许生成或上传 zip。
4. 如果用户保留了旧的空 runtime config，新版程序启动时应能从默认配置文件补回 `api_key` / `browser_user_data_dir`，并写回 runtime config。
5. `load_runtime_config()` 不得通过调用 `save_runtime_config()` 完成自修复，避免递归。

## 设计

- `settings.DEFAULT_API_KEY` 继续只读 `SHOPIFY_IMAGE_LOCALIZER_API_KEY`，未配置时为空。
- 新增 `shopify_image_localizer_default_config.json`，仅作为发布包兜底配置；运行时优先级为：
  1. 用户 runtime config 非空值；
  2. 发布包 default config 非空值；
  3. 环境变量 / 内置非敏感默认值。
- `build_exe._write_runtime_config()` 生成或复制 runtime config 后，必须写出同内容 default config，并验证两份配置的必填字段。
- `scripts/build_shopify_image_localizer_wine.sh` 在 Wine build 前从环境变量 `SHOPIFY_IMAGE_LOCALIZER_API_KEY` 读取 key；若未设置，则从 `/opt/autovideosrt/.env` 的 `OPENAPI_MEDIA_API_KEY` 注入到 build 环境。脚本不得打印 key 值。
- Wine helper 在 zip 生成后再打开 zip 检查两份配置，确认必填字段非空后才复制到 `/opt/autovideosrt/web/static/downloads/tools/`。

## 验证

- `pytest tests/test_project_docs.py tests/test_shopify_image_localizer_domains.py tests/test_shopify_image_localizer_build_exe.py -q`
- 发布时 `scripts/build_shopify_image_localizer_wine.sh --version <ver>` 必须在 zip 上传前打印配置校验通过。
