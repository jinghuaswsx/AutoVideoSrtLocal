# 视频翻译模型质量对照评估

**日期：** 2026-04-30
**状态：** Draft（待用户评审）
**任务：** 一次性评估，非生产功能

## 1. 背景

`video_translate.localize` 这条 use_case 当前默认走 OpenRouter 的 `anthropic/claude-sonnet-4.6`（input $3 / output $15 per 1M），是 video_translate 流水线里第一次把源语言 ASR 文本翻译成目标语种的步骤。本月 9 天累计花费 ¥297（其中 localize 占 ¥31.72，但更贵的 `tts_script` ¥193 也在同模型上）。

需要评估 Gemini 3.1 Pro / Flash 在 localize 这一步能否在保持质量的前提下换掉 Sonnet 4.6，给后续是否切换主流水线绑定提供决策依据。

## 2. 目标

- 对比 **Claude Sonnet 4.6 / Gemini 3.1 Pro / Gemini 3.1 Flash** 三个模型在 localize 步骤的翻译质量
- 覆盖系统当前启用的全部 9 个目标语种（de / en / es / fr / it / ja / nl / pt / sv）
- 输出可决策的对比报告：要不要切换、切到哪个、有什么 trade-off

## 3. 不在范围

- 不评估 `tts_script` / `rewrite` / TTS 朗读这些下游步骤
- 不切换主流水线 binding（评估完后另起决策）
- 不做大规模回归测试，只评估典型样本
- 不对历史已跑结果做回填或重评

## 4. 设计

### 4.1 数据源

两段真实 ASR 原稿，从历史 `video_translate` 项目里挑选：

| 原稿 | 来源 | 长度阈值 |
|---|---|---|
| 中文 | 任一中文视频翻译项目的 `asr_result.json` | ≥ 100 中文字 |
| 英文 | 任一英文视频翻译项目的 `asr_result.json` | ≥ 50 英文词 |

挑选规则：长度满足、内容完整连贯（不能截断）、最近一周内的真实业务素材。

### 4.2 翻译矩阵

| 维度 | 取值 | 数量 |
|---|---|---|
| 中文原稿目标语种 | de, en, es, fr, it, ja, nl, pt, sv | 9 |
| 英文原稿目标语种 | de, es, fr, it, ja, nl, pt, sv（排除自身） | 8 |
| 模型 | Claude Sonnet 4.6 / Gemini 3.1 Pro / Gemini 3.1 Flash | 3 |
| **总翻译次数** | (9+8) × 3 | **51** |

### 4.3 三个候选模型

| 模型 | provider / model_id | 单价（per 1M tokens） |
|---|---|---|
| Claude Sonnet 4.6 | openrouter / anthropic/claude-sonnet-4.6 | $3 / $15 |
| Gemini 3.1 Pro | openrouter / google/gemini-3.1-pro-preview | $1.25 / $10 |
| Gemini 3.1 Flash | openrouter / google/gemini-3.1-flash-preview | $0.30 / $2.50 |

> Gemini 3.1 Flash 在系统里也可走 `gemini_vertex` channel；但为对照公平，三家都走 OpenRouter。

### 4.4 实施

#### 脚本：`tools/translate_quality_eval.py`

独立 CLI 工具，不进主流水线、不写 ai_billing 表（避免污染统计）。

```
python -m tools.translate_quality_eval \
  --zh-task <zh_task_id> \
  --en-task <en_task_id> \
  --output docs/superpowers/specs/2026-04-30-translate-quality-eval/
```

流程：

1. 从 `output/<zh_task_id>/asr_result.json` 拉中文原稿
2. 从 `output/<en_task_id>/asr_result.json` 拉英文原稿
3. 对每段原稿 × 每个目标语种 × 每个模型，调用 `pipeline.translate.generate_localized_translation`（带 `provider_override` 参数指向对应模型）
4. 落盘 `results.json`（结构化）+ `report.md`（可读对照表）
5. 失败重试 1 次，再失败记 `error` 字段不阻塞其他

#### 输入构造

`generate_localized_translation` 需要 `source_full_text` + `script_segments`。从 `asr_result.json` 中：
- `source_full_text` = 整段拼起来
- `script_segments` = ASR 句段直接传

每个目标语种 / 模型组合传同样的 input，仅 `provider` 和 `model` 不同。

#### 错误处理

- 网络抖动：单次调用最多 retry 1 次
- 模型 schema 验证失败（Gemini 偶尔不返合规 JSON）：记录错误，落入 `error` 字段，不影响其它语种
- 评估阶段对失败样本标 N/A，单独说明

### 4.5 评估方法

由我（Claude Code）逐条评估，每条按 4 个维度 1-5 分打分：

| 维度 | 含义 |
|---|---|
| **准确度** | 是否完整保留原文意思，无遗漏、无杜撰 |
| **流畅度** | 目标语言语法是否地道，有无机翻味 |
| **本地化** | 文化习语、量词、品牌口吻是否贴合目标地区 |
| **广告语调** | 是否保持原文的促销 / 描述 / 引导风格 |

输出：

1. **对比表**：原稿 + 三家译文并排，每条 4 维评分 + 简评
2. **横向分析**：每个语种里三家的平均分、强弱项
3. **决策建议**：哪个 use_case 适合换、换哪个、可省多少

### 4.6 落盘

`docs/superpowers/specs/2026-04-30-translate-quality-eval/`
- `design.md`（即本文）
- `inputs.json`（中文/英文 ASR 原稿快照）
- `results.json`（51 条翻译原始结果）
- `report.md`（评估对比表 + 分析）

## 5. 测试

非产品代码，无单测要求。脚本 smoke test：用 1 段超短文本 × 1 个目标语种 × 1 个模型先跑通，确认链路 OK 再批量。

## 6. 成本估算

| 项 | 次数 | 估算金额 |
|---|---|---|
| Claude Sonnet 4.6 | 17 | ¥1.0 |
| Gemini 3.1 Pro | 17 | ¥3.0 |
| Gemini 3.1 Flash | 17 | ¥0.5 |
| 我评估的 token | ~25k | ¥1.5 |
| **合计** | | **¥6** |

## 7. 风险

- **Gemini 3.1 Flash 模型 ID** 需要核对：仓库 `llm_use_cases.py` 里出现过 `gemini-3-flash-preview` 但 OpenRouter 官方目录可能是 `google/gemini-3.1-flash-preview` 或别的名字。脚本启动前先发一条最便宜的请求 ping 一下确认。
- **Schema 校验**：`generate_localized_translation` 内部用 JSON schema 校验，Gemini 系列偶尔返非合规 JSON。已计入错误处理。
- **评估主观性**：我打的 4 维分数本身有主观性，但**横向对比**（同一原稿同一目标语下三家比较）能降低主观偏差影响。

## 8. 后续

报告完成后：

- 如果 Gemini Pro 整体不输 Sonnet 4.6 → 切换 `video_translate.localize` 绑定到 Gemini Pro，月省 ~¥21
- 如果 Gemini Flash 在多数语种上够用 → 进一步考虑用 Flash，月省 ~¥27
- 如果差距明显 → 维持 Sonnet 4.6，把节省方向放到别处（例如 cache prompt）
- 如果某些小语种特别拉胯（比如 Flash 在 ja/nl 不行）→ 按语种分层路由
