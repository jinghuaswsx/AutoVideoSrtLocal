# en/de/fr 视频翻译：移除软字幕视频 + AI 分析改为可选手动触发 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让英语、德语、法语三种视频翻译主流程不再生成/展示软字幕视频；AI 视频分析从主流程中移除，挪到时间线末尾作为手动触发的附加功能。

**Architecture:** 基类 `PipelineRunner` 加两个类属性 `include_soft_video=False` / `include_analysis_in_main_flow=False`，子类 de/fr 自动继承，v2 显式 override 保持原行为。`compose_video` 加 `with_soft` 开关跳过软字幕合成。analysis 单独通过 `POST /analysis/run` 路由 + 新增的 `run_analysis_only` 后台函数按需执行，不影响 task 整体 status。前端把 analysis step 在模板 DOM 中挪到 export 之后，STEP_ORDER 重排，进度计算排除 analysis，并为 analysis 卡片加「运行 AI 分析」按钮。

**Tech Stack:** Python 3, Flask, Flask-SocketIO, Jinja2, pytest + monkeypatch/unittest.mock。

**Spec:** [docs/superpowers/specs/2026-04-16-de-fr-soft-video-and-optional-analysis-design.md](../specs/2026-04-16-de-fr-soft-video-and-optional-analysis-design.md)

---

## Task 1：`compose_video` 增加 `with_soft` 开关

**Files:**
- Modify: `pipeline/compose.py:40-77`
- Test: `tests/test_compose.py`

- [ ] **Step 1: 在 `tests/test_compose.py` 末尾追加失败测试**

```python
# ---------------------------------------------------------------------------
# compose_video — with_soft 开关
# ---------------------------------------------------------------------------

class TestComposeVideoWithSoftFlag:
    """控制是否生成软字幕视频。"""

    def _patch_all(self, monkeypatch, calls):
        """让 _compose_soft_from_manifest / _compose_soft_legacy / _compose_hard
        都替换为只记录调用的 mock。"""
        from pipeline import compose as compose_mod

        def fake_soft_manifest(*args, **kwargs):
            calls.append(("soft_manifest", args, kwargs))
        def fake_soft_legacy(*args, **kwargs):
            calls.append(("soft_legacy", args, kwargs))
        def fake_hard(*args, **kwargs):
            calls.append(("hard", args, kwargs))

        monkeypatch.setattr(compose_mod, "_compose_soft_from_manifest", fake_soft_manifest)
        monkeypatch.setattr(compose_mod, "_compose_soft_legacy", fake_soft_legacy)
        monkeypatch.setattr(compose_mod, "_compose_hard", fake_hard)
        monkeypatch.setattr(compose_mod, "_get_duration", lambda p: 10.0)

    def test_with_soft_true_generates_both(self, tmp_path, monkeypatch):
        from pipeline.compose import compose_video
        calls = []
        self._patch_all(monkeypatch, calls)

        result = compose_video(
            video_path=str(tmp_path / "in.mp4"),
            tts_audio_path=str(tmp_path / "tts.mp3"),
            srt_path=str(tmp_path / "sub.srt"),
            output_dir=str(tmp_path),
            timeline_manifest={"segments": [{"video_ranges": [{"start": 0, "end": 1}]}],
                               "total_tts_duration": 1.0, "video_consumed_duration": 1.0},
            with_soft=True,
        )

        kinds = [c[0] for c in calls]
        assert "soft_manifest" in kinds
        assert "hard" in kinds
        assert result["soft_video"] and result["soft_video"].endswith("_soft.mp4")
        assert result["hard_video"] and result["hard_video"].endswith("_hard.mp4")

    def test_with_soft_false_skips_soft(self, tmp_path, monkeypatch):
        from pipeline.compose import compose_video
        calls = []
        self._patch_all(monkeypatch, calls)

        result = compose_video(
            video_path=str(tmp_path / "in.mp4"),
            tts_audio_path=str(tmp_path / "tts.mp3"),
            srt_path=str(tmp_path / "sub.srt"),
            output_dir=str(tmp_path),
            timeline_manifest={"segments": [{"video_ranges": [{"start": 0, "end": 1}]}],
                               "total_tts_duration": 1.0, "video_consumed_duration": 1.0},
            with_soft=False,
        )

        kinds = [c[0] for c in calls]
        assert "soft_manifest" not in kinds
        assert "soft_legacy" not in kinds
        assert "hard" in kinds
        assert result["soft_video"] is None
        assert result["hard_video"] and result["hard_video"].endswith("_hard.mp4")

    def test_default_with_soft_is_true(self, tmp_path, monkeypatch):
        """不传 with_soft 参数时默认生成软字幕，保持向后兼容。"""
        from pipeline.compose import compose_video
        calls = []
        self._patch_all(monkeypatch, calls)

        compose_video(
            video_path=str(tmp_path / "in.mp4"),
            tts_audio_path=str(tmp_path / "tts.mp3"),
            srt_path=str(tmp_path / "sub.srt"),
            output_dir=str(tmp_path),
            timeline_manifest={"segments": [{"video_ranges": [{"start": 0, "end": 1}]}],
                               "total_tts_duration": 1.0, "video_consumed_duration": 1.0},
        )

        kinds = [c[0] for c in calls]
        assert "soft_manifest" in kinds

    def test_with_soft_false_without_manifest_still_skips(self, tmp_path, monkeypatch):
        """legacy 分支（无 timeline_manifest）也要尊重 with_soft=False。"""
        from pipeline.compose import compose_video
        calls = []
        self._patch_all(monkeypatch, calls)

        result = compose_video(
            video_path=str(tmp_path / "in.mp4"),
            tts_audio_path=str(tmp_path / "tts.mp3"),
            srt_path=str(tmp_path / "sub.srt"),
            output_dir=str(tmp_path),
            timeline_manifest=None,
            with_soft=False,
        )

        kinds = [c[0] for c in calls]
        assert "soft_manifest" not in kinds
        assert "soft_legacy" not in kinds
        assert "hard" in kinds
        assert result["soft_video"] is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_compose.py::TestComposeVideoWithSoftFlag -v`
