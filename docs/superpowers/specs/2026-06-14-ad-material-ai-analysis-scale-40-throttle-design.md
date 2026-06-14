# 投放素材 AI 分析：产品池放大到 40 + google_wj 自适应节流/重试设计

日期：2026-06-14
模块：`appcore.ad_material_ai_analysis`（左侧大菜单「投放素材AI分析」，**不涉及** 素材管理子 Tab 的 `AI素材军师`）
代码锚点：`docs/superpowers/specs/2026-06-09-ai-material-strategist-project-design.md#2026-06-10-功能拆分纠偏`

## 1. 背景与目标

用户两条诉求：

1. **产品池 20 → 40**：左侧「投放素材AI分析」一次项目评估的产品数从 20 提到 40，且 40 个产品都要经过 AI 选品复评（扩候选覆盖全部 40），而非靠规则分补齐。
2. **google_wj 通道的健壮性**：当评估走 Google WG（代码 `PROVIDER_CODE = "google_wj"`）通道时，每个产品详细评估完成后间隔 ~10 秒；并加一套**频率动态监控（自适应调速 + 进度页可视化）** 与**重试机制**，保证整轮（约 86 次调用）评估不被限流打断、最终跑完出结果。

核心目标：**40 个产品的投放素材 AI 分析在限流压力下也能稳定跑完**，个别确实失败的环节降级兜底并明确标注。

## 2. 范围与非目标

**范围**：仅 `appcore/ad_material_ai_analysis.py` 及其前端 `web/static/ad_material_ai_analysis.js`；新增独立节流模块 `appcore/ad_material_throttle.py`。

**非目标（YAGNI）**：
- 不改 `appcore/ai_material_strategist`（独立的 AI素材军师，Top 30，与本功能不共享任何命名空间）。
- 不改通用 `appcore/llm_client.py` 或 `appcore/llm_providers/*` adapter 的重试（adapter 层已有瞬时重试，见 §4）。
- 不做跨项目的限流配额中心、不做前端 WebSocket 推送（沿用现有进度轮询）。

## 3. 现状（代码锚点）

| 项 | 位置 | 现状 |
|---|---|---|
| 产品池大小 | `_PROJECT_TOP_N = 20`（:34） | 控制 ranking 最终选品数、`_select_products` 截断、`build_preview` |
| 规则预筛候选上限 | `_MAX_AI_CANDIDATES = 60`（:33） | 主流程 `score_product_rows(..., limit=_MAX_AI_CANDIDATES)`（:3363） |
| 选品排序 | `_run_ai_ranking`（:1860） | 60 候选 → `_snake_batches(candidates, 20)`=3 批 → 每批 prompt「最多输出 Top10」（:1869）→ merged ≈30 → final「最终 Top20」（:1904）→ `[:_PROJECT_TOP_N]` |
| 不足补齐 | `_select_products`（:2918-2929） | AI 选不足 `_PROJECT_TOP_N` 时按规则候选顺序补齐再截断 |
| 调用级节流 | `_pace_llm()`（:459）/ `_LAST_LLM_AT`（:448） | 全局每次 `invoke_generate` 前最小间隔 2 秒（env `AD_MATERIAL_AI_ANALYSIS_LLM_SPACING_SECONDS`） |
| LLM 调用点（4 处） | :1872/1907（ranking）、:2446（素材评审）、:4663（国家评审） | 均 `_pace_llm()` + `llm_client.invoke_generate(provider_override="google_wj")` |
| 逐产品主循环 | `for rank_no, product in enumerate(selected, ...)`（:3397） | 每产品 2 次调用（素材 + 5国合并）；尾部 `_upsert_product_result` + checkpoint（:3486-3494） |
| 重试 | 无（上层） | `invoke_generate` 出错直接 raise（`llm_client.py:328`），调用方 try/except → 规则兜底 |
| 进度 JSON | `_initial_progress`（:107）/ `_progress_update`（:150） | 含 `status/percent/steps/product_progress/logs/runner_state/runner_heartbeat_at` |
| 前端进度渲染 | `ad_material_ai_analysis.js` :1001-1063 | 渲染 `percent/current_step_label/message/product_progress/steps/logs(最近6条)` |

**adapter 层既有重试**（`appcore/llm_providers/gemini_vertex_adapter.py:212-256`，`GoogleWJVertexAdapter`）：`for attempt in range(3)`，对 `_is_retryable`（状态码 ∈ `{429,500,502,503,504}` 或 `genai_errors.ServerError`，见 `_helpers/gemini_calls.py:29,156`）退避 `2**attempt`（1s/2s），3 次耗尽抛 `RuntimeError("Vertex Gemini call failed: ...")`。**只能扛秒级抖动**；连续高频调用遇通道级持续限流时迅速耗尽并上抛。

## 4. 设计

### 4.1 产品池 40 + 扩候选（需求 1）

