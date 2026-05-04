# TTS 变速短路收敛 + AI 质量评估 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 多语言视频翻译 TTS Duration Loop 在某轮音频落入 `[0.9v, 1.1v]` 但不在 `[v-1, v+2]` 时，立刻用 ElevenLabs `voice_settings.speed` 重生成一遍音频"短路"收敛，并对每次变速产物用 OpenRouter Gemini 3 Flash 做双轨对比 AI 评分，写库供 admin 评估是否上线。

**Architecture:** 在 [appcore/runtime/_pipeline_runner.py:_run_tts_duration_loop](../../appcore/runtime/_pipeline_runner.py) 每轮 measure 之后插一个分支：①已收敛走原路径不动；②落入 ±10% 走"变速 + 同步评估 + 终结"新路径；③不在 ±10% 走原 rewrite 下一轮。变速重生成在 [pipeline/tts.py](../../pipeline/tts.py) 加 `regenerate_full_audio_with_speed`；AI 评估封装在新模块 `appcore/tts_speedup_eval.py`，走 `llm_client.invoke_generate(use_case="video_translate.tts_speedup_quality_review")` + 注册表 + DB binding 接现有 LLM 统一调用链路。结果存 MySQL 新表 `tts_speedup_evaluations`，admin 通过 `/admin/tts-speedup-evaluations` 查询页评估上线决策。

**Tech Stack:** Python 3 / Flask / MySQL / pytest / ElevenLabs SDK / OpenRouter via `appcore.llm_client` / Jinja2 + Ocean Blue 设计系统。

---

## 关键背景信息（实现前必读）

1. **数据库是 MySQL**，不是 PostgreSQL。语法用 `BIGINT AUTO_INCREMENT PRIMARY KEY / JSON / DATETIME / CURRENT_TIMESTAMP`，迁移文件按 `YYYY_MM_DD_<name>.sql` 命名，由 `appcore.db` 启动时自动 apply 并登记 `schema_migrations`。**不要手跑 SQL**，commit 后由 systemd 启动器执行。
2. **`_uc()` 是 8 个 positional 参数**：`(code, module, label, desc, provider, model, service, units_type)`。不是 keyword。参考 [appcore/llm_use_cases.py](../../appcore/llm_use_cases.py)。
3. **`llm_client.invoke_generate` 没有 `timeout` 参数**。要在调用方用 `concurrent.futures.ThreadPoolExecutor` 包超时，参考下方 Task 5。
4. **DB 访问统一走 [appcore/db.py](../../appcore/db.py)** 的 `query / query_one / execute / get_conn`。MySQL 占位符是 `%s`，不是 `?`。
5. **本项目的设计系统是 Ocean Blue**（hue 200-240，零紫色），见 worktree 根 `CLAUDE.md` "Frontend Design System"。
6. **测试基线已知有 pre-existing 失败**（参考 `docs/superpowers/notes/2026-04-21-pytest-baseline-failures.md`），跑测试时只关心新增/触及的用例。
7. **Worktree 路径**：所有改动在 `.worktrees/tts-speedup-shortcut/` 进行。git 命令带 `-C` 或在该路径下执行。
8. **commit message 用中文 + Co-Authored-By footer**（参考 master 历史 commit 风格）。

---

## Task 1: 数据库迁移 + schema.sql

**Files:**
- Create: `db/migrations/2026_05_04_tts_speedup_evaluations.sql`
- Modify: `db/schema.sql`（在文件末尾追加同样表声明，与迁移保持一致）

- [ ] **Step 1: 创建迁移文件**

写入 `db/migrations/2026_05_04_tts_speedup_evaluations.sql`：

```sql
-- TTS 变速短路收敛 AI 评估记录
-- 每次 ElevenLabs 变速短路（duration loop ±10% 分支）跑一行；
-- 同 task_id + round_index 唯一，重新评估只更新该行。
CREATE TABLE IF NOT EXISTS tts_speedup_evaluations (
  id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
  task_id             VARCHAR(64) NOT NULL,
  round_index         INT NOT NULL,
  language            VARCHAR(16) NOT NULL,
  video_duration      DECIMAL(10,3) NOT NULL,
  audio_pre_duration  DECIMAL(10,3) NOT NULL,
  audio_post_duration DECIMAL(10,3) NOT NULL,
  speed_ratio         DECIMAL(6,4) NOT NULL,
  hit_final_range     TINYINT(1) NOT NULL,

  -- AI 五维评分（评估失败时为 NULL）
  score_naturalness     TINYINT,
  score_pacing          TINYINT,
  score_timbre          TINYINT,
  score_intelligibility TINYINT,
  score_overall         TINYINT,
  summary_text          TEXT,
  flags_json            JSON,

  -- 模型信息 + 计费
  model_provider     VARCHAR(64),
  model_id           VARCHAR(128),
  llm_input_tokens   INT,
  llm_output_tokens  INT,
  llm_cost_usd       DECIMAL(10,6),

  -- 评估状态
  status             VARCHAR(16) NOT NULL DEFAULT 'pending',  -- pending | ok | failed
  error_text         TEXT,

  -- 音频路径（task_dir 相对路径，UI 通过现有 artifact 路由读）
  audio_pre_path     VARCHAR(255) NOT NULL,
  audio_post_path    VARCHAR(255) NOT NULL,

  created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  evaluated_at DATETIME,

  UNIQUE KEY uk_task_round (task_id, round_index),
  KEY idx_created (created_at),
  KEY idx_lang_overall (language, score_overall),
  KEY idx_status (status, created_at)
);
```

- [ ] **Step 2: schema.sql 同步**

打开 `db/schema.sql`，找到表声明集中区（末尾或按字母顺序），把上述 `CREATE TABLE IF NOT EXISTS tts_speedup_evaluations (...);` 完整复制粘贴进去（保持 MySQL 语法不变）。如果 schema.sql 是按模块分块的，找一个最合适的语义位置（建议靠近 `tts_generation_stats`、`video_ai_reviews` 等同类）。

- [ ] **Step 3: 验证迁移文件能解析**

Run（在 worktree 根目录，PowerShell）：
```powershell
python -c "from appcore.db import get_conn; c=get_conn(); cur=c.cursor(); cur.execute(open('db/migrations/2026_05_04_tts_speedup_evaluations.sql','r',encoding='utf-8').read()); cur.execute('DESCRIBE tts_speedup_evaluations'); print([r for r in cur.fetchall()][:5])"
```
Expected: 输出 `tts_speedup_evaluations` 的前 5 列描述（id / task_id / round_index / language / video_duration），不报错。

如果你的本地 dev DB 已经有同名表（之前测试过），用：
```powershell
python -c "from appcore.db import get_conn; c=get_conn(); c.cursor().execute('DROP TABLE IF EXISTS tts_speedup_evaluations'); c.commit()"
```
先清掉再跑上面。

- [ ] **Step 4: Commit**

```bash
git -C .worktrees/tts-speedup-shortcut add db/migrations/2026_05_04_tts_speedup_evaluations.sql db/schema.sql
git -C .worktrees/tts-speedup-shortcut commit -m "$(cat <<'EOF'
feat(db): 新增 tts_speedup_evaluations 表用于 TTS 变速短路 AI 评估

每次 ElevenLabs 变速短路触发都写一行记录，含变速参数、AI 五维评分、模型与计费信息，admin 跨任务查询用于决定是否上线变速短路。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: 注册 LLM use_case `video_translate.tts_speedup_quality_review`

**Files:**
- Modify: `appcore/llm_use_cases.py`
- Test: `tests/test_llm_use_cases_registry.py`（已有，扩展一条断言）

- [ ] **Step 1: 写失败测试**

打开 `tests/test_llm_use_cases_registry.py`，先看现有测试结构。然后在末尾追加：

```python
def test_tts_speedup_quality_review_use_case_registered():
    from appcore.llm_use_cases import USE_CASES, get_use_case

    assert "video_translate.tts_speedup_quality_review" in USE_CASES, (
        "video_translate.tts_speedup_quality_review use_case 未注册"
    )
    uc = get_use_case("video_translate.tts_speedup_quality_review")
    assert uc["module"] == "video_translate"
    assert uc["default_provider"] == "openrouter"
    assert uc["default_model"] == "google/gemini-3-flash-preview"
    assert uc["usage_log_service"] == "openrouter"
    assert uc["units_type"] == "tokens"
    assert uc["label"]
    assert uc["description"]
```

- [ ] **Step 2: 跑测试看失败**

Run:
```powershell
pytest tests/test_llm_use_cases_registry.py::test_tts_speedup_quality_review_use_case_registered -v
```
Expected: FAIL — `KeyError: 'unknown use_case: video_translate.tts_speedup_quality_review'` 或类似。

- [ ] **Step 3: 注册 use_case**

打开 `appcore/llm_use_cases.py`，找到 `USE_CASES` 字典里 `video_translate` 模块的最后一条（搜 `"video_translate.tts_language_check"` 那块），在合理位置（例如紧跟 `video_translate.tts_language_check` 之后）插入：

```python
    "video_translate.tts_speedup_quality_review": _uc(
        "video_translate.tts_speedup_quality_review",
        "video_translate",
        "TTS 变速短路质量评估",
        "对 ElevenLabs 变速短路产物（变速前+变速后双轨）做多模态对比，输出 5 维质量分",
        "openrouter",
        "google/gemini-3-flash-preview",
        "openrouter",
        "tokens",
    ),
```

注意是 **8 个 positional 参数**，按 `(code, module, label, desc, provider, model, service, units_type)` 顺序传。不要用 keyword。

- [ ] **Step 4: 跑测试看通过**

Run:
```powershell
pytest tests/test_llm_use_cases_registry.py::test_tts_speedup_quality_review_use_case_registered -v
```
Expected: PASS。

也跑一遍整个文件确认没破坏其他注册项：
```powershell
pytest tests/test_llm_use_cases_registry.py -v
```
Expected: 所有测试 PASS。

- [ ] **Step 5: Commit**

```bash
git -C .worktrees/tts-speedup-shortcut add appcore/llm_use_cases.py tests/test_llm_use_cases_registry.py
git -C .worktrees/tts-speedup-shortcut commit -m "$(cat <<'EOF'
feat(llm): 注册 video_translate.tts_speedup_quality_review use_case

OpenRouter + google/gemini-3-flash-preview 默认绑定，admin 可在
/settings?tab=bindings 覆盖。units_type=tokens，usage_log 走 openrouter。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: 加纯函数 `_in_speedup_window` / `_speedup_ratio` 到 `_helpers.py`

**Files:**
- Modify: `appcore/runtime/_helpers.py`（在 `_tts_final_target_range` 附近追加两个纯函数）
- Modify: `appcore/runtime/__init__.py`（re-export 新增函数）
- Test: `tests/test_tts_duration_loop.py`（在 `TestComputeNextTarget` 之后追加 `TestSpeedupWindow`）

- [ ] **Step 1: 写失败测试**

打开 `tests/test_tts_duration_loop.py`，在 `class TestComputeNextTarget:` 之后、`import os` 之前加：