Expected: FAIL（当前 `compose_video` 无 `with_soft` 参数，且总是生成 soft）

- [ ] **Step 3: 修改 `pipeline/compose.py:40-77` 的 `compose_video` 函数**

把函数签名和实现改为：

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
    with_soft: bool = True,
) -> dict:
    os.makedirs(output_dir, exist_ok=True)
    base_name = os.path.splitext(os.path.basename(video_path))[0]
    suffix = f".{variant}" if variant else ""

    soft_output = os.path.join(output_dir, f"{base_name}_soft{suffix}.mp4")
    hard_output = os.path.join(output_dir, f"{base_name}_hard{suffix}.mp4")

    # 硬字幕合成依赖软字幕中间产物（把原视频画面和 TTS 音轨烧进一个容器）
    # 因此即使 with_soft=False，仍需要生成 soft 作为中间文件，合成完硬字幕后删除
    if timeline_manifest:
        _compose_soft_from_manifest(video_path, tts_audio_path, timeline_manifest, soft_output)
    else:
        tts_duration = _get_duration(tts_audio_path)
        video_duration = _get_duration(video_path)
        _compose_soft_legacy(video_path, tts_audio_path, min(tts_duration, video_duration), soft_output)

    _compose_hard(
        soft_output, srt_path, hard_output,
        font_name=font_name,
        font_size_preset=font_size_preset,
        subtitle_position_y=subtitle_position_y,
    )

    if not with_soft:
        try:
            os.remove(soft_output)
        except OSError:
            pass
        soft_output = None

    return {
        "soft_video": soft_output,
        "hard_video": hard_output,
        "srt": srt_path,
    }
```

（注意：硬字幕是以软字幕为输入进 _compose_hard 的，所以中间仍需生成一次软字幕。`with_soft=False` 时在返回前把它删掉，返回 `soft_video=None`。）

- [ ] **Step 4: 修正测试 mock — 前三个测试期望"完全跳过 soft"，需要改为"生成后清理"**

由于硬字幕依赖软字幕作为输入，`with_soft=False` 时 soft 仍会被调用一次。修改两个期望"soft_manifest not in kinds"的断言：

```python
    def test_with_soft_false_skips_soft(self, tmp_path, monkeypatch):
        from pipeline.compose import compose_video
        calls = []
        self._patch_all(monkeypatch, calls)

        result = compose_video(
            video_path=str(tmp_path / "in.mp4"),
            tts_audio_path=str(tmp_path / "tts.mp3"),
            srt_path=str(tmp_path / "sub.srt"),
            output_dir=str(tmp_path),
            timeline_manifest={"segments": [{"video_ranges": [{"start": 0, "end": 1}]}],
                               "total_tts_duration": 1.0, "video_consumed_duration": 1.0},
            with_soft=False,
        )

        # 硬字幕仍需 soft 作为中间产物，所以 soft_manifest 会被调用
        kinds = [c[0] for c in calls]
        assert "hard" in kinds
        # 但返回值中 soft_video 为 None（中间文件已清理）
        assert result["soft_video"] is None
        assert result["hard_video"] and result["hard_video"].endswith("_hard.mp4")

    def test_with_soft_false_without_manifest_still_skips(self, tmp_path, monkeypatch):
        """legacy 分支（无 timeline_manifest）也要尊重 with_soft=False。"""
        from pipeline.compose import compose_video
        calls = []
        self._patch_all(monkeypatch, calls)

        result = compose_video(
            video_path=str(tmp_path / "in.mp4"),
            tts_audio_path=str(tmp_path / "tts.mp3"),
            srt_path=str(tmp_path / "sub.srt"),
            output_dir=str(tmp_path),
            timeline_manifest=None,
            with_soft=False,
        )

        kinds = [c[0] for c in calls]
        assert "hard" in kinds
        # 返回值 soft_video 为 None
        assert result["soft_video"] is None
