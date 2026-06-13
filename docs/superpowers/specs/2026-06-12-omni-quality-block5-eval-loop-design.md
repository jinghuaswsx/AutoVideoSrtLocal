# Block 5 — 质量评估闭环升级（P2）

- **日期**: 2026-06-12
- **状态**: Approved（待实施）
- **总览**: [2026-06-12-omni-quality-overview.md](2026-06-12-omni-quality-overview.md)（红线必读）
- **实施计划**: [plans/2026-06-12-omni-quality-block5-eval-loop.md](../plans/2026-06-12-omni-quality-block5-eval-loop.md)
- **改动层**: 评估 prompt/schema、阈值展示、聚合报表；不动收敛与时长逻辑 → 音画对齐零影响

## 背景与问题

每个任务 subtitle 完成后异步跑一次翻译质量评估（`appcore/quality_assessment.py` + `pipeline/translation_quality.py`），三输入（原始 ASR / 译文 / TTS 二次 ASR）双分（translation_score / tts_score）入库 `translation_quality_assessments`。问题：

1. **裁判弱于选手**：评估绑定 `gemini-3.1-flash-lite`，给 `gemini-3-flash` 的产出打分，区分度与可信度存疑。
2. **没有首尾维度**：评估维度（semantic_fidelity / completeness / naturalness）不覆盖产品硬需求——首句钩子强度、尾句收尾/CTA 完整性。Block 3 在过程中守门，这里要在结果上验收同样的东西。
3. **无消费闭环**：分数只能点进任务看，低分任务不醒目、无聚合趋势（按语言/按模型），评估结果驱动不了任何动作。

## 目标

1. 裁判模型升级为 `gemini-3.5-flash`（项目内 `omni_av_sync.verify` 等已在用）。
2. 评估维度新增 `hook_strength`（0-100，首句作为前 3 秒钩子的强度）与 `ending_integrity`（0-100，尾句收尾/CTA 相对源结尾意图的完整性，**若发生尾部截断应显著低分**）。
3. 低分任务在列表与详情页醒目标红；后台新增按目标语言的近 30 天聚合视图。

## 非目标

- 不做自动重译/自动重跑（标红 + 人工决策；自动化留到有数据后再说）。
- 不改 `translation_quality_assessments` 表结构（新维度放进既有 `translation_dimensions` JSON 列）。
- 不动评估触发时机（仍为 subtitle 后异步 + 手动触发）。

## 需求细则

### R1 裁判升级

`appcore/llm_use_cases.py` 的 `translation_quality.assess` 默认绑定改 `openrouter / google/gemini-3.5-flash`（与 `omni_av_sync.verify` 同通道同模型）；`appcore/quality_assessment.py` 的 `_DEFAULT_MODEL` 字符串同步改为 `gemini-3.5-flash`（仅展示用）。验收说明提示管理员同步改现网 DB binding。

### R2 新维度

`pipeline/translation_quality.py`：
- system prompt 的 TRANSLATION_SCORE 维度清单追加：
  - `hook_strength`: does TRANSLATION's first sentence work as a strong 3-second hook (clear outcome / benefit / curiosity / contrast)?
  - `ending_integrity`: does TRANSLATION's final sentence preserve the closing / CTA intent of ORIGINAL_ASR's ending? If the translation ends mid-thought or visibly loses the source's wrap-up/CTA, score low (≤40).
- response json_schema 的 `translation_dimensions` properties/required 同步加这两个 integer 字段。
- `translation_score` 总分口径：保持模型自评总分的现状机制；若代码侧有从子维度求均值的逻辑，则把新维度纳入均值（实施时确认现状后选其一，并在 commit message 注明）。
- 评估输入增强：`appcore/quality_assessment.py::_build_inputs` 把 task 的 `quality_warnings`（Block 3 引入，含尾部截断信息）拼进评估 prompt 的辅助说明（如 `NOTE: the final audio was tail-truncated, N sentences removed`），让 `ending_integrity` 有据可依。task 无该字段时不输出此行（向后兼容）。

### R3 低分标红

- 阈值进 `config.py`：`TRANSLATION_QUALITY_RED_SCORE = 70`（translation_score 低于即红）、`TRANSLATION_QUALITY_ENDING_RED = 60`（ending_integrity 低于即红）。
- 任务详情页：评估卡片（已有展示区）对触红的分项加红色高亮 + 文案「⚠️ 低于质量线」。
- 任务列表页（omni / omni_v2）：行内已展示评估分则对触红任务加红色 badge；**若列表当前不展示评估分**，则实施时调研列表数据接口，最小代价补一个 verdict/score 字段 + 红点（不做大改版）。

### R4 聚合视图

- 新查询（`appcore/quality_assessment.py` 增加 `summarize_recent(days=30) -> list[dict]`）：按 `project_type` + 任务的 `target_lang`（join projects/task 数据可得；如 join 成本高，退化为只按 project_type）聚合近 N 天 `status='done'` 行的 `AVG(translation_score)`、`AVG(tts_score)`、`COUNT(*)`、触红率。
- 展示位：管理后台现有页面体系内加一个简单区块（跟随 `admin_ai_billing.html` 的实现模式：一个 route + 一个模板表格），admin only。不做图表，表格即可。

## 验收标准

1. 单测：新 schema 解析（含新字段的合法响应通过、缺字段抛 `AssessmentResponseInvalidError`）；阈值判断函数；`summarize_recent` SQL（mock DB）。
2. `python3 scripts/pytest_related.py --base origin/master --run` 通过。
3. 人工验收：跑一条任务，评估完成后详情页可见 hook_strength / ending_integrity 两个新分项；构造一条触红任务确认红色标识；后台聚合页有数据。
4. 验收说明含：现网 DB `llm_use_case_bindings` 中 `translation_quality.assess` 需管理员同步改为 `openrouter / google/gemini-3.5-flash`。
