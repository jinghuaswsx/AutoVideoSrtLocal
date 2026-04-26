# A 子系统：明空选品 → 素材入库（mk-import）设计文档

- **日期**：2026-04-26
- **作者**：与 Claude 协作 brainstorm
- **状态**：spec 已完成，待实施计划
- **范围标识**：子系统 **A — mk-import**
- **上位文档**：[docs/任务中心需求文档-2026-04-26.md](../../任务中心需求文档-2026-04-26.md) + [master pipeline](2026-04-26-mingkong-pipeline-master.md)

---

## 0. 一句话目标

让 admin 在明空选品（`/medias/mk-selection`）页面看到一个值得搬到小语种的视频时，点一个按钮，这条视频自动落到素材管理库，可以接下来走翻译流程。

**A 不做**：不自动建任务（admin 入库后回任务中心 → 待派单素材 tab 用 C 已有的"一键创建任务"流程）；不做 AI 评估（B 子系统的活）。

---

## 1. 范围与边界

### 1.1 本 spec 做什么

1. 新增 service 层 `appcore/mk_import.py`：`import_mk_video()` 主入口 + 5 个 helper
2. 新增 Blueprint `web/routes/mk_import.py`，前缀 `/mk-import`，3 个端点
3. 改造 `web/templates/mk_selection.html`：每个视频卡片加【加入素材库】按钮 + 页面加载时批量查询已入库状态 + 点击事件 + 翻译员选择 mini-modal
4. 极小扩展现有素材管理"添加 item" service：增加可选 `mk_source_metadata` 入参用于溯源（向后兼容）
5. 单元 + 集成测试覆盖 service 层、API 端点、dedup 边界

### 1.2 本 spec 不做什么

- ❌ 自动建任务中心父任务（A 范围之外，admin 仍用 C 的"待派单素材"流程）
- ❌ AI 评估 / 9 国矩阵（B 子系统）
- ❌ 视频卡片批量勾选 / 批量入库（YAGNI，初期不需要）
- ❌ 异步后台任务调度（用同步模式）
- ❌ 失败自动重试（admin 自己点重试）
- ❌ 已入库视频跳转到素材管理对应产品（保留按钮 disabled 即可）

### 1.3 关键依赖

- `media_products` 表（含 `product_code` / `mk_id` / `name` / `user_id` / `archived` / `deleted_at`）
- `media_items` 表（含 `filename` / `product_id` / `lang` / `object_key` / `cover_object_key` / `duration_seconds` / `display_name`）
- 现有素材管理"添加 item" service（在 `appcore/medias.py`，**实施时 grep 确认函数名**，例如 `add_item` / `create_item`）
- 明控视频接口（提供 MP4 URL + 元数据，**实施时确认接口路径，参考 mk_selection.html 现有调用**）
- C 已实现的 `/tasks/api/translators` 端点（翻译员下拉数据，复用）
- `users.permissions.can_translate` 权限位（C 已加）

### 1.4 风险点（实施时必须先核）

- 现有"添加 item" service 的入参签名（filename / 本地文件路径 / product_id 等）—— 实施时 read 后再扩展
- 明控 MP4 URL 的认证 / 跨域 —— 下载时是否需要带 cookie / token
- `media_items.filename` 现状是否保证唯一（如果允许重名怎么办）—— grep 现有逻辑
- 大文件下载（200+ MB）的内存压力 —— 用流式下载（`requests stream=True` + chunk write）

### 1.5 命名约定

- 数据：复用 `media_products` / `media_items`，不加新表
- Blueprint：`mk_import`，前缀 `/mk-import`（与现有 `mk_selection` 区分但相邻）
- 服务模块：`appcore/mk_import.py`
- 前端 JS namespace：`mki*`（mk-import 缩写，避免和现有 `mk*` 选品命名冲突）

---

## 2. 已锁定决定（brainstorm 期间逐条确认）