```python
class TestSpeedupWindow:
    """变速短路触发条件 + speed ratio 计算的纯函数测试。"""

    def test_in_window_true_for_audio_outside_final_but_within_10pct(self):
        from appcore.runtime import _in_speedup_window
        # video=60, final=[59,62], stage1=[54,66]
        # 64s: 在 stage1 但不在 final → True
        assert _in_speedup_window(audio_duration=64.0, video_duration=60.0) is True

    def test_in_window_false_when_audio_already_in_final(self):
        from appcore.runtime import _in_speedup_window
        # 60.5s 在 final [59,62] → False（已收敛，不应触发变速）
        assert _in_speedup_window(audio_duration=60.5, video_duration=60.0) is False

    def test_in_window_false_when_audio_outside_10pct(self):
        from appcore.runtime import _in_speedup_window
        # 70s > 1.1*60=66 → False
        assert _in_speedup_window(audio_duration=70.0, video_duration=60.0) is False
        # 50s < 0.9*60=54 → False
        assert _in_speedup_window(audio_duration=50.0, video_duration=60.0) is False

    def test_in_window_false_when_durations_invalid(self):
        from appcore.runtime import _in_speedup_window
        assert _in_speedup_window(audio_duration=0.0, video_duration=60.0) is False
        assert _in_speedup_window(audio_duration=60.0, video_duration=0.0) is False

    def test_speedup_ratio_basic(self):
        from appcore.runtime import _speedup_ratio
        # audio=64, video=60 → speed=64/60=1.0667（音频要变快、变短）
        assert _speedup_ratio(64.0, 60.0) == pytest.approx(1.0667, abs=1e-4)

    def test_speedup_ratio_clamps_to_elevenlabs_legal_range(self):
        from appcore.runtime import _speedup_ratio
        # ElevenLabs 合法 speed ∈ [0.7, 1.2]
        # 极端情况 audio=120, video=60 → ratio=2.0，应被 clamp 到 1.2
        assert _speedup_ratio(120.0, 60.0) == 1.2
        # audio=30, video=60 → ratio=0.5，应被 clamp 到 0.7
        assert _speedup_ratio(30.0, 60.0) == 0.7
```

- [ ] **Step 2: 跑测试看失败**

Run:
```powershell
pytest tests/test_tts_duration_loop.py::TestSpeedupWindow -v
```
Expected: 全部 FAIL — `ImportError: cannot import name '_in_speedup_window' from 'appcore.runtime'`。

- [ ] **Step 3: 加纯函数**

打开 `appcore/runtime/_helpers.py`，找到 `_tts_final_target_range` 函数（约第 316 行），在它之后插入：

```python
def _in_speedup_window(*, audio_duration: float, video_duration: float) -> bool:
    """判断音频时长是否落入"变速短路"触发窗口：
    在 stage-1 区间 [0.9v, 1.1v] 内，但不在最终收敛区间 [v-1, v+2] 内。

    满足条件时，duration loop 应跳过下一轮 rewrite，改用 ElevenLabs voice_settings.speed
    重生成一遍音频试图直接收敛到 [v-1, v+2]。
    """
    if not audio_duration or not video_duration or audio_duration <= 0 or video_duration <= 0:
        return False
    final_lo, final_hi = _tts_final_target_range(video_duration)
    stage1_lo = video_duration * 0.9
    stage1_hi = video_duration * 1.1
    in_stage1 = stage1_lo <= audio_duration <= stage1_hi
    in_final = final_lo <= audio_duration <= final_hi
    return in_stage1 and not in_final


def _speedup_ratio(audio_duration: float, video_duration: float) -> float:
    """计算 ElevenLabs voice_settings.speed 取值。

    ratio = audio_duration / video_duration：
    - >1 时音频过长，需要变快、变短 → speed > 1
    - <1 时音频过短，需要变慢、变长 → speed < 1
    Clamp 到 ElevenLabs 合法范围 [0.7, 1.2]，超出窗口的极端值由调用方在
    _in_speedup_window 阶段已经过滤掉，这里 clamp 只是兜底。
    """
    raw = audio_duration / video_duration
    return max(0.7, min(1.2, raw))
```

- [ ] **Step 4: 在 `appcore/runtime/__init__.py` 里 re-export**

打开 `appcore/runtime/__init__.py`，找到现有的 `_tts_final_target_range` re-export（搜索 `_tts_final_target_range`），在同一个 import 块里追加 `_in_speedup_window, _speedup_ratio`。例如：

```python
from appcore.runtime._helpers import (
    _tts_final_target_range,
    _distance_to_duration_range,
    _compute_next_target,
    _in_speedup_window,
    _speedup_ratio,
)
```

（具体合并方式以现有 import 风格为准，关键是保证 `from appcore.runtime import _in_speedup_window` 能成功。）

- [ ] **Step 5: 跑测试看通过**

Run:
```powershell
pytest tests/test_tts_duration_loop.py::TestSpeedupWindow -v
```
Expected: 6 个用例全 PASS。

- [ ] **Step 6: Commit**

```bash
git -C .worktrees/tts-speedup-shortcut add appcore/runtime/_helpers.py appcore/runtime/__init__.py tests/test_tts_duration_loop.py
git -C .worktrees/tts-speedup-shortcut commit -m "$(cat <<'EOF'
feat(runtime): 加 _in_speedup_window / _speedup_ratio 纯函数

判定 TTS 变速短路触发窗口（在 ±10% 但不在 final 区间）+ 计算合法 speed ratio。
为 _run_tts_duration_loop 即将引入的变速分支提供决策原语。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: `pipeline/tts.py` 新增 `regenerate_full_audio_with_speed`

**Files:**
- Modify: `pipeline/tts.py`
- Test: `tests/test_tts_speedup_pipeline.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_tts_speedup_pipeline.py`：

```python
"""regenerate_full_audio_with_speed 单测：mock ElevenLabs SDK，验证：
- 每段 segment 都用同一个 speed 调用 generate_segment_audio
- segments 落盘到独立目录避免缓存命中干扰
- concat 出的 mp3 路径符合命名约定
- 网络异常透出（不吞）让上层走 fallback
"""
import os
from unittest.mock import MagicMock, patch

import pytest


def test_regenerate_full_audio_with_speed_calls_each_segment_with_speed(tmp_path):
    from pipeline import tts

    segments = [
        {"index": 0, "tts_text": "hello world", "translated": "ignored"},
        {"index": 1, "tts_text": "second segment"},
        {"index": 2, "tts_text": "third"},
    ]

    seg_calls = []

    def fake_generate_segment_audio(text, voice_id, output_path, **kwargs):
        seg_calls.append({"text": text, "voice_id": voice_id,
                          "output_path": output_path,
                          "speed": kwargs.get("speed")})
        # 真的写一个空文件让 concat 不爆
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(b"\xff\xfb\x10\x00")  # mp3 magic bytes 占位
        return output_path

    def fake_get_audio_duration(path):
        return 1.5

    with patch.object(tts, "generate_segment_audio", side_effect=fake_generate_segment_audio), \
         patch.object(tts, "_get_audio_duration", side_effect=fake_get_audio_duration), \
         patch("subprocess.run") as fake_run:
        fake_run.return_value = MagicMock(returncode=0, stderr="", stdout="")

        result = tts.regenerate_full_audio_with_speed(
            segments=segments,
            voice_id="voice-xyz",
            output_dir=str(tmp_path),
            variant="round_2",
            speed=0.9772,
            elevenlabs_api_key="fake-key",
            model_id="eleven_turbo_v2_5",
            language_code="es",
        )

    # 全部 segment 都用 speed=0.9772
    assert len(seg_calls) == 3
    for c in seg_calls:
        assert c["speed"] == pytest.approx(0.9772, abs=1e-4)
        assert c["voice_id"] == "voice-xyz"

    # segments 落盘到独立目录 round_2_speedup
    expected_seg_dir = os.path.join(str(tmp_path), "tts_segments", "round_2_speedup")
    for i, c in enumerate(seg_calls):
        assert c["output_path"] == os.path.join(expected_seg_dir, f"seg_{i:04d}.mp3")

    # concat 输出路径 tts_full.round_2.speedup.mp3
    assert result["full_audio_path"] == os.path.join(
        str(tmp_path), "tts_full.round_2.speedup.mp3"
    )
    assert len(result["segments"]) == 3
    for s in result["segments"]:
        assert "tts_path" in s and "tts_duration" in s


def test_regenerate_full_audio_with_speed_propagates_elevenlabs_failure(tmp_path):
    """ElevenLabs SDK 抛错时函数应该让异常上抛，调用方会 fallback。"""
    from pipeline import tts

    segments = [{"index": 0, "tts_text": "x"}]

    def boom(*args, **kwargs):
        raise RuntimeError("simulated elevenlabs SSL EOF")

    with patch.object(tts, "generate_segment_audio", side_effect=boom):
        with pytest.raises(RuntimeError, match="simulated elevenlabs SSL EOF"):
            tts.regenerate_full_audio_with_speed(
                segments=segments,
                voice_id="v",
                output_dir=str(tmp_path),
                variant="round_3",
                speed=1.05,
            )


def test_regenerate_full_audio_with_speed_invokes_on_segment_done_callback(tmp_path):
    from pipeline import tts

    segments = [{"index": i, "tts_text": f"seg{i}"} for i in range(3)]
    progress = []

    def fake_gen(text, voice_id, output_path, **kw):
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(b"\xff\xfb\x10\x00")
        return output_path

    with patch.object(tts, "generate_segment_audio", side_effect=fake_gen), \
         patch.object(tts, "_get_audio_duration", return_value=1.0), \
         patch("subprocess.run") as fake_run:
        fake_run.return_value = MagicMock(returncode=0, stderr="", stdout="")
        tts.regenerate_full_audio_with_speed(
            segments=segments, voice_id="v", output_dir=str(tmp_path),
            variant="r1", speed=1.05,
            on_segment_done=lambda done, total, info: progress.append((done, total)),
        )

    assert progress == [(1, 3), (2, 3), (3, 3)]
```

- [ ] **Step 2: 跑测试看失败**

Run:
```powershell
pytest tests/test_tts_speedup_pipeline.py -v
```
Expected: 全部 FAIL — `AttributeError: module 'pipeline.tts' has no attribute 'regenerate_full_audio_with_speed'`。

- [ ] **Step 3: 实现 `regenerate_full_audio_with_speed`**

打开 `pipeline/tts.py`，在 `generate_full_audio` 函数（约第 166 行）之后追加新函数：

```python
def regenerate_full_audio_with_speed(
    segments: List[Dict],
    voice_id: str,
    output_dir: str,
    *,
    variant: str,
    speed: float,
    elevenlabs_api_key: str | None = None,
    model_id: str = "eleven_turbo_v2_5",
    language_code: str | None = None,
    on_segment_done: Optional[Callable[[int, int, dict], None]] = None,
) -> Dict:
    """以指定 speed 重新合成 segments 并 concat。

    用于 TTS Duration Loop 的"变速短路"分支：当某轮原始音频落入 ±10% 但不在
    final range，通过 voice_settings.speed 一击直接收敛到 [v-1, v+2]。

    Args:
        segments: 与 generate_full_audio 相同的输入（含 tts_text）
        variant: 用于命名 segment 子目录和 concat 产物，例如 "round_2"
        speed: ElevenLabs voice_settings.speed，合法范围 [0.7, 1.2]，调用方须先 clamp
        on_segment_done: 同 generate_full_audio

    Returns:
        {"full_audio_path": str, "segments": [...]}  # 每段含 tts_path / tts_duration

    Raises:
        透出 ElevenLabs SDK 的网络异常（已通过 _call_with_network_retry 重试），
        让 _run_tts_duration_loop 走原始音频 atempo fallback。
    """
    if not (0.7 <= speed <= 1.2):
        raise ValueError(f"speed must be in [0.7, 1.2], got {speed}")
    seg_dir = os.path.join(output_dir, "tts_segments", f"{variant}_speedup")
    os.makedirs(seg_dir, exist_ok=True)

    updated_segments = []
    concat_list_path = os.path.join(seg_dir, "concat.txt")
    total = len(segments)

    with open(concat_list_path, "w", encoding="utf-8") as concat_f:
        for i, seg in enumerate(segments):
            text = seg.get("tts_text") or seg.get("translated") or seg.get("text", "")
            seg_path = os.path.join(seg_dir, f"seg_{i:04d}.mp3")

            generate_segment_audio(
                text, voice_id, seg_path,
                elevenlabs_api_key=elevenlabs_api_key,
                model_id=model_id, language_code=language_code,
                speed=speed,
            )
            duration = _get_audio_duration(seg_path)

            seg_copy = dict(seg)
            seg_copy["tts_path"] = seg_path
            seg_copy["tts_duration"] = duration
            updated_segments.append(seg_copy)
            concat_f.write(f"file '{os.path.abspath(seg_path)}'\n")

            if on_segment_done is not None:
                try:
                    on_segment_done(i + 1, total, {
                        "segment_index": i,
                        "tts_duration": duration,
                        "tts_text_preview": (text or "")[:60],
                        "speed": speed,
                    })
                except Exception:
                    log.exception("on_segment_done callback raised; ignoring")

    full_audio_path = os.path.join(output_dir, f"tts_full.{variant}.speedup.mp3")
    result = subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list_path,
         "-c", "copy", full_audio_path],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"音频拼接失败 (speedup): {result.stderr}")

    return {"full_audio_path": full_audio_path, "segments": updated_segments}
