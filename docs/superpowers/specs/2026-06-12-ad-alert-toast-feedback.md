# 广告预警 Toast 操作反馈

## 问题
阈值保存、AI 评估完成等操作无反馈。阈值 modal 静默关闭，用户不知道保存成功还是失败。

## 目标
在 `ad_alerts.html` 内建一个通用的 toast 通知系统。

## 要求

### 1. Toast 函数
在模板 `<script>` 区域的 `state` 初始化之后，加全局函数：

```js
function showToast(message, type) {
  // type: 'success' | 'error' | 'info'
  // 1. 创建浮动 <div> 元素，右上角 fixed 定位
  // 2. 背景色用现有 CSS 变量：
  //    success → var(--oc-ad-alert-green)
  //    error   → var(--oc-ad-alert-red)
  //    info    → var(--oc-ad-alert-blue)
  // 3. 白色文字 + 圆角 10px + padding 12px 20px + font-weight 700
  // 4. 2.5 秒后 fadeOut 移除
  // 5. 多个 toast 垂直堆叠（top 递增）
}
```

### 2. 调用点
- 阈值保存成功（`/api/threshold` POST 成功回调）：`showToast('阈值已更新为 1.40', 'success')`
- 阈值保存失败（catch 分支）：`showToast('保存失败：' + error, 'error')`
- 阈值设定值不合法（`value <= 0` 提前 return 前）：`showToast('阈值必须大于 0', 'error')`
- AI 评估完成（renderAdEvaluations 之前）：`showToast('评估完成，共 N 条建议', 'info')`
- 复制 JSON 成功：`showToast('已复制评估结果', 'success')`

### 3. Toast CSS（内联在模板 `<style>` 末尾）
```css
.oc-ad-alert-toast-container {
  position: fixed;
  top: 20px;
  right: 20px;
  z-index: 99999;
  display: flex;
  flex-direction: column;
  gap: 8px;
  pointer-events: none;
}
.oc-ad-alert-toast {
  pointer-events: auto;
  padding: 12px 20px;
  border-radius: 10px;
  color: #fff;
  font-size: 14px;
  font-weight: 700;
  box-shadow: 0 6px 20px rgba(0,0,0,0.15);
  animation: ocToastIn 0.3s ease-out, ocToastOut 0.3s ease-in 2.2s forwards;
}
@keyframes ocToastIn {
  from { opacity: 0; transform: translateX(40px); }
  to { opacity: 1; transform: translateX(0); }
}
@keyframes ocToastOut {
  from { opacity: 1; }
  to { opacity: 0; transform: translateX(40px); }
}
```

### 4. HTML 容器
在 `<div class="oc-ad-alert-page">` 内部最底部加（若有多个 toast 容器也无妨，`document.body` 上的更可靠）：
```html
<div class="oc-ad-alert-toast-container" id="adAlertToastContainer"></div>
```
或在 JS 里首次调用 `showToast` 时检测容器的存在，不存在则在 `document.body` 上创建。

## 验证
- 打开广告预警页 → 点阈值 ✎ → 输入 1.5 → 保存 → 右上角绿色"阈值已更新为 1.50" toast 自动消失
- AI 评估完成后出现 info toast
- 阈值输入负数 → 红色 toast "阈值必须大于 0"
