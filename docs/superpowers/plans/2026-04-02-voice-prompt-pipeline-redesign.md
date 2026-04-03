# 音色选择、翻译提示词、Pipeline 单线化重构

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 音色系统迁移到数据库按用户隔离并改为列表式 UI；翻译提示词改为用户级可选可编辑支持多次翻译对比；Pipeline 从翻译往后合并为单线。

**Architecture:** `user_voices` 和 `user_prompts` 两张新表存储用户级数据；`VoiceLibrary` 改为读写数据库；pipeline 各步骤去掉 variant 循环固定为 `"normal"`；新增 retranslate/select-translation API 支持多次翻译对比。

**Tech Stack:** Flask + SocketIO + pymysql + ElevenLabs API，已有依赖无新增。

---

## File Map

**New files:**
- `db/migrations/002_user_voices_and_prompts.sql` — 建表 SQL
- `web/routes/prompt.py` — 翻译提示词 CRUD 蓝图

**Modified files:**
- `pipeline/voice_library.py` — 改为读写数据库，所有方法加 `user_id` 参数
- `pipeline/elevenlabs_voices.py` — `import_voice` 加 `user_id` 参数
- `web/routes/voice.py` — 所有端点加 `user_id` 过滤和 `@login_required`
- `web/app.py` — 注册 prompt 蓝图
- `appcore/runtime.py` — 各步骤去掉 variant 循环，translate 接收 prompt 参数
- `appcore/task_state.py` — create() 只初始化 normal variant
- `pipeline/localization.py` — 导出默认提示词文本，build_localized_translation_messages 接收 custom prompt
- `pipeline/translate.py` — generate_localized_translation 接收 custom_system_prompt
- `web/routes/task.py` — 新增 retranslate、select-translation 端点
- `web/templates/_task_workbench.html` — 音色区块独立、翻译提示词 UI
- `web/templates/_task_workbench_scripts.html` — 音色列表交互、提示词选择/编辑/重新翻译
- `web/templates/_task_workbench_styles.html` — 新区块样式

---

## Task 1: 数据库建表

**Files:**
- Create: `db/migrations/002_user_voices_and_prompts.sql`
- Modify: `db/schema.sql`

- [ ] **Step 1: 写建表 SQL**

创建 `db/migrations/002_user_voices_and_prompts.sql`：

```sql
CREATE TABLE IF NOT EXISTS user_voices (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    name VARCHAR(100) NOT NULL,
    gender ENUM('male','female') NOT NULL,
    elevenlabs_voice_id VARCHAR(50) NOT NULL,
    description TEXT DEFAULT '',
    style_tags JSON DEFAULT NULL,
    preview_url VARCHAR(500) DEFAULT '',
    source VARCHAR(50) DEFAULT 'manual',
    labels JSON DEFAULT NULL,
    is_default BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_user_voice (user_id, elevenlabs_voice_id)
);

CREATE TABLE IF NOT EXISTS user_prompts (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    name VARCHAR(100) NOT NULL,
    prompt_text TEXT NOT NULL,
    is_default BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);
```

- [ ] **Step 2: 更新 schema.sql**

在 `db/schema.sql` 末尾追加同样的两段 CREATE TABLE。

- [ ] **Step 3: 在服务器执行建表**

```bash
ssh root@14.103.220.208 "cd /opt/autovideosrt && mysql -u root -pwylf1109 auto_video < db/migrations/002_user_voices_and_prompts.sql"
```

- [ ] **Step 4: Commit**

```bash
git add db/migrations/002_user_voices_and_prompts.sql db/schema.sql
git commit -m "feat: add user_voices and user_prompts tables"
```

---

## Task 2: VoiceLibrary 改为读写数据库

**Files:**
- Modify: `pipeline/voice_library.py`
- Modify: `tests/test_voice_library.py`

- [ ] **Step 1: 写测试**

在 `tests/test_voice_library.py` 中新增数据库版本的测试：

```python
import pytest
from unittest.mock import patch, MagicMock


def _mock_query(sql, args=()):
    """Mock appcore.db.query for voice library tests."""
    return []


def _mock_execute(sql, args=()):
    """Mock appcore.db.execute for voice library tests."""
    return 1


def _mock_query_one(sql, args=()):
    return None


@patch("pipeline.voice_library.db_query", _mock_query)
@patch("pipeline.voice_library.db_execute", _mock_execute)
@patch("pipeline.voice_library.db_query_one", _mock_query_one)
def test_ensure_defaults_inserts_for_new_user():
    from pipeline.voice_library import VoiceLibrary
    lib = VoiceLibrary()
    calls = []
    with patch("pipeline.voice_library.db_execute", side_effect=lambda sql, args=(): calls.append((sql, args)) or 1):
        with patch("pipeline.voice_library.db_query", return_value=[]):
            lib.ensure_defaults(user_id=99)
    assert len(calls) == 2  # Adam + Rachel
    assert any("pNInz6obpgDQGcFmaJgB" in str(c) for c in calls)
    assert any("21m00Tcm4TlvDq8ikWAM" in str(c) for c in calls)


@patch("pipeline.voice_library.db_query", _mock_query)
@patch("pipeline.voice_library.db_execute", _mock_execute)
@patch("pipeline.voice_library.db_query_one", _mock_query_one)
def test_list_voices_filters_by_user_id():
    from pipeline.voice_library import VoiceLibrary
    lib = VoiceLibrary()
    captured = []
    def mock_q(sql, args=()):
        captured.append((sql, args))
        return [{"id": 1, "name": "Test", "gender": "male", "elevenlabs_voice_id": "abc"}]
    with patch("pipeline.voice_library.db_query", mock_q):
        result = lib.list_voices(user_id=5)
    assert len(result) == 1
    assert "user_id" in captured[0][0]
    assert captured[0][1] == (5,)
```

- [ ] **Step 2: 运行测试确认失败**

```bash
pytest tests/test_voice_library.py -v
```

Expected: FAIL — `db_query` not imported in voice_library.

- [ ] **Step 3: 重写 VoiceLibrary 为数据库版本**

替换 `pipeline/voice_library.py` 的全部内容：

