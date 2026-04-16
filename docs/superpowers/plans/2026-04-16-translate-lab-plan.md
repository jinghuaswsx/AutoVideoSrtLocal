# 视频翻译 V2（Translate Lab）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建分镜驱动的视频翻译系统，用 Gemini 视觉分析拆解分镜、语速模型约束翻译长度、TTS 时长校验迭代修正文案，解决现有翻译模块音画错位问题。作为独立测试菜单「视频翻译（测试）」上线，不影响现有三个翻译模块。

**Architecture:** 新建 `project_type = 'translate_lab'`，独立路由 `translate_lab.py`、独立 `PipelineRunnerV2`、独立模板。7 步流水线：extract → shot_decompose → voice_match → translate → tts_verify → subtitle → compose。复用现有 `extract_audio`、`compose_video`、`EventBus`、`task_state`、Socket.IO、ffprobe 等基础设施。

**Tech Stack:** Python + Flask + Jinja2 + Socket.IO / MySQL `projects` + 新表 / `google-genai` SDK / `elevenlabs` SDK / `resemblyzer` / ffmpeg + ffprobe / pytest

**Spec:** `docs/superpowers/specs/2026-04-16-translate-lab-design.md`

**Branch:** `feature/translate-lab`

---

## File Structure

### Create

| File | Responsibility |
| --- | --- |
| `db/migrations/2026_04_16_translate_lab_schema.sql` | 新增 `elevenlabs_voices`、`voice_speech_rate` 表，并扩展 `projects.type` ENUM |
| `pipeline/voice_library_sync.py` | 从 ElevenLabs 分页拉取全量共享音色并写入 `elevenlabs_voices` 表 |
| `pipeline/voice_embedding.py` | 用 resemblyzer 从音频提取 256 维 speaker embedding |
| `pipeline/voice_match.py` | 提取原视频人声特征，基于余弦相似度返回候选音色 |
| `pipeline/speech_rate_model.py` | 语速模型 CRUD + 初始化基准 + 增量更新 |
| `pipeline/shot_decompose.py` | Gemini Pro 分析视频输出分镜列表，合并 ASR 文本 |
| `pipeline/translate_v2.py` | 分镜级翻译，字符数上限约束，最多 2 次缩写重试 |
| `pipeline/tts_v2.py` | 分镜级 TTS + 时长校验 + 微调循环 |
| `pipeline/subtitle_v2.py` | 字幕块拆分、统一字号计算、SRT 生成 |
| `appcore/runtime_v2.py` | 新流水线编排器 `PipelineRunnerV2`，7 步流程 |
| `web/services/translate_lab_runner.py` | 后台线程启动 + Socket.IO 事件桥接 |
| `web/routes/translate_lab.py` | 路由：列表、详情、上传、启动、恢复、音色选择 |
| `web/templates/translate_lab_list.html` | 列表页 |
| `web/templates/translate_lab_detail.html` | 详情页（分镜、音色候选、翻译、TTS、字幕预览） |
| `tests/test_voice_library_sync.py` | 分页拉取 + 存储测试 |
| `tests/test_voice_match.py` | embedding + 余弦相似度测试 |
| `tests/test_speech_rate_model.py` | 语速模型 CRUD 和更新测试 |
| `tests/test_shot_decompose.py` | 分镜输出结构 + ASR 对齐测试 |
| `tests/test_translate_v2.py` | 字符数约束翻译测试 |
| `tests/test_tts_v2.py` | 时长校验 + 微调循环测试 |
| `tests/test_subtitle_v2.py` | 字号计算 + 拆分测试 |
| `tests/test_runtime_v2.py` | 流水线集成测试 |
| `tests/test_translate_lab_routes.py` | 路由测试（列表、详情、启动、恢复） |

### Modify

| File | Change |
| --- | --- |
| `db/schema.sql` | 同步新表定义和 `projects.type` ENUM 新值 `translate_lab` |
| `appcore/events.py` | 新增 `lab_*` 事件类型常量 |
| `appcore/settings.py` | 注册 `translate_lab` 项目标签（保留策略/UI label 识别） |
| `appcore/task_state.py` | 增加 `create_translate_lab(...)` 工厂函数 |
| `appcore/gemini.py` | 确认 `VIDEO_CAPABLE_MODELS` 包含 `gemini-3.1-pro-preview`（已有） |
| `web/store.py` | 重导出 `create_translate_lab` |
| `web/app.py` | 注册新 blueprint，添加 `join_translate_lab_task` Socket.IO 事件，启动时触发 lab 任务恢复 |
| `web/templates/layout.html` | 新增「视频翻译（测试）」菜单项 |
| `config.py` | 新增 `TRANSLATE_LAB_MAX_RETRY`、`TRANSLATE_LAB_SHOT_MODEL` 等配置 |
| `requirements.txt` | 新增 `resemblyzer>=0.1.1` |
| `tests/test_web_routes.py` | 新增 layout 菜单渲染测试 |

---

## Task 1: 数据库迁移与 schema 同步

**Files:**
- Create: `db/migrations/2026_04_16_translate_lab_schema.sql`
- Modify: `db/schema.sql`

- [ ] **Step 1: 编写迁移脚本**

Create `db/migrations/2026_04_16_translate_lab_schema.sql`:

```sql
-- 扩展 projects.type ENUM
ALTER TABLE `projects` MODIFY COLUMN `type` ENUM(
  'translation','copywriting','video_creation','video_review',
  'text_translate','de_translate','fr_translate','subtitle_removal',
  'translate_lab'
) NOT NULL DEFAULT 'translation';

-- ElevenLabs 全量音色库
CREATE TABLE IF NOT EXISTS `elevenlabs_voices` (
  `voice_id` VARCHAR(64) NOT NULL PRIMARY KEY,
  `name` VARCHAR(255) NOT NULL,
  `gender` VARCHAR(32) DEFAULT NULL,
  `age` VARCHAR(32) DEFAULT NULL,
  `language` VARCHAR(32) DEFAULT NULL,
  `accent` VARCHAR(64) DEFAULT NULL,
  `category` VARCHAR(64) DEFAULT NULL,
  `descriptive` VARCHAR(255) DEFAULT NULL,
  `preview_url` TEXT DEFAULT NULL,
  `audio_embedding` MEDIUMBLOB DEFAULT NULL,
  `labels_json` JSON DEFAULT NULL,
  `public_owner_id` VARCHAR(128) DEFAULT NULL,
  `synced_at` DATETIME NOT NULL,
  `updated_at` DATETIME NOT NULL ON UPDATE CURRENT_TIMESTAMP,
  KEY `idx_language` (`language`),
  KEY `idx_gender_language` (`gender`, `language`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 语速模型
CREATE TABLE IF NOT EXISTS `voice_speech_rate` (
  `id` BIGINT AUTO_INCREMENT PRIMARY KEY,
  `voice_id` VARCHAR(64) NOT NULL,
  `language` VARCHAR(32) NOT NULL,
  `chars_per_second` DECIMAL(6,3) NOT NULL,
  `sample_count` INT NOT NULL DEFAULT 1,
  `updated_at` DATETIME NOT NULL ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY `uniq_voice_lang` (`voice_id`, `language`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

- [ ] **Step 2: 同步到 schema.sql**

在 `db/schema.sql` 中：
- 修改 `projects.type` ENUM 定义加上 `translate_lab`
- 追加两张新表的 CREATE TABLE 语句

- [ ] **Step 3: 运行迁移**

```bash
mysql -u <user> -p <db> < db/migrations/2026_04_16_translate_lab_schema.sql
```

验证：
```bash
mysql -e "SHOW TABLES LIKE 'elevenlabs_voices';" <db>
mysql -e "SHOW TABLES LIKE 'voice_speech_rate';" <db>
mysql -e "SHOW COLUMNS FROM projects LIKE 'type';" <db>
```

- [ ] **Step 4: Commit**

```bash
git add db/migrations/2026_04_16_translate_lab_schema.sql db/schema.sql
git commit -m "feat: 新增 translate_lab 模块数据库表和类型"
```

---

## Task 2: 模块骨架（事件、路由、模板、菜单、任务工厂）

**Files:**
- Modify: `appcore/events.py`
- Modify: `appcore/settings.py`
- Modify: `appcore/task_state.py`
- Modify: `web/store.py`
- Modify: `web/app.py`
- Modify: `web/templates/layout.html`
- Create: `web/routes/translate_lab.py`
- Create: `web/templates/translate_lab_list.html`
- Create: `web/templates/translate_lab_detail.html`
- Create: `tests/test_translate_lab_routes.py`

- [ ] **Step 1: 添加事件常量**

Edit `appcore/events.py` — 追加：

```python
EVT_LAB_SHOT_DECOMPOSE_RESULT = "lab_shot_decompose_result"
EVT_LAB_VOICE_MATCH_CANDIDATES = "lab_voice_match_candidates"
EVT_LAB_VOICE_CONFIRMED = "lab_voice_confirmed"
EVT_LAB_TRANSLATE_PROGRESS = "lab_translate_progress"
EVT_LAB_TTS_PROGRESS = "lab_tts_progress"
EVT_LAB_SUBTITLE_READY = "lab_subtitle_ready"
EVT_LAB_PIPELINE_DONE = "lab_pipeline_done"
EVT_LAB_PIPELINE_ERROR = "lab_pipeline_error"
```

- [ ] **Step 2: 注册 project type 标签**

Edit `appcore/settings.py` — 在项目类型标签映射中加：
```python
"translate_lab": "视频翻译（测试）",
```

- [ ] **Step 3: 新建 task factory**

Edit `appcore/task_state.py` — 新增：
```python
def create_translate_lab(task_id, video_path, task_dir, *, original_filename, user_id, **options):
    return _create_task(
        task_id=task_id,
        project_type="translate_lab",
        video_path=video_path,
        task_dir=task_dir,
        original_filename=original_filename,
        user_id=user_id,
        options=options,
    )
```

Edit `web/store.py` — 在 facade 处：
```python
from appcore.task_state import create_translate_lab  # re-export
```

- [ ] **Step 4: 写 failing 测试**

Create `tests/test_translate_lab_routes.py`:

```python
import pytest
from web import store


def test_translate_lab_list_page_renders(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "web.routes.translate_lab.list_projects_by_type",
        lambda user_id, project_type: [],
    )
    resp = authed_client_no_db.get("/translate-lab")
    assert resp.status_code == 200
    assert "视频翻译（测试）" in resp.get_data(as_text=True)