```

- [ ] **Step 5: 运行测试确认全部通过**

Run: `pytest tests/test_compose.py::TestComposeVideoWithSoftFlag -v`
Expected: 4 passed

- [ ] **Step 6: 运行 compose 模块完整测试确认没有破坏其他测试**

Run: `pytest tests/test_compose.py -v`
Expected: 所有测试通过（含原有 20+ 个测试）

- [ ] **Step 7: Commit**

```bash
git add pipeline/compose.py tests/test_compose.py
git commit -m "feat(compose): compose_video 支持 with_soft=False 跳过软字幕产物"
```

---

## Task 2：`PipelineRunner` 新增类属性 + `_step_compose` 使用开关 + `_run` 过滤 analysis

**Files:**
- Modify: `appcore/runtime.py:130`（类定义起始）、`appcore/runtime.py:155-165`（`_run` steps 列表）、`appcore/runtime.py:466-495`（`_step_compose`）

- [ ] **Step 1: 在 `PipelineRunner` 类定义里新增两个类属性**

找到 `appcore/runtime.py:130` 附近的 `class PipelineRunner:`，在类定义内（project_type 定义前或后）添加：

```python
class PipelineRunner:
    # ...现有代码...

    # 是否在 compose 阶段生成软字幕视频（仅 v2 重新 override 为 True 保持原行为）
    include_soft_video: bool = False

    # 是否把 AI 视频分析放在主流程 _run() 的 steps 列表里（v2 override 为 True）
    include_analysis_in_main_flow: bool = False
```

（这两个属性的默认值就是 False，因为英语/德语/法语都要改为 False，v2 会 override 回来。）

- [ ] **Step 2: 修改 `_step_compose` 传入 `with_soft`**

定位 `appcore/runtime.py:466-495` 的 `_step_compose`，把对 `compose_video` 的调用加 `with_soft=self.include_soft_video`：

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
            with_soft=self.include_soft_video,
        )
```

- [ ] **Step 3: 修改 `_run` 按类属性过滤 analysis 步骤**

定位 `appcore/runtime.py:155-165` 的 `_run`，把 steps 列表后增加过滤：

```python
    def _run(self, task_id: str, start_step: str = "extract") -> None:
        task = task_state.get(task_id)
        video_path = task["video_path"]
        task_dir = task["task_dir"]
        steps = [
            ("extract", lambda: self._step_extract(task_id, video_path, task_dir)),
            ("asr", lambda: self._step_asr(task_id, task_dir)),
            ("alignment", lambda: self._step_alignment(task_id, video_path, task_dir)),
            ("translate", lambda: self._step_translate(task_id)),
            ("tts", lambda: self._step_tts(task_id, task_dir)),
            ("subtitle", lambda: self._step_subtitle(task_id, task_dir)),
            ("compose", lambda: self._step_compose(task_id, video_path, task_dir)),
            ("analysis", lambda: self._step_analysis(task_id)),
            ("export", lambda: self._step_export(task_id, video_path, task_dir)),
        ]
        if not self.include_analysis_in_main_flow:
            steps = [s for s in steps if s[0] != "analysis"]

        try:
            # ...（保持剩余代码不变）...
```

- [ ] **Step 4: 新增 `run_analysis_only` 模块级函数**

在 `appcore/runtime.py` 末尾追加一个独立函数：

```python
def run_analysis_only(
    task_id: str,
    runner: "PipelineRunner",
) -> None:
    """单独执行 AI 视频分析步骤，不影响任务整体 status。

    - 所有异常只更新 `steps.analysis` 为 error、记录 step_message；
      绝不触碰 task 整体 status 与 error 字段。
    - `runner` 由调用方构造（带好 EventBus 订阅），方便服务层按
      project_type 使用不同子类。
    """
    try:
        runner._step_analysis(task_id)
    except Exception as exc:
        log.exception("AI 分析执行失败 task_id=%s", task_id)
        try:
            runner._set_step(task_id, "analysis", "error", f"AI 分析失败：{exc}")
        except Exception:
            pass
```

（此函数不注册/注销 active_task — 因为 analysis 不是主流程步骤，任务恢复机制对 analysis 不适用。）

- [ ] **Step 5: 运行现有 runner 相关测试（如果有）**

Run: `pytest tests/ -k "runtime or runner" -v`
Expected: 所有现有测试通过（我们只是加了类属性默认值和一个可选分支，未改变现有调用）

- [ ] **Step 6: Commit**

```bash
git add appcore/runtime.py
git commit -m "feat(runtime): 基类支持 include_soft_video / include_analysis_in_main_flow 开关"
```

---

## Task 3：`PipelineRunnerV2` 显式 override 类属性保持 v2 原行为

**Files:**
- Modify: `appcore/runtime_v2.py:35`（`PipelineRunnerV2` 类定义）

- [ ] **Step 1: 读取当前 runtime_v2.py 类定义位置**

Run: `grep -n "class PipelineRunnerV2" appcore/runtime_v2.py`
Expected: line 35

- [ ] **Step 2: 在 `PipelineRunnerV2` 类体内显式 override**

定位 `appcore/runtime_v2.py:35` 的 `class PipelineRunnerV2(PipelineRunner):`，在类内所有已有内容之前加：