```

- [ ] **Step 4: 跑测试看通过**

Run:
```powershell
pytest tests/test_tts_speedup_pipeline.py -v
```
Expected: 3 个用例全 PASS。

- [ ] **Step 5: Commit**

```bash
git -C .worktrees/tts-speedup-shortcut add pipeline/tts.py tests/test_tts_speedup_pipeline.py
git -C .worktrees/tts-speedup-shortcut commit -m "$(cat <<'EOF'
feat(tts): 新增 regenerate_full_audio_with_speed

按指定 ElevenLabs voice_settings.speed 重新合成 segments 并 concat，
落盘到独立 tts_segments/<variant>_speedup/ 目录避免与原始 segments
缓存冲突，成品 tts_full.<variant>.speedup.mp3。供 TTS Duration Loop
变速短路分支使用。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: 评估 orchestrator `appcore/tts_speedup_eval.py`

**Files:**
- Create: `appcore/tts_speedup_eval.py`
- Test: `tests/test_tts_speedup_eval.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_tts_speedup_eval.py`：

```python
"""tts_speedup_eval orchestrator 测试。
覆盖：
- run_evaluation 写 pending 行 → 调 invoke_generate → 写 ok 行
- LLM 抛异常时写 failed 行，不向上抛
- LLM 超过 timeout 时写 failed 行
- retry_evaluation 重跑只更新 score / model / status，不动 audio 路径
"""
from unittest.mock import MagicMock, patch
import pytest


@pytest.fixture(autouse=True)
def _stub_db(monkeypatch):
    """把 appcore.tts_speedup_eval 内部用的 db 调用 stub 掉。"""
    written = {"rows": []}

    def fake_execute(sql, params=None):
        written["rows"].append({"sql": sql, "params": params})
        return MagicMock(lastrowid=42)

    def fake_query_one(sql, params=None):
        # 重跑测试用：返回一行 fake 数据
        return {
            "id": 42, "task_id": "t1", "round_index": 2, "language": "es",
            "audio_pre_path": "tts_full.round_2.mp3",
            "audio_post_path": "tts_full.round_2.speedup.mp3",
            "video_duration": 60.0, "audio_pre_duration": 64.0,
            "audio_post_duration": 60.5, "speed_ratio": 1.0667,
            "hit_final_range": 1, "status": "failed",
        }

    monkeypatch.setattr("appcore.tts_speedup_eval.db_execute", fake_execute, raising=False)
    monkeypatch.setattr("appcore.tts_speedup_eval.db_query_one", fake_query_one, raising=False)
    return written


def _llm_ok():
    return {
        "json": {
            "score_naturalness": 4,
            "score_pacing": 3,
            "score_timbre": 5,
            "score_intelligibility": 5,
            "score_overall": 4,
            "summary": "整体可用，节奏轻微抖动",
            "flags": ["minor_pace_jitter"],
        },
        "usage": {"input_tokens": 1234, "output_tokens": 89, "cost_cny": 0.012},
    }


def test_run_evaluation_happy_path_writes_ok_row(tmp_path, _stub_db):
    from appcore import tts_speedup_eval

    # 创建 fake audio 文件
    pre = tmp_path / "pre.mp3"; pre.write_bytes(b"\xff\xfb\x10\x00")
    post = tmp_path / "post.mp3"; post.write_bytes(b"\xff\xfb\x10\x00")

    with patch("appcore.tts_speedup_eval.llm_client.invoke_generate", return_value=_llm_ok()):
        eval_id = tts_speedup_eval.run_evaluation(
            task_id="task-xyz", round_index=2, language="es",
            video_duration=60.0,
            audio_pre_path=str(pre), audio_pre_duration=64.0,
            audio_post_path=str(post), audio_post_duration=60.5,
            speed_ratio=1.0667, hit_final_range=True,
            user_id=1,
        )
    assert eval_id == 42
    sqls = [r["sql"] for r in _stub_db["rows"]]
    assert any("INSERT INTO tts_speedup_evaluations" in s for s in sqls)
    assert any("UPDATE tts_speedup_evaluations" in s and "status" in s for s in sqls)


def test_run_evaluation_llm_failure_writes_failed_row(tmp_path, _stub_db):
    from appcore import tts_speedup_eval

    pre = tmp_path / "pre.mp3"; pre.write_bytes(b"\xff\xfb\x10\x00")
    post = tmp_path / "post.mp3"; post.write_bytes(b"\xff\xfb\x10\x00")

    def boom(*args, **kwargs):
        raise RuntimeError("openrouter 502")

    with patch("appcore.tts_speedup_eval.llm_client.invoke_generate", side_effect=boom):
        eval_id = tts_speedup_eval.run_evaluation(
            task_id="task-xyz", round_index=2, language="es",
            video_duration=60.0,
            audio_pre_path=str(pre), audio_pre_duration=64.0,
            audio_post_path=str(post), audio_post_duration=60.5,
            speed_ratio=1.0667, hit_final_range=True,
            user_id=1,
        )
    assert eval_id == 42  # 仍然返回 ID，但 status=failed
    sqls = " ".join(r["sql"] for r in _stub_db["rows"])
    assert "INSERT INTO tts_speedup_evaluations" in sqls
    assert "UPDATE tts_speedup_evaluations" in sqls
    failed_update = [r for r in _stub_db["rows"]
                     if r["sql"].strip().startswith("UPDATE")]
    assert any("failed" in str(r["params"]) for r in failed_update)


def test_run_evaluation_timeout_writes_failed_row(tmp_path, _stub_db):
    """超过 EVAL_TIMEOUT_SECONDS 时写 failed 行，不向上抛。"""
    import time
    from appcore import tts_speedup_eval

    pre = tmp_path / "pre.mp3"; pre.write_bytes(b"\xff\xfb\x10\x00")
    post = tmp_path / "post.mp3"; post.write_bytes(b"\xff\xfb\x10\x00")

    def slow(*args, **kwargs):
        time.sleep(5)  # 超过测试用的小 timeout
        return _llm_ok()

    with patch("appcore.tts_speedup_eval.llm_client.invoke_generate", side_effect=slow), \
         patch("appcore.tts_speedup_eval.EVAL_TIMEOUT_SECONDS", 0.5):
        eval_id = tts_speedup_eval.run_evaluation(
            task_id="task-xyz", round_index=2, language="es",
            video_duration=60.0,
            audio_pre_path=str(pre), audio_pre_duration=64.0,
            audio_post_path=str(post), audio_post_duration=60.5,
            speed_ratio=1.0667, hit_final_range=True,
            user_id=1,
        )
    failed_rows = [r for r in _stub_db["rows"] if "UPDATE" in r["sql"]]
    assert any("failed" in str(r["params"]) for r in failed_rows)


def test_retry_evaluation_only_updates_scores_and_status(tmp_path, _stub_db):
    """retry 不重新写 audio 路径，只更新 score / model / status。"""
    from appcore import tts_speedup_eval

    with patch("appcore.tts_speedup_eval.llm_client.invoke_generate", return_value=_llm_ok()):
        ok = tts_speedup_eval.retry_evaluation(eval_id=42, user_id=1)
    assert ok is True
    update_rows = [r for r in _stub_db["rows"]
                   if r["sql"].strip().startswith("UPDATE")]
    assert update_rows
    # audio_pre_path / audio_post_path 不应在 UPDATE 字段里
    for r in update_rows:
        assert "audio_pre_path" not in r["sql"]
        assert "audio_post_path" not in r["sql"]
```

- [ ] **Step 2: 跑测试看失败**

Run:
```powershell
pytest tests/test_tts_speedup_eval.py -v
```
Expected: 全部 FAIL — `ModuleNotFoundError: No module named 'appcore.tts_speedup_eval'`。

- [ ] **Step 3: 实现 orchestrator**

新建 `appcore/tts_speedup_eval.py`：

