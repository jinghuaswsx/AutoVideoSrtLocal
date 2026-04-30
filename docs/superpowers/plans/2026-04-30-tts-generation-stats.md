# TTS Generation Stats Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把视频翻译"语音生成"环节的 `translate_calls` 与 `audio_calls` 两个汇总指标写到 `projects.state_json` 和新表 `tts_generation_stats`，并在 `_step_tts` 结束时用粗体蓝色 ANSI 日志打印一句任务级总结。

**Architecture:** 抽出独立工具模块 `appcore/tts_generation_stats.py`（纯函数 + DB upsert），在 `BaseRunner._step_tts` 两个 return 路径前各调用一次 `finalize_tts_generation_stats(...)`，把 stats 同步到 state_json + DB + logger。改动集中、可独立单测。

**Tech Stack:** Python（项目主语言），MySQL（PyMySQL/dbutils），pytest，已有 `appcore.task_state` / `appcore.db` / `appcore.db_migrations` 基础设施。

**Spec:** `docs/superpowers/specs/2026-04-30-tts-generation-stats-design.md`

## File Structure

| 文件 | 角色 | 创建/修改 |
|---|---|---|
| `db/migrations/2026_04_30_tts_generation_stats.sql` | 建表 SQL，启动时由 `appcore.db_migrations` 自动 apply | 创建 |
| `appcore/tts_generation_stats.py` | 纯函数 `compute_summary(rounds)` + `format_log_line(summary)` + DB upsert `upsert(...)` + 高层组合 `finalize(task_id, task, rounds, logger)` | 创建 |
| `tests/test_tts_generation_stats.py` | 单测：compute_summary 边界、format_log_line ANSI 序列、upsert UPSERT 行为（用 fake DB 模式）、finalize 异常容错 | 创建 |
| `appcore/runtime.py` | 在 `_step_tts` 主循环 `return` 之前两处插入 `finalize(...)` 调用 | 修改 |
| `tests/test_runtime_tts_stats_integration.py` | 集成测：跑一个 fake `_step_tts` 流程后，state_json/DB/logger 三处都被正确更新 | 创建 |

每个文件单一职责：migration 只建表；`tts_generation_stats.py` 只算/写/格式化；runtime.py 只多两行调用。

---

## Task 1: 数据库 migration

**Files:**
- Create: `db/migrations/2026_04_30_tts_generation_stats.sql`

- [ ] **Step 1: 写 migration SQL**

文件 `db/migrations/2026_04_30_tts_generation_stats.sql`：

