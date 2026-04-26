# B 子系统：新品审核 + AI 评估矩阵（new-product-review）设计文档

- **日期**：2026-04-26
- **作者**：与 Claude 协作 brainstorm（用户授权后续决定全权）
- **状态**：spec 完成，待实施计划
- **范围标识**：子系统 **B — new-product-review**
- **上位文档**：[docs/任务中心需求文档-2026-04-26.md](../../任务中心需求文档-2026-04-26.md) + [master pipeline](2026-04-26-mingkong-pipeline-master.md)
- **依赖子系统**：A（mk-import 已上线，B 不动 A）+ C（任务中心，B 调 `appcore.tasks.create_parent_task`）
- **复用现有**：`appcore/material_evaluation.py`（9 国 LLM 评估已生产可用）

---

## 0. 一句话目标

让 admin 在 A 把明空视频入库后，通过"新品审核"工作台一站式完成：**AI 评估 9 国 → 看矩阵决定上架国家与翻译员 → 一键建任务**——把"该不该做"和"分配给谁"两个判断绑成一个动作，跟在 A 后面就直接对接到 C。

**B 不做**：不动 A 的入库流程；不维护独立"新品审核"表（直接复用 `media_products`）；不做老品的重新评估（老品不进 Tab）。

---

## 1. 范围与边界

### 1.1 本 spec 做什么

1. 新增 service 层 `appcore/new_product_review.py`：
   - `list_pending(...)` — 待审核产品列表（带 AI 评估结果）
   - `evaluate_product(product_id, actor_user_id)` — 触发评估（含 ffmpeg 15s 截短 + LLM 调用）
   - `decide_approve(product_id, countries, translator_id, actor_user_id)` — 决策上架 + 建任务（事务）
   - `decide_reject(product_id, reason, actor_user_id)` — 决策不上架
2. 新增 Blueprint `web/routes/new_product_review.py`，前缀 `/new-product-review`
3. 新增模板 `web/templates/new_product_review_list.html`：9 国矩阵 + 国家选择 modal + 翻译员下拉
4. `web/templates/mk_selection.html` 顶部加页面级 Tab 切换栏（"明控选品" / "新品审核"），新品审核 Tab 链接到 `/new-product-review/`
5. 新增 use_case 注册（如果选择和现有 `material_evaluation.evaluate` 不同的 use_case；本 spec 直接复用 `material_evaluation.evaluate`，不新增 use_case）
6. 新增 1 个 migration `db/migrations/2026_04_28_media_products_npr_decision.sql`，加 4 列：
   - `npr_decision_status ENUM('pending','approved','rejected') NULL DEFAULT NULL`
   - `npr_decided_countries JSON NULL DEFAULT NULL`
   - `npr_decided_at DATETIME NULL DEFAULT NULL`
   - `npr_decided_by INT NULL DEFAULT NULL`
   - `npr_rejected_reason VARCHAR(500) NULL DEFAULT NULL`
   - `npr_eval_clip_path VARCHAR(512) NULL DEFAULT NULL`（截短产物路径，便于重评 + 调试）
7. 新增 ffmpeg 截短 helper `appcore/new_product_review.py:_make_eval_clip_15s`（容器层 cut，毫秒级）
8. 单元 + 集成测试覆盖：service / 截短 / 评估 happy / 决策事务 / 路由权限

### 1.2 本 spec 不做什么

- ❌ 不改造 A 子系统，A 仍按原路一点击立即入库
- ❌ 不引入"新品独立表"，直接复用 `media_products`
- ❌ 不重写 `material_evaluation.py`，复用其 `build_prompt` / `build_response_schema` / `normalize_result` / `_materialize_media`，B 只调 `llm_client.invoke_generate` 走相同 use_case
- ❌ 不批量评估（admin 一行一行点）；不自动评估（用户原话"独立 Tab 让 admin 选择性触发"）
- ❌ 不做老品评估（Tab 只列 `mk_id IS NOT NULL` 的）
- ❌ 不做"半路加国家" / "重评单国"（YAGNI，第二阶段再加）
- ❌ 不做矩阵的列 sticky / 行虚拟滚动（Ocean Blue 现有 oc-table 即可）
- ❌ 不做悬浮内容的异步加载（评估结果一次拉全，hover 用 inline data attr 渲染 tooltip）