```python
"""TTS 变速短路质量评估 orchestrator。

业务流程：duration loop 跑完变速 pass 后，同步调用 run_evaluation：
1. INSERT 一行 status=pending（占位）
2. concurrent.futures 包 60s 超时调 llm_client.invoke_generate
3. UPDATE 该行成 ok/failed + scores + 模型信息

后续 admin 可在跨任务页点"重新评估"触发 retry_evaluation（只更新 score 字段）。

设计点：
- 评估失败永远不向上抛，只写 status=failed，让任务正常返回收敛结果
- audio_pre_path / audio_post_path 是 task_dir 相对路径（一致与 _maybe_tempo_align 风格），
  实际传给 invoke_generate(media=...) 时由调用方拼成绝对路径
- llm_client.invoke_generate 没有原生 timeout，用 ThreadPoolExecutor.submit().result(timeout=...)
"""
from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Any

from appcore import llm_client
from appcore.db import execute as db_execute, query_one as db_query_one

log = logging.getLogger(__name__)

USE_CASE_CODE = "video_translate.tts_speedup_quality_review"
EVAL_TIMEOUT_SECONDS = 120  # 双音频多模态评估，留充足余量

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "score_naturalness":     {"type": "integer", "minimum": 1, "maximum": 5},
        "score_pacing":          {"type": "integer", "minimum": 1, "maximum": 5},
        "score_timbre":          {"type": "integer", "minimum": 1, "maximum": 5},
        "score_intelligibility": {"type": "integer", "minimum": 1, "maximum": 5},
        "score_overall":         {"type": "integer", "minimum": 1, "maximum": 5},
        "summary":               {"type": "string"},
        "flags":                 {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "score_naturalness", "score_pacing", "score_timbre",
        "score_intelligibility", "score_overall", "summary", "flags",
    ],
}


def _build_prompt(
    *, language: str, speed_ratio: float,
    video_duration: float,
    audio_pre_duration: float, audio_post_duration: float,
    hit_final_range: bool,
) -> str:
    return (
        f"你是带货视频配音质量评审。系统在 TTS 时长收敛流程中尝试用 ElevenLabs "
        f"voice_settings.speed={speed_ratio:.4f} 把目标语言（{language}）配音从 "
        f"{audio_pre_duration:.2f}s 调整到 {audio_post_duration:.2f}s，"
        f"目标视频时长 {video_duration:.2f}s，"
        f"{'变速后已落入最终收敛区间' if hit_final_range else '变速后仍偏离最终收敛区间'}。\n\n"
        "请对比附带的两段音频（第一段=变速前原始合成，第二段=变速重生成）"
        "并按 1-5 分输出五维评分（5 最好）：\n"
        "- naturalness：人声自然度（机械感/鸭嗓/chipmunk 越强分越低）\n"
        "- pacing：节奏稳定性（拖音/卡顿/时间拉伸抖动）\n"
        "- timbre：音色保留度（变速后是否还像同一个人）\n"
        "- intelligibility：可懂度（母语听众能否清晰理解每个词）\n"
        "- overall：整体是否愿意发布\n\n"
        "summary 用中文写一段总结（≤120 字）。"
        "flags 是问题点的英文短标签数组（如 chipmunk_effect / tail_wobble / "
        "pace_jitter / muffled_consonant），无问题给空数组。"
    )


def run_evaluation(
    *,
    task_id: str,
    round_index: int,
    language: str,
    video_duration: float,
    audio_pre_path: str,
    audio_pre_duration: float,
    audio_post_path: str,
    audio_post_duration: float,
    speed_ratio: float,
    hit_final_range: bool,
    user_id: int | None,
) -> int:
    """同步执行评估。返回 eval_id。永远不抛异常 — 失败也写 status=failed 行。"""
    db_execute(
        """
        INSERT INTO tts_speedup_evaluations
          (task_id, round_index, language, video_duration,
           audio_pre_duration, audio_post_duration, speed_ratio, hit_final_range,
           audio_pre_path, audio_post_path, status, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending', CURRENT_TIMESTAMP)
        ON DUPLICATE KEY UPDATE
          audio_pre_duration=VALUES(audio_pre_duration),
          audio_post_duration=VALUES(audio_post_duration),
          speed_ratio=VALUES(speed_ratio),
          hit_final_range=VALUES(hit_final_range),
          audio_pre_path=VALUES(audio_pre_path),
          audio_post_path=VALUES(audio_post_path),
          status='pending', error_text=NULL,
          score_naturalness=NULL, score_pacing=NULL, score_timbre=NULL,
          score_intelligibility=NULL, score_overall=NULL, summary_text=NULL,
          flags_json=NULL, llm_input_tokens=NULL, llm_output_tokens=NULL,
          llm_cost_usd=NULL, evaluated_at=NULL
        """,
        (task_id, round_index, language, video_duration,
         audio_pre_duration, audio_post_duration, speed_ratio,
         1 if hit_final_range else 0,
         audio_pre_path, audio_post_path),
    )
    row = db_query_one(
        "SELECT id FROM tts_speedup_evaluations WHERE task_id=%s AND round_index=%s",
        (task_id, round_index),
    )
    eval_id = int(row["id"]) if row else 0

    prompt = _build_prompt(
        language=language, speed_ratio=speed_ratio,
        video_duration=video_duration,
        audio_pre_duration=audio_pre_duration,
        audio_post_duration=audio_post_duration,
        hit_final_range=hit_final_range,
    )

    def _do_call():
        return llm_client.invoke_generate(
            USE_CASE_CODE,
            prompt=prompt,
            user_id=user_id,
            project_id=task_id,
            media=[audio_pre_path, audio_post_path],
            response_schema=RESPONSE_SCHEMA,
            temperature=0.2,
        )

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_do_call)
            result = future.result(timeout=EVAL_TIMEOUT_SECONDS)
    except FuturesTimeoutError as exc:
        log.warning("[tts_speedup_eval] timeout for task %s round %s", task_id, round_index)
        _write_failed(eval_id, f"timeout after {EVAL_TIMEOUT_SECONDS}s")
        return eval_id
    except Exception as exc:
        log.exception("[tts_speedup_eval] LLM error for task %s round %s",
                      task_id, round_index)
        _write_failed(eval_id, str(exc)[:1000])
        return eval_id

    _write_ok(eval_id, result)
    return eval_id


def retry_evaluation(*, eval_id: int, user_id: int | None) -> bool:
    """对已存在的 eval 行重跑 LLM 调用。成功返回 True。"""
    row = db_query_one(
        """SELECT id, task_id, round_index, language, video_duration,
                  audio_pre_path, audio_post_path,
                  audio_pre_duration, audio_post_duration,
                  speed_ratio, hit_final_range
           FROM tts_speedup_evaluations WHERE id=%s""",
        (eval_id,),
    )
    if not row:
        return False
    db_execute(
        """UPDATE tts_speedup_evaluations
           SET status='pending', error_text=NULL,
               score_naturalness=NULL, score_pacing=NULL, score_timbre=NULL,
               score_intelligibility=NULL, score_overall=NULL,
               summary_text=NULL, flags_json=NULL,
               llm_input_tokens=NULL, llm_output_tokens=NULL,
               llm_cost_usd=NULL, evaluated_at=NULL
           WHERE id=%s""",
        (eval_id,),
    )
    prompt = _build_prompt(
        language=row["language"],
        speed_ratio=float(row["speed_ratio"]),
        video_duration=float(row["video_duration"]),
        audio_pre_duration=float(row["audio_pre_duration"]),
        audio_post_duration=float(row["audio_post_duration"]),
        hit_final_range=bool(row["hit_final_range"]),
    )

    def _do_call():
        return llm_client.invoke_generate(
            USE_CASE_CODE, prompt=prompt, user_id=user_id,
            project_id=row["task_id"],
            media=[row["audio_pre_path"], row["audio_post_path"]],
            response_schema=RESPONSE_SCHEMA, temperature=0.2,
        )

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_do_call)
            result = future.result(timeout=EVAL_TIMEOUT_SECONDS)
    except FuturesTimeoutError:
        _write_failed(eval_id, f"timeout after {EVAL_TIMEOUT_SECONDS}s")
        return False
    except Exception as exc:
        _write_failed(eval_id, str(exc)[:1000])
        return False

    _write_ok(eval_id, result)
    return True


def _write_ok(eval_id: int, result: dict) -> None:
    payload = result.get("json") or {}
    usage = result.get("usage") or {}
    binding = _resolve_binding_for_log()
    db_execute(
        """UPDATE tts_speedup_evaluations
           SET status='ok', error_text=NULL,
               score_naturalness=%s, score_pacing=%s, score_timbre=%s,
               score_intelligibility=%s, score_overall=%s,
               summary_text=%s, flags_json=%s,
               model_provider=%s, model_id=%s,
               llm_input_tokens=%s, llm_output_tokens=%s, llm_cost_usd=%s,
               evaluated_at=CURRENT_TIMESTAMP
           WHERE id=%s""",
        (
            payload.get("score_naturalness"), payload.get("score_pacing"),
            payload.get("score_timbre"), payload.get("score_intelligibility"),
            payload.get("score_overall"),
            payload.get("summary") or "",
            json.dumps(payload.get("flags") or [], ensure_ascii=False),
            binding["provider"], binding["model"],
            usage.get("input_tokens"), usage.get("output_tokens"),
            usage.get("cost_usd"),
            eval_id,
        ),
    )


def _write_failed(eval_id: int, error_text: str) -> None:
    binding = _resolve_binding_for_log()
    db_execute(
        """UPDATE tts_speedup_evaluations
           SET status='failed', error_text=%s,
               model_provider=%s, model_id=%s,
               evaluated_at=CURRENT_TIMESTAMP
           WHERE id=%s""",
        (error_text, binding["provider"], binding["model"], eval_id),
    )


def _resolve_binding_for_log() -> dict:
    """提前 resolve binding 以便记录实际使用的 provider/model（即便后续 LLM 调用失败）。"""
    try:
        from appcore import llm_bindings
        return llm_bindings.resolve(USE_CASE_CODE)
    except Exception:
        return {"provider": "unknown", "model": "unknown"}
```

- [ ] **Step 4: 跑测试看通过**

Run:
```powershell
pytest tests/test_tts_speedup_eval.py -v
```
Expected: 4 个用例全 PASS。

> 提示：如果 `test_run_evaluation_timeout_writes_failed_row` 跑得慢，把 `time.sleep(5)` 换更短即可，但要保证 > `EVAL_TIMEOUT_SECONDS` 的 patched 值。

- [ ] **Step 5: Commit**

```bash
git -C .worktrees/tts-speedup-shortcut add appcore/tts_speedup_eval.py tests/test_tts_speedup_eval.py
git -C .worktrees/tts-speedup-shortcut commit -m "$(cat <<'EOF'
feat(eval): 新增 tts_speedup_eval orchestrator

run_evaluation/retry_evaluation 同步执行 OpenRouter Gemini 3 Flash 双音频
对比评分，超时/异常都写 status=failed 不向上抛，保证 TTS Duration Loop
变速短路分支即使评估出错也能正常收敛返回。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: `_run_tts_duration_loop` 注入变速短路分支

**Files:**
- Modify: `appcore/runtime/_pipeline_runner.py`（修改 `_run_tts_duration_loop`，约第 585-650 行 measure 分支后）

- [ ] **Step 1: 阅读现有 measure 分支**

打开 `appcore/runtime/_pipeline_runner.py:587-647`。看清楚：
- `audio_duration` 通过 `_get_audio_duration(result["full_audio_path"])` 拿到
- `final_target_lo / final_target_hi` 是当前 round 起点已经计算
- `if final_target_lo <= audio_duration <= final_target_hi:` 是已收敛分支（保留不动）
- 它之后是 `last_audio_duration = audio_duration; last_word_count = word_count` 进入下一轮 rewrite

变速短路分支要插在 "已收敛分支" 后面、`last_audio_duration = audio_duration` 前面。

- [ ] **Step 2: 写实现**

打开 `appcore/runtime/_pipeline_runner.py`，找到 `_run_tts_duration_loop` 里 `if final_target_lo <= audio_duration <= final_target_hi:` 这块的 `return {...}` 右括号（约第 643 行）。**紧接着这个 return 后面**、`# Note: do NOT update prev_localized` 注释之前，插入变速短路分支：

```python
            # ============= 变速短路分支（2026-05-04） =============
            # 进入 ±10% 但不在 [v-1, v+2] 时，用 ElevenLabs voice_settings.speed
            # 重新合成一遍音频。命中 final 即收敛；未命中走 atempo 兜底；变速本身
            # 失败则回退到原始音频走 atempo。无论哪条路径，都立即终结，不再继续
            # 后续 rewrite 轮次。
            from appcore.runtime import _in_speedup_window, _speedup_ratio
            if _in_speedup_window(
                audio_duration=audio_duration, video_duration=video_duration,
            ):
                speed = _speedup_ratio(audio_duration, video_duration)
                round_record["speedup_applied"] = True
                round_record["speedup_speed"] = round(speed, 4)
                round_record["speedup_pre_duration"] = audio_duration
                round_record["is_final"] = True
                _substep(f"变速短路：speed={speed:.4f}, 重生成 ElevenLabs 音频")
                self._emit_duration_round(task_id, round_index, "speedup_start", round_record)

                speedup_audio_path = None
                speedup_duration = None
                speedup_failed_reason = None
                try:
                    from pipeline.tts import regenerate_full_audio_with_speed
                    speedup_result = regenerate_full_audio_with_speed(
                        result["segments"],
                        voice["elevenlabs_voice_id"],
                        task_dir,
                        variant=f"round_{round_index}",
                        speed=speed,
                        elevenlabs_api_key=elevenlabs_api_key,
                        model_id=tts_model_id,
                        language_code=tts_language_code,
                    )
                    speedup_audio_path = speedup_result["full_audio_path"]
                    speedup_duration = _get_audio_duration(speedup_audio_path)
                    round_record["speedup_audio_path"] = (
                        os.path.relpath(speedup_audio_path, task_dir)
                    )
                    round_record["speedup_post_duration"] = speedup_duration
                    round_record["speedup_chars_used"] = sum(
                        len((s.get("tts_text") or "")) for s in result["segments"]
                    )
                except Exception as exc:
                    log.exception(
                        "[task %s] speedup regeneration failed at round %d, falling back",
                        task_id, round_index,
                    )
                    speedup_failed_reason = str(exc)[:500]
                    round_record["speedup_failed_reason"] = speedup_failed_reason

                # Decide which audio is the final adopted one.
                if speedup_audio_path is None:
                    # Fallback：原始音频 + atempo
                    final_audio_path = self._maybe_tempo_align(
                        audio_path=result["full_audio_path"],
                        audio_duration=audio_duration,
                        video_duration=video_duration,
                        task_dir=task_dir, variant=variant,
                        round_record=round_record, task_id=task_id,
                    )
                    round_record["final_reason"] = "speedup_failed_fallback"
                    round_record["speedup_hit_final"] = False
                else:
                    hit_final = (
                        final_target_lo <= speedup_duration <= final_target_hi
                    )
                    round_record["speedup_hit_final"] = hit_final
                    if hit_final:
                        # 命中：再走一次 atempo 兜底精确对齐（误差 ≤ 5% 时拉伸到精确等长）
                        final_audio_path = self._maybe_tempo_align(
                            audio_path=speedup_audio_path,
                            audio_duration=speedup_duration,
                            video_duration=video_duration,
                            task_dir=task_dir, variant=f"{variant}_speedup",
                            round_record=round_record, task_id=task_id,
                        )
                        round_record["final_reason"] = "speedup_converged"
                    else:
                        # 未命中 final：仍然终结，对变速产物跑 atempo（如果误差在 ±5%
                        # 内可拉伸到精确等长；否则保留变速产物）
                        final_audio_path = self._maybe_tempo_align(
                            audio_path=speedup_audio_path,
                            audio_duration=speedup_duration,
                            video_duration=video_duration,
                            task_dir=task_dir, variant=f"{variant}_speedup",
                            round_record=round_record, task_id=task_id,
                        )
                        round_record["final_reason"] = "speedup_then_atempo"

                # 同步 AI 评估（仅当变速成功有 audio_post 才跑）
                eval_id = None
                if speedup_audio_path is not None:
                    try:
                        from appcore import tts_speedup_eval
                        eval_id = tts_speedup_eval.run_evaluation(
                            task_id=task_id,
                            round_index=round_index,
                            language=target_language_label or "",
                            video_duration=video_duration,
                            audio_pre_path=os.path.relpath(
                                result["full_audio_path"], task_dir,
                            ),
                            audio_pre_duration=audio_duration,
                            audio_post_path=round_record.get(
                                "speedup_audio_path", ""
                            ),
                            audio_post_duration=speedup_duration,
                            speed_ratio=speed,
                            hit_final_range=bool(
                                round_record.get("speedup_hit_final")
                            ),
                            user_id=self.user_id,
                        )
                    except Exception:
                        log.exception(
                            "[task %s] tts_speedup_eval.run_evaluation raised; ignoring",
                            task_id,
                        )
                round_record["speedup_eval_id"] = eval_id

                round_products[-1]["tts_audio_path"] = final_audio_path
                rounds[-1] = round_record
                task_state.update(
                    task_id,
                    tts_duration_rounds=rounds,
                    tts_duration_status="converged",
                    tts_final_round=round_index,
                    tts_final_reason=round_record["final_reason"],
                    tts_final_distance=0.0,
                )
                self._emit_duration_round(
                    task_id, round_index, "speedup_done", round_record,
                )
                tts_generation_stats.finalize(
                    task_id=task_id,
                    task=task_state.get(task_id) or {},
                    rounds=rounds,
                )
                return {
                    "localized_translation": localized_translation,
                    "tts_script": tts_script,
                    "tts_audio_path": final_audio_path,
                    "tts_segments": result["segments"],
                    "rounds": rounds,
                    "round_products": round_products,
                    "final_round": round_index,
                }
            # ============= 变速短路分支结束 =============
```