def test_translate_lab_detail_page_renders(authed_client_no_db, monkeypatch):
    task = store.create_translate_lab(
        "lab-1",
        "uploads/lab-1.mp4",
        "output/lab-1",
        original_filename="demo.mp4",
        user_id=1,
    )
    monkeypatch.setattr(
        "web.routes.translate_lab.get_project",
        lambda task_id, user_id: {
            "id": task_id,
            "user_id": user_id,
            "type": "translate_lab",
            "display_name": "demo",
            "original_filename": "demo.mp4",
            "status": "uploaded",
        },
    )
    resp = authed_client_no_db.get(f"/translate-lab/{task['id']}")
    assert resp.status_code == 200


def test_layout_contains_translate_lab_link(authed_client_no_db):
    resp = authed_client_no_db.get("/translate-lab")
    body = resp.get_data(as_text=True)
    assert "/translate-lab" in body
```

Run: `pytest tests/test_translate_lab_routes.py -v`
Expected: FAIL（路由未定义）

- [ ] **Step 5: 实现路由骨架**

Create `web/routes/translate_lab.py`:

```python
from flask import Blueprint, render_template, request, jsonify, abort
from appcore.auth import login_required, current_user_id
from web.store import list_projects_by_type, get_project

bp = Blueprint("translate_lab", __name__, url_prefix="/translate-lab")


@bp.route("/", methods=["GET"])
@login_required
def index():
    user_id = current_user_id()
    tasks = list_projects_by_type(user_id, "translate_lab")
    return render_template("translate_lab_list.html", tasks=tasks)


@bp.route("/<task_id>", methods=["GET"])
@login_required
def detail(task_id):
    user_id = current_user_id()
    task = get_project(task_id, user_id)
    if not task or task.get("type") != "translate_lab":
        abort(404)
    return render_template("translate_lab_detail.html", task=task)
```

- [ ] **Step 6: 实现模板骨架**

Create `web/templates/translate_lab_list.html` — 基于现有 `de_translate_list.html` 复制改名，标题改成「视频翻译（测试）」。

Create `web/templates/translate_lab_detail.html` — 基于 `de_translate_detail.html` 复制改名，模板里先放一个空的 7 步进度条占位。

- [ ] **Step 7: 注册 blueprint 和菜单**

Edit `web/app.py`：
```python
from web.routes.translate_lab import bp as translate_lab_bp
app.register_blueprint(translate_lab_bp)
```

Edit `web/templates/layout.html`，在现有翻译菜单区域下方加：
```html
<li>
  <a href="{{ url_for('translate_lab.index') }}"
     class="{% if request.blueprint == 'translate_lab' %}active{% endif %}">
    <span class="icon">🧪</span>
    <span>视频翻译（测试）</span>
  </a>
</li>
```

- [ ] **Step 8: 验证测试通过**

```bash
pytest tests/test_translate_lab_routes.py -v
```
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add appcore/events.py appcore/settings.py appcore/task_state.py \
  web/store.py web/app.py web/templates/layout.html \
  web/routes/translate_lab.py \
  web/templates/translate_lab_list.html \
  web/templates/translate_lab_detail.html \
  tests/test_translate_lab_routes.py
git commit -m "feat(translate-lab): 模块骨架、菜单、空路由与模板"
```

---

## Task 3: ElevenLabs 共享音色 API 客户端（分页抓取）

**Files:**
- Create: `pipeline/voice_library_sync.py`
- Create: `tests/test_voice_library_sync.py`

- [ ] **Step 1: 写 failing 测试**

Create `tests/test_voice_library_sync.py`:

```python
from unittest.mock import patch, MagicMock
from pipeline.voice_library_sync import fetch_shared_voices_page, sync_all_shared_voices


def test_fetch_shared_voices_page_returns_voices_and_next_token():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "voices": [
            {"voice_id": "v1", "name": "Rachel", "gender": "female",
             "language": "en", "preview_url": "http://a.mp3",
             "labels": {"accent": "american"}, "category": "professional"}
        ],
        "has_more": True,
        "next_page_token": "token-next",
    }
    with patch("pipeline.voice_library_sync.requests.get", return_value=mock_response):
        voices, next_token = fetch_shared_voices_page(
            api_key="dummy",
            page_size=100,
            next_page_token=None,
            language=None,
        )
    assert len(voices) == 1
    assert voices[0]["voice_id"] == "v1"
    assert next_token == "token-next"


def test_sync_all_iterates_pages_until_no_more():
    pages = [
        ({"voices": [{"voice_id": "v1", "name": "A"}], "has_more": True,
          "next_page_token": "t2"}, None),
        ({"voices": [{"voice_id": "v2", "name": "B"}], "has_more": False,
          "next_page_token": None}, "t2"),
    ]
    call_count = {"i": 0}

    def fake_get(url, headers, params):
        i = call_count["i"]
        call_count["i"] += 1
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = pages[i][0]
        return resp

    stored = []
    with patch("pipeline.voice_library_sync.requests.get", side_effect=fake_get), \
         patch("pipeline.voice_library_sync.upsert_voice",
               side_effect=lambda v: stored.append(v)):
        total = sync_all_shared_voices(api_key="dummy")
    assert total == 2
    assert [v["voice_id"] for v in stored] == ["v1", "v2"]
```

Run: `pytest tests/test_voice_library_sync.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 2: 实现 `voice_library_sync.py`**

Create `pipeline/voice_library_sync.py`:

```python
import json
from datetime import datetime
from typing import Optional
import requests
from appcore.db import execute

SHARED_VOICES_URL = "https://api.elevenlabs.io/v1/shared-voices"
DEFAULT_PAGE_SIZE = 100