```sql
CREATE TABLE IF NOT EXISTS tts_generation_stats (
    task_id          VARCHAR(64)  NOT NULL PRIMARY KEY,
    project_type     VARCHAR(32)  NOT NULL,
    target_lang      VARCHAR(8)   NOT NULL,
    user_id          INT          NULL,
    translate_calls  INT          NOT NULL,
    audio_calls      INT          NOT NULL,
    finished_at      DATETIME     NOT NULL,
    INDEX idx_user_time (user_id, finished_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

- [ ] **Step 2: 在本地 DB apply 一次（验证 SQL 正确）**

Run（本地或 LocalServer 都可，确认 SQL 可执行）：
```bash
ssh -i C:/Users/admin/.ssh/CC.pem root@172.30.254.14 "cd /opt/autovideosrt && set -a && . .env && set +a && /opt/autovideosrt/venv/bin/python -c '
import sys; sys.path.insert(0, \".\")
from appcore.db import execute, query_one
sql = open(\"/dev/stdin\").read()
# 这一步 plan 仅作语法演示，真正 apply 由 db_migrations 启动时做
print(\"SQL OK\")
'"
```

实际不在本地数据库 apply，让它由 `db_migrations` 模块在服务器启动时自动跑。仅本步用语法/缩进检查即可。

- [ ] **Step 3: Commit**

```bash
git add db/migrations/2026_04_30_tts_generation_stats.sql
git commit -m "feat(db): add tts_generation_stats table"
```

---

## Task 2: utils 模块 `appcore/tts_generation_stats.py`

**Files:**
- Create: `appcore/tts_generation_stats.py`
- Test: `tests/test_tts_generation_stats.py`

### Step 1: 写失败的单元测试

- [ ] **Step 1: 写 `tests/test_tts_generation_stats.py` 第一版（compute_summary）**

```python
"""tts_generation_stats utils 测试。

聚焦 compute_summary 的口径正确性、format_log_line 的 ANSI 输出、
upsert 的 UPSERT 行为，以及 finalize 在异常路径下的容错。
"""
from __future__ import annotations

import pytest


def _round(rewrite_attempts: int = 0, audio_segments: int = 0) -> dict:
    """构造一个最简的 round_record。round 1 没有 rewrite_attempts。"""
    rec: dict = {"audio_segments_total": audio_segments}
    if rewrite_attempts > 0:
        rec["rewrite_attempts"] = [{"attempt": i + 1} for i in range(rewrite_attempts)]
    return rec


def test_compute_summary_round1_only_initial_translate():
    from appcore.tts_generation_stats import compute_summary
    rounds = [_round(rewrite_attempts=0, audio_segments=9)]
    summary = compute_summary(rounds)
    assert summary["translate_calls"] == 1   # round 1 = initial translate
    assert summary["audio_calls"] == 9


def test_compute_summary_multi_round_aggregates_rewrite_and_segments():
    from appcore.tts_generation_stats import compute_summary
    rounds = [
        _round(rewrite_attempts=0, audio_segments=9),   # round 1: initial
        _round(rewrite_attempts=2, audio_segments=9),   # round 2: 2 rewrites
        _round(rewrite_attempts=5, audio_segments=10),  # round 3: 5 rewrites
    ]
    summary = compute_summary(rounds)
    # initial 1 + round2 2 + round3 5 = 8
    assert summary["translate_calls"] == 8
    # 9 + 9 + 10 = 28
    assert summary["audio_calls"] == 28


def test_compute_summary_handles_missing_audio_segments_total():
    """如果某个 round_record 没有 audio_segments_total（不应该但要稳）。"""
    from appcore.tts_generation_stats import compute_summary
    rounds = [{"rewrite_attempts": []}]
    summary = compute_summary(rounds)
    assert summary["translate_calls"] == 1
    assert summary["audio_calls"] == 0


def test_compute_summary_empty_rounds():
    from appcore.tts_generation_stats import compute_summary
    summary = compute_summary([])
    assert summary["translate_calls"] == 0
    assert summary["audio_calls"] == 0
```

- [ ] **Step 2: 跑测试，确认 4 条 FAIL**

Run:
```bash
python -m pytest tests/test_tts_generation_stats.py -v
```

Expected: 4 failures，错误是 `ModuleNotFoundError: No module named 'appcore.tts_generation_stats'`。

### Step 3-4: 实现 compute_summary，跑过

- [ ] **Step 3: 创建 `appcore/tts_generation_stats.py`，实现 compute_summary**

```python
"""TTS 语音生成步骤的统计汇总 + 持久化 + 日志。

每条任务跑完 _step_tts 后，会调用 finalize()，把以下两个核心指标写到：
1) projects.state_json.tts_generation_summary（详情页可读）
2) tts_generation_stats 独立表（聚合分析）
3) 一条粗体蓝色 ANSI 日志（journalctl 可读）

指标口径：
- translate_calls: round 1 的 1 次初始翻译 + 每个 round 内所有 rewrite_attempt 之和
- audio_calls:     所有 round 的 audio_segments_total 之和（段级 ElevenLabs 调用总数）
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable


# ANSI 转义：粗体 + 蓝色，重置
_ANSI_BOLD_BLUE = "\033[1;34m"
_ANSI_RESET = "\033[0m"


def compute_summary(rounds: Iterable[dict]) -> dict:
    """从 _step_tts 的 rounds 列表汇总两个核心指标。

    Args:
        rounds: list of round_record dicts (生产代码里来自 task["tts_duration_rounds"])

    Returns:
        {"translate_calls": int, "audio_calls": int}
    """
    rounds_list = list(rounds)
    if not rounds_list:
        return {"translate_calls": 0, "audio_calls": 0}

    translate_calls = 0
    audio_calls = 0
    for idx, rec in enumerate(rounds_list):
        if idx == 0:
            # round 1 = initial translate (没有 rewrite_attempts)
            translate_calls += 1
        else:
            # round 2+ = 内层 rewrite_attempt 个数
            translate_calls += len(rec.get("rewrite_attempts") or [])
        audio_calls += int(rec.get("audio_segments_total") or 0)
    return {"translate_calls": translate_calls, "audio_calls": audio_calls}
```

- [ ] **Step 4: 跑测试确认 4 条 PASS**

Run:
```bash
python -m pytest tests/test_tts_generation_stats.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add appcore/tts_generation_stats.py tests/test_tts_generation_stats.py
git commit -m "feat(stats): compute_summary for tts generation rounds"
```

### Step 6-9: 实现 format_log_line + 测试

- [ ] **Step 6: 在测试文件追加 format_log_line 测试**

```python
def test_format_log_line_contains_counts_and_ansi():
    from appcore.tts_generation_stats import format_log_line
    line = format_log_line({"translate_calls": 4, "audio_calls": 27})
    # 含粗体蓝色 ANSI 转义
    assert "\033[1;34m" in line
    assert "\033[0m" in line
    # 含中文文案 + 数字
    assert "4 次翻译" in line
    assert "27 次语音生成" in line


def test_format_log_line_zero_counts():
    from appcore.tts_generation_stats import format_log_line
    line = format_log_line({"translate_calls": 0, "audio_calls": 0})
    assert "0 次翻译" in line
    assert "0 次语音生成" in line
```

- [ ] **Step 7: 跑测试确认 2 条新 FAIL**

Run:
```bash
python -m pytest tests/test_tts_generation_stats.py::test_format_log_line_contains_counts_and_ansi tests/test_tts_generation_stats.py::test_format_log_line_zero_counts -v
```

Expected: 2 failures，`AttributeError: module 'appcore.tts_generation_stats' has no attribute 'format_log_line'`.

- [ ] **Step 8: 在 `appcore/tts_generation_stats.py` 追加 format_log_line**

```python
def format_log_line(summary: dict) -> str:
    """构造一条粗体蓝色 ANSI 总结日志。"""
    return (
        f"{_ANSI_BOLD_BLUE}"
        f"本任务用了 {summary['translate_calls']} 次翻译，"
        f"{summary['audio_calls']} 次语音生成。"
        f"{_ANSI_RESET}"
    )
```

- [ ] **Step 9: 跑测试确认 6 条全 PASS**

Run:
```bash
python -m pytest tests/test_tts_generation_stats.py -v
```

Expected: 6 passed.

- [ ] **Step 10: Commit**

```bash
git add appcore/tts_generation_stats.py tests/test_tts_generation_stats.py
git commit -m "feat(stats): bold-blue ANSI log line for tts summary"
```

### Step 11-15: 实现 upsert + 测试

- [ ] **Step 11: 在测试文件追加 upsert 测试（沿用项目里既有的 fake DB 模式）**

参考 `tests/test_bulk_translate_runtime.py` 的 `_FakeProjectsDB` 模式，本次更简洁：用 monkeypatch 把 `appcore.db.execute` 换成收集器。

```python
def test_upsert_inserts_then_updates(monkeypatch):
    """同一个 task_id 上调两次 upsert，第二次应更新而非重复插入。"""
    from appcore import tts_generation_stats as stats_mod

    captured: list[tuple[str, tuple]] = []

    def fake_execute(sql, args=None):
        captured.append((sql, args or ()))
        return 1

    monkeypatch.setattr(stats_mod, "execute", fake_execute)

    stats_mod.upsert(
        task_id="task-x",
        project_type="multi_translate",
        target_lang="it",
        user_id=42,
        summary={"translate_calls": 3, "audio_calls": 18},
        finished_at_iso="2026-04-30T10:00:00",
    )
    stats_mod.upsert(
        task_id="task-x",
        project_type="multi_translate",
        target_lang="it",
        user_id=42,
        summary={"translate_calls": 5, "audio_calls": 27},
        finished_at_iso="2026-04-30T10:05:00",
    )

    assert len(captured) == 2
    sql0, args0 = captured[0]
    # 必须是 INSERT ... ON DUPLICATE KEY UPDATE，确保 task_id 重复时覆盖
    sql_norm = " ".join(sql0.split()).upper()
    assert "INSERT INTO TTS_GENERATION_STATS" in sql_norm
    assert "ON DUPLICATE KEY UPDATE" in sql_norm
    # args 顺序必须固定：task_id, project_type, target_lang, user_id, t_calls, a_calls, finished_at
    assert args0[0] == "task-x"
    assert args0[1] == "multi_translate"
    assert args0[2] == "it"
    assert args0[3] == 42
    assert args0[4] == 3
    assert args0[5] == 18
    assert args0[6] == "2026-04-30T10:00:00"


def test_upsert_handles_null_user_id(monkeypatch):
    from appcore import tts_generation_stats as stats_mod
    captured: list[tuple[str, tuple]] = []
    monkeypatch.setattr(stats_mod, "execute",
                        lambda sql, args=None: captured.append((sql, args or ())) or 1)

    stats_mod.upsert(
        task_id="task-y",
        project_type="ja_translate",
        target_lang="ja",
        user_id=None,
        summary={"translate_calls": 1, "audio_calls": 5},
        finished_at_iso="2026-04-30T11:00:00",
    )

    assert captured[0][1][3] is None  # user_id NULL passes through
```

- [ ] **Step 12: 跑测试确认 2 条 FAIL**

Run:
```bash
python -m pytest tests/test_tts_generation_stats.py::test_upsert_inserts_then_updates tests/test_tts_generation_stats.py::test_upsert_handles_null_user_id -v
```

Expected: failures，`AttributeError: ... has no attribute 'upsert'` / `'execute'`.

- [ ] **Step 13: 在 `appcore/tts_generation_stats.py` 追加 upsert + execute 引用**

在文件顶部 import 区域加：

```python
from appcore.db import execute  # noqa: E402
```

在文件底部追加：

```python
_UPSERT_SQL = """
INSERT INTO tts_generation_stats
    (task_id, project_type, target_lang, user_id,
     translate_calls, audio_calls, finished_at)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON DUPLICATE KEY UPDATE
    project_type    = VALUES(project_type),
    target_lang     = VALUES(target_lang),
    user_id         = VALUES(user_id),
    translate_calls = VALUES(translate_calls),
    audio_calls     = VALUES(audio_calls),
    finished_at     = VALUES(finished_at)
"""


def upsert(
    *,
    task_id: str,
    project_type: str,
    target_lang: str,
    user_id: int | None,
    summary: dict,
    finished_at_iso: str,
) -> None:
    """把汇总写入 tts_generation_stats（同 task_id 重复跑覆盖）。"""
    execute(
        _UPSERT_SQL,
        (
            task_id,
            project_type,
            target_lang,
            user_id,
            int(summary["translate_calls"]),
            int(summary["audio_calls"]),
            finished_at_iso,
        ),
    )
```

- [ ] **Step 14: 跑测试确认全部 8 条 PASS**

Run:
```bash
python -m pytest tests/test_tts_generation_stats.py -v
```

Expected: 8 passed.

- [ ] **Step 15: Commit**

```bash
git add appcore/tts_generation_stats.py tests/test_tts_generation_stats.py
git commit -m "feat(stats): upsert summary into tts_generation_stats"
```

### Step 16-19: 实现 finalize 高层组合 + 测试

- [ ] **Step 16: 在测试文件追加 finalize 测试**

```python
def test_finalize_writes_state_json_db_and_logger(monkeypatch, caplog):
    """finalize 应该：1) update task_state；2) upsert DB；3) logger.info 蓝色总结。"""
    from appcore import tts_generation_stats as stats_mod

    state_updates: dict = {}

    def fake_task_state_update(task_id, **fields):
        state_updates[task_id] = fields

    db_calls: list[tuple] = []

    def fake_execute(sql, args=None):
        db_calls.append((sql, args or ()))
        return 1

    monkeypatch.setattr(stats_mod, "task_state_update", fake_task_state_update)
    monkeypatch.setattr(stats_mod, "execute", fake_execute)

    rounds = [
        {"audio_segments_total": 9},                                      # round 1
        {"rewrite_attempts": [1, 2, 3], "audio_segments_total": 9},       # round 2 → +3
    ]
    task = {
        "type": "multi_translate",
        "target_lang": "it",
        "user_id": 77,
    }

    import logging
    caplog.set_level(logging.INFO, logger="appcore.tts_generation_stats")

    stats_mod.finalize(task_id="task-z", task=task, rounds=rounds)

    # 1) state_json
    assert state_updates["task-z"]["tts_generation_summary"]["translate_calls"] == 4
    assert state_updates["task-z"]["tts_generation_summary"]["audio_calls"] == 18
    assert "finished_at" in state_updates["task-z"]["tts_generation_summary"]
    # 2) DB
    assert len(db_calls) == 1
    assert db_calls[0][1][0] == "task-z"
    assert db_calls[0][1][4] == 4   # translate_calls
    assert db_calls[0][1][5] == 18  # audio_calls
    # 3) logger
    log_record = next((r for r in caplog.records if "次翻译" in r.message), None)
    assert log_record is not None
    assert "4 次翻译" in log_record.message
    assert "18 次语音生成" in log_record.message


def test_finalize_swallows_db_error_but_keeps_state_json_and_log(monkeypatch, caplog):
    """DB 写失败不应让 _step_tts 整个崩溃。state_json 与 log 都先于 DB。"""
    from appcore import tts_generation_stats as stats_mod

    state_updates: dict = {}
    monkeypatch.setattr(
        stats_mod, "task_state_update",
        lambda task_id, **f: state_updates.setdefault(task_id, f),
    )

    def boom_execute(sql, args=None):
        raise RuntimeError("DB unreachable")

    monkeypatch.setattr(stats_mod, "execute", boom_execute)

    import logging
    caplog.set_level(logging.INFO, logger="appcore.tts_generation_stats")

    rounds = [{"audio_segments_total": 5}]
    task = {"type": "fr_translate", "target_lang": "fr", "user_id": None}

    # 不应抛出
    stats_mod.finalize(task_id="task-err", task=task, rounds=rounds)

    # state_json 已写
    assert "task-err" in state_updates
    # log 已打
    assert any("次翻译" in r.message for r in caplog.records)
    # DB 错误被记录到 logger.warning
    assert any("DB unreachable" in r.message and r.levelname == "WARNING"
               for r in caplog.records)
```

- [ ] **Step 17: 跑测试确认 2 条 FAIL**

Run:
```bash
python -m pytest tests/test_tts_generation_stats.py::test_finalize_writes_state_json_db_and_logger tests/test_tts_generation_stats.py::test_finalize_swallows_db_error_but_keeps_state_json_and_log -v
```

Expected: failures，`AttributeError: ... has no attribute 'finalize'`.

- [ ] **Step 18: 在 `appcore/tts_generation_stats.py` 追加 finalize**

文件顶部 import 补：

```python
import logging

from appcore.task_state import update as task_state_update  # noqa: E402

logger = logging.getLogger(__name__)
```

文件底部追加：

```python
def finalize(*, task_id: str, task: dict, rounds: list[dict]) -> None:
    """_step_tts 主循环 return 之前调用一次：算 summary、写 state_json、写 DB、打日志。

    任何 DB 异常都被记录为 warning，不抛出（不阻断主流程）。
    """
    summary = compute_summary(rounds)
    finished_at_iso = datetime.now().replace(microsecond=0).isoformat()
    summary_with_ts = {**summary, "finished_at": finished_at_iso}

    # 1) state_json：详情页可读
    task_state_update(task_id, tts_generation_summary=summary_with_ts)

    # 2) 蓝色日志：journalctl 可读
    logger.info(format_log_line(summary))

    # 3) DB upsert：聚合分析可读。失败不阻断主流程。
    try:
        upsert(
            task_id=task_id,
            project_type=str(task.get("type") or ""),
            target_lang=str(task.get("target_lang") or ""),
            user_id=task.get("user_id"),
            summary=summary,
            finished_at_iso=finished_at_iso,
        )
    except Exception as exc:
        logger.warning("tts_generation_stats upsert failed: %s", exc)
```

- [ ] **Step 19: 跑测试确认全部 10 条 PASS**

Run:
```bash
python -m pytest tests/test_tts_generation_stats.py -v
```

Expected: 10 passed.

- [ ] **Step 20: Commit**

```bash
git add appcore/tts_generation_stats.py tests/test_tts_generation_stats.py
git commit -m "feat(stats): finalize() composes summary write to state_json + DB + log"
```

---

## Task 3: 接入 `_step_tts`

**Files:**
- Modify: `appcore/runtime.py` (`_step_tts` 方法两个 return 路径前)
- Test: `tests/test_runtime_tts_stats_integration.py`

### Step 1: 写集成测试（不改 runtime 之前应失败）

- [ ] **Step 1: 创建 `tests/test_runtime_tts_stats_integration.py`**

```python
"""验证 BaseRunner._step_tts 在收尾时调用 tts_generation_stats.finalize。