- [ ] **Step 3: 语法自检**

Run:
```powershell
python -c "from appcore.runtime._pipeline_runner import PipelineRunner; print('OK')"
```
Expected: 输出 `OK`。如果报 `ImportError` / `SyntaxError`，回看插入位置。

- [ ] **Step 4: 现有测试不能破**

Run:
```powershell
pytest tests/test_tts_duration_loop.py -v
```
Expected: 已有的 `TestComputeNextTarget / TestSpeedupWindow / TestDurationLoopRound1Only` 全部通过。变速短路分支不会被现有测试触发（因为现有测试 audio 都直接落入 final 区间），所以应保持兼容。

- [ ] **Step 5: Commit**

```bash
git -C .worktrees/tts-speedup-shortcut add appcore/runtime/_pipeline_runner.py
git -C .worktrees/tts-speedup-shortcut commit -m "$(cat <<'EOF'
feat(runtime): _run_tts_duration_loop 引入变速短路分支

每轮 measure 后判断：音频在 ±10% 但不在 [v-1, v+2] → ElevenLabs 变速重
合成 + 同步 AI 评估 + atempo 兜底 + 终结，不再继续后续 rewrite 轮次。
变速失败回退原始音频跑 atempo + 终结。3-5 分钟以上长视频常见的"5 轮跑
完仍偏离 ±5%~10%"场景预期 1 轮收敛。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: 集成测试（duration loop 各分支）

**Files:**
- Modify: `tests/test_tts_duration_loop.py`（新增 `TestSpeedupShortcut` 类）

- [ ] **Step 1: 写四组失败测试**

在 `tests/test_tts_duration_loop.py` 末尾追加：

```python
class TestSpeedupShortcut:
    """变速短路分支集成测试。
    每个用例 fake 出 audio_duration 落入 ±10% 但不在 final，触发新分支。"""

    def _make_runner(self):
        from appcore.events import EventBus
        from appcore.runtime import PipelineRunner
        return PipelineRunner(bus=EventBus(), user_id=1)

    def _common_patches(self, monkeypatch, audio_dur, speedup_dur=None,
                         speedup_raises=None):
        """统一打桩：translate / tts_script / generate_full_audio /
        regenerate_full_audio_with_speed / _get_audio_duration / 评估。"""
        import os, json
        # 把所有外部依赖都桩成同步 deterministic 行为
        monkeypatch.setattr(
            "pipeline.translate.generate_localized_rewrite",
            lambda **kw: {"full_text": "x" * 80, "sentences": [{"text": "x"}],
                          "_usage": {}, "_messages": []},
        )
        monkeypatch.setattr(
            "pipeline.translate.generate_tts_script",
            lambda loc, **kw: {"full_text": "x", "blocks": [
                {"index": 0, "text": "x", "sentence_indices": [0],
                 "source_segment_indices": [0]}],
                "subtitle_chunks": [], "_usage": {}},
        )
        # generate_full_audio：写一个空 mp3，segments 占位
        def fake_full_audio(segs, voice_id, task_dir, variant=None, **kw):
            out = os.path.join(task_dir, f"tts_full.{variant}.mp3")
            with open(out, "wb") as f:
                f.write(b"\xff\xfb\x10\x00")
            return {"full_audio_path": out,
                    "segments": [{"index": 0, "tts_path": out,
                                   "tts_duration": audio_dur, "tts_text": "x"}]}
        monkeypatch.setattr("pipeline.tts.generate_full_audio", fake_full_audio)

        # 变速重合成：抛错或写文件
        if speedup_raises is not None:
            def boom(*a, **kw):
                raise speedup_raises
            monkeypatch.setattr(
                "pipeline.tts.regenerate_full_audio_with_speed", boom,
            )
        else:
            def fake_speedup(segs, voice_id, task_dir, variant=None, **kw):
                out = os.path.join(task_dir,
                                    f"tts_full.{variant}.speedup.mp3")
                with open(out, "wb") as f:
                    f.write(b"\xff\xfb\x10\x00")
                return {"full_audio_path": out,
                        "segments": [{"index": 0, "tts_path": out,
                                       "tts_duration": speedup_dur or audio_dur,
                                       "tts_text": "x"}]}
            monkeypatch.setattr(
                "pipeline.tts.regenerate_full_audio_with_speed", fake_speedup,
            )

        # _get_audio_duration：根据路径返回不同长度（区分 pre vs post）
        from pipeline import tts as _tts_mod
        def fake_dur(path):
            if path.endswith(".speedup.mp3"):
                return speedup_dur if speedup_dur is not None else audio_dur
            return audio_dur
        monkeypatch.setattr(_tts_mod, "_get_audio_duration", fake_dur)

        # 评估调用全部 stub 成 noop（测的是分支，不是评估）
        called = {"eval": []}
        monkeypatch.setattr(
            "appcore.tts_speedup_eval.run_evaluation",
            lambda **kw: (called["eval"].append(kw) or 999),
        )
        # tempo align 简化为 identity（不真的走 ffmpeg）
        from appcore.runtime._pipeline_runner import PipelineRunner
        monkeypatch.setattr(
            PipelineRunner, "_maybe_tempo_align",
            lambda self, **kw: kw["audio_path"],
        )
        return called

    def _run(self, runner, tmp_path, video_duration=60.0):
        """触发 _run_tts_duration_loop 的简化入口。"""
        import importlib
        loc_mod = importlib.import_module("pipeline.localization")
        # 必备的最小输入 —— 实际测试需配合现有 fixture _disable_tts_language_guard
        return runner._run_tts_duration_loop(
            task_id="t-speedup",
            task_dir=str(tmp_path),
            loc_mod=loc_mod,
            provider="openrouter",
            video_duration=video_duration,
            voice={"elevenlabs_voice_id": "v-fake"},
            initial_localized_translation={
                "full_text": "x", "sentences": [{"text": "x"}], "_usage": {},
            },
            source_full_text="x",
            source_language="en",
            elevenlabs_api_key="fake",
            script_segments=[{"index": 0, "text": "x", "start": 0, "end": 1}],
            variant="normal",
            target_language_label="es",
            tts_model_id="eleven_turbo_v2_5",
            tts_language_code="es",
        )

    def test_speedup_triggered_when_audio_in_window(self, tmp_path, monkeypatch):
        """video=60, audio=64 (in stage1, not in final) → 触发变速。"""
        called = self._common_patches(monkeypatch, audio_dur=64.0,
                                       speedup_dur=60.5)
        runner = self._make_runner()
        result = self._run(runner, tmp_path, video_duration=60.0)
        assert result["final_round"] == 1
        round_rec = result["rounds"][0]
        assert round_rec.get("speedup_applied") is True
        assert round_rec["speedup_pre_duration"] == 64.0
        assert round_rec["speedup_post_duration"] == 60.5
        assert round_rec["speedup_hit_final"] is True
        assert round_rec["final_reason"] == "speedup_converged"
        assert len(called["eval"]) == 1

    def test_speedup_miss_final_uses_atempo(self, tmp_path, monkeypatch):
        """变速后 65s 仍 > final_hi=62 → 走 speedup_then_atempo。"""
        called = self._common_patches(monkeypatch, audio_dur=64.0,
                                       speedup_dur=63.0)
        runner = self._make_runner()
        result = self._run(runner, tmp_path, video_duration=60.0)
        round_rec = result["rounds"][0]
        assert round_rec.get("speedup_applied") is True
        assert round_rec["speedup_hit_final"] is False
        assert round_rec["final_reason"] == "speedup_then_atempo"
        assert len(called["eval"]) == 1  # 仍然评估

    def test_speedup_failure_falls_back_to_original(self, tmp_path, monkeypatch):
        """ElevenLabs 变速调用抛错 → 用原始音频 atempo 收敛 + 不评估。"""
        called = self._common_patches(
            monkeypatch, audio_dur=64.0,
            speedup_raises=RuntimeError("simulated SSL EOF"),
        )
        runner = self._make_runner()
        result = self._run(runner, tmp_path, video_duration=60.0)
        round_rec = result["rounds"][0]
        assert round_rec.get("speedup_applied") is True
        assert "speedup_failed_reason" in round_rec
        assert round_rec["final_reason"] == "speedup_failed_fallback"
        assert called["eval"] == []  # 变速失败不发起评估

    def test_speedup_skipped_when_audio_already_in_final(self, tmp_path, monkeypatch):
        """audio=60.5 已在 final [59,62] → 不触发变速分支。"""
        called = self._common_patches(monkeypatch, audio_dur=60.5)
        runner = self._make_runner()
        result = self._run(runner, tmp_path, video_duration=60.0)
        round_rec = result["rounds"][0]
        assert not round_rec.get("speedup_applied")
        assert called["eval"] == []
