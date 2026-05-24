# 明空视频卡片 AI 精细评估自动任务设计

最后更新：2026-05-24

## 背景

明空选品的视频卡片已经支持手动点击 `精细AI评估`。该入口会把卡片商品链接和当前视频一起提交给 AI 精细评估流程，并把结果写入 `ai_evaluation_runs` / `ai_country_evaluations`，供弹窗、独立页、加入素材库建议和小语种建议复用。

运营希望系统自动补齐高价值明空视频卡片的精细评估结果，避免人工逐张点击。自动任务必须复用手动按钮的评估逻辑和结果展示契约，不能产生第二套 AI 结果。

## 目标

1. 使用常驻后台 worker 池持续处理自动评估任务，不再依赖 10 分钟 APScheduler tick。
2. 默认任务池并发为 2 张视频卡片；某张卡片跑完后立即补下一张。
3. 同一时间只允许一个 worker 进程运行。
4. 单个卡片任务在领取后跑完即结束；如进程重启，已经领取过的 `material_key` 不再自动重复领取。
5. 优先处理明空 `视频素材库` 按 90 天消耗倒序 Top1000 视频卡片。
6. Top1000 没有可跑任务后，再处理 `昨天消耗前300` 的全部 Top300 视频卡片。
7. 同一视频卡片只自动跑一轮；重复出现在两个列表里的卡片不重复跑。
8. 自动跑出的结果必须和手动点击按钮弹窗、独立页、加入素材库弹窗、小语种建议共用。
9. 国家之间不等待，先把任务跑起来。
10. 不开启 Google Search 工具。
11. 单国家评估失败时自动重试 1 次；第二次无论成功或失败都结束该国家并进入下一步。

生产观察显示单张卡片真实执行时长通常在 4-6 分钟。改为常驻任务池后，吞吐由“每 10 分钟最多 2 张”变为“多个卡片同时跑，跑完即补位”。2026-05-24 生产观察未出现 `429` 后，任务池先从 2 张提高到 4 张；首个 30 分钟窗口仍未出现 `429` 后，再提高到 6 张。随后 6 并发在 ADC Gemini 3.5 Flash 通道出现明确 `429 RESOURCE_EXHAUSTED`，回退 4 后短窗口仍有 429，默认并发最终回退到 2。单张卡片内部的国家评估默认仍保持串行，避免 ADC 通道突然增加国家级并发。

## 非目标

- 不改变手动 `精细AI评估` 的前端交互入口。
- 不新增第二套精细评估弹窗或结果表。
- 不改变普通 `AI评估` 或视频 AI 分析入口。
- 不批量下载明空 MP4 到长期素材库；仍只按精细评估需要缓存当前卡片视频。
- 不在 Windows 本机 MySQL 上做验证。

## 现有锚点

- `docs/superpowers/specs/2026-05-22-fine-ai-card-video-evaluation-design.md`：卡片级精细评估必须使用当前商品链接和当前卡片视频。
- `docs/superpowers/specs/2026-05-22-fine-ai-evaluation-progress-visualization-design.md`：`progress_json` 是执行可视化事实来源。
- `docs/superpowers/specs/2026-05-22-fine-ai-material-import-advice-design.md`：加入素材库和小语种弹窗读取精细评估结果。
- `docs/superpowers/specs/2026-05-18-mingkong-daily-material-snapshot-top100-design.md`：本地明空视频素材库和昨天消耗 Top300 的事实表。
- `appcore/scheduled_tasks.py`：后台轮询 / systemd 常驻任务必须登记到后台任务清单。

## 调度

改为 systemd 常驻 worker：

- Code：`mingkong_fine_ai_auto_evaluation_tick`
- Name：`明空视频卡片 AI 精细评估任务池`
- Schedule：连续后台任务池，默认 2 个卡片并发
- Source type：`systemd`
- Source ref：`autovideosrt-mingkong-fine-ai-worker.service`
- Runner：`tools/mingkong_fine_ai_auto_evaluation_worker.py --workers 2`
- Status table：`mingkong_fine_ai_auto_evaluations`

Web 进程 APScheduler 不再注册该任务。worker 自己持有进程锁，防止重复启动；systemd 负责拉起和异常重启。

## 单例和进程锁

worker 启动时获取 `/tmp/autovideosrt-mingkong-fine-ai-worker.lock` 独占锁。

处理规则：