```python
"""Voice library backed by user_voices database table."""
from __future__ import annotations

import json
import re
from typing import Dict, List, Optional

from appcore.db import query as db_query, execute as db_execute, query_one as db_query_one

_DEFAULT_VOICES = [
    {
        "name": "Adam",
        "gender": "male",
        "elevenlabs_voice_id": "pNInz6obpgDQGcFmaJgB",
        "description": "美式男声，自然有力，适合卖货展示类视频",
        "style_tags": ["energetic", "trustworthy", "casual"],
        "is_default": True,
    },
    {
        "name": "Rachel",
        "gender": "female",
        "elevenlabs_voice_id": "21m00Tcm4TlvDq8ikWAM",
        "description": "美式女声，亲切自然，适合美妆护肤生活类视频",
        "style_tags": ["warm", "friendly", "expressive"],
        "is_default": True,
    },
]


class VoiceLibrary:
    def ensure_defaults(self, user_id: int) -> None:
        """Insert default voices for a user if they have none."""
        existing = db_query("SELECT id FROM user_voices WHERE user_id = %s LIMIT 1", (user_id,))
        if existing:
            return
        for voice in _DEFAULT_VOICES:
            db_execute(
                """INSERT INTO user_voices
                   (user_id, name, gender, elevenlabs_voice_id, description, style_tags, is_default, source)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, 'manual')""",
                (user_id, voice["name"], voice["gender"], voice["elevenlabs_voice_id"],
                 voice["description"], json.dumps(voice["style_tags"]), voice["is_default"]),
            )

    def list_voices(self, user_id: int) -> List[Dict]:
        rows = db_query(
            "SELECT * FROM user_voices WHERE user_id = %s ORDER BY is_default DESC, created_at",
            (user_id,),
        )
        return [_row_to_voice(r) for r in rows]

    def get_voice(self, voice_id: int, user_id: int) -> Optional[Dict]:
        row = db_query_one(
            "SELECT * FROM user_voices WHERE id = %s AND user_id = %s",
            (voice_id, user_id),
        )
        return _row_to_voice(row) if row else None

    def get_voice_by_elevenlabs_id(self, elevenlabs_voice_id: str, user_id: int) -> Optional[Dict]:
        row = db_query_one(
            "SELECT * FROM user_voices WHERE elevenlabs_voice_id = %s AND user_id = %s",
            (elevenlabs_voice_id, user_id),
        )
        return _row_to_voice(row) if row else None

    def get_default_voice(self, user_id: int, gender: str = "male") -> Optional[Dict]:
        row = db_query_one(
            "SELECT * FROM user_voices WHERE user_id = %s AND gender = %s AND is_default = TRUE LIMIT 1",
            (user_id, gender),
        )
        if row:
            return _row_to_voice(row)
        row = db_query_one(
            "SELECT * FROM user_voices WHERE user_id = %s AND gender = %s LIMIT 1",
            (user_id, gender),
        )
        if row:
            return _row_to_voice(row)
        row = db_query_one(
            "SELECT * FROM user_voices WHERE user_id = %s LIMIT 1",
            (user_id,),
        )
        return _row_to_voice(row) if row else None

    def create_voice(self, user_id: int, payload: Dict) -> Dict:
        name = (payload.get("name") or "").strip()
        if not name:
            raise ValueError("name is required")
        gender = (payload.get("gender") or "").strip().lower()
        if gender not in ("male", "female"):
            raise ValueError("gender must be 'male' or 'female'")
        elevenlabs_voice_id = (payload.get("elevenlabs_voice_id") or "").strip()
        if not elevenlabs_voice_id:
            raise ValueError("elevenlabs_voice_id is required")

        row_id = db_execute(
            """INSERT INTO user_voices
               (user_id, name, gender, elevenlabs_voice_id, description, style_tags,
                preview_url, source, labels, is_default)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (user_id, name, gender, elevenlabs_voice_id,
             (payload.get("description") or "").strip(),
             json.dumps(payload.get("style_tags") or []),
             (payload.get("preview_url") or "").strip(),
             payload.get("source", "manual"),
             json.dumps(payload.get("labels") or {}),
             bool(payload.get("is_default", False))),
        )
        return self.get_voice(row_id, user_id)

    def update_voice(self, voice_id: int, user_id: int, payload: Dict) -> Dict:
        sets = []
        args = []
        for col in ("name", "gender", "description", "preview_url", "source"):
            if col in payload:
                sets.append(f"{col} = %s")
                args.append(payload[col])
        if "style_tags" in payload:
            sets.append("style_tags = %s")
            args.append(json.dumps(payload["style_tags"]))
        if "labels" in payload:
            sets.append("labels = %s")
            args.append(json.dumps(payload["labels"]))
        if "is_default" in payload:
            sets.append("is_default = %s")
            args.append(bool(payload["is_default"]))
        if not sets:
            return self.get_voice(voice_id, user_id)
        args.extend([voice_id, user_id])
        db_execute(f"UPDATE user_voices SET {', '.join(sets)} WHERE id = %s AND user_id = %s", tuple(args))
        return self.get_voice(voice_id, user_id)

    def delete_voice(self, voice_id: int, user_id: int) -> None:
        db_execute("DELETE FROM user_voices WHERE id = %s AND user_id = %s", (voice_id, user_id))

    def recommend_voice(self, user_id: int, text: str) -> Optional[Dict]:
        voices = self.list_voices(user_id)
        if not voices:
            return None
        normalized = text.lower()
        keyword_sets = {
            "beauty": ["beauty", "makeup", "skincare", "serum", "cream", "护肤", "精华", "面霜", "妆"],
            "tech": ["tech", "gadget", "drone", "tool", "电子", "科技", "无人机"],
            "warm": ["family", "mom", "baby", "soft", "亲和", "温柔", "宝宝"],
        }
        best_voice = None
        best_score = -1
        for voice in voices:
            haystack = " ".join([
                voice.get("name", ""),
                voice.get("description", ""),
                " ".join(voice.get("style_tags") or []),
            ]).lower()
            score = 0
            for tag, keywords in keyword_sets.items():
                if any(keyword in normalized for keyword in keywords):
                    if tag in haystack:
                        score += 2
                    if any(keyword in haystack for keyword in keywords):
                        score += 1
            if voice.get("gender") == "female" and any(keyword in normalized for keyword in keyword_sets["beauty"]):
                score += 3
            if voice.get("gender") == "male" and any(keyword in normalized for keyword in keyword_sets["tech"]):
                score += 2
            if score > best_score:
                best_score = score
                best_voice = voice
        return best_voice or self.get_default_voice(user_id, "male")


def _row_to_voice(row: dict) -> dict:
    """Convert a DB row to a voice dict."""
    voice = dict(row)
    if isinstance(voice.get("style_tags"), str):
        try:
            voice["style_tags"] = json.loads(voice["style_tags"])
        except (json.JSONDecodeError, TypeError):
            voice["style_tags"] = []
    if isinstance(voice.get("labels"), str):
        try:
            voice["labels"] = json.loads(voice["labels"])
        except (json.JSONDecodeError, TypeError):
            voice["labels"] = {}
    voice["is_default"] = bool(voice.get("is_default"))
    return voice


def get_voice_library() -> VoiceLibrary:
    return VoiceLibrary()
```

