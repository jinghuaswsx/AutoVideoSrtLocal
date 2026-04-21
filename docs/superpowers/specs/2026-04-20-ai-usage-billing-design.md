# AI 用量账单与定价管理 — 设计 Spec

**日期**：2026-04-20
**分支**：`feature/ai-usage-billing`
**Worktree**：`.worktrees/ai-usage-billing`
**实现方**：Codex
**设计方**：Claude

---

## 0. 目标

项目里大量调用 AI 模型（LLM / 图像 / TTS / ASR），当前缺一个能看"每一次请求"的账单视图，也没有费用统计。落地后要具备：

1. **每一次** AI 付费请求都落到 `usage_logs`，带模块、功能、用户、模型、供应商、费用（CNY）
2. 管理员可在 `/admin/ai-usage` 看**逐条明细**，支持多维筛选、分组汇总、导出 CSV
3. 普通用户可在 `/my-ai-usage` 看自己的账单
4. 管理员可在 `/admin/settings/ai-pricing` 编辑每个 provider/model 的单价
5. 费用写入时按当时价格表算好锁定（后续改价不改历史）
6. OpenRouter 响应里直接带 cost 字段（`usage.include=true`），直接用；其他 provider 查价格表算

## 1. 非目标

- 不做多币种结算（只 CNY，USD→CNY 固定汇率 6.8）
- 不做自动汇率更新
- 不做历史数据回填（TRUNCATE `usage_logs` 从零开始，方案 C 已确认）
- 不做每日/每月聚合物化表（真有性能问题再加）
- 不做费用预算告警 / 限流（后续迭代）
- 不新增 provider

## 2. 改造范围（选型决策摘要）

| 决策 | 选择 | 理由 |
|---|---|---|
| 纳入范围 | **C** = 全部 AI 计费（LLM+图像+TTS+ASR） | 诉求要求完整账单 |
| 明细粒度 | **A** = 每请求一行 | 诉求要能翻到每条 |
| 货币 | RMB，USD→CNY 固定 6.8 | 国内视角 |
| 价格表 | **C** = OpenRouter 响应 cost + 其他查表 | OR 模型太多自动最省；其他 provider 种子+管理员可编辑 |
| 费用策略 | 写入时锁定，后续改价不回填 | 账单语义=当时多少钱 |
| use_case 覆盖 | **A** = 全量 AI 调用 | 按"模块→功能"两级归类 |
| 老数据 | **C** = TRUNCATE | 已确认 |
| 表结构 | **P1** = 原地扩展 `usage_logs` + 新增 `ai_model_prices` | 改动最小 |

## 3. 数据模型

### 3.1 `usage_logs` 扩列

`TRUNCATE` 后执行：

```sql
ALTER TABLE usage_logs
  ADD COLUMN use_case_code VARCHAR(64)  DEFAULT NULL AFTER service,
  ADD COLUMN module        VARCHAR(32)  DEFAULT NULL AFTER use_case_code,
  ADD COLUMN provider      VARCHAR(32)  DEFAULT NULL AFTER module,
  ADD COLUMN request_units INT          DEFAULT NULL AFTER audio_duration_seconds,
  ADD COLUMN units_type    VARCHAR(16)  DEFAULT NULL AFTER request_units,
  ADD COLUMN cost_cny      DECIMAL(12,6) DEFAULT NULL AFTER units_type,
  ADD COLUMN cost_source   ENUM('response','pricebook','unknown')
                             NOT NULL DEFAULT 'unknown' AFTER cost_cny,
  ADD INDEX idx_called_at (called_at),
  ADD INDEX idx_user_module (user_id, module, called_at),
  ADD INDEX idx_use_case (use_case_code, called_at);
```

字段说明：

| 列 | 含义 |
|---|---|
| `use_case_code` | 业务功能代码，如 `video_translate.localize` / `video_translate.tts` |
| `module` | 业务模块，如 `video_translate` / `copywriting` / `video_analysis` / `image` / `text_translate` |
| `provider` | `openrouter` / `doubao` / `gemini_aistudio` / `gemini_vertex` / `elevenlabs` / `doubao_asr` |
| `request_units` + `units_type` | 异构计量统一列：`tokens` / `chars` / `seconds` / `images` |
| `cost_cny` | 本次调用人民币费用（DECIMAL(12,6)，支持到分的 1/10000 精度） |
| `cost_source` | `response`（OpenRouter 响应自带）/ `pricebook`（查表算）/ `unknown`（缺 token 或缺定价） |