```python
class PipelineRunnerV2(PipelineRunner):
    # v2 流水线保持原行为：生成软字幕视频 + 主流程自动跑 analysis
    include_soft_video = True
    include_analysis_in_main_flow = True

    # ...（保留原有代码）...
```

- [ ] **Step 3: 运行现有 v2 相关测试**

Run: `pytest tests/ -k "v2 or translate_lab" -v`
Expected: 所有测试通过

- [ ] **Step 4: Commit**

```bash
git add appcore/runtime_v2.py
git commit -m "feat(runtime-v2): 显式 override 类属性保持 v2 软字幕+自动 analysis 行为"
```

---

## Task 4：英语流水线服务层新增 `run_analysis`

**Files:**
- Modify: `web/services/pipeline_runner.py`

- [ ] **Step 1: 在 `web/services/pipeline_runner.py` 末尾追加 `run_analysis` 函数**

```python
def run_analysis(task_id: str, user_id: int | None = None):
    """手动触发单次 AI 视频分析，不影响任务整体 status。"""
    from appcore.runtime import run_analysis_only

    bus = EventBus()
    bus.subscribe(_make_socketio_handler(task_id))
    runner = PipelineRunner(bus=bus, user_id=user_id)
    thread = threading.Thread(
        target=run_analysis_only,
        args=(task_id, runner),
        daemon=True,
    )
    thread.start()
```

- [ ] **Step 2: 确认 import 可用（无需新增 import — `PipelineRunner`、`EventBus`、`threading`、`_make_socketio_handler` 都已在文件顶部）**

Run: `python -c "from web.services.pipeline_runner import run_analysis"`
Expected: 无输出，无报错

- [ ] **Step 3: Commit**

```bash
git add web/services/pipeline_runner.py
git commit -m "feat(pipeline-runner): 新增 run_analysis 入口用于手动触发 AI 分析"
```

---

## Task 5：德语流水线服务层新增 `run_analysis`

**Files:**
- Modify: `web/services/de_pipeline_runner.py`

- [ ] **Step 1: 在文件末尾追加**

```python
def run_analysis(task_id: str, user_id: int | None = None):
    """手动触发单次 AI 视频分析，不影响任务整体 status。"""
    from appcore.runtime import run_analysis_only

    bus = EventBus()
    bus.subscribe(_make_socketio_handler(task_id))
    runner = DeTranslateRunner(bus=bus, user_id=user_id)
    thread = threading.Thread(
        target=run_analysis_only,
        args=(task_id, runner),
        daemon=True,
    )
    thread.start()
```

- [ ] **Step 2: Commit**

```bash
git add web/services/de_pipeline_runner.py
git commit -m "feat(de-pipeline-runner): 新增 run_analysis 入口"
```

---

## Task 6：法语流水线服务层新增 `run_analysis`

**Files:**
- Modify: `web/services/fr_pipeline_runner.py`

- [ ] **Step 1: 在文件末尾追加**

```python
def run_analysis(task_id: str, user_id: int | None = None):
    """手动触发单次 AI 视频分析，不影响任务整体 status。"""
    from appcore.runtime import run_analysis_only

    bus = EventBus()
    bus.subscribe(_make_socketio_handler(task_id))
    runner = FrTranslateRunner(bus=bus, user_id=user_id)
    thread = threading.Thread(
        target=run_analysis_only,
        args=(task_id, runner),
        daemon=True,
    )
    thread.start()
```

- [ ] **Step 2: Commit**

```bash
git add web/services/fr_pipeline_runner.py
git commit -m "feat(fr-pipeline-runner): 新增 run_analysis 入口"
```

---

## Task 7：英语路由 `/api/task/<id>/analysis/run` + `RESUMABLE_STEPS` 去掉 analysis

**Files:**
- Modify: `web/routes/task.py:595`（`RESUMABLE_STEPS` 常量）、`web/routes/task.py` 末尾（新增路由）

- [ ] **Step 1: 修改 `RESUMABLE_STEPS` 去掉 `"analysis"`**

定位 `web/routes/task.py:595`：

```python
RESUMABLE_STEPS = ["extract", "asr", "alignment", "translate", "tts", "subtitle", "compose", "analysis", "export"]
```

改为：

```python
RESUMABLE_STEPS = ["extract", "asr", "alignment", "translate", "tts", "subtitle", "compose", "export"]
```

（analysis 不再是主流程步骤，不能通过「从此步继续」恢复；手动触发走新路由。）

- [ ] **Step 2: 在 `web/routes/task.py` 末尾追加 analysis 手动触发路由**

参考该文件其他 `@bp.route` 的写法（login_required、db_query_one 权限校验），在文件末尾追加：

```python
@bp.route("/<task_id>/analysis/run", methods=["POST"])
@login_required
def run_ai_analysis(task_id):
    """手动触发 AI 视频分析（评分 + CSK），不影响任务整体 status。"""
    row = db_query_one(
        "SELECT id FROM projects WHERE id=%s AND user_id=%s AND deleted_at IS NULL",
        (task_id, current_user.id),
    )
    if not row:
        return jsonify({"error": "Task not found"}), 404

    task = store.get(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404

    if (task.get("steps") or {}).get("analysis") == "running":
        return jsonify({"error": "AI 分析正在运行中"}), 409

    user_id = current_user.id if current_user.is_authenticated else None
    pipeline_runner.run_analysis(task_id, user_id=user_id)
    return jsonify({"status": "started"})
```

