# 推送内容质量检查设计

最后更新：2026-04-29

## 目标

在推送管理中为管理员提供三项大模型检查结果：文案、封面图、视频。检查只用于推送前辅助判断，不自动阻断推送。

## 范围

- 检查对象：推送管理能加载出来的非英语素材中，状态为待推送或推送失败且已经满足现有就绪条件的素材。
- 检查要素：
  1. 文案：对应语种的小语种文案。
  2. 封面图：当前素材的 `cover_object_key`。
  3. 视频：当前素材的 `object_key`，只截取前 5 秒发给模型。
- 不检查商品链接。链接由现有系统保障。
- 模型固定走统一 LLM 调用：`provider=openrouter`，`model=google/gemini-3.1-flash-lite-preview`。

## 自动重试控制

自动定时任务必须只对同一内容做一次检查。检查成功、发现风险、判定失败、文件不可用、LLM 调用异常都算一次已尝试。

记录层使用内容指纹控制重复：

- 文案指纹：`product_id + lang + title/body/description` 的哈希。
- 封面指纹：`item_id + lang + cover_object_key` 的哈希。
- 视频指纹：`item_id + lang + object_key` 的哈希。

定时任务只处理缺少对应指纹记录的要素。已有记录直接复用，不会自动再次调用模型。管理员在推送弹窗点击“重新评估”时，才允许生成新的手动检查记录。

## 数据模型

新增表 `media_push_quality_checks`，一条记录保存一个素材在一次检查中的聚合结果。

关键字段：

- `item_id`, `product_id`, `lang`
- `attempt_source`: `auto` 或 `manual`
- `status`: `running`, `passed`, `warning`, `failed`, `error`
- `copy_fingerprint`, `cover_fingerprint`, `video_fingerprint`
- `copy_result_json`, `cover_result_json`, `video_result_json`
- `summary`, `failed_reasons`
- `provider`, `model`
- `started_at`, `finished_at`, `created_at`, `updated_at`

同一 `item_id + copy_fingerprint + cover_fingerprint + video_fingerprint + attempt_source` 不重复插入；自动任务查询最新同指纹记录并复用。

## 检查行为

文案检查使用 `llm_client.invoke_chat`。输入包含目标语种、商品信息和小语种文案字段，输出结构化 JSON：

- `status`: `passed`, `warning`, `failed`
- `summary`: 中文摘要
- `issues`: 问题数组
- `is_clean`: 是否纯净匹配目标语种

封面和视频检查使用 `llm_client.invoke_generate`。封面传图片文件，视频传 5 秒临时片段。提示要求模型判断是否存在明显中文、英文、乱码、错语种字幕或不相关内容，并输出同样结构。

聚合状态规则：

- 三项均 `passed`：整体验证 `passed`。
- 任一项 `failed`：整体验证 `failed`。
- 无 failed 但任一项 `warning`：整体验证 `warning`。
- 调用或素材处理异常：对应项为 `error`，整体为 `error`。

## 后台与页面

- 新增 APScheduler 任务 `push_quality_check_tick`，每 10 分钟处理小批量待检查素材。
- 在 `appcore/scheduled_tasks.py` 登记任务，运行日志走 `scheduled_task_runs`。
- `/pushes/api/items` 返回列表上的最新检查状态。
- `/pushes/api/items/<id>/payload` 返回弹窗展示所需的完整检查结果。
- 新增 `/pushes/api/items/<id>/quality-check/retry` 管理员接口，手动重新评估当前素材三项。
- 推送弹窗顶部显示三项检查卡片和“重新评估”按钮。结果不阻断推送。

## 测试

- 单元测试覆盖指纹生成、记录复用、自动只尝试一次、手动重评估允许再次尝试。
- 调度器测试覆盖 APScheduler 注册。
- 路由测试覆盖 payload 返回质量检查结果、手动重评估接口调用。
- 前端改动以静态函数测试为主；必要时用浏览器手工验弹窗布局。