- [ ] **Step 4: 运行测试**

```bash
pytest tests/test_voice_library.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pipeline/voice_library.py tests/test_voice_library.py
git commit -m "feat: migrate VoiceLibrary from JSON to database"
```

---

## Task 3: 更新 voice 路由和 elevenlabs import 适配 user_id

**Files:**
- Modify: `web/routes/voice.py`
- Modify: `pipeline/elevenlabs_voices.py`

- [ ] **Step 1: 重写 web/routes/voice.py**

```python
"""
音色库蓝图

提供音色列表查询、基础 CRUD 和 ElevenLabs Voice Library 导入。
"""
from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user

from pipeline.voice_library import get_voice_library

bp = Blueprint("voice", __name__, url_prefix="/api/voices")


@bp.route("", methods=["GET"])
@login_required
def list_voices():
    lib = get_voice_library()
    lib.ensure_defaults(current_user.id)
    return jsonify({"voices": lib.list_voices(current_user.id)})


@bp.route("", methods=["POST"])
@login_required
def create_voice():
    body = request.get_json(silent=True) or {}
    try:
        voice = get_voice_library().create_voice(current_user.id, body)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"voice": voice}), 201


@bp.route("/<int:voice_id>", methods=["PUT"])
@login_required
def update_voice(voice_id):
    body = request.get_json(silent=True) or {}
    lib = get_voice_library()
    if not lib.get_voice(voice_id, current_user.id):
        return jsonify({"error": "Voice not found"}), 404
    try:
        voice = lib.update_voice(voice_id, current_user.id, body)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"voice": voice})


@bp.route("/<int:voice_id>", methods=["DELETE"])
@login_required
def delete_voice(voice_id):
    lib = get_voice_library()
    if not lib.get_voice(voice_id, current_user.id):
        return jsonify({"error": "Voice not found"}), 404
    lib.delete_voice(voice_id, current_user.id)
    return jsonify({"status": "ok"})


@bp.route("/import", methods=["POST"])
@login_required
def import_voice():
    """Import a voice from ElevenLabs Voice Library by voiceId or URL."""
    from pipeline.elevenlabs_voices import import_voice as do_import

    body = request.get_json(silent=True) or {}
    source = (body.get("source") or "").strip()
    if not source:
        return jsonify({"error": "source 参数不能为空（voiceId 或 ElevenLabs 链接）"}), 400

    overrides = {}
    for key in ("name", "gender", "description", "style_tags",
                "is_default"):
        if key in body:
            overrides[key] = body[key]

    api_key = body.get("api_key") or None
    save_to_elevenlabs = bool(body.get("save_to_elevenlabs", False))

    try:
        voice = do_import(
            source,
            user_id=current_user.id,
            api_key=api_key,
            save_to_elevenlabs=save_to_elevenlabs,
            overrides=overrides,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 502
    return jsonify({"voice": voice, "imported": True}), 201
```

- [ ] **Step 2: 更新 elevenlabs_voices.py 的 import_voice 签名**

修改 `pipeline/elevenlabs_voices.py` 中 `import_voice` 函数，加 `user_id` 参数：

```python
def import_voice(
    source: str,
    *,
    user_id: int,
    api_key: str | None = None,
    save_to_elevenlabs: bool = False,
    overrides: dict | None = None,
) -> dict:
    """Full import flow: parse source → resolve via API → store locally."""
    from pipeline.voice_library import get_voice_library

    voice_id = extract_voice_id(source)
    shared = find_shared_voice(voice_id, api_key=api_key)

    if save_to_elevenlabs:
        public_user_id = shared.get("public_owner_id") or ""
        if public_user_id:
            try:
                add_shared_voice_to_account(public_user_id, voice_id, api_key=api_key)
            except RuntimeError:
                log.warning("Could not add voice %s to ElevenLabs account, continuing anyway", voice_id)

    local_payload = _map_shared_voice_to_local(shared, overrides)

    lib = get_voice_library()

    # Idempotent: if same elevenlabs_voice_id already exists for this user, update it
    existing = lib.get_voice_by_elevenlabs_id(voice_id, user_id)

    if existing:
        voice = lib.update_voice(existing["id"], user_id, local_payload)
    else:
        voice = lib.create_voice(user_id, local_payload)

    return voice
```

- [ ] **Step 3: 运行测试**

```bash
pytest tests/test_voice_library.py tests/test_web_routes.py -v -k "voice" 2>&1 | tail -20
```

- [ ] **Step 4: Commit**

```bash
git add web/routes/voice.py pipeline/elevenlabs_voices.py
git commit -m "feat: voice routes and import now per-user with login_required"
```

---

## Task 4: 翻译提示词后端 — prompt 路由和种子数据

**Files:**
- Create: `web/routes/prompt.py`
- Modify: `web/app.py`
- Modify: `pipeline/localization.py`

- [ ] **Step 1: 导出默认提示词文本**

在 `pipeline/localization.py` 末尾追加：

```python
DEFAULT_PROMPTS = [
    {"name": "普通翻译", "prompt_text": LOCALIZED_TRANSLATION_SYSTEM_PROMPT, "is_default": True},
    {"name": "黄金3秒+CTA", "prompt_text": HOOK_CTA_TRANSLATION_SYSTEM_PROMPT, "is_default": True},
]
```

- [ ] **Step 2: 创建 web/routes/prompt.py**

