# Link Check Desktop Local Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为链接检查桌面端生成本地 `report.html`，并在任务完成后自动打开，提供接近原链接检查详情页的静态复核界面。

**Architecture:** 新增 `link_check_desktop/report.py` 负责把控制器结果渲染为自包含静态 HTML。`controller.py` 在分析完成后调用报告生成器并回填 `report_html_path`；`gui.py` 在成功回调中展示路径并自动打开本地结果页。

**Tech Stack:** Python 3.14, Tkinter, pathlib, webbrowser, pytest

---

### Task 1: 落地报告生成器

**Files:**
- Create: `link_check_desktop/report.py`
- Modify: `link_check_desktop/__init__.py`
- Test: `tests/test_link_check_desktop_report.py`

- [ ] **Step 1: 写报告生成器失败测试**

```python
def test_write_report_renders_summary_and_relative_image_paths(tmp_path):
    from link_check_desktop import report

    workspace_root = tmp_path / "img" / "402-20260421170000"
    (workspace_root / "site").mkdir(parents=True)
    (workspace_root / "reference").mkdir(parents=True)
    site_file = workspace_root / "site" / "site-001.jpg"
    ref_file = workspace_root / "reference" / "ref-001.jpg"
    site_file.write_bytes(b"site")
    ref_file.write_bytes(b"ref")

    result = {
        "workspace_root": str(workspace_root),
        "product": {"id": 402, "name": "Demo Product"},
        "target_language": "fr",
        "target_language_name": "法语",
        "normalized_url": "https://newjoyloo.com/fr/products/demo",
        "analysis": {
            "summary": {"overall_decision": "review", "pass_count": 1, "replace_count": 1, "review_count": 0},
            "items": [{
                "id": "site-001",
                "kind": "detail",
                "source_url": "https://cdn.example.com/site-001.jpg",
                "local_path": str(site_file),
                "reference_match": {"status": "matched", "reference_path": str(ref_file), "reference_filename": "ref-001.jpg"},
                "binary_quick_check": {"status": "pass", "binary_similarity": 0.98, "foreground_overlap": 0.97, "threshold": 0.90, "reason": "binary ok"},
                "same_image_llm": {"status": "done", "answer": "是", "channel_label": "Google AI Studio", "model": "gemini-demo", "reason": "same image"},
                "analysis": {"decision": "pass", "detected_language": "fr", "quality_score": 92, "quality_reason": "quality ok"},
                "download_evidence": {"requested_url": "https://cdn.example.com/site-001.jpg", "resolved_url": "https://cdn.example.com/site-001.jpg", "preserved_asset": True, "content_type": "image/jpeg"},
                "status": "done",
                "error": "",
            }],
        },
    }

    report_path = report.write_report(result)

    html = report_path.read_text(encoding="utf-8")
    assert report_path.name == "report.html"
    assert "Demo Product" in html
    assert "site/site-001.jpg" in html
    assert "reference/ref-001.jpg" in html
    assert "200px" in html
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_link_check_desktop_report.py::test_write_report_renders_summary_and_relative_image_paths -v`
Expected: FAIL with `ModuleNotFoundError` or missing `write_report`

- [ ] **Step 3: 写最小实现**

```python
def write_report(result: dict[str, Any]) -> Path:
    workspace_root = Path(result["workspace_root"])
    html = _render_document(result, workspace_root)
    output = workspace_root / "report.html"
    output.write_text(html, encoding="utf-8")
    return output


def open_report(path: str | Path) -> None:
    report_path = Path(path).resolve()
    webbrowser.open(report_path.as_uri())
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_link_check_desktop_report.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add link_check_desktop/__init__.py link_check_desktop/report.py tests/test_link_check_desktop_report.py
git commit -m "feat(link-check): add local desktop report generator"
```

### Task 2: 接入控制器结果

**Files:**
- Modify: `link_check_desktop/controller.py`
- Modify: `tests/test_link_check_desktop_controller.py`

- [ ] **Step 1: 写控制器失败测试**

