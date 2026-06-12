# AI 评估结果可导出

## 问题
AI 评估结果仅在弹窗中一次性展示，无 JSON 导出或复制功能，运营无法粘贴到其他工具分析。

## 目标
在 AI 评估结果区顶部加"复制 JSON"按钮，一键将评估结果写入剪贴板。

## 要求

### 1. 复制按钮
在 `renderAdEvaluations()` 函数的输出 HTML 中，在结果区顶部加按钮：
```html
<div class="oc-ad-alert-evaluation-actions">
  <button class="oc-ad-alert-btn oc-ad-alert-btn-sm" type="button" id="adAlertEvalCopyBtn">📋 复制 JSON</button>
</div>
```
按钮样式复用 `oc-ad-alert-btn`，加小号变体：
```css
.oc-ad-alert-btn-sm {
  min-height: 28px;
  padding: 0 10px;
  font-size: 12px;
}
```

### 2. 复制逻辑
在 `renderAdEvaluations()` 中，替换 `htmlParts.push` 拼接的头部，加入复制按钮。绑定点击事件：

```js
document.getElementById('adAlertEvalCopyBtn').addEventListener('click', function() {
  var data = adEvaluationCache[cacheKey];
  if (!data) return;
  var json = JSON.stringify(data, null, 2);
  navigator.clipboard.writeText(json).then(function() {
    showToast('已复制评估结果', 'success');
  }).catch(function() {
    // fallback：创建 textarea select 复制
    showToast('复制失败，请手动选择复制', 'error');
  });
});
```

注意：`cacheKey` 需要在 `renderAdEvaluations` 作用域可访问。当前 `cacheKey` 在 `runAdEvaluation()` 中定义。有两种方式：
- A. 把 `cacheKey` 存到 `state` 对象上（推荐，最干净）
- B. 用 `data-ad-eval-cache-key` 属性挂在结果容器上

推荐方案 A：在 `state` 上加 `evalCacheKey` 字段，`runAdEvaluation()` 中设置 `state.evalCacheKey = cacheKey`。

### 3. 复制按钮的可见性
- AI 评估结果容器 `#adAlertEvaluationResult` 原本有 `hidden` 属性
- 复制按钮应该跟随结果容器的可见性
- 如果评估列表为空（`暂无需要评估的亏损广告`），不显示复制按钮

### 4. 实现位置
`renderAdEvaluations()` 函数中，在 `.oc-ad-alert-evaluation-title` 行之后、渲染分组之前插入按钮 HTML。然后在外部的 click 委托中或直接在渲染后用事件绑定。

## 验证
1. 打开广告预警 → 点任意语言 badge → 详情弹窗 → 点"AI 评估"→ 结果出现
2. 复制按钮"📋 复制 JSON" 出现在结果标题旁
3. 点击按钮 → toast "已复制评估结果" → 粘贴到记事本 → 格式正确的 JSON 数组
4. 无评估结果时按钮不出现