```python
"""翻译提示词蓝图 — 用户级 CRUD"""
from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user

from appcore.db import query as db_query, execute as db_execute, query_one as db_query_one
from pipeline.localization import DEFAULT_PROMPTS

bp = Blueprint("prompt", __name__, url_prefix="/api/prompts")


def _ensure_defaults(user_id: int) -> None:
    existing = db_query("SELECT id FROM user_prompts WHERE user_id = %s LIMIT 1", (user_id,))
    if existing:
        return
    for p in DEFAULT_PROMPTS:
        db_execute(
            "INSERT INTO user_prompts (user_id, name, prompt_text, is_default) VALUES (%s, %s, %s, %s)",
            (user_id, p["name"], p["prompt_text"], p["is_default"]),
        )


@bp.route("", methods=["GET"])
@login_required
def list_prompts():
    _ensure_defaults(current_user.id)
    rows = db_query(
        "SELECT * FROM user_prompts WHERE user_id = %s ORDER BY is_default DESC, created_at",
        (current_user.id,),
    )
    return jsonify({"prompts": [dict(r) for r in rows]})


@bp.route("", methods=["POST"])
@login_required
def create_prompt():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    prompt_text = (body.get("prompt_text") or "").strip()
    if not name or not prompt_text:
        return jsonify({"error": "name and prompt_text are required"}), 400
    row_id = db_execute(
        "INSERT INTO user_prompts (user_id, name, prompt_text, is_default) VALUES (%s, %s, %s, FALSE)",
        (current_user.id, name, prompt_text),
    )
    row = db_query_one("SELECT * FROM user_prompts WHERE id = %s", (row_id,))
    return jsonify({"prompt": dict(row)}), 201


@bp.route("/<int:prompt_id>", methods=["PUT"])
@login_required
def update_prompt(prompt_id):
    body = request.get_json(silent=True) or {}
    row = db_query_one(
        "SELECT * FROM user_prompts WHERE id = %s AND user_id = %s",
        (prompt_id, current_user.id),
    )
    if not row:
        return jsonify({"error": "Prompt not found"}), 404
    sets = []
    args = []
    if "name" in body:
        sets.append("name = %s")
        args.append(body["name"].strip())
    if "prompt_text" in body:
        sets.append("prompt_text = %s")
        args.append(body["prompt_text"].strip())
    if not sets:
        return jsonify({"prompt": dict(row)})
    args.extend([prompt_id, current_user.id])
    db_execute(
        f"UPDATE user_prompts SET {', '.join(sets)} WHERE id = %s AND user_id = %s",
        tuple(args),
    )
    updated = db_query_one("SELECT * FROM user_prompts WHERE id = %s", (prompt_id,))
    return jsonify({"prompt": dict(updated)})


@bp.route("/<int:prompt_id>", methods=["DELETE"])
@login_required
def delete_prompt(prompt_id):
    row = db_query_one(
        "SELECT * FROM user_prompts WHERE id = %s AND user_id = %s",
        (prompt_id, current_user.id),
    )
    if not row:
        return jsonify({"error": "Prompt not found"}), 404
    if row.get("is_default"):
        return jsonify({"error": "系统预设提示词不可删除"}), 403
    db_execute("DELETE FROM user_prompts WHERE id = %s AND user_id = %s", (prompt_id, current_user.id))
    return jsonify({"status": "ok"})
```

- [ ] **Step 3: 注册蓝图到 web/app.py**

在 `web/app.py` 中已有 blueprint 导入区域追加：

```python
from web.routes.prompt import bp as prompt_bp
```

在 `create_app()` 中 `app.register_blueprint(voice_bp)` 后追加：

```python
app.register_blueprint(prompt_bp)
```

- [ ] **Step 4: 运行测试**

```bash
python -c "from web.routes.prompt import bp; print('OK')"
python -c "from web.app import create_app; app = create_app(); print([r.rule for r in app.url_map.iter_rules() if 'prompt' in r.rule])"
```

Expected: 输出包含 `/api/prompts` 相关路由。

- [ ] **Step 5: Commit**

```bash
git add pipeline/localization.py web/routes/prompt.py web/app.py
git commit -m "feat: add user_prompts CRUD API with default seed data"
```

---

## Task 5: Pipeline 单线化 — translate 接收自定义 prompt

**Files:**
- Modify: `pipeline/translate.py`
- Modify: `pipeline/localization.py`
- Modify: `appcore/runtime.py`
- Modify: `appcore/task_state.py`

- [ ] **Step 1: translate.py 支持自定义 system prompt**

修改 `pipeline/translate.py` 中 `generate_localized_translation` 函数签名，在 `variant` 参数后加 `custom_system_prompt`：

```python
def generate_localized_translation(
    source_full_text_zh: str,
    script_segments: list[dict],
    variant: str = "normal",
    openrouter_api_key: str | None = None,
    custom_system_prompt: str | None = None,
) -> dict:
```

然后修改函数体中调用 `build_localized_translation_messages` 的地方，传入 `custom_system_prompt`：

```python
    messages = build_localized_translation_messages(
        source_full_text_zh, script_segments, variant=variant,
        custom_system_prompt=custom_system_prompt,
    )
```

- [ ] **Step 2: localization.py 的 build_localized_translation_messages 支持自定义 prompt**

修改 `pipeline/localization.py` 中 `build_localized_translation_messages` 签名：

```python
def build_localized_translation_messages(
    source_full_text_zh: str,
    script_segments: list[dict],
    variant: str = "normal",
    custom_system_prompt: str | None = None,
) -> list[dict]:
```

修改函数体中选择 system prompt 的逻辑（约 line 354-358）：

```python
    if custom_system_prompt:
        system_prompt = custom_system_prompt
    elif variant == "hook_cta":
        system_prompt = HOOK_CTA_TRANSLATION_SYSTEM_PROMPT
    else:
        system_prompt = LOCALIZED_TRANSLATION_SYSTEM_PROMPT
```

- [ ] **Step 3: task_state.py — create() 只初始化 normal variant**

修改 `appcore/task_state.py` 中 `create()` 函数，将 variants 初始化改为只创建 normal：

找到大约 line 126-129 的 variants 初始化：
```python
        "variants": {
            key: _empty_variant_state(label)
            for key, label in VARIANT_LABELS.items()
        },
```

替换为：
```python
        "variants": {
            "normal": _empty_variant_state("普通版"),
        },
```

- [ ] **Step 4: runtime.py — 所有步骤去掉 variant 循环**