| # | 决定 |
|---|---|
| 1 | 触发 = 每个视频卡片右下角【加入素材库】按钮（不批量、不全选） |
| 2 | A 范围 = **只做自动入库**，不自动建任务（任务由 C 的"待派单素材"tab 走） |
| 3 | 视频级去重 = `media_items.filename` 精确匹配 |
| 4 | 产品级判断 = `product_code` 标准化匹配（去 `-RJC` 后缀，大小写无关，两边各 normalize 后 exact match） |
| 5 | 文件存储 = 下载到本地后**复用素材管理现有"添加 item" service**（它处理 object_key / 本地 FS 映射） |
| 6 | 视频源 = 明控接口的 MP4 URL，下载到本地临时目录 |
| 7 | 同步模式（admin UI 等下载 + 入库完成才返回） |
| 8 | 字段：视频维度（MP4 URL / filename / duration / cover URL）+ 产品维度（name / link / main_image / product_code）；**不要 shopifyid** |
| 9 | 新品入库时 `media_products.user_id` = A 弹"指定翻译员"小窗，admin 选有 `can_translate` 权限的非 admin 用户 → user_id = 选定翻译员（C 阶段老品自动沿用就是对的） |
| 10 | 已加过的视频 → 按钮显示"已入库"灰色禁用；明空选品页加载时**批量查询** filename 命中状态（一次性 SELECT IN）|
| 11 | 失败处理 = 内联红色 toast 显示具体错误，**不自动重试**；服务端超时 120 秒 |
| 12 | 架构 = 方案 Alpha（独立 service + blueprint，与现有 `medias.py` 解耦） |

---

## 3. 数据模型

**不新增表，不修改 schema**。本 spec 只是逻辑扩展。

### 3.1 复用 `media_products`

字段映射（明控 → media_products）：

| 明控字段 | media_products 字段 | 备注 |
|---|---|---|
| product name | `name` | NOT NULL，直接填 |
| product_code | `product_code` | 直接填（标准化处理在 dedup 时） |
| product link | （新增字段或塞到 `selling_points` 等已有字段？）| **实施时确认 schema，可能需要小 migration 加 `product_link VARCHAR(2048)`** |
| main image URL | （类似上面，新增字段或塞到现有字段）| **同上需确认** |
| mk_id | `mk_id` | 已有字段（2026-04-21 加），填上方便溯源 |
| translator user_id | `user_id` | A 弹窗选定的翻译员 |

⚠️ 实施时第一步 grep `media_products` schema 全字段，看 `product_link` / `main_image` 是不是已有别的命名。如果没有，加一个最小 migration `2026_04_27_media_products_mk_metadata.sql` 加这两列。

### 3.2 复用 `media_items`

| 明控字段 | media_items 字段 | 备注 |
|---|---|---|
| MP4 URL | （下载后存入本地，由现有 service 写 `object_key`）| 用现有 add_item service |
| filename | `filename` | 去重键 |
| duration | `duration_seconds` | 已有字段 |
| cover URL | `cover_object_key` | 也下载封面图后入库（如果有）|
| 产品 ID | `product_id` | A 决定（dedup / new product） |
| lang | 固定 `'en'` | 入的是英文原素材 |
| user_id | 同 product 的 user_id | 跟随产品 |

### 3.3 审计 / 溯源

A 入库时把 `mk_id` 同时写到 `media_products.mk_id`，并在 `media_items` 表如果有 `source` / `import_source` 字段就写 `'mk-selection'`。**实施时 grep 确认 `media_items` 是否已有 source 字段**——如果没有，**不强加**（YAGNI），有需要再扩。

---

## 4. Service 层（`appcore/mk_import.py`）

### 4.1 主入口

```python
def import_mk_video(
    *,
    mk_video_metadata: dict,        # 明控接口数据：mp4_url, filename, duration, cover_url,
                                     # product_name, product_link, main_image, product_code, mk_id
    translator_id: int,              # 新品时由 admin 弹窗选定；老品时可传 None（A 内部用老品 owner）
    actor_user_id: int,              # 当前 admin
) -> dict:
    """同步执行 mk → 素材库入库。返回 {'media_item_id', 'media_product_id', 'is_new_product', 'duration_ms'}.

    抛出：
      - DuplicateError(filename) — filename 已存在于 media_items
      - DownloadError(reason) — MP4 拉不到
      - StorageError(reason) — 本地写盘失败
      - DBError(reason) — DB 操作失败
    """
```

### 4.2 内部 helper

```python
def _normalize_product_code(code: str) -> str:
    """去 -RJC 后缀（case-insensitive），转 lowercase。空返回空。"""

def _find_existing_product(normalized_code: str) -> dict | None:
    """SELECT * FROM media_products WHERE LOWER(REGEXP_REPLACE(product_code, '-rjc$', '')) = %s"""

def _is_video_already_imported(filename: str) -> bool:
    """SELECT 1 FROM media_items WHERE filename=%s AND deleted_at IS NULL LIMIT 1"""

def _download_mp4(url: str, dest_path: str, timeout: int = 120) -> int:
    """流式下载 MP4 到本地。返回下载字节数。timeout 是连接 + 读取总时长。"""

def _download_cover(url: str | None, dest_path: str) -> str | None:
    """如果 cover URL 提供则下载；返回本地路径或 None。"""

def _build_create_product_payload(meta: dict, translator_id: int) -> dict:
    """从 mk metadata + translator_id 构造 media_products INSERT 用的 dict"""
```