保留列：`input_tokens` / `output_tokens` / `audio_duration_seconds` / `extra_data` 照旧；新增的 `request_units` 语义和旧列有重叠——约定 **LLM tokens 的调用必须同时写 `input_tokens`+`output_tokens` 和 `request_units=input+output`/`units_type='tokens'`**，便于聚合和展示统一走 `request_units`。

### 3.2 新表 `ai_model_prices`

```sql
CREATE TABLE IF NOT EXISTS ai_model_prices (
  id               INT AUTO_INCREMENT PRIMARY KEY,
  provider         VARCHAR(32)  NOT NULL,
  model            VARCHAR(128) NOT NULL,
  units_type       VARCHAR(16)  NOT NULL,          -- tokens/chars/seconds/images
  unit_input_cny   DECIMAL(14,8) DEFAULT NULL,     -- 每 1 单位（单个 token/char/sec/image）的 CNY
  unit_output_cny  DECIMAL(14,8) DEFAULT NULL,
  unit_flat_cny    DECIMAL(14,8) DEFAULT NULL,     -- 不分 in/out 的单价（chars/seconds/images 用）
  note             VARCHAR(255) DEFAULT NULL,
  updated_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                              ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uniq_provider_model (provider, model)
);
```

通配：`model='*'` 表示"本 provider 所有模型的兜底价"（ElevenLabs 每次调用模型名不一，用 `*` 即可）。查表时先精确匹配 `(provider, model)`，未命中再查 `(provider, '*')`。

### 3.3 种子数据

迁移脚本同文件 `INSERT` 9 条（所有单价备注都含"待复核"，由用户在定价页修正）：

```sql
INSERT INTO ai_model_prices (provider, model, units_type, unit_input_cny, unit_output_cny, unit_flat_cny, note)
VALUES
  ('gemini_aistudio','gemini-3.1-pro-preview','tokens',0.0000578,0.0002312,NULL,'待复核：8.5/34 USD/M ×6.8'),
  ('gemini_aistudio','gemini-2.5-flash','tokens',0.00000204,0.00000816,NULL,'待复核：0.3/1.2 USD/M ×6.8'),
  ('gemini_aistudio','gemini-3-pro-image-preview','images',NULL,NULL,0.2652,'待复核：0.039 USD/image ×6.8'),
  ('gemini_vertex','gemini-3.1-flash-lite-preview','tokens',0.00000816,0.00003264,NULL,'待复核：1.2/4.8 USD/M ×6.8'),
  ('gemini_vertex','gemini-3.1-pro-preview','tokens',0.0000578,0.0002312,NULL,'待复核：8.5/34 USD/M ×6.8'),
  ('doubao','doubao-1-5-pro-32k','tokens',0.000006,0.000012,NULL,'待复核：0.006/0.012 RMB/千tok'),
  ('elevenlabs','*','chars',NULL,NULL,0.000165,'待复核：≈0.165 RMB/千字符'),
  ('doubao_asr','*','seconds',NULL,NULL,0.014,'待复核：≈0.014 RMB/秒'),
  ('openrouter','*','tokens',NULL,NULL,NULL,'响应 cost 缺失时兜底，留空不计费');
```

## 4. 写入链路

### 4.1 `usage_log.record()` 签名扩展

[appcore/usage_log.py](appcore/usage_log.py) 新增 keyword-only 参数，全部默认 `None`，不破坏既有调用点：

```python
def record(
    user_id, project_id, service,
    *,
    use_case_code: str | None = None,     # NEW
    module: str | None = None,            # NEW
    provider: str | None = None,          # NEW
    model_name: str | None = None,
    success: bool = True,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    audio_duration_seconds: float | None = None,
    request_units: int | None = None,     # NEW
    units_type: str | None = None,        # NEW
    cost_cny: Decimal | None = None,      # NEW
    cost_source: str = "unknown",         # NEW
    extra_data: dict | None = None,
) -> None
```

内部 SQL 对应 `INSERT INTO usage_logs (...)`。

### 4.2 新门面 `appcore/ai_billing.py`

所有 AI 调用通过这一个函数写库，集中处理"查价格 → 算费用 → 记 log"：

