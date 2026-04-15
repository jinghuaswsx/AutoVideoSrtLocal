# 字幕配置模块 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在任务配置面板新增字幕样式区块，允许用户选择字体（6款）、字号（小/中/大）并通过 TikTok 仿真手机界面拖拽定位字幕位置，位置保存为全局 localStorage 默认。

**Architecture:** 后端 `pipeline/compose.py` 新增分辨率自适应字号计算和动态 MarginV 换算；任务配置流程（task_state → task.py route → runtime.py → compose_video）透传三个新参数（font_name / font_size_preset / subtitle_position_y）；前端配置面板替换旧的 `<select>` 为字体卡片 + 字号按钮组 + 手机弹窗定位器。

**Tech Stack:** Python 3, ffmpeg `subtitles` filter（ASS force_style），Google Fonts（.ttf 文件打包），原生 JS + 现有 Flask/Jinja2 模板体系，localStorage 持久化位置

---

## 文件结构

| 文件 | 动作 | 说明 |
|---|---|---|
| `fonts/Anton-Regular.ttf` | 新增 | Google Fonts |
| `fonts/Oswald-Bold.ttf` | 新增 | Google Fonts |
| `fonts/BebasNeue-Regular.ttf` | 新增 | Google Fonts |
| `fonts/Montserrat-ExtraBold.ttf` | 新增 | Google Fonts |
| `fonts/Poppins-Bold.ttf` | 新增 | Google Fonts |
| `pipeline/compose.py` | 修改 | 新增三个辅助函数；重构 `_build_subtitle_filter` 和 `_compose_hard`；扩展 `compose_video` 签名 |
| `tests/test_compose.py` | 修改 | 更新旧测试；新增辅助函数测试 |
| `appcore/task_state.py` | 修改 | 在 `_empty_task_state()` 默认字典里新增三个字段 |
| `web/routes/task.py` | 修改 | `start()` 路由读取三个新参数 |
| `appcore/runtime.py` | 修改 | `_step_compose()` 透传三个新参数到 `compose_video()` |
| `web/templates/_task_workbench.html` | 修改 | 替换旧字幕位置 `<select>` 为新 UI 区块 + 手机弹窗 HTML |
| `web/templates/_task_workbench_scripts.html` | 修改 | 替换旧 JS 引用；新增字体卡片/字号/手机拖拽逻辑 |

---

## Task 1：下载字体文件

**Files:**
- Create: `fonts/Anton-Regular.ttf`
- Create: `fonts/Oswald-Bold.ttf`
- Create: `fonts/BebasNeue-Regular.ttf`
- Create: `fonts/Montserrat-ExtraBold.ttf`
- Create: `fonts/Poppins-Bold.ttf`

- [ ] **Step 1: 创建 fonts 目录并下载五个字体文件**

```bash
mkdir -p fonts
curl -L -o fonts/Anton-Regular.ttf \
  "https://github.com/google/fonts/raw/main/ofl/anton/Anton-Regular.ttf"
curl -L -o fonts/Oswald-Bold.ttf \
  "https://github.com/google/fonts/raw/main/ofl/oswald/static/Oswald-Bold.ttf"
curl -L -o fonts/BebasNeue-Regular.ttf \
  "https://github.com/google/fonts/raw/main/ofl/bebasneu/BebasNeue-Regular.ttf"
curl -L -o fonts/Montserrat-ExtraBold.ttf \
  "https://github.com/google/fonts/raw/main/ofl/montserrat/static/Montserrat-ExtraBold.ttf"
curl -L -o fonts/Poppins-Bold.ttf \
  "https://github.com/google/fonts/raw/main/ofl/poppins/Poppins-Bold.ttf"
```

- [ ] **Step 2: 验证文件存在且大小合理（>20KB）**

```bash
ls -lh fonts/
```

预期每个文件 30KB–200KB，无 0 字节文件。

- [ ] **Step 3: 添加 fonts/ 到 .gitignore（字体文件不进版本库）**

在 `.gitignore` 末尾追加：

```
fonts/*.ttf
fonts/*.otf
```

- [ ] **Step 4: 提交 .gitignore 变更**

```bash
git add .gitignore
git commit -m "chore: 排除字体文件（fonts/*.ttf）不进版本库"
```

---

## Task 2：在 compose.py 新增辅助函数（TDD）

**Files:**
- Modify: `pipeline/compose.py`
- Modify: `tests/test_compose.py`

- [ ] **Step 1: 写失败测试——字号计算**

在 `tests/test_compose.py` 末尾追加：

