# 视频翻译·语音生成环节统计持久化与日志输出 — 设计

- **日期**：2026-04-30
- **作者**：jinghuaswsx + Claude (brainstorming)
- **范围**：所有继承自 `appcore.runtime.BaseRunner._step_tts` 的视频翻译 project_type（`multi_translate` / `ja_translate` / `fr_translate` / `de_translate` / `omni_translate` 等），同步打统计 + 写库 + 打日志

## 一、背景

`_step_tts` 是双层循环：

- **外层 round** 1–5：`rewrite → tts_script → audio_gen → measure`，直到音频时长落入 `[video-1, video+2]` 秒；落不进就挑距离最近的一轮作为 best_pick。
- **内层 attempt** 1–5：每个 round 在 rewrite 阶段最多 5 次尝试让字数落入目标 ±tol。
- round 1 没有 rewrite，使用 translate 步骤的初始译文。

现状：每个 round 的细节（`rewrite_attempts`、`audio_segments_total` 等）已经写入 `projects.state_json.tts_duration_rounds`，但**没有任务级汇总**，无法回答"这个任务一共调了多少次翻译 / 多少次 ElevenLabs"。

## 二、目标

- 汇总每条任务"语音生成环节"的两个核心成本指标：**翻译调用次数** 与 **段级 ElevenLabs 调用次数**。
- 把汇总既写进 `state_json`（详情页/排查方便），也写进独立表（聚合分析方便）。
- 在 `_step_tts` 结束的日志末尾用粗体蓝色 ANSI 打印一句任务级统计。

## 三、不在范围（明确不做）

- ❌ 变速优化（用 ElevenLabs `speed` 参数处理 audio>video 的 case） — 以后单独立项。
- ❌ 字数收敛 / 时长收敛的统计指标（state_json 里 `tts_final_reason / tts_duration_status` 已有，本次不汇总也不写独立表列）。
- ❌ round 数列、`audio_calls_round` 列。
- ❌ 任务中途增量写库（统计在 `_step_tts` 结束时一次性写入）。

## 四、指标定义

| 字段 | 口径 | 计算来源（每个 round_record） |
|---|---|---|
| `translate_calls` | round 1 的 1 次初始翻译 + 各 round 内所有 rewrite_attempt 之和 | `1` (round 1) + `sum(len(r["rewrite_attempts"]) for r in rounds[1:])` |
| `audio_calls` | 段级 ElevenLabs 调用总数（所有 round × 各 round 段数之和） | `sum(r["audio_segments_total"] for r in rounds)` |

**注**：`audio_segments_total` 是每个 round 实际被调用的段数；如果某个 round 因为字数收敛失败被丢弃，仍计入（确实调用了）。

## 五、持久化

### 5.1 `projects.state_json` 新字段

在 `_step_tts` 返回前写入（通过 `task_state.update`）：

```json
{
  "tts_generation_summary": {
    "translate_calls": 4,
    "audio_calls": 27,
    "finished_at": "2026-04-30T11:23:45"
  }
}
```

写入时机：`_step_tts` 主循环 `return` 之前（覆盖 converged 与 best_pick 两种返回路径）。

### 5.2 独立表 `tts_generation_stats`

```sql
CREATE TABLE tts_generation_stats (
    task_id          VARCHAR(64)  PRIMARY KEY,
    project_type     VARCHAR(32)  NOT NULL,
    target_lang      VARCHAR(8)   NOT NULL,
    user_id          INT          NULL,
    translate_calls  INT          NOT NULL,
    audio_calls      INT          NOT NULL,
    finished_at      DATETIME     NOT NULL,
    INDEX idx_user_time (user_id, finished_at)
);
```

- 主键 `task_id`：一条任务一行；`_step_tts` 重跑时用 `INSERT ... ON DUPLICATE KEY UPDATE` 覆盖（restart 场景下统计应反映最后一次跑的结果）。
- `project_type` 来自 `task["type"]`（`multi_translate` / `ja_translate` / ...）。
- `target_lang` 来自 `task["target_lang"]`。
- `user_id` 来自 `task["user_id"]`，可为 NULL。

