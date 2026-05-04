# TTS 变速短路收敛 + AI 质量评估

- 创建日期：2026-05-04
- 模块：多语言视频翻译 → 语音生成（TTS Duration Loop）
- 目标：长视频任务在文案重写多轮仍无法精确落到 `[v-1, v+2]` 时，浪费大量 LLM/TTS 调用。引入 ElevenLabs 变速短路 + AI 评估，加速收敛并量化变速音频可用性。

## 1. 背景

[appcore/runtime/_pipeline_runner.py:_run_tts_duration_loop](../../appcore/runtime/_pipeline_runner.py#L203) 当前用最多 5 轮 LLM rewrite + ElevenLabs TTS 来收敛译文音频时长到 `[v-1, v+2]`。3-5 分钟以上的长视频经常 5 轮跑完仍只能拿到 ±5%~±10% 的偏差，最后回落到 `_maybe_tempo_align`（ffmpeg atempo，仅在 ±5% 内生效）。每多一轮 = 一次 LLM 文案重写 + 全部 segments 的 ElevenLabs 调用，成本和时长都翻倍。

ElevenLabs 自带 `voice_settings.speed`（合法范围 0.7–1.2）。±10% 偏差对应 speed ∈ [0.91, 1.10]，完全在合法窗口内。SDK 调用接口 [pipeline/tts.py:generate_segment_audio](../../pipeline/tts.py#L118) 已经支持透传 `speed`，但 [generate_full_audio](../../pipeline/tts.py#L166) 链路上没接通，duration loop 也从未启用过。

变速会带来 chipmunk effect / 拖音 / 音色漂移等劣化。改动是否值得上线，需要 AI + 人工双重评估。

## 2. 范围

### 包含
- 在 duration loop 里加"变速短路"分支：进入 ±10% 但不在 `[v-1, v+2]` 时，直接用 ElevenLabs speed 重新合成一遍音频，命中即收敛、未命中走 atempo + 终结，**不再继续后续 rewrite 轮次**。
- 自动 AI 评估：每次变速 pass 都跑一次双轨对比评分，五维 1-5 + 总结 + flags，写库。
- 跨任务评分查询页（admin），用于决定该功能是否应该在生产保留。
- 评估 use_case 接入现有 LLM 统一调用 + bindings 后台，admin 可切换供应商和模型。

### 不包含
- 不改 5 轮 rewrite 的上限和现有 `_maybe_tempo_align` 算法。
- 不改已经在 `[v-1, v+2]` 内的轮次行为（继续走原 atempo 路径）。
- 不为变速 pass 引入新的 LLM 文案重写。
- 不做主观评分人工录入界面（只展示 AI 评分；后续如要加人工评分另行设计）。

## 3. 设计

### 3.1 触发与流程

伪代码（在 `_run_tts_duration_loop` 每轮 measure 之后）：

```
audio_duration = measure(round_record)
if final_lo <= audio_duration <= final_hi:
    # 现有路径：原样不动
    apply_tempo_align(...)
    return converged

if 0.9 * v <= audio_duration <= 1.1 * v:
    # 新路径：变速短路
    speed = audio_duration / video_duration
    try:
        speedup_audio = regenerate_with_speed(tts_segments, voice, speed)
    except ElevenLabsError as exc:
        round_record["speedup_failed"] = str(exc)
        # fallback：回退原始音频走 atempo + 终结
        apply_tempo_align(原始音频)
        return converged_with_fallback

    speedup_duration = probe(speedup_audio)
    round_record["speedup_*"] = ...

    # 同步 AI 评估（即使未命中也评估，因为最终采用的可能是 speedup 或 atempo 后版本）
    eval_record = run_speedup_quality_eval(原始音频, speedup_audio, ctx)
    persist_eval(eval_record)

    if final_lo <= speedup_duration <= final_hi:
        return converged_with_speedup(speedup_audio)

    # 未命中：对变速后音频跑 atempo 兜底，仍然终结
    final_audio = apply_tempo_align(speedup_audio)
    return converged_with_speedup_then_atempo(final_audio)

# else: audio 不在 ±10%，走现有的"进下一轮 rewrite"分支
continue
```

5 轮跑完仍未触发 ±10%（即从未进入新分支）的 best_pick + atempo 路径维持不变。

### 3.2 变速重生成实现

新增 [pipeline/tts.py:regenerate_full_audio_with_speed](../../pipeline/tts.py)：
- 入参：现有 segments 列表（含 `tts_text`）、voice_id、output_dir、variant、speed、api_key、model_id、language_code、on_segment_done
- 落盘：`<output_dir>/tts_segments/<variant>_speedup/seg_NNNN.mp3`，concat 出 `tts_full.<variant>.speedup.mp3`
- 逐段调 `generate_segment_audio(..., speed=speed)`（已有 `voice_settings={"speed": speed}` 透传）
- 不复用原 segments 缓存（路径不同，避免 `_audio_file_already_valid` 把 `speed=1.0` 的旧文件误判为命中）
- 复用 `_call_with_network_retry` 的网络重试

### 3.3 round_record 新增字段
```
speedup_applied: bool
speedup_speed: float  (4 位精度)
speedup_pre_duration: float  (变速前)
speedup_post_duration: float  (变速后)
speedup_hit_final: bool
speedup_audio_path: str (相对 task_dir)
speedup_chars_used: int (额外 ElevenLabs 字符消耗，便于成本观察)
speedup_failed_reason: str | null
speedup_eval_id: int | null  → tts_speedup_evaluations.id
```

### 3.4 LLM use_case 注册

在 [appcore/llm_use_cases.py](../../appcore/llm_use_cases.py) 的 `USE_CASES` 加一条：

```python
"video_translate.tts_speedup_quality_review": _uc(
    title="TTS 变速短路质量评估",
    default_provider="openrouter",
    default_model="google/gemini-3-flash-preview",
    usage_log_service="tts_speedup_review",
    description="对 ElevenLabs 变速短路产物做双轨对比 AI 评分，输出 5 维质量分。",
),
```

调用方式（在 runtime 内）：

```python
result = llm_client.invoke_generate(
    use_case="video_translate.tts_speedup_quality_review",
    prompt=build_speedup_review_prompt(ctx),  # 含 speed/语言/时长/任务 ID
    media=[原始音频文件路径, 变速后音频文件路径],
    user_id=self.user_id,
    project_id=task_id,
    response_schema={
        "type": "object",
        "properties": {
            "score_naturalness":      {"type": "integer", "minimum": 1, "maximum": 5},
            "score_pacing":           {"type": "integer", "minimum": 1, "maximum": 5},
            "score_timbre":           {"type": "integer", "minimum": 1, "maximum": 5},
            "score_intelligibility":  {"type": "integer", "minimum": 1, "maximum": 5},
            "score_overall":          {"type": "integer", "minimum": 1, "maximum": 5},
            "summary":                {"type": "string"},
            "flags":                  {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "score_naturalness", "score_pacing", "score_timbre",
            "score_intelligibility", "score_overall", "summary", "flags",
        ],
    },
    temperature=0.2,
    timeout=60,
)
```

admin 可在 `/settings?tab=bindings` 通过现有 binding 表覆盖默认 provider/model。

### 3.5 数据库

新增迁移 `db/migrations/2026_05_04_tts_speedup_evaluations.sql`：

```sql
CREATE TABLE IF NOT EXISTS tts_speedup_evaluations (
    id BIGSERIAL PRIMARY KEY,
    task_id TEXT NOT NULL,
    round_index INTEGER NOT NULL,
    language TEXT NOT NULL,
    video_duration NUMERIC(10,3) NOT NULL,
    audio_pre_duration NUMERIC(10,3) NOT NULL,
    audio_post_duration NUMERIC(10,3) NOT NULL,
    speed_ratio NUMERIC(6,4) NOT NULL,
    hit_final_range BOOLEAN NOT NULL,
    score_naturalness SMALLINT,
    score_pacing SMALLINT,
    score_timbre SMALLINT,
    score_intelligibility SMALLINT,
    score_overall SMALLINT,
    summary_text TEXT,
    flags_json JSONB,
    model_provider TEXT,
    model_id TEXT,
    llm_input_tokens INTEGER,
    llm_output_tokens INTEGER,
    llm_cost_usd NUMERIC(10,6),
    status TEXT NOT NULL DEFAULT 'pending',  -- pending / ok / failed
    error_text TEXT,
    audio_pre_path TEXT NOT NULL,
    audio_post_path TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    evaluated_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_tts_speedup_evals_task ON tts_speedup_evaluations (task_id, round_index);
CREATE INDEX IF NOT EXISTS idx_tts_speedup_evals_created ON tts_speedup_evaluations (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tts_speedup_evals_lang_overall ON tts_speedup_evaluations (language, score_overall);
```

按项目惯例，迁移自动 apply 并登记 schema_migrations marker。

### 3.6 同步评估流水线

时机：变速 pass 落盘后、duration loop return 前。
失败处理：
- LLM 调用 60s 超时（在 invoke_generate 入口或外层 wrapper 控）
- 任何异常 → `tts_speedup_evaluations.status='failed'` + `error_text`，**任务照常返回收敛结果**
- UI 卡片显示"评估失败 - 重新评估"按钮，admin 点击触发后台重跑（路由 `POST /admin/tts-speedup-evaluations/<id>/retry`）

成本预估：
- 双音频 token 视模型实现而定。Gemini 3 Flash 多模态音频按时长计费，长视频单次 600s 总输入 → 单次评估几分钱级别，对总成本影响极低。

### 3.7 UI

#### 任务详情页（multi-translate 工作台）

在该轮 TTS 卡片下追加"变速短路"子区块，按现有 Ocean Blue 风格（`--accent` 海洋蓝、`--radius-lg` 圆角、五维分用 `--chart-*` 系列）：
- 顶部：`变速重生成 (speed=0.9772) · 215.3s → 219.8s ✓ 命中 [v-1, v+2]`
- 两个 audio 播放器并排：
  - 左：`变速前 215.3s`
  - 右：`变速后 219.8s`
- 五维分柱条：自然度 / 节奏稳定 / 音色保留 / 可懂度 / 整体可用，1-5 分各染色
- summary 文本（中文）+ flags 标签（每个标签 `--bg-muted` 描边小 chip）
- 评分状态徽章：`评估中 / 完成 / 失败 [重新评估]`
- 评分元数据折叠：模型供应商 / 模型 ID / token / 估算 cost

#### 跨任务评分查询页

新增路由 `/admin/tts-speedup-evaluations`，挂入左侧导航"数据分析"分组。
- 列表列：创建时间、任务 ID（点击跳详情）、语种、speed、变速前后时长、是否命中 final、五维分、整体分、状态、模型、cost
- 筛选：语种、命中/未命中、整体分阈值、时间范围、status
- 排序：创建时间 / 整体分
- 导出 CSV
- 顶部统计卡片：总样本数、命中 final 比例、整体分平均、各 flag 出现频次 top 5（用于回答"该不该上线"）

### 3.8 模块边界 / 文件改动清单

| 文件 | 改动类型 | 说明 |
|------|---------|------|
| [pipeline/tts.py](../../pipeline/tts.py) | 新增函数 | `regenerate_full_audio_with_speed` |
| [appcore/runtime/_pipeline_runner.py](../../appcore/runtime/_pipeline_runner.py) | 修改 | duration loop 注入变速短路分支 |
| [appcore/runtime/_helpers.py](../../appcore/runtime/_helpers.py) | 可选新增 | speed 计算 / 区间判定的纯函数 |
| [appcore/llm_use_cases.py](../../appcore/llm_use_cases.py) | 新增条目 | `video_translate.tts_speedup_quality_review` |
| `appcore/tts_speedup_eval.py` | 新建 | 评估 orchestrator：构造 prompt、调 llm_client、写库 |
| `db/migrations/2026_05_04_tts_speedup_evaluations.sql` | 新建 | DDL |
| `db/schema.sql` | 同步追加 | 表声明（保持文件一致） |
| `web/routes/tts_speedup_eval.py` | 新建 | admin 列表 / 详情 / 重跑 / CSV 导出 |
| `web/templates/admin/tts_speedup_eval_list.html` | 新建 | 跨任务查询页 |
| `web/templates/_task_workbench_scripts.html` | 修改 | 任务详情页变速卡片渲染 + 重跑按钮 |
| `web/templates/_task_workbench_styles.html` | 修改 | 新增子卡片样式（仅 token 化变量） |
| `web/templates/layout.html` | 修改 | 数据分析分组下增加导航条目 |
| `tests/test_tts_duration_loop.py` | 扩展 | 触发条件、变速失败回退、变速命中/未命中分支 |
| `tests/test_tts_speedup_eval.py` | 新建 | 评估 orchestrator 单测 + use_case 注册检查 |
| `tests/test_admin_tts_speedup_eval_routes.py` | 新建 | 列表 / 重跑 / CSV 导出路由测试 |

## 4. 失败模式与边界

| 场景 | 行为 |
|------|------|
| 变速调用失败（API/SSL/超时） | 回退原始音频 atempo + 终结，写 `speedup_failed_reason` |
| 评估 LLM 失败 | 任务正常返回，eval 行 `status=failed`，UI 显示重跑按钮 |
| 变速后超出 `[v-1, v+2]` 但在 ±5% | atempo 收敛 |
| 变速后偏差 > ±5% | atempo 跳过，最终采用变速产物，记录偏差到 round_record |
| 任务中途取消 | 评估和变速都通过现有 `cancellation.throw_if_cancelled` 检查 |
| 重跑评估 | 只覆盖该 eval 行的 score / model / status，audio 路径不变 |

## 5. 验收

1. 长视频任务（≥3 分钟）首轮就落入 ±10% 时，变速 pass 触发，任务一轮内收敛（不再跑后续 rewrite）。
2. 短视频任务首轮就在 `[v-1, v+2]` 时，行为与现状一致（不触发变速 pass）。
3. 任务详情页能看到变速前/后两个播放器、五维分、summary、flags。
4. `/admin/tts-speedup-evaluations` 列出所有跑过的样本，可筛可导出。
5. 关闭功能：admin 在 bindings 后台把 use_case 解绑或停用 → 任务级开关待定（如需 kill switch，下个版本加 `enabled` 配置项）。
6. 现有 TTS Duration Loop 单测全部通过。

## 6. 上线判断与回滚

跑 1-2 周累计 ≥30 个样本后，admin 在跨任务评分页观察：
- 整体分均值 ≥ 4.0 且 flag 频次合理 → 保留
- 整体分均值 < 3.5 或频繁出现 chipmunk/wobble → 下线该分支（保留代码，注释掉触发条件 / 加 kill switch 配置）

回滚成本低 —— 改动集中在 duration loop 一个分支 + 一张孤立的评估表，不影响主翻译路径。

## 7. 后续可拓展（非本次范围）

- 人工评分覆盖 AI 评分（双源对照）
- 多速档对比（同时跑 speed=ratio 和 speed=ratio+/-5% 让模型选最优）
- 跨语种聚合分析报表（哪类语种变速劣化最严重）
- 任务级 kill switch / 灰度配置