```python
from pipeline.compose import _compute_font_size, _compute_margin_v


def test_compute_font_size_medium_at_1080p():
    assert _compute_font_size(1080, "medium") == 14


def test_compute_font_size_small_at_1080p():
    assert _compute_font_size(1080, "small") == 11


def test_compute_font_size_large_at_1080p():
    assert _compute_font_size(1080, "large") == 18


def test_compute_font_size_scales_with_height():
    # 720p medium: round(720/1080*14) = round(9.33) = 9
    assert _compute_font_size(720, "medium") == 9
    # 1920p large: round(1920/1080*18) = round(32.0) = 32
    assert _compute_font_size(1920, "large") == 32


def test_compute_font_size_unknown_preset_falls_back_to_medium():
    assert _compute_font_size(1080, "xlarge") == 14


def test_compute_margin_v_default_position():
    # position_y=0.68 → margin_v = round(1080*(1-0.68)) = round(345.6) = 346
    assert _compute_margin_v(1080, 0.68) == 346


def test_compute_margin_v_bottom():
    # position_y=0.95 → margin_v = round(1080*0.05) = 54
    assert _compute_margin_v(1080, 0.95) == 54


def test_compute_margin_v_top():
    # position_y=0.1 → margin_v = round(1080*0.9) = 972
    assert _compute_margin_v(1080, 0.1) == 972
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
pytest tests/test_compose.py::test_compute_font_size_medium_at_1080p -v
```

预期：`ImportError: cannot import name '_compute_font_size'`

- [ ] **Step 3: 在 compose.py 顶部（imports 之后）添加常量和辅助函数**

在 `pipeline/compose.py` 的 `import os` 和 `import subprocess` 之后、`compose_video` 之前插入：

```python
_FONT_SIZE_BASE: dict[str, int] = {"small": 11, "medium": 14, "large": 18}


def _fonts_dir() -> str:
    """返回项目 fonts/ 目录的绝对路径。"""
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fonts")


def _compute_font_size(video_height: int, preset: str) -> int:
    """根据视频高度和预设档位计算自适应字号（ASS pt）。"""
    base = _FONT_SIZE_BASE.get(preset, _FONT_SIZE_BASE["medium"])
    return round(video_height / 1080 * base)


def _compute_margin_v(video_height: int, position_y: float) -> int:
    """将「距顶百分比」转换为 ffmpeg ASS MarginV（距底像素）。"""
    return round(video_height * (1.0 - position_y))
```

- [ ] **Step 4: 运行新测试，确认全部通过**

```bash
pytest tests/test_compose.py -k "compute" -v
```

预期：8 个测试全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add pipeline/compose.py tests/test_compose.py
git commit -m "feat: compose 新增字号自适应和 MarginV 换算辅助函数"
```

---

## Task 3：新增 `_get_video_height` 并重构 `_build_subtitle_filter` / `_compose_hard`（TDD）

**Files:**
- Modify: `pipeline/compose.py`
- Modify: `tests/test_compose.py`

- [ ] **Step 1: 写失败测试——`_build_subtitle_filter` 接受字体参数**

在 `tests/test_compose.py` 末尾追加：

```python
from pipeline.compose import _build_subtitle_filter


def test_build_subtitle_filter_includes_font_name():
    vf = _build_subtitle_filter("/tmp/sub.srt", "Anton", 14, 346)
    assert "FontName=Anton" in vf


def test_build_subtitle_filter_includes_font_size():
    vf = _build_subtitle_filter("/tmp/sub.srt", "Impact", 18, 50)
    assert "FontSize=18" in vf


def test_build_subtitle_filter_includes_margin_v():
    vf = _build_subtitle_filter("/tmp/sub.srt", "Impact", 14, 346)
    assert "MarginV=346" in vf
    assert "Alignment=2" in vf


def test_build_subtitle_filter_includes_fontsdir():
    vf = _build_subtitle_filter("/tmp/sub.srt", "Anton", 14, 346)
    assert "fontsdir=" in vf
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
pytest tests/test_compose.py::test_build_subtitle_filter_includes_font_name -v
```

预期：`TypeError: _build_subtitle_filter() takes 2 positional arguments but 4 were given`

- [ ] **Step 3: 替换 `_build_subtitle_filter` 实现**

将 `pipeline/compose.py` 中现有的 `_build_subtitle_filter` 函数完整替换为：

```python
def _build_subtitle_filter(srt_path: str, font_name: str, font_size_pt: int, margin_v: int) -> str:
    fonts_dir = _escape_subtitle_filter_path(_fonts_dir())
    escaped_path = _escape_subtitle_filter_path(srt_path)
    return (
        f"subtitles=filename='{escaped_path}'"
        f":fontsdir='{fonts_dir}'"
        f":force_style='FontName={font_name},FontSize={font_size_pt},"
        f"PrimaryColour=&HFFFFFF,OutlineColour=&H000000,Outline=2,Bold=1,"
        f"Alignment=2,MarginV={margin_v}'"
    )
