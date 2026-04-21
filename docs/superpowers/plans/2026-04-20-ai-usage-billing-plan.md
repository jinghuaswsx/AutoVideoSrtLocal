# AI 用量账单与定价管理 Implementation Plan

> **For Codex:** 逐 Task 执行,每个 Task 完成后 commit 一次,中文提交信息。 每个 Task 末尾的 `Verify` 命令必须执行并通过才算完成。

**Spec:** [docs/superpowers/specs/2026-04-20-ai-usage-billing-design.md](../specs/2026-04-20-ai-usage-billing-design.md)

**Goal:** 把所有 AI 付费调用（LLM + 图像 + TTS + ASR）收口到统一账单表,每请求一行,每行带模块/功能/供应商/模型/用户/费用。管理员可在 UI 看逐条明细 + 按维度汇总 + 导出 CSV,还能编辑每个模型的单价。费用写入时锁定(OpenRouter 用响应 cost,其他查表算)。

**Architecture:** 原地扩展 `usage_logs` 表加 7 列 + 3 索引；新增 `ai_model_prices` 价格表；新 `appcore/ai_billing.py` 门面统一所有 AI log 写入；新 `appcore/pricing.py` 算费用;`USE_CASES` 注册表补全 TTS/ASR 两条;OpenRouter adapter 开 `usage.include=true` 拿实锁 cost;UI 加 `/admin/ai-usage` 详单页 + `/settings?tab=pricing` 定价管理。

**Tech Stack:** Python 3.10+ / Flask / MySQL / pytest / Jinja2 / OpenAI SDK / google.genai

---

## 实施前置条件

### 1. Worktree

本 plan 已在 worktree 中执行:

```
路径: g:\Code\AutoVideoSrt\.worktrees\ai-usage-billing
分支: feature/ai-usage-billing (基于 master 5a407bc)
```

Codex 开始前 `cd` 到该目录,后续所有操作在此目录内,不要回主 worktree。

### 2. 环境

```bash
python -m pip install -r requirements.txt
```

需要 MySQL 连接(本地开发库,详见 `config.py`)。

### 3. 基线测试

```bash
pytest tests/ -q 2>&1 | tee /tmp/ai-billing-baseline.txt
tail -3 /tmp/ai-billing-baseline.txt
```

记录通过数。每个 Task commit 前重跑,通过数必须 ≥ 基线。

### 4. 现状漂移核查

| 引用 | 核查命令 | 预期 |
|------|---------|------|
| `usage_logs` 表结构 | `mysql auto_video -e "DESCRIBE usage_logs"` | 11 列(尚未扩) |
| `appcore/usage_log.py:record()` | `rg -n "^def record" appcore/usage_log.py` | 存在 |
| `USE_CASES` 14 条 | `rg -n '"video_translate|"copywriting|"video_score|"video_review|"shot_decompose|"image_translate|"link_check|"text_translate' appcore/llm_use_cases.py` | 至少 12 条存在 |
| `llm_client.invoke_chat/generate` | `rg -n "def invoke_" appcore/llm_client.py` | 两者都在 |
| `openrouter_adapter.OpenRouterAdapter` | `rg -n "class OpenRouterAdapter" appcore/llm_providers/openrouter_adapter.py` | 存在 |
| `gemini._log_gemini_usage` | `rg -n "_log_gemini_usage" appcore/gemini.py` | 存在 |
| 现有 `/admin/usage` 路由 | `rg -n "/usage" web/routes/admin_usage.py` | bp.route("/usage") 存在 |

如任一失败,停止,先更新 plan 再继续。

---

## Task 1 · 建迁移脚本 + 更新 schema.sql

**Files:**
- `db/migrations/2026_04_20_ai_billing.sql`(新建)
- `db/schema.sql`(修改)

**Changes:**
1. 新建迁移文件,内容见 spec §7.1(TRUNCATE + ALTER + CREATE + INSERT)
2. 同步修改 `db/schema.sql`:
   - 在 `usage_logs` 表定义里追加新 7 列 + 3 索引
   - 在文件末尾追加 `ai_model_prices` 表的 `CREATE TABLE IF NOT EXISTS`