在 `appcore/runtime.py` 中，将以下 5 个方法中的 `for variant in VARIANT_KEYS:` 循环去掉，固定为 `variant = "normal"`。

**_step_translate（约 line 264）：**

将：
```python
        for variant in VARIANT_KEYS:
            localized_translation = generate_localized_translation(...)
            ...
```

改为：
```python
        variant = "normal"
        custom_prompt = task.get("custom_translate_prompt")
        localized_translation = generate_localized_translation(
            source_full_text_zh, script_segments, variant=variant,
            openrouter_api_key=openrouter_api_key,
            custom_system_prompt=custom_prompt,
        )
```

（后续操作取消一级缩进，不再在 for 循环内。）

**_step_tts（约 line 342）、_step_subtitle（约 line 406）、_step_compose（约 line 466）、_step_export（约 line 511）：**

对每个方法，将：
```python
        for variant in VARIANT_KEYS:
            # ... variant-specific logic ...
```

改为：
```python
        variant = "normal"
        # ... same logic, one level less indentation ...
```

同时将 variant_compare artifact 改为普通单项 artifact。

- [ ] **Step 5: 去掉 runtime.py 中对 VARIANT_KEYS 的 import**

在文件顶部 import 区域，移除：
```python
from pipeline.localization import VARIANT_KEYS
```

（保留其他 localization 的 import。）

- [ ] **Step 6: 运行测试**

```bash
pytest tests/ -v 2>&1 | tail -20
```

- [ ] **Step 7: Commit**

```bash
git add pipeline/translate.py pipeline/localization.py appcore/runtime.py appcore/task_state.py
git commit -m "feat: pipeline single-line — remove variant loops, support custom prompt"
```

---

## Task 6: retranslate 和 select-translation API

**Files:**
- Modify: `web/routes/task.py`

- [ ] **Step 1: 新增 retranslate 端点**

在 `web/routes/task.py` 中 `start` 路由之后追加：

```python
@bp.route("/<task_id>/retranslate", methods=["POST"])
@login_required
def retranslate(task_id):
    """Re-run translation with a different prompt. Stores result alongside existing translations."""
    task = store.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify({"error": "Task not found"}), 404

    step_status = (task.get("steps") or {}).get("translate")
    if step_status not in ("done", "error"):
        return jsonify({"error": "翻译步骤尚未完成，无法重新翻译"}), 400

    body = request.get_json(silent=True) or {}
    prompt_text = (body.get("prompt_text") or "").strip()
    prompt_id = body.get("prompt_id")

    if not prompt_text and prompt_id:
        from appcore.db import query_one as db_query_one
        row = db_query_one(
            "SELECT prompt_text FROM user_prompts WHERE id = %s AND user_id = %s",
            (prompt_id, current_user.id),
        )
        if row:
            prompt_text = row["prompt_text"]

    if not prompt_text:
        return jsonify({"error": "需要提供 prompt_text 或有效的 prompt_id"}), 400

    from pipeline.translate import generate_localized_translation
    from pipeline.localization import build_source_full_text_zh
    from appcore.api_keys import resolve_key

    script_segments = task.get("script_segments") or []
    source_full_text_zh = build_source_full_text_zh(script_segments)
    openrouter_api_key = resolve_key(current_user.id, "openrouter", "OPENROUTER_API_KEY")

    try:
        result = generate_localized_translation(
            source_full_text_zh, script_segments, variant="normal",
            openrouter_api_key=openrouter_api_key,
            custom_system_prompt=prompt_text,
        )
    except Exception as exc:
        return jsonify({"error": f"翻译失败: {exc}"}), 500

    # Store as additional translation attempt
    translation_history = task.get("translation_history") or []
    translation_history.append({
        "prompt_text": prompt_text,
        "prompt_id": prompt_id,
        "result": result,
    })
    if len(translation_history) > 3:
        translation_history = translation_history[-3:]

    store.update(task_id, translation_history=translation_history)

    return jsonify({
        "translation": result,
        "history_index": len(translation_history) - 1,
        "translation_history": translation_history,
    })


@bp.route("/<task_id>/select-translation", methods=["PUT"])
@login_required
def select_translation(task_id):
    """Select one of the translation attempts as the active translation."""
    task = store.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify({"error": "Task not found"}), 404

    body = request.get_json(silent=True) or {}
    index = body.get("index")
    if index is None:
        return jsonify({"error": "index is required"}), 400

    translation_history = task.get("translation_history") or []
    if not (0 <= index < len(translation_history)):
        return jsonify({"error": "无效的翻译索引"}), 400

    selected = translation_history[index]["result"]
    store.update_variant(task_id, "normal", localized_translation=selected)
    store.update(task_id, selected_translation_index=index)

    return jsonify({"status": "ok", "selected_index": index})
```

- [ ] **Step 2: 运行测试**

```bash
python -c "from web.routes.task import bp; print([r.rule for r in bp.deferred_functions])" 2>&1 || echo "OK - route registered"
pytest tests/test_web_routes.py -v -k "start" 2>&1 | tail -10
```

- [ ] **Step 3: Commit**

```bash
git add web/routes/task.py
git commit -m "feat: add retranslate and select-translation API endpoints"
```

---

## Task 7: 前端 — 音色列表式选择 UI

**Files:**
- Modify: `web/templates/_task_workbench.html`
- Modify: `web/templates/_task_workbench_styles.html`
- Modify: `web/templates/_task_workbench_scripts.html`

- [ ] **Step 1: 替换音色 HTML 区块**

在 `web/templates/_task_workbench.html` 中，将当前 `configPanel` 卡片之前，插入独立音色卡片。找到 `<div class="card hidden" id="configPanel">` 之前，加入：

```html
<div class="card hidden" id="voicePanel">
  <div class="voice-panel-header">
    <h2>音色选择</h2>
    <button type="button" class="btn btn-ghost btn-sm" id="voiceImportBtn">+ 导入音色</button>
  </div>
  <div class="voice-list" id="voiceList">
    <div class="voice-list-empty">加载中...</div>
  </div>
  <audio id="voicePreviewAudio" preload="none"></audio>
</div>
```

然后在 `configPanel` 里**删除**整个音色 config-item（从 `<div class="config-item">` 包含 `voiceSelect` 到对应的闭合 `</div>`），只保留字幕位置和确认模式两列。