### 1.3 关键依赖

- `media_products` 表：已有 `mk_id` / `ai_score` / `ai_evaluation_result` / `ai_evaluation_detail` / `user_id` / `name` / `product_link` / `main_image` / `cover_object_key` / `product_code`
- `appcore/material_evaluation.py`：build_prompt / build_response_schema / normalize_result / _materialize_media / `_first_english_video` / `USE_CASE_CODE = "material_evaluation.evaluate"`
- `appcore/llm_client.invoke_generate`：统一 LLM 入口（CLAUDE.md "LLM 统一调用"节）
- `appcore/medias.list_enabled_languages_kv()`：返回 [(code, name_zh), ...] 启用语种
- `appcore/medias.update_product(product_id, **fields)`：白名单字段写入（B 需要扩展 allowed 集，加 npr_* 字段）
- `appcore/medias.list_items(product_id, lang)`：拿英文视频
- `appcore/tasks.create_parent_task(*, media_product_id, media_item_id, countries, translator_id, created_by) -> int`：B → C 的接口
- `appcore/tasks.list_translators()` 等价 GET `/tasks/api/translators`：翻译员下拉
- `appcore/permissions`：admin 角色判定（沿用 `require_admin`）
- `ffmpeg`：服务器已装（路径 `/usr/bin/ffmpeg` Linux / `ffmpeg` Windows）

### 1.4 风险点（实施时必须先核）

1. **截短可能失败**：moov atom 在尾、容器损坏、I 帧不在开头——需要 fallback 用原视频
2. **LLM 调用慢**：Gemini 2.5 Pro 多模态 + 视频，单次 30-180 秒——前端必须加 loading 状态 + 服务端超时 240 秒
3. **media_products 字段命名**：现有已经有 `ai_*` 系列，新加 `npr_*` 不冲突（实施第一步 grep 确认）
4. **admin 同时审多个产品**：评估调用本身是同步阻塞的，admin 点了一个等回来再点下一个；前端要按钮 disabled 防双击
5. **建任务事务**：`create_parent_task` 内部已是事务，B 决策时把"写 npr_*" + "建任务" 包到同一连接事务里需要注意 — 直接接受"先写 npr_* → 再建任务"两步，建任务失败时回滚 npr_*（DB 事务包裹）
6. **新品 user_id 已被 A 设过**：A 入库时让 admin 选了翻译员，赋给 `media_products.user_id`。B 决策时如果 admin 又改了翻译员，要走 `update_product_owner`（owner cascade），不能直接 UPDATE `user_id`

### 1.5 命名约定

- 数据：复用 `media_products`，新加 6 列前缀 `npr_`（避免和 `ai_*` 混淆）
- Blueprint：`new_product_review`，前缀 `/new-product-review`
- 服务模块：`appcore/new_product_review.py`
- 前端模板：`web/templates/new_product_review_list.html`
- 前端 JS namespace：`npr*`（new-product-review 缩写）

---

## 2. 已锁定决定