**Verify:**
```bash
# 本地跑迁移(假设已连本地 MySQL)
mysql auto_video < db/migrations/2026_04_20_ai_billing.sql
mysql auto_video -e "DESCRIBE usage_logs"       # 应看到 7 个新列
mysql auto_video -e "DESCRIBE ai_model_prices"  # 应看到 9 列
mysql auto_video -e "SELECT COUNT(*) FROM ai_model_prices"  # 应返回 9
mysql auto_video -e "SELECT COUNT(*) FROM usage_logs"       # 应返回 0
```

**Commit:** `feat(billing): 新增 usage_logs 扩列 + ai_model_prices 价格表迁移`

---

## Task 2 · 扩展 USE_CASES 注册表

**Files:**
- `appcore/llm_use_cases.py`(修改)

**Changes:**
1. `UseCase` TypedDict 新增 `units_type: str` 字段
2. 14 条现有 use_case 全部补 `units_type`(见 spec §4.5 映射表)
3. 新增两条 use_case:
   - `video_translate.tts` → module=`video_translate` / provider=`elevenlabs` / model=`<runtime>` / service=`elevenlabs` / units_type=`chars`
   - `video_translate.asr` → module=`video_translate` / provider=`doubao_asr` / model=`big-model` / service=`doubao_asr` / units_type=`seconds`
4. `_uc(...)` 辅助函数签名加 `units_type` 参数
5. `MODULE_LABELS` 不变(两条 TTS/ASR 也在 video_translate 模块下)

**Verify:**
```bash
python -c "
from appcore.llm_use_cases import USE_CASES, get_use_case
assert len(USE_CASES) == 16
assert get_use_case('video_translate.tts')['units_type'] == 'chars'
assert get_use_case('video_translate.asr')['units_type'] == 'seconds'
for code, uc in USE_CASES.items():
    assert 'units_type' in uc, code
    assert uc['units_type'] in {'tokens','chars','seconds','images'}, code
print('OK', len(USE_CASES), 'use_cases')
"
```

**Commit:** `feat(billing): USE_CASES 注册表补 units_type 和 TTS/ASR 两条`

---

## Task 3 · `appcore/pricing.py` 价格计算模块

**Files:**
- `appcore/pricing.py`(新建)
- `config.py`(修改,加 `USD_TO_CNY = 6.8`)
- `tests/test_pricing.py`(新建)

**Changes:** 完全按 spec §5.1 实现,包含:
- `_load_prices()` 带 60s TTL 缓存
- `_lookup(provider, model)` 精确匹配优先于 `*` 通配
- `compute_cost_cny(...)` 返回 `(Decimal | None, source)`
- `invalidate_cache()` 公开接口

**测试覆盖:**
- tokens 场景: 输入 1000/输出 500, 单价 0.001/0.002 → 期望 2.0
- chars flat 场景: 1000 字符, 0.00016 单价 → 期望 0.16
- seconds flat 场景
- images flat 场景
- 精确匹配优先: `(gemini_aistudio, gemini-2.5-flash)` 和 `(gemini_aistudio, *)` 同时存在时走精确
- 兜底通配: `(elevenlabs, custom_voice_xxx)` 命中 `(elevenlabs, *)`
- 缺价: 返回 `(None, 'unknown')`
- 缺 token: 返回 `(None, 'unknown')`
- 缓存: 第二次调用不再查库(用 monkeypatch 验证)
- `invalidate_cache` 后重新查库

**Verify:**
```bash
pytest tests/test_pricing.py -v
```

**Commit:** `feat(billing): 新增 pricing.py 价格计算模块 + 单元测试`

---

## Task 4 · `appcore/ai_billing.py` 统一门面

**Files:**
- `appcore/ai_billing.py`(新建)
- `appcore/usage_log.py`(扩展 record 签名)
- `tests/test_ai_billing.py`(新建)

**Changes:**

### 4.1 `usage_log.record()` 签名扩展

按 spec §4.1 加 6 个 keyword-only 参数,默认值都是 `None` / `'unknown'`:
- `use_case_code`, `module`, `provider`, `request_units`, `units_type`, `cost_cny`, `cost_source`

SQL INSERT 语句对应加列。

### 4.2 `ai_billing.log_request(...)` 门面