### 4.3 状态/异常

```python
class MkImportError(Exception): pass
class DuplicateError(MkImportError): pass        # 视频已加过
class DownloadError(MkImportError): pass         # MP4 下载失败
class StorageError(MkImportError): pass          # 本地写盘 / 现有 add_item 失败
class DBError(MkImportError): pass               # DB 操作失败
```

---

## 5. API 路由（Blueprint `mk_import`，前缀 `/mk-import`）

| 方法 | 路径 | 用途 | 权限 |
|---|---|---|---|
| GET  | `/mk-import/check`         | 批量查 filenames 已入库状态 | login_required |
| POST | `/mk-import/video`         | 触发同步入库（admin 主动） | admin |
| GET  | `/mk-import/translators`   | 翻译员下拉（**复用 C 的 `/tasks/api/translators`，本 endpoint 只是 alias 转发**） | login_required |

### 5.1 `GET /mk-import/check?filenames=a,b,c`

**Request**：query param `filenames`，逗号分隔（最多 100 个）

**Response**：
```json
{
  "imported": ["a.mp4", "c.mp4"],
  "missing": ["b.mp4"]
}
```

### 5.2 `POST /mk-import/video`

**Request body**：
```json
{
  "mk_video_metadata": {
    "mp4_url": "...",
    "filename": "...",
    "duration_seconds": 32.5,
    "cover_url": "...",          // 可空
    "product_name": "...",
    "product_link": "...",
    "main_image": "...",          // 可空
    "product_code": "ABC-DEF-RJC",
    "mk_id": 12345
  },
  "translator_id": 42              // 新品时必填；老品时 admin 也传，A 内部判断是否覆盖
}
```

**Response 200**：
```json
{
  "media_item_id": 8888,
  "media_product_id": 999,
  "is_new_product": true,
  "duration_ms": 4321
}
```

**Response 4xx / 5xx**：
```json
{ "error": "duplicate_filename" / "download_failed" / "storage_failed" / "db_failed", "detail": "..." }
```

服务端超时 120 秒，到点返回 504。

### 5.3 `GET /mk-import/translators`

直接 redirect 或代理到 `/tasks/api/translators`（C 已实现）。或者干脆**前端直接调 `/tasks/api/translators`**，A 不重新提供这个接口，省一个 alias。**实施时倾向后者**。

---

## 6. 前端改造（`web/templates/mk_selection.html`）

### 6.1 视频卡片右下角加按钮

```html
<button class="mki-btn mki-btn--add"
        data-filename="{{ video.filename }}"
        data-mp4-url="{{ video.mp4_url }}"
        data-duration="{{ video.duration_seconds }}"
        ...>
  加入素材库
</button>
```

样式走 Ocean Blue 设计系统（`--accent` 蓝色 + 圆角 6px + 高 28-32px）。

### 6.2 页面加载时批量查询

```javascript
window.addEventListener('DOMContentLoaded', async () => {
  const allBtns = document.querySelectorAll('.mki-btn--add');
  const filenames = Array.from(allBtns).map(b => b.dataset.filename);
  if (!filenames.length) return;
  // 分批 chunk 100 个一次
  for (let i = 0; i < filenames.length; i += 100) {
    const chunk = filenames.slice(i, i + 100);
    const r = await fetch('/mk-import/check?filenames=' + encodeURIComponent(chunk.join(',')));
    const data = await r.json();
    data.imported.forEach(fn => {
      const btn = document.querySelector(`.mki-btn--add[data-filename="${fn}"]`);
      if (btn) {
        btn.textContent = '已入库';
        btn.disabled = true;
        btn.classList.add('mki-btn--disabled');
      }
    });
  }
});
```

### 6.3 点击事件 + 产品判定 + 翻译员选择 mini-modal