1. 没有同类 worker：正常启动任务池。
2. 已有 worker 持锁：新进程直接退出。
3. 发布或手工重启时，先 stop/kill 旧 worker，再 start 新 worker。

卡片去重不依赖进程状态。`mingkong_fine_ai_auto_evaluations.material_key` 唯一约束保证同一视频卡片只自动领取一次；`running` / `failed` / `skipped` / `completed` 都表示这张卡片已经经历过自动流程，不再自动重跑。

## 候选来源和优先级

候选必须来自本地归档表，不实时请求明空列表。

### 第一优先级：视频素材库 Top1000

从最新成功明空素材快照中取全局视频级 Top1000：

```sql
FROM mingkong_material_daily_snapshots
JOIN mingkong_material_sync_runs ON run_id = mingkong_material_sync_runs.id
WHERE mingkong_material_sync_runs.status = 'success'
ORDER BY cumulative_90_spend DESC, video_ads_count DESC, id ASC
LIMIT 1000
```

该列表对应 `/xuanpin/mk` 的 `视频素材库` 默认排序口径：严格按视频级 90 天消耗倒序，不被产品级消耗或产品排名覆盖。

### 第二优先级：昨天消耗 Top300

当 Top1000 中没有可跑任务后，再从最新归档的 `mingkong_material_daily_top100` 取全部 Top300：

```sql
FROM mingkong_material_daily_top100
ORDER BY display_position ASC, rank_position ASC, id ASC
LIMIT 300
```

这里的 Top300 是完整 `昨天消耗前300`，不是只取 `is_new_top100_entry=1` 的新进 Top300。表名和字段名保留 `top100` 是兼容历史迁移，业务语义按 Top300。

### 去重

自动评估以 `material_key` 作为视频卡片唯一身份。同一 `material_key`：

- 在 Top1000 内重复，只取一次。
- 同时出现在 Top1000 和 Top300，只按 Top1000 优先级跑一次。
- 已有任何自动评估记录后，不再自动跑第二轮。`running` 记录表示卡片已经被某个 run 领取；如领取方被 30 分钟接管，接管逻辑会把该记录标记为 `failed`，但仍不再自动重跑。

## 自动评估记录表

新增轻量表保存自动评估状态，避免依赖 JSON 查询做去重和排查。

表名：`mingkong_fine_ai_auto_evaluations`

字段：

- `id`
- `material_key`
- `source_bucket`：`top1000_90d_spend` 或 `yesterday_top300`
- `source_rank`
- `product_code`
- `product_url`
- `mk_product_id`
- `mk_product_link`
- `video_name`
- `video_path`
- `video_image_path`
- `cumulative_90_spend`
- `yesterday_spend_delta`
- `evaluation_run_id`
- `status`：`pending` / `running` / `completed` / `partially_completed` / `failed` / `skipped`
- `attempts`
- `last_error`
- `scheduled_run_id`
- `started_at`
- `finished_at`
- `created_at`
- `updated_at`

约束和索引：

- `UNIQUE KEY uk_mk_fine_ai_auto_material (material_key)`
- `KEY idx_mk_fine_ai_auto_status (status, updated_at)`
- `KEY idx_mk_fine_ai_auto_eval_run (evaluation_run_id)`

`completed`、`partially_completed`、`failed` 都表示该卡片自动流程已经跑过一轮；后续不再自动重试。人工仍可通过弹窗手动重新评估。

## 执行流程

worker 池：

1. 按默认并发 6 维护卡片任务槽位。
2. 有空槽时先查询 Top1000 候选，过滤已在 `mingkong_fine_ai_auto_evaluations` 有任何记录的 `material_key`。
3. 如果 Top1000 没有可领取候选，查询全部 Top300 候选。
4. 主线程先领取卡片，插入自动评估记录为 `running`，写入卡片快照；领取成功后才提交到线程池，避免同一轮重复提交。
5. 对每张卡片：
   - 复用现有卡片外链精细评估创建逻辑：
   - 商品链接使用卡片 `mk_product_link`，缺失时回退 `product_url`。
   - 视频使用卡片 `video_path` / `video_name` / `video_duration_seconds`。
   - 自动任务必须强制把当前卡片视频落到本地缓存；远端备份存在但本地文件缺失时不能直接复用远端 object key，避免后续裁剪/LLM 素材准备卡在远端读取。
   - `product_code`、`mk_product_id` 一并写入 run metadata。
   - 同步执行 `FineAiEvaluationService.run_evaluation(evaluation_run_id)`。
   - 根据最终 run status 更新自动评估记录。