| # | 决定 |
|---|---|
| 1 | **触发方式 = 独立"新品审核"Tab**（明空选品页 Tab 切换 → 跳到 `/new-product-review/`），admin 显式触发评估 |
| 2 | **AI 调用 = 一次输出 9 国**（沿用 `material_evaluation.evaluate` use_case），不分国家逐次调用 |
| 3 | **待评估清单判定**：`mk_id IS NOT NULL AND (ai_evaluation_result IS NULL OR ai_evaluation_result = '' OR ai_evaluation_result = '评估失败') AND npr_decision_status IS NULL OR npr_decision_status = 'pending'` |
| 4 | **评估失败行**保留在 Tab，行内"重新评估"按钮（红色） |
| 5 | **15s 截短**：取前 15 秒，`ffmpeg -ss 0 -i in.mp4 -t 15 -c copy -avoid_negative_ts 1 out.mp4` 容器层 cut。失败 fallback 用原视频整段 |
| 6 | **截短产物存储**：本地 `instance/eval_clips/<product_id>/<item_id>_15s.mp4`，按需懒生成；路径写到 `media_products.npr_eval_clip_path`；保留以便重评 |
| 7 | **评估输入 = 商品主图 + 15s 截短视频 + 商品标题 + 商品链接 + 启用语种列表**（沿用 material_evaluation 的 prompt 结构，唯一差别是视频路径换成截短产物） |
| 8 | **评估结果写回 media_products**：`ai_score` / `ai_evaluation_result` / `ai_evaluation_detail`（结构和 material_evaluation 一致，admin_usage 等其他读取方零改动） |
| 9 | **9 国矩阵 UI**：表格行=产品，列=每个启用语种，单元格 = ✓（绿）/ ✗（灰）+ 小字 score。hover → tooltip score + reason 摘要（inline data，无额外请求）；点击 → modal 显示完整 reason + suggestions |
| 10 | **行级"上架"按钮**点击 → 国家选择 modal：9 国 checkbox 预勾 AI 推荐（is_suitable=true），admin 可改；下方"翻译员"下拉默认为产品当前 `user_id`（A 设的），admin 可改；点"确认建任务"调后端 |
| 11 | **行级"不上架"按钮**点击 → 弹 reason 输入 modal（≥10 字符），写 `npr_decision_status='rejected'` + `npr_rejected_reason` |
| 12 | **决策完成后建任务（事务）**：`decide_approve` 在同一 DB 事务里：(1) 如 admin 改了翻译员 → 走 `update_product_owner` (2) 写 `npr_decision_status='approved'` + `npr_decided_countries` + `npr_decided_at` + `npr_decided_by` (3) 调 `tasks.create_parent_task`。任一失败回滚 |
| 13 | **国家代码统一大写 ISO**（DE / FR / JA / NL / SV / FI / ...）；`media_languages.code` 是小写，B 内部转大写后传给 `create_parent_task`；矩阵显示用 `name_zh`（"德语" / "法语"...） |
| 14 | **权限 = admin only**（superadmin/admin），普通员工返回 403 |
| 15 | **失败处理**：评估失败写 `ai_evaluation_result='评估失败'`，Tab 列出，admin 手动重试。决策失败前端红 toast，按钮复原 |
| 16 | **Tab 集成 = 独立页面**：`/mk_selection` 页顶加 2 个页面 Tab 切换链接，"新品审核" 链接到 `/new-product-review/`（不内嵌 iframe，简化） |
| 17 | **不上传 TOS**：截短产物只在本地（`instance/eval_clips/`），评估完即可删（保留方便重评），不占 TOS 配额 |

---

## 3. 数据模型

### 3.1 复用 `media_products`（**不加新表**）

新增 6 列（migration `2026_04_28_media_products_npr_decision.sql`）：

| 字段名 | 类型 | 约束 | 含义 |
|---|---|---|---|
| `npr_decision_status` | `ENUM('pending','approved','rejected')` | NULL DEFAULT NULL | 新品审核决策状态。NULL = A 入库后还未触发审核流；pending = 在审核 Tab 等决策；approved = 已建任务；rejected = admin 决定不做 |
| `npr_decided_countries` | `JSON` | NULL | 决策上架国家清单（大写 ISO）`["DE","FR",...]` |
| `npr_decided_at` | `DATETIME` | NULL | 决策时间 |
| `npr_decided_by` | `INT` | NULL | 决策人 user_id |
| `npr_rejected_reason` | `VARCHAR(500)` | NULL | 不上架理由（仅 rejected 时填） |
| `npr_eval_clip_path` | `VARCHAR(512)` | NULL | 截短产物本地路径（相对项目根） |

**字段不加 INDEX**（待审核清单 SQL 走 `mk_id` + `ai_evaluation_result` 已有索引；npr_decision_status 是 ENUM 4 值，全表扫描代价小）

**`media_products.user_id` 不加新字段**：翻译员就是产品 owner，A 入库时已设。B 决策时若改翻译员 → 走 `update_product_owner` 触发 owner cascade。

### 3.2 复用 `appcore/medias.update_product`

实施时把 `npr_decision_status` / `npr_decided_countries` / `npr_decided_at` / `npr_decided_by` / `npr_rejected_reason` / `npr_eval_clip_path` 加到 `medias.update_product` 的 `allowed` 白名单（`appcore/medias.py:407` 附近）。

### 3.3 待评估清单 SQL（`appcore/new_product_review.list_pending`）