- [ ] **Step 3: 本地冒烟**

Run: `python -c "from web.routes.task import bp; print([r for r in bp.deferred_functions])"` 或直接启动 web 服务 `python run.py`，访问 `/api/task/<id>/analysis/run`（POST）确认返回 200（或 401/404，非 500）。

Expected: 路由注册成功，无 ImportError

- [ ] **Step 4: Commit**

```bash
git add web/routes/task.py
git commit -m "feat(task-routes): POST /analysis/run 手动触发 + RESUMABLE_STEPS 去掉 analysis"
```

---

## Task 8：德语路由 `/api/de-translate/<id>/analysis/run` + `RESUMABLE_STEPS` 去掉 analysis

**Files:**
- Modify: `web/routes/de_translate.py:260`（`RESUMABLE_STEPS`）、文件末尾（新增路由）

- [ ] **Step 1: 修改 `RESUMABLE_STEPS`**

定位 line 260：

```python
RESUMABLE_STEPS = ["extract", "asr", "alignment", "translate", "tts", "subtitle", "compose", "analysis", "export"]
```

改为：

```python
RESUMABLE_STEPS = ["extract", "asr", "alignment", "translate", "tts", "subtitle", "compose", "export"]
```

- [ ] **Step 2: 在文件末尾追加路由**

参考 [web/routes/de_translate.py](web/routes/de_translate.py) 里其他 `@bp.route("/api/de-translate/<task_id>/...")` 的写法：

```python
@bp.route("/api/de-translate/<task_id>/analysis/run", methods=["POST"])
@login_required
def run_ai_analysis(task_id):
    """手动触发德语项目 AI 视频分析，不影响任务整体 status。"""
    row = db_query_one(
        "SELECT id FROM projects WHERE id=%s AND user_id=%s AND deleted_at IS NULL",
        (task_id, current_user.id),
    )
    if not row:
        return jsonify({"error": "Task not found"}), 404

    task = store.get(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404

    if (task.get("steps") or {}).get("analysis") == "running":
        return jsonify({"error": "AI 分析正在运行中"}), 409

    de_pipeline_runner.run_analysis(task_id, user_id=current_user.id)
    return jsonify({"status": "started"})
```

（`store`、`db_query_one`、`de_pipeline_runner`、`login_required` 等已在文件顶部 import；若 lint 报未 import，按既有 import 风格补一行。）

- [ ] **Step 3: Commit**

```bash
git add web/routes/de_translate.py
git commit -m "feat(de-translate): POST /analysis/run 手动触发 + RESUMABLE_STEPS 去掉 analysis"
```

---

## Task 9：法语路由 `/api/fr-translate/<id>/analysis/run` + `RESUMABLE_STEPS` 去掉 analysis

**Files:**
- Modify: `web/routes/fr_translate.py`（`RESUMABLE_STEPS` + 末尾新增路由）

- [ ] **Step 1: 定位并修改 `RESUMABLE_STEPS`**

Run: `grep -n "RESUMABLE_STEPS" web/routes/fr_translate.py`
找到常量定义行，改为：

```python
RESUMABLE_STEPS = ["extract", "asr", "alignment", "translate", "tts", "subtitle", "compose", "export"]
```

- [ ] **Step 2: 在文件末尾追加路由**

```python
@bp.route("/api/fr-translate/<task_id>/analysis/run", methods=["POST"])
@login_required
def run_ai_analysis(task_id):
    """手动触发法语项目 AI 视频分析，不影响任务整体 status。"""
    row = db_query_one(
        "SELECT id FROM projects WHERE id=%s AND user_id=%s AND deleted_at IS NULL",
        (task_id, current_user.id),
    )
    if not row:
        return jsonify({"error": "Task not found"}), 404

    task = store.get(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404

    if (task.get("steps") or {}).get("analysis") == "running":
        return jsonify({"error": "AI 分析正在运行中"}), 409

    fr_pipeline_runner.run_analysis(task_id, user_id=current_user.id)
    return jsonify({"status": "started"})
```

- [ ] **Step 3: Commit**

```bash
git add web/routes/fr_translate.py
git commit -m "feat(fr-translate): POST /analysis/run 手动触发 + RESUMABLE_STEPS 去掉 analysis"
```

---

## Task 10：模板 `_task_workbench.html` — analysis 挪到 export 之后 + 加按钮区

**Files:**
- Modify: `web/templates/_task_workbench.html:525-563`（step-compose → step-analysis → step-export 三块）

- [ ] **Step 1: 定位当前三块 DOM**

当前结构（简化后）：

```html
<div class="step" id="step-compose">  <!-- 编号 7 -->
  ...
</div>
<div class="step" id="step-analysis">  <!-- 编号 8 -->
  ...（含 resume-btn, step-name "AI 视频分析"）
</div>
<div class="step" id="step-export">  <!-- 编号 9 -->
  ...
</div>
```