- `_PROJECT_TOP_N`：20 → **40**。
- `_MAX_AI_CANDIDATES`：60 → **80**（规则预筛候选池扩大，给 AI 选品足够素材）。
- ranking 输出量调整，保证 AI 实际排得出 ≥40：
  - 分批仍 `_snake_batches(candidates, 20)`（80 → 4 批）。
  - 每批 prompt 文案「本批最多输出 Top10」→ **Top14**（4×14=56 merged，>40 留余量）。
  - final prompt「从所有批次候选里输出最终 Top20」→ **Top40**。
- 文案同步（避免 prompt 仍告知模型「Top20」导致 final 只吐 20 个）：
  - `PROGRESS_STEPS` 中 `ai_ranking` 步骤 label/描述「Top 20 AI 复评」→「Top 40 AI 复评」（:66）。
  - checkpoint 文案：「分批复评 Top 20」（:3380）、「复用已保存 Top 20 排名」（:3377）、「不在本轮 Top 20 内」（:3496）等统一改 40。
- `_select_products` 补齐逻辑不变（候选 80 足以补到 40），但正常情况下 AI 已覆盖 40。

> 说明：无论是否经 AI 选品，被选中的 40 个产品都会走完整的逐产品详细评估（素材 + 国家）。扩候选只让「选品排序」也覆盖全 40。

### 4.2 新增节流/重试模块 `appcore/ad_material_throttle.py`（需求 2 核心）

单一职责、可单测，**仅对 `provider_code == "google_wj"` 启用强节流**；其它 provider 退化为「只走调用级最小间隔、不做长退避重试」。与 adapter 层分层互补：adapter 管秒级抖动重试，本模块管**调用间主动节流 + 通道级持续限流的长退避重试 + 可观测**。

接口（草案）：

```python
class GoogleWjThrottle:
    def __init__(self, *, provider_code: str, on_event=None,
                 config: ThrottleConfig | None = None,
                 sleep=time.sleep, monotonic=time.monotonic):
        # sleep/monotonic 可注入，便于单测不真睡

    def mark_product_boundary(self) -> None:
        """进入新产品前调用：下一次调用前至少满足 base_interval（10s）。"""

    def guarded_invoke(self, fn, *, stage: str, product_id=None) -> dict:
        """包裹一次 llm_client.invoke_generate：
        1) 调用前 sleep 到满足 当前自适应间隔 / 产品级基线 / 调用级最小间隔 的较大者
        2) 执行 fn()
        3) 命中限流类异常(_classify) → 拉长 current_interval(×factor 封顶) +
           长退避(base*2**retry 封顶) 重试，直到 max_retries；每步 on_event
        4) 成功 → 记连续成功数，达 recover_successes 次后 current_interval ÷factor 回落到基线
        5) 重试耗尽 → raise(上抛给调用方走规则兜底)；非限流异常不重试直接 raise
        """

    def note_degraded(self, *, stage, product_id, reason) -> None:
        """调用方走兜底后回报一次降级，累计 degraded 计数。"""

    def snapshot(self) -> dict:
        """当前 throttle 状态，供 progress 可视化。"""
```

**限流信号识别 `_classify(exc)`**（上层拿到的是 adapter 包装后的异常，原始状态码在异常链里）：
- 遍历 `exc` 及其 `__cause__` / `__context__`，取 `code` / `status_code`，命中 `{429,500,502,503,504}` → 限流可重试。
- 否则对 `str(exc)`（小写）做关键词匹配：`429`、`resource_exhausted`、`rate limit`/`rate_limit`、`quota`、`too many requests`、`unavailable`、`deadline`、`timeout`、`vertex gemini call failed` → 可重试。
- 其余（schema 校验、解析、鉴权等）→ 不可重试。

**自适应曲线**：基线 `base_interval`（10s）。命中限流：`current_interval = min(current_interval * factor, adaptive_max)`，并对该次调用按 `min(backoff_base * 2**retry, backoff_max)` 退避后重试。连续成功 `recover_successes` 次：`current_interval = max(current_interval / factor, base_interval)`。

### 4.3 接入点

`ad_material_ai_analysis.py` 项目运行函数内实例化一个 `throttle`（注入 `on_event` 回调，把事件写进 progress）：

- 4 个调用点把 `_pace_llm(); result = llm_client.invoke_generate(...)` 改为
  `result = throttle.guarded_invoke(lambda: llm_client.invoke_generate(...), stage="batch_rank"/"final_rank"/"material_review"/"country_review", product_id=...)`。
- 逐产品主循环（:3397）每个产品开始处调用 `throttle.mark_product_boundary()`，实现「每个产品详细评估之间间隔 ~10 秒」。
- 调用方现有 try/except 兜底路径里追加 `throttle.note_degraded(...)`。
- 4 处迁移后，模块级 `_pace_llm` / `_LAST_LLM_AT` 移除，调用级最小间隔逻辑收敛进 throttle（保留 env 名兼容）。

### 4.4 重试耗尽 → 降级兜底 + 标注（默认行为）

- 限流类调用长退避重试耗尽 → 上抛 → 调用方走现有规则兜底（`_fallback_product_analysis` / `_fallback_country_review` / `_fallback_ranking`），`note_degraded` 计数 +1，progress logs 记 `warning`。
- 结尾 `_summarize_project` 增加「降级清单」：列出哪些产品/环节因限流走了兜底，便于事后针对性重跑。
- 外层项目 try/except（:3535）保持，保证项目状态最终收敛（success / failed）。