```sql
SELECT
  p.id, p.name, p.product_code, p.product_link, p.main_image,
  p.user_id AS translator_id,
  u.username AS translator_name,
  p.cover_object_key, p.mk_id,
  p.ai_score, p.ai_evaluation_result, p.ai_evaluation_detail,
  p.npr_decision_status, p.npr_decided_countries, p.npr_decided_at,
  p.npr_eval_clip_path,
  p.created_at, p.updated_at
FROM media_products p
LEFT JOIN users u ON u.id = p.user_id
WHERE p.deleted_at IS NULL
  AND p.mk_id IS NOT NULL
  AND COALESCE(p.archived, 0) = 0
  AND (p.npr_decision_status IS NULL OR p.npr_decision_status = 'pending')
ORDER BY p.created_at DESC, p.id DESC
LIMIT 200
```

**列表行级行为映射**：
- `ai_evaluation_result IS NULL OR ''` → 行显示"未评估" + 蓝按钮"AI 评估"
- `ai_evaluation_result = '评估失败'` → 行红字"评估失败" + 红按钮"重新评估"
- `ai_evaluation_result IN ('适合推广', '部分适合推广', '不适合推广', '需人工复核')` → 行展示矩阵 + "上架"/"不上架"按钮

---

## 4. Service 层（`appcore/new_product_review.py`）

### 4.1 公开入口

```python
def list_pending(*, limit: int = 200) -> list[dict]:
    """返回待评估 / 已评估未决策的产品（含 AI 矩阵 detail JSON 解析）。"""

def evaluate_product(product_id: int, *, actor_user_id: int) -> dict:
    """执行评估，同步阻塞。
    步骤：
      1. 取产品 + 第一条英语视频
      2. 生成（或复用）15s 截短产物到 instance/eval_clips/<product_id>/<item_id>_15s.mp4
      3. 走 llm_client.invoke_generate(use_case='material_evaluation.evaluate', media=[cover, clip], ...)
      4. normalize_result + write back ai_score / ai_evaluation_result / ai_evaluation_detail
      5. 写 npr_eval_clip_path
      6. 写 npr_decision_status = 'pending'（如果原本是 NULL）
    返回 {'status': 'evaluated'/'failed', 'product_id': int, 'detail': dict}.
    异常透传给 route layer 由其转 5xx。
    """

def decide_approve(
    product_id: int, *,
    countries: list[str],
    translator_id: int,
    actor_user_id: int,
) -> dict:
    """事务化决策 + 建任务。
    步骤（一条 DB 事务）：
      1. 校验：product 存在 + npr_decision_status != 'approved'
      2. 校验：countries 非空，translator_id 是有效 can_translate 用户
      3. 如 translator_id != product.user_id → medias.update_product_owner(...)
      4. 取 product.media_item_id（第一条英语视频 id）
      5. 调 tasks.create_parent_task(...) 拿 task_id
      6. UPDATE media_products SET npr_decision_status='approved',
            npr_decided_countries=JSON_ARRAY('DE','FR',...),
            npr_decided_at=NOW(), npr_decided_by=actor
    返回 {'task_id': int, 'product_id': int}.
    """

def decide_reject(
    product_id: int, *,
    reason: str,
    actor_user_id: int,
) -> dict:
    """admin 决定不做。
    UPDATE media_products SET npr_decision_status='rejected',
        npr_rejected_reason=%s, npr_decided_at=NOW(), npr_decided_by=actor
    返回 {'product_id': int}.
    """
```

### 4.2 内部 helper

```python
def _make_eval_clip_15s(product_id: int, item: dict) -> str:
    """为视频生成（或复用）15 秒截短。返回相对路径（相对项目根）。
    
    输入 item 含 object_key（TOS）。如本地缓存已存在，直接返回。
    否则：
      1. 用 material_evaluation._materialize_media(object_key) 拿到原视频本地路径
      2. ffmpeg -ss 0 -i <input> -t 15 -c copy -avoid_negative_ts 1 -y <out>
      3. 失败 fallback：返回原视频路径（不截）
    
    输出路径：instance/eval_clips/<product_id>/<item_id>_15s.mp4
    """

def _build_evaluation_inputs(product: dict) -> tuple[Path, Path, list]:
    """返回 (cover_path, video_path_15s, languages_list)。
    复用 material_evaluation._resolve_product_cover_key + _first_english_video。
    """

def _resolve_translator(translator_id: int) -> dict:
    """SELECT * FROM users WHERE id=%s AND is_active=1 AND JSON_EXTRACT(permissions,'$.can_translate')=true"""
```

