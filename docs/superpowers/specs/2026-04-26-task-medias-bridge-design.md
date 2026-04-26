# E 子系统：task-medias-bridge 设计文档

- **日期**：2026-04-26
- **范围**：E — 把任务中心和素材管理页深度打通（智能深度链接 + 翻译产物状态面板）
- **上位**：[docs/任务中心需求文档-2026-04-26.md](../../任务中心需求文档-2026-04-26.md)

---

## 0. 一句话目标

让翻译员从任务中心点【翻译】跳到素材管理时，**自动预选 + 锁定**对应产品/素材/语种，且能在任务详情抽屉里**一眼看到产物缺什么**（封面 / 视频 / 文案）。

不做：iframe 嵌入、UI 重写、半路加国家、推送页改造。

---

## 1. 范围

### 1.1 做什么

1. 改 `web/templates/medias_list.html`：检测 `from_task` / `product` / `item` / `lang` query → 自动定位产品 + 弹出对应语种编辑面板 + 顶部蓝色 banner 显示来源
2. 改 `web/templates/tasks_list.html`：子任务详情抽屉加"翻译产物状态"面板（4 项 ✓/✗：封面 / 视频 / 文案 / 链接）
3. 新增 `GET /tasks/api/child/<id>/readiness`：返回该子任务对应语种 item 的 `compute_readiness()` 结果

### 1.2 不做

- iframe / SPA 嵌入翻译 UI
- /pushes 页面新增任务来源列
- 半路加国家功能
- 任何 schema 改动

---

## 2. 已锁定决定

12 条（详见上文）。

---

## 3. 数据 / Schema

不动。复用：
- `tasks` (C 已建)
- `media_items` / `media_products` / `media_copywritings`
- `appcore.pushes.compute_readiness(item, product)` 已存在

---

## 4. 服务层

无新模块。1 个新端点：

```python
# in web/routes/tasks.py
@bp.route("/api/child/<int:tid>/readiness", methods=["GET"])
@login_required
def api_child_readiness(tid: int):
    """返回该子任务对应语种 media_item 的 readiness dict + missing 列表。"""
    from appcore.db import query_one
    from appcore import pushes, tasks as tsvc
    row = query_one(
        "SELECT t.*, p.id AS product_id "
        "FROM tasks t JOIN media_products p ON p.id=t.media_product_id "
        "WHERE t.id=%s AND t.parent_task_id IS NOT NULL", (tid,)
    )
    if not row:
        return jsonify({"error": "child task not found"}), 404
    item = tsvc._find_target_lang_item(row["media_product_id"], row["country_code"])
    if not item:
        return jsonify({
            "ready": False, "missing": ["lang_item_missing"],
            "country_code": row["country_code"],
        })
    product = tsvc._find_product(row["media_product_id"])
    readiness = pushes.compute_readiness(item, product)
    is_ready = pushes.is_ready(readiness)
    missing = [k for k, v in readiness.items()
               if not str(k).endswith("_reason") and not v]
    return jsonify({
        "ready": is_ready,
        "missing": missing,
        "readiness": {k: bool(v) for k, v in readiness.items() if not str(k).endswith("_reason")},
        "country_code": row["country_code"],
        "media_item_id": item["id"],
    })
```

---

## 5. 前端改动

### 5.1 medias_list.html — query string 识别 + banner

页面加载时（DOMContentLoaded）：
```javascript
const params = new URLSearchParams(window.location.search);
const fromTask = params.get('from_task');
const productId = params.get('product');
const itemId = params.get('item');
const lang = params.get('lang');
const action = params.get('action');  // 'translate' | 'history'

if (fromTask) {
  // 1) 顶部插入蓝色 banner
  // 2) 滚动到 productId 对应的行（用 data-product-id 找）
  // 3) 自动打开该产品的编辑模态 + 选中 lang tab
  //    (复用现有 openEditModal(productId) 函数 + setLangTab(lang))
  // 4) action=='translate' 时高亮"开始翻译"按钮（不自动点）
}
```

Banner HTML：
```html
<div id="taskBridgeBanner" style="background:var(--oc-accent-subtle); border-left:4px solid var(--oc-accent);
     padding:12px 16px; margin-bottom:12px; border-radius:6px; display:none;">
  <strong>来自任务中心 #<span id="tbTaskId"></span></strong>
  · 产品 <span id="tbProductName"></span> · 语种 <span id="tbLang"></span>
  <a href="/tasks/" target="_blank" style="float:right;">返回任务中心 →</a>
</div>
```

### 5.2 tasks_list.html — 翻译产物状态面板

`tcRenderDetail()` 函数对**子任务**追加 readiness 面板。先 fetch `/tasks/api/child/<id>/readiness`，渲染：

```
翻译产物状态 (DE)
✓ 视频   ✓ 封面   ✗ 文案   ✗ 链接
```

行内 ✓/✗ 用颜色（✓ 绿色 / ✗ 红色），点 ✗ 跳转到 /medias/ 加 query 直接定位。

readiness keys 映射：
- `has_video` → 视频
- `has_cover` → 封面
- `has_copywriting` → 文案
- `has_push_texts` → 推送文案
- `is_listed` → 商品在架

---

## 6. 测试

### 6.1 集成测试

- `test_child_readiness_endpoint_smoke`：authed_client_no_db → 200/500
- `test_child_readiness_returns_missing_list`：DB-backed，verify shape

### 6.2 手动验收

- 任务中心 → 子任务详情 → 看到 readiness 面板
- 点【翻译】跳 /medias/ → banner 显示 + 自动定位产品

---

## 7. 接驳

- 上游：复用 C 子任务、A 入库的 media_items
- 下游：素材管理已有的翻译能力 (multi_translate / image_translate / copywriting)
- 不影响 D（D 处理父任务的视频，E 处理子任务的翻译产物）