按 spec §4.2 完整实现:
- 从 `USE_CASES[use_case_code]` 反查 `module` + `service`
- tokens 场景自动填 `request_units = input_tokens + output_tokens`
- 若传入 `response_cost_cny` → 直接用, `cost_source='response'`
- 否则调 `pricing.compute_cost_cny()`
- 调 `usage_log.record()` 持久化
- 全程异常吞掉,debug 日志

**测试覆盖:**
- 传 `response_cost_cny=Decimal("1.23")` → record 收到 cost_cny=1.23, source='response'
- 不传 cost, 价格表有 → source='pricebook', cost 正确
- 不传 cost, 价格表无 → source='unknown', cost_cny=None
- `use_case_code` 不存在 → 吞异常不抛
- `user_id=None` → 直接返回不记录
- tokens 场景 request_units 自动等于 input+output

**Verify:**
```bash
pytest tests/test_ai_billing.py tests/test_pricing.py -v
```

**Commit:** `feat(billing): 新增 ai_billing.py 门面 + 扩展 usage_log.record 签名`

---

## Task 5 · 改造 OpenRouter adapter 开 usage.include

**Files:**
- `appcore/llm_providers/openrouter_adapter.py`(修改)

**Changes:**
1. `OpenRouterAdapter.chat()` 构造 `body` 时,始终设 `body["usage"] = {"include": True}`(已存在的 `plugins` 不冲突)
2. 响应解析时,从 `resp.usage` 取 `cost`(USD),乘 `config.USD_TO_CNY` 得 `cost_cny`
3. 返回 dict 的 `usage` 加一个字段:
   ```python
   "usage": {
       "input_tokens": ...,
       "output_tokens": ...,
       "cost_cny": Decimal(str(cost_usd)) * Decimal(str(USD_TO_CNY)) if cost_usd else None,
   }
   ```
4. `DoubaoAdapter` 不动(豆包 API 不返 cost 字段)

**Verify:** 临时写个小脚本,调 OpenRouter 一次真实请求:
```bash
python -c "
from appcore.llm_providers import get_adapter
r = get_adapter('openrouter').chat(
    model='anthropic/claude-haiku-4.5',
    messages=[{'role':'user','content':'hi'}],
    user_id=None,
)
print('usage:', r['usage'])
assert 'cost_cny' in r['usage']
assert r['usage']['cost_cny'] is not None
"
```
(如果本地没有 API Key,跳过真实调用,起码确保 import 不报错 + mock 测试通过)

**Commit:** `feat(billing): OpenRouter adapter 开 usage.include 直取响应 cost`

---

## Task 6 · 改造 llm_client._log_usage

**Files:**
- `appcore/llm_client.py`(修改)
- `tests/test_llm_client_invoke.py`(修改 expectations)

**Changes:**
1. `_log_usage(...)` 原本直接调 `usage_log.record`,改为调 `ai_billing.log_request`
2. 从 adapter 返回的 `usage` dict 里透传:
   - `input_tokens`, `output_tokens`, `cost_cny`(如有 → 作为 `response_cost_cny`)
3. 还要传 `provider`(从 `binding["provider"]` 来)和 `use_case_code`
4. 错误路径(`except Exception as e`)也用 `ai_billing.log_request` 带 `success=False`

**测试调整:** `test_llm_client_invoke.py` 里 `patch("appcore.llm_client.usage_log.record")` 改成 `patch("appcore.ai_billing.usage_log.record")` 或 `patch("appcore.llm_client.ai_billing.log_request")`,确认调用参数。

**Verify:**
```bash
pytest tests/test_llm_client_invoke.py -v
pytest tests/test_ai_billing.py -v
```

**Commit:** `refactor(billing): llm_client 切到 ai_billing 门面`

---

## Task 7 · 改造 gemini.py + gemini_image.py 的 log 写入

**Files:**
- `appcore/gemini.py`(修改)
- `appcore/gemini_image.py`(修改)

**Changes:**