```

- [ ] **Step 4: 在 `_get_duration` 之前添加 `_get_video_height`**

```python
def _get_video_height(video_path: str) -> int:
    """读取视频流高度；读取失败时返回 1080 作为安全默认值。"""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=height",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return int(result.stdout.strip())
    except ValueError:
        return 1080
```

- [ ] **Step 5: 替换 `_compose_hard` 签名和实现**

将现有 `_compose_hard` 函数完整替换为：

```python
def _compose_hard(
    video_path: str,
    srt_path: str,
    output_path: str,
    font_name: str = "Impact",
    font_size_preset: str = "medium",
    subtitle_position_y: float = 0.68,
) -> None:
    video_height = _get_video_height(video_path)
    font_size_pt = _compute_font_size(video_height, font_size_preset)
    margin_v = _compute_margin_v(video_height, subtitle_position_y)
    vf = _build_subtitle_filter(srt_path, font_name, font_size_pt, margin_v)

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-c:a", "copy",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"硬字幕版合成失败: {result.stderr}")
```

- [ ] **Step 6: 更新 `compose_video` 中对 `_compose_hard` 的调用**

找到 `compose_video` 函数内的这一行：

```python
    _compose_hard(soft_output, srt_path, subtitle_position, hard_output)
```

替换为（新参数在后，旧 `subtitle_position` 字符串参数不再传入）：

```python
    _compose_hard(
        soft_output, srt_path, hard_output,
        font_name=font_name,
        font_size_preset=font_size_preset,
        subtitle_position_y=subtitle_position_y,
    )
