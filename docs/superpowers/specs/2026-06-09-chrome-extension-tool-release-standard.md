# Chrome 插件工具发布标准

日期：2026-06-09

## 背景

店小秘采购洞察首版需要像 Shopify Image Localizer 自动换图工具一样，在服务器上提供可下载发布包，并在素材管理页展示版本号和发布日期。后续所有 Chrome 插件型内部工具发布，都必须沿用同一套流程，不再手工上传 zip 或在模板里硬编码下载地址。

## 事实来源

- `AGENTS.md`：代码发布必须先有文档锚点，发布生产需走测试环境和生产环境验证。
- `tools/shopify_image_localizer/CLAUDE.md`：自动换图工具下载入口必须读取 `system_settings` release JSON，不得在模板中硬编码版本号或 zip 文件名。
- `docs/superpowers/specs/2026-05-24-shopify-image-localizer-release-standard-fix.md`：自动换图工具发布流程包含版本门禁、打包、上传、写 release JSON 和 range curl 验证。
- `docs/superpowers/specs/2026-06-09-dianxiaomi-procurement-insights-extension-design.md`：店小秘采购洞察插件首版设计和后端接口。

## 发布原则

1. Chrome 插件发布包必须是 zip，不允许只把源码目录暴露给用户。
2. 线上下载入口必须读取 `system_settings` release JSON：
   - `version`
   - `released_at`
   - `released_at_display`
   - `release_note`
   - `download_url`
   - `filename`
3. 模板不得硬编码插件 zip 文件名、版本号或发布时间；回滚只改 release JSON 指向旧 zip。
4. 发布包必须放到 `/opt/autovideosrt/web/static/downloads/tools/`。
5. `released_at` 使用北京时间紧凑格式 `MMDD-HHMMSS`，展示优先使用 `released_at_display`。
6. 同版本 zip 已存在时禁止覆盖，必须升版本。
7. 打包必须在普通 `master` checkout 上执行：
   - 当前分支为 `master`
   - 不是 git worktree
   - tracked 工作区干净
   - `HEAD == origin/master`
8. 发布脚本必须写入 `release_manifest.json`，至少包含：
   - `tool`
   - `version`
   - `source_commit`
   - `origin_master_commit`
   - `release_standard`
   - `built_at`

## 店小秘采购洞察插件

首个接入此标准的插件：

| 项 | 值 |
| --- | --- |
| 工具 key | `dianxiaomi_procurement_insights` |
| 源码目录 | `tools/dianxiaomi_procurement_insights/chrome_ext` |
| 版本源 | `tools/dianxiaomi_procurement_insights/version.py` 与 `chrome_ext/manifest.json` |
| release setting key | `dianxiaomi_procurement_insights_extension_release` |
| zip 文件名 | `DianxiaomiProcurementInsights-chrome-<version>.zip` |

## 标准发布命令

在生产服务器普通 master checkout `/opt/autovideosrt` 中执行：

```bash
./venv/bin/python scripts/build_chrome_extension_release.py \
  --release-standard-read \
  --tool dianxiaomi_procurement_insights \
  --version <version> \
  --release-note "..."
```

脚本必须完成：

1. 校验源码门禁和版本一致性。
2. 打包 Chrome 插件源码目录。
3. 复制 zip 到 `/opt/autovideosrt/web/static/downloads/tools/`。
4. 写入对应 `system_settings` release JSON。
5. 使用 `curl --range 0-99` 验证静态下载链接返回 `200` 或 `206`。

## 素材管理入口

素材管理顶部操作区中，店小秘采购洞察插件下载按钮必须放在“下载自动换图工具”左侧，并复用同一视觉样式：

- `oc-tool-download-group`
- `oc-tool-download-btn`
- `oc-tool-download-meta`

两个下载按钮都展示版本号和发布时间；如果对应 release JSON 不存在或没有 `download_url`，则隐藏对应按钮。

## 验证

代码变更后至少运行：

```bash
pytest \
  tests/test_dianxiaomi_procurement_insights.py \
  tests/test_dianxiaomi_procurement_release.py \
  tests/test_shopify_image_localizer_release_web.py \
  tests/test_media_pages_service.py \
  tests/test_medias_list_filters.py -q
```

发布后在服务器验证：

```bash
curl -s -o /dev/null -w "%{http_code}" --range 0-99 \
  http://127.0.0.1/static/downloads/tools/DianxiaomiProcurementInsights-chrome-<version>.zip
```

期望返回 `200` 或 `206`。随后打开素材管理页确认按钮显示版本号和发布时间。