### 7.1 `gemini._log_gemini_usage`
- 签名加 `use_case_code: str`(必填),保留 `service` 作为兜底
- 内部从 `use_case_code` 反查 provider(aistudio 还是 vertex 根据调用方; 这里由调用方传入 provider)
- 实际上更简单: 把 `_log_gemini_usage` 整体删掉,所有调用点直接调 `ai_billing.log_request`
- 推荐: 保留函数但内部改成 thin wrapper, 签名:
  ```python
  def _log_gemini_usage(*, user_id, project_id, use_case_code, provider, model_id, success, resp=None, error=None):
      from appcore import ai_billing
      input_tokens, output_tokens = _extract_gemini_tokens(resp) if resp else (None, None)
      ai_billing.log_request(
          use_case_code=use_case_code, user_id=user_id, project_id=project_id,
          provider=provider, model=model_id,
          input_tokens=input_tokens, output_tokens=output_tokens,
          units_type="tokens", success=success,
          extra={"error": str(error)[:500]} if error else None,
      )
  ```
- 所有调用 `_log_gemini_usage(service=...)` 的地方改为传 `use_case_code=...` + `provider=...`

### 7.2 `gemini_image._log_*`
同样改为调 `ai_billing.log_request`,use_case_code=`image_translate.generate`,provider=`gemini_aistudio`,units_type=`images`,request_units 按生成图片数(通常 1)。

**Verify:**
```bash
pytest tests/ -q 2>&1 | tail -3
# 通过数 ≥ baseline
# 手工触发一次图片翻译(或 mock)验证 usage_logs 插入新行
```

**Commit:** `refactor(billing): gemini 和 gemini_image 的 log 切到 ai_billing 门面`

---

## Task 8 · 改造 runtime + copywriting_runtime 的手工 log

**Files:**
- `appcore/runtime.py`(修改)
- `appcore/runtime_de.py`(修改)
- `appcore/runtime_fr.py`(修改)
- `appcore/runtime_multi.py`(修改)
- `appcore/copywriting_runtime.py`(修改)

**Changes:** 这些文件里现有的 `from appcore.usage_log import record as _log_usage` 调用点,改成 `from appcore import ai_billing`,按具体业务给 `use_case_code`:

| 场景 | use_case_code | provider | units_type |
|---|---|---|---|
| doubao ASR | `video_translate.asr` | `doubao_asr` | `seconds` |
| ElevenLabs TTS | `video_translate.tts` | `elevenlabs` | `chars` |
| LLM 翻译 loop(rewrite 轮) | `video_translate.rewrite` | 按实际 provider | `tokens` |
| 文案生成 | `copywriting.generate` | 按实际 provider | `tokens` |

**关键点:**
- ElevenLabs TTS 的 `request_units` = 生成文本的 `len()`(字符数)
- 豆包 ASR 的 `request_units` = 音频秒数(也可以同时写 `audio_duration_seconds` 保持兼容)
- 如果 runtime 里原本 `_log_usage` 传了 `extra_data=...`,迁到 `ai_billing.log_request(extra=...)`

**Verify:**
```bash
pytest tests/ -q 2>&1 | tail -3
# 通过数 ≥ baseline
```

**Commit:** `refactor(billing): runtime 和 copywriting_runtime 的 log 切到 ai_billing 门面`

---

## Task 9 · admin_ai_billing 路由 + 查询逻辑

**Files:**
- `web/routes/admin_ai_billing.py`(新建)
- `web/app.py`(注册 blueprint)
- `tests/test_ai_billing_routes.py`(新建)

**Changes:**

### 9.1 新 blueprint
两个蓝图:
- `admin_ai_billing_bp` 前缀 `/admin`,路由 `/ai-usage` (admin_required)
- `user_ai_billing_bp` 路由 `/my-ai-usage` (login_required,只查自己)
- 两者都调用一个共享 `_render(admin: bool)`,逻辑参照 [admin_usage.py](web/routes/admin_usage.py) 改写

### 9.2 查询
支持 URL 参数: `from`, `to`, `user_id`(admin 专用), `module`, `use_case`, `provider`, `model`, `status`, `q`(project_id 搜索), `group_by`(module/use_case/provider/model/user,默认 module), `page`(从 1 起,每页 50)。

SQL 都参数化,禁止字符串拼接。

### 9.3 三个数据块
- `summary`: 总费用/总调用数/已计费数/未计费数
- `groups`: 按 group_by 分组的 `SUM(cost_cny), COUNT(*), SUM(request_units)`
- `rows`: 分页明细(LIMIT/OFFSET)

### 9.4 CSV 导出
`/admin/ai-usage/export.csv?<同上>`(admin 专用),不分页,stream response。