```

- [ ] **Step 7: 更新 `compose_video` 函数签名**，在现有参数末尾新增三个关键字参数：

```python
def compose_video(
    video_path: str,
    tts_audio_path: str,
    srt_path: str,
    output_dir: str,
    subtitle_position: str = "bottom",   # 保留供 CapCut 模块使用
    timeline_manifest: dict | None = None,
    variant: str | None = None,
    font_name: str = "Impact",
    font_size_preset: str = "medium",
    subtitle_position_y: float = 0.68,
) -> dict:
```

- [ ] **Step 8: 更新现有 `test_compose_hard_uses_filename_quoted_subtitle_filter_on_windows`**

将测试中的调用和断言替换为：

```python
def test_compose_hard_uses_filename_quoted_subtitle_filter_on_windows(monkeypatch, tmp_path):
    captured = {}

    def fake_run(cmd, capture_output=True, text=True):
        captured["cmd"] = cmd
        # 第一次调用是 ffprobe（_get_video_height），第二次是 ffmpeg
        if "ffprobe" in cmd[0]:
            return SimpleNamespace(returncode=0, stdout="1080\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("pipeline.compose.subprocess.run", fake_run)

    video_path = str(tmp_path / "video_soft.mp4")
    output_path = str(tmp_path / "video_hard.mp4")
    windows_srt_path = r"G:\Code\AutoVideoSrt\output\task\subtitle.srt"

    _compose_hard(video_path, windows_srt_path, output_path)

    # 最后一次 subprocess.run 是 ffmpeg
    vf = captured["cmd"][captured["cmd"].index("-vf") + 1]
    assert "G\\:/Code/AutoVideoSrt/output/task/subtitle.srt" in vf
    assert "FontName=Impact" in vf
    assert "FontSize=14" in vf   # medium preset at 1080p
    assert "MarginV=346" in vf   # round(1080*(1-0.68))
    assert "Alignment=2" in vf
```

- [ ] **Step 9: 运行全部 compose 测试**

```bash
pytest tests/test_compose.py -v
```

预期：全部 PASS。

- [ ] **Step 10: 提交**

```bash
git add pipeline/compose.py tests/test_compose.py
git commit -m "feat: compose 支持字体名/字号预设/位置 Y 轴参数，字号分辨率自适应"
```

---

## Task 4：更新任务状态默认值与 start 路由

**Files:**
- Modify: `appcore/task_state.py:136`
- Modify: `web/routes/task.py:285-291`

- [ ] **Step 1: 在 `appcore/task_state.py` 的默认字典里新增三个字段**

找到第 136 行附近的：

```python
        "subtitle_position": "bottom",
```

在其正下方追加：

```python
        "subtitle_font": "Impact",
        "subtitle_size": "medium",
        "subtitle_position_y": 0.68,
```

- [ ] **Step 2: 在 `web/routes/task.py` 的 `start()` 路由里接收新字段**

找到 `start()` 路由中的 `store.update(...)` 调用（约第 285 行），将其扩展为：

```python
    store.update(
        task_id,
        voice_gender=body.get("voice_gender", "male"),
        voice_id=None if body.get("voice_id") in (None, "", "auto") else body.get("voice_id"),
        subtitle_position=body.get("subtitle_position", "bottom"),
        subtitle_font=body.get("subtitle_font", "Impact"),
        subtitle_size=body.get("subtitle_size", "medium"),
        subtitle_position_y=float(body.get("subtitle_position_y", 0.68)),
        interactive_review=_parse_bool(body.get("interactive_review", False)),
    )
```

- [ ] **Step 3: 提交**

```bash
git add appcore/task_state.py web/routes/task.py
git commit -m "feat: 任务状态和启动路由支持字幕字体/字号/位置Y参数"
```

---

## Task 5：更新 runtime.py 透传字幕参数

**Files:**
- Modify: `appcore/runtime.py:474-482`

- [ ] **Step 1: 在 `_step_compose` 中扩展 `compose_video` 调用**

找到 `appcore/runtime.py` 的 `_step_compose` 方法，将 `compose_video` 调用替换为：

```python
        result = compose_video(
            video_path=video_path,
            tts_audio_path=variant_state["tts_audio_path"],
            srt_path=variant_state["srt_path"],
            output_dir=task_dir,
            subtitle_position=task.get("subtitle_position", "bottom"),
            timeline_manifest=variant_state.get("timeline_manifest"),
            variant=variant,
            font_name=task.get("subtitle_font", "Impact"),
            font_size_preset=task.get("subtitle_size", "medium"),
            subtitle_position_y=float(task.get("subtitle_position_y", 0.68)),
        )
```

- [ ] **Step 2: 运行 pipeline runner 测试，确认无回归**

```bash
pytest tests/test_pipeline_runner.py -v
```

预期：全部 PASS（新参数有默认值，不影响现有 mock）。

- [ ] **Step 3: 提交**

```bash
git add appcore/runtime.py
git commit -m "feat: runtime 透传字幕字体/字号/位置参数到 compose_video"
```

---

## Task 6：更新前端配置面板 HTML

**Files:**
- Modify: `web/templates/_task_workbench.html`

- [ ] **Step 1: 替换旧字幕位置 `<select>` 所在的 `.config-item` div**

找到 `_task_workbench.html` 中以下片段（约第 61–69 行）：

```html
    <div class="config-item">
      <label for="subtitlePosition">字幕位置</label>
      <select id="subtitlePosition">
        <option value="bottom" selected>底部（默认）</option>
        <option value="middle">中部</option>
        <option value="top">顶部</option>
      </select>
      <div class="hint">硬字幕与 CapCut 导出都会使用这里的字幕位置。</div>
    </div>
```

将其整体替换为：

```html
    <div class="config-item config-item--subtitle">
      <label>字幕样式</label>
      <div class="subtitle-font-grid" id="subtitleFontGrid">
        <button type="button" class="font-card font-card--selected" data-font="Impact">
          <span class="font-card__preview" style="font-weight:900;letter-spacing:.01em;">Aa</span>
          <span class="font-card__name">Impact</span>
          <span class="font-card__tag">冲击感</span>
        </button>
        <button type="button" class="font-card" data-font="Oswald Bold">
          <span class="font-card__preview" style="font-weight:700;letter-spacing:.03em;">Aa</span>
          <span class="font-card__name">Oswald</span>
          <span class="font-card__tag">现代感</span>
        </button>
        <button type="button" class="font-card" data-font="Bebas Neue">
          <span class="font-card__preview" style="font-weight:900;letter-spacing:.08em;">AA</span>
          <span class="font-card__name">Bebas</span>
          <span class="font-card__tag">全大写</span>
        </button>
        <button type="button" class="font-card" data-font="Montserrat ExtraBold">
          <span class="font-card__preview" style="font-weight:800;">Aa</span>
          <span class="font-card__name">Montserrat</span>
          <span class="font-card__tag">几何感</span>
        </button>
        <button type="button" class="font-card" data-font="Poppins Bold">
          <span class="font-card__preview" style="font-weight:700;">Aa</span>
          <span class="font-card__name">Poppins</span>
          <span class="font-card__tag">友好感</span>
        </button>
        <button type="button" class="font-card" data-font="Anton">
          <span class="font-card__preview" style="font-weight:900;letter-spacing:.02em;">Aa</span>
          <span class="font-card__name">Anton</span>
          <span class="font-card__tag">标题感</span>
        </button>
      </div>
      <div class="subtitle-controls">
        <div class="subtitle-size-group">
          <span class="subtitle-controls__label">字号</span>
          <div class="btn-group" id="subtitleSizeGroup">
            <button type="button" class="btn btn-sm btn-outline" data-size="small">小</button>
            <button type="button" class="btn btn-sm btn-outline btn-outline--active" data-size="medium">中</button>
            <button type="button" class="btn btn-sm btn-outline" data-size="large">大</button>
          </div>
        </div>
        <div class="subtitle-pos-group">
          <span class="subtitle-controls__label">位置</span>
          <button type="button" class="btn btn-sm btn-outline" id="openPhonePickerBtn">
            📱 在手机界面里设置
          </button>
          <span class="subtitle-controls__pos-hint" id="subtitlePosHint">默认（68%）</span>
        </div>
      </div>
      <div class="hint">字体、字号和位置同时应用于硬字幕烧录版与 CapCut 工程包。</div>
    </div>
```

- [ ] **Step 2: 在 `</div>` 关闭 `configPanel` 之前，插入手机位置选择器弹窗**

找到 `_task_workbench.html` 中：

```html
  <div class="review-actions">
    <button class="btn btn-primary" id="startBtn">开始处理</button>
  </div>
</div>
```

在 `</div>` 关闭 `configPanel` 后（即 `<div class="card hidden" id="pipelineCard">` 之前）插入：

```html
<!-- 手机位置选择器弹窗 -->
<div class="modal-backdrop hidden" id="phonePickerBackdrop">
  <div class="phone-picker-modal">
    <div class="phone-picker-modal__header">
      <span>拖动字幕条选择位置</span>
      <button type="button" class="btn btn-ghost btn-sm" id="closePhonePickerBtn">✕ 关闭</button>
    </div>
    <div class="phone-picker-modal__body">
      <div class="phone-frame" id="phoneFrame">
        <!-- TikTok 顶部导航 -->
        <div class="pf-topbar">
          <span class="pf-tab">Following</span>
          <span class="pf-tab pf-tab--active">For You</span>
          <span class="pf-tab">LIVE</span>
        </div>
        <!-- 视频背景 -->
        <div class="pf-videobg"></div>
        <!-- 右侧图标列 -->
        <div class="pf-right-icons">
          <div class="pf-avatar">
            <div class="pf-avatar__circle"></div>
            <div class="pf-avatar__plus">+</div>
          </div>
          <div class="pf-icon-item">
            <div class="pf-icon-circle">♥</div>
            <span class="pf-icon-num">24.6K</span>
          </div>
          <div class="pf-icon-item">
            <div class="pf-icon-circle">💬</div>
            <span class="pf-icon-num">1,203</span>
          </div>
          <div class="pf-icon-item">
            <div class="pf-icon-circle">↗</div>
            <span class="pf-icon-num">Share</span>
          </div>
        </div>
        <!-- 遮挡区提示 -->
        <div class="pf-blocked pf-blocked--bottom">
          <span class="pf-blocked__label">⚠ 左下UI遮挡区</span>
        </div>
        <!-- 左下角信息 -->
        <div class="pf-info">
          <div class="pf-info__creator">@美妆达人小雅</div>
          <div class="pf-info__title">这款精华真的太好用了！</div>
          <div class="pf-info__music">♪ 原声</div>
        </div>
        <!-- 可拖动字幕条 -->
        <div class="pf-subtitle-bar" id="pfSubtitleBar">
          <div class="pf-subtitle-bar__inner">
            <div class="pf-subtitle-bar__text">Get this amazing product now!</div>
            <div class="pf-subtitle-bar__hint">▲▼ 拖动调整</div>
          </div>
        </div>
      </div>
      <div class="phone-picker-modal__info">
        <div>当前位置（距顶）：<strong id="pfPosDisplay">68.0%</strong></div>
        <div id="pfZoneBadge" class="badge badge--info">安全区 ✓</div>
        <button type="button" class="btn btn-primary btn-sm" id="confirmPhonePosBtn">确认此位置</button>
      </div>
    </div>
  </div>
</div>
```

- [ ] **Step 3: 在 `_task_workbench.html` 的 `<style>` 块（或末尾）新增字幕配置样式**

在现有 `<style>` 块末尾（或文件末尾的 `<style>` 标签内）追加：

```css
/* ===== 字幕配置 ===== */
.config-item--subtitle { grid-column: 1 / -1; }

.subtitle-font-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 6px;
  margin-bottom: 10px;
}
.font-card {
  display: flex; flex-direction: column; align-items: center;
  padding: 8px 4px; border: 1px solid var(--border);
  border-radius: var(--radius-md); background: var(--bg);
  cursor: pointer; transition: border-color var(--duration-fast), background var(--duration-fast);
  font-family: inherit;
}
.font-card:hover { border-color: var(--accent); background: var(--accent-subtle); }
.font-card--selected { border: 2px solid var(--accent); background: var(--accent-subtle); }
.font-card__preview { font-size: 20px; line-height: 1; color: var(--fg); }
.font-card__name { font-size: 10px; font-weight: 700; color: var(--fg); margin-top: 2px; }
.font-card__tag { font-size: 9px; color: var(--fg-subtle); }