### 5.3 Migration

新增 migration 文件 `db/migrations/<NNN>_create_tts_generation_stats.sql`：

```sql
CREATE TABLE IF NOT EXISTS tts_generation_stats (
    task_id          VARCHAR(64)  PRIMARY KEY,
    project_type     VARCHAR(32)  NOT NULL,
    target_lang      VARCHAR(8)   NOT NULL,
    user_id          INT          NULL,
    translate_calls  INT          NOT NULL,
    audio_calls      INT          NOT NULL,
    finished_at      DATETIME     NOT NULL,
    INDEX idx_user_time (user_id, finished_at)
);
```

按现有 migration 流程，服务器启动时 `appcore.db_migrations` 自动 apply 并登记 `schema_migrations`。

## 六、日志输出

### 6.1 格式

`_step_tts` 主循环结束、写完 state_json + 独立表之后，再 `logger.info` 一条粗体蓝色总结：

```
\033[1;34m本任务用了 4 次翻译，27 次语音生成。\033[0m
```

- ANSI：`\033[1;34m`（粗体 + 蓝色），`\033[0m`（重置）。
- Python `logging` 框架直接输出到 stdout，gunicorn 终端 / journalctl 都能看到（journalctl 默认会保留 ANSI；前端用户看不到）。
- 不影响 SocketIO 事件、不影响前端显示。

### 6.2 触发条件

- `_step_tts` 正常结束（converged）→ 打印
- `_step_tts` 选 best_pick 结束 → 也打印（仍然完成了配对）
- `_step_tts` 中途抛异常（如 `TtsLanguageValidationError`）→ **不打印**（因为统计未完成）

## 七、改动点清单

| 文件 | 改动 |
|---|---|
| `appcore/runtime.py` | `_step_tts` 主循环 `return` 前两个路径都新增"汇总 + 写 state_json + 写独立表 + 打蓝色日志"逻辑（提取一个 `_finalize_tts_generation_stats(task_id, task, rounds)` 私有方法） |
| `appcore/tts_generation_stats.py` *(新建)* | 工具模块：`compute_summary(rounds) -> dict`、`upsert(task_id, project_type, target_lang, user_id, summary)`、`format_log_line(summary) -> str` |
| `db/migrations/<NNN>_create_tts_generation_stats.sql` *(新建)* | 上文 schema |
| `tests/test_tts_generation_stats.py` *(新建)* | 单元测试：(a) `compute_summary` 对样例 rounds 输出正确数；(b) `upsert` UPSERT 行为；(c) `format_log_line` 含 ANSI 转义；(d) `_step_tts` 结束后 `state_json.tts_generation_summary` 已写入（用 fake DB）；(e) 异常路径不写库 |

## 八、影响面

- **同时受益**：`multi_translate` / `ja_translate` / `fr_translate` / `de_translate` / `omni_translate` 等所有走 `BaseRunner._step_tts` 的 project_type，因为改的是基类方法。
- **未受影响**：`text_translate` / `copywriting` / `image_translate` / `subtitle_removal`（这些 project_type 不走 `_step_tts`）。
- **现有任务**：不回填，仅对新跑的 `_step_tts` 任务生效。

## 九、回归保护

- `pytest tests/test_tts_generation_stats.py` 全绿。
- 跑一次 `pytest tests/test_multi_translate_routes.py tests/test_bulk_translate_runtime.py -q` 确认基类方法改动没有打破现有路由/编排测试。
- 服务器部署后人工抽测：跑一条新的多语种翻译任务 → `SELECT * FROM tts_generation_stats WHERE task_id=...` 看到一行；详情页 `state_json.tts_generation_summary` 有值；journalctl 有蓝色日志一行。

## 十、Open questions / 风险

- `_step_tts` 异常路径（语种检查失败等）不写库——但 state_json 也不写——如果未来要看"哪些任务在 tts 阶段失败"，得看别的字段。本次不解决。
- 独立表只存"完成的"任务汇总。如果用户在 _step_tts 中途强行重启服务，那次的统计会丢。本次不做幂等增量写。