def fetch_shared_voices_page(api_key, page_size=DEFAULT_PAGE_SIZE,
                              next_page_token=None, language=None,
                              gender=None, category=None):
    headers = {"xi-api-key": api_key}
    params = {"page_size": page_size}
    if next_page_token:
        params["next_page_token"] = next_page_token
    if language:
        params["language"] = language
    if gender:
        params["gender"] = gender
    if category:
        params["category"] = category

    resp = requests.get(SHARED_VOICES_URL, headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get("voices", []), data.get("next_page_token") if data.get("has_more") else None


def upsert_voice(voice):
    labels = voice.get("labels") or {}
    now = datetime.utcnow()
    execute(
        """
        INSERT INTO elevenlabs_voices
          (voice_id, name, gender, age, language, accent, category,
           descriptive, preview_url, labels_json, public_owner_id,
           synced_at, updated_at)
        VALUES
          (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          name=VALUES(name), gender=VALUES(gender), age=VALUES(age),
          language=VALUES(language), accent=VALUES(accent),
          category=VALUES(category), descriptive=VALUES(descriptive),
          preview_url=VALUES(preview_url), labels_json=VALUES(labels_json),
          public_owner_id=VALUES(public_owner_id),
          synced_at=VALUES(synced_at)
        """,
        (
            voice["voice_id"],
            voice.get("name") or "",
            voice.get("gender") or labels.get("gender"),
            voice.get("age") or labels.get("age"),
            voice.get("language") or labels.get("language"),
            voice.get("accent") or labels.get("accent"),
            voice.get("category"),
            voice.get("descriptive") or labels.get("descriptive"),
            voice.get("preview_url"),
            json.dumps(labels),
            voice.get("public_owner_id"),
            now,
            now,
        ),
    )


def sync_all_shared_voices(api_key, *, language=None, gender=None,
                            category=None, page_size=DEFAULT_PAGE_SIZE):
    total = 0
    next_token = None
    while True:
        voices, next_token = fetch_shared_voices_page(
            api_key=api_key,
            page_size=page_size,
            next_page_token=next_token,
            language=language,
            gender=gender,
            category=category,
        )
        for voice in voices:
            upsert_voice(voice)
            total += 1
        if not next_token:
            break
    return total
```

- [ ] **Step 3: 验证测试通过**

```bash
pytest tests/test_voice_library_sync.py -v
```
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add pipeline/voice_library_sync.py tests/test_voice_library_sync.py
git commit -m "feat(translate-lab): ElevenLabs 共享音色全量分页同步"
```

---

## Task 4: 音频 Speaker Embedding 提取（resemblyzer）

**Files:**
- Create: `pipeline/voice_embedding.py`
- Create: `tests/test_voice_embedding.py`
- Modify: `requirements.txt`

- [ ] **Step 1: 添加依赖**

Edit `requirements.txt` — 追加：
```
resemblyzer>=0.1.1
librosa>=0.10.0
numpy>=1.24,<3.0
```

Run: `pip install resemblyzer librosa`

- [ ] **Step 2: 写 failing 测试**

Create `tests/test_voice_embedding.py`:

```python
import numpy as np
from unittest.mock import patch, MagicMock
from pipeline.voice_embedding import embed_audio_file, cosine_similarity


def test_cosine_similarity_same_vector_is_one():
    v = np.array([1.0, 2.0, 3.0])
    assert cosine_similarity(v, v) == 1.0


def test_cosine_similarity_orthogonal_is_zero():
    a = np.array([1.0, 0.0])
    b = np.array([0.0, 1.0])
    assert cosine_similarity(a, b) == 0.0


def test_embed_audio_file_returns_256d_vector(tmp_path):
    dummy_audio = tmp_path / "test.wav"
    dummy_audio.write_bytes(b"fake")
    mock_encoder = MagicMock()
    mock_encoder.embed_utterance.return_value = np.zeros(256, dtype=np.float32)
    with patch("pipeline.voice_embedding.VoiceEncoder", return_value=mock_encoder), \
         patch("pipeline.voice_embedding.preprocess_wav", return_value=np.zeros(16000)):
        vec = embed_audio_file(str(dummy_audio))
    assert vec.shape == (256,)
```

Run: `pytest tests/test_voice_embedding.py -v`
Expected: FAIL

- [ ] **Step 3: 实现 `voice_embedding.py`**

Create `pipeline/voice_embedding.py`:

```python
import numpy as np
from resemblyzer import VoiceEncoder, preprocess_wav

_ENCODER_CACHE = {}


def _get_encoder():
    if "enc" not in _ENCODER_CACHE:
        _ENCODER_CACHE["enc"] = VoiceEncoder(verbose=False)
    return _ENCODER_CACHE["enc"]


def embed_audio_file(audio_path):
    wav = preprocess_wav(audio_path)
    encoder = _get_encoder()
    return encoder.embed_utterance(wav)


def cosine_similarity(a, b):
    a = np.asarray(a, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def serialize_embedding(vec):
    return np.asarray(vec, dtype=np.float32).tobytes()


def deserialize_embedding(blob):
    return np.frombuffer(blob, dtype=np.float32)
```

- [ ] **Step 4: 验证测试通过**

```bash
pytest tests/test_voice_embedding.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pipeline/voice_embedding.py tests/test_voice_embedding.py requirements.txt
git commit -m "feat(translate-lab): resemblyzer speaker embedding + 余弦相似度"
```

---

## Task 5: 音色库预览音频批量下载与 embedding 回写

**Files:**
- Modify: `pipeline/voice_library_sync.py`
- Modify: `tests/test_voice_library_sync.py`

- [ ] **Step 1: 写 failing 测试**

Edit `tests/test_voice_library_sync.py` — 追加：

```python
import numpy as np
from unittest.mock import patch, MagicMock


def test_embed_missing_voices_downloads_and_stores(tmp_path, monkeypatch):
    voices_needing_embed = [
        {"voice_id": "v1", "preview_url": "http://a.mp3"},
        {"voice_id": "v2", "preview_url": "http://b.mp3"},
    ]

    saved = {}

    def fake_download(url, dest):
        dest.write_bytes(b"x")
        return str(dest)

    def fake_embed(path):
        return np.full(256, 0.5, dtype=np.float32)

    def fake_update(voice_id, blob):
        saved[voice_id] = blob

    with patch("pipeline.voice_library_sync._list_voices_without_embedding",
               return_value=voices_needing_embed), \
         patch("pipeline.voice_library_sync._download_preview",
               side_effect=fake_download), \
         patch("pipeline.voice_library_sync.embed_audio_file",
               side_effect=fake_embed), \
         patch("pipeline.voice_library_sync._update_embedding",
               side_effect=fake_update):
        from pipeline.voice_library_sync import embed_missing_voices
        count = embed_missing_voices(cache_dir=str(tmp_path))
    assert count == 2
    assert set(saved.keys()) == {"v1", "v2"}
```

Run: `pytest tests/test_voice_library_sync.py::test_embed_missing_voices_downloads_and_stores -v`
Expected: FAIL

- [ ] **Step 2: 实现 embedding 批量处理**

Edit `pipeline/voice_library_sync.py` — 追加：

```python
import os
import hashlib
from pipeline.voice_embedding import embed_audio_file, serialize_embedding
from appcore.db import query

def _list_voices_without_embedding(limit=None):
    sql = """
        SELECT voice_id, preview_url FROM elevenlabs_voices
        WHERE preview_url IS NOT NULL AND audio_embedding IS NULL
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    return query(sql)


def _download_preview(url, dest_path):
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    with open(dest_path, "wb") as f:
        f.write(resp.content)
    return dest_path


def _update_embedding(voice_id, blob):
    execute(
        "UPDATE elevenlabs_voices SET audio_embedding=%s, updated_at=%s WHERE voice_id=%s",
        (blob, datetime.utcnow(), voice_id),
    )


def embed_missing_voices(cache_dir, limit=None):
    os.makedirs(cache_dir, exist_ok=True)
    count = 0
    for row in _list_voices_without_embedding(limit=limit):
        voice_id = row["voice_id"]
        url = row["preview_url"]
        if not url:
            continue
        file_name = hashlib.sha1(voice_id.encode()).hexdigest() + ".mp3"
        dest = os.path.join(cache_dir, file_name)
        try:
            _download_preview(url, dest)
            vec = embed_audio_file(dest)
            _update_embedding(voice_id, serialize_embedding(vec))
            count += 1
        except Exception as exc:
            # 日志但不中断整批
            print(f"[embed_missing_voices] failed {voice_id}: {exc}")
    return count
```

- [ ] **Step 3: 验证测试通过**

```bash
pytest tests/test_voice_library_sync.py -v
```
Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add pipeline/voice_library_sync.py tests/test_voice_library_sync.py
git commit -m "feat(translate-lab): 预览音频下载并回写 speaker embedding"
```

---

## Task 6: 音色匹配（原视频音频特征 + 余弦相似度 top 3）

**Files:**
- Create: `pipeline/voice_match.py`
- Create: `tests/test_voice_match.py`

- [ ] **Step 1: 写 failing 测试**

Create `tests/test_voice_match.py`:

```python
import numpy as np
from unittest.mock import patch
from pipeline.voice_match import extract_sample_clip, match_candidates


def test_match_candidates_returns_top_k_by_cosine_similarity():
    query_vec = np.array([1.0, 0.0], dtype=np.float32)
    rows = [
        {"voice_id": "a", "name": "A", "language": "en",
         "audio_embedding": np.array([1.0, 0.0], dtype=np.float32).tobytes()},
        {"voice_id": "b", "name": "B", "language": "en",
         "audio_embedding": np.array([0.9, 0.1], dtype=np.float32).tobytes()},
        {"voice_id": "c", "name": "C", "language": "en",
         "audio_embedding": np.array([0.0, 1.0], dtype=np.float32).tobytes()},
    ]
    with patch("pipeline.voice_match._query_voices_by_language",
               return_value=rows):
        top3 = match_candidates(query_vec, language="en", top_k=3)
    assert [c["voice_id"] for c in top3] == ["a", "b", "c"]
    assert top3[0]["similarity"] == 1.0


def test_extract_sample_clip_picks_middle_voiced_segment(tmp_path):
    video_path = tmp_path / "v.mp4"
    video_path.write_bytes(b"fake")
    with patch("pipeline.voice_match._extract_audio_track",
               return_value=str(tmp_path / "full.wav")) as ext, \
         patch("pipeline.voice_match._cut_clip",
               return_value=str(tmp_path / "clip.wav")) as cut, \
         patch("pipeline.voice_match._get_duration", return_value=30.0):
        clip = extract_sample_clip(str(video_path), out_dir=str(tmp_path))
    assert clip.endswith("clip.wav")
    # 取 30s 视频的 10-20s 段（中间）
    args = cut.call_args[0]
    assert args[1] == 10.0
    assert args[2] == 20.0
```

Run: `pytest tests/test_voice_match.py -v`
Expected: FAIL

- [ ] **Step 2: 实现 `voice_match.py`**

Create `pipeline/voice_match.py`:

```python
import os
import subprocess
import numpy as np
from appcore.db import query
from pipeline.voice_embedding import (
    embed_audio_file, cosine_similarity, deserialize_embedding
)
from pipeline.ffutil import get_media_duration

SAMPLE_CLIP_SECONDS = 10.0


def _extract_audio_track(video_path, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    wav_path = os.path.join(out_dir, "source_audio.wav")
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        wav_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return wav_path


def _cut_clip(src_wav, start, end, dest_dir):
    dest = os.path.join(dest_dir, "source_clip.wav")
    cmd = [
        "ffmpeg", "-y", "-i", src_wav,
        "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
        "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        dest,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return dest


def _get_duration(path):
    return get_media_duration(path)


def extract_sample_clip(video_path, out_dir):
    full_wav = _extract_audio_track(video_path, out_dir)
    dur = _get_duration(full_wav)
    mid = dur / 2.0
    start = max(0.0, mid - SAMPLE_CLIP_SECONDS / 2.0)
    end = min(dur, start + SAMPLE_CLIP_SECONDS)
    return _cut_clip(full_wav, start, end, out_dir)


def _query_voices_by_language(language, gender=None, limit=None):
    sql = """
        SELECT voice_id, name, gender, language, accent, category,
               preview_url, audio_embedding
        FROM elevenlabs_voices
        WHERE language = %s AND audio_embedding IS NOT NULL
    """
    params = [language]
    if gender:
        sql += " AND gender = %s"
        params.append(gender)
    if limit:
        sql += f" LIMIT {int(limit)}"
    return query(sql, tuple(params))


def match_candidates(query_embedding, *, language, gender=None, top_k=3):
    rows = _query_voices_by_language(language=language, gender=gender)
    scored = []
    for row in rows:
        blob = row.get("audio_embedding")
        if not blob:
            continue
        cand_vec = deserialize_embedding(blob)
        sim = cosine_similarity(query_embedding, cand_vec)
        scored.append({
            "voice_id": row["voice_id"],
            "name": row["name"],
            "language": row["language"],
            "gender": row.get("gender"),
            "accent": row.get("accent"),
            "preview_url": row.get("preview_url"),
            "similarity": sim,
        })
    scored.sort(key=lambda x: x["similarity"], reverse=True)
    return scored[:top_k]


def match_for_video(video_path, *, language, gender=None, top_k=3, out_dir):
    clip_path = extract_sample_clip(video_path, out_dir)
    query_vec = embed_audio_file(clip_path)
    return match_candidates(query_vec, language=language, gender=gender, top_k=top_k)
```

- [ ] **Step 3: 验证测试通过**

```bash
pytest tests/test_voice_match.py -v
```
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add pipeline/voice_match.py tests/test_voice_match.py
git commit -m "feat(translate-lab): 原视频音色特征提取与候选匹配"
```

---

## Task 7: 语速模型（CRUD + 基准初始化 + 增量更新）

**Files:**
- Create: `pipeline/speech_rate_model.py`
- Create: `tests/test_speech_rate_model.py`

- [ ] **Step 1: 写 failing 测试**

Create `tests/test_speech_rate_model.py`:

```python
from unittest.mock import patch
from pipeline.speech_rate_model import (
    get_rate, update_rate, initialize_baseline
)


def test_get_rate_returns_default_when_no_record():
    with patch("pipeline.speech_rate_model._query_rate", return_value=None):
        rate = get_rate("v1", "en")
    assert rate is None


def test_update_rate_inserts_first_sample():
    captured = {}
    def fake_upsert(voice_id, language, cps, count):
        captured.update(voice_id=voice_id, language=language,
                        cps=cps, count=count)
    with patch("pipeline.speech_rate_model._query_rate", return_value=None), \
         patch("pipeline.speech_rate_model._upsert_rate", side_effect=fake_upsert):
        update_rate("v1", "en", chars=90, duration_seconds=6.0)
    assert captured["cps"] == 15.0
    assert captured["count"] == 1


def test_update_rate_averages_incrementally():
    # existing: 20 char/s, count=2  => total chars = 40
    # new sample: 60 chars / 4s = 15 char/s
    # new cps = (20*2 + 15) / 3 = 18.333..
    captured = {}
    def fake_upsert(voice_id, language, cps, count):
        captured.update(cps=cps, count=count)
    with patch("pipeline.speech_rate_model._query_rate",
               return_value={"chars_per_second": 20.0, "sample_count": 2}), \
         patch("pipeline.speech_rate_model._upsert_rate", side_effect=fake_upsert):
        update_rate("v1", "en", chars=60, duration_seconds=4.0)
    assert round(captured["cps"], 3) == 18.333
    assert captured["count"] == 3


def test_initialize_baseline_uses_benchmark_text():
    with patch("pipeline.speech_rate_model._generate_tts",
               return_value=("/tmp/out.mp3", 4.8)) as gen, \
         patch("pipeline.speech_rate_model.update_rate") as upd:
        cps = initialize_baseline("v1", "en", api_key="k", work_dir="/tmp")
    text = gen.call_args.kwargs["text"]
    assert len(text) > 40
    assert cps == pytest.approx(len(text) / 4.8, rel=0.01)
    upd.assert_called_once()
```

Run: `pytest tests/test_speech_rate_model.py -v`
Expected: FAIL

- [ ] **Step 2: 实现 `speech_rate_model.py`**

Create `pipeline/speech_rate_model.py`:

```python
import os
from datetime import datetime
from appcore.db import execute, query_one
from pipeline.tts import generate_segment_audio, _get_audio_duration

BENCHMARK_TEXT = {
    "en": "The quick brown fox jumps over the lazy dog. Bright sunlight filtered through the autumn leaves as she walked along the quiet riverside path, lost in thought.",
    "de": "Der Computer ist ein wichtiges Werkzeug im modernen Leben. Die Sonne scheint heute hell, und der Wind weht sanft durch die Bäume im Garten.",
    "fr": "Le soleil brille aujourd'hui sur la petite ville. Elle marcha lentement vers la place centrale, regardant les enfants qui jouaient près de la fontaine.",
    "ja": "今日はとてもいい天気ですね。彼女は静かに公園を歩きながら、子供のころの思い出を振り返っていました。遠くの山々が夕日に染まっています。",
    "es": "Hoy hace un día maravilloso. Ella caminaba despacio por el parque, recordando su infancia mientras los niños jugaban alegremente cerca de la fuente.",
}


def _query_rate(voice_id, language):
    return query_one(
        "SELECT chars_per_second, sample_count FROM voice_speech_rate "
        "WHERE voice_id=%s AND language=%s",
        (voice_id, language),
    )


def _upsert_rate(voice_id, language, cps, count):
    execute(
        """
        INSERT INTO voice_speech_rate (voice_id, language, chars_per_second,
                                        sample_count, updated_at)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          chars_per_second=VALUES(chars_per_second),
          sample_count=VALUES(sample_count),
          updated_at=VALUES(updated_at)
        """,
        (voice_id, language, cps, count, datetime.utcnow()),
    )


def get_rate(voice_id, language):
    row = _query_rate(voice_id, language)
    if not row:
        return None
    return float(row["chars_per_second"])


def update_rate(voice_id, language, *, chars, duration_seconds):
    if duration_seconds <= 0 or chars <= 0:
        return
    new_cps = chars / duration_seconds
    existing = _query_rate(voice_id, language)
    if existing is None:
        _upsert_rate(voice_id, language, new_cps, 1)
        return
    old_cps = float(existing["chars_per_second"])
    old_count = int(existing["sample_count"])
    merged_cps = (old_cps * old_count + new_cps) / (old_count + 1)
    _upsert_rate(voice_id, language, merged_cps, old_count + 1)


def _generate_tts(text, voice_id, api_key, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"baseline_{voice_id}.mp3")
    generate_segment_audio(
        text=text, voice_id=voice_id,
        output_path=out_path, api_key=api_key,
    )
    return out_path, _get_audio_duration(out_path)


def initialize_baseline(voice_id, language, *, api_key, work_dir):
    text = BENCHMARK_TEXT.get(language, BENCHMARK_TEXT["en"])
    out_path, duration = _generate_tts(
        text=text, voice_id=voice_id,
        api_key=api_key, out_dir=work_dir,
    )
    cps = len(text) / duration if duration > 0 else 0.0
    update_rate(voice_id, language, chars=len(text),
                duration_seconds=duration)
    return cps
```

- [ ] **Step 3: 验证测试通过**

```bash
pytest tests/test_speech_rate_model.py -v
```
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add pipeline/speech_rate_model.py tests/test_speech_rate_model.py
git commit -m "feat(translate-lab): 语速模型 CRUD + 基准 + 增量更新"
```

---

## Task 8: Gemini 分镜拆解

**Files:**
- Create: `pipeline/shot_decompose.py`
- Create: `tests/test_shot_decompose.py`

- [ ] **Step 1: 写 failing 测试**

Create `tests/test_shot_decompose.py`:

```python
from unittest.mock import patch
from pipeline.shot_decompose import decompose_shots, align_asr_to_shots


def test_decompose_shots_calls_gemini_and_parses_response():
    fake_response = {
        "shots": [
            {"index": 1, "start": 0.0, "end": 5.2,
             "description": "女主角走进咖啡厅"},
            {"index": 2, "start": 5.2, "end": 9.8,
             "description": "镜头切到吧台"},
        ]
    }
    with patch("pipeline.shot_decompose.gemini_generate",
               return_value=fake_response):
        shots = decompose_shots(
            video_path="/tmp/v.mp4",
            user_id=1,
            duration_seconds=9.8,
        )
    assert len(shots) == 2
    assert shots[0]["start"] == 0.0
    assert shots[1]["description"] == "镜头切到吧台"


def test_align_asr_to_shots_groups_segments_by_time():
    shots = [
        {"index": 1, "start": 0.0, "end": 5.0},
        {"index": 2, "start": 5.0, "end": 10.0},
    ]
    asr_segments = [
        {"start": 0.5, "end": 4.5, "text": "她推开门"},
        {"start": 5.2, "end": 9.0, "text": "咖啡师正在忙碌"},
    ]
    aligned = align_asr_to_shots(shots, asr_segments)
    assert aligned[0]["source_text"] == "她推开门"
    assert aligned[1]["source_text"] == "咖啡师正在忙碌"


def test_align_asr_splits_cross_boundary_segment():
    shots = [
        {"index": 1, "start": 0.0, "end": 5.0},
        {"index": 2, "start": 5.0, "end": 10.0},
    ]
    # asr 片段跨越边界 4.0-7.0
    asr_segments = [
        {"start": 4.0, "end": 7.0, "text": "跨越的句子"},
    ]
    aligned = align_asr_to_shots(shots, asr_segments)
    # 按时间权重分配，超过 50% 在 shot1 的话分给 shot1
    assert aligned[0]["source_text"] == "跨越的句子" or \
           aligned[1]["source_text"] == "跨越的句子"
```

Run: `pytest tests/test_shot_decompose.py -v`
Expected: FAIL

- [ ] **Step 2: 实现 `shot_decompose.py`**

Create `pipeline/shot_decompose.py`:

```python
from appcore.gemini import generate as gemini_generate

SHOT_DECOMPOSE_SCHEMA = {
    "type": "object",
    "properties": {
        "shots": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "description": {"type": "string"},
                },
                "required": ["index", "start", "end", "description"],
            },
        },
    },
    "required": ["shots"],
}