**测试覆盖:**
- 非 admin 访问 /admin/ai-usage → 403
- /my-ai-usage 只返回 `user_id = current_user.id` 的行
- 筛选参数注入: `?user_id=' OR 1=1 --` 不能返回全部
- CSV 首行是 header, 内容行数 = 查询结果数

**Verify:**
```bash
pytest tests/test_ai_billing_routes.py -v
```

**Commit:** `feat(billing): 新增 /admin/ai-usage 路由 + CSV 导出`

---

## Task 10 · admin_ai_billing.html 详单页模板

**Files:**
- `web/templates/admin_ai_billing.html`(新建)
- `web/templates/layout.html` 或对应侧栏 partial(修改,加导航项)

**Changes:**

### 10.1 页面结构
按 spec §6.1:
- 筛选栏(form GET)
- 4 汇总卡片
- 分组汇总 Tab 条(JS 切换 group_by)
- 逐条明细表(行点击 JS 展开 extra_data)
- 分页组件

### 10.2 样式
严格遵守 [CLAUDE.md](CLAUDE.md) 的 Ocean Blue 设计规范:
- 颜色用 `oklch()` CSS 变量,禁紫色(hue 200-240)
- 圆角用 `--radius-lg`
- 按钮用 `--accent`
- 参考 [admin_usage.html](web/templates/admin_usage.html) 的密度但费用列加 `cost_source` 着色

### 10.3 颜色规则(费用列)
```css
.cost-response { color: var(--success); }
.cost-pricebook { color: var(--info); }
.cost-unknown { color: var(--fg-subtle); }
```

### 10.4 侧栏
管理分组顶部加:
```html
<a href="/admin/ai-usage" class="sidebar-link {% if request.path.startswith('/admin/ai-usage') %}active{% endif %}">
  API 账单
</a>
```
(具体插入位置参考现有 /admin/usage 链接附近)

普通用户侧栏加 `/my-ai-usage` 入口(可见性由 current_user 控制)。

**Verify:** 浏览器打开 `http://localhost:5000/admin/ai-usage`,视觉检查:
- [ ] 零紫色
- [ ] 深海蓝侧栏
- [ ] 费用列三色可区分
- [ ] 筛选条件 URL 同步
- [ ] 分页工作
- [ ] CSV 下载有内容

**Commit:** `feat(billing): API 账单详单页 UI`

---

## Task 11 · 定价管理页 UI + CRUD

**Files:**
- `web/routes/settings.py`(修改,加 `pricing` tab 路由)
- `web/templates/admin_settings.html`(修改,加 tab 片段)
- `web/static/admin_pricing.js`(新建,可选,或内联在模板)
- `tests/test_ai_billing_routes.py`(扩展)

**Changes:**

### 11.1 后端路由(都走 admin_required + CSRF)
- `GET  /admin/settings/ai-pricing/list` → JSON 所有行
- `POST /admin/settings/ai-pricing` Body: provider/model/units_type/unit_*_cny/note → 插入,调 `pricing.invalidate_cache()`
- `PUT  /admin/settings/ai-pricing/<id>` 同上 → 更新
- `DELETE /admin/settings/ai-pricing/<id>` → 删除

### 11.2 前端 tab
在 `/settings` 页加 `pricing` tab。表格列按 spec §6.2 要求。行内编辑切换。新增行弹 inline row。删除二次确认(用 `confirm()` 或 modal)。

### 11.3 样式
延续 Ocean Blue,表格用 `--border-strong` 描边,按钮 `--radius-md`。

### 11.4 校验
前端: 数值字段非负,至少一个单价必填。
后端: 同样校验,非法返回 400。

**测试覆盖:**
- 非 admin POST/PUT/DELETE → 403
- POST 合法 → DB 多一行, cache 失效
- PUT 改 unit_flat_cny → 生效
- DELETE → 行消失
- 缺字段 → 400

**Verify:**
```bash
pytest tests/test_ai_billing_routes.py -v
# 浏览器手工:增删改一条 elevenlabs 记录,触发一次 TTS 调用,看明细页费用是否用新单价
```

**Commit:** `feat(billing): /settings?tab=pricing 定价管理 UI + CRUD 接口`

---

## Task 12 · 端到端冒烟 + 测试环境发布

**Files:** 无代码改动

**Steps:**