.subtitle-controls {
  display: flex; gap: 16px; align-items: center; flex-wrap: wrap;
  margin-bottom: 8px;
}
.subtitle-size-group, .subtitle-pos-group {
  display: flex; align-items: center; gap: 6px;
}
.subtitle-controls__label { font-size: 12px; color: var(--fg-muted); white-space: nowrap; }
.subtitle-controls__pos-hint { font-size: 11px; color: var(--fg-subtle); }

.btn-outline { border: 1px solid var(--border-strong); background: transparent; color: var(--fg); border-radius: var(--radius); padding: 4px 10px; cursor: pointer; font-size: 12px; font-family: inherit; }
.btn-outline:hover { background: var(--bg-muted); }
.btn-outline--active { border-color: var(--accent); background: var(--accent-subtle); color: var(--accent); font-weight: 700; }
.btn-group { display: flex; gap: 4px; }

/* ===== 手机弹窗 ===== */
.modal-backdrop {
  position: fixed; inset: 0; background: rgba(0,0,0,.5);
  display: flex; align-items: center; justify-content: center; z-index: 1000;
}
.modal-backdrop.hidden { display: none !important; }
.phone-picker-modal {
  background: #fff; border-radius: 16px; width: 360px; overflow: hidden;
  box-shadow: 0 20px 60px rgba(0,0,0,.3);
}
.phone-picker-modal__header {
  display: flex; justify-content: space-between; align-items: center;
  padding: 14px 16px; border-bottom: 1px solid var(--border);
  font-weight: 700; font-size: 14px;
}
.phone-picker-modal__body { padding: 16px; display: flex; gap: 16px; align-items: flex-start; }
.phone-picker-modal__info { display: flex; flex-direction: column; gap: 10px; font-size: 12px; }

