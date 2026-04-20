# 视频翻译音画同步(Video Translate v2 — Audio-Visual Sync)设计

## Goal

新增一条"看画面的忠实翻译"管线,作为 `video_translate.av_localize` use case 与现有 `video_translate.localize`(盲翻)并存。核心改进:

1. **看画面翻译**:两阶段(Stage1 多模态画面笔记 + Stage2 纯文本逐句翻译),译文必须匹配画面正在发生的事。
2. **逐句时长硬约束**:译文生成时就按语速模型给定的 `target_chars_range` 控字数,而不是事后 5 轮字数重写。
3. **带货调性注入**:Stage2 prompt 强制"为目标市场做带货短视频配音"身份,并注入产品资料(自动推断 + 可选手填覆盖)。
4. **时长闭环组合策略**:TTS 后按偏差分级处理(±5% 通过 / 5-15% speed 微调 / >15% 局部重写 ≤2 轮 / 兜底 warning + 硬压)。

保持与原 ASR 句的严格时间戳对齐,不重切时间戳。

---

## Confirmed Decisions

| 决策 | 选择 | 理由 |
|---|---|---|
| Q1 定位 | 看画面的忠实翻译 | 原视频话术是已验证的带货骨架,别扔 |
| Q2 对齐粒度 | 严格跟原 ASR 句时间戳 | 原时间戳已是音画对齐的,重切引入无底洞 |
| Q3 画面理解 | 两阶段(笔记 → 翻译) | 可审查 / 可复用 / 可定位问题 / 成本低 |
| Q4 时长控制 | 事前硬约束 + TTS speed 微调 + 局部重写兜底 | 语义扰动最小,调用最少 |
| Q5 带货 context | 调性 + 市场 + 产品资料 | 调性必加、市场必加、产品资料自动推断零负担 |
| Q6 笔记粒度 | 双层(全局摘要 + 逐句) | 翻译需要"此刻画面"+"当前在 Hook/Demo/CTA 哪段" |
| Q7 迁移 | 新 use case 并存(不原地改老代码) | 新文件从零写,失败只影响新任务,回滚简单 |
| Q8 context 来源 | Stage1 自动推断 + 任务级手填覆盖 | 遵循"默认零字段输入"原则 |
| Q9 provider 默认 | Stage1=`gemini_aistudio` / Stage2=`openrouter` | Stage1 要视频多模态;Stage2 纯文本便宜灵活 |

---

## Architecture

### 管线数据流

```
上传视频 → extract → asr → build_script_segments (复用)
       ↓
       ├─ [新] shot_notes  (Stage1, gemini_aistudio, 1 call)
       │     input:  video_path + script_segments
       │     output: {global: {...}, sentences: [{per-asr-note}]}
       │     落:     task.state_json.shot_notes
       ↓
       ├─ [新] av_translate  (Stage2, openrouter, 1 call, 可能分段)
       │     input:  script_segments + shot_notes + av_translate_inputs + speech_rate_model
       │     output: {sentences: [{text, est_chars}]}
       │     落:     task.state_json.variants["av"].sentences[*].text
       ↓
       ├─ tts (复用 ElevenLabs)
       │     逐句合成 + 拼接
       ↓
       ├─ [新] duration_reconcile
       │     逐句算 overshoot_ratio,分支处理(见 "时长闭环")
       │     可能回调 av_translate.rewrite_one + tts.regenerate_segment
       ↓
       ├─ subtitle (复用) → SRT
       ↓
       └─ 落 state_json.variants["av"]
```

### 文件布局

**新增**:
- `pipeline/shot_notes.py` — Stage1 入口 `generate_shot_notes(task, video_path, script_segments) -> dict`
- `pipeline/av_translate.py` — Stage2 入口 `generate_av_localized_translation(task, script_segments, shot_notes, av_inputs) -> dict`;以及 `rewrite_one(task, asr_index, prev_text, overshoot_sec, new_target_chars_range) -> str`
- `pipeline/duration_reconcile.py` — `reconcile_duration(task, av_output, tts_output) -> final_sentences`

**改动**:
- `appcore/llm_use_cases.py` — 注册 `video_translate.shot_notes`(默认 `gemini_aistudio`)+ `video_translate.av_localize`(默认 `openrouter`)
- `appcore/runtime.py` — 新 run 分支(`run_av_localize`),不动老 `run_localize`;v2 的 variant key 为 `"av"`(与老 `"normal"` / `"hook_cta"` 并存)
- `appcore/task_state.py` — 新字段(additive,见"Data Model")
- `web/routes/` + `web/static/` — 任务创建表单加 `target_language / target_market / 产品 overrides`;任务详情页加"画面笔记预览"+"时长警告列表"