同时删除之前的 `voice-import-overlay` 弹窗 HTML（保留在 voicePanel 后面重新添加更简洁的版本）。

configPanel 内只保留：
```html
<div class="card hidden" id="configPanel">
  <h2>生成配置</h2>
  <div class="config-row">
    <div class="config-item">
      <label for="subtitlePosition">字幕位置</label>
      <select id="subtitlePosition">
        <option value="bottom" selected>底部（默认）</option>
        <option value="middle">中部</option>
        <option value="top">顶部</option>
      </select>
      <div class="hint">硬字幕与 CapCut 导出都会使用这里的字幕位置。</div>
    </div>
    <div class="config-item">
      <label for="interactiveReviewToggle">确认模式</label>
      <select id="interactiveReviewToggle">
        <option value="false" selected>全自动</option>
        <option value="true">手动确认</option>
      </select>
      <div class="mode-note">手动确认时，分段和翻译会等待你点击下一步。</div>
    </div>
  </div>
  <div class="review-actions">
    <button class="btn btn-primary" id="startBtn">开始处理</button>
  </div>
</div>
```

- [ ] **Step 2: 添加音色列表样式**

在 `web/templates/_task_workbench_styles.html` 中追加：

```css
.voice-panel-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px; }
.voice-panel-header h2 { margin: 0; }
.voice-list { max-height: 300px; overflow-y: auto; display: flex; flex-direction: column; gap: 4px; }
.voice-list-empty { text-align: center; color: #9ca3af; padding: 24px; font-size: 13px; }
.voice-item {
  display: flex; align-items: center; gap: 12px; padding: 10px 14px;
  border: 1.5px solid var(--border-main); border-radius: 12px;
  cursor: pointer; transition: all .15s; background: var(--bg-body);
}
.voice-item:hover { border-color: var(--primary-color); background: var(--sidebar-hover-bg); }
.voice-item.selected { border-color: var(--primary-color); background: var(--sidebar-hover-bg); box-shadow: 0 0 8px rgba(59,130,246,0.15); }
.voice-item-info { flex: 1; min-width: 0; }
.voice-item-name { font-weight: 700; font-size: 14px; color: var(--text-main); }
.voice-item-desc { font-size: 12px; color: #9ca3af; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-top: 2px; }
.voice-item-gender { font-size: 11px; padding: 2px 8px; border-radius: 999px; font-weight: 600; flex-shrink: 0; }
.voice-item-gender.male { background: rgba(59,130,246,0.15); color: #3b82f6; }
.voice-item-gender.female { background: rgba(236,72,153,0.15); color: #ec4899; }
.voice-play-btn {
  width: 32px; height: 32px; border-radius: 50%; border: 1.5px solid var(--border-main);
  background: var(--bg-card); color: var(--text-main); font-size: 14px;
  cursor: pointer; display: flex; align-items: center; justify-content: center;
  flex-shrink: 0; transition: all .15s;
}
.voice-play-btn:hover { border-color: var(--primary-color); color: var(--primary-color); }
.voice-play-btn.playing { border-color: var(--primary-color); color: var(--primary-color); background: rgba(59,130,246,0.1); }
```

同时删除之前的 `.voice-select-row` 和 `.btn-icon` 样式（不再需要）。

- [ ] **Step 3: 替换音色 JS 逻辑**

在 `web/templates/_task_workbench_scripts.html` 中，替换 `bootVoices`、`renderVoiceOptions`、`updatePreviewBtn` 及所有 voice import modal 相关的 JS。移除旧的 `_voicesCache`、`bootVoices`、`renderVoiceOptions`、`updatePreviewBtn`、voice preview/import 事件监听（大约 lines 36-170）。

替换为：

```javascript
  let _voicesCache = [];
  let _selectedVoiceId = null;
  let _playingAudio = null;

  async function bootVoices() {
    try {
      const res = await fetch("/api/voices");
      const data = await res.json();
      _voicesCache = data.voices || [];
      renderVoiceList();
      applyTaskConfigToForm();
    } catch {
      document.getElementById("voiceList").innerHTML = '<div class="voice-list-empty">音色列表加载失败</div>';
    }
  }

  function renderVoiceList() {
    const list = document.getElementById("voiceList");
    if (!_voicesCache.length) {
      list.innerHTML = '<div class="voice-list-empty">暂无音色，点击右上角导入</div>';
      return;
    }
    list.innerHTML = _voicesCache.map(voice => `
      <div class="voice-item ${_selectedVoiceId == voice.id ? 'selected' : ''}"
           data-voice-id="${voice.id}" onclick="selectVoice(${voice.id})">
        <div class="voice-item-info">
          <div class="voice-item-name">${escapeHtml(voice.name)}</div>
          <div class="voice-item-desc">${escapeHtml(voice.description || '')}</div>
        </div>
        <span class="voice-item-gender ${voice.gender}">${voice.gender === 'female' ? '女声' : '男声'}</span>
        ${voice.preview_url ? `<button class="voice-play-btn" onclick="playVoicePreview(event, '${escapeJs(voice.preview_url)}', this)" title="试听">&#9654;</button>` : ''}
      </div>
    `).join("");
  }

  function selectVoice(voiceId) {
    _selectedVoiceId = voiceId;
    renderVoiceList();
  }

  function playVoicePreview(e, url, btn) {
    e.stopPropagation();
    const audio = document.getElementById("voicePreviewAudio");
    // Stop if already playing this one
    if (_playingAudio === url && !audio.paused) {
      audio.pause();
      btn.classList.remove("playing");
      btn.innerHTML = "&#9654;";
      _playingAudio = null;
      return;
    }
    // Stop any previous
    document.querySelectorAll(".voice-play-btn.playing").forEach(b => {
      b.classList.remove("playing");
      b.innerHTML = "&#9654;";
    });
    audio.src = url;
    audio.play().catch(() => {});
    btn.classList.add("playing");
    btn.innerHTML = "&#9646;&#9646;";
    _playingAudio = url;
    audio.onended = () => {
      btn.classList.remove("playing");
      btn.innerHTML = "&#9654;";
      _playingAudio = null;
    };
  }

  // Voice import modal (reuse existing overlay)
  document.getElementById("voiceImportBtn").addEventListener("click", () => {
    document.getElementById("voiceImportOverlay").classList.remove("hidden");
    document.getElementById("importVoiceSource").value = "";
    document.getElementById("importVoiceName").value = "";
    document.getElementById("importVoiceGender").value = "";
    document.getElementById("importVoiceError").classList.add("hidden");
    document.getElementById("importPreviewSection").classList.add("hidden");
    document.getElementById("importVoiceSource").focus();
  });

  document.getElementById("importVoiceCancel").addEventListener("click", () => {
    document.getElementById("voiceImportOverlay").classList.add("hidden");
  });

  document.getElementById("voiceImportOverlay").addEventListener("click", (e) => {
    if (e.target === e.currentTarget) document.getElementById("voiceImportOverlay").classList.add("hidden");
  });

  document.getElementById("importVoiceSubmit").addEventListener("click", async () => {
    const source = document.getElementById("importVoiceSource").value.trim();
    if (!source) { showImportError("请输入 voiceId 或 ElevenLabs 链接"); return; }
    const errEl = document.getElementById("importVoiceError");
    errEl.classList.add("hidden");
    const submitBtn = document.getElementById("importVoiceSubmit");
    submitBtn.disabled = true;
    submitBtn.textContent = "导入中...";
    const body = { source };
    const name = document.getElementById("importVoiceName").value.trim();
    const gender = document.getElementById("importVoiceGender").value;
    if (name) body.name = name;
    if (gender) body.gender = gender;
    try {
      const res = await fetch("/api/voices/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) { showImportError(data.error || "导入失败"); return; }
      _voicesCache.push(data.voice);
      _selectedVoiceId = data.voice.id;
      renderVoiceList();
      document.getElementById("voiceImportOverlay").classList.add("hidden");
    } catch (err) {
      showImportError(err.message || "网络错误");
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = "导入";
    }
  });

  function showImportError(msg) {
    const el = document.getElementById("importVoiceError");
    el.textContent = msg;
    el.classList.remove("hidden");
  }
```