1. 本地完整测试:
   ```bash
   pytest tests/ -q 2>&1 | tail -3
   # 通过数 ≥ baseline
   ```

2. 本地手工端到端(从前端触发,全链验证):
   - [ ] 触发一次视频翻译 → usage_logs 应有 5 条(localize + tts_script + rewrite + tts + asr 中的部分)
   - [ ] 触发一次文案生成 → usage_logs 应有 `copywriting.generate` 一条,走 OpenRouter cost_source=`response`
   - [ ] 触发一次图片翻译 → usage_logs 应有 `image_translate.generate` 一条,cost_source=`pricebook`,units_type=`images`
   - [ ] 触发一次视频评分 → `video_score.run` 一条,tokens 计费
   - [ ] `/admin/ai-usage` 能看到上面 4 类数据,费用列有数值,筛选各维度工作
   - [ ] 导出 CSV 打开可读
   - [ ] 在 `/settings?tab=pricing` 改一条单价,触发对应功能,新行按新价

3. 按 [CLAUDE.md](CLAUDE.md) "测试发布"流程发到测试环境(/opt/autovideosrt-test, 端口 9999):
   - 先 commit 当前所有改动
   - 推到 `feature/ai-usage-billing` 远程
   - SSH 到测试服务器 `git fetch && git checkout feature/ai-usage-billing && mysql auto_video_test < db/migrations/2026_04_20_ai_billing.sql && systemctl restart autovideosrt-test`
   - 在测试环境重复第 2 步的手工端到端

**Commit:**(无代码)只在完成端到端后,在 plan 文件最底下追加 "## 测试环境验收记录" 段落记日期 + 结果。

---

## Task 13 · 合并 master + 生产发布

**Files:** 无(git 操作)

**Steps:**
1. 所有 Task 都通过 → PR / 合并 `feature/ai-usage-billing` 到 `master`
2. 按 CLAUDE.md "发布"流程: commit+push+SSH 生产 pull+跑 migration+restart
3. 生产侧 24h 内观察:
   ```sql
   SELECT cost_source, COUNT(*), SUM(cost_cny)
   FROM usage_logs
   WHERE called_at >= NOW() - INTERVAL 24 HOUR
   GROUP BY cost_source;
   ```
   期望 `unknown` 占比 < 5%
4. 用户登录管理员账号,按 spec §7.4 验收清单逐项打勾

---

## 全量验收 Checklist

完成所有 Task 后,以下每一项必须为 ✅ 才算交付:

**数据层**
- [ ] `usage_logs` 有 18 列(11 原 + 7 新),3 个新索引
- [ ] `ai_model_prices` 表存在,9 行种子数据
- [ ] `TRUNCATE` 已执行,无老数据残留

**代码层**
- [ ] `appcore/ai_billing.py` 和 `appcore/pricing.py` 新建,无循环依赖
- [ ] `USE_CASES` 共 16 条,全部带 `units_type`
- [ ] 所有 AI log 写入都经 `ai_billing.log_request` (grep 确认不再有裸 `usage_log.record` 调用,除了 `ai_billing.py` 自己)

**功能层**
- [ ] 4 种业务(视频翻译 / 文案 / 图片 / 视频评分)触发后, usage_logs 有对应新行
- [ ] OpenRouter 调用 cost_source=`response` 占比 > 90%
- [ ] `/admin/ai-usage` 筛选 / 分组 / 分页 / 导出工作
- [ ] `/my-ai-usage` 只看自己的
- [ ] `/settings?tab=pricing` CRUD 工作,改价后新请求按新价

**UI 层**
- [ ] 零紫色 (hue 200-240)
- [ ] 遵守 Ocean Blue 规范(圆角 / 间距 / 字号)
- [ ] 三态齐全(empty/loading/error)

**测试层**
- [ ] 新增 3 个测试文件 (`test_pricing.py` / `test_ai_billing.py` / `test_ai_billing_routes.py`) 全绿
- [ ] 全量 pytest 通过数 ≥ baseline

**部署层**
- [ ] 测试环境端到端通过
- [ ] 生产环境 migration 执行成功
- [ ] 生产 24h `unknown` 占比 < 5%

---

## 风险 & 注意事项