```python
# appcore/ai_billing.py
from decimal import Decimal

def log_request(
    *, use_case_code: str,
    user_id: int | None,
    project_id: str | None,
    provider: str,
    model: str,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    request_units: int | None = None,
    units_type: str = "tokens",
    audio_duration_seconds: float | None = None,
    response_cost_cny: Decimal | None = None,   # OpenRouter 实锁价
    success: bool = True,
    extra: dict | None = None,
) -> None:
    """所有 AI 调用统一入口。
    职责：
      1. 从 USE_CASES 反查 module + service
      2. 若 response_cost_cny 已给（OpenRouter），cost_source='response'
         否则走 pricing.compute_cost_cny()
      3. 调 usage_log.record()
    异常全吞，不影响业务主流程。
    """
    if user_id is None:
        return
    try:
        from appcore.llm_use_cases import get_use_case
        uc = get_use_case(use_case_code)  # 缺失抛 KeyError，外层吞掉
        module = uc["module"]
        service = uc["usage_log_service"]

        # tokens 场景补全 request_units
        if units_type == "tokens" and request_units is None:
            request_units = (input_tokens or 0) + (output_tokens or 0)

        if response_cost_cny is not None:
            cost_cny, cost_source = response_cost_cny, "response"
        else:
            from appcore.pricing import compute_cost_cny
            cost_cny, cost_source = compute_cost_cny(
                provider=provider, model=model, units_type=units_type,
                input_tokens=input_tokens, output_tokens=output_tokens,
                request_units=request_units,
            )

        from appcore import usage_log
        usage_log.record(
            user_id, project_id, service,
            use_case_code=use_case_code, module=module, provider=provider,
            model_name=model, success=success,
            input_tokens=input_tokens, output_tokens=output_tokens,
            audio_duration_seconds=audio_duration_seconds,
            request_units=request_units, units_type=units_type,
            cost_cny=cost_cny, cost_source=cost_source,
            extra_data=extra,
        )
    except Exception:
        import logging
        logging.getLogger(__name__).debug(
            "ai_billing.log_request failed", exc_info=True,
        )
```

### 4.3 调用点改造清单

所有 AI 写 log 的路径都切到 `ai_billing.log_request`：

| 文件 | 原写入 | 改造 |
|---|---|---|
| [appcore/llm_client.py](appcore/llm_client.py) `_log_usage` | 直接 `usage_log.record` | 改调 `ai_billing.log_request`，透传 use_case_code |
| [appcore/gemini.py](appcore/gemini.py) `_log_gemini_usage` | 直接 `usage_log.record` | 改调 `ai_billing.log_request`，use_case 从调用方传入（现有 `service` 参数扩为 `use_case_code`） |
| [appcore/gemini_image.py](appcore/gemini_image.py) `_log_*` | 直接 `usage_log.record` | 同上，use_case_code=`image_translate.generate` |
| [appcore/runtime.py](appcore/runtime.py)（ASR + TTS + 翻译 loop） | `_log_usage(self.user_id, task_id, "doubao_asr", ...)` 等 | 改调 `ai_billing.log_request` |
| [appcore/runtime_de.py](appcore/runtime_de.py) / [appcore/runtime_fr.py](appcore/runtime_fr.py) / [appcore/runtime_multi.py](appcore/runtime_multi.py) | 同上 | 同上 |
| [appcore/copywriting_runtime.py](appcore/copywriting_runtime.py) | 同上 | 同上 |

Codex 做改造时：
- **保持业务逻辑不变**，只改 log 函数调用
- 所有调用点都必须带 `use_case_code`
- 如遇到某个调用点无法明确 use_case_code（比如 ElevenLabs 被多个业务链用），查业务调用栈，用对应 `video_translate.tts`
- 找不到匹配的 use_case code → 新增到 `USE_CASES` 注册表，**不要** 在调用点传硬编码字符串

### 4.4 OpenRouter 特殊路径

[appcore/llm_providers/openrouter_adapter.py](appcore/llm_providers/openrouter_adapter.py) 改动：

1. 请求时注入 `extra_body["usage"] = {"include": True}`
2. 响应里 `resp.usage.cost`（USD）→ 乘 6.8 → 作为 `response_cost_cny` 返回
3. `invoke_chat` 返回的 `usage` dict 新增 `cost_cny` 字段（如有）
4. `llm_client._log_usage` 把 `cost_cny` 透传给 `ai_billing.log_request` 的 `response_cost_cny` 参数

如果某次响应 OpenRouter 没返回 cost（插件冲突等），`response_cost_cny=None` 自动 fallback 到 pricebook 查表（OpenRouter 有通配兜底行，若单价为空 → `cost_source='unknown'`）。