```

- [ ] **Step 2: 跑测试看通过**

Run:
```powershell
pytest tests/test_tts_duration_loop.py::TestSpeedupShortcut -v
```
Expected: 4 个用例全 PASS。

如果某个用例失败，先确认：
1. `_common_patches` 里的 `monkeypatch` 路径是否拼对了（`pipeline.tts.regenerate_full_audio_with_speed` vs `appcore.runtime._pipeline_runner.regenerate_full_audio_with_speed` —— 取决于代码里是 `from pipeline.tts import regenerate_full_audio_with_speed` 还是 `import pipeline.tts; pipeline.tts.regenerate_full_audio_with_speed(...)`）。Task 6 里写的是函数内 `from pipeline.tts import ...`，所以 monkeypatch 要 patch 模块属性 `pipeline.tts.regenerate_full_audio_with_speed`，这是上面写的。
2. `_maybe_tempo_align` 需要从 `appcore.runtime._pipeline_runner.PipelineRunner` 类上 patch。

- [ ] **Step 3: Commit**

```bash
git -C .worktrees/tts-speedup-shortcut add tests/test_tts_duration_loop.py
git -C .worktrees/tts-speedup-shortcut commit -m "$(cat <<'EOF'
test(runtime): 新增变速短路分支集成测试

覆盖：触发条件 / 命中 final / 未命中走 atempo / 变速失败回退 /
audio 已在 final 时不触发 共四个分支。配合 Task 6 的实现做 TDD 验证。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Web 路由 `/admin/tts-speedup-evaluations`

**Files:**
- Create: `web/routes/tts_speedup_eval.py`
- Modify: `web/app.py`（注册 blueprint）
- Test: `tests/test_admin_tts_speedup_eval_routes.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_admin_tts_speedup_eval_routes.py`：

```python
"""Admin /admin/tts-speedup-evaluations 路由测试。
覆盖：列表页渲染 / 重跑接口 / CSV 导出。
"""
from unittest.mock import patch, MagicMock
import pytest

# 复用项目已有的 admin login fixture（参考 tests/test_web_routes.py）
@pytest.fixture
def admin_client():
    from web.app import create_app
    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    client = app.test_client()
    # 简化：直接 patch login_required + admin_required 通过
    return client, app


def test_list_page_renders_with_filters(admin_client):
    client, app = admin_client
    fake_rows = [
        {
            "id": 1, "task_id": "t-aaa", "round_index": 2, "language": "es",
            "video_duration": 60.0, "audio_pre_duration": 64.0,
            "audio_post_duration": 60.5, "speed_ratio": 1.0667,
            "hit_final_range": 1, "score_overall": 4, "score_naturalness": 4,
            "score_pacing": 3, "score_timbre": 5, "score_intelligibility": 5,
            "summary_text": "ok", "flags_json": "[]",
            "model_provider": "openrouter",
            "model_id": "google/gemini-3-flash-preview",
            "llm_cost_usd": 0.012, "status": "ok",
            "created_at": "2026-05-04 10:00:00",
        },
    ]
    with patch("web.routes.tts_speedup_eval._fetch_rows", return_value=fake_rows), \
         patch("web.routes.tts_speedup_eval._fetch_summary", return_value={
              "total": 1, "hit_final_pct": 100.0, "avg_overall": 4.0,
              "top_flags": [],
         }), \
         patch("flask_login.utils._get_user", return_value=MagicMock(
              is_authenticated=True, role="admin", id=1, username="t",
         )):
        resp = client.get("/admin/tts-speedup-evaluations")
    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    assert "tts_speedup_evaluations" in body or "变速短路" in body
    assert "t-aaa" in body
    assert "1.0667" in body or "1.07" in body


def test_retry_endpoint_calls_orchestrator(admin_client):
    client, app = admin_client
    with patch("appcore.tts_speedup_eval.retry_evaluation",
                return_value=True) as fake_retry, \
         patch("flask_login.utils._get_user", return_value=MagicMock(
              is_authenticated=True, role="admin", id=1, username="t",
         )):
        resp = client.post("/admin/tts-speedup-evaluations/42/retry")
    assert resp.status_code in (200, 302)
    fake_retry.assert_called_once()
    assert fake_retry.call_args.kwargs["eval_id"] == 42


def test_export_csv_returns_csv_content(admin_client):
    client, app = admin_client
    fake_rows = [
        {
            "id": 1, "task_id": "t-aaa", "round_index": 2, "language": "es",
            "video_duration": 60.0, "audio_pre_duration": 64.0,
            "audio_post_duration": 60.5, "speed_ratio": 1.0667,
            "hit_final_range": 1, "score_overall": 4, "score_naturalness": 4,
            "score_pacing": 3, "score_timbre": 5, "score_intelligibility": 5,
            "summary_text": "ok", "flags_json": "[]",
            "model_provider": "openrouter",
            "model_id": "google/gemini-3-flash-preview",
            "llm_cost_usd": 0.012, "status": "ok",
            "created_at": "2026-05-04 10:00:00",
        },
    ]
    with patch("web.routes.tts_speedup_eval._fetch_rows", return_value=fake_rows), \
         patch("flask_login.utils._get_user", return_value=MagicMock(
              is_authenticated=True, role="admin", id=1, username="t",
         )):
        resp = client.get("/admin/tts-speedup-evaluations.csv")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers.get("Content-Type", "")
    body = resp.data.decode("utf-8-sig")
    assert "task_id" in body
    assert "t-aaa" in body
```

- [ ] **Step 2: 跑测试看失败**

Run:
```powershell
pytest tests/test_admin_tts_speedup_eval_routes.py -v
```
Expected: 全部 FAIL — `404` / `ModuleNotFoundError`。

- [ ] **Step 3: 实现 routes**

新建 `web/routes/tts_speedup_eval.py`：

```python
"""Admin: TTS 变速短路 AI 评估跨任务查询页 + 重跑 + CSV 导出。"""
from __future__ import annotations

import csv
import io
import json
from typing import Any

from flask import Blueprint, render_template, request, jsonify, redirect, url_for, Response, flash
from flask_login import login_required, current_user

from web.auth import admin_required
from appcore.db import query as db_query, query_one as db_query_one
from appcore import tts_speedup_eval

bp = Blueprint("tts_speedup_eval", __name__,
               url_prefix="/admin/tts-speedup-evaluations")


_LIST_SQL = """
  SELECT id, task_id, round_index, language,
         video_duration, audio_pre_duration, audio_post_duration,
         speed_ratio, hit_final_range,
         score_naturalness, score_pacing, score_timbre,
         score_intelligibility, score_overall,
         summary_text, flags_json,
         model_provider, model_id, llm_input_tokens, llm_output_tokens,
         llm_cost_usd, status, error_text,
         audio_pre_path, audio_post_path,
         created_at, evaluated_at
    FROM tts_speedup_evaluations
   {where}
   ORDER BY created_at DESC
   LIMIT %s OFFSET %s
"""


def _build_where(args) -> tuple[str, list]:
    clauses = []
    params: list = []
    lang = (args.get("language") or "").strip()
    if lang:
        clauses.append("language = %s")
        params.append(lang)
    status = (args.get("status") or "").strip()
    if status in ("ok", "failed", "pending"):
        clauses.append("status = %s")
        params.append(status)
    hit = args.get("hit_final")
    if hit in ("0", "1"):
        clauses.append("hit_final_range = %s")
        params.append(int(hit))
    min_overall = args.get("min_overall")
    if min_overall and min_overall.isdigit():
        clauses.append("score_overall >= %s")
        params.append(int(min_overall))
    return ("WHERE " + " AND ".join(clauses)) if clauses else "", params


def _fetch_rows(args, *, limit: int = 200, offset: int = 0) -> list[dict]:
    where, params = _build_where(args)
    sql = _LIST_SQL.format(where=where)
    return db_query(sql, (*params, limit, offset))


def _fetch_summary(args) -> dict:
    where, params = _build_where(args)
    total_row = db_query_one(
        f"SELECT COUNT(*) AS n, AVG(score_overall) AS avg_overall, "
        f"  SUM(hit_final_range) AS hits "
        f"FROM tts_speedup_evaluations {where}",
        tuple(params),
    ) or {"n": 0, "avg_overall": None, "hits": 0}
    n = int(total_row.get("n") or 0)
    hits = int(total_row.get("hits") or 0)
    avg_overall = float(total_row["avg_overall"]) if total_row.get("avg_overall") else 0.0
    # top flags：简化版本，扫前 500 行 flags_json 聚合
    flag_rows = db_query(
        f"SELECT flags_json FROM tts_speedup_evaluations {where} "
        f"ORDER BY created_at DESC LIMIT 500",
        tuple(params),
    )
    counts: dict[str, int] = {}
    for r in flag_rows:
        raw = r.get("flags_json")
        if not raw:
            continue
        try:
            tags = json.loads(raw) if isinstance(raw, str) else (raw or [])
        except Exception:
            tags = []
        for t in tags:
            counts[t] = counts.get(t, 0) + 1
    top = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:5]
    return {
        "total": n,
        "hit_final_pct": round(hits / n * 100, 1) if n else 0.0,
        "avg_overall": round(avg_overall, 2),
        "top_flags": [{"flag": k, "count": v} for k, v in top],
    }


@bp.route("/", methods=["GET"])
@login_required
@admin_required
def list_page():
    rows = _fetch_rows(request.args)
    summary = _fetch_summary(request.args)
    return render_template(
        "admin/tts_speedup_eval_list.html",
        rows=rows, summary=summary, args=request.args,
    )


@bp.route("/<int:eval_id>/retry", methods=["POST"])
@login_required
@admin_required
def retry_endpoint(eval_id: int):
    ok = tts_speedup_eval.retry_evaluation(
        eval_id=eval_id, user_id=current_user.id,
    )
    if request.is_json or request.headers.get("Accept", "").startswith("application/json"):
        return jsonify({"ok": ok, "eval_id": eval_id})
    flash("评估已重跑" if ok else "评估重跑失败，请查看 error_text", "info")
    return redirect(url_for("tts_speedup_eval.list_page"))


@bp.route(".csv", methods=["GET"])
@login_required
@admin_required
def export_csv():
    rows = _fetch_rows(request.args, limit=10000)
    buf = io.StringIO()
    fieldnames = [
        "id", "created_at", "task_id", "round_index", "language",
        "video_duration", "audio_pre_duration", "audio_post_duration",
        "speed_ratio", "hit_final_range",
        "score_overall", "score_naturalness", "score_pacing",
        "score_timbre", "score_intelligibility",
        "summary_text", "flags_json",
        "model_provider", "model_id",
        "llm_input_tokens", "llm_output_tokens", "llm_cost_usd",
        "status", "error_text",
    ]
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow({k: r.get(k) for k in fieldnames})
    csv_text = buf.getvalue()
    return Response(
        csv_text.encode("utf-8-sig"),
        mimetype="text/csv; charset=utf-8",
        headers={
            "Content-Disposition":
                'attachment; filename="tts_speedup_evaluations.csv"',
        },
    )
```

- [ ] **Step 4: 注册 blueprint**

打开 `web/app.py`，找到现有的 `app.register_blueprint(admin_usage_bp)` 那一段（约第 243 行）。在该段附近找一处合适位置追加：

```python
    from web.routes.tts_speedup_eval import bp as tts_speedup_eval_bp
    app.register_blueprint(tts_speedup_eval_bp)
```

（参考其他 blueprint 在 `create_app()` 里的注册模式。如果该文件用了顶层 import 风格而不是函数内 import，按现有风格走。）

- [ ] **Step 5: 跑测试看通过**

Run:
```powershell
pytest tests/test_admin_tts_speedup_eval_routes.py -v
```
Expected: 3 个用例全 PASS。

> 如果模板渲染测试失败因为模板还没建，先临时把 `render_template` 替换为 `return jsonify(...)` 让测试过；下个 Task 9 再建模板就把它改回来。或者先在 Task 9 建模板，再跑这个测试。两种顺序都可以。

- [ ] **Step 6: Commit**