### 4.3 异常

```python
class NewProductReviewError(Exception): pass
class ProductNotFoundError(NewProductReviewError): pass
class InvalidStateError(NewProductReviewError): pass        # 已 approved 不能再决策
class ClipGenerationError(NewProductReviewError): pass      # 容器层 cut 失败 + fallback 也失败
class EvaluationError(NewProductReviewError): pass          # LLM 调用失败
class TranslatorInvalidError(NewProductReviewError): pass   # translator_id 不合规
```

---

## 5. API 路由（Blueprint `new_product_review`，前缀 `/new-product-review`）

| 方法 | 路径 | 用途 | 权限 |
|---|---|---|---|
| GET | `/new-product-review/` | 渲染 Tab 页面 HTML | admin |
| GET | `/new-product-review/api/list` | JSON 列表 | admin |
| POST | `/new-product-review/api/<int:product_id>/evaluate` | 触发评估 | admin |
| POST | `/new-product-review/api/<int:product_id>/decide` | 决策上架 + 建任务 | admin |
| POST | `/new-product-review/api/<int:product_id>/reject` | 决策不上架 | admin |

### 5.1 `GET /new-product-review/`

渲染 `new_product_review_list.html`，初始数据由模板内联（避免首次 fetch 闪烁）。

模板 context：
```python
{
  "products": list_pending(limit=200),
  "languages": [{"code": "de", "name_zh": "德语", ...}, ...],  # 启用语种
  "translators": tasks.list_translators(),
}
```

### 5.2 `GET /new-product-review/api/list`

```json
{
  "products": [
    {
      "id": 999,
      "name": "...",
      "product_link": "...",
      "main_image": "...",
      "translator_id": 42,
      "translator_name": "alice",
      "ai_score": 78.5,
      "ai_evaluation_result": "部分适合推广",
      "ai_evaluation_detail": {
        "schema_version": 1,
        "countries": [
          {"lang":"de","language":"德语","is_suitable":true,"score":85,"reason":"...","suggestions":[...]},
          ...
        ]
      },
      "npr_decision_status": "pending",
      "created_at": "..."
    }
  ],
  "languages": [{"code":"de","name_zh":"德语"},...],
  "translators": [{"id":42,"username":"alice"},...]
}
```

### 5.3 `POST /new-product-review/api/<int:product_id>/evaluate`

无 body。返回：

```json
// 200 OK
{ "status": "evaluated", "product_id": 999, "ai_score": 78.5, "ai_evaluation_result": "部分适合推广", "detail": {...} }

// 4xx / 5xx
{ "error": "evaluation_failed" / "product_not_found" / "no_video", "detail": "..." }
```

服务端超时 240 秒。

### 5.4 `POST /new-product-review/api/<int:product_id>/decide`

```json
// Request
{
  "countries": ["DE", "FR", "JA"],
  "translator_id": 42
}

// Response 200
{ "task_id": 123, "product_id": 999 }

// 4xx
{ "error": "no_countries" / "invalid_translator" / "already_decided", "detail": "..." }
```

### 5.5 `POST /new-product-review/api/<int:product_id>/reject`

```json
// Request
{ "reason": "材质有合规风险，先不做" }

// Response 200
{ "product_id": 999 }

// 4xx
{ "error": "reason_required", "detail": "理由至少 10 字符" }
```

---

## 6. 前端（`new_product_review_list.html`）

### 6.1 顶部页面 Tab 切换栏（同时改 `mk_selection.html`）

```html
<div class="oc-page-tabs">
  <a class="oc-page-tab {% if active_tab == 'mk_selection' %}active{% endif %}"
     href="/medias/mk-selection">明控选品</a>
  <a class="oc-page-tab {% if active_tab == 'new_product_review' %}active{% endif %}"
     href="/new-product-review/">新品审核</a>
</div>
```

样式：Ocean Blue 风格——白底，激活底色 `--accent-subtle`，左竖线 `--accent`，`--radius` 6px，h=36。