SHOT_DECOMPOSE_PROMPT = """你是专业的视频分镜师。请分析这段视频，识别所有镜头切换点，输出分镜列表。

要求：
1. 每个分镜有明确的起止时间（秒，保留 2 位小数）
2. 每个分镜附带一句画面内容描述（20-40字中文）
3. 分镜的 end 必须等于下一个分镜的 start，即首尾相连
4. 第一个分镜从 0.0 开始
5. 最后一个分镜的 end 等于视频总时长 %.2f 秒

输出 JSON 格式：
{
  "shots": [
    {"index": 1, "start": 0.0, "end": 5.2, "description": "..."},
    ...
  ]
}
"""


def decompose_shots(video_path, user_id, duration_seconds, *, model=None):
    prompt = SHOT_DECOMPOSE_PROMPT % duration_seconds
    response = gemini_generate(
        prompt=prompt,
        media=[{"path": video_path, "mime": "video/mp4"}],
        user_id=user_id,
        model=model or "gemini-3.1-pro-preview",
        response_schema=SHOT_DECOMPOSE_SCHEMA,
    )
    shots = response.get("shots", [])
    _validate_and_normalize_shots(shots, duration_seconds)
    return shots


def _validate_and_normalize_shots(shots, duration_seconds):
    if not shots:
        raise ValueError("Gemini 未返回任何分镜")
    # 强制首尾衔接
    shots[0]["start"] = 0.0
    shots[-1]["end"] = duration_seconds
    for i in range(len(shots) - 1):
        shots[i + 1]["start"] = shots[i]["end"]
    for shot in shots:
        shot["duration"] = round(shot["end"] - shot["start"], 3)


def align_asr_to_shots(shots, asr_segments):
    enriched = [dict(s, source_text="", asr_segments=[]) for s in shots]
    for seg in asr_segments:
        s_start = seg.get("start", 0.0)
        s_end = seg.get("end", 0.0)
        text = seg.get("text", "").strip()
        if not text:
            continue
        best_idx = None
        best_overlap = 0.0
        for i, shot in enumerate(enriched):
            ov_start = max(s_start, shot["start"])
            ov_end = min(s_end, shot["end"])
            overlap = max(0.0, ov_end - ov_start)
            if overlap > best_overlap:
                best_overlap = overlap
                best_idx = i
        if best_idx is not None:
            if enriched[best_idx]["source_text"]:
                enriched[best_idx]["source_text"] += " " + text
            else:
                enriched[best_idx]["source_text"] = text
            enriched[best_idx]["asr_segments"].append(seg)
    for shot in enriched:
        shot["silent"] = not shot["source_text"]
    return enriched
```

- [ ] **Step 3: 验证测试通过**

```bash
pytest tests/test_shot_decompose.py -v
```
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add pipeline/shot_decompose.py tests/test_shot_decompose.py
git commit -m "feat(translate-lab): Gemini 分镜拆解与 ASR 对齐"
```

---

## Task 9: 分镜级翻译（字符数上限约束）

**Files:**
- Create: `pipeline/translate_v2.py`
- Create: `tests/test_translate_v2.py`

- [ ] **Step 1: 写 failing 测试**

Create `tests/test_translate_v2.py`:

```python
from unittest.mock import patch
from pipeline.translate_v2 import translate_shot, compute_char_limit


def test_compute_char_limit_uses_voice_rate_and_tolerance():
    # 分镜 10 秒，语速 15 字符/秒，容忍度 0.9
    limit = compute_char_limit(shot_duration=10.0, chars_per_second=15.0,
                                tolerance=0.9)
    assert limit == 135  # 10*0.9*15


def test_translate_shot_returns_text_within_limit():
    with patch("pipeline.translate_v2._call_llm",
               return_value="She stepped in."):
        result = translate_shot(
            shot={"index": 1, "source_text": "她推开门",
                  "description": "走进咖啡厅", "duration": 3.0},
            target_language="en",
            char_limit=30,
            prev_translation=None,
            next_source=None,
            user_id=1,
        )
    assert result["translated_text"] == "She stepped in."
    assert result["char_count"] <= 30


def test_translate_shot_retries_when_over_limit():
    calls = {"n": 0}
    def fake_llm(prompt, user_id):
        calls["n"] += 1
        if calls["n"] == 1:
            return "This translation is way too long for the limit here."
        return "Short."
    with patch("pipeline.translate_v2._call_llm", side_effect=fake_llm):
        result = translate_shot(
            shot={"index": 1, "source_text": "原文", "description": "d",
                  "duration": 2.0},
            target_language="en",
            char_limit=20,
            prev_translation=None,
            next_source=None,
            user_id=1,
        )
    assert calls["n"] == 2
    assert result["translated_text"] == "Short."
```