- [ ] **Step 4: 更新 showTaskWorkbench 和 applyTaskConfigToForm**

在 `showTaskWorkbench` 中加入 `voicePanel` 的显示：

```javascript
  function showTaskWorkbench() {
    document.getElementById("voicePanel").classList.remove("hidden");
    document.getElementById("configPanel").classList.remove("hidden");
    document.getElementById("pipelineCard").classList.remove("hidden");
    const hero = document.getElementById("taskHero");
    if (hero) hero.classList.remove("hidden");
  }
```

在 `applyTaskConfigToForm` 中将 voice_id 的设置改为列表选中：

```javascript
  function applyTaskConfigToForm() {
    if (!currentTask) return;
    _selectedVoiceId = currentTask.voice_id || null;
    renderVoiceList();
    document.getElementById("subtitlePosition").value = currentTask.subtitle_position || "bottom";
    document.getElementById("interactiveReviewToggle").value = String(Boolean(currentTask.interactive_review));
  }
```

在 `startBtn` click handler 中把 `voice_id` 改为从 `_selectedVoiceId` 读取：

```javascript
    body: JSON.stringify({
      voice_id: _selectedVoiceId || "auto",
      subtitle_position: document.getElementById("subtitlePosition").value,
      interactive_review: document.getElementById("interactiveReviewToggle").value === "true",
    }),
```

- [ ] **Step 5: Commit**

```bash
git add web/templates/_task_workbench.html web/templates/_task_workbench_scripts.html web/templates/_task_workbench_styles.html
git commit -m "feat: voice selection panel with list UI, inline preview, import"
```

---

## Task 8: 前端 — 翻译提示词选择/编辑/重新翻译 UI

**Files:**
- Modify: `web/templates/_task_workbench.html`
- Modify: `web/templates/_task_workbench_scripts.html`
- Modify: `web/templates/_task_workbench_styles.html`

- [ ] **Step 1: 在翻译步骤的 step-preview 区域加入提示词 UI**

在 `web/templates/_task_workbench.html` 中，找到 `id="step-translate"` 的步骤块，在其 `<div class="step-preview" id="preview-translate">` 之前，插入提示词配置区：

```html
    <div class="translate-prompt-panel hidden" id="translatePromptPanel">
      <div class="prompt-selector">
        <label>翻译提示词</label>
        <div class="prompt-tabs" id="promptTabs"></div>
      </div>
      <textarea class="prompt-editor" id="promptEditor" rows="8" placeholder="提示词内容"></textarea>
      <div class="prompt-actions">
        <button class="btn btn-ghost btn-sm" id="savePromptBtn">保存提示词</button>
        <button class="btn btn-primary btn-sm" id="retranslateBtn">重新翻译</button>
      </div>
      <div class="translation-history hidden" id="translationHistory">
        <label>翻译结果对比</label>
        <div class="translation-results" id="translationResults"></div>
      </div>
    </div>
```

- [ ] **Step 2: 添加翻译提示词样式**

在 `web/templates/_task_workbench_styles.html` 中追加：

```css
.translate-prompt-panel { padding: 14px 0; }
.prompt-selector label, .translation-history label {
  display: block; margin-bottom: 8px; color: #6b7280; font-size: 12px;
  letter-spacing: 0.06em; text-transform: uppercase; font-weight: 600;
}
.prompt-tabs { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }
.prompt-tab {
  padding: 6px 14px; border-radius: 999px; font-size: 13px; font-weight: 600;
  border: 1.5px solid var(--border-main); background: var(--bg-body); color: var(--text-main);
  cursor: pointer; transition: all .15s; font-family: inherit;
}
.prompt-tab:hover { border-color: var(--primary-color); }
.prompt-tab.active { border-color: var(--primary-color); background: rgba(59,130,246,0.1); color: var(--primary-color); }
.prompt-editor {
  width: 100%; background: var(--bg-body); border: 1.5px solid var(--border-main);
  border-radius: 10px; color: var(--text-main); padding: 10px 12px; font-size: 13px;
  font-family: inherit; outline: none; resize: vertical; min-height: 120px;
  box-sizing: border-box; transition: border-color 0.2s;
}
.prompt-editor:focus { border-color: var(--primary-color); box-shadow: 0 0 10px rgba(59,130,246,0.2); }
.prompt-actions { display: flex; gap: 10px; margin-top: 10px; }
.translation-history { margin-top: 16px; }
.translation-results { display: flex; flex-direction: column; gap: 10px; }
.translation-result-card {
  background: var(--bg-body); border: 1.5px solid var(--border-main); border-radius: 12px; padding: 12px;
}
.translation-result-card.selected-result { border-color: #4ade80; background: rgba(22,163,74,0.05); }
.translation-result-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.translation-result-label { font-size: 12px; color: #6b7280; font-weight: 600; }
.translation-result-text { font-size: 13px; color: var(--text-main); line-height: 1.6; white-space: pre-wrap; }
```