### 6.2 矩阵主表格

```html
<table class="oc-table npr-matrix">
  <thead>
    <tr>
      <th class="npr-col-product">产品</th>
      <th class="npr-col-score">AI 综合</th>
      {% for lang in languages %}
      <th class="npr-col-lang">{{ lang.name_zh }}</th>
      {% endfor %}
      <th class="npr-col-translator">翻译员</th>
      <th class="npr-col-actions">操作</th>
    </tr>
  </thead>
  <tbody id="nprBody">
    {% for p in products %}<tr data-product-id="{{ p.id }}" ...>...</tr>{% endfor %}
  </tbody>
</table>
```

**单元格渲染**（每个国家列）：
- 未评估：灰色 "—"
- 评估失败：红色 "!"
- 评估成功：✓（绿，`color: var(--success)`）或 ✗（灰）+ 下方小字 score

**hover tooltip**（纯 CSS + JS）：

```html
<td class="npr-cell" data-lang="de"
    data-score="85" data-suitable="1"
    data-reason="欧洲消费者对此品类有明确需求..."
    data-suggestions='["建议","..."]'>
  <span class="npr-icon-ok">✓</span>
  <span class="npr-cell-score">85</span>
</td>
```

JS hover 时读 dataset 渲染浮动 tooltip。

### 6.3 行操作按钮

```html
<td class="npr-col-actions">
  {% if 未评估或评估失败 %}
    <button class="oc-btn oc-btn--accent npr-btn-eval">AI 评估</button>
  {% else %}
    <button class="oc-btn oc-btn--accent npr-btn-approve">上架</button>
    <button class="oc-btn npr-btn-reject">不上架</button>
  {% endif %}
</td>
```

**评估按钮点击**：disable + spinner → POST `/api/<id>/evaluate` → 回来重新渲染该行（拉 `/api/list` 整表 refresh，简化）

**"上架"按钮点击**：打开国家选择 modal（见 6.4）

**"不上架"按钮点击**：打开 reason modal → POST `/api/<id>/reject`

### 6.4 国家选择 Modal

```html
<div id="nprApproveModal" class="oc-modal" hidden>
  <div class="oc-modal-card">
    <h3>上架国家选择</h3>
    <div class="npr-country-grid">
      {% for lang in languages %}
      <label>
        <input type="checkbox" class="npr-country-cb"
               data-lang="{{ lang.code | upper }}">
        {{ lang.name_zh }} <span class="npr-country-score"></span>
      </label>
      {% endfor %}
    </div>
    <div class="npr-translator-row">
      翻译员：
      <select id="nprTranslatorSel">
        {% for t in translators %}
        <option value="{{ t.id }}">{{ t.username }}</option>
        {% endfor %}
      </select>
    </div>
    <div class="oc-modal-footer">
      <button id="nprApproveCancel">取消</button>
      <button id="nprApproveConfirm" class="oc-btn oc-btn--accent">确认建任务</button>
    </div>
  </div>
</div>
```

**打开时**：从行 dataset 读 AI 评估结果，预勾 `is_suitable=true` 的国家，每国 score 显示在右边；翻译员下拉默认选中产品当前 `user_id`。

**确认点击**：POST `/api/<id>/decide` body `{countries, translator_id}` → 成功后 toast + 该行从表格移除（已 approved 不再显示）

### 6.5 Reason Modal（拒绝时）

简易 textarea + 字符计数 + 确认按钮（≥10 字符才 enable）。

### 6.6 JS 文件位置与命名

行内 `<script>` 不超过 200 行，超过的话拆出 `web/static/new_product_review.js`。前端 namespace：所有函数以 `npr` 前缀，DOM ID 以 `npr` 前缀，CSS class 以 `npr-` 前缀。

---

## 7. 错误处理与 UX

