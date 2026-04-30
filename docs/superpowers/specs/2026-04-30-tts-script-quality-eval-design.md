# video_translate.tts_script 模型质量对照评估

**日期：** 2026-04-30
**状态：** Draft（接续 [localize 评估](2026-04-30-translate-quality-eval-design.md)）
**关联报告：** localize 评估报告显示 Gemini 3 Flash 在 localize 这步可替代 Claude Sonnet 4.6（5 胜 2 平 1 输），月省 ~¥28。本评估验证 Flash 是否也能拿下更贵的 `tts_script`（月开销 ¥193）。

## 1. 背景

`video_translate.tts_script` 是 video_translate 流水线里 LLM 开销最大的一步：
- 月度开销 ¥193（占 anthropic 系列 67%）
- 1033 次调用，平均 input 2516 / output 1332 token（比 localize 大 2-2.5 倍）

如果 Flash 能在 tts_script 上保持质量，月度可再省 ¥150-170。但 tts_script 跟 localize 性质不同：

| 维度 | localize | tts_script |
|---|---|---|
| 任务性质 | 翻译 + 本地化（**创造性**） | 切分 + 朗读优化（**约束性**：只切分不改写） |
| schema 复杂度 | 简单（`full_text + sentences[]`） | 复杂（`full_text + blocks[] + subtitle_chunks[]` 两层切分 + 索引引用）|
| 主要风险点 | 翻译质量 | **schema 合规率 + 文本不变性** |

## 2. 评估范围

| 项 | 设定 |
|---|---|
| 评估模型 | **Claude Sonnet 4.6 vs Gemini 3 Flash**（2 家） |
| 不评估 | Gemini 3.1 Pro（已在 localize 评估证明：31% 失败、慢 5-15 倍、output token 5x、单条贵 2.4x，没必要再跑）|
| 输入 | 4 段英文原稿先经 Claude localize 跑出 8 语种本地化结果，作为 tts_script 输入 |
| 翻译矩阵 | 4 段 × 8 语种 × 2 模型 = **64 次 tts_script 调用** |
| 准备阶段 | Claude localize 4 段 × 8 语种 = 32 次（不算评估调用，准备 tts_script 输入用）|

## 3. 数据源

4 段真实英文 ASR 原稿，覆盖 4 个完全不同品类：

| 段 | task_id | 题材 | 词数 |
|---|---|---|---|
| A | `e51759992ed5516388897810782aa1f0` | 厨房（ice ball mold）| 111 |
| B | `12c71abff2be555ab1547d94a53288e8` | 工具（utility knife）| 108 |
| C | `c1aca4a071f753a9b34077b2145e4b12` | 户外服饰（sunscreen clip）| 85 |
| D | `681b65d365f4576d94fdfc987439d8e8` | 汽车（car mount）| 162 |

A、B 复用 localize 评估的 2 段；C、D 是新增的 2 段（题材不同避免重叠偏差）。

## 4. 评估维度

跟 localize 不同，tts_script 是切分任务（不改写文案），评估重点是**结构正确性**而非翻译质量。3 维自动统计 + 1 维人工抽样：

### 4.1 自动统计维度（适用 64 条全量）

#### A. Schema 合规率
- 必填字段齐全（`full_text`、`blocks[]`、`subtitle_chunks[]`）
- `blocks[].source_segment_indices` 引用合法（指向 localize 输出的 sentence indices）
- `subtitle_chunks[].block_indices` 引用合法（指向自己的 blocks）

**过线标准：100%**（任何一条 schema 不合规即视为评估失败）

#### B. 文本不变性
TTS_SCRIPT_SYSTEM_PROMPT 明确要求：`Use the localized English as the only wording source. subtitle_chunks optimize on-screen reading without changing wording relative to full_text.`

把 `blocks[].text` 拼起来 vs localize 输出的 `full_text` 做 diff，**diff 越小越好**。允许的差异：标点合并/拆分、空格归一化；不允许：增删词汇、改写语序。