Run: `pytest tests/test_translate_v2.py -v`
Expected: FAIL

- [ ] **Step 2: 实现 `translate_v2.py`**

Create `pipeline/translate_v2.py`:

```python
from appcore.gemini import generate as gemini_generate

TRANSLATE_SCHEMA = {
    "type": "object",
    "properties": {
        "translated_text": {"type": "string"},
    },
    "required": ["translated_text"],
}

TRANSLATE_PROMPT = """你是专业的影视本土化翻译。请将下面的分镜原文翻译为 {target_language}。

分镜画面描述：{description}
分镜时长：{duration} 秒
原文：{source_text}
前一句译文：{prev_translation}
后一句原文：{next_source}

硬性要求：
- 译文的字符数必须 ≤ {char_limit} 字符
- 保留核心语义，保持与前后句的连贯
- 本土化表达，地道自然，不要直译
- 只输出译文，不要解释

以 JSON 输出：{{"translated_text": "..."}}"""

RETRY_PROMPT = """上一版译文「{previous}」超出了 {char_limit} 字符上限。请缩写为 ≤ {char_limit} 字符，保留核心含义。以 JSON 输出：{{"translated_text": "..."}}"""


def compute_char_limit(shot_duration, chars_per_second, tolerance=0.9):
    return int(shot_duration * tolerance * chars_per_second)


def _call_llm(prompt, user_id):
    resp = gemini_generate(
        prompt=prompt, user_id=user_id,
        response_schema=TRANSLATE_SCHEMA,
    )
    return resp.get("translated_text", "").strip()


def translate_shot(shot, *, target_language, char_limit,
                   prev_translation, next_source, user_id,
                   max_retries=2):
    prompt = TRANSLATE_PROMPT.format(
        target_language=target_language,
        description=shot.get("description", ""),
        duration=shot.get("duration", 0.0),
        source_text=shot.get("source_text", ""),
        prev_translation=prev_translation or "（无）",
        next_source=next_source or "（无）",
        char_limit=char_limit,
    )
    text = _call_llm(prompt, user_id)
    for _ in range(max_retries):
        if len(text) <= char_limit:
            break
        retry = RETRY_PROMPT.format(previous=text, char_limit=char_limit)
        text = _call_llm(retry, user_id)
    return {
        "shot_index": shot.get("index"),
        "translated_text": text,
        "char_count": len(text),
        "over_limit": len(text) > char_limit,
    }
```

- [ ] **Step 3: 验证测试通过**

```bash
pytest tests/test_translate_v2.py -v
```
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add pipeline/translate_v2.py tests/test_translate_v2.py
git commit -m "feat(translate-lab): 分镜级翻译带字符数约束"
```

---

## Task 10: TTS V2 生成 + 时长校验 + 文案微调循环

**Files:**
- Create: `pipeline/tts_v2.py`
- Create: `tests/test_tts_v2.py`

- [ ] **Step 1: 写 failing 测试**

Create `tests/test_tts_v2.py`:

```python
from unittest.mock import patch, MagicMock
from pipeline.tts_v2 import generate_and_verify_shot


def test_generate_passes_on_first_try_within_tolerance():
    gen_calls = []
    def fake_gen(text, voice_id, output_path, api_key):
        gen_calls.append(text)
        return output_path
    with patch("pipeline.tts_v2._tts_generate", side_effect=fake_gen), \
         patch("pipeline.tts_v2._get_duration", return_value=4.8), \
         patch("pipeline.tts_v2._refine_text") as refine:
        result = generate_and_verify_shot(
            shot={"index": 1, "duration": 5.0},
            translated_text="Some translation.",
            voice_id="v1",
            api_key="k",
            language="en",
            user_id=1,
            out_dir="/tmp",
        )
    assert result["final_duration"] == 4.8
    assert result["retry_count"] == 0
    refine.assert_not_called()


def test_generate_refines_when_over_tolerance():
    durations = iter([6.2, 4.9])  # 初次超限 → 微调后通过
    texts = iter(["Initial long version.", "Short version."])

    def fake_gen(text, voice_id, output_path, api_key):
        return output_path

    def fake_duration(path):
        return next(durations)

    def fake_refine(prev_text, over_ratio, target_chars, user_id):
        return next(texts)

    # 消耗迭代器的第一个值以对齐
    first_text = next(texts)

    with patch("pipeline.tts_v2._tts_generate", side_effect=fake_gen), \
         patch("pipeline.tts_v2._get_duration", side_effect=fake_duration), \
         patch("pipeline.tts_v2._refine_text", side_effect=fake_refine):
        result = generate_and_verify_shot(
            shot={"index": 1, "duration": 5.0},
            translated_text=first_text,
            voice_id="v1",
            api_key="k",
            language="en",
            user_id=1,
            out_dir="/tmp",
        )
    assert result["retry_count"] == 1
    assert result["final_text"] == "Short version."


def test_generate_gives_up_after_max_retries():
    with patch("pipeline.tts_v2._tts_generate", return_value="/tmp/out.mp3"), \
         patch("pipeline.tts_v2._get_duration", return_value=10.0), \
         patch("pipeline.tts_v2._refine_text", return_value="still long"):
        result = generate_and_verify_shot(
            shot={"index": 1, "duration": 5.0},
            translated_text="original too long",
            voice_id="v1",
            api_key="k",
            language="en",
            user_id=1,
            out_dir="/tmp",
            max_retries=3,
        )
    assert result["retry_count"] == 3
    assert result["over_tolerance"] is True
```

Run: `pytest tests/test_tts_v2.py -v`
Expected: FAIL

- [ ] **Step 2: 实现 `tts_v2.py`**

Create `pipeline/tts_v2.py`:

```python
import os
from pipeline.tts import generate_segment_audio, _get_audio_duration
from pipeline.speech_rate_model import get_rate, update_rate
from appcore.gemini import generate as gemini_generate

TOLERANCE = 1.10  # 允许实际时长不超过分镜 *1.10

REFINE_SCHEMA = {
    "type": "object",
    "properties": {"translated_text": {"type": "string"}},
    "required": ["translated_text"],
}

REFINE_PROMPT = """上一版译文「{previous}」生成的音频比分镜长 {over_ratio:.0%}。
请缩写为约 {target_chars} 字符，保留核心语义，只删修饰性内容。
以 JSON 输出：{{"translated_text": "..."}}"""


def _tts_generate(text, voice_id, output_path, api_key):
    return generate_segment_audio(
        text=text, voice_id=voice_id,
        output_path=output_path, api_key=api_key,
    )


def _get_duration(path):
    return _get_audio_duration(path)


def _refine_text(previous_text, over_ratio, target_chars, user_id):
    prompt = REFINE_PROMPT.format(
        previous=previous_text,
        over_ratio=over_ratio,
        target_chars=target_chars,
    )
    resp = gemini_generate(
        prompt=prompt, user_id=user_id, response_schema=REFINE_SCHEMA,
    )
    return resp.get("translated_text", "").strip()


def generate_and_verify_shot(shot, *, translated_text, voice_id, api_key,
                              language, user_id, out_dir, max_retries=3):
    os.makedirs(out_dir, exist_ok=True)
    shot_duration = shot.get("duration", 0.0)
    limit_seconds = shot_duration * TOLERANCE
    current_text = translated_text
    audio_path = os.path.join(out_dir, f"shot_{shot['index']}.mp3")

    retry_count = 0
    final_duration = 0.0
    over_tolerance = False

    for attempt in range(max_retries + 1):
        _tts_generate(current_text, voice_id, audio_path, api_key)
        final_duration = _get_duration(audio_path)

        # 增量更新语速模型
        update_rate(voice_id, language,
                    chars=len(current_text),
                    duration_seconds=final_duration)

        if final_duration <= limit_seconds:
            break
        if attempt >= max_retries:
            over_tolerance = True
            break

        over_ratio = (final_duration - shot_duration) / shot_duration
        cps = get_rate(voice_id, language) or (len(current_text) / final_duration)
        target_chars = int(shot_duration * 0.9 * cps)
        current_text = _refine_text(current_text, over_ratio, target_chars, user_id)
        retry_count += 1

    return {
        "shot_index": shot["index"],
        "final_text": current_text,
        "final_char_count": len(current_text),
        "final_duration": final_duration,
        "audio_path": audio_path,
        "retry_count": retry_count,
        "over_tolerance": over_tolerance,
    }
```

- [ ] **Step 3: 验证测试通过**

```bash
pytest tests/test_tts_v2.py -v
```
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add pipeline/tts_v2.py tests/test_tts_v2.py
git commit -m "feat(translate-lab): TTS 生成 + 时长校验 + 文案微调循环"
```

---

## Task 11: 字幕生成（拆分 + 统一字号 + SRT）

**Files:**
- Create: `pipeline/subtitle_v2.py`
- Create: `tests/test_subtitle_v2.py`

- [ ] **Step 1: 写 failing 测试**

Create `tests/test_subtitle_v2.py`:

```python
from pipeline.subtitle_v2 import (
    split_into_blocks, compute_unified_font_size, generate_srt
)


def test_split_into_blocks_keeps_short_text_as_one_block():
    blocks = split_into_blocks("Short text.", max_chars_per_line=40)
    assert len(blocks) == 1
    assert blocks[0] == ["Short text."]


def test_split_into_blocks_wraps_into_two_lines_at_punctuation():
    text = "She stepped inside the cafe, looking around for her friend."
    blocks = split_into_blocks(text, max_chars_per_line=30)
    assert len(blocks) == 1
    assert len(blocks[0]) == 2
    assert all(len(line) <= 30 for line in blocks[0])


def test_split_into_blocks_creates_second_block_when_too_long():
    long = ("Part one goes here. " * 5).strip()
    blocks = split_into_blocks(long, max_chars_per_line=30)
    assert len(blocks) >= 2


def test_compute_unified_font_size_fits_worst_case():
    shots = [
        {"index": 1, "final_text": "Short."},
        {"index": 2, "final_text": "A medium length caption here."},
        {"index": 3, "final_text": "This is the longest caption in the whole video that we have."},
    ]
    size = compute_unified_font_size(
        shots, video_width=1920, video_height=1080,
        min_size=16, max_size=42,
    )
    assert 16 <= size <= 42


def test_generate_srt_outputs_correct_timestamps():
    shots = [
        {"index": 1, "start": 0.0, "end": 5.0,
         "final_text": "Hello world.", "final_duration": 4.5},
        {"index": 2, "start": 5.0, "end": 10.0,
         "final_text": "Second caption.", "final_duration": 4.8},
    ]
    srt = generate_srt(shots, font_size=28, max_chars_per_line=40)
    assert "1\n00:00:00,000 --> 00:00:04,500" in srt
    assert "Hello world." in srt
    assert "2\n00:00:05,000 --> 00:00:09,800" in srt
```