```bash
git -C .worktrees/tts-speedup-shortcut add web/routes/tts_speedup_eval.py web/app.py tests/test_admin_tts_speedup_eval_routes.py
git -C .worktrees/tts-speedup-shortcut commit -m "$(cat <<'EOF'
feat(web): 加 /admin/tts-speedup-evaluations 列表/重跑/CSV 路由

跨任务查询变速短路 AI 评估样本，可按语种/命中状态/整体分阈值/时间筛选，
点重跑触发 tts_speedup_eval.retry_evaluation，CSV 导出供离线分析。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: 任务详情页变速卡片渲染

**Files:**
- Modify: `web/templates/_task_workbench_scripts.html`
- Modify: `web/templates/_task_workbench_styles.html`

> Task 9 的代码细节比较多，**实现前先 Read 一遍现有 `_task_workbench_scripts.html`** 找到 multi-translate TTS 卡片的轮次渲染位置（一般是 round 卡片的 measure 信息块下方），把变速卡片插在 round_record 对象 **`speedup_applied === true`** 时。

- [ ] **Step 1: 阅读现有 round 渲染**

```powershell
git -C .worktrees/tts-speedup-shortcut grep -n "speedup\|tempo_applied\|audio_segments_done" web/templates/_task_workbench_scripts.html | head -30
```

锁定一个现有 round_record 字段（如 `tempo_applied`）的渲染位置作为参考锚点。

- [ ] **Step 2: 在 `_task_workbench_styles.html` 加样式**

在文件末尾（最后一个 `</style>` 前）追加：

```html
<style>
  .tts-speedup-card {
    margin-top: var(--space-4);
    padding: var(--space-4);
    background: var(--bg-subtle);
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
  }
  .tts-speedup-header {
    display: flex; align-items: center; gap: var(--space-3);
    font-size: var(--text-sm); color: var(--fg);
    margin-bottom: var(--space-3);
  }
  .tts-speedup-header .tag {
    font-family: var(--font-mono); padding: 2px 8px;
    border-radius: var(--radius-md); background: var(--accent-subtle);
    color: var(--accent); font-size: var(--text-xs);
  }
  .tts-speedup-players { display: flex; gap: var(--space-4); flex-wrap: wrap; }
  .tts-speedup-players audio { width: min(380px, 100%); }
  .tts-speedup-scores {
    display: grid; grid-template-columns: repeat(5, 1fr);
    gap: var(--space-3); margin-top: var(--space-3);
  }
  .tts-speedup-score-item { font-size: var(--text-xs); color: var(--fg-muted); }
  .tts-speedup-score-bar {
    height: 6px; border-radius: 3px; background: var(--bg-muted);
    margin-top: 4px; overflow: hidden;
  }
  .tts-speedup-score-bar > span {
    display: block; height: 100%; background: var(--chart-1);
  }
  .tts-speedup-summary {
    margin-top: var(--space-3); font-size: var(--text-sm);
    line-height: var(--leading);
  }
  .tts-speedup-flags { margin-top: var(--space-2); }
  .tts-speedup-flags .flag-chip {
    display: inline-block; margin-right: 6px; padding: 2px 8px;
    background: var(--bg-muted); border: 1px solid var(--border);
    border-radius: var(--radius-md); font-size: var(--text-xs);
    font-family: var(--font-mono); color: var(--fg-muted);
  }
  .tts-speedup-status-pending { color: var(--fg-muted); }
  .tts-speedup-status-failed { color: var(--danger); }
  .tts-speedup-retry-btn {
    margin-left: auto; height: 28px; padding: 0 12px;
    border: 1px solid var(--border-strong); background: white;
    border-radius: var(--radius); font-size: var(--text-xs);
    cursor: pointer;
  }
  .tts-speedup-retry-btn:hover { background: var(--bg-muted); }
