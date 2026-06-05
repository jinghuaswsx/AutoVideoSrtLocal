# 素材管理列表 SKU 与时间列精简设计

- 状态：已确认
- 日期：2026-06-05
- 页面：`/medias`
- 入口：产品管理列表

## 文档锚点

- [AGENTS.md](../../../AGENTS.md)：素材管理位于 `web/`，改动后按项目验证顺序做针对性测试。
- [web/static/CLAUDE.md](../../../web/static/CLAUDE.md)：`medias.js` 使用 Ocean Blue 组件风格，新增交互复用已有封装与弹窗。
- [2026-04-15 素材管理列表表格化](2026-04-15-medias-list-table-and-edit-modal-design.md)：素材管理产品列表表格是本次调整对象。
- [2026-04-21 明空 ID 字段](2026-04-21-media-products-mk-id-design.md)：明空 ID 的数据与编辑能力保留，本次只从列表隐藏独立列。
- [2026-06-05 单量情况列](2026-06-05-medias-order-stats-column-design.md)：当前列表列序已有“语种和投放情况 / 单量情况 / 投放情况”连续展示。

## 背景

产品管理列表已经承载主图、产品信息、投放、单量、推送和操作等高密度信息。运营在列表中不再需要独立查看明空 ID，也不需要在 SKU 列预览 ERP 编码摘要；SKU 详情仍需要可进入原有弹窗查看。创建时间和修改时间仍有价值，但不需要占用两列。

## 目标

1. 从产品管理表格中移除“明空 ID”独立列。
2. 将“ERP SKU”列改名为“SKU”。
3. SKU 列只显示一个“SKU”按钮；点击按钮打开现有 SKU 配对详情弹窗。
4. 从产品管理表格中移除“修改时间”独立列。
5. “创建时间”列保留创建时间，并在同一单元格下方用约一半字号展示修改时间。

## 前端设计

`web/static/medias.js`：

- 表头删除 `<th>明空 ID</th>` 和 `<th>修改时间</th>`。
- 表头 `<th>ERP SKU</th>` 改为 `<th>SKU</th>`。
- `colgroup` 删除明空 ID 与修改时间对应列宽，保留 SKU 列一个按钮宽度。
- 行渲染不再输出 `mk-id-cell` 对应 `<td>`。
- SKU 单元格输出：

```html
<td class="sku-action-cell">
  <button type="button" class="oc-btn sm ghost sku-detail-btn" data-sku-detail="{pid}">SKU</button>
</td>
```

- 点击 `[data-sku-detail]` 后按 `pid` 找到当前列表产品，并调用现有 `openSkuDetail(product)`。
- 创建时间单元格输出 `.product-time-cell`，第一行使用 `fmtDateTimeLines(p.created_at)`，第二行 `.product-time-cell__updated` 使用 `fmtDateTimeLines(p.updated_at)`。

`web/templates/medias_list.html`：

- 移除旧 SKU 摘要 hover 样式。
- 新增 SKU 按钮居中样式和时间副行样式；副行字号约为主时间的一半，颜色使用 muted token。

## 非目标

- 不删除 `mk_id` 字段、接口、编辑模态字段或 inline edit 函数。
- 不改变 SKU 详情弹窗结构、刷新 SKU 接口、SKU 手动编辑接口。
- 不新增后端字段或列表 API。
- 不调整投放、单量、推送列口径。

## 验证

1. 静态测试确认表头已隐藏“明空 ID / ERP SKU / 修改时间”，保留“SKU / 创建时间”。
2. 静态测试确认列表行渲染使用 `data-sku-detail` 按钮，并仍调用 `openSkuDetail(product)`。
3. 静态测试确认创建时间单元格包含修改时间副行。
4. 执行：

```bash
pytest tests/test_medias_translation_assets.py -q
node --check web/static/medias.js
```