- [ ] **Step 2: 把 step-analysis 块整段挪到 step-export 之后，并调整编号**

替换顺序为 `compose → export → analysis`，并把 step-export 编号从 `9` 改为 `8`，step-analysis 编号从 `8` 改为 `9`；同时在 step-analysis 里用「运行 AI 分析」按钮替换「从此步继续」按钮（analysis 不再走 resume 流程），step-name 右侧加按钮：

```html
<div class="step" id="step-compose">
  <div class="step-main">
    <div class="step-icon" id="icon-compose">7</div>
    <div style="flex:1">
      <div class="step-name-row">
        <span class="step-name">视频合成</span>
        <button class="resume-btn hidden" id="resume-compose" data-step="compose">从此步继续</button>
      </div>
      <div class="step-msg" id="msg-compose">等待中...</div>
    </div>
  </div>
  <div class="step-preview" id="preview-compose"></div>
</div>
<div class="step" id="step-export">
  <div class="step-main">
    <div class="step-icon" id="icon-export">8</div>
    <div style="flex:1">
      <div class="step-name-row">
        <span class="step-name">CapCut 导出</span>
        <button class="resume-btn hidden" id="resume-export" data-step="export">从此步继续</button>
      </div>
      <div class="step-msg" id="msg-export">等待中...</div>
    </div>
  </div>
  <div class="step-preview" id="preview-export"></div>
</div>
<div class="step" id="step-analysis">
  <div class="step-main">
    <div class="step-icon" id="icon-analysis">9</div>
    <div style="flex:1">
      <div class="step-name-row">
        <span class="step-name">AI 视频分析</span>
        <button class="btn btn-primary btn-sm" id="runAnalysisBtn">运行 AI 分析</button>
      </div>
      <div class="step-msg" id="msg-analysis">可选附加分析 · 点击按钮手动触发</div>
    </div>
  </div>
  <div class="step-preview" id="preview-analysis"></div>
</div>
```

（注意：`resume-btn` 对 analysis 已删除，改为 `runAnalysisBtn`；它的显示/隐藏在 Task 11 里的 JS 里按步骤状态决定。）

- [ ] **Step 3: 本地冒烟 — 起服务打开 detail 页**

不启动 ffmpeg，只确认页面可打开且 analysis 卡片在时间线最末尾。Run: `python run.py`（测试环境）。在浏览器里随便打开一个 en/de/fr 项目详情页，目测：
- 卡片顺序是 1-7 compose → 8 export → 9 analysis
- analysis 卡片上有「运行 AI 分析」按钮（样式尚未通过 JS 控制时也应是可见的主按钮）
- 点击按钮暂时会报 404/handler 未绑定（Task 11 接）

- [ ] **Step 4: Commit**

```bash
git add web/templates/_task_workbench.html
git commit -m "feat(workbench): AI 视频分析卡片挪到 export 之后编号 9 + 增加运行按钮"
```

---

## Task 11：`_task_workbench_scripts.html` — STEP_ORDER 重排 + analysis 特殊状态机 + 过滤 soft_video + 绑定按钮 + 进度排除

**Files:**
- Modify: `web/templates/_task_workbench_scripts.html`

  - line 3：`STEP_ORDER` 定义
  - line 314-326：`stepLabel` 函数
  - line 546-584：`renderStepMessages` / `renderStepPreviews`
  - line 888-910：`updateStartButtonState` / `updateResumeButtons`
  - line 938-947：`renderResultPanelFromTask`（删 `downloads.soft` 分支）

- [ ] **Step 1: `STEP_ORDER` 调整顺序 + 新增 `MAIN_STEPS`**

定位 line 3：

```javascript
  const STEP_ORDER = ["extract", "asr", "alignment", "translate", "tts", "subtitle", "compose", "analysis", "export"];
```

改为（analysis 放最后 + 主流程列表单独开）：

```javascript
  const STEP_ORDER = ["extract", "asr", "alignment", "translate", "tts", "subtitle", "compose", "export", "analysis"];
  const MAIN_STEPS = ["extract", "asr", "alignment", "translate", "tts", "subtitle", "compose", "export"];
```

- [ ] **Step 2: `renderStepMessages` 给 analysis 步骤特殊 idle 占位**

定位 line 546-558 的 `renderStepMessages`：

```javascript
  function renderStepMessages() {
    STEP_ORDER.forEach(step => {
      const stepEl = document.getElementById(`step-${step}`);
      const iconEl = document.getElementById(`icon-${step}`);
      const msgEl = document.getElementById(`msg-${step}`);
      let status = currentTask?.steps?.[step] || "pending";
      if (status === "running" && currentTask?.status === "error") status = "error";
      const message = currentTask?.step_messages?.[step] || placeholderText(status);
      stepEl.className = `step ${status}`;
      iconEl.className = `step-icon ${status}`;
      iconEl.innerHTML = status === "running" ? '<span class="spinner"></span>' : (stepIcons[status] || "·");
      msgEl.textContent = message;
    });
  }
```

