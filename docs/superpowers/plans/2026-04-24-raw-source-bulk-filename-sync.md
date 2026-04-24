# Raw Source Bulk Filename Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把每个产品唯一原始视频的显示名与实际文件名，对齐为该产品最早英语视频的文件名，并输出无法自动处理的异常清单。

**Architecture:** 新增一个聚焦的同步模块，负责读取候选产品、挑选“最早英语视频 + 唯一原始视频”的匹配关系、执行数据库与本地媒体文件重命名。命令行脚本只负责调用该模块做 dry-run / apply，并把异常项与英语视频列表序列化成报告，便于直接发给用户。

**Tech Stack:** Python、PyMySQL DAO、pytest、本地媒体存储 `appcore.local_media_storage`

---

### Task 1: 为候选选择与异常报告写失败测试

**Files:**
- Create: `tests/test_raw_source_filename_sync.py`
- Reference: `appcore/medias.py`

- [ ] **Step 1: 写候选选择失败测试**

```python
from datetime import datetime

from appcore import raw_source_filename_sync as mod


def test_collect_candidates_prefers_oldest_english_video(monkeypatch):
    monkeypatch.setattr(mod.medias, "list_products", lambda *args, **kwargs: ([
        {"id": 101, "name": "虎眼石绿天然手串"},
    ], 1))
    monkeypatch.setattr(mod.medias, "list_items", lambda product_id, lang=None: [
        {"id": 1, "product_id": 101, "lang": "en", "filename": "2025.07.20-newer.mp4", "created_at": datetime(2025, 7, 20, 9, 0, 0)},
        {"id": 2, "product_id": 101, "lang": "en", "filename": "2025.07.19-older.mp4", "created_at": datetime(2025, 7, 19, 9, 0, 0)},
    ] if product_id == 101 else [])
    monkeypatch.setattr(mod.medias, "list_raw_sources", lambda product_id: [
        {"id": 88, "product_id": 101, "display_name": "2025.07.19-虎眼石绿天然手串-原素材.mp4", "video_object_key": "1/medias/101/raw_sources/a_old.mp4"},
    ])

    report = mod.collect_sync_report()

    assert report["syncable"][0]["target_filename"] == "2025.07.19-older.mp4"
    assert [item["filename"] for item in report["syncable"][0]["english_videos"]] == [
        "2025.07.19-older.mp4",
        "2025.07.20-newer.mp4",
    ]
```

- [ ] **Step 2: 运行测试确认先失败**

Run: `pytest tests/test_raw_source_filename_sync.py::test_collect_candidates_prefers_oldest_english_video -q`
Expected: `ModuleNotFoundError` 或缺少 `collect_sync_report`

- [ ] **Step 3: 写异常报告失败测试**

```python
def test_collect_candidates_flags_multiple_raw_sources(monkeypatch):
    monkeypatch.setattr(mod.medias, "list_products", lambda *args, **kwargs: ([
        {"id": 202, "name": "双原始视频产品"},
    ], 1))
    monkeypatch.setattr(mod.medias, "list_items", lambda product_id, lang=None: [
        {"id": 11, "product_id": 202, "lang": "en", "filename": "2025.07.18-english-a.mp4", "created_at": datetime(2025, 7, 18, 12, 0, 0)},
        {"id": 12, "product_id": 202, "lang": "en", "filename": "2025.07.19-english-b.mp4", "created_at": datetime(2025, 7, 19, 12, 0, 0)},
    ])
    monkeypatch.setattr(mod.medias, "list_raw_sources", lambda product_id: [
        {"id": 91, "product_id": 202, "display_name": "raw-a.mp4", "video_object_key": "1/medias/202/raw_sources/a.mp4"},
        {"id": 92, "product_id": 202, "display_name": "raw-b.mp4", "video_object_key": "1/medias/202/raw_sources/b.mp4"},
    ])

    report = mod.collect_sync_report()

    assert report["problems"] == [{
        "product_id": 202,
        "raw_source_count": 2,
        "raw_source_names": ["raw-a.mp4", "raw-b.mp4"],
        "english_video_names": ["2025.07.18-english-a.mp4", "2025.07.19-english-b.mp4"],
        "reason": "raw_source_count_not_one",
    }]
```

- [ ] **Step 4: 运行测试确认先失败**

Run: `pytest tests/test_raw_source_filename_sync.py::test_collect_candidates_flags_multiple_raw_sources -q`
Expected: FAIL，因为异常报告结构尚未实现

- [ ] **Step 5: 提交**

```bash
git add tests/test_raw_source_filename_sync.py
git commit -m "test: cover raw source filename sync selection"
```

### Task 2: 实现同步模块与 DAO/文件重命名

**Files:**
- Create: `appcore/raw_source_filename_sync.py`
- Modify: `appcore/medias.py`
- Modify: `appcore/local_media_storage.py`
- Test: `tests/test_raw_source_filename_sync.py`

- [ ] **Step 1: 在 `tests/test_raw_source_filename_sync.py` 增加实际执行失败测试**

```python
def test_apply_sync_renames_db_and_storage(monkeypatch, tmp_path):
    moved = []
    monkeypatch.setattr(mod.local_media_storage, "rename", lambda old_key, new_key: moved.append((old_key, new_key)))
    monkeypatch.setattr(mod.medias, "rename_raw_source_video", lambda raw_source_id, **kwargs: {
        "raw_source_id": raw_source_id,
        **kwargs,
    })

    result = mod.apply_sync({
        "product_id": 101,
        "raw_source_id": 88,
        "raw_source_name": "old-name.mp4",
        "raw_video_object_key": "1/medias/101/raw_sources/uuid_old-name.mp4",
        "target_filename": "new-name.mp4",
        "english_videos": [{"filename": "new-name.mp4"}],
    })

    assert moved == [("1/medias/101/raw_sources/uuid_old-name.mp4", "1/medias/101/raw_sources/uuid_new-name.mp4")]
    assert result["new_object_key"].endswith("uuid_new-name.mp4")
```