度量：**词级 Levenshtein 距离 / 原文长度**，越接近 0 越好。

#### C. 切分长度分布
prompt 要求 `Each subtitle chunk should usually be 5-10 words`。统计 64 条里 subtitle_chunks 长度分布：
- 5-10 词的占比（**期望 ≥ 80%**）
- 1-3 词碎片的占比（**期望 ≤ 5%**）
- 12+ 词超长的占比（**期望 ≤ 5%**）

### 4.2 人工抽样维度

**朗读节奏（rhythm）**：抽 4-6 段（每个语种 1 段，覆盖广），人工读一遍 blocks 看切分点是否在语义自然处（即配音员朗读时该停顿的地方），无突兀断开。

## 5. 实施

### 5.1 脚本：`tools/tts_script_quality_eval.py`

复用 localize 评估的脚手架（基本结构相同），改动：

1. 接受 `--localize-results <path>` 参数，从 localize 评估的 results.json 拉 Claude 跑出的本地化结果作为 tts_script 输入
2. 但 localize 那份只跑了 A、B 两段；C、D 需要先跑一次 Claude localize（脚本内置）
3. 调 `pipeline.translate.generate_tts_script(localized_translation, provider=...)`
4. 输出双层结构（blocks + subtitle_chunks），需要把这两层都落盘
5. 自动统计 schema 合规 / diff / 切分长度，输出到 `report_auto.json`

服务器运行：

```bash
cd /opt/autovideosrt && PYTHONPATH=/opt/autovideosrt \
  /opt/autovideosrt/venv/bin/python /tmp/eval/tts_script_run.py \
  --asr e51759...:A_ice_ball,12c71a...:B_utility_knife,c1aca4...:C_sunscreen,681b65...:D_car_mount \
  --output /tmp/eval/tts_results.json
```

### 5.2 工作流

1. 准备 4 段原稿（task_id → asr_result.json）
2. **准备阶段**：用 Claude 跑 4 × 8 = 32 次 localize，得到 32 段本地化译文
3. **评估阶段**：每段译文喂给 Claude / Flash 各跑一次 tts_script = 64 次
4. **自动统计**：schema 合规率、词级 diff、切分长度分布
5. **人工抽样**：4-6 段不同语种的朗读节奏读一遍
6. **报告**：跟 localize 报告同样格式，输出 [report.md](2026-04-30-tts-script-quality-eval-report.md)

### 5.3 错误处理

- 跟 localize 评估一致：单次 retry 1 次，失败记 error 字段不阻塞
- schema 校验失败的归入"FAIL"统计

## 6. 成本估算

| 项 | 次数 | 估算金额 |
|---|---|---|
| Claude Sonnet 4.6（准备 localize） | 32 | ¥3 |
| Claude Sonnet 4.6（tts_script） | 32 | ¥6 |
| Gemini 3 Flash（tts_script） | 32 | ¥1 |
| 我评估 token（含 diff + 抽样） | ~30k | ¥2 |
| **合计** | | **¥12** |

## 7. 风险

1. **Flash 在复杂 schema 下崩**：tts_script 比 localize 多一层 subtitle_chunks，Flash 可能 schema validation 失败。这本身就是评估的核心问题——如果 Flash 失败率 > 10%，建议**保留 Claude 不切**。

2. **Flash 改写文案违反约束**：localize 时 Flash 加 CTA 是好事；tts_script 里加内容会让 source_segment_indices 失准 → 后续字幕轨道时间码错位。词级 diff 度量会暴露这点。

3. **prompt ASCII 限制副作用**：localize 评估里发现 Claude 严守、Flash 不严守。tts_script 同一条 prompt 限制存在。Flash 不严守可能在意大利语等语种里输出更自然，但要看是否同时引入新的 wording 违规。

## 8. 不在范围

- 不评估实际 TTS 音频效果（要 ElevenLabs 跑出来听才知道，超出本次范围）
- 不评估 rewrite（字数收敛重写）这条 use_case
- 不切换主流水线绑定（评估完后另起决策）