| 场景 | 后端响应 | 前端展示 |
|---|---|---|
| 产品不存在 | 404 `{error: 'product_not_found'}` | toast "产品不存在，请刷新" |
| 已 approved | 422 `{error: 'already_decided'}` | toast "该产品已建任务" + 刷新页面 |
| 评估失败（截短失败 fallback 也失败） | 500 `{error: 'evaluation_failed'}` | toast "AI 评估失败：..." + 行变红"重新评估" |
| 评估失败（LLM 调用失败） | 500 `{error: 'evaluation_failed'}` | 同上 |
| 评估超时（240 秒） | 504 | toast "评估超时，请稍后重试" |
| 国家清单空 | 422 `{error: 'no_countries'}` | toast "请至少选 1 个国家" |
| translator_id 无效 | 422 `{error: 'invalid_translator'}` | toast "翻译员不合规，请重选" |
| reason 太短 | 422 `{error: 'reason_required'}` | toast "理由至少 10 字符" |
| 决策建任务失败（事务回滚） | 500 `{error: 'task_create_failed'}` | toast "建任务失败：..." 按钮复原 |

**不重试**（admin 自己点）。

---

## 8. 测试策略

### 8.1 单元测试 `tests/test_appcore_new_product_review.py`

- `test_list_pending_filters_by_mk_id_and_status` — Tab SQL 过滤正确
- `test_list_pending_excludes_approved_and_rejected` — 已决策不出现
- `test_list_pending_includes_failed_evaluation` — 评估失败仍出现
- `test_make_eval_clip_15s_creates_file` — 用 monkeypatch mock subprocess
- `test_make_eval_clip_15s_falls_back_on_ffmpeg_failure` — 返回原视频路径
- `test_evaluate_product_writes_back_ai_fields` — mock LLM 返回，验证 update_product 调用
- `test_evaluate_product_sets_npr_pending_when_null` — 评估完写 `npr_decision_status='pending'`
- `test_decide_approve_creates_task_and_writes_status` — mock create_parent_task
- `test_decide_approve_changes_owner_when_translator_differs` — 走 update_product_owner
- `test_decide_approve_rolls_back_on_task_failure` — 任务建失败时 npr_* 字段未写入
- `test_decide_reject_writes_status_and_reason`
- `test_decide_reject_requires_reason_min_10` — 9 字 → ValueError
- `test_resolve_translator_rejects_inactive` — is_active=0 → 异常
- `test_resolve_translator_rejects_no_can_translate_perm` — 权限位缺失 → 异常

### 8.2 集成测试 `tests/test_new_product_review_routes.py`

- `test_get_index_admin_only` — 非 admin 403，admin 200
- `test_get_list_returns_json` — 结构正确
- `test_post_evaluate_admin_only` — 非 admin 403
- `test_post_evaluate_calls_service` — mock service 验证调用
- `test_post_decide_creates_task` — mock create_parent_task 验证
- `test_post_decide_invalid_translator_returns_422`
- `test_post_decide_no_countries_returns_422`
- `test_post_reject_writes_status` — 验证 `npr_decision_status='rejected'`

### 8.3 路由测试 fixture

复用现有 `authed_client_no_db` / `authed_user_client_no_db` fixture（CLAUDE.md 约定，本地无 MySQL）。
service 层测试在服务器测试环境跑（`/opt/autovideosrt-test`），通过 SSH 触发。

### 8.4 手动验收

测试环境 `http://172.30.254.14:8080/`，账号 `admin/709709@`：
1. 在明空选品 Tab 入库一个新产品
2. 切到"新品审核" Tab，看到该产品（mk_id 命中）
3. 点【AI 评估】，等待 30-180 秒，看到矩阵填充
4. hover 单元格看 tooltip
5. 点单元格看 modal
6. 点【上架】，看到 9 国 checkbox 预勾 AI 推荐 + 翻译员下拉
7. 改国家 + 翻译员 + 确认建任务，到任务中心看到父任务
8. 另一个产品点【不上架】+ 填理由，回 Tab 应该消失
9. 评估失败的产品行红色 + "重新评估"

---

## 9. 接驳点