.phone-frame {
  width: 160px; height: 284px; border-radius: 18px;
  border: 3px solid #222; background: #111; position: relative; overflow: hidden;
  cursor: crosshair; flex-shrink: 0; user-select: none;
}
.pf-videobg {
  position: absolute; inset: 0;
  background: linear-gradient(180deg, #2a3a55 0%, #111a2e 60%, #080e1a 100%);
}
.pf-topbar {
  position: absolute; top: 0; left: 0; right: 0; z-index: 10; pointer-events: none;
  display: flex; justify-content: center; gap: 12px; padding: 8px 0 4px;
  background: linear-gradient(180deg, rgba(0,0,0,.5) 0%, transparent 100%);
}
.pf-tab { font-size: 8px; color: rgba(255,255,255,.6); font-weight: 600; }
.pf-tab--active { color: #fff; border-bottom: 1.5px solid #fff; padding-bottom: 1px; }
.pf-right-icons {
  position: absolute; right: 5px; bottom: 60px; z-index: 10; pointer-events: none;
  display: flex; flex-direction: column; align-items: center; gap: 10px;
}
.pf-avatar { position: relative; margin-bottom: 4px; }
.pf-avatar__circle {
  width: 26px; height: 26px; border-radius: 50%;
  background: linear-gradient(135deg,#f9ca24,#f0932b); border: 2px solid #fff;
}
.pf-avatar__plus {
  width: 12px; height: 12px; background: #fe2c55; border: 1.5px solid #fff;
  border-radius: 50%; font-size: 9px; color: #fff; font-weight: 900;
  display: flex; align-items: center; justify-content: center;
  position: absolute; bottom: -4px; left: 50%; transform: translateX(-50%);
}
.pf-icon-item { display: flex; flex-direction: column; align-items: center; gap: 2px; }
.pf-icon-circle {
  width: 26px; height: 26px; border-radius: 50%; background: rgba(255,255,255,.15);
  display: flex; align-items: center; justify-content: center; font-size: 12px; color: #fff;
}
.pf-icon-num { font-size: 7px; color: rgba(255,255,255,.85); font-weight: 600; }
.pf-blocked--bottom {
  position: absolute; bottom: 0; left: 0; right: 0; height: 58px;
  background: rgba(255,80,80,.12); border-top: 1px dashed rgba(255,100,100,.5);
  z-index: 9; pointer-events: none;
}
.pf-blocked__label { font-size: 7px; color: rgba(255,120,120,.9); font-weight: 700; position: absolute; top: 3px; left: 4px; }
.pf-info {
  position: absolute; bottom: 0; left: 0; right: 36px; z-index: 10; pointer-events: none;
  padding: 6px 6px 8px;
  background: linear-gradient(0deg, rgba(0,0,0,.7) 0%, transparent 100%);
}
.pf-info__creator { font-size: 8px; font-weight: 700; color: #fff; margin-bottom: 2px; }
.pf-info__title { font-size: 7px; color: rgba(255,255,255,.85); line-height: 1.3; margin-bottom: 2px; }
.pf-info__music { font-size: 7px; color: rgba(255,255,255,.6); }
.pf-subtitle-bar {
  position: absolute; left: 0; right: 0; z-index: 20; transform: translateY(-50%);
  display: flex; justify-content: center; cursor: ns-resize;
}
.pf-subtitle-bar__inner {
  background: rgba(0,0,0,.65); border: 1.5px solid rgba(255,220,0,.85);
  border-radius: 3px; padding: 2px 8px; text-align: center;
}
.pf-subtitle-bar__text { font-size: 7px; color: #fff; font-weight: 700; text-shadow: 1px 1px 0 #000; white-space: nowrap; }
.pf-subtitle-bar__hint { font-size: 6px; color: rgba(255,220,0,.9); font-weight: 600; }

.badge { display: inline-block; padding: 2px 8px; border-radius: 99px; font-size: 11px; font-weight: 700; }
.badge--info { background: var(--accent-subtle); color: var(--accent); }
.badge--warn { background: var(--warning-bg); color: var(--warning-fg); }
```

- [ ] **Step 4: 提交**

```bash
git add web/templates/_task_workbench.html
git commit -m "feat: 配置面板新增字幕字体卡片/字号按钮组/手机位置选择器弹窗 HTML"
```

---

## Task 7：更新前端 JS 交互逻辑

**Files:**
- Modify: `web/templates/_task_workbench_scripts.html`

- [ ] **Step 1: 找到脚本文件中恢复任务状态到配置面板的代码**

搜索包含以下内容的区域（约第 265 行）：

```js
document.getElementById("subtitlePosition").value = currentTask.subtitle_position || "bottom";
```

将该行替换为：

```js
// 恢复字幕字体
const savedFont = currentTask.subtitle_font || "Impact";
document.querySelectorAll("#subtitleFontGrid .font-card").forEach(card => {
  card.classList.toggle("font-card--selected", card.dataset.font === savedFont);
});
_selectedSubtitleFont = savedFont;

// 恢复字幕字号
const savedSize = currentTask.subtitle_size || "medium";
document.querySelectorAll("#subtitleSizeGroup button").forEach(btn => {
  btn.classList.toggle("btn-outline--active", btn.dataset.size === savedSize);
});
_selectedSubtitleSize = savedSize;

// 恢复字幕位置（优先从 localStorage）
const lsPos = parseFloat(localStorage.getItem("subtitle_position_y") || "");
_subtitlePositionY = isNaN(lsPos) ? (currentTask.subtitle_position_y ?? 0.68) : lsPos;
document.getElementById("subtitlePosHint").textContent = ((_subtitlePositionY * 100).toFixed(1) + "%");
```

- [ ] **Step 2: 找到并更新 start 按钮发送的 payload**

搜索约第 391 行包含：

```js
          subtitle_position: document.getElementById("subtitlePosition").value,
```

将该行替换为：

```js
          subtitle_font: _selectedSubtitleFont,
          subtitle_size: _selectedSubtitleSize,
          subtitle_position_y: _subtitlePositionY,
```

- [ ] **Step 3: 在脚本文件顶部的状态变量区域追加三个新变量**

找到文件中已有的 `let _selectedVoiceId` 变量声明区域，在其附近追加：

```js
let _selectedSubtitleFont = "Impact";
let _selectedSubtitleSize = "medium";
let _subtitlePositionY = parseFloat(localStorage.getItem("subtitle_position_y") || "0.68");
```

- [ ] **Step 4: 在脚本文件末尾（或 DOMContentLoaded 内）添加字体卡片、字号按钮、手机弹窗交互逻辑**

```js
// ===== 字幕字体卡片 =====
document.getElementById("subtitleFontGrid").addEventListener("click", function(e) {
  const card = e.target.closest(".font-card");
  if (!card) return;
  document.querySelectorAll("#subtitleFontGrid .font-card").forEach(c => c.classList.remove("font-card--selected"));
  card.classList.add("font-card--selected");
  _selectedSubtitleFont = card.dataset.font;
});

// ===== 字号按钮组 =====
document.getElementById("subtitleSizeGroup").addEventListener("click", function(e) {
  const btn = e.target.closest("button[data-size]");
  if (!btn) return;
  document.querySelectorAll("#subtitleSizeGroup button").forEach(b => b.classList.remove("btn-outline--active"));
  btn.classList.add("btn-outline--active");
  _selectedSubtitleSize = btn.dataset.size;
});

// ===== 手机位置选择器 =====
(function initPhonePicker() {
  const backdrop = document.getElementById("phonePickerBackdrop");
  const phoneFrame = document.getElementById("phoneFrame");
  const subtitleBar = document.getElementById("pfSubtitleBar");
  const posDisplay = document.getElementById("pfPosDisplay");
  const zoneBadge = document.getElementById("pfZoneBadge");
  const posHint = document.getElementById("subtitlePosHint");

  let _pickerPosPct = _subtitlePositionY * 100;  // 0-100
  let _isDragging = false;
  let _dragStartClientY = 0;
  let _dragStartPct = 0;

  function updateBar(pct) {
    _pickerPosPct = Math.min(95, Math.max(5, pct));
    subtitleBar.style.top = _pickerPosPct + "%";
    posDisplay.textContent = _pickerPosPct.toFixed(1) + "%";
    if (_pickerPosPct > 79) {
      zoneBadge.textContent = "⚠ 遮挡区";
      zoneBadge.className = "badge badge--warn";
    } else {
      zoneBadge.textContent = "安全区 ✓";
      zoneBadge.className = "badge badge--info";
    }
  }

  function pctFromClientY(clientY) {
    const rect = phoneFrame.getBoundingClientRect();
    return ((clientY - rect.top) / rect.height) * 100;
  }

  // 打开弹窗
  document.getElementById("openPhonePickerBtn").addEventListener("click", function() {
    _pickerPosPct = _subtitlePositionY * 100;
    updateBar(_pickerPosPct);
    backdrop.classList.remove("hidden");
  });

  // 关闭弹窗
  document.getElementById("closePhonePickerBtn").addEventListener("click", function() {
    backdrop.classList.add("hidden");
  });
  backdrop.addEventListener("click", function(e) {
    if (e.target === backdrop) backdrop.classList.add("hidden");
  });

  // 点击屏幕跳位
  phoneFrame.addEventListener("click", function(e) {
    if (_isDragging) return;
    if (e.target.closest("#pfSubtitleBar")) return;
    updateBar(pctFromClientY(e.clientY));
  });

  // 拖动字幕条（鼠标）
  subtitleBar.addEventListener("mousedown", function(e) {
    _isDragging = true;
    _dragStartClientY = e.clientY;
    _dragStartPct = _pickerPosPct;
    e.preventDefault();
  });
  document.addEventListener("mousemove", function(e) {
    if (!_isDragging) return;
    const rect = phoneFrame.getBoundingClientRect();
    const delta = ((e.clientY - _dragStartClientY) / rect.height) * 100;
    updateBar(_dragStartPct + delta);
  });
  document.addEventListener("mouseup", function() { _isDragging = false; });

  // 拖动字幕条（触摸）
  subtitleBar.addEventListener("touchstart", function(e) {
    _isDragging = true;
    _dragStartClientY = e.touches[0].clientY;
    _dragStartPct = _pickerPosPct;
    e.preventDefault();
  }, { passive: false });
  document.addEventListener("touchmove", function(e) {
    if (!_isDragging) return;
    const rect = phoneFrame.getBoundingClientRect();
    const delta = ((e.touches[0].clientY - _dragStartClientY) / rect.height) * 100;
    updateBar(_dragStartPct + delta);
  }, { passive: false });
  document.addEventListener("touchend", function() { _isDragging = false; });

  // 确认位置
  document.getElementById("confirmPhonePosBtn").addEventListener("click", function() {
    _subtitlePositionY = _pickerPosPct / 100;
    localStorage.setItem("subtitle_position_y", String(_subtitlePositionY));
    posHint.textContent = (_pickerPosPct.toFixed(1) + "%");
    backdrop.classList.add("hidden");
  });
})();
```

- [ ] **Step 5: 提交**

```bash
git add web/templates/_task_workbench_scripts.html
git commit -m "feat: 字幕配置面板 JS 交互——字体卡片/字号/手机拖拽定位器"
```

---

## Task 8：端到端冒烟测试

- [ ] **Step 1: 启动服务器**

```bash
python main.py
```

- [ ] **Step 2: 打开浏览器，上传一个测试视频，检查配置面板**

预期：
- 配置面板显示 6 个字体卡片，Impact 默认选中
- 字号按钮组显示小/中/大，「中」默认高亮
- 「📱 在手机界面里设置」按钮可用，点击弹出手机弹窗
- 弹窗内字幕条可拖动，进入底部遮挡区时徽标变橙
- 点击「确认此位置」关闭弹窗，位置百分比显示更新

- [ ] **Step 3: 运行全量测试套件，确认无回归**

```bash
pytest tests/ -v --tb=short
```

预期：全部 PASS。

- [ ] **Step 4: 最终提交（如有遗漏的小修复）**

```bash
git add -p
git commit -m "fix: 字幕配置模块端到端冒烟测试修复"
```