- [ ] **Step 2: 运行测试确认先失败**

Run: `pytest tests/test_raw_source_filename_sync.py::test_apply_sync_renames_db_and_storage -q`
Expected: FAIL，因为 `apply_sync` / `rename_raw_source_video` / `local_media_storage.rename` 尚未实现

- [ ] **Step 3: 在 `appcore/local_media_storage.py` 加最小重命名支持**

```python
def rename(old_object_key: str, new_object_key: str) -> Path:
    source = local_path_for(old_object_key)
    if not source.is_file():
        raise FileNotFoundError(old_object_key)
    destination = local_path_for(new_object_key)
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, destination)
    return destination
```

- [ ] **Step 4: 在 `appcore/medias.py` 增加原始视频改名 DAO**

```python
def rename_raw_source_video(
    raw_source_id: int,
    *,
    display_name: str,
    video_object_key: str,
) -> int:
    return execute(
        "UPDATE media_raw_sources SET display_name=%s, video_object_key=%s WHERE id=%s",
        (display_name, video_object_key, raw_source_id),
    )
```

- [ ] **Step 5: 在 `appcore/raw_source_filename_sync.py` 实现选择与应用逻辑**

```python
def collect_sync_report() -> dict:
    ...


def apply_sync(candidate: dict) -> dict:
    ...
```

要求：
- 只把 `lang='en'` 的 `media_items` 作为英语视频来源
- 用 `created_at ASC, id ASC` 选最早英语视频
- 只允许 `media_raw_sources` 数量恰好为 1 的产品进入 `syncable`
- 如果原始视频已经等于目标名，归类为 `already_aligned`
- 报告里保留所有英语视频名，便于用户核对

- [ ] **Step 6: 跑测试转绿**

Run: `pytest tests/test_raw_source_filename_sync.py -q`
Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add appcore/local_media_storage.py appcore/medias.py appcore/raw_source_filename_sync.py tests/test_raw_source_filename_sync.py
git commit -m "feat: add raw source filename sync module"
```

### Task 3: 提供批量执行脚本与报告输出

**Files:**
- Create: `scripts/sync_raw_source_filenames.py`
- Modify: `tests/test_raw_source_filename_sync.py`

- [ ] **Step 1: 为脚本输出补失败测试**

```python
def test_cli_prints_problem_report(capsys, monkeypatch):
    monkeypatch.setattr(mod, "collect_sync_report", lambda: {
        "syncable": [],
        "already_aligned": [],
        "problems": [{
            "product_id": 202,
            "raw_source_count": 2,
            "raw_source_names": ["raw-a.mp4", "raw-b.mp4"],
            "english_video_names": ["2025.07.18-english-a.mp4", "2025.07.19-english-b.mp4"],
            "reason": "raw_source_count_not_one",
        }],
    })
```

- [ ] **Step 2: 运行测试确认先失败**

Run: `pytest tests/test_raw_source_filename_sync.py::test_cli_prints_problem_report -q`
Expected: FAIL，因为 CLI 还不存在

- [ ] **Step 3: 实现脚本**

```python
parser.add_argument("--apply", action="store_true")
parser.add_argument("--json-out")
```

行为：
- 默认 dry-run，只输出 `syncable / already_aligned / problems` 汇总
- `--apply` 时逐条执行 `apply_sync`
- `--json-out` 时把完整报告写到文件，便于后续发给用户

- [ ] **Step 4: 跑相关测试**

Run: `pytest tests/test_raw_source_filename_sync.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add scripts/sync_raw_source_filenames.py tests/test_raw_source_filename_sync.py
git commit -m "feat: add raw source filename sync cli"
```

### Task 4: 执行测试环境批量对齐并导出报告

**Files:**
- Use: `scripts/sync_raw_source_filenames.py`
- Output: `output/raw-source-filename-sync-report.json`

- [ ] **Step 1: 先 dry-run 生成报告**

Run:

```bash
python scripts/sync_raw_source_filenames.py --json-out output/raw-source-filename-sync-report.json
```

Expected:
- 命令 exit 0
- 报告中区分 `syncable / already_aligned / problems`

- [ ] **Step 2: 真正执行批量对齐**

Run:

```bash
python scripts/sync_raw_source_filenames.py --apply --json-out output/raw-source-filename-sync-report.json
```

Expected:
- `syncable` 项全部落到 `applied`
- 数据库里的 `media_raw_sources.display_name` / `video_object_key` 已更新
- 本地媒体文件路径同步改名

- [ ] **Step 3: 回读验证**

Run:

```bash
python scripts/sync_raw_source_filenames.py --json-out output/raw-source-filename-sync-report-after.json
```

Expected:
- 刚处理过的产品出现在 `already_aligned`
- `problems` 只剩无法自动处理的产品

- [ ] **Step 4: 整理给用户的异常清单**

输出内容必须包含：
- `product_id`
- 当前原始视频名字
- 原始视频条数
- 该产品所有英语视频名字（有两条就列两条）

- [ ] **Step 5: 提交**

```bash
git add appcore/local_media_storage.py appcore/medias.py appcore/raw_source_filename_sync.py scripts/sync_raw_source_filenames.py tests/test_raw_source_filename_sync.py docs/superpowers/plans/2026-04-24-raw-source-bulk-filename-sync.md
git commit -m "feat: bulk sync raw source filenames from english videos"
```