**不动**:`pipeline/translate.py`、`pipeline/localization.py`、老 use case `video_translate.localize`。

---

## Data Model(state_json additive)

```python
task.av_translate_inputs = {
  "target_language": "en",                 # 必填
  "target_language_name": "English",       # UI 展示
  "target_market": "US",                   # 必填,白名单: US/UK/AU/CA/SEA/JP/OTHER
  "product_overrides": {                   # 全空则完全走 Stage1 推断
    "product_name": null,
    "brand": null,
    "selling_points": null,   # list[str] | null
    "price": null,            # "$19.99" 字符串
    "target_audience": null,
    "extra_info": null,
  },
}

task.shot_notes = {
  "global": {
    "product_name": str | None,
    "category": str | None,
    "overall_theme": str,
    "hook_range": [int, int] | None,
    "demo_range": [int, int] | None,
    "proof_range": [int, int] | None,
    "cta_range": [int, int] | None,
    "observed_selling_points": list[str],
    "price_mentioned": str | None,
    "on_screen_persistent_text": list[str],
    "pacing_note": str,
  },
  "sentences": [
    {
      "asr_index": int,
      "start_time": float,
      "end_time": float,
      "scene": str,
      "action": str,
      "on_screen_text": list[str],
      "product_visible": bool,
      "shot_type": "close_up" | "medium" | "wide" | "pov" | "overlay",
      "emotion_hint": str,
    }
  ],
  "generated_at": ts,
  "model": {"provider": "...", "model": "..."},
}

task.variants["av"] = {
  "sentences": [
    {
      "asr_index": int,
      "start_time": float,
      "end_time": float,
      "target_duration": float,
      "target_chars_range": [int, int],
      "text": str,
      "est_chars": int,
      "tts_path": str,
      "tts_duration": float,
      "speed": float,                    # 最终 TTS speed
      "rewrite_rounds": int,             # 0..2
      "status": "ok" | "speed_adjusted" | "ok_short" | "rewritten"
                | "warning_overshoot" | "warning_short",
    }
  ],
  "srt_path": str,
  "tts_full_path": str,
}
```

老任务无这些字段,UI/runtime 读取时走 getattr/default,不崩。

---

## Stage1 Shot Notes — Prompt & Schema

**调用**:`llm_client.invoke_generate("video_translate.shot_notes", prompt, media=[video_path], response_schema=SHOT_NOTES_SCHEMA, ...)`

**prompt 核心指令**(中文,内部 prompt):

1. 输入信息:目标市场 {target_market},目标语言 {target_language},原 ASR 句列表(index / start / end / text)。
2. 任务:
   - 全局层:识别产品名、类目、整体主题、Hook/Demo/Proof/CTA 对应的 ASR index 范围、观察到的卖点、主播提到的价格、画面常驻硬字幕/水印、整体节奏感。
   - 逐句层:对每个 ASR index,输出此时画面的 scene/action/on_screen_text/product_visible/shot_type/emotion_hint。
3. 输出:严格按 JSON schema,不省略字段(未知填 null 或空数组);sentences 数组长度必须等于输入 ASR 句数。

**schema**:见 Data Model 中 `task.shot_notes` 的形状,用 `response_schema` 或 `response_format=json_schema` 强约束。

**失败处理**:
- 调用失败:重试 2 次(指数退避),仍失败则 task.status = `failed` 并落错误详情到 `steps`。
- 漏段(sentences 数量 < ASR 数):缺失的 index 自动补 `{shot_context: null}`,Stage2 prompt 对这些句子写"画面信息缺失,按原句保守翻译"。

---

## Stage2 av_translate — Prompt & Schema

**调用**:`llm_client.invoke_chat("video_translate.av_localize", messages, response_format=AV_TRANSLATE_SCHEMA, ...)`

**系统层 prompt**(固定):

