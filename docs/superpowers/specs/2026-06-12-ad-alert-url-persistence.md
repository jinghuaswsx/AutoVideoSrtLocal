# 广告预警 URL 搜索参数持久化

## 问题
用户在广告预警页选了严重度筛选、搜了关键词、选了日期范围后，刷新页面或分享链接丢失所有状态。

## 目标
将筛选状态同步到 URL query string，页面加载时从 URL 恢复状态。

## 要求

### 1. 筛选 → URL（loadList 成功后）
`loadList()` 的成功回调（`.then()` 内 `renderList` 之后）调用 `syncUrlParams()`：

```js
function syncUrlParams() {
  // 只在 activeTab === 'alerts' 时同步
  // 用 history.replaceState 更新当前 URL query string
  // 参数：severity, search, start_date, end_date
  // 值空时不要出现在 URL 中
}
```

### 2. URL → 筛选（DOMContentLoaded）
页面加载时，`state` 初始化后、首次加载前，读取 URL params：

```js
function restoreUrlParams() {
  // new URLSearchParams(window.location.search)
  // 有 severity → 更新 state.severity + 高亮对应按钮
  // 有 search → 更新 state.search + 填回 #adAlertSearch.value
  // 有 start_date/end_date → 填回对应 input.value
  // 只对 activeTab === 'alerts' 生效
}
```

### 3. 不同步到 URL 的内容
- `threshold` — 服务端自有，不必同步
- `problem` tab 的状态 — 问题广告筛选是独立场景

### 4. 实现位置
在 `ad_alerts.html` 的 `<script>` 中：
- `restoreUrlParams()` 在 `loadList()` 调用之前执行
- `syncUrlParams()` 在 `loadList().then()` 中调用

## 验证
1. 打开 `/ad-alerts/` → 筛选"严重"→ 搜"夹子"→ 选日期 → URL 变为 `?severity=severe&search=夹子&start_date=2026-06-01`
2. 刷新页面 → 筛选按钮、搜索框、日期都保持
3. 复制 URL 发给别人 → 打开后筛选状态一致
4. 清除日期 → URL 中 date 参数消失