### 4.5 `USE_CASES` 扩充

[appcore/llm_use_cases.py](appcore/llm_use_cases.py) 新增 2 条：

```python
"video_translate.tts": _uc(
    "video_translate.tts", "video_translate", "TTS 配音",
    "ElevenLabs 生成本土化配音",
    "elevenlabs", "<runtime>", "elevenlabs",
),
"video_translate.asr": _uc(
    "video_translate.asr", "video_translate", "ASR 识别",
    "豆包语音识别原视频",
    "doubao_asr", "big-model", "doubao_asr",
),
```

`UseCase` TypedDict 新增字段 `units_type`（所有 14 条补上）：

```python
class UseCase(TypedDict):
    code: str
    module: str
    label: str
    description: str
    default_provider: str
    default_model: str
    usage_log_service: str
    units_type: str  # NEW: tokens/chars/seconds/images
```

14 条 use_case 的 `units_type` 映射：

| use_case_code | units_type |
|---|---|
| video_translate.localize | tokens |
| video_translate.tts_script | tokens |
| video_translate.rewrite | tokens |
| video_translate.tts (NEW) | chars |
| video_translate.asr (NEW) | seconds |
| copywriting.generate | tokens |
| copywriting.rewrite | tokens |
| video_score.run | tokens |
| video_review.analyze | tokens |
| shot_decompose.run | tokens |
| image_translate.detect | tokens |
| image_translate.generate | images |
| link_check.analyze | tokens |
| text_translate.generate | tokens |

## 5. 定价计算模块

### 5.1 `appcore/pricing.py`

```python
# appcore/pricing.py
from decimal import Decimal
from functools import lru_cache
import time
from typing import Literal

_CACHE_TTL = 60  # 秒
_cache: dict = {"expire": 0, "data": {}}  # key=(provider, model) → row dict

def _load_prices():
    now = time.time()
    if now < _cache["expire"]:
        return _cache["data"]
    from appcore.db import query
    rows = query("""
        SELECT provider, model, units_type,
               unit_input_cny, unit_output_cny, unit_flat_cny
        FROM ai_model_prices
    """)
    data = {(r["provider"], r["model"]): r for r in rows}
    _cache["data"] = data
    _cache["expire"] = now + _CACHE_TTL
    return data

def _lookup(provider: str, model: str) -> dict | None:
    data = _load_prices()
    return data.get((provider, model)) or data.get((provider, "*"))

def compute_cost_cny(
    *, provider: str, model: str, units_type: str,
    input_tokens: int | None,
    output_tokens: int | None,
    request_units: int | None,
) -> tuple[Decimal | None, Literal["pricebook", "unknown"]]:
    """返回 (cost_cny, source)。查不到价格或缺乘子 → (None, 'unknown')"""
    row = _lookup(provider, model)
    if not row:
        return None, "unknown"
    try:
        if units_type == "tokens":
            uin = row.get("unit_input_cny")
            uout = row.get("unit_output_cny")
            if uin is None or uout is None:
                return None, "unknown"
            if input_tokens is None or output_tokens is None:
                return None, "unknown"
            cost = Decimal(input_tokens) * uin + Decimal(output_tokens) * uout
            return cost.quantize(Decimal("0.000001")), "pricebook"
        else:  # chars/seconds/images
            flat = row.get("unit_flat_cny")
            if flat is None or request_units is None:
                return None, "unknown"
            cost = Decimal(request_units) * flat
            return cost.quantize(Decimal("0.000001")), "pricebook"
    except Exception:
        return None, "unknown"

def invalidate_cache():
    """管理员改价后由路由调用，立即失效缓存。"""
    _cache["expire"] = 0
```

### 5.2 汇率

`config.USD_TO_CNY = 6.8`（新增）。当前只被 OpenRouter adapter 用（`cost_usd × USD_TO_CNY`）。`ai_model_prices` 表里已经是 CNY 单价，不用再乘汇率。

## 6. UI

### 6.1 API 账单页

**路由**：
- `/admin/ai-usage`（admin only）
- `/my-ai-usage`（普通用户看自己）

新 blueprint `web/routes/admin_ai_billing.py`，现有 `admin_usage.py` 不动（保留旧聚合视图 `/admin/usage` 给兼容）。

**侧栏**：管理分组顶部新增"API 账单"项，链接到 `/admin/ai-usage`（admin）或 `/my-ai-usage`（user）。