</style>
```

- [ ] **Step 3: 在 `_task_workbench_scripts.html` 加 JS 渲染**

找到现有 round_record 渲染区（参考 `tempo_applied` 锚点）。在其同级位置追加（在轮次卡片 HTML 串拼接里）：

```javascript
// 假设 round_record 在 JS 中叫 r
function renderSpeedupCard(r, taskId) {
  if (!r.speedup_applied) return '';
  const speedFmt = (r.speedup_speed != null) ? r.speedup_speed.toFixed(4) : '?';
  const preDur = r.speedup_pre_duration?.toFixed(2) ?? '?';
  const postDur = r.speedup_post_duration?.toFixed(2) ?? '?';
  const hit = r.speedup_hit_final ? '<span style="color:var(--success-fg)">✓ 命中 [v-1, v+2]</span>'
                                  : '<span style="color:var(--warning-fg)">⚠ 未命中 final</span>';

  let content = `
    <div class="tts-speedup-card">
      <div class="tts-speedup-header">
        <strong>变速短路</strong>
        <span class="tag">speed=${speedFmt}</span>
        <span>${preDur}s → ${postDur}s</span>
        <span>${hit}</span>
      </div>
  `;

  if (r.speedup_failed_reason) {
    content += `<div class="tts-speedup-summary tts-speedup-status-failed">变速调用失败：${escapeHtml(r.speedup_failed_reason)}（已回退原始音频 + atempo）</div>`;
    content += `</div>`;
    return content;
  }

  // 双轨播放器：用现有 artifact 路由读 task_dir 里的相对路径
  const preUrl = `/tasks/${encodeURIComponent(taskId)}/artifact?path=${encodeURIComponent('tts_full.round_' + r.round + '.mp3')}`;
  const postUrl = `/tasks/${encodeURIComponent(taskId)}/artifact?path=${encodeURIComponent(r.speedup_audio_path || '')}`;
  content += `
      <div class="tts-speedup-players">
        <div><div style="font-size:var(--text-xs);color:var(--fg-muted)">变速前 ${preDur}s</div>
             <audio controls preload="none" src="${preUrl}"></audio></div>
        <div><div style="font-size:var(--text-xs);color:var(--fg-muted)">变速后 ${postDur}s</div>
             <audio controls preload="none" src="${postUrl}"></audio></div>
      </div>
  `;

  // 评估卡片：通过 r.speedup_eval_id 现拉一次评估状态（异步）。
  // 简化：直接在 JS 渲染 placeholder，evaluation 详情在跨任务页查看。
  if (r.speedup_eval_id) {
    content += `
      <div class="tts-speedup-summary">
        AI 评估已生成（eval_id=${r.speedup_eval_id}），完整评分见
        <a href="/admin/tts-speedup-evaluations?task_id=${encodeURIComponent(taskId)}" target="_blank">跨任务评估页</a>。
        <button class="tts-speedup-retry-btn" data-eval-id="${r.speedup_eval_id}">重新评估</button>
      </div>
    `;
  } else {
    content += `<div class="tts-speedup-summary tts-speedup-status-pending">AI 评估未发起</div>`;
  }

  content += `</div>`;
  return content;
}
```

把 `renderSpeedupCard(r, taskId)` 的返回字符串拼接到现有 round 卡片 HTML 串里（在 `tempo_applied` 状态行之后位置）。

同时绑定重跑按钮事件（在 round 卡片绑定其他按钮的统一位置，或文档级 delegation）：

```javascript
document.addEventListener('click', async (e) => {
  const btn = e.target.closest('.tts-speedup-retry-btn');
  if (!btn) return;
  const evalId = btn.dataset.evalId;
  if (!evalId) return;
  btn.disabled = true; btn.textContent = '评估中…';
  try {
    const resp = await fetch(`/admin/tts-speedup-evaluations/${evalId}/retry`,
      { method: 'POST', headers: { 'Accept': 'application/json' } });
    if (resp.ok) { btn.textContent = '已发起'; }
    else { btn.textContent = '失败，请重试'; btn.disabled = false; }
  } catch (err) {
    btn.textContent = '网络错误'; btn.disabled = false;
  }
});
```

> 上面 `escapeHtml` 函数本文件应该已经存在，复用；如果没有，在文件顶部 utility 块补一个：
> ```javascript
> function escapeHtml(s) {
>   return String(s ?? '').replace(/[&<>"']/g, m => ({
>     '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
> }
> ```

- [ ] **Step 4: 启动 dev server 自检**

Run（在 worktree 根目录）：
```powershell
python web/app.py
```

打开浏览器访问 `http://127.0.0.1:5000/`（或项目实际端口）。需要一个真的跑过变速分支的 task 才能看到卡片 —— 用户会自己跑。我们至少要保证：
- 现有任务页**不会因为新代码 JS 报错**（打开 DevTools Console 应无 SyntaxError / TypeError）
- 现有不变速的任务渲染不受影响

如果没有真实任务可触发，可以临时在浏览器 console 注入一个 fake round_record：
```javascript
// DevTools Console
const fake = { round: 2, speedup_applied: true, speedup_speed: 1.0667,
  speedup_pre_duration: 64.0, speedup_post_duration: 60.5,
  speedup_hit_final: true, speedup_audio_path: 'tts_full.round_2.speedup.mp3',
  speedup_eval_id: 42 };
console.log(renderSpeedupCard(fake, 'fake-task'));
```
预期：返回的 HTML 包含 `tts-speedup-card` 和 `1.0667`。

- [ ] **Step 5: Commit**

```bash
git -C .worktrees/tts-speedup-shortcut add web/templates/_task_workbench_scripts.html web/templates/_task_workbench_styles.html
git -C .worktrees/tts-speedup-shortcut commit -m "$(cat <<'EOF'
feat(ui): 任务详情页加 TTS 变速短路卡片

显示 speed ratio、变速前后双轨播放器、是否命中 final、AI 评估状态 +
重新评估按钮。Ocean Blue 设计 token 化样式，无紫色 hue 越界。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: 跨任务评分查询页模板 + 导航

**Files:**
- Create: `web/templates/admin/tts_speedup_eval_list.html`
- Modify: `web/templates/layout.html`（数据分析分组下加导航条目）

- [ ] **Step 1: 创建模板**

新建 `web/templates/admin/tts_speedup_eval_list.html`：

```html
{% extends "layout.html" %}
{% block title %}TTS 变速短路评估{% endblock %}
{% block content %}
<div class="page-header" style="margin-bottom: var(--space-6)">
  <h1 style="font-size: var(--text-2xl); margin: 0;">TTS 变速短路 AI 评估</h1>
  <p style="color: var(--fg-muted); margin: var(--space-2) 0 0 0;">
    长视频 TTS 时长收敛短路样本汇总 — 用于评估变速短路是否应在生产保留。
  </p>
</div>

<!-- 概览卡片 -->
<div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: var(--space-4); margin-bottom: var(--space-6);">
  <div class="kpi-card" style="padding:var(--space-4); background:white; border:1px solid var(--border); border-radius:var(--radius-lg);">
    <div style="color:var(--fg-muted); font-size:var(--text-xs);">总样本</div>
    <div style="font-size:var(--text-2xl); font-family:var(--font-mono);">{{ summary.total }}</div>
  </div>
  <div class="kpi-card" style="padding:var(--space-4); background:white; border:1px solid var(--border); border-radius:var(--radius-lg);">
    <div style="color:var(--fg-muted); font-size:var(--text-xs);">命中 final 比例</div>
    <div style="font-size:var(--text-2xl); font-family:var(--font-mono);">{{ summary.hit_final_pct }}%</div>
  </div>
  <div class="kpi-card" style="padding:var(--space-4); background:white; border:1px solid var(--border); border-radius:var(--radius-lg);">
    <div style="color:var(--fg-muted); font-size:var(--text-xs);">整体分均值</div>
    <div style="font-size:var(--text-2xl); font-family:var(--font-mono);">{{ summary.avg_overall }}</div>
  </div>
  <div class="kpi-card" style="padding:var(--space-4); background:white; border:1px solid var(--border); border-radius:var(--radius-lg);">
    <div style="color:var(--fg-muted); font-size:var(--text-xs);">高频问题点 top 5</div>
    <div style="font-size:var(--text-sm); margin-top:var(--space-2);">
      {% for f in summary.top_flags %}
        <span style="display:inline-block;margin-right:8px;padding:2px 8px;background:var(--bg-muted);border-radius:var(--radius-md);font-family:var(--font-mono);font-size:var(--text-xs);">{{ f.flag }} ({{ f.count }})</span>
      {% else %}
        <span style="color:var(--fg-subtle)">无</span>
      {% endfor %}
    </div>
  </div>
</div>

<!-- 筛选条 -->
<form method="GET" style="display:flex; gap:var(--space-3); margin-bottom: var(--space-4); flex-wrap:wrap;">
  <select name="language" style="height:32px; padding:0 8px; border:1px solid var(--border-strong); border-radius:var(--radius);">
    <option value="">全部语言</option>
    {% for code in ['en','es','de','fr','pt','it','nl','sv','fi','ja','ko'] %}
      <option value="{{ code }}" {% if args.language == code %}selected{% endif %}>{{ code }}</option>
    {% endfor %}
  </select>
  <select name="status" style="height:32px; padding:0 8px; border:1px solid var(--border-strong); border-radius:var(--radius);">
    <option value="">所有状态</option>
    <option value="ok" {% if args.status == 'ok' %}selected{% endif %}>ok</option>
    <option value="failed" {% if args.status == 'failed' %}selected{% endif %}>failed</option>
    <option value="pending" {% if args.status == 'pending' %}selected{% endif %}>pending</option>
  </select>
  <select name="hit_final" style="height:32px; padding:0 8px; border:1px solid var(--border-strong); border-radius:var(--radius);">
    <option value="">命中?</option>
    <option value="1" {% if args.hit_final == '1' %}selected{% endif %}>命中 final</option>
    <option value="0" {% if args.hit_final == '0' %}selected{% endif %}>未命中</option>
  </select>
  <input name="min_overall" placeholder="整体分 ≥" value="{{ args.min_overall or '' }}"
         style="height:32px; width:120px; padding:0 8px; border:1px solid var(--border-strong); border-radius:var(--radius);">
  <button type="submit" style="height:32px; padding:0 16px; background:var(--accent); color:white; border:0; border-radius:var(--radius);">筛选</button>
  <a href="{{ url_for('tts_speedup_eval.export_csv') }}?{{ request.query_string.decode() }}"
     style="height:32px; line-height:32px; padding:0 16px; background:white; border:1px solid var(--border-strong); border-radius:var(--radius); color:var(--fg);">导出 CSV</a>
</form>

<!-- 列表 -->
<div style="background:white; border:1px solid var(--border); border-radius:var(--radius-lg); overflow:hidden;">
  <table style="width:100%; border-collapse:collapse;">
    <thead style="background:var(--bg-subtle);">
      <tr>
        {% for h in ['创建时间','任务','语种','speed','变速前/后','命中?','整体分','5维','flags','状态','成本','操作'] %}
          <th style="padding:10px 12px; text-align:left; font-size:var(--text-xs); color:var(--fg-muted); font-weight:500; border-bottom:1px solid var(--border);">{{ h }}</th>
        {% endfor %}
      </tr>
    </thead>
    <tbody>
      {% for r in rows %}
      <tr>
        <td style="padding:8px 12px; font-family:var(--font-mono); font-size:var(--text-xs);">{{ r.created_at }}</td>
        <td style="padding:8px 12px; font-family:var(--font-mono); font-size:var(--text-xs);"><a href="/multi-translate/{{ r.task_id }}" target="_blank">{{ r.task_id[:12] }}…</a></td>
        <td style="padding:8px 12px;">{{ r.language }}</td>
        <td style="padding:8px 12px; font-family:var(--font-mono);">{{ "%.4f"|format(r.speed_ratio|float) }}</td>
        <td style="padding:8px 12px; font-family:var(--font-mono); font-size:var(--text-xs);">{{ "%.2f"|format(r.audio_pre_duration|float) }}s → {{ "%.2f"|format(r.audio_post_duration|float) }}s</td>
        <td style="padding:8px 12px;">
          {% if r.hit_final_range %}<span style="color:var(--success-fg)">✓</span>{% else %}<span style="color:var(--warning-fg)">⚠</span>{% endif %}
        </td>
        <td style="padding:8px 12px; font-family:var(--font-mono); font-weight:600;">{{ r.score_overall or '-' }}</td>
        <td style="padding:8px 12px; font-family:var(--font-mono); font-size:var(--text-xs);">
          N{{ r.score_naturalness or '-' }} / P{{ r.score_pacing or '-' }} / T{{ r.score_timbre or '-' }} / I{{ r.score_intelligibility or '-' }}
        </td>
        <td style="padding:8px 12px; font-size:var(--text-xs);">{{ r.flags_json or '[]' }}</td>
        <td style="padding:8px 12px;">
          {% if r.status == 'ok' %}<span style="color:var(--success-fg)">ok</span>
          {% elif r.status == 'failed' %}<span style="color:var(--danger)">failed</span>
          {% else %}<span style="color:var(--fg-muted)">pending</span>{% endif %}
        </td>
        <td style="padding:8px 12px; font-family:var(--font-mono); font-size:var(--text-xs);">${{ r.llm_cost_usd or '0' }}</td>
        <td style="padding:8px 12px;">
          <form method="POST" action="{{ url_for('tts_speedup_eval.retry_endpoint', eval_id=r.id) }}" style="display:inline;">
            <button type="submit" style="height:28px; padding:0 8px; background:white; border:1px solid var(--border-strong); border-radius:var(--radius); font-size:var(--text-xs); cursor:pointer;">重跑</button>
          </form>
        </td>
      </tr>
      {% else %}
      <tr><td colspan="12" style="padding:24px; text-align:center; color:var(--fg-subtle);">暂无样本</td></tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% endblock %}
```

- [ ] **Step 2: 在 `layout.html` 导航加入口**

打开 `web/templates/layout.html`，找到现有的 "数据分析" 导航分组（搜 `productivity_stats` 或 `数据分析`）。在该分组的 `<a>` 列表里追加（按现有行格式）：

```html
{% if current_user.is_authenticated and current_user.role in ('admin', 'superadmin') %}
<a href="{{ url_for('tts_speedup_eval.list_page') }}"
   class="nav-item {% if request.endpoint and request.endpoint.startswith('tts_speedup_eval.') %}active{% endif %}">
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
    <path d="M9 4v16M15 4v16M3 8h18M3 16h18"/>
  </svg>
  TTS 变速评估
</a>
{% endif %}
```

> 实际位置和样式以 layout.html 现有 nav-item 模式为准。如果导航是 JS 渲染的，参考其他菜单项加法。

- [ ] **Step 3: 浏览器自检**

```powershell
python web/app.py
```

访问 `http://127.0.0.1:5000/admin/tts-speedup-evaluations`（admin 用户登录态下）。预期：
- 页面成功渲染
- KPI 卡片 4 个
- 筛选表单可提交
- "暂无样本"或显示已有数据

也跑一遍前面的路由测试确认模板渲染不爆：
```powershell
pytest tests/test_admin_tts_speedup_eval_routes.py -v
```
Expected: 全 PASS。

- [ ] **Step 4: Commit**

```bash
git -C .worktrees/tts-speedup-shortcut add web/templates/admin/tts_speedup_eval_list.html web/templates/layout.html
git -C .worktrees/tts-speedup-shortcut commit -m "$(cat <<'EOF'
feat(ui): /admin/tts-speedup-evaluations 列表页 + 导航入口

KPI 卡片（总样本 / 命中比例 / 整体分均值 / 高频问题点）+ 多维度筛选 +
列表 + CSV 导出 + 重跑按钮。Ocean Blue 风格 token 化。导航挂在数据
分析分组下，仅 admin 可见。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: 自检 + 文档更新

**Files:**
- Modify: `CLAUDE.md`（worktree 根，加一行变速短路 + 评估的运维注记）
- 跑全局测试 spot-check

- [ ] **Step 1: 全量 spot-check**

Run（在 worktree 根目录）：
```powershell
pytest tests/test_tts_duration_loop.py tests/test_tts_speedup_pipeline.py tests/test_tts_speedup_eval.py tests/test_admin_tts_speedup_eval_routes.py tests/test_llm_use_cases_registry.py -v
```
Expected: 所有用例 PASS。

- [ ] **Step 2: 跑一次现有 TTS 相关测试确认未回归**

```powershell
pytest tests/test_pipeline_runner.py tests/test_runtime_tts_stats_integration.py -v
```
Expected: 维持原状（不引入新 fail）。如果有 pre-existing fail，与 master 同步——参考 `docs/superpowers/notes/2026-04-21-pytest-baseline-failures.md`。

- [ ] **Step 3: 在 `CLAUDE.md` 末尾追加变速短路注记**

打开 worktree 根 `CLAUDE.md`，在末尾追加：

```markdown

## TTS Duration Loop 变速短路（2026-05-04）

- 当 multi-translate 任务某一轮 TTS 音频落入 `[0.9v, 1.1v]` 但不在 `[v-1, v+2]`，会**自动**用 ElevenLabs `voice_settings.speed` 重生成一遍音频试图直接收敛。命中即终结；未命中走 atempo 兜底；变速调用失败回退原始音频走 atempo。**任何分支都不再继续后续 rewrite 轮次**。
- 每次变速 pass 都会**同步**调用 `video_translate.tts_speedup_quality_review`（默认 OpenRouter + google/gemini-3-flash-preview）做双轨对比 AI 评分，120s 超时不阻塞任务。结果写入 `tts_speedup_evaluations` 表。
- admin 可在 `/admin/tts-speedup-evaluations` 跨任务查询样本，并在 `/settings?tab=bindings` 切换评估模型。
- 想下线该功能：把 `_in_speedup_window` 改为永远返回 False（或加 settings 开关）即可，不会破坏现有 5 轮 rewrite 主路径。
```

- [ ] **Step 4: Commit**

```bash
git -C .worktrees/tts-speedup-shortcut add CLAUDE.md
git -C .worktrees/tts-speedup-shortcut commit -m "$(cat <<'EOF'
docs: 加 TTS 变速短路 + AI 评估的运维注记

记录触发条件、AI 评估默认模型、跨任务查询入口、下线方法。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 5: 推送 worktree 分支（可选，本地保留也可）**

```bash
git -C .worktrees/tts-speedup-shortcut push -u origin feature/tts-speedup-shortcut
```

> 如果用户没说"发布 / 部署"，**不要**跑 `deploy/publish.sh`，只 push 分支。

---

## 自检（写完后回头扫）

- [x] **Spec coverage**：
  - 3.1 触发流程 → Task 6
  - 3.2 变速重生成 → Task 4
  - 3.3 round_record 字段 → Task 6（在分支里写入）
  - 3.4 use_case 注册 → Task 2
  - 3.5 数据库 → Task 1
  - 3.6 同步评估流水线 → Task 5 + Task 6
  - 3.7 UI（任务详情 + 跨任务查询）→ Task 9 + Task 10
  - 3.8 文件改动清单 → 全部 task
  - 4. 失败模式 → Task 6 / Task 5 单测覆盖
  - 5. 验收 → Task 7 集成测试 + Task 11 自检
  - 6. 上线判断 → Task 10 KPI + 顶部统计
- [x] **Placeholder scan**：所有步骤都有具体代码、命令、预期输出，无 TBD/TODO。
- [x] **Type consistency**：
  - `regenerate_full_audio_with_speed(... variant=, speed=, ...)` 跨 Task 4/6/7 一致
  - `tts_speedup_eval.run_evaluation(... task_id, round_index, language, video_duration, audio_pre_path, audio_pre_duration, audio_post_path, audio_post_duration, speed_ratio, hit_final_range, user_id)` 跨 Task 5/6 一致
  - DB 字段名跨 Task 1/5/8/10 一致
  - `_in_speedup_window` 签名 `(*, audio_duration, video_duration)` 跨 Task 3/6 一致

---

## 执行选择

**Plan complete and saved to `docs/superpowers/plans/2026-05-04-tts-speedup-shortcut.md`. Two execution options:**

**1. Subagent-Driven (recommended)** - 我每个 task 派一个 fresh subagent，task 间我做 review，迭代快。

**2. Inline Execution** - 在当前会话里一气呵成，按 task 检查点 batch 执行。

**选哪个？**