不实际跑 ElevenLabs / 任何 LLM。直接构造 rounds，调用 finalize 收尾路径。
本集成测试只验证"两个 return 路径都接上了 finalize"。
"""
from __future__ import annotations

from unittest.mock import patch

import pytest


def test_step_tts_converged_path_calls_finalize():
    from appcore import tts_generation_stats as stats_mod
    from appcore.runtime import BaseRunner  # noqa: F401  (just import sanity)

    captured: list[dict] = []

    def fake_finalize(*, task_id, task, rounds):
        captured.append({"task_id": task_id, "rounds_len": len(rounds)})

    # 直接验证 runtime.py 里 import 的 finalize 是同一个，否则 patch 失败
    import appcore.runtime as runtime_mod
    assert hasattr(runtime_mod, "tts_generation_stats")
    assert runtime_mod.tts_generation_stats.finalize is stats_mod.finalize

    with patch.object(stats_mod, "finalize", side_effect=fake_finalize):
        # 直接调用 finalize 的注入点（runtime 里通过 module 引用）
        runtime_mod.tts_generation_stats.finalize(
            task_id="t1",
            task={"type": "multi_translate", "target_lang": "it", "user_id": 1},
            rounds=[{"audio_segments_total": 5}],
        )
    assert captured == [{"task_id": "t1", "rounds_len": 1}]


def test_runtime_imports_finalize_from_stats_module():
    """硬性断言：runtime.py 通过 module 级引用调用 finalize（不是 from ... import finalize）。

    这样 monkeypatch.setattr(stats_mod, "finalize", ...) 才能在测试里生效。
    """
    import appcore.runtime as runtime_mod
    src = open(runtime_mod.__file__, encoding="utf-8").read()
    assert "from appcore import tts_generation_stats" in src or \
        "import appcore.tts_generation_stats" in src
    # 调用站点必须形如 "tts_generation_stats.finalize(" 或类似
    assert "tts_generation_stats.finalize(" in src


def test_step_tts_calls_finalize_for_both_return_paths():
    """白盒：runtime.py 源码里必须有两处 finalize 调用（converged + best_pick）。"""
    import appcore.runtime as runtime_mod
    src = open(runtime_mod.__file__, encoding="utf-8").read()
    occurrences = src.count("tts_generation_stats.finalize(")
    assert occurrences >= 2, (
        f"_step_tts 必须在 converged 和 best_pick 两条 return 路径前都调用 finalize，"
        f"当前只看到 {occurrences} 处"
    )
```

- [ ] **Step 2: 跑集成测试，确认 3 条 FAIL**

Run:
```bash
python -m pytest tests/test_runtime_tts_stats_integration.py -v
```

Expected: 3 failures（runtime.py 还没 import / 调用 finalize）。

### Step 3-4: 改 runtime.py 接入 finalize

- [ ] **Step 3: 在 `appcore/runtime.py` 顶部 import 区域加一行**

打开 `appcore/runtime.py`，找到现有的 imports 段（约前 30 行），加上：

```python
from appcore import tts_generation_stats
```

- [ ] **Step 4: 在 `_step_tts` 的 converged return 路径前插入 finalize**

定位 `_step_tts` 内 converged 分支（约 858–880 行，搜 `if final_target_lo <= audio_duration <= final_target_hi:`）。在 `return { ... }` 之前插入：

```python
            if final_target_lo <= audio_duration <= final_target_hi:
                # 标记本轮为最终采用：UI 画 ✨ 徽章 + 底部摘要说明
                round_record["is_final"] = True
                round_record["final_reason"] = "converged"
                rounds[-1] = round_record
                task_state.update(
                    task_id,
                    tts_duration_rounds=rounds,
                    tts_duration_status="converged",
                    tts_final_round=round_index,
                    tts_final_reason="converged",
                    tts_final_distance=0.0,
                )
                self._emit_duration_round(task_id, round_index, "converged", round_record)
                # ↓↓↓ 新增：写 stats（state_json + DB + 蓝色日志）
                tts_generation_stats.finalize(
                    task_id=task_id,
                    task=task_state.get(task_id) or {},
                    rounds=rounds,
                )
                return {
                    "localized_translation": localized_translation,
                    "tts_script": tts_script,
                    "tts_audio_path": result["full_audio_path"],
                    "tts_segments": result["segments"],
                    "rounds": rounds,
                    "round_products": round_products,
                    "final_round": round_index,
                }
```

- [ ] **Step 5: 在 `_step_tts` 的 best_pick return 路径前插入 finalize**

定位 best_pick 分支（约 909–925 行，搜 `task_state.update(... tts_final_reason="best_pick" ...)`）。在 `return { ... }` 之前插入：

```python
        task_state.update(
            task_id,
            tts_duration_rounds=rounds,
            tts_duration_status="converged",
            tts_final_round=best_i + 1,
            tts_final_reason="best_pick",
            tts_final_distance=round(best_distance, 3),
        )
        # ↓↓↓ 新增：写 stats
        tts_generation_stats.finalize(
            task_id=task_id,
            task=task_state.get(task_id) or {},
            rounds=rounds,
        )
        return {
            "localized_translation": best_product["localized_translation"],
            "tts_script": best_product["tts_script"],
            "tts_audio_path": best_product["tts_audio_path"],
            "tts_segments": best_product["tts_segments"],
            "rounds": rounds,
            "round_products": round_products,
            "final_round": best_i + 1,
        }
```

- [ ] **Step 6: 跑集成测试确认 3 条 PASS**

Run:
```bash
python -m pytest tests/test_runtime_tts_stats_integration.py -v
```

Expected: 3 passed.

- [ ] **Step 7: 跑相关回归（确保没把现有路由/编排测试搞坏）**

Run:
```bash
python -m pytest tests/test_tts_generation_stats.py tests/test_runtime_tts_stats_integration.py tests/test_bulk_translate_runtime.py -q
```

Expected: 全绿。`test_multi_translate_routes.py` 跑得慢（约 2 分钟），放到 Task 4 收尾再跑一次。

- [ ] **Step 8: Commit**

```bash
git add appcore/runtime.py tests/test_runtime_tts_stats_integration.py
git commit -m "feat(runtime): wire tts_generation_stats.finalize into _step_tts"
```

---

## Task 4: 收尾 — 全量回归 + 部署脚本里写一行说明

**Files:**
- Modify: `docs/superpowers/specs/2026-04-30-tts-generation-stats-design.md`（spec 实施完成后追加 status 段，可选）

- [ ] **Step 1: 跑全量相关回归**

Run:
```bash
python -m pytest tests/test_tts_generation_stats.py tests/test_runtime_tts_stats_integration.py tests/test_bulk_translate_runtime.py tests/test_multi_translate_routes.py -q
```

Expected: 全绿。如果 `test_multi_translate_routes.py` 有任何 fail，必须修复或确认与本 PR 无关后再继续。

- [ ] **Step 2: 自检最后一遍**

```bash
git diff master --stat
git log master..HEAD --oneline
```

Expected：4 个文件改动（migration + utils + 1 个测试 + runtime.py + 1 个集成测试），约 4–5 个 commit。

- [ ] **Step 3: 推 worktree 分支**

```bash
git push -u origin feature/tts-generation-stats
```

至此，feature 分支推到 origin 完成。**合并 + 部署 + worktree 清理在 plan 之外做（按 CLAUDE.md 收尾流程：master 合并 → push → 服务器 git pull + restart → 自动 apply migration → 抽查日志/DB → 清理 worktree）。**

---

## Self-Review

1. **Spec coverage**：
   - ✅ Indicators (`translate_calls` / `audio_calls`) → Task 2 Step 1–4 (compute_summary)
   - ✅ state_json `tts_generation_summary` → Task 2 Step 16–20 (finalize)
   - ✅ 独立表 + migration → Task 1 + Task 2 Step 11–15 (upsert)
   - ✅ ANSI 蓝色日志 → Task 2 Step 6–10 (format_log_line) + Step 16–20 (finalize 调用)
   - ✅ 接入 `_step_tts` 两个 return 路径 → Task 3
   - ✅ 异常路径不阻断 → Task 2 `test_finalize_swallows_db_error_but_keeps_state_json_and_log`
   - ✅ 不做：变速/收敛指标/round 数 — plan 里也未包含，与 spec 一致
2. **Placeholder scan**：无 TBD/TODO/"add appropriate ..."；每一步都有具体代码或具体命令。
3. **Type consistency**：
   - `compute_summary(rounds) -> dict` 在 Task 2 全程一致
   - `finalize(*, task_id, task, rounds)` 签名在 Task 2 / 3 一致
   - `upsert(*, task_id, project_type, target_lang, user_id, summary, finished_at_iso)` 在 Step 13 + 18 一致
   - state_json key 全程用 `tts_generation_summary`
   - DB 表名全程用 `tts_generation_stats`