> 你是专业的{target_market}市场带货短视频本地化配音师。规则:
> 1. 服从原视频的 Hook / 卖点 / CTA 骨架顺序,不重排。
> 2. 每句译文必须对应提供的 shot_context(此刻画面)。
> 3. 每句字符数必须落在给定的 target_chars_range 内(硬约束)。
> 4. 带货语气:钩子化、痛点化、口语化,静音看字幕能看懂;忌新闻腔、直译腔。
> 5. 产品特写镜头优先说产品名 / 卖点;无产品画面时讲故事 / 痛点 / 证据。
> 6. 文化专有梗不硬翻,换成 {target_market} 习惯(俚语 / 货币 / 节日 / 购物术语)。

**用户层**包含:

- `global_context` 合并规则:
  - 来自 `av_translate_inputs.product_overrides` 优先(非空即采用):`product_name, brand, selling_points, price, target_audience, extra_info`
  - 来自 `shot_notes.global` 的只读字段:`category, overall_theme, structure_ranges(从 hook/demo/proof/cta_range 拼), pacing_note, observed_selling_points, price_mentioned`
  - overrides 某字段为空时,用 shot_notes.global 对应推断字段回退(如 `product_name` 回退 `shot_notes.global.product_name`,`selling_points` 回退 `observed_selling_points`,`price` 回退 `price_mentioned`)
- `target_language`、`target_market`:从 `av_translate_inputs` 直接取
- `sentences` 数组:每条 `{asr_index, start_time, end_time, source_text, shot_context, role_in_structure, target_duration, target_chars_range}`
  - `role_in_structure` ∈ `{hook, demo, proof, cta, unknown}`,由 `shot_notes.global` 的 range 字段映射得出(若 asr_index 落在多个 range 内,按 hook > cta > demo > proof 优先级;无 range 覆盖则 `unknown`)

**输出 schema**:
```json
{"sentences":[{"asr_index":0,"text":"...","est_chars":55,"notes":"optional"}]}
```

**调用策略**:
- MVP 一次批量:短视频 ASR 句 10-30 条,JSON 输出一般 ≤ 4K tokens。
- 如果单次输出超 8K 或被模型截断:按 `structure_ranges` 分段(Hook / Demo / Proof / CTA)拆 2-4 次调用。
- 实现时先一次批量,监控 token / 截断率,必要时加拆分。

**失败处理**:
- 调用失败:重试 1 次;仍失败 → task.status = `failed`。
- 输出 sentences 数量不对:按 asr_index 逐条核对,缺失的句子单独重跑(rewrite_one)。

### rewrite_one 接口

```python
def rewrite_one(
    task, asr_index, prev_text, overshoot_sec,
    new_target_chars_range, shot_context, global_context,
) -> str
```

在完整上下文基础上,追加:

> 上一版译文:"{prev_text}"。TTS 实测超出目标 {overshoot} 秒。请重写到 {min}-{max} 字符,保留卖点和画面贴合,优先砍修饰词 / 感叹 / 重复,不改 Hook/CTA 意图。

---

## 时长闭环(duration_reconcile)

### target_chars_range 计算

```python
cps = speech_rate_model.get(voice, target_language)  # 复用现有 model
target_chars_min = floor(cps × target_duration × 0.92)
target_chars_max = ceil(cps × target_duration × 1.08)
```

±8% buffer 给 LLM 字数控制误差留空间。

### 分支处理(逐句)

```
overshoot_ratio = (tts_duration - target_duration) / target_duration

-5% ≤ r ≤ +5%     → status="ok",              speed=1.0
+5% < r ≤ +15%    → status="speed_adjusted",  speed=clamp(tts/target, 1.0, 1.08)
-15% ≤ r < -5%    → status="ok_short",        speed=1.0(保留静音段)
r > +15%          → 局部重写 ≤ 2 轮:
                      new_target_chars = old × (target / tts)
                      调 av_translate.rewrite_one → tts.regenerate_segment
                    2 轮后仍 > 15%:
                      status="warning_overshoot"
                      speed=1.12 兜底硬压(接受轻微金属感)
r < -15%          → status="warning_short"(不自动加字,UI 提示运营手改)
```

### 兜底原则

即使最坏情况也输出完整 SRT + TTS(拼接音频),warning 句子在任务详情页醒目标注,运营可单句手改后重跑。

---

## LLM Use Case 注册

`appcore/llm_use_cases.py` 新增两条:

```python
"video_translate.shot_notes": _uc(
    provider="gemini_aistudio",
    model="<与 video_score.run 同档,由 bindings 系统决定>",
    usage_log_service="video_translate_shot_notes",
),
"video_translate.av_localize": _uc(
    provider="openrouter",
    model="<与 video_translate.localize 同档>",
    usage_log_service="video_translate_av_localize",
),
```