改为（analysis 步骤的 `pending` 视为 `idle`，不受主流程错误影响）：

```javascript
  function renderStepMessages() {
    STEP_ORDER.forEach(step => {
      const stepEl = document.getElementById(`step-${step}`);
      const iconEl = document.getElementById(`icon-${step}`);
      const msgEl = document.getElementById(`msg-${step}`);
      let status = currentTask?.steps?.[step] || "pending";
      if (step === "analysis") {
        // AI 分析是手动触发的附加步骤，不跟随主流程错误
        if (status === "pending") status = "idle";
      } else {
        if (status === "running" && currentTask?.status === "error") status = "error";
      }
      const fallback = step === "analysis" && status === "idle"
        ? "可选附加分析 · 点击按钮手动触发"
        : placeholderText(status);
      const message = currentTask?.step_messages?.[step] || fallback;
      stepEl.className = `step ${status}`;
      iconEl.className = `step-icon ${status}`;
      iconEl.innerHTML = status === "running" ? '<span class="spinner"></span>' : (stepIcons[status] || "·");
      msgEl.textContent = message;
    });
    updateAnalysisButton();
  }
```

- [ ] **Step 3: 新增 `updateAnalysisButton()` 函数控制按钮状态**

在 `renderStepMessages` 下方新增：

```javascript
  function updateAnalysisButton() {
    const btn = document.getElementById("runAnalysisBtn");
    if (!btn) return;
    const status = currentTask?.steps?.analysis || "pending";
    if (status === "running") {
      btn.disabled = true;
      btn.textContent = "AI 分析中...";
      btn.classList.remove("hidden");
    } else if (status === "done") {
      btn.disabled = false;
      btn.textContent = "重新分析";
      btn.classList.remove("hidden");
    } else if (status === "error") {
      btn.disabled = false;
      btn.textContent = "重新分析";
      btn.classList.remove("hidden");
    } else {
      // idle / pending
      btn.disabled = false;
      btn.textContent = "运行 AI 分析";
      btn.classList.remove("hidden");
    }
  }
```

- [ ] **Step 4: 绑定按钮点击事件，页面加载后绑一次**

在 DOMContentLoaded 或脚本最后（参考其他按钮绑定如 `document.querySelectorAll(".resume-btn")...` 的风格）新增绑定。找到一段现有绑定代码（如 line 912 附近 `.resume-btn` 的 `forEach`），在其下方追加：

```javascript
  const runAnalysisBtn = document.getElementById("runAnalysisBtn");
  if (runAnalysisBtn) {
    runAnalysisBtn.addEventListener("click", async () => {
      if (!taskId) return;
      const original = runAnalysisBtn.textContent;
      runAnalysisBtn.disabled = true;
      runAnalysisBtn.textContent = "启动中...";
      try {
        const res = await fetch(_apiUrl("/analysis/run"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
        });
        if (!res.ok) {
          const data = await res.json().catch(() => ({}));
          showError(data.error || "启动 AI 分析失败");
          runAnalysisBtn.disabled = false;
          runAnalysisBtn.textContent = original;
          return;
        }
        socket.emit("join_task", { task_id: taskId });
        scheduleRefreshTaskState(200);
      } catch (err) {
        showError(err.message || "启动 AI 分析失败");
        runAnalysisBtn.disabled = false;
        runAnalysisBtn.textContent = original;
      }
    });
  }
```

（`_apiUrl`、`taskId`、`socket`、`scheduleRefreshTaskState`、`showError` 均已在文件中定义。）

- [ ] **Step 5: `updateStartButtonState` 改用 MAIN_STEPS**

定位 line 888：

```javascript
    const hasStarted = STEP_ORDER.some(step => (currentTask.steps?.[step] || "pending") !== "pending");
    const isWaiting = STEP_ORDER.some(step => currentTask.steps?.[step] === "waiting");
    const isRunning = STEP_ORDER.some(step => currentTask.steps?.[step] === "running");
```

改为（analysis 不计入开始/运行判断，避免 analysis idle 被当作"未开始"而误开放开始按钮）：

```javascript
    const hasStarted = MAIN_STEPS.some(step => (currentTask.steps?.[step] || "pending") !== "pending");
    const isWaiting = MAIN_STEPS.some(step => currentTask.steps?.[step] === "waiting");
    const isRunning = MAIN_STEPS.some(step => currentTask.steps?.[step] === "running");
```

- [ ] **Step 6: `updateResumeButtons` 跳过 analysis**

定位 line 897：

```javascript
  function updateResumeButtons(task) {
    const steps = task.steps || {};
    const isRunning = task.status === "running";
    STEP_ORDER.forEach(step => {
      const btn = document.getElementById(`resume-${step}`);
      if (!btn) return;
      ...
    });
  }
```

改为使用 `MAIN_STEPS`（analysis 步骤没有 resume-btn，loop 多一次不出错，但语义上 analysis 不参与 resume）：