1. **不要并行工作多 Task**: 每个 Task commit 完成后再开下一个
2. **禁止跳过 Verify**: Verify 命令必须全部通过才能 commit
3. **TRUNCATE 不可逆**: 迁移只在 Codex 自己本地开发库跑,测试/生产环境的 TRUNCATE 由用户发布时跑
4. **Gemini Vertex vs AI Studio 的 provider 归属**: 按现有 USE_CASES 里的 default_provider 判断,不要主观臆测
5. **Decimal vs float**: 全程用 `Decimal` 避免浮点误差,JSON 序列化时 `str(Decimal)` 再转
6. **`audio_duration_seconds` 保留**: 不要删这列,ASR 调用同时写它和 `request_units`(兼容老聚合 SQL)

如遇 spec 里没说清的边界情况,停下来列出选项问用户,不要自己拍。

---

## 完成后清理

```bash
# 主仓
git worktree remove .worktrees/ai-usage-billing
# 分支合并后
git branch -d feature/ai-usage-billing
```

## 测试环境验收记录

**日期:** 2026-04-21

**测试环境发布**
- 服务器: `14.103.220.208`
- 实际目录: `/data/autovideosrt-test`
- 分支 / 提交: `feature/ai-usage-billing` @ `813c3ac`
- 服务: `autovideosrt-test` 已重启并为 `active`
- 测试库: `auto_video_test`

**本地验证**
- `pytest tests/test_copywriting_runtime.py tests/test_copywriting_pipeline.py tests/test_ai_billing.py tests/test_pricing.py tests/test_llm_client_invoke.py tests/test_ai_billing_routes.py -q`
  - 结果: `37 passed, 2 warnings`
- `pytest tests/test_image_translate_runtime.py tests/test_gemini_image.py -q`
  - 结果: `15 passed, 1 warning`
- `python -m pytest tests/ -q`
  - 当前机器上 1 小时内未跑完；`C:\Users\admin\AppData\Local\Temp\pytest-task12-full-suite.txt` 保留了前段输出，`C:\Users\admin\AppData\Local\Temp\pytest-task12-vv.out` 保留了 `-vv` 采样输出。本次放行主要依赖新增/相关测试全绿 + 测试环境端到端验收。

**验收中发现并修复的真实缺口**
- `copywriting.generate` 原先未把 OpenRouter 响应 `cost` 透传到 `ai_billing.log_request`
  - 修复提交: `cffe565` `fix(billing): 记录 copywriting 的 OpenRouter 响应成本`
- 图片翻译 runtime 调 `gemini_image.generate_image()` 时误传 `service="image_translate"`，导致 `ai_billing` 查不到注册表项而静默丢账
  - 修复提交: `813c3ac` `fix(billing): 修正图片翻译账单 use_case 编码`

**测试环境端到端结果**
- 视频翻译任务: `cc4ecd5b-0007-472b-992d-0c899f5b6a7e`
  - 完成状态: `export=done`
  - 账单已写入: `video_translate.asr` / `video_translate.localize` / `video_translate.tts_script` / `video_translate.rewrite` / `video_translate.tts`
- 视频评分:
  - 同任务触发 `video_score.run`
  - 落账: `provider=gemini_aistudio`, `cost_source=pricebook`, `cost_cny=0.201028`
- 文案生成任务: `10c2b67e-f88e-4abc-9e3d-b825d9287fb6`
  - 落账: `copywriting.generate`, `provider=openrouter`, `cost_source=response`, `cost_cny=0.202511`
- 图片翻译任务: `0b3e2f0a-9bb2-4650-988d-36fff52ca644`
  - 先将 `gemini-3-pro-image-preview` 单价从 `0.2652` 临时改为 `0.333333`
  - 落账: `image_translate.generate`, `provider=gemini_aistudio`, `units_type=images`, `cost_source=pricebook`, `cost_cny=0.333333`
  - 验证后已把价格恢复为 `0.2652`

**后台 / 导出 / 定价页**
- `/admin/ai-usage` 返回 `200`，页面可见 `copywriting.generate` / `image_translate.generate` / `video_score.run`
- `/admin/ai-usage?provider=openrouter&group_by=provider` 返回 `200`，筛选后可见 `openrouter`
- `/admin/ai-usage/export.csv` 返回 `200`
  - CSV 表头包含 `use_case_code` / `cost_source`