```javascript
async function mkiHandleClick(btn) {
  // 先查产品是否已在素材库（按 product_code 去 -RJC）
  // 这一步可以在按钮 data 上预先标记（page load 时同时查），免一个 round trip；
  // 但实施初版可以省略，直接 POST，后端自己判断 + 返回 is_new_product。
  // 如果是新品，需要弹翻译员选择 modal。

  // 简化：总是先弹翻译员 modal（不区分新老品 — 后端忽略老品的 translator_id）
  const translatorId = await mkiOpenTranslatorModal();
  if (translatorId === null) return;  // 用户取消
  btn.disabled = true;
  btn.textContent = '入库中...';
  try {
    const rsp = await fetch('/mk-import/video', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        mk_video_metadata: {/* 从 button data + 父卡片 data 收集 */},
        translator_id: translatorId,
      }),
    });
    if (!rsp.ok) {
      const err = await rsp.json();
      throw new Error(err.detail || err.error);
    }
    const data = await rsp.json();
    btn.textContent = '已入库';
    btn.classList.add('mki-btn--disabled');
    // 弹绿色 toast 提示成功 + "去任务中心建任务" 链接
    mkiToast('success', `素材已入库（item ${data.media_item_id}）${data.is_new_product ? '（新建产品）' : ''}`);
  } catch (e) {
    btn.disabled = false;
    btn.textContent = '加入素材库';
    mkiToast('error', `入库失败：${e.message}`);
  }
}
```

### 6.4 翻译员选择 mini-modal

简易版：
```html
<div id="mkiTranslatorModal" hidden>
  <div class="mki-modal-card">
    <h3>指定翻译员</h3>
    <select id="mkiTranslatorSel"></select>
    <button onclick="mkiTranslatorOK()">确定</button>
    <button onclick="mkiTranslatorCancel()">取消</button>
  </div>
</div>
```

JS 打开时 `fetch('/tasks/api/translators')`，填充下拉。

**优化**：第一次打开时缓存结果（admin 多次入库不重新查）。

### 6.5 老品翻译员复用（决策 #9 的"老品自动沿用"语义）

后端 `import_mk_video` 收到 `translator_id` 时：
- 如果产品已存在（老品） → 忽略 `translator_id`，使用 `media_products.user_id`
- 如果产品不存在（新品） → 用 `translator_id` 设置 `media_products.user_id`

前端**总是弹 modal 让 admin 选**——简单一致。后端自己处理"老品忽略 translator_id"。

---

## 7. 错误处理与 UX

| 场景 | 后端响应 | 前端展示 |
|---|---|---|
| 视频已加过 | 422 `{error: 'duplicate_filename'}` | 红 toast "该视频已加过"（理论上不该发生因为按钮已 disabled） |
| MP4 下载失败 | 502 `{error: 'download_failed', detail: 'HTTP 404'}` | 红 toast "下载失败：HTTP 404" |
| 本地写盘失败 | 500 `{error: 'storage_failed', detail: 'No space left'}` | 红 toast "入库失败：磁盘空间" |
| DB 失败 | 500 `{error: 'db_failed', detail: '...'}` | 红 toast "入库失败：DB 错误，请联系管理员" |
| 服务端超时 | 504 (nginx 配 120s)| 红 toast "服务超时，请稍后重试" |
| 浏览器侧超时 | network error | 红 toast "网络错误" |

**不重试**：决策 #11 — admin 自己点。前端按钮在失败时复原可点击。

---

## 8. 测试策略

### 8.1 单元测试 `tests/test_appcore_mk_import.py`

- `test_normalize_product_code` — RJC 后缀去除 + 大小写
- `test_dedup_video_by_filename`
- `test_resolve_existing_product` — 产品已在库时正确找到
- `test_create_new_product_with_translator` — 新品时 user_id = translator_id
- `test_old_product_ignores_translator_id` — 老品忽略 translator_id
- `test_download_mp4_streams` — 用 monkeypatch mock requests，验证流式
- `test_download_failure_raises` — HTTP 404 → DownloadError
- `test_full_happy_path` — 端到端入库

### 8.2 集成测试 `tests/test_mk_import_routes.py`

- `test_check_endpoint_batch_query` — 多 filename 一次查
- `test_video_endpoint_admin_only` — 非 admin 403
- `test_video_endpoint_happy_path` — 成功返回 200 + ids
- `test_video_endpoint_duplicate_returns_422`

### 8.3 手动验收

- 在测试环境真实跑一次：admin 登录 → 明空选品 → 找一个视频点【加入素材库】 → 弹翻译员选 → 看结果 → 去素材管理验证产品 + item 都建好 → 去任务中心待派单素材 tab 应该看到这个产品

---

## 9. 接驳点

- **B 子系统**：B 完成 AI 评估后，把"国家建议"塞到 C 的"待派单素材 → 一键创建任务"弹窗里（不影响 A）
- **C 子系统**：C 已实现"老品自动沿用 owner 作为翻译员"——A 设的 user_id 就是翻译员，C 会自动选中
- **D 子系统**：未来 D 强化原始视频任务库时，可能改造 A 的下载逻辑（比如 A 下载到 D 的"待处理"队列），暂不影响

---

## 10. 决策日志

12 条决定，brainstorm 期间逐条与用户确认（详见第 2 节）。
