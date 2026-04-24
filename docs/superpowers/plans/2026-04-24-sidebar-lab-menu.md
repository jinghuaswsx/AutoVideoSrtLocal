# Sidebar Lab Menu Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 调整左侧菜单顺序，并新增一个默认收起的“实验室”抽屉分组来承载实验性质入口。

**Architecture:** 保持现有 `web/templates/layout.html` 作为侧栏唯一菜单定义来源，不引入新的后端配置层。通过在模板中加入原生 `<details>/<summary>` 抽屉和少量配套样式完成交互，并用 `tests/test_av_sync_menu_routes.py` 锁定新排序、分组和实验室底部位置。

**Tech Stack:** Flask/Jinja2、原生 HTML/CSS、pytest

---

### Task 1: 锁定菜单新结构测试

**Files:**
- Modify: `tests/test_av_sync_menu_routes.py`
- Test: `tests/test_av_sync_menu_routes.py`

- [ ] **Step 1: 写出会失败的菜单排序与实验室分组测试**

```python
def test_dashboard_sidebar_promotes_primary_translation_entries(authed_client_no_db):
    resp = authed_client_no_db.get("/video-translate-av-sync")
    nav_html = resp.get_data(as_text=True)

    assert nav_html.index('href="/medias"') < nav_html.index('href="/multi-translate"')
    assert nav_html.index('href="/multi-translate"') < nav_html.index('href="/title-translate"')
    assert nav_html.index('href="/title-translate"') < nav_html.index('href="/image-translate"')
    assert nav_html.index('href="/image-translate"') < nav_html.index('href="/subtitle-removal"')
```

- [ ] **Step 2: 运行单测并确认因旧菜单结构失败**

Run: `pytest tests/test_av_sync_menu_routes.py -q`
Expected: 至少 1 个菜单顺序/实验室结构断言失败

- [ ] **Step 3: 补充实验室抽屉测试**

```python
def test_dashboard_sidebar_places_lab_group_at_bottom(authed_client_no_db):
    resp = authed_client_no_db.get("/video-translate-av-sync")
    nav_html = resp.get_data(as_text=True)

    assert "实验室" in nav_html
    assert nav_html.rfind("<details") > nav_html.index('href="/"')
    assert nav_html.index('href="/voice-library"') > nav_html.index(">实验室<")
```

- [ ] **Step 4: 再次运行单测确认仍然报红但错误符合预期**

Run: `pytest tests/test_av_sync_menu_routes.py -q`
Expected: FAIL，失败点落在实验室抽屉和新菜单顺序

### Task 2: 在侧栏模板实现实验室抽屉与新顺序

**Files:**
- Modify: `web/templates/layout.html`
- Test: `tests/test_av_sync_menu_routes.py`

- [ ] **Step 1: 按确认方案重排主菜单**

```html
<a href="/medias">素材管理</a>
<a href="/multi-translate">多语种视频翻译</a>
<a href="{{ url_for('title_translate.page') }}">多语言标题翻译</a>
<a href="{{ url_for('image_translate.page_list') }}">图片翻译</a>
<a href="{{ url_for('subtitle_removal.list_page') }}">字幕移除</a>
```

- [ ] **Step 2: 新增“实验室”抽屉并放到底部**

```html
<details class="sidebar-group sidebar-lab-group">
  <summary>
    <span class="nav-icon">🧪</span>
    <span class="nav-label">实验室</span>
  </summary>
  <div class="sidebar-subnav">
    <a href="/voice-library">声音仓库</a>
    <a href="/prompt-library">提示词典</a>
    <a href="/copywriting">文案创作</a>
  </div>
</details>
```

- [ ] **Step 3: 补齐抽屉样式，让 summary 与现有菜单视觉保持一致**

```css
.sidebar-group summary {
  display: flex;
  align-items: center;
  cursor: pointer;
}

.sidebar-subnav a {
  padding-left: 36px;
}
```

- [ ] **Step 4: 运行菜单单测确认转绿**

Run: `pytest tests/test_av_sync_menu_routes.py -q`
Expected: PASS

### Task 3: 完整验证

**Files:**
- Verify only: `tests/test_av_sync_menu_routes.py`

- [ ] **Step 1: 运行菜单相关验证**

Run: `pytest tests/test_av_sync_menu_routes.py -q`
Expected: PASS，0 failures

- [ ] **Step 2: 检查工作区变更**

Run: `git status --short`
Expected: 仅出现计划文档、菜单模板和测试文件变更