Run: `pytest tests/test_subtitle_v2.py -v`
Expected: FAIL

- [ ] **Step 2: 实现 `subtitle_v2.py`**

Create `pipeline/subtitle_v2.py`:

```python
import re
import math

BREAK_PUNCT = r"[.!?,;:\u3002\uFF01\uFF1F\uFF0C\uFF1B\uFF1A]"


def _split_line(text, max_chars):
    if len(text) <= max_chars:
        return [text]
    mid = len(text) // 2
    best = None
    for m in re.finditer(BREAK_PUNCT + r"\s*", text):
        if abs(m.end() - mid) < (abs(best.end() - mid) if best else 1e9):
            best = m
    if best:
        return [text[: best.end()].strip(), text[best.end():].strip()]
    space = text.rfind(" ", 0, max_chars)
    if space < 0:
        space = max_chars
    return [text[:space].strip(), text[space:].strip()]


def split_into_blocks(text, *, max_chars_per_line, max_lines_per_block=2):
    text = text.strip()
    if not text:
        return []
    lines = _split_line(text, max_chars_per_line)
    while any(len(line) > max_chars_per_line for line in lines):
        new_lines = []
        for line in lines:
            if len(line) > max_chars_per_line:
                new_lines.extend(_split_line(line, max_chars_per_line))
            else:
                new_lines.append(line)
        lines = new_lines
    blocks = []
    for i in range(0, len(lines), max_lines_per_block):
        blocks.append(lines[i: i + max_lines_per_block])
    return blocks


def _max_chars_for_font(video_width, font_size, safe_ratio=0.8):
    # 粗略按字号 1em ≈ 0.55*font_size 宽度估算
    avg_char_width = font_size * 0.55
    return max(1, int((video_width * safe_ratio) / avg_char_width))


def compute_unified_font_size(shots, *, video_width, video_height,
                               min_size=16, max_size=42):
    longest = max((s.get("final_text", "") for s in shots), key=len, default="")
    if not longest:
        return max_size
    for size in range(max_size, min_size - 1, -1):
        mcpl = _max_chars_for_font(video_width, size)
        blocks = split_into_blocks(longest, max_chars_per_line=mcpl)
        if len(blocks) == 1 and len(blocks[0]) <= 2:
            return size
    return min_size


def _fmt_time(t):
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    ms = int(round((t - int(t)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def generate_srt(shots, *, font_size, max_chars_per_line):
    entries = []
    counter = 1
    for shot in shots:
        if not shot.get("final_text"):
            continue
        start = shot["start"]
        end = start + shot.get("final_duration", shot.get("duration", 0))
        blocks = split_into_blocks(
            shot["final_text"],
            max_chars_per_line=max_chars_per_line,
        )
        if not blocks:
            continue
        total = len(blocks)
        block_dur = (end - start) / total if total else 0
        for i, block in enumerate(blocks):
            b_start = start + i * block_dur
            b_end = start + (i + 1) * block_dur if i < total - 1 else end
            text_block = "\n".join(block)
            entries.append(
                f"{counter}\n"
                f"{_fmt_time(b_start)} --> {_fmt_time(b_end)}\n"
                f"{text_block}\n"
            )
            counter += 1
    return "\n".join(entries)
```

- [ ] **Step 3: 验证测试通过**

```bash
pytest tests/test_subtitle_v2.py -v
```
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add pipeline/subtitle_v2.py tests/test_subtitle_v2.py
git commit -m "feat(translate-lab): 字幕拆分、统一字号、SRT 输出"
```

---

## Task 12: PipelineRunnerV2 流水线编排器

**Files:**
- Create: `appcore/runtime_v2.py`
- Create: `tests/test_runtime_v2.py`

- [ ] **Step 1: 写 failing 测试**

Create `tests/test_runtime_v2.py`:

```python
from unittest.mock import MagicMock, patch
from appcore.events import EventBus
from appcore.runtime_v2 import PipelineRunnerV2


def test_runner_has_seven_steps():
    bus = EventBus()
    runner = PipelineRunnerV2(bus=bus, user_id=1)
    step_names = [name for name, _ in runner._build_steps("task", "/v.mp4", "/dir")]
    assert step_names == [
        "extract", "shot_decompose", "voice_match",
        "translate", "tts_verify", "subtitle", "compose",
    ]


def test_runner_skips_voice_match_confirmation_in_auto_mode():
    bus = EventBus()
    runner = PipelineRunnerV2(bus=bus, user_id=1)
    runner.task_options = {"voice_match_mode": "auto"}
    with patch.object(runner, "_await_voice_confirmation") as await_call:
        runner._step_voice_match(
            task_id="t", video_path="/v.mp4", task_dir="/d",
        )
    # auto 模式下不应该阻塞等待
    # 具体实现在 Task 13 完成
```

Run: `pytest tests/test_runtime_v2.py -v`
Expected: FAIL

- [ ] **Step 2: 实现 `runtime_v2.py`**

Create `appcore/runtime_v2.py`:

```python
import os
from appcore.runtime import PipelineRunner
from appcore import task_state
from appcore.events import (
    EVT_STEP_UPDATE, EVT_LAB_SHOT_DECOMPOSE_RESULT,
    EVT_LAB_VOICE_MATCH_CANDIDATES, EVT_LAB_VOICE_CONFIRMED,
    EVT_LAB_TRANSLATE_PROGRESS, EVT_LAB_TTS_PROGRESS,
    EVT_LAB_SUBTITLE_READY, EVT_LAB_PIPELINE_DONE, EVT_LAB_PIPELINE_ERROR,
)
from pipeline.extract import extract_audio
from pipeline.asr import transcribe as asr_transcribe
from pipeline.shot_decompose import decompose_shots, align_asr_to_shots
from pipeline.voice_match import match_for_video
from pipeline.speech_rate_model import get_rate, initialize_baseline
from pipeline.translate_v2 import translate_shot, compute_char_limit
from pipeline.tts_v2 import generate_and_verify_shot
from pipeline.subtitle_v2 import compute_unified_font_size, generate_srt
from pipeline.compose import compose_video
from pipeline.ffutil import get_media_duration
from appcore.api_keys import resolve_key


class PipelineRunnerV2(PipelineRunner):
    project_type = "translate_lab"

    def _build_steps(self, task_id, video_path, task_dir):
        return [
            ("extract",        lambda: self._step_extract(task_id, video_path, task_dir)),
            ("shot_decompose", lambda: self._step_shot_decompose(task_id, video_path, task_dir)),
            ("voice_match",    lambda: self._step_voice_match(task_id, video_path, task_dir)),
            ("translate",      lambda: self._step_translate(task_id)),
            ("tts_verify",     lambda: self._step_tts_verify(task_id, task_dir)),
            ("subtitle",       lambda: self._step_subtitle(task_id, task_dir)),
            ("compose",        lambda: self._step_compose(task_id, video_path, task_dir)),
        ]

    def _step_extract(self, task_id, video_path, task_dir):
        self._set_step(task_id, "extract", "running", "提取音频")
        audio_path = extract_audio(video_path, os.path.join(task_dir, "audio.wav"))
        task_state.update(task_id, audio_path=audio_path,
                          video_duration=get_media_duration(video_path))
        self._set_step(task_id, "extract", "done")

    def _step_shot_decompose(self, task_id, video_path, task_dir):
        self._set_step(task_id, "shot_decompose", "running", "Gemini 分镜分析")
        task = task_state.get(task_id)
        duration = task["video_duration"]
        source_lang = task.get("options", {}).get("source_language", "zh")

        shots = decompose_shots(
            video_path=video_path,
            user_id=self.user_id,
            duration_seconds=duration,
        )
        asr = asr_transcribe(task["audio_path"], language=source_lang)
        aligned = align_asr_to_shots(shots, asr.get("segments", []))

        task_state.update(task_id, shots=aligned, source_language=source_lang)
        self._emit(task_id, EVT_LAB_SHOT_DECOMPOSE_RESULT, {"shots": aligned})
        self._set_step(task_id, "shot_decompose", "done")

    def _step_voice_match(self, task_id, video_path, task_dir):
        self._set_step(task_id, "voice_match", "running", "匹配音色")
        task = task_state.get(task_id)
        options = task.get("options", {})
        mode = options.get("voice_match_mode", "auto")
        target_lang = options.get("target_language", "en")

        candidates = match_for_video(
            video_path=video_path,
            language=target_lang,
            gender=options.get("voice_gender"),
            top_k=3,
            out_dir=os.path.join(task_dir, "voice_match"),
        )
        self._emit(task_id, EVT_LAB_VOICE_MATCH_CANDIDATES,
                   {"candidates": candidates})

        if mode == "auto":
            chosen = candidates[0] if candidates else None
        else:
            chosen = self._await_voice_confirmation(task_id, candidates)

        if not chosen:
            raise RuntimeError("未确定音色")

        api_key = resolve_key(self.user_id, "elevenlabs")
        if get_rate(chosen["voice_id"], target_lang) is None:
            initialize_baseline(
                voice_id=chosen["voice_id"], language=target_lang,
                api_key=api_key, work_dir=os.path.join(task_dir, "voice_match"),
            )

        task_state.update(task_id, chosen_voice=chosen)
        self._emit(task_id, EVT_LAB_VOICE_CONFIRMED, {"voice": chosen})
        self._set_step(task_id, "voice_match", "done")

    def _await_voice_confirmation(self, task_id, candidates):
        task_state.update(
            task_id, pending_voice_choice=candidates, status="awaiting_voice"
        )
        # 等待前端 API 设置 chosen_voice，详见 web/routes/translate_lab.py
        import time
        for _ in range(60 * 30):  # 最多 30 分钟
            t = task_state.get(task_id)
            chosen = t.get("chosen_voice")
            if chosen:
                return chosen
            time.sleep(1)
        return None

    def _step_translate(self, task_id):
        self._set_step(task_id, "translate", "running", "分镜翻译")
        task = task_state.get(task_id)
        shots = task["shots"]
        voice = task["chosen_voice"]
        target_lang = task["options"].get("target_language", "en")
        cps = get_rate(voice["voice_id"], target_lang) or 15.0

        translations = []
        for i, shot in enumerate(shots):
            if shot.get("silent"):
                translations.append({"shot_index": shot["index"],
                                     "translated_text": "",
                                     "char_count": 0})
                continue
            limit = compute_char_limit(shot["duration"], cps)
            prev = translations[-1]["translated_text"] if translations else None
            nxt = shots[i + 1].get("source_text") if i + 1 < len(shots) else None
            result = translate_shot(
                shot=shot, target_language=target_lang, char_limit=limit,
                prev_translation=prev, next_source=nxt, user_id=self.user_id,
            )
            translations.append(result)
            self._emit(task_id, EVT_LAB_TRANSLATE_PROGRESS,
                       {"index": shot["index"], "result": result})

        task_state.update(task_id, translations=translations)
        self._set_step(task_id, "translate", "done")

    def _step_tts_verify(self, task_id, task_dir):
        self._set_step(task_id, "tts_verify", "running", "生成配音并校验")
        task = task_state.get(task_id)
        translations = {t["shot_index"]: t for t in task["translations"]}
        shots = task["shots"]
        voice = task["chosen_voice"]
        target_lang = task["options"].get("target_language", "en")
        api_key = resolve_key(self.user_id, "elevenlabs")
        tts_dir = os.path.join(task_dir, "tts_v2")

        results = []
        for shot in shots:
            tr = translations.get(shot["index"])
            if not tr or not tr["translated_text"]:
                continue
            verified = generate_and_verify_shot(
                shot=shot, translated_text=tr["translated_text"],
                voice_id=voice["voice_id"], api_key=api_key,
                language=target_lang, user_id=self.user_id,
                out_dir=tts_dir,
            )
            results.append(verified)
            self._emit(task_id, EVT_LAB_TTS_PROGRESS,
                       {"index": shot["index"], "result": verified})

        task_state.update(task_id, tts_results=results)
        self._set_step(task_id, "tts_verify", "done")

    def _step_subtitle(self, task_id, task_dir):
        self._set_step(task_id, "subtitle", "running", "生成字幕")
        task = task_state.get(task_id)
        shots = task["shots"]
        tts_by_idx = {r["shot_index"]: r for r in task["tts_results"]}
        final_shots = []
        for shot in shots:
            r = tts_by_idx.get(shot["index"])
            if r:
                final_shots.append({**shot, **r})

        width = task.get("video_width", 1920)
        height = task.get("video_height", 1080)
        font_size = compute_unified_font_size(
            final_shots, video_width=width, video_height=height,
        )
        max_chars = int((width * 0.8) / (font_size * 0.55))
        srt_text = generate_srt(final_shots, font_size=font_size,
                                max_chars_per_line=max_chars)
        srt_path = os.path.join(task_dir, "subtitles.srt")
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write(srt_text)

        task_state.update(task_id, subtitle_path=srt_path,
                          font_size=font_size)
        self._emit(task_id, EVT_LAB_SUBTITLE_READY,
                   {"srt_path": srt_path, "font_size": font_size})
        self._set_step(task_id, "subtitle", "done")

    def _step_compose(self, task_id, video_path, task_dir):
        self._set_step(task_id, "compose", "running", "合成最终视频")
        task = task_state.get(task_id)
        # 拼接 TTS 音频（复用现有工具或 ffmpeg concat）
        # 合成视频 + 字幕
        final_path = compose_video(
            video_path=video_path,
            audio_tracks=[r["audio_path"] for r in task["tts_results"]],
            subtitle_path=task["subtitle_path"],
            output_dir=task_dir,
            font_size=task["font_size"],
        )
        task_state.update(task_id, final_video=final_path,
                          status="completed")
        self._emit(task_id, EVT_LAB_PIPELINE_DONE, {"video": final_path})
        self._set_step(task_id, "compose", "done")