6. 某张卡片完成后立即释放槽位并补下一张候选。
7. worker 收到 SIGTERM/SIGINT 后停止领取新卡片，等待已提交卡片自然结束后退出。

自动任务不调用前端 API。它复用后端 service 和同一套数据库结果，达到与点击按钮相同的数据效果。

## 结果共用

自动评估创建的 run 必须满足现有外链卡片结果读取契约：

- `ai_evaluation_runs.product_id = 0`
- `metadata.source_type = external_product_link`
- `metadata.external_product_link` 为最终使用的商品链接
- `metadata.external_card_video.path` 为当前卡片 `video_path`
- `metadata.external_card_video.name` 为当前卡片 `video_name`
- `metadata.asset_snapshot.videos` 包含当前卡片视频缓存结果

卡片接口读取精细评估结果时，未入库商品不能只按本地 `media_product_id` 查结果；还需要按当前卡片的商品链接和视频路径读取最新外链 run。这样自动跑完后：

- 点击 `精细AI评估` 能直接打开已有结果。
- `加入素材库` 弹窗顶部能展示 5 国建议。
- `创建小语种翻译任务` 弹窗能使用相同国家建议。
- 独立页继续使用现有 `/xuanpin/fine-ai-evaluation/<evaluation_run_id>`。

## 国家失败重试

精细评估服务新增单国家自动重试参数，默认用于自动任务，也可用于手动任务而不改变前端契约。

规则：

1. 每个国家最多尝试 2 次。
2. 第一次失败后，在同一国家步骤内记录 retry 日志，并立即再次调用同一国家评估。
3. 第二次成功则该国家记为 completed。
4. 第二次失败则该国家记为 failed，并继续下一个国家。
5. `progress_json.steps[].logs` 必须能看到第一次失败和第二次结果。
6. `ai_country_evaluations.metadata_json` 记录 `attempts` 和最近一次 LLM metadata。

商品事实整理失败仍使整个 run 失败，不进入国家评估；该卡片自动记录为 failed，后续不自动重跑。

## LLM 参数

- 国家之间等待时间设为 0。
- `FineAiEvaluationService.get_service()` 不再注入 30 秒国家等待。
- 国家评估并发模式默认 `serial`，国家并发数默认 `1`；后台设置可切换为并发并配置国家并发数。
- `FineAiGeminiClient` 继续对商品事实和国家评估传 `google_search=False`。
- URL Context 保持现状，用于读取商品链接上下文；本需求只要求不开 Google Search。
- 国家评估主请求单次 LLM 超时为 60 秒；商品事实提取和 JSON 修复请求保持 40 秒。生产观察里成功首次国家评估 P75 约 53 秒，原 40 秒偏紧。

## 错误处理

- 缺少商品链接：记录自动评估为 skipped，原因 `missing_product_link`。
- 缺少视频路径：记录自动评估为 skipped，原因 `missing_video_path`。
- 明空视频缓存失败：记录 failed，并保存错误摘要。
- 商品链接探活失败：沿用现有 link check 失败结果，记录 failed。
- 单国家失败：最多自动重试一次，最终可以得到 `partially_completed`。
- worker 进程异常退出：systemd 重启 worker；已经有自动评估记录的卡片不再自动领取。

## 验收

自动化验证：

- scheduler registry 测试确认任务登记为 systemd worker，且 Web APScheduler 不再注册该任务。
- service 测试确认 Top1000 优先、Top1000 耗尽后取全部 Top300。
- service 测试确认同一 `material_key` 在两个来源重复时只跑一次。
- service 测试确认 worker 池最多 2 个卡片并发，并在卡片完成后补位。
- Fine AI pipeline 测试确认国家失败会自动重试 1 次，第二次失败后继续后续国家。
- Fine AI client 测试确认 `google_search=False`。
- xuanpin route / serializer 测试确认外链卡片能读取自动任务生成的精细评估结果。

手动/服务器验证：

- 不使用 Windows 本机 MySQL。
- 运行相关迁移和聚焦 pytest。
- 起 dev server，未登录 `/xuanpin/mk` 返回 302。
- 登录 admin 后 `/xuanpin/mk` 返回 200。
- 找一张自动跑完的卡片，点击 `精细AI评估` 直接看到已有结果。
- 同一张卡片打开 `加入素材库`，顶部展示精细 AI 国家建议。