两个 use case 均支持 `/settings?tab=bindings` 管理员覆盖。

---

## UI 改动

### 任务创建表单(新)

- `target_language`:下拉(默认 English)
- `target_market`:下拉(US / UK / AU / CA / SEA / JP / OTHER,默认 US)
- 折叠区"带货资料微调(可选,留空自动从视频识别)":
  - `product_name`、`brand`、`selling_points`(多行)、`price`、`target_audience`、`extra_info`

### 任务详情页(新)

- **画面笔记预览卡片**:展示 `shot_notes.global`(产品名、主题、结构分段)+ 可展开"逐句画面笔记"(表格:asr_index / scene / action / product_visible)。
- **时长警告列表**:筛出 `status ∈ {warning_overshoot, warning_short}` 的句子,显示目标时长 / 实测时长 / 偏差 / 当前译文;每行带"手动重写"按钮(弹窗让运营改译文 → 重跑 TTS)。

---

## Migration & Rollback

- 默认全局走 `video_translate.av_localize`(v2)。
- 老 `video_translate.localize` 代码路径保留 3 个月不动。
- **回滚开关**:在 `runtime.py` 新 `run_av_localize` 入口最顶端加 `if settings.av_localize_fallback: return run_localize(...)` 分支;settings 里加布尔开关,管理员一键回退。
- DB 仅 additive(往 state_json 加 key),老任务打开 UI 不崩。
- v2 稳定 3 个月后,开另一个清理 PR 删 `pipeline/translate.py:generate_localized_translation` 及其调用链。

---

## Risks

- **Stage1 对产品识别不稳**:LLM 可能把"无糖酸奶"识别成"酸奶饮料",或漏看品牌。缓解:运营可通过 `product_overrides` 硬覆盖;Stage1 推断值保留在 `shot_notes.global`,UI 展示供对照。
- **Stage2 字数控制不 100% 准**:LLM 输出可能偏离 `target_chars_range` 5-10%。这是设计预期,由 `duration_reconcile` 的 speed/rewrite 兜底。
- **单次 JSON 输出截断**:超长视频(>60s,ASR > 30 句)可能触发 token 截断。缓解:监控截断率,必要时按 structure_ranges 分段调用。
- **多模态调用延迟**:gemini_aistudio 视频输入上传 + 推理可能 10-30s,MVP 同步等待;如影响 UX 再考虑异步化。
- **带货感主观**:Stage2 prompt 里"带货语气"是软约束,LLM 输出可能偏新闻腔。缓解:prompt 里给 2-3 个 few-shot 示例(Hook / Demo / CTA 各一条),spec 迭代期收集坏例子反补 prompt。

---

## Test Plan

### 单元测试(`tests/pipeline/`)

- `test_shot_notes.py`
  - mock `llm_client.invoke_generate` 返回固定 JSON,验 schema 解析、正常落盘
  - 验 sentences 漏段的降级补齐
  - 验调用失败的重试与最终失败路径
- `test_av_translate.py`
  - mock openrouter 返回,验字符数边界(est_chars 在 range 内)、asr_index 连续性
  - 验 `rewrite_one` prompt 带上了 overshoot 反馈
  - 验一次批量与 structure_ranges 分段两种路径
- `test_duration_reconcile.py`
  - 参数化 `(target_duration, tts_duration)` 覆盖每个分支(±5% / 5-15% / ±15%+ / 负向)
  - 验重写 2 轮仍超的兜底路径(speed=1.12 + warning_overshoot)
  - 验 `-15%+` 不触发重写,status=warning_short

### 集成测试

- 选 3 段线上已有视频(好 / 中 / 差三档,30-60s 各一),跑完整 v2 管线。
- 人工对比 v1 / v2:
  - 声画同步度(当下画面 vs 译文讲的事)
  - 带货感(Hook / 痛点 / 卖点 / CTA 是否清晰)
  - 时长偏差分布(直方图 + warning 比例)
- 对比通过门槛:声画同步主观评分 v2 > v1;warning 比例 ≤ 10%。

### UI 冒烟

- 任务创建表单:3 种 override 组合(全空 / 部分填 / 全填)都能触发 v2 完整流程。
- 详情页:画面笔记正常展示;warning 列表手动重写能走通。

### 不做

- 端到端 Playwright 测试(`webapp-testing`):MVP 暂不做,手工验证。
