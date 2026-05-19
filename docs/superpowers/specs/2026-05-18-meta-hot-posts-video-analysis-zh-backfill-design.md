# Meta 热帖视频分析中文解读回填

日期：2026-05-18

## 背景

`meta_hot_post_video_copyability_analyses` 已保存美国可抄视频分析结果，前端 `video_copyability` 数据块目前展示英文 `summary`。存量已完成分析约 5000 条，运营刷素材时需要优先看到中文解读；缺失中文缓存时仍可回退英文原分析，避免页面空白。

## 目标

- 为视频可抄分析结果增加中文解读缓存字段。
- 新增 `meta_hot_posts.video_copyability_translate` LLM 用例，使用 Google ADC 通道 `gemini_vertex_adc` 与 `gemini-3.1-flash-lite`。
- 存量英文分析通过长任务回填中文解读，初始不设置条目间隔；发现 429 / quota / resource exhausted 时立即把后续条目间隔增加 1 秒，并至少观察 10 分钟的失败情况。
- 新增视频分析结果如果模型返回中文解读字段，应直接存档；没有中文字段时仍由回填任务补齐。
- 前端优先展示中文解读；中文为空时展示原始英文 `summary`。

## 数据模型

在 `meta_hot_post_video_copyability_analyses` 增加：

- `summary_zh TEXT NULL`
- `summary_zh_status VARCHAR(16) NOT NULL DEFAULT 'pending'`
- `summary_zh_attempts INT UNSIGNED NOT NULL DEFAULT 0`
- `summary_zh_error MEDIUMTEXT NULL`
- `summary_zh_translated_at DATETIME NULL`
- `idx_meta_hot_post_video_copyability_summary_zh_status`

仅 `status='done'` 且英文 `summary` 非空的记录进入回填队列。失败记录可重试，超过默认尝试次数后不再选取。

## 任务节奏与限流策略

长任务脚本按批循环：

1. 每批最多取 120 条待翻译记录。
2. 每条调用一次 `meta_hot_posts.video_copyability_translate`。
3. 初始 `delay_seconds=0`，正常情况下不额外 sleep。
4. 任一条命中 429 / quota / resource exhausted 时，本批停止，脚本把 `delay_seconds += 1`，记录策略调整日志，并继续跑下一批。
5. 每次策略调整后至少观察 10 分钟；如果再次命中限流，再继续 `+1s`。
6. 当下一批为空时退出。

3000 到 5000 条量级在无间隔、无 429 时主要取决于模型响应时间；若出现 429，按 1 秒步进逐步降速，不一次性拉长到固定大间隔。

## 前端

`/xuanpin/meta-hot-posts` 的 `copyabilityBlock(row)` 使用：

```text
data.summary_zh || data.summary || ''
```

因此四个子 tab 只要接口带出 `video_copyability.summary_zh` 就优先显示中文；没有中文缓存时继续显示英文原分析。

## 验证

- `tests/test_meta_hot_posts_store.py` 覆盖字段查询、写入和待翻译队列。
- `tests/test_meta_hot_posts_service.py` 覆盖 hydrate 输出 `summary_zh`。
- `tests/test_meta_hot_posts_video_copyability_translation.py` 覆盖 Gemini ADC Flash-Lite 翻译调用和 5 秒节奏。
- `tests/test_meta_hot_posts_routes.py` 覆盖前端优先展示中文字段。
- `tests/test_llm_use_cases_registry.py` / `tests/test_llm_bindings_dao.py` 覆盖 use case 与 ADC 绑定。
