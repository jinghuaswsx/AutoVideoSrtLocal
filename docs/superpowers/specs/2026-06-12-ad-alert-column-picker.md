# 问题广告表格列自定义

## 问题
问题广告表格 17 列（广告名 + 账户 + 今天/昨天/7天/30天/整体 各 3 列），小屏横向滚动体验差。"昨天"列与"问题广告定义（今天有消耗无成效）"弱相关，默认应隐藏。

## 目标
给问题广告表格加"自定义列"功能，允许运营选择显示/隐藏列组，状态持久化到 sessionStorage。

## 要求

### 1. 列组定义
在 JS state 中定义列组：
```js
var columnGroups = {
  today:     { label: '今天',     default: true },
  yesterday: { label: '昨天',     default: false },  // 默认隐藏
  last_7d:   { label: '最近 7 天', default: true },
  last_30d:  { label: '最近 30 天', default: true },
  overall:   { label: '整体',     default: true },
};
```

### 2. 自定义列按钮
在问题广告工具栏（`#adAlertProblemLevels` 同一行）加：
```html
<div class="oc-ad-alert-column-picker" style="position:relative;">
  <button class="oc-ad-alert-btn" type="button" id="adAlertColumnPickerBtn">自定义列 ▾</button>
  <div class="oc-ad-alert-column-dropdown" id="adAlertColumnDropdown" hidden>
    <!-- JS 动态渲染 checkbox 列表 -->
  </div>
</div>
```

### 3. column-picker 样式
在 `<style>` 中追加：
```css
.oc-ad-alert-column-dropdown {
  position: absolute;
  top: 100%;
  left: 0;
  z-index: 100;
  background: var(--oc-ad-alert-card);
  border: 1px solid var(--oc-ad-alert-border);
  border-radius: 10px;
  padding: 8px 0;
  box-shadow: 0 8px 24px rgba(0,0,0,0.12);
  min-width: 160px;
}
.oc-ad-alert-column-dropdown label {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 14px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  white-space: nowrap;
}
.oc-ad-alert-column-dropdown label:hover {
  background: var(--oc-ad-alert-table-hover);
}
```

### 4. 列显示/隐藏逻辑
- 表格 `<thead>` `<th>` 和 `<tbody>` `<td>` 中，每个时间窗口的 3 列（消耗/成效/ROAS）加 `<colgroup>` 包裹
- 用 CSS class `.oc-ad-alert-col-hidden` 控制隐藏：`display: none`
- 切换 check/uncheck 时：
  - 读 `sessionStorage.setItem('problem_ads_cols', JSON.stringify(visible))`
  - 调用 `applyColumnVisibility()` 更新 DOM
  - 重渲染 `renderProblemAds()` 时自动读取当前可见列

### 5. 表格结构调整
在 `<table>` 内部，为每个时间窗口加 `<colgroup>`：
```html
<colgroup class="oc-ad-alert-col-today"></colgroup>
<colgroup class="oc-ad-alert-col-yesterday"></colgroup>
<colgroup class="oc-ad-alert-col-last_7d"></colgroup>
<colgroup class="oc-ad-alert-col-last_30d"></colgroup>
<colgroup class="oc-ad-alert-col-overall"></colgroup>
```

对应的 `<th colspan="3">` 和 JS 生成的 `<td>` 也需要包在 colgroup 范围内。注意当前表格是纯 JS 生成（`renderProblemAds` + `renderProblemAds` 中的 `problemMetricCells`），所以列隐藏需要在 JS 渲染后操作 DOM。

实现方式：`renderProblemAds()` 完成后调用 `applyColumnVisibility()`，它遍历每个 colgroup 设置 `.oc-ad-alert-col-hidden` 类来隐藏列。

### 6. 初始化
- `DOMContentLoaded` 时读 sessionStorage。无存储则用默认值（yesterday 默认隐藏）
- 写入 `state.visibleCols` 对象

## 验证
1. 打开 `/ad-alerts/problem` → 点"自定义列"→ 下拉出现 5 个 checkbox
2. "昨天"默认未勾选 → 表格"昨天"列隐藏
3. 勾选"昨天"→ 列出现 → 刷新页面 → 仍勾选、仍可见
4. 取消勾选"整体"→ 隐藏 → 刷新 → 仍隐藏