```javascript
  function updateResumeButtons(task) {
    const steps = task.steps || {};
    const isRunning = task.status === "running";
    MAIN_STEPS.forEach(step => {
      const btn = document.getElementById(`resume-${step}`);
      if (!btn) return;
      const stepStatus = steps[step];
      if (!isRunning && stepStatus !== "pending") {
        btn.classList.remove("hidden");
      } else {
        btn.classList.add("hidden");
      }
    });
  }
```

- [ ] **Step 7: 删除 `downloads.soft` 逻辑**

定位 line 940-942：

```javascript
    if (currentTask.result?.hard_video) downloads.hard = _apiUrl('/download/hard');
    if (currentTask.result?.soft_video) downloads.soft = _apiUrl('/download/soft');
    if (currentTask.srt_path) downloads.srt = _apiUrl('/download/srt');
```

把 soft 那行删掉：

```javascript
    if (currentTask.result?.hard_video) downloads.hard = _apiUrl('/download/hard');
    if (currentTask.srt_path) downloads.srt = _apiUrl('/download/srt');
```

- [ ] **Step 8: 防御式过滤老项目 compose artifact 里的 soft_video 条目**

定位 line 561 的 `renderStepPreviews`，在 `html = artifact.items.map(item => renderPreviewItem(item)).join("");` 这一行之前，过滤 items：

```javascript
      } else if (!artifact.items || !artifact.items.length) {
        html = `<div class="preview-placeholder">${placeholderText(status)}</div>`;
      } else {
        // 老项目的 compose artifact 里可能还留着 soft_video 条目 → 过滤
        const items = (step === "compose")
          ? artifact.items.filter(it => it.artifact !== "soft_video")
          : artifact.items;
        html = items.map(item => renderPreviewItem(item)).join("");
      }
```

- [ ] **Step 9: 本地冒烟 — 新建一个项目打开看效果**

启动测试服务 `python run.py`（或 9999 端口测试环境），打开一个 en 项目：
- compose 步骤 preview 里只剩硬字幕视频（或者在旧项目上不再显示软字幕）
- analysis 卡片在时间线最末尾
- analysis 卡片「运行 AI 分析」按钮可见
- 开始任务后，主流程跑完 compose → export → task status 为 done，但 analysis 仍为 idle
- 点击「运行 AI 分析」，按钮变 "AI 分析中..."，完成后变 "重新分析"，预览区展示评分/CSK 结果
- 下载栏不再出现软字幕下载

- [ ] **Step 10: Commit**

```bash
git add web/templates/_task_workbench_scripts.html
git commit -m "feat(workbench-scripts): STEP_ORDER 重排 + analysis 手动触发按钮 + 过滤 soft_video"
```

---

## Task 12：全链路手工 QA

**Files:** 无新文件

- [ ] **Step 1: 新建一个英语项目，走完主流程**

- 上传视频 → 开始 → 主流程 8 步按顺序 running→done
- compose 卡片 preview 里只有一个硬字幕视频（无软字幕）
- task 整体 status 变为 `done`，analysis 此时为 idle，按钮可点
- 点击「运行 AI 分析」→ 状态变 running → 评分和 CSK 展示
- 人为制造失败（关掉 gemini/api key）重新点按钮 → step 变 error，task status 仍 done
- 检查 task_dir 里没有 `*_soft.mp4` 文件

- [ ] **Step 2: 重复 Step 1 但用德语项目（de_translate）**

- [ ] **Step 3: 重复 Step 1 但用法语项目（fr_translate）**

- [ ] **Step 4: 打开一个老的 en/de/fr 项目（本次改动前创建的，artifact 有 soft_video）**

- compose 卡片 preview 只展示硬字幕视频（软字幕已被前端过滤）
- 下载栏没有「软字幕视频」选项
- analysis 卡片：如果老项目 `steps.analysis === "done"`，预览区正常展示结果，按钮显示"重新分析"；如果为 undefined/pending，按钮显示"运行 AI 分析"

- [ ] **Step 5: translate_lab（v2）项目回归**

- 上传并跑完 → compose 应同时生成软+硬字幕（v2 未改变行为）
- 看 v2 专属 detail 模板（不是 `_task_workbench.html`），不受本次改动影响

- [ ] **Step 6: 集合测试**

Run: `pytest tests/ -x -q`
Expected: 全部通过

- [ ] **Step 7: 若 Step 1-6 全通过，合并/发布**

等用户确认，然后按 CLAUDE.md 约定走"发布"流程。

---

## 落地后的行为对照表

| 项 | 改前 | 改后（en/de/fr） | v2（translate_lab） |
|---|---|---|---|
| compose 产出 | soft + hard mp4 | 仅 hard mp4 | soft + hard（不变） |
| 主流程步骤数 | 9 | 8 | 9（不变） |
| AI 分析触发 | 主流程自动 | 用户手动点按钮 | 自动（不变） |
| AI 分析失败影响 status？| 否（本就没）| 否 | 否（不变） |
| 时间线 analysis 卡片位置 | compose 与 export 之间 | export 之后（最末尾）| 按模板实际决定 |
| `/download/soft` 路由 | 可用 | 可用但 result.soft_video=None → 404 | 可用（不变） |