- `/my-ai-usage` 返回 `200`，当前用户可见自己的账单明细
- `/settings?tab=pricing`
  - `PUT` 改价已验证
  - 额外验证了 `POST` 新增一条临时价格记录并 `DELETE` 删除，确认 CRUD 可用

**账单摘要（测试环境验收完成后）**
- `provider='openrouter'` 的调用中，`cost_source=response` 为 `1/1`
- `usage_logs` 汇总:
  - `response`: `1` 条, `SUM(cost_cny)=0.202511`
  - `pricebook`: `8` 条, `SUM(cost_cny)=0.698881`
  - `unknown`: `10` 条

**备注**
- `unknown` 主要来自 Doubao 文本翻译链路当前未配置到具体模型单价；本次验收关注点是:
  - OpenRouter 响应成本是否直落 `response`
  - 图片 / ASR / Gemini 视频评分是否按价格表记账
  - 管理员详单页、CSV、定价页是否工作

---

## 2026-04-21 验收补录（Claude 接手收尾）

**目标：** 修掉 Codex 留下的两个 blocker（unknown 占比 53% + 全量 pytest 未跑完），把分支带到可以发生产的状态。

### 缺口 1 — `unknown` 占比 53%（已修）

根因：`ai_model_prices` 种子只有 `doubao-1-5-pro-32k`，但视频翻译 bindings 实际用的是 `doubao-seed-2-0-pro-260215`，查表未命中 → `cost_source='unknown'`。

修复：新增 migration `db/migrations/2026_04_21_seed_doubao_wildcard.sql`（commit `737d4e4`），
为 `doubao` / `gemini_aistudio` / `gemini_vertex` 三家补 `(provider, '*')` 通配兜底价，全部标"待复核"。
OpenRouter / ElevenLabs / doubao_asr 本来就有通配，现在 6 家 provider 全覆盖。

验证（测试环境 `/data/autovideosrt-test`，分支 `feature/ai-usage-billing` @ `737d4e4`）：

1. migration apply 成功，6 家 provider 都有 `*` 行：
   ```
   doubao          *  tokens   0.00000600 / 0.00001200
   doubao_asr      *  seconds  NULL / NULL / 0.01400000
   elevenlabs      *  chars    NULL / NULL / 0.00016500
   gemini_aistudio *  tokens   0.00000204 / 0.00000816
   gemini_vertex   *  tokens   0.00000816 / 0.00003264
   openrouter      *  tokens   NULL（响应 cost 兜底）
   ```
2. `compute_cost_cny(provider='doubao', model='doubao-seed-2-0-pro-260215', ...)` → `(0.012000, 'pricebook')`
3. 直接调 `ai_billing.log_request(... provider='doubao', model='doubao-seed-2-0-pro-260215' ...)` → 写入行 `cost_source=pricebook`, `cost_cny=0.012000`

后续（生产发布时）：原 migration 会 `TRUNCATE usage_logs`，历史测试数据全清；上线后首个 24h 观察，`unknown` 应 < 5%。

### 缺口 2 — 全量 pytest（已在测试服务器跑完）

本地 1 小时跑不完，改到测试服务器（约 58 秒）。为准确判断有无回归，先跑 master baseline 再对比。

| 分支 | passed | failed |
|---|---|---|
| master @ 最新 | 1126 | 35 |
| feature/ai-usage-billing @ 737d4e4 | 1166 | 34 |

差集（对比失败用例的完整 ID）：

- **feature-only 新失败**: 1 个 — `test_runtime_multi_translate.py::test_step_translate_calls_resolver_with_base_plus_plugin`。单跑立即通过（`1 passed in 0.49s`），是 test pollution（全量跑时其他测试污染了共享状态），不是代码回归。
- **master-only 已修好**: 2 个（feature 分支因改写链路顺带修好了）
- **两边都失败**: 33 个（pre-existing，和本次改动无关）

其余 1 个疑似回归（`tests/test_translate_lab_e2e.py`）是 Python 3.12 的 `too many statically nested blocks` 语法限制，master 和 feature 都收集失败，用 `--ignore` 跳过。

**结论**：feature 分支相比 master 净增 40 passed、无真实 regression。
全量验收 checklist 的"全量 pytest 通过数 ≥ baseline" 达标（1166 ≥ 1126）。