**页面结构**（[web/templates/admin_ai_billing.html](web/templates/admin_ai_billing.html)）：

1. **筛选栏**：日期范围（默认今天）/ 用户（admin 可见，user 隐藏）/ 模块 / 功能（随模块联动）/ 供应商 / 模型 / 状态（成功/失败）/ 搜索框（匹配 project_id）/ [导出 CSV] 按钮。筛选条件写到 URL query。
2. **汇总卡片**（4 张）：总费用（¥）/ 总调用数 / 已计费条数 / 未计费条数（`cost_source='unknown'` 计数）。
3. **分组汇总 Tab**（按模块 / 按功能 / 按供应商 / 按模型 / 按用户[admin]）：表格显示各维度的"调用数 / request_units / 费用小计"，按费用降序。
4. **逐条明细表**（分页，50 条/页，URL 带 `page=`）：
    列：时间 / 用户 / 模块 / 功能 / 供应商 / 模型 / units / 费用 / 状态 / 项目 ID
    费用列按 `cost_source` 着色：`response`=绿 / `pricebook`=蓝 / `unknown`=灰"—"
    行点击展开显示完整 `extra_data` JSON 和报错信息

**导出 CSV**：GET `/admin/ai-usage/export.csv?<filter>` 按筛选导出全部匹配行（不分页），列包括所有 `usage_logs` 列 + `extra_data` JSON 字符串。文件名 `ai-usage-YYYYMMDD-HHMMSS.csv`。

**样式**：严格遵守项目 Ocean Blue Admin 设计规范（深海蓝 / 零紫色 / OKLCH tokens），参照 [admin_usage.html](web/templates/admin_usage.html) 的密度和圆角。

### 6.2 定价管理页

**路由**：复用现有 `/settings?tab=pricing` 结构，在 [web/routes/settings.py](web/routes/settings.py) 加 tab。

**页面**（[web/templates/admin_settings.html](web/templates/admin_settings.html) 的新 tab 片段）：

| 列 | 可编辑 |
|---|---|
| 供应商 | ✗（新增时可填） |
| 模型 | ✗（新增时可填，支持 `*` 通配） |
| 计量单位 | ✗（新增时可选 tokens/chars/seconds/images） |
| 输入 ¥/单位 | ✓ |
| 输出 ¥/单位 | ✓ |
| 统一 ¥/单位 | ✓ |
| 备注 | ✓ |
| 操作 | [编辑] [删除] |

**交互**：
- 行内编辑（点[编辑] → 变 input → [保存]/[取消]）
- [+ 新增模型定价] 弹出 inline row
- 保存后调 `pricing.invalidate_cache()`
- 删除二次确认（modal）
- 前端校验：数值字段非负；至少一个单价字段必填
- 页面顶部提示："汇率 USD→CNY 固定为 6.8，修改需联系开发"

**API 路由**：
- `GET  /admin/settings/ai-pricing/list` → JSON
- `POST /admin/settings/ai-pricing` 新建
- `PUT  /admin/settings/ai-pricing/<id>` 更新
- `DELETE /admin/settings/ai-pricing/<id>` 删除

都走 CSRF + admin_required。

## 7. 迁移 & 部署

### 7.1 迁移脚本

单一文件 `db/migrations/2026_04_20_ai_billing.sql`，包含：
1. `TRUNCATE usage_logs`
2. `ALTER TABLE usage_logs` 加 7 列 + 3 索引
3. `CREATE TABLE ai_model_prices`
4. `INSERT` 9 条种子数据

`db/schema.sql` 同步更新（新列 + 新表）。

### 7.2 上线顺序

1. Codex 在 `.worktrees/ai-usage-billing` 改完 → 本地跑全量 pytest
2. 合回 master 前先到测试环境（/opt/autovideosrt-test，端口 9999）：
    a. 跑 migration
    b. 部署代码
    c. 手工跑几次视频翻译 / 文案生成 / 图片翻译，确认 `/admin/ai-usage` 有数据
3. 测试通过 → 合 master → 按 CLAUDE.md"发布"流程上生产 → 生产执行 migration

### 7.3 回滚

- 代码回滚：`git revert` + 重启
- 数据不回滚：新列 `DEFAULT NULL`，老代码无感；`ai_model_prices` 老代码不碰
- `TRUNCATE` 不可逆（已确认）

### 7.4 监控 & 验收