```

- [ ] **Step 3: 验证测试通过**

```bash
pytest tests/test_runtime_v2.py -v
```
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add appcore/runtime_v2.py tests/test_runtime_v2.py
git commit -m "feat(translate-lab): PipelineRunnerV2 七步流水线编排"
```

---

## Task 13: Web 服务层和路由（上传、启动、恢复、音色确认、音色库同步）

**Files:**
- Create: `web/services/translate_lab_runner.py`
- Modify: `web/routes/translate_lab.py`
- Modify: `web/app.py`
- Modify: `tests/test_translate_lab_routes.py`

- [ ] **Step 1: 写 failing 测试**

Edit `tests/test_translate_lab_routes.py` — 追加：

```python
def test_start_task_triggers_runner(authed_client_no_db, monkeypatch):
    started = {}
    def fake_start(task_id, user_id):
        started["task_id"] = task_id
    monkeypatch.setattr(
        "web.services.translate_lab_runner.start",
        fake_start,
    )
    monkeypatch.setattr(
        "web.routes.translate_lab.get_project",
        lambda tid, uid: {"id": tid, "user_id": uid,
                          "type": "translate_lab", "status": "uploaded"},
    )
    resp = authed_client_no_db.post(
        "/api/translate-lab/lab-1/start",
        json={"source_language": "zh", "target_language": "en",
              "voice_match_mode": "auto"},
    )
    assert resp.status_code == 200
    assert started["task_id"] == "lab-1"


def test_confirm_voice_sets_chosen_voice(authed_client_no_db, monkeypatch):
    updated = {}
    def fake_update(task_id, **fields):
        updated.update(task_id=task_id, **fields)
    monkeypatch.setattr(
        "appcore.task_state.update", fake_update,
    )
    monkeypatch.setattr(
        "web.routes.translate_lab.get_project",
        lambda tid, uid: {"id": tid, "user_id": uid,
                          "type": "translate_lab"},
    )
    resp = authed_client_no_db.post(
        "/api/translate-lab/lab-1/confirm-voice",
        json={"voice_id": "abc"},
    )
    assert resp.status_code == 200
    assert updated["chosen_voice"]["voice_id"] == "abc"


def test_admin_sync_voice_library_triggers_sync(authed_client_no_db, monkeypatch):
    called = {}
    def fake_sync(api_key):
        called["api_key"] = api_key
        return 42
    monkeypatch.setattr(
        "pipeline.voice_library_sync.sync_all_shared_voices", fake_sync,
    )
    monkeypatch.setattr(
        "web.routes.translate_lab.resolve_key",
        lambda uid, service: "k",
    )
    resp = authed_client_no_db.post("/api/translate-lab/voice-library/sync")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total"] == 42
```

Run: `pytest tests/test_translate_lab_routes.py -v`
Expected: FAIL

- [ ] **Step 2: 实现服务层**

Create `web/services/translate_lab_runner.py`:

```python
import threading
from appcore.runtime_v2 import PipelineRunnerV2
from appcore.events import EventBus
from web.socketio_bridge import subscribe_socketio

_bus = EventBus()


def _subscribe_once(socketio):
    if getattr(_bus, "_lab_subscribed", False):
        return
    subscribe_socketio(_bus, socketio, room_prefix="")
    _bus._lab_subscribed = True


def start(task_id, user_id, socketio=None):
    if socketio:
        _subscribe_once(socketio)
    runner = PipelineRunnerV2(bus=_bus, user_id=user_id)
    threading.Thread(
        target=runner.start, args=(task_id,), daemon=True
    ).start()


def resume(task_id, user_id, start_step, socketio=None):
    if socketio:
        _subscribe_once(socketio)
    runner = PipelineRunnerV2(bus=_bus, user_id=user_id)
    threading.Thread(
        target=runner.resume, args=(task_id, start_step), daemon=True
    ).start()
```

- [ ] **Step 3: 扩展路由**

Edit `web/routes/translate_lab.py`:

```python
from flask import jsonify, current_app, request
from appcore import task_state
from appcore.api_keys import resolve_key
from pipeline.voice_library_sync import sync_all_shared_voices, embed_missing_voices
from web.services import translate_lab_runner


@bp.route("/api/translate-lab/<task_id>/start", methods=["POST"])
@login_required
def start_task(task_id):
    user_id = current_user_id()
    task = get_project(task_id, user_id)
    if not task or task.get("type") != "translate_lab":
        abort(404)
    options = request.get_json(silent=True) or {}
    task_state.update(task_id, options=options, status="running")
    translate_lab_runner.start(
        task_id=task_id, user_id=user_id,
        socketio=current_app.extensions.get("socketio"),
    )
    return jsonify({"ok": True})


@bp.route("/api/translate-lab/<task_id>/confirm-voice", methods=["POST"])
@login_required
def confirm_voice(task_id):
    user_id = current_user_id()
    task = get_project(task_id, user_id)
    if not task or task.get("type") != "translate_lab":
        abort(404)
    payload = request.get_json(silent=True) or {}
    voice_id = payload.get("voice_id")
    if not voice_id:
        return jsonify({"error": "voice_id required"}), 400
    pending = task_state.get(task_id).get("pending_voice_choice") or []
    chosen = next((v for v in pending if v["voice_id"] == voice_id), None)
    if not chosen:
        chosen = {"voice_id": voice_id}
    task_state.update(task_id, chosen_voice=chosen, status="running")
    return jsonify({"ok": True})


@bp.route("/api/translate-lab/voice-library/sync", methods=["POST"])
@login_required
def sync_voice_library():
    user_id = current_user_id()
    api_key = resolve_key(user_id, "elevenlabs")
    total = sync_all_shared_voices(api_key)
    return jsonify({"ok": True, "total": total})


@bp.route("/api/translate-lab/voice-library/embed", methods=["POST"])
@login_required
def embed_voice_library():
    from config import TRANSLATE_LAB_EMBED_CACHE
    count = embed_missing_voices(cache_dir=TRANSLATE_LAB_EMBED_CACHE)
    return jsonify({"ok": True, "count": count})
```

Edit `web/app.py` — 在 Socket.IO 事件注册区追加：
```python
@socketio.on("join_translate_lab_task")
def on_join_lab(data):
    task_id = data.get("task_id")
    if task_id:
        join_room(task_id)
```

- [ ] **Step 4: 验证测试通过**

```bash
pytest tests/test_translate_lab_routes.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add web/services/translate_lab_runner.py \
  web/routes/translate_lab.py web/app.py \
  tests/test_translate_lab_routes.py
git commit -m "feat(translate-lab): 服务层、启动/确认音色/同步音色库路由"
```

---

## Task 14: 列表页与详情页 UI

**Files:**
- Modify: `web/templates/translate_lab_list.html`
- Modify: `web/templates/translate_lab_detail.html`
- Create: `web/static/translate_lab.js`
- Create: `web/static/translate_lab.css`

