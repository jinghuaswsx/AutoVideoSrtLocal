# Tabcut 商品中文信息补全设计

最后更新：2026-06-11

## 背景

运营在 `/xuanpin/tabcut` 视频列表和沉浸式刷视频时，需要快速判断商品是什么。当前 Tabcut 视频卡片和浮层主要展示英文商品标题，副屏非移动端浮层虽然可展开信息，但仍缺少中文标题、中文短名和中文类目。用户确认：

- 非移动端沉浸式浮层顶部信息默认直接展开。
- 商品标题需要定时批量翻译。
- 类目信息也要放进中文信息里。
- 翻译后给产品取一个中文名字。
- 使用 Gemini 3.1 Flash Lite 做翻译和中文命名。
- 未翻译时提供手动翻译按钮。
- Tabcut 视频列表页也要显示中文产品信息，方便快速过品。

## 锚点

- `AGENTS.md#文档驱动代码`：新要求先固化为 spec，再作为代码锚点。
- `docs/superpowers/specs/2026-06-11-tabcut-today-new-immersive-video-design.md#左上角产品概要`：Tabcut 浮层左上角显示产品概要并支持展开/收起。
- `docs/superpowers/specs/2026-05-12-tabcut-crawler-design.md#数据库`：Tabcut 商品数据落在 `tabcut_goods`，视频候选关联 `primary_item_id`。
- `docs/superpowers/specs/2026-05-20-xuanpin-secondary-screen-columns-design.md#Context`：副屏为窄桌面/竖屏场景，优先提高信息密度和扫品效率。
- `appcore/scheduled_tasks.py#tabcut_video_localization_tick`：Tabcut APScheduler 任务需要登记到定时任务中心。
- `web/templates/CLAUDE.md#CSRF / 路由守卫`：新增 POST 接口需要登录管理员守卫并带 CSRF。

## 目标

1. Tabcut 商品维表保存中文标题、中文短名、中文类目和翻译状态。
2. Tabcut 视频列表 API、今日新增 API、商品榜 API 都返回这些中文字段。
3. 视频列表卡片的商品 mini 区优先显示中文短名/中文标题，并显示中文类目；未翻译时显示“翻译”按钮。
4. 沉浸式浮层优先显示中文短名/中文标题和中文类目；非移动端默认展开，移动端保持默认收起。
5. 新增手动翻译 API，点击按钮后立即翻译当前商品并刷新当前卡片/浮层数据。
6. 新增 APScheduler 定时任务，定期批量处理未翻译商品，直到全量补完。

## 非目标

- 不改变 Tabcut 采集源、登录态、CDP 请求节流和日快照 systemd timer。
- 不翻译视频文案或作者信息。
- 不用本地 Windows MySQL 做验证。
- 不把分享页开放手动翻译能力。

## 数据模型

新增迁移 `db/migrations/2026_06_11_tabcut_goods_chinese_info.sql`：

- `tabcut_goods.item_name_zh TEXT NULL`
- `tabcut_goods.item_name_zh_short VARCHAR(255) NULL`
- `tabcut_goods.category_name_zh VARCHAR(255) NULL`
- `tabcut_goods.category_l1_name_zh VARCHAR(255) NULL`
- `tabcut_goods.category_l2_name_zh VARCHAR(255) NULL`
- `tabcut_goods.category_l3_name_zh VARCHAR(255) NULL`
- `tabcut_goods.zh_translation_status VARCHAR(16) NOT NULL DEFAULT 'pending'`
- `tabcut_goods.zh_translation_attempts INT UNSIGNED NOT NULL DEFAULT 0`
- `tabcut_goods.zh_translation_error MEDIUMTEXT NULL`
- `tabcut_goods.zh_translated_at DATETIME NULL`

状态语义：

- `pending`：有英文标题但尚未翻译。
- `running`：定时任务或手动按钮正在处理。
- `done`：已保存中文信息。
- `failed`：翻译失败，可被定时任务重试，最多 3 次。

## LLM 契约

新增 use case：`tabcut.translate_goods_info`

默认绑定：

- provider：`openrouter`
- model：`google/gemini-3.1-flash-lite`

输入包含：

- 商品英文标题 `item_name`
- `category_name`
- `category_l1_name`
- `category_l2_name`
- `category_l3_name`

输出 JSON：

```json
{
  "item_name_zh": "完整中文商品标题",
  "item_name_zh_short": "便于扫品的中文商品名",
  "category_name_zh": "中文完整类目",
  "category_l1_name_zh": "中文一级类目",
  "category_l2_name_zh": "中文二级类目",
  "category_l3_name_zh": "中文三级类目"
}
```

服务层需要清理空值、限制长度，并在 JSON 解析失败时用模型文本中的 JSON 片段修复。

## API

新增内部 API：

- `POST /medias/api/tabcut-selection/goods/<item_id>/translate`
- `POST /xuanpin/api/tabcut/goods/<item_id>/translate`

权限：

- 登录用户。
- 管理员。
- POST 带 `X-CSRFToken`。

响应：

```json
{
  "ok": true,
  "item": {
    "item_id": "123",
    "item_name": "English title",
    "item_name_zh": "中文标题",
    "item_name_zh_short": "中文短名",
    "category_l1_name_zh": "中文一级类目"
  }
}
```

## 定时任务

新增任务：

- `task_code=tabcut_goods_translation_tick`
- runner：`appcore.tabcut_selection.scheduler.goods_translation_tick_once`
- schedule：每 10 分钟
- limit：默认每轮 30 个商品
- log_table：`scheduled_task_runs`

每轮流程：

1. 先把超过 1 小时的 `running` 状态重置为 `failed`，避免进程中断后商品永久卡住。
2. 读取 `zh_translation_status IN ('pending','failed')` 且 attempts < 3 的商品。
3. 标记 running 并 attempts + 1。
4. 调用 Gemini 3.1 Flash Lite 生成中文信息。
5. 成功则写回中文字段并标 done。
6. 失败则写 `failed + error`，遇到全局 provider 配置/额度错误时停止本轮。

## 前端展示

视频列表卡片：

- `renderProductMini(row)` 优先使用 `item_name_zh_short`。
- 次级显示 `item_name_zh` 或英文标题。
- meta 行显示价格、销量、中文类目。
- 未翻译时显示翻译按钮；翻译中禁用按钮；失败时按钮可重试。

沉浸式浮层：

- 标题优先使用 `item_name_zh_short`，其次 `item_name_zh`，最后英文标题。
- 展开详情显示中文标题、英文标题、中文类目、英文类目、商品 ID、价格、销量和视频指标。
- 非移动端打开时默认 `infoExpanded=true`。
- 移动端打开时保持默认 `infoExpanded=false`。
- 未翻译时显示翻译按钮，点击后更新当前 item、当前卡片和浮层。

## 验证

自动化：

```bash
pytest tests/test_tabcut_selection_store.py tests/test_tabcut_selection_routes.py tests/test_xuanpin_routes.py tests/test_tabcut_goods_translation.py tests/test_appcore_scheduled_tasks.py -q
python3 scripts/pytest_related.py --base origin/master --run
python3 -m compileall appcore/tabcut_selection web/routes tests -q
git diff --check
```

手动：

- 未登录 `/xuanpin/tabcut` 继续 302。
- 登录后 `/xuanpin/tabcut` 返回 200。
- 视频列表卡片展示中文短名、中文标题或翻译按钮。
- 打开非移动端浮层时顶部信息默认展开。
- 点击翻译按钮后 POST 成功，卡片和浮层中文信息刷新。
- 定时任务登记页可看到 `tabcut_goods_translation_tick`。