上线 24h 内：
- `SELECT COUNT(*) FROM usage_logs WHERE called_at >= CURDATE()` > 0
- `SELECT cost_source, COUNT(*) FROM usage_logs WHERE called_at >= CURDATE() GROUP BY cost_source`：`unknown` 占比 < 5%
- `/admin/ai-usage` 和 `/admin/settings/ai-pricing` 可用

## 8. 测试要求

### 8.1 单元测试（新增）

- [tests/test_ai_billing.py](tests/test_ai_billing.py)
    - OpenRouter 路径：传 `response_cost_cny` → cost_source='response'
    - Pricebook 路径：查到价 → cost_source='pricebook'，算数对
    - 缺价：cost_source='unknown'，cost_cny=None
    - 缺 use_case：整个函数吞异常不抛
- [tests/test_pricing.py](tests/test_pricing.py)
    - tokens 输入输出分算
    - chars/seconds/images flat 算
    - 精确匹配优先于通配
    - 缓存失效
- [tests/test_ai_billing_routes.py](tests/test_ai_billing_routes.py)
    - /admin/ai-usage 权限（非 admin 403）
    - 筛选参数注入 SQL 参数化
    - CSV 导出格式
    - /admin/settings/ai-pricing CRUD

### 8.2 回归测试

现有 [tests/test_llm_client_invoke.py](tests/test_llm_client_invoke.py) / [tests/test_tts_duration_loop.py](tests/test_tts_duration_loop.py) 改造后必须全绿。

## 9. 验收标准

功能层面：

- [ ] `usage_logs` 表结构符合 §3.1，`ai_model_prices` 表存在且种子数据 9 行
- [ ] 任意触发一次视频翻译、文案生成、图片翻译、视频评分 → `usage_logs` 各有新行，含 `use_case_code`/`module`/`provider`/`cost_cny`/`cost_source`
- [ ] OpenRouter 调用的 `cost_source='response'` 占比 > 90%（如果 OpenRouter 正常返回 cost）
- [ ] `/admin/ai-usage` 页可筛选、可翻页、可导出 CSV；明细行可展开 extra_data
- [ ] `/my-ai-usage` 只显示当前用户数据
- [ ] `/admin/settings/ai-pricing` 可 CRUD，保存后新请求费用按新单价
- [ ] 页面样式遵守 Ocean Blue Admin（零紫色、大圆角、OKLCH tokens）
- [ ] 全量 pytest 通过

代码层面：

- [ ] 所有 AI 写 log 的入口统一到 `ai_billing.log_request`
- [ ] `USE_CASES` 覆盖全部 14 个 AI 场景
- [ ] 新模块 `appcore/pricing.py` / `appcore/ai_billing.py` 独立、无循环依赖
- [ ] Migration 幂等（`IF NOT EXISTS` / `INSERT ... ON DUPLICATE KEY UPDATE`）

## 10. 风险 & 取舍

| 风险 | 应对 |
|---|---|
| OpenRouter 某些模型不返回 cost | fallback pricebook；通配行单价留空时 cost_source='unknown'，不计费 |
| Gemini 某些请求 `usage_metadata` 缺失 | token 缺失 → cost_source='unknown'；明细页灰色"—"标注 |
| 种子价格不准 | 备注标"待复核"，管理员上线后立即改 |
| ElevenLabs 字符数拿不到 | TTS adapter 里从生成文本 `len()` 推 `request_units` |
| 并发写 usage_logs 表竞争 | 只 INSERT 不 UPDATE，无锁 |
| 迁移过程 `TRUNCATE` 丢数据 | 已确认（方案 C） |

## 11. 交付物

Codex 完成后应有：

- 代码：`appcore/ai_billing.py` / `appcore/pricing.py` 新建；`appcore/usage_log.py` / `appcore/llm_use_cases.py` / `appcore/llm_client.py` / `appcore/gemini.py` / `appcore/gemini_image.py` / 4 个 runtime / `copywriting_runtime.py` / `openrouter_adapter.py` / `config.py` 修改
- 迁移：`db/migrations/2026_04_20_ai_billing.sql` + `db/schema.sql` 同步
- UI：`web/routes/admin_ai_billing.py` 新建；`web/routes/settings.py` 加 tab；`web/templates/admin_ai_billing.html` 新建；`web/templates/admin_settings.html` 加片段；侧栏模板（`layout.html` 或等价）加导航
- 测试：`tests/test_ai_billing.py` / `tests/test_pricing.py` / `tests/test_ai_billing_routes.py` 新建
- 文档：本 spec + plan 不改