- **A 子系统**：A 完成入库后，产品自动出现在 B 的"新品审核" Tab（B 通过 `mk_id IS NOT NULL` 识别）。A 不需要任何改动
- **C 子系统**：B 决策完成时调 `appcore.tasks.create_parent_task(...)`。这是 C 已暴露的 service 入口，与 admin 在任务中心 UI 手工建任务等价。`update_product_owner` 触发 owner cascade 也是 C 的现有 hook
- **D 子系统**：B 不影响。D 后续做"原始素材任务库强化"时，B 建好的父任务自动进入 D 的认领池
- **E 子系统**：B 不影响。E 后续做"翻译任务池深度集成"时，B 建好的子任务自动进入 E 的工作台
- **`material_evaluation.py` 自动评估**：现有的 `evaluate_product_if_ready` 后台扫描评估在 A 入库的产品上**也会触发**（因为它扫的是 `ai_evaluation_result IS NULL`）。这有两种处理：
  - **方案 X（推荐，本 spec 默认）**：保留现有自动扫描，B 的 admin 手动评估 = 再评一遍（force=True 覆盖）。预评估完 admin 一进 Tab 就直接看到结果，体验更好
  - **方案 Y**：禁用自动扫描对 mk_id 产品。**本 spec 不采用**

---

## 10. 部署与 migration

### 10.1 Migration 文件

`db/migrations/2026_04_28_media_products_npr_decision.sql`：

```sql
-- B 子系统：新品审核决策字段
-- 复用 media_products 表，加 6 列承载新品审核决策状态
-- 启动时 appcore.db_migrations.apply_pending() 自动 apply

ALTER TABLE media_products
  ADD COLUMN npr_decision_status ENUM('pending','approved','rejected') NULL DEFAULT NULL COMMENT '新品审核决策状态' AFTER listing_status,
  ADD COLUMN npr_decided_countries JSON NULL DEFAULT NULL COMMENT '决策上架国家清单(大写ISO)' AFTER npr_decision_status,
  ADD COLUMN npr_decided_at DATETIME NULL DEFAULT NULL COMMENT '决策时间' AFTER npr_decided_countries,
  ADD COLUMN npr_decided_by INT NULL DEFAULT NULL COMMENT '决策人user_id' AFTER npr_decided_at,
  ADD COLUMN npr_rejected_reason VARCHAR(500) NULL DEFAULT NULL COMMENT '不上架理由' AFTER npr_decided_by,
  ADD COLUMN npr_eval_clip_path VARCHAR(512) NULL DEFAULT NULL COMMENT '15s截短产物本地路径' AFTER npr_rejected_reason;
```

启动时由 `appcore.db_migrations.apply_pending()` 自动 apply（参考 CLAUDE.md / 项目已有惯例）。

### 10.2 部署步骤

按 CLAUDE.md "部署 migration：让自动机制跑"：服务器只跑 `git pull + restart`，启动时自动 apply migration。**不要**手动 SQL。

```bash
ssh -i C:\Users\admin\.ssh\CC.pem ubuntu@172.30.254.14
sudo bash -c 'cd /opt/autovideosrt && git pull && systemctl restart autovideosrt'
curl -sI http://172.30.254.14/login   # 验证 200
```

测试环境同流程，路径 `/opt/autovideosrt-test/`。

---

## 11. 决策日志

12 条决定（用户授权后由 Claude 自主拍板，详见第 2 节）。

---

## 12. 实施次序（与 plan 衔接）

按 plan 文件 `docs/superpowers/plans/2026-04-26-new-product-review.md` 的 30 任务分解执行。大块顺序：

1. Migration + medias.update_product 白名单扩展（1 任务）
2. service 层骨架 + 异常 + helper（5 任务）
3. service 主入口（list_pending / evaluate / decide_approve / decide_reject）（4 任务）
4. service 单元测试（5 任务）
5. Blueprint + 5 个 endpoint（5 任务）
6. 路由集成测试（4 任务）
7. 模板 HTML 骨架 + 矩阵渲染（3 任务）
8. 国家选择 modal + reason modal + JS（2 任务）
9. mk_selection.html Tab 切换栏 + 路由注册（1 任务）

合计 30 任务。

---

## 13. 维护备忘

- 本 spec 与 master 文件互相引用，B 子系统状态从"未 brainstorm"翻成"已 brainstorm + 实施中"
- `material_evaluation.py` 的 use_case `material_evaluation.evaluate` **不要**重命名 / 拆分 — B 复用即可
- 如果未来要做"每国独立调用"或"重评单国"，新加 use_case `new_product_review.evaluate_single_country`，**不要**改 material_evaluation 的现有契约
- B 完成后回写 [master 文件第 1/3 节](2026-04-26-mingkong-pipeline-master.md) + [需求文档第 7 节](../../任务中心需求文档-2026-04-26.md)