```python
def test_run_link_check_generates_report(monkeypatch, tmp_path):
    from link_check_desktop import controller

    workspace_root = tmp_path / "img" / "123-20260420230518"
    workspace = SimpleNamespace(
        root=workspace_root,
        reference_dir=workspace_root / "reference",
        site_dir=workspace_root / "site",
        compare_dir=workspace_root / "compare",
    )
    written = []

    monkeypatch.setattr(controller.storage, "create_workspace", lambda product_id, now=None: workspace)
    monkeypatch.setattr(controller.storage, "write_json", lambda path, payload: written.append((path, payload)))
    monkeypatch.setattr(controller.report, "write_report", lambda payload: workspace_root / "report.html")
    ...

    result = controller.run_link_check(...)

    assert result["report_html_path"].endswith("report.html")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_link_check_desktop_controller.py::test_run_link_check_generates_report -v`
Expected: FAIL because `report_html_path` missing or `report.write_report` not called

- [ ] **Step 3: 写最小实现**

```python
report_path = report.write_report(result)
result["report_html_path"] = str(report_path)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_link_check_desktop_controller.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add link_check_desktop/controller.py tests/test_link_check_desktop_controller.py
git commit -m "feat(link-check): attach local report to desktop results"
```

### Task 3: 接入 GUI 自动打开

**Files:**
- Modify: `link_check_desktop/gui.py`
- Modify: `tests/test_link_check_desktop_gui.py`

- [ ] **Step 1: 写 GUI 失败测试**

```python
def test_start_run_opens_generated_report(monkeypatch):
    from link_check_desktop import gui

    opened = []
    monkeypatch.setattr(gui.report, "open_report", lambda path: opened.append(path))
    monkeypatch.setattr(
        gui.controller,
        "run_link_check",
        lambda **kwargs: {
            "product": {"id": 402},
            "target_language": "en",
            "workspace_root": "G:\\Code\\AutoVideoSrt\\.worktrees\\link-check-desktop\\img\\402-demo",
            "report_html_path": "G:\\Code\\AutoVideoSrt\\.worktrees\\link-check-desktop\\img\\402-demo\\report.html",
            "analysis": {"summary": {"overall_decision": "review", "pass_count": 18, "replace_count": 1, "review_count": 0}},
        },
    )
    ...
    app.start_run()
    assert opened == ["G:\\Code\\AutoVideoSrt\\.worktrees\\link-check-desktop\\img\\402-demo\\report.html"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_link_check_desktop_gui.py::test_start_run_opens_generated_report -v`
Expected: FAIL because GUI never opens report

- [ ] **Step 3: 写最小实现**

```python
report_html_path = result.get("report_html_path") or ""
if report_html_path:
    try:
        report.open_report(report_html_path)
    except Exception:
        pass
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_link_check_desktop_gui.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add link_check_desktop/gui.py tests/test_link_check_desktop_gui.py
git commit -m "feat(link-check): open local report from desktop gui"
```

### Task 4: 更新文档与回归验证

**Files:**
- Modify: `link_check_desktop/README.md`
- Test: `tests/test_link_check_desktop_report.py`
- Test: `tests/test_link_check_desktop_controller.py`
- Test: `tests/test_link_check_desktop_gui.py`

- [ ] **Step 1: 更新 README**

```md
任务完成后会在 `img/<product_id>-<timestamp>/report.html` 生成本地结果页，并自动用默认浏览器打开。
```

- [ ] **Step 2: 运行聚焦测试**

Run: `pytest tests/test_link_check_desktop_report.py tests/test_link_check_desktop_controller.py tests/test_link_check_desktop_gui.py -q`
Expected: all pass

- [ ] **Step 3: 运行桌面端全套测试**

Run: `pytest tests/test_link_check_desktop_bootstrap_api.py tests/test_link_check_desktop_browser_worker.py tests/test_link_check_desktop_build_exe.py tests/test_link_check_desktop_controller.py tests/test_link_check_desktop_gui.py tests/test_link_check_desktop_html_extract.py tests/test_link_check_desktop_image_analyzer.py tests/test_link_check_desktop_image_compare.py tests/test_link_check_desktop_report.py tests/test_link_check_desktop_same_image.py tests/test_link_check_desktop_settings.py tests/test_link_check_desktop_storage.py -q`
Expected: all pass

- [ ] **Step 4: 重新打包绿色版**

Run: `python link_check_desktop/build_exe.py`
Expected: build succeeds and outputs `dist/LinkCheckDesktop/`

- [ ] **Step 5: 提交**

```bash
git add link_check_desktop/README.md
git commit -m "docs(link-check): document local desktop report output"
```