### 4.5 可视化：`progress["throttle"]`（需求 2 的「监控」）

`_progress_update` / 新回调把 `throttle.snapshot()` 写入 `progress["throttle"]`：

```jsonc
{
  "enabled": true,            // provider==google_wj 才 true
  "base_interval": 10,
  "current_interval": 10.0,   // 当前自适应间隔
  "retrying": false,
  "current_retry": 0,
  "max_retries": 4,
  "rate_limit_hits": 0,       // 累计限流命中
  "degraded": 0,              // 累计降级环节数
  "last_event": "",
  "updated_at": "..."
}
```

关键事件（命中限流、开始第 N 次重试、回落、降级）写入现有 `progress.logs`（前端已渲染最近 6 条）。

前端 `ad_material_ai_analysis.js`：在 run 卡片 `renderProgressSteps` 之后、`renderProgressLogs` 之前，新增 `renderThrottle(progress.throttle)`，仅 `enabled` 时显示一块「通道节流/重试」状态：当前间隔、是否重试中（第 N/M 次）、累计限流命中、降级数。脚本引用加版本参数防缓存。

### 4.6 配置（env，给默认值，可线上调参不改码）

| env | 默认 | 含义 |
|---|---|---|
| `AD_MATERIAL_AI_ANALYSIS_PRODUCT_SPACING_SECONDS` | 10 | 产品级基线间隔 |
| `AD_MATERIAL_AI_ANALYSIS_LLM_SPACING_SECONDS` | 2 | 调用级最小间隔（沿用） |
| `AD_MATERIAL_AI_ANALYSIS_LLM_MAX_RETRIES` | 4 | 上层任务级重试次数（adapter 3 次之外） |
| `AD_MATERIAL_AI_ANALYSIS_LLM_BACKOFF_BASE_SECONDS` | 10 | 任务级退避基数 |
| `AD_MATERIAL_AI_ANALYSIS_LLM_BACKOFF_MAX_SECONDS` | 120 | 退避封顶 |
| `AD_MATERIAL_AI_ANALYSIS_THROTTLE_FACTOR` | 2.0 | 自适应放大/回落系数 |
| `AD_MATERIAL_AI_ANALYSIS_THROTTLE_MAX_SECONDS` | 60 | 自适应间隔封顶 |
| `AD_MATERIAL_AI_ANALYSIS_THROTTLE_RECOVER_SUCCESSES` | 3 | 连续成功几次后回落一档 |

## 5. 错误处理与边界

- 限流类异常：长退避重试 → 耗尽降级兜底（§4.4）。
- 非限流异常（schema/解析/鉴权）：不重试，直接走现有兜底。
- `provider != google_wj`：throttle 退化，只保留调用级最小间隔，不加 10 秒/不长退避，`throttle.enabled=false`，前端不显示该块。
- 断电接管：`runner_heartbeat_at` 机制不变；throttle 状态是内存态，接管后重置（不影响正确性，只是计数从 0 起）。
- 时长：约 86 次调用 + 产品间 40×10s≈400s，整轮后台任务约 15–30 分钟（不阻塞页面）。

## 6. 测试策略（按仓库 pytest 最小化规则）

- 新增 `tests/test_ad_material_throttle.py`（注入 fake sleep/clock，不真睡）：
  - 限流异常触发长退避重试，达 `max_retries` 后 raise。
  - 自适应：命中后 `current_interval` 增大并封顶；连续成功 `recover_successes` 次后回落到基线。
  - 非限流异常不重试。
  - `provider != google_wj` 退化（无 10s、无长退避）。
  - `mark_product_boundary` 后下次 `guarded_invoke` 前等待满足基线。
  - `_classify`：异常链 code 与字符串关键词识别。
- `tests/test_ad_material_ai_analysis_routes.py`（及相关单测）：`_PROJECT_TOP_N==40`；mock LLM 下 ranking 扩候选产出 ≥40；`build_preview` Top 数；回归不破。
- 运行方式：`python3 scripts/pytest_related.py --base origin/master --run`；汇报实际 focused tests 与全量是否跳过。

## 7. 验收标准

1. 一次项目评估产品池为 40，正常情况下 40 个均经 AI 选品复评。
2. `provider == google_wj` 时：产品之间间隔 ~10s；限流命中触发自适应拉长 + 长退避重试；连续成功回落到 10s 基线。
3. 进度页能看到「通道节流/重试」状态块与限流/重试/降级日志。
4. 在限流压力下整轮任务仍跑完出结果；被降级的产品/环节在结尾汇总明确列出。
5. 切到非 google_wj 通道时自动退化，不强加 10s/长退避。

## 8. 实施顺序（供 writing-plans 拆解）

1. `ad_material_throttle.py` + 单测（TDD）。
2. `ad_material_ai_analysis.py`：常量 40/80、ranking 文案与输出量、4 调用点接入 throttle、产品边界、降级标注、progress.throttle。
3. 前端 `renderThrottle` + 版本号。
4. focused pytest + 验收。