- [ ] **Step 3: 添加翻译提示词 JS 逻辑**

在 `web/templates/_task_workbench_scripts.html` 末尾（`</script>` 之前）追加：

```javascript
  // --- Translation Prompt System ---
  let _promptsCache = [];
  let _selectedPromptId = null;
  let _translationHistory = [];

  async function bootPrompts() {
    try {
      const res = await fetch("/api/prompts");
      const data = await res.json();
      _promptsCache = data.prompts || [];
      if (_promptsCache.length && !_selectedPromptId) {
        _selectedPromptId = _promptsCache[0].id;
      }
      renderPromptTabs();
    } catch {}
  }

  function renderPromptTabs() {
    const tabs = document.getElementById("promptTabs");
    tabs.innerHTML = _promptsCache.map(p => `
      <button class="prompt-tab ${p.id === _selectedPromptId ? 'active' : ''}"
              onclick="selectPrompt(${p.id})">${escapeHtml(p.name)}</button>
    `).join("");
    const selected = _promptsCache.find(p => p.id === _selectedPromptId);
    if (selected) {
      document.getElementById("promptEditor").value = selected.prompt_text;
    }
  }

  function selectPrompt(promptId) {
    _selectedPromptId = promptId;
    renderPromptTabs();
  }

  function showTranslatePromptPanel() {
    document.getElementById("translatePromptPanel").classList.remove("hidden");
    bootPrompts();
  }

  document.getElementById("savePromptBtn").addEventListener("click", async () => {
    if (!_selectedPromptId) return;
    const text = document.getElementById("promptEditor").value.trim();
    if (!text) return;
    await fetch(`/api/prompts/${_selectedPromptId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt_text: text }),
    });
    const p = _promptsCache.find(p => p.id === _selectedPromptId);
    if (p) p.prompt_text = text;
  });

  document.getElementById("retranslateBtn").addEventListener("click", async () => {
    if (!taskId) return;
    const btn = document.getElementById("retranslateBtn");
    btn.disabled = true;
    btn.textContent = "翻译中...";
    const promptText = document.getElementById("promptEditor").value.trim();
    try {
      const res = await fetch(`/api/tasks/${taskId}/retranslate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt_id: _selectedPromptId, prompt_text: promptText }),
      });
      const data = await res.json();
      if (!res.ok) { showError(data.error || "翻译失败"); return; }
      _translationHistory = data.translation_history || [];
      renderTranslationHistory();
    } catch (err) {
      showError(err.message || "翻译失败");
    } finally {
      btn.disabled = false;
      btn.textContent = "重新翻译";
    }
  });

  function renderTranslationHistory() {
    const container = document.getElementById("translationResults");
    const panel = document.getElementById("translationHistory");
    if (!_translationHistory.length) {
      panel.classList.add("hidden");
      return;
    }
    panel.classList.remove("hidden");
    const selectedIdx = currentTask?.selected_translation_index;
    container.innerHTML = _translationHistory.map((item, idx) => {
      const sentences = (item.result?.sentences || []).map(s => s.text).join(" ");
      const isSelected = selectedIdx === idx;
      return `
        <div class="translation-result-card ${isSelected ? 'selected-result' : ''}">
          <div class="translation-result-header">
            <span class="translation-result-label">翻译 #${idx + 1}</span>
            <button class="btn btn-ghost btn-sm" onclick="selectTranslation(${idx})">${isSelected ? '✓ 已选用' : '选用这个'}</button>
          </div>
          <div class="translation-result-text">${escapeHtml(sentences)}</div>
        </div>
      `;
    }).join("");
  }

  async function selectTranslation(index) {
    if (!taskId) return;
    await fetch(`/api/tasks/${taskId}/select-translation`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ index }),
    });
    if (currentTask) currentTask.selected_translation_index = index;
    renderTranslationHistory();
    scheduleRefreshTaskState(0);
  }

  // Show prompt panel when translate step becomes active or done
  const _origRenderStepMessages = renderStepMessages;
  renderStepMessages = function() {
    _origRenderStepMessages();
    const translateStatus = currentTask?.steps?.translate;
    if (translateStatus === "running" || translateStatus === "done" || translateStatus === "waiting") {
      showTranslatePromptPanel();
    }
    // Restore translation history from task state
    if (currentTask?.translation_history) {
      _translationHistory = currentTask.translation_history;
      renderTranslationHistory();
    }
  };

  bootPrompts();
```

- [ ] **Step 4: Commit**

```bash
git add web/templates/_task_workbench.html web/templates/_task_workbench_scripts.html web/templates/_task_workbench_styles.html
git commit -m "feat: translation prompt selector with editable prompts and retranslate UI"
```

---

## Task 9: 集成测试和部署

**Files:**
- No new files

- [ ] **Step 1: 运行全部测试**

```bash
pytest tests/ -v 2>&1 | tail -30
```

修复任何因改动导致的失败。

- [ ] **Step 2: 本地启动验证**

```bash
python main.py
```

打开浏览器访问，检查：
- 音色列表正常加载，可试听，可导入
- 生成配置只显示字幕位置和确认模式
- 翻译步骤显示提示词选择器
- Pipeline 不再产出两个变体

- [ ] **Step 3: 提交所有改动、推送、创建 PR**

```bash
git push -u origin HEAD
gh pr create --base master --title "feat: 音色/提示词/Pipeline 重构" --body "..."
gh pr merge <PR_NUMBER> --merge --admin
```

- [ ] **Step 4: 部署到服务器**

```bash
ssh root@14.103.220.208 "cd /opt/autovideosrt && git pull origin master && mysql -u root -pwylf1109 auto_video < db/migrations/002_user_voices_and_prompts.sql && systemctl restart autovideosrt"
```

- [ ] **Step 5: Playwright 验证**

```python
# 验证音色面板、提示词面板、pipeline 单线输出
```

- [ ] **Step 6: Commit**

```bash
git commit -m "chore: final integration fixes"
```