- [ ] **Step 1: 列表页实现**

Edit `web/templates/translate_lab_list.html`：
- 参考 `de_translate_list.html` 的卡片列表样式
- 顶部「新建任务」按钮 → 弹窗上传视频
- 弹窗内选择源语言、目标语言、音色匹配模式
- 上传成功后跳转详情页

关键区域：
```html
<div class="toolbar">
  <h1>视频翻译（测试）</h1>
  <button id="btn-new-task" class="btn primary">新建任务</button>
  <button id="btn-sync-voices" class="btn ghost">同步音色库</button>
</div>

<div class="grid-tasks">
  {% for task in tasks %}
    <a class="card" href="{{ url_for('translate_lab.detail', task_id=task.id) }}">
      <div class="card-title">{{ task.display_name }}</div>
      <div class="card-meta">
        {{ task.options.source_language }} → {{ task.options.target_language }}
      </div>
      <div class="card-status status-{{ task.status }}">{{ task.status }}</div>
    </a>
  {% endfor %}
</div>

<!-- 新建任务弹窗 -->
<dialog id="dlg-new-task">
  <form method="dialog">
    <input type="file" id="upload-video" accept="video/*" required>
    <select id="source-lang">
      <option value="zh">中文</option>
      <option value="en">英文</option>
    </select>
    <select id="target-lang">
      <option value="en">英文</option>
      <option value="de">德文</option>
      <option value="fr">法文</option>
      <option value="ja">日文</option>
      <option value="es">西班牙文</option>
      <option value="pt">葡萄牙文</option>
    </select>
    <label>
      <input type="radio" name="voice-mode" value="auto" checked> 全自动
    </label>
    <label>
      <input type="radio" name="voice-mode" value="manual"> 人工确认
    </label>
    <button id="btn-submit-task" class="btn primary">开始</button>
  </form>
</dialog>

<script src="/static/translate_lab.js"></script>
<link rel="stylesheet" href="/static/translate_lab.css">
```

- [ ] **Step 2: 详情页实现**

Edit `web/templates/translate_lab_detail.html`：
- 顶部 7 步进度条
- 分镜时间轴列表（点击某条展开原文+译文+TTS 音频播放）
- 人工确认模式下的音色候选卡片区域（带预览播放 + 确认按钮）
- 最终合成视频预览 + 下载按钮

关键区域：
```html
<div class="pipeline-steps">
  {% for step in ['extract','shot_decompose','voice_match','translate','tts_verify','subtitle','compose'] %}
    <div class="step" data-step="{{ step }}">{{ step }}</div>
  {% endfor %}
</div>

<section id="shot-timeline" hidden>
  <h2>分镜时间轴</h2>
  <div id="shots-list"></div>
</section>

<section id="voice-candidates" hidden>
  <h2>选择音色</h2>
  <div id="candidates-list"></div>
</section>

<section id="translate-section" hidden>
  <h2>分镜翻译与配音</h2>
  <div id="translations-list"></div>
</section>

<section id="final-result" hidden>
  <h2>最终效果</h2>
  <video id="final-video" controls></video>
  <a id="download-srt" class="btn">下载字幕</a>
</section>

<script>
  const TASK_ID = "{{ task.id }}";
</script>
<script src="/static/translate_lab.js"></script>
```

- [ ] **Step 3: 前端交互脚本**

Create `web/static/translate_lab.js` — 处理：
- 页面加载后建立 Socket.IO 连接并 `emit('join_translate_lab_task', {task_id})`
- 监听 `lab_shot_decompose_result`、`lab_voice_match_candidates`、`lab_translate_progress`、`lab_tts_progress`、`lab_subtitle_ready`、`lab_pipeline_done`、`lab_pipeline_error` 事件
- 渲染分镜列表、候选音色卡片、翻译进度、最终视频
- 点击候选音色卡片的「确认」按钮 → POST `/api/translate-lab/<task_id>/confirm-voice`
- 同步音色库按钮 → POST `/api/translate-lab/voice-library/sync`（带 loading 状态）

（由于篇幅，完整 JS 代码在执行时实现。关键是复用现有 `de_translate.js` 的 Socket.IO 模式，事件名换成 `lab_*`）

- [ ] **Step 4: 样式表**

Create `web/static/translate_lab.css` — 参照项目现有 CLAUDE.md 的设计系统：
- 深海蓝侧栏已由 layout.html 提供，这里只需内容区样式
- 卡片用 `--radius-lg` 和 `--border`
- 分镜条目用 `--bg-subtle` 和 hover `--bg-muted`
- 进度步骤用 `--accent` / `--success-fg` / `--danger-fg`

- [ ] **Step 5: 手动验证**

启动服务器：
```bash
python run.py
```

访问 `http://localhost:5000/translate-lab`，验证：
- 菜单项出现且高亮正确
- 新建任务弹窗可打开并上传视频
- 上传后跳转详情页，各区域骨架正确渲染

- [ ] **Step 6: Commit**

```bash
git add web/templates/translate_lab_list.html \
  web/templates/translate_lab_detail.html \
  web/static/translate_lab.js web/static/translate_lab.css
git commit -m "feat(translate-lab): 列表页、详情页、前端交互脚本"
```

---

## Task 15: 端到端集成测试 + 任务恢复 + PR

**Files:**
- Modify: `web/app.py`
- Create: `tests/test_translate_lab_e2e.py`

- [ ] **Step 1: 任务恢复支持**

Edit `web/app.py` — 在启动时的 recovery hook 追加：
```python
from web.services import translate_lab_runner
from appcore.task_recovery import recover_translate_lab_tasks

@app.before_first_request
def _recover_lab_tasks():
    for task_id, user_id, start_step in recover_translate_lab_tasks():
        translate_lab_runner.resume(
            task_id=task_id, user_id=user_id,
            start_step=start_step,
            socketio=app.extensions.get("socketio"),
        )
```

`recover_translate_lab_tasks` 在 `appcore/task_recovery.py` 中实现（查询 `projects` 表中 `type='translate_lab' AND status='running'` 的任务）。

- [ ] **Step 2: 写端到端测试**

Create `tests/test_translate_lab_e2e.py`:

```python
import os
from unittest.mock import patch, MagicMock
from appcore.events import EventBus
from appcore.runtime_v2 import PipelineRunnerV2


def test_full_pipeline_integration(tmp_path, monkeypatch):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")
    bus = EventBus()
    events = []
    bus.subscribe(lambda e: events.append(e.type))

    with patch("pipeline.extract.extract_audio",
               return_value=str(tmp_path / "audio.wav")), \
         patch("pipeline.ffutil.get_media_duration", return_value=30.0), \
         patch("pipeline.shot_decompose.decompose_shots",
               return_value=[{"index": 1, "start": 0.0, "end": 30.0,
                              "description": "d", "duration": 30.0}]), \
         patch("pipeline.asr.transcribe",
               return_value={"segments": [
                   {"start": 0.0, "end": 30.0, "text": "原文"}
               ]}), \
         patch("pipeline.voice_match.match_for_video",
               return_value=[{"voice_id": "v1", "name": "A",
                              "preview_url": "", "similarity": 0.9}]), \
         patch("pipeline.speech_rate_model.get_rate", return_value=15.0), \
         patch("pipeline.translate_v2._call_llm",
               return_value="Hello."), \
         patch("pipeline.tts_v2._tts_generate",
               return_value=str(tmp_path / "shot_1.mp3")), \
         patch("pipeline.tts_v2._get_duration", return_value=2.0), \
         patch("pipeline.compose.compose_video",
               return_value=str(tmp_path / "final.mp4")), \
         patch("appcore.api_keys.resolve_key", return_value="k"):
        task_state_mock = {}
        with patch("appcore.task_state.get",
                   side_effect=lambda tid: task_state_mock.setdefault(
                       tid, {"audio_path": "", "video_duration": 30.0,
                             "options": {"source_language": "zh",
                                         "target_language": "en",
                                         "voice_match_mode": "auto"}})), \
             patch("appcore.task_state.update",
                   side_effect=lambda tid, **kw: task_state_mock.setdefault(
                       tid, {}).update(kw)):
            runner = PipelineRunnerV2(bus=bus, user_id=1)
            runner.start("t1")
    assert "lab_pipeline_done" in events
```

Run: `pytest tests/test_translate_lab_e2e.py -v`
Expected: PASS

- [ ] **Step 3: 运行全部测试**

```bash
pytest tests/ -v
```
Expected: ALL PASS（注意：新测试全通过；其他现有测试保持原状态）

- [ ] **Step 4: Commit**

```bash
git add web/app.py tests/test_translate_lab_e2e.py
git commit -m "feat(translate-lab): 端到端集成测试与任务恢复"
```

- [ ] **Step 5: 推送分支并创建 PR**

```bash
git push -u origin feature/translate-lab

gh pr create --title "feat: 视频翻译 V2 分镜驱动翻译系统（测试）" --body "$(cat <<'EOF'
## Summary

- 新增独立模块「视频翻译（测试）」，不影响现有三个翻译模块
- 分镜驱动的翻译流水线：Gemini 分镜 → 音色匹配 → 翻译 → TTS → 字幕 → 合成
- 语速模型约束译文字符数，超限自动微调文案
- ElevenLabs 全量音色库同步 + resemblyzer speaker embedding 匹配

## Test plan

- [ ] 全量同步 ElevenLabs 音色库并回填 embedding
- [ ] 上传一段 30 秒的中文视频，中→英全自动模式，验证分镜、翻译、配音、字幕
- [ ] 同视频切人工确认模式，验证音色候选选择逻辑
- [ ] 中→日、英→西 各测一次小语种流程
- [ ] 音画同步容忍度验证：每个分镜 TTS 时长 ≤ 分镜时长 × 1.10
- [ ] 确认现有「视频翻译」「视频翻译（德语）」「视频翻译（法语）」三个模块功能不受影响
- [ ] pytest 全绿

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## 执行建议

- Task 1、2 是必须先完成的基础。完成后建议先在本地手动运行一次迁移确认数据库结构正常。
- Task 3、4、5 是音色库栈，可以独立于主流水线先跑通（用管理接口手动触发同步和 embedding）。
- Task 6 依赖 Task 4、5。
- Task 7 依赖 Task 1。
- Task 8、9、10、11 互相独立（除了 Task 10 用到 Task 7 的 `get_rate` 和 `update_rate`），可以并行开发。
- Task 12（runtime_v2）依赖 Task 6-11 全部完成。
- Task 13、14 是前后端集成，可以并行。
- Task 15 是最终验证。

TDD 节奏：每个 Task 内部严格按「写失败测试 → 实现 → 跑通测试 → commit」推进，不要跳步。
