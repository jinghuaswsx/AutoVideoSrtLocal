# web/templates/

Jinja 模板专属规则。改本目录任何 `*.html` 前必读。

## 翻译详情页继承防呆（multi/omni/ja）

**已知事故 2026-05-04**：multi/omni 详情页（`/multi-translate/<id>`、`/omni-translate/<id>`）右侧浮一个独立"原文标准化"卡片。根因：`multi_translate_detail.html` / `omni_translate_detail.html` 当时是 `{% include "_translate_detail_shell.html" %}` 后再拼 `<section class="card asr-normalize-card">`，落到了 shell 输出的 `</html>` **之外**。`layout.html` 是 `display: flex` → 这块孤立 section 变成 `.sidebar` / `.main-wrap` 之外的第三列贴在视口右沿。

### 硬规则
- detail 模板要在 `_translate_detail_shell.html` 渲染出的页面上追加内容（asr-normalize-card 等）必须：
  - `{% extends "_translate_detail_shell.html" %}`
  - 用 `{% block detail_extra %}…{% endblock %}` 包裹要追加的 HTML/script/style
- `detail_extra` 占位符位置：`_translate_detail_shell.html` 的 `{% block content %}` 内、`{% include "_task_workbench.html" %}` 之后、`{% endif %}` 之前。新增第四个 detail_mode（如 av_sync 之外的）沿用同一 block。

### 反模式（禁止）
- `{% include "_translate_detail_shell.html" %}` 之后再追加任何 raw HTML / `<script>` / `<style>`。
- 通用约束：所有"shell extends layout"场景，`{% include base_with_extends %}` 之后只允许跟 `{% set %}` 等不产生文本输出的指令。

### 自检
改 `multi_translate_detail.html` / `omni_translate_detail.html` / `ja_translate_detail.html` / `_translate_detail_shell.html` 后：
1. `pytest tests/test_multi_translate_routes.py tests/test_omni_translate_routes.py tests/test_runtime_multi_asr_normalize.py -q`
2. 如有 asr-normalize-card 渲染路径，DevTools 确认 `document.querySelector('section.asr-normalize-card').parentElement` 链路上能找到 `<main class="main-content">`，且其 `getBoundingClientRect()` 横向 bbox 在 main 的 left/right 内（不会贴 viewport 右沿）。

## CSRF / 路由守卫
- 新模板里的 `<form>` POST 必须含 CSRF token。前端 fetch 走 `X-CSRFToken` header（从 `layout.html` `<meta name="csrf-token">` 读）。
- 路由侧加 `@login_required + @admin_required`（layout.html 访问 `current_user.username`，未登录会 500）。

## API 账单分页组件
为 `admin_ai_billing.html` 页面添加分页组件：
1. 包含数据总条数和总页数显示。
2. 包含首页、末页、上一页、下一页按钮。
3. 显示当前是第几页。
4. 包含去第 XXX 页的跳转输入框与确定按钮。
5. 分页组件放置在明细表格的顶部与底部，两处分页组件的状态及跳转逻辑需完全同步并保留所有筛选过滤参数。
