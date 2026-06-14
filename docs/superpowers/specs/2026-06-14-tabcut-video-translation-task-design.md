# Tabcut 视频文案中文翻译任务设计

最后更新：2026-06-14

## 背景

运营在 `/xuanpin/tabcut` 打开视频卡片和沉浸式视频时，需要直接看到中文视频文案、视频关联商品标题等信息。当前系统已有两个相邻任务：

- `tabcut_goods_translation_tick`：每 10 分钟翻译 `tabcut_goods` 商品标题和类目。
- `tabcut_video_localization_tick`：每 10 分钟下载 Tabcut 视频、封面和时长。

缺口是 `tabcut_videos.video_desc` 与视频维表上的 `primary_item_name` 没有独立中文翻译字段，也没有持续消费未翻译视频的任务池。因此新抓到的视频和历史视频仍需要用户手动触发翻译或只能看英文。

## 锚点

- `AGENTS.md#文档驱动代码`：新要求先固化为 spec，再作为代码锚点。
- `AGENTS.md#定时任务一律登记`：新增 APScheduler 任务必须同步登记到 `appcore/scheduled_tasks.py` 和后台定时任务模块。
- `docs/superpowers/specs/2026-05-12-tabcut-crawler-design.md#数据库`：Tabcut 视频维表为 `tabcut_videos`，按 `video_id` upsert，字段包含 `video_desc` 和 `primary_item_name`。
- `docs/superpowers/specs/2026-06-11-tabcut-product-chinese-info-design.md`：商品级中文翻译任务已存在，本次只补视频级信息，不替代商品翻译。
- `docs/superpowers/specs/2026-06-11-tabcut-today-new-immersive-video-design.md`：视频列表与今日新增视图共用 `tabcut_videos` 数据契约，打开视频时应读取持久化字段。
- `docs/superpowers/specs/2026-06-08-targeted-pytest-verification.md`：改动后运行相关 focused tests，不默认全量 pytest。

## 目标

1. 每 10 分钟持续运行 Tabcut 视频中文翻译任务。
2. 每轮最多拉取 10 个未翻译或可重试失败的视频。
3. 使用 OpenRouter Gemini Flash 翻译视频文案和视频关联商品标题。上线 smoke 确认 OpenRouter 当前没有 Gemini 1.5 Flash 可用 endpoint，因此生产使用 `google/gemini-2.5-flash`。
4. 翻译结果持久化写回 `tabcut_videos`，以后打开视频直接读取缓存结果。
5. 新抓到的视频和历史未翻译视频都进入同一任务池。
6. 任务登记到后台“定时任务”模块，运行日志进入 `scheduled_task_runs`。

## 非目标

- 不改变 Tabcut 采集器、CDP 登录态、日快照 timer 或视频下载任务。
- 不替换商品级 `tabcut_goods_translation_tick`。
- 不做视频画面理解、ASR 或口播翻译；本期只翻译已有文本字段。
- 不连接 Windows 本机 MySQL 做验证。

## 数据模型

新增迁移 `db/migrations/2026_06_14_tabcut_video_chinese_info.sql`：

- `tabcut_videos.video_desc_zh MEDIUMTEXT NULL`
- `tabcut_videos.primary_item_name_zh TEXT NULL`
- `tabcut_videos.zh_translation_status VARCHAR(16) NOT NULL DEFAULT 'pending'`
- `tabcut_videos.zh_translation_attempts INT UNSIGNED NOT NULL DEFAULT 0`
- `tabcut_videos.zh_translation_error MEDIUMTEXT NULL`
- `tabcut_videos.zh_translated_at DATETIME NULL`
- 索引：`idx_tabcut_videos_zh_translation_status (zh_translation_status, zh_translation_attempts, last_seen_at)`

已有历史视频中，只要 `video_desc` 或 `primary_item_name` 非空且中文字段为空，迁移后标记为 `pending`。

状态语义：

- `pending`：存在可翻译文本，尚未翻译。
- `running`：定时任务或手动入口正在处理。
- `done`：已保存中文结果。
- `failed`：单条翻译失败，可被后续任务重试。

## LLM 契约

新增 use case：`tabcut.translate_video_info`

默认绑定：

- provider：`openrouter`
- model：`google/gemini-2.5-flash`

输入包含：

- `video_id`
- `video_desc`
- `primary_item_name`
- `author_name`
- `primary_item_id`

输出 JSON：

```json
{
  "video_desc_zh": "中文视频文案",
  "primary_item_name_zh": "中文商品标题"
}
```

服务层需要清理空值、去掉 Markdown code fence，并在模型只返回文本 JSON 时修复解析。

## 定时任务

新增任务：

- `task_code=tabcut_video_translation_tick`
- runner：`appcore.tabcut_selection.scheduler.video_translation_tick_once`
- schedule：每 10 分钟
- limit：默认每轮 10 个视频
- log_table：`scheduled_task_runs`

每轮流程：

1. 把超过 1 小时的 `running` 视频重置为 `failed`，避免进程中断永久卡住。
2. 读取 `zh_translation_status IN ('pending','failed')` 且 attempts < 3 的视频。
3. 标记 running 并 attempts + 1。
4. 调用 OpenRouter Gemini 2.5 Flash 翻译文本。
5. 成功写回中文字段并标记 `done`。
6. 失败写入 `failed + error`，遇到全局 provider 配置/额度错误时停止本轮。

## API 与展示

视频列表、今日新增和视频详情继续通过现有查询读取 `tabcut_videos`。后端响应增加：

- `video_desc_zh`
- `primary_item_name_zh`
- `video_zh_translation_status`
- `video_zh_translation_attempts`
- `video_zh_translation_error`
- `video_zh_translated_at`

前端打开卡片或沉浸式浮层时优先展示中文视频文案；没有中文时回退英文原文。商品标题展示仍优先使用 `tabcut_goods` 的商品中文短名/标题，其次回退 `tabcut_videos.primary_item_name_zh`。

## 验证

自动化：

```bash
pytest tests/test_tabcut_video_translation.py tests/test_tabcut_selection_store.py tests/test_tabcut_selection_schema.py tests/test_appcore_scheduled_tasks.py tests/test_llm_use_cases_registry.py -q
python3 scripts/pytest_related.py --base origin/master --run
python3 -m compileall appcore/tabcut_selection appcore/llm_use_cases.py -q
git diff --check
```

手动：

- 定时任务后台可看到 `tabcut_video_translation_tick`。
- 打开 `/xuanpin/tabcut`，视频卡片 payload 中包含 `video_desc_zh`。
- 历史未翻译视频被逐轮消耗，每轮最多 10 条。
