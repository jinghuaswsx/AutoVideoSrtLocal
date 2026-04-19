# LLM 调用统一管理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> ⚠️ **执行前必读文末《2026-04-19 漂移修正记录》**：从 origin/master 同步后发现主线已经引入了 Vertex AI provider 族（commit 57b503a），对本 plan 有 9 处具体覆盖（provider 从 3 → 4、`pipeline/translate.py` 改造方式等）。**勘误表中的内容覆盖前文 Task 的对应片段。**

**Goal:** 把散落在各模块的 LLM 调用统一到一个 API 设置中心，解耦「服务商接入」「模块模型绑定」「Prompt」三层，重构 `/settings` 页。

**Architecture:**
三层解耦：(1) **Provider 层**沿用 `api_keys` 表（用户级凭证/base_url）；(2) 新增全局级 **UseCase Binding 层**（`llm_use_case_bindings` 表）把 `module.function → provider + model` 绑定存 DB；(3) Prompt 层沿用现有 `llm_prompt_configs` 表。运行时统一入口 `appcore/llm_client.py:invoke(use_case, user_id=...)` 内部按 provider_code 分发到 `openrouter / doubao / gemini` 三个 adapter。所有现有调用点通过"先查 bindings，查不到 fallback 到原逻辑"平滑过渡，零行为变化。UI 分 3 Tab：**服务商接入 / 模块模型分配 / 通用设置**，用 ocean-blue 设计系统重写。

**Tech Stack:** Python 3.10+ / Flask / MySQL / pytest / Jinja2 / 现有 `openai` + `google.genai` SDK

---

## 实施前置条件（启动前必读）

本 plan 于 2026-04-19 编写，**实施与编写存在时间差**。启动前必须满足：

### 1. 代码新鲜度
- 启动前，**主仓（`master` 分支）必须已把其他模块的在途修改全部提交**。本重构横跨 `pipeline/ + appcore/ + web/routes/ + web/templates/`，影响面很大，任何未提交的改动都会在合并时产生复杂冲突。
- 执行者开始前必须 `git fetch origin && git log origin/master..HEAD` 确认和主分支一致，或明确知道自己将基于哪个 commit 启动。

### 2. Worktree 隔离
本 plan 必须在**独立 git worktree** 中执行，不要污染主工作区：

```bash
# 从主仓根目录执行
git fetch origin
git worktree add ../AutoVideoSrtCodex-llm-unify -b feat/llm-call-unification origin/master
cd ../AutoVideoSrtCodex-llm-unify
```

完成并合并回 master 后再清理：
```bash
git worktree remove ../AutoVideoSrtCodex-llm-unify
git branch -d feat/llm-call-unification
```

### 3. 现状漂移核查（每次启动前必跑）
本 plan 里引用了大量具体文件 + 行号 + 函数签名。时间差内它们可能变化。**启动前逐项核对**：

| 引用 | 核查命令 | 预期 |
|------|---------|------|
| `pipeline/translate.py:resolve_provider_config()` L36-65 | `rg -n "def resolve_provider_config" pipeline/translate.py` | 函数仍存在 |
| `appcore/gemini.py:resolve_config()` L59-83 | `rg -n "def resolve_config" appcore/gemini.py` | 函数仍存在 |
| `SCORE_MODEL` 硬编码 | `rg -n "SCORE_MODEL" pipeline/video_score.py` | 常量仍在 L13 附近 |
| `DEFAULT_MODEL` in shot_decompose | `rg -n "DEFAULT_MODEL" pipeline/shot_decompose.py` | 仍在 L50 附近 |
| `_FLASH_MODEL` in link_check | `rg -n "_FLASH_MODEL" appcore/link_check_gemini.py` | 仍在 L7 附近 |
| api_keys 表 schema | `mysql -e "DESCRIBE api_keys"` | 列仍是 `user_id/service/key_value/extra_config/updated_at` |
| `llm_prompt_configs` 表存在 | `mysql -e "DESCRIBE llm_prompt_configs"` | 表仍存在（多语种翻译 prompt） |
| `system_settings` DAO | `rg -n "get_setting\|set_setting" appcore/settings.py` | 函数仍存在 |
| `USE_CASES` 的 14 个 use_case 代码映射的模块是否还存在 | 逐个 `ls` 对应文件 | 所有 14 个 `appcore/*` `pipeline/*` 文件仍在 |

如果任一条核查失败，**停止执行 plan**，先更新 plan 对应 Task 再启动。常见的需要重写的场景：
- `resolve_provider_config` 被重构改签名 → Task 6 重写
- 某个硬编码模型已经被别人迁到别处 → 对应 Task 跳过或改为指向新位置
- `api_keys` 表加了 `user_id=0` 的全局默认行之类 → Task 15 的 DAO 要考虑兼容

### 4. 测试基线快照
启动前先跑一遍全量测试并记录基线，便于每步后比对：

```bash
pytest tests/ -q 2>&1 | tee /tmp/llm-unify-baseline.txt
tail -3 /tmp/llm-unify-baseline.txt  # 记录通过数
```

每完成一个 Task 的 commit 前重跑一次，确保通过数 **≥** 基线。

### 5. 数据库快照
`llm_use_case_bindings` 是新表，无数据风险；但 Task 16 重写 `/settings` 路由时如果线上已有用户保存的 `api_keys` 记录，**不能动这些行**。在本地执行前：

```bash
mysqldump -u root -p auto_video api_keys system_settings llm_prompt_configs \
  > /tmp/llm-unify-rollback.sql
```

失败时 `mysql auto_video < /tmp/llm-unify-rollback.sql` 回滚。

### 6. 跨模块影响范围提示
本重构会摸到至少这些模块的执行路径（跑 smoke test 时每个至少走一遍）：
- 视频翻译主流程（英 / 德 / 法 / 多语种）
- 文案创作（copywriting）
- 文案翻译（copywriting_translate）
- 视频评测 + 视频评分
- 分镜拆解（translate_lab）
- 图片翻译（image_translate）
- 链接检查（link_check）
- 纯文本翻译（text_translate）

任何一个在实施后跑不通，先回滚该 Task，再排查。

---

## 核心设计决策

1. **bindings 表是"全局"的（无 user_id）**：管理员在 `/settings` 第二 Tab 设置，所有用户共享。依据：
   - 与现有 `llm_prompt_configs`（管理员后台，全局）架构一致
   - 用户主诉求是"统一换模型"，不是"每个用户用不同模型"
   - 未来若需要用户级覆盖，加 `user_id` 列即可，不破坏接口
2. **API Key 仍然是用户级**（`api_keys` 表，结构不改）
3. **老接口保留**：`pipeline/translate.py:resolve_provider_config()` 和 `appcore/gemini.py:resolve_config()` 继续存在，只在内部前置"查 bindings 表"。新代码直接调 `llm_client.invoke()`
4. **ElevenLabs / 火山 ASR 纳入 Provider Tab**：ASR 仅管理员可改（走 `system_settings` 或额外 flag）；ElevenLabs 走用户级 `api_keys`
5. **Gemini AIStudio vs Vertex backend 本期不拆**：仍用全局 `GEMINI_BACKEND` env。标注二期
6. **迁移安全原则**：每一个 Task 结束后必须 `pytest tests/ -q` 全绿

---

## 文件结构

### 新增文件

```
appcore/
  llm_use_cases.py              # UseCase 注册表（枚举 + 默认 provider/model/服务名）
  llm_bindings.py               # llm_use_case_bindings 表的 DAO + resolver（含 seed 默认）
  llm_client.py                 # 统一调用入口 invoke(use_case, ...)
  llm_providers/
    __init__.py                 # 导出 PROVIDER_ADAPTERS
    base.py                     # Adapter 抽象基类
    openrouter_adapter.py       # OpenRouter / 豆包走 OpenAI-compatible
    gemini_adapter.py           # Gemini 走 google.genai

db/migrations/
  2026_04_19_llm_use_case_bindings.sql
  2026_04_19_api_keys_add_volc_elevenlabs.sql   # 确保 service 字段允许 volc/elevenlabs

tests/
  test_llm_use_cases_registry.py
  test_llm_bindings_dao.py
  test_llm_client_invoke.py
  test_llm_providers_openrouter.py
  test_llm_providers_gemini.py
  test_settings_routes_new.py
```

### 修改文件

```
pipeline/translate.py              # resolve_provider_config() 内部前置查 bindings
appcore/gemini.py                  # resolve_config() 支持 use_case 参数
pipeline/video_score.py            # 走 llm_client.invoke("video_score.run", ...)
pipeline/shot_decompose.py         # 走 llm_client.invoke("shot_decompose.run", ...)
pipeline/video_review.py           # 走 llm_client.invoke("video_review.analyze", ...)
pipeline/copywriting.py            # 走 llm_client.invoke("copywriting.generate|rewrite", ...)
pipeline/text_translate.py         # 走 llm_client.invoke("text_translate.generate", ...)
appcore/link_check_gemini.py       # 走 llm_client.invoke("link_check.analyze", ...)
appcore/gemini_image.py            # 走 llm_client（image_translate.detect / image_translate.generate）
web/routes/settings.py             # 三 Tab 表单处理
web/templates/settings.html        # ocean-blue 重写
```

### 不变文件（但要验证行为）

```
appcore/api_keys.py                # 结构不改
db/schema.sql 已有表               # api_keys / system_settings / llm_prompt_configs 保持
config.py                          # 所有 env 保留
pipeline/localization*.py          # prompt 仍从代码读
appcore/runtime*.py                # user_id 传递不变
```

---

# Phase 1 · 基础设施（不改业务）

### Task 1: 建 `llm_use_case_bindings` 表

**Files:**
- Create: `db/migrations/2026_04_19_llm_use_case_bindings.sql`
- Create: `db/migrations/2026_04_19_api_keys_add_volc_elevenlabs.sql`
- Test: 手动在本地 MySQL `auto_video` 库跑迁移，`DESCRIBE llm_use_case_bindings;` 验证

- [ ] **Step 1.1: 写迁移 SQL**

创建 `db/migrations/2026_04_19_llm_use_case_bindings.sql`：

```sql
CREATE TABLE IF NOT EXISTS llm_use_case_bindings (
    use_case_code VARCHAR(64) NOT NULL PRIMARY KEY,
    provider_code VARCHAR(32) NOT NULL,
    model_id      VARCHAR(128) NOT NULL,
    extra_config  JSON,
    enabled       TINYINT(1) NOT NULL DEFAULT 1,
    updated_by    INT NULL,
    updated_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_provider_model (provider_code, model_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

`api_keys` 表的 `service` 字段已是 `VARCHAR(32)`（schema.sql:16），新加 `volc` / `elevenlabs` service 不需要 DDL。若迁移脚本目录里看到 schema 硬限制再加一个空 migration 占位。创建 `2026_04_19_api_keys_add_volc_elevenlabs.sql`：

```sql
-- no-op：api_keys.service 已是 VARCHAR(32)，支持任意 service 字符串。
-- 本文件仅作为本次变更的书面记录。
SELECT 'api_keys.service accepts arbitrary values; no schema change required.' AS note;
```

- [ ] **Step 1.2: 跑迁移验证**

```bash
python db/migrate.py
mysql -u root -p auto_video -e "DESCRIBE llm_use_case_bindings;"
```

预期输出包含 `use_case_code / provider_code / model_id / extra_config / enabled / updated_by / updated_at` 七列。

- [ ] **Step 1.3: Commit**

```bash
git add db/migrations/2026_04_19_llm_use_case_bindings.sql db/migrations/2026_04_19_api_keys_add_volc_elevenlabs.sql
git commit -m "feat(db): add llm_use_case_bindings table for module→model mapping"
```

---

### Task 2: UseCase 注册表 `appcore/llm_use_cases.py`

**Files:**
- Create: `appcore/llm_use_cases.py`
- Create: `tests/test_llm_use_cases_registry.py`

- [ ] **Step 2.1: 写测试 `tests/test_llm_use_cases_registry.py`**

```python
from appcore.llm_use_cases import USE_CASES, get_use_case, list_by_module


def test_all_use_cases_have_required_fields():
    for code, uc in USE_CASES.items():
        assert uc["code"] == code, f"{code} mismatch self-key"
        assert uc["module"], f"{code} missing module"
        assert uc["label"], f"{code} missing label"
        assert uc["default_provider"] in {"openrouter", "doubao", "gemini"}
        assert uc["default_model"], f"{code} missing default_model"


def test_get_use_case_unknown_raises():
    import pytest
    with pytest.raises(KeyError):
        get_use_case("nonexistent.case")


def test_list_by_module_groups_correctly():
    groups = list_by_module()
    assert "video_translate" in groups
    assert "copywriting" in groups
    assert "video_analysis" in groups
    for module, items in groups.items():
        assert items, f"module {module} has no use cases"
```

- [ ] **Step 2.2: 跑测试确认失败**

```bash
pytest tests/test_llm_use_cases_registry.py -v
```
预期: `ModuleNotFoundError: appcore.llm_use_cases`

- [ ] **Step 2.3: 实现 `appcore/llm_use_cases.py`**

```python
"""LLM UseCase 注册表。

每个业务功能（模块.功能）对应一个 use_case_code，绑定默认 provider + model
以及写 usage_logs 时的 service 名称。UI 和 resolver 都读这里。
"""
from __future__ import annotations

from typing import TypedDict


class UseCase(TypedDict):
    code: str                  # "video_translate.localize"
    module: str                # "video_translate"
    label: str                 # "本土化改写" 中文短名
    description: str           # 一句话描述
    default_provider: str      # "openrouter" | "doubao" | "gemini"
    default_model: str         # "anthropic/claude-sonnet-4.6" 等
    usage_log_service: str     # 写 usage_logs 时的 service 字段


def _uc(code, module, label, desc, provider, model, service) -> UseCase:
    return {
        "code": code, "module": module, "label": label,
        "description": desc, "default_provider": provider,
        "default_model": model, "usage_log_service": service,
    }


USE_CASES: dict[str, UseCase] = {
    # ── 视频翻译 ──
    "video_translate.localize": _uc(
        "video_translate.localize", "video_translate", "本土化改写",
        "视频翻译主流程中把中文转成目标语言本土化文本",
        "openrouter", "google/gemini-3.1-flash-lite-preview", "openrouter",
    ),
    "video_translate.tts_script": _uc(
        "video_translate.tts_script", "video_translate", "TTS 脚本生成",
        "根据本土化文本切分成适合配音的 TTS 脚本段落",
        "openrouter", "google/gemini-3.1-flash-lite-preview", "openrouter",
    ),
    "video_translate.rewrite": _uc(
        "video_translate.rewrite", "video_translate", "字数收敛重写",
        "TTS 时长不达标时回卷到文案重写的内循环",
        "openrouter", "google/gemini-3.1-flash-lite-preview", "openrouter",
    ),
    # ── 文案创作 ──
    "copywriting.generate": _uc(
        "copywriting.generate", "copywriting", "文案生成",
        "根据关键帧+商品信息生成带货文案",
        "openrouter", "anthropic/claude-sonnet-4.6", "openrouter",
    ),
    "copywriting.rewrite": _uc(
        "copywriting.rewrite", "copywriting", "文案段重写",
        "单段文案重写",
        "openrouter", "anthropic/claude-sonnet-4.6", "openrouter",
    ),
    # ── 视频分析 ──
    "video_score.run": _uc(
        "video_score.run", "video_analysis", "视频评分",
        "对硬字幕成片按美国带货要素打分",
        "gemini", "gemini-3.1-pro-preview", "gemini_video_analysis",
    ),
    "video_review.analyze": _uc(
        "video_review.analyze", "video_analysis", "视频评测",
        "按用户自定义 prompt 分析视频",
        "gemini", "gemini-3.1-pro-preview", "gemini_video_analysis",
    ),
    "shot_decompose.run": _uc(
        "shot_decompose.run", "video_analysis", "分镜拆解",
        "Gemini 识别镜头切换并描述画面",
        "gemini", "gemini-3.1-pro-preview", "gemini_video_analysis",
    ),
    # ── 图片 & 链接 ──
    "image_translate.detect": _uc(
        "image_translate.detect", "image", "图片文字检测",
        "判定商品图是否已本地化为目标语种",
        "gemini", "gemini-2.5-flash", "gemini",
    ),
    "image_translate.generate": _uc(
        "image_translate.generate", "image", "图片本地化重绘",
        "用图像模型重绘目标语种的商品图",
        "gemini", "gemini-3-pro-image-preview", "gemini",
    ),
    "link_check.analyze": _uc(
        "link_check.analyze", "image", "链接商品图审查",
        "核查外链商品图文字是否匹配目标语种",
        "gemini", "gemini-2.5-flash", "gemini",
    ),
    # ── 文本翻译 ──
    "text_translate.generate": _uc(
        "text_translate.generate", "text_translate", "纯文本翻译",
        "把任意文本翻译到目标语言",
        "openrouter", "google/gemini-3.1-flash-lite-preview", "openrouter",
    ),
}


def get_use_case(code: str) -> UseCase:
    if code not in USE_CASES:
        raise KeyError(f"unknown use_case: {code}")
    return USE_CASES[code]


def list_by_module() -> dict[str, list[UseCase]]:
    groups: dict[str, list[UseCase]] = {}
    for uc in USE_CASES.values():
        groups.setdefault(uc["module"], []).append(uc)
    return groups


MODULE_LABELS: dict[str, str] = {
    "video_translate": "视频翻译",
    "copywriting": "文案创作",
    "video_analysis": "视频分析",
    "image": "图片 & 链接",
    "text_translate": "文本翻译",
}
```

- [ ] **Step 2.4: 跑测试确认通过**

```bash
pytest tests/test_llm_use_cases_registry.py -v
```
预期: 3 passed

- [ ] **Step 2.5: Commit**

```bash
git add appcore/llm_use_cases.py tests/test_llm_use_cases_registry.py
git commit -m "feat(llm): add UseCase registry with 12 default bindings"
```

---

### Task 3: Bindings DAO `appcore/llm_bindings.py`

**Files:**
- Create: `appcore/llm_bindings.py`
- Create: `tests/test_llm_bindings_dao.py`

- [ ] **Step 3.1: 写测试 `tests/test_llm_bindings_dao.py`**

```python
from unittest.mock import patch

from appcore import llm_bindings


def test_resolve_uses_default_when_db_empty():
    with patch("appcore.llm_bindings.query_one", return_value=None), \
         patch("appcore.llm_bindings.execute") as m_exec:
        result = llm_bindings.resolve("video_score.run")
    assert result["provider"] == "gemini"
    assert result["model"] == "gemini-3.1-pro-preview"
    # 默认值被 seed 回 DB
    assert m_exec.called
    assert m_exec.call_args[0][0].startswith("INSERT INTO llm_use_case_bindings")


def test_resolve_returns_db_value_when_present():
    row = {
        "provider_code": "doubao",
        "model_id": "doubao-custom-model",
        "extra_config": None,
        "enabled": 1,
    }
    with patch("appcore.llm_bindings.query_one", return_value=row):
        result = llm_bindings.resolve("copywriting.generate")
    assert result["provider"] == "doubao"
    assert result["model"] == "doubao-custom-model"


def test_resolve_disabled_falls_back_to_default():
    row = {
        "provider_code": "doubao",
        "model_id": "custom",
        "extra_config": None,
        "enabled": 0,
    }
    with patch("appcore.llm_bindings.query_one", return_value=row):
        result = llm_bindings.resolve("video_score.run")
    # enabled=0 视为无绑定，走默认
    assert result["provider"] == "gemini"
    assert result["model"] == "gemini-3.1-pro-preview"


def test_upsert_calls_insert_on_duplicate_update():
    with patch("appcore.llm_bindings.execute") as m_exec:
        llm_bindings.upsert(
            "video_score.run",
            provider="openrouter", model="openai/gpt-4o",
            updated_by=1,
        )
    assert m_exec.called
    sql = m_exec.call_args[0][0]
    assert "ON DUPLICATE KEY UPDATE" in sql


def test_upsert_rejects_unknown_use_case():
    import pytest
    with pytest.raises(KeyError):
        llm_bindings.upsert("nonexistent.case", provider="openrouter",
                            model="x", updated_by=1)


def test_list_all_returns_rows_with_defaults_merged():
    """list_all 应该把 DB 记录和 USE_CASES 默认合并，UI 能拿到所有 use_case。"""
    db_rows = [
        {"use_case_code": "video_score.run",
         "provider_code": "openrouter",
         "model_id": "openai/gpt-4o-mini",
         "extra_config": None, "enabled": 1, "updated_at": None,
         "updated_by": None},
    ]
    with patch("appcore.llm_bindings.query", return_value=db_rows):
        result = llm_bindings.list_all()
    codes = {r["code"] for r in result}
    # 所有默认 use_case 都出现
    from appcore.llm_use_cases import USE_CASES
    assert codes == set(USE_CASES.keys())
    # 被 DB 覆盖的条目 is_custom=True
    overridden = next(r for r in result if r["code"] == "video_score.run")
    assert overridden["provider"] == "openrouter"
    assert overridden["is_custom"] is True
```

- [ ] **Step 3.2: 跑测试确认失败**

```bash
pytest tests/test_llm_bindings_dao.py -v
```
预期: `ModuleNotFoundError: appcore.llm_bindings`

- [ ] **Step 3.3: 实现 `appcore/llm_bindings.py`**

```python
"""llm_use_case_bindings 表的 DAO + resolver。

resolver 会自动 fallback 到 USE_CASES 里的默认值；写入 DB 时会 seed。
bindings 表是全局级（无 user_id），由管理员在 /settings 第二 Tab 编辑。
"""
from __future__ import annotations

import json
from typing import Any

from appcore.db import execute, query, query_one
from appcore.llm_use_cases import USE_CASES, get_use_case


def _parse_extra(raw: Any) -> dict:
    if raw is None:
        return {}
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return dict(raw) if isinstance(raw, dict) else {}


def resolve(use_case_code: str) -> dict:
    """返回 {provider, model, extra, source}；DB 命中且 enabled=1 时走 DB，否则走默认。

    source ∈ {"db", "default"}。
    """
    default = get_use_case(use_case_code)
    row = query_one(
        "SELECT provider_code, model_id, extra_config, enabled "
        "FROM llm_use_case_bindings WHERE use_case_code = %s",
        (use_case_code,),
    )
    if row and int(row.get("enabled") or 0) == 1:
        return {
            "provider": row["provider_code"],
            "model": row["model_id"],
            "extra": _parse_extra(row.get("extra_config")),
            "source": "db",
        }
    return {
        "provider": default["default_provider"],
        "model": default["default_model"],
        "extra": {},
        "source": "default",
    }


def upsert(use_case_code: str, *, provider: str, model: str,
           extra: dict | None = None, enabled: bool = True,
           updated_by: int | None) -> None:
    get_use_case(use_case_code)  # 校验存在
    execute(
        "INSERT INTO llm_use_case_bindings "
        "(use_case_code, provider_code, model_id, extra_config, enabled, updated_by) "
        "VALUES (%s, %s, %s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE "
        "  provider_code = VALUES(provider_code), "
        "  model_id = VALUES(model_id), "
        "  extra_config = VALUES(extra_config), "
        "  enabled = VALUES(enabled), "
        "  updated_by = VALUES(updated_by)",
        (use_case_code, provider, model,
         json.dumps(extra) if extra else None,
         1 if enabled else 0, updated_by),
    )


def delete(use_case_code: str) -> None:
    """删除覆盖，下次 resolve 回到默认。"""
    execute(
        "DELETE FROM llm_use_case_bindings WHERE use_case_code = %s",
        (use_case_code,),
    )


def list_all() -> list[dict]:
    """返回所有 use_case 的合并列表（USE_CASES 默认 ∪ DB 覆盖）。"""
    rows = query(
        "SELECT use_case_code, provider_code, model_id, extra_config, "
        "       enabled, updated_by, updated_at "
        "FROM llm_use_case_bindings"
    )
    by_code = {r["use_case_code"]: r for r in rows}
    out: list[dict] = []
    for code, uc in USE_CASES.items():
        row = by_code.get(code)
        if row and int(row.get("enabled") or 0) == 1:
            out.append({
                "code": code,
                "module": uc["module"],
                "label": uc["label"],
                "description": uc["description"],
                "provider": row["provider_code"],
                "model": row["model_id"],
                "extra": _parse_extra(row.get("extra_config")),
                "enabled": True,
                "is_custom": True,
                "updated_at": row.get("updated_at"),
                "updated_by": row.get("updated_by"),
            })
        else:
            out.append({
                "code": code,
                "module": uc["module"],
                "label": uc["label"],
                "description": uc["description"],
                "provider": uc["default_provider"],
                "model": uc["default_model"],
                "extra": {},
                "enabled": True,
                "is_custom": False,
                "updated_at": None,
                "updated_by": None,
            })
    return out
```

- [ ] **Step 3.4: 跑测试确认通过**

```bash
pytest tests/test_llm_bindings_dao.py -v
```
预期: 6 passed

- [ ] **Step 3.5: Commit**

```bash
git add appcore/llm_bindings.py tests/test_llm_bindings_dao.py
git commit -m "feat(llm): add bindings DAO with default fallback and DB override"
```

---

### Task 4: Provider 适配器 `appcore/llm_providers/`

**Files:**
- Create: `appcore/llm_providers/__init__.py`
- Create: `appcore/llm_providers/base.py`
- Create: `appcore/llm_providers/openrouter_adapter.py`
- Create: `appcore/llm_providers/gemini_adapter.py`
- Create: `tests/test_llm_providers_openrouter.py`
- Create: `tests/test_llm_providers_gemini.py`

- [ ] **Step 4.1: 写 base 抽象类 `appcore/llm_providers/base.py`**

```python
"""Provider Adapter 抽象基类。

每个 provider 实现 chat() 和 generate() 两个方法：
- chat: messages 列表风格（OpenAI 兼容格式），返回 {"text", "raw", "usage"}
- generate: 单 prompt + 可选 media/schema，返回 {"text" or dict, "raw", "usage"}

Adapter 不做 UseCase → provider 路由，只负责 provider 级的 HTTP 调用和 usage 规范化。
上层 llm_client.invoke() 负责选 adapter。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Iterable


class LLMAdapter(ABC):
    provider_code: str = ""

    @abstractmethod
    def resolve_credentials(self, user_id: int | None) -> dict:
        """返回 {'api_key': str, 'base_url': str|None, 'extra': dict}"""
        ...

    def chat(
        self,
        *,
        model: str,
        messages: list[dict],
        user_id: int | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict | None = None,
        extra_body: dict | None = None,
    ) -> dict:
        raise NotImplementedError(f"{self.provider_code} does not support chat()")

    def generate(
        self,
        *,
        model: str,
        prompt: str,
        user_id: int | None = None,
        system: str | None = None,
        media: Iterable[str | Path] | None = None,
        response_schema: dict | None = None,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
    ) -> dict:
        raise NotImplementedError(f"{self.provider_code} does not support generate()")
```

- [ ] **Step 4.2: 写 openrouter adapter `appcore/llm_providers/openrouter_adapter.py`**

```python
"""OpenRouter / 豆包 ARK 适配器（OpenAI-compatible）。"""
from __future__ import annotations

from openai import OpenAI

from appcore.api_keys import resolve_extra, resolve_key
from appcore.llm_providers.base import LLMAdapter
from config import (
    DOUBAO_LLM_API_KEY, DOUBAO_LLM_BASE_URL,
    OPENROUTER_API_KEY, OPENROUTER_BASE_URL,
)


class OpenRouterAdapter(LLMAdapter):
    provider_code = "openrouter"

    def resolve_credentials(self, user_id: int | None) -> dict:
        key = (resolve_key(user_id, "openrouter", "OPENROUTER_API_KEY")
               if user_id is not None else OPENROUTER_API_KEY)
        extra = resolve_extra(user_id, "openrouter") if user_id else {}
        return {
            "api_key": key or "",
            "base_url": extra.get("base_url") or OPENROUTER_BASE_URL,
            "extra": extra,
        }

    def chat(self, *, model, messages, user_id=None, temperature=None,
             max_tokens=None, response_format=None, extra_body=None):
        creds = self.resolve_credentials(user_id)
        if not creds["api_key"]:
            raise RuntimeError("OpenRouter API Key 未配置")
        client = OpenAI(api_key=creds["api_key"], base_url=creds["base_url"])
        body: dict = dict(extra_body or {})
        if response_format is not None:
            body["response_format"] = response_format
        # OpenRouter 的 response-healing 插件
        if not extra_body or "plugins" not in extra_body:
            body["plugins"] = [{"id": "response-healing"}]
        kwargs: dict = {}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if body:
            kwargs["extra_body"] = body
        resp = client.chat.completions.create(model=model, messages=messages, **kwargs)
        usage = getattr(resp, "usage", None)
        return {
            "text": resp.choices[0].message.content or "",
            "raw": resp,
            "usage": {
                "input_tokens": getattr(usage, "prompt_tokens", None) if usage else None,
                "output_tokens": getattr(usage, "completion_tokens", None) if usage else None,
            },
        }


class DoubaoAdapter(LLMAdapter):
    provider_code = "doubao"

    def resolve_credentials(self, user_id: int | None) -> dict:
        key = (resolve_key(user_id, "doubao_llm", "DOUBAO_LLM_API_KEY")
               if user_id is not None else DOUBAO_LLM_API_KEY)
        extra = resolve_extra(user_id, "doubao_llm") if user_id else {}
        return {
            "api_key": key or "",
            "base_url": extra.get("base_url") or DOUBAO_LLM_BASE_URL,
            "extra": extra,
        }

    def chat(self, *, model, messages, user_id=None, temperature=None,
             max_tokens=None, response_format=None, extra_body=None):
        creds = self.resolve_credentials(user_id)
        if not creds["api_key"]:
            raise RuntimeError("豆包 LLM API Key 未配置")
        client = OpenAI(api_key=creds["api_key"], base_url=creds["base_url"])
        # 豆包不支持 response_format / plugins；忽略之
        kwargs: dict = {}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        resp = client.chat.completions.create(model=model, messages=messages, **kwargs)
        usage = getattr(resp, "usage", None)
        return {
            "text": resp.choices[0].message.content or "",
            "raw": resp,
            "usage": {
                "input_tokens": getattr(usage, "prompt_tokens", None) if usage else None,
                "output_tokens": getattr(usage, "completion_tokens", None) if usage else None,
            },
        }
```

- [ ] **Step 4.3: 写 gemini adapter `appcore/llm_providers/gemini_adapter.py`**

```python
"""Gemini 适配器，复用现有 appcore.gemini。"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from appcore import gemini as gemini_api
from appcore.llm_providers.base import LLMAdapter


class GeminiAdapter(LLMAdapter):
    provider_code = "gemini"

    def resolve_credentials(self, user_id: int | None) -> dict:
        key, _ = gemini_api.resolve_config(user_id=user_id)
        return {"api_key": key or "", "base_url": None, "extra": {}}

    def generate(self, *, model, prompt, user_id=None, system=None,
                 media=None, response_schema=None, temperature=None,
                 max_output_tokens=None):
        media_list: list[str | Path] | None = None
        if media:
            media_list = [media] if isinstance(media, (str, Path)) else list(media)
        # 复用 appcore.gemini.generate —— 它自带重试、usage_log、backend 选择
        result = gemini_api.generate(
            prompt,
            system=system,
            model=model,
            media=media_list,
            response_schema=response_schema,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            user_id=user_id,
            service="gemini",          # usage_log 的 service 由 llm_client 覆盖
            default_model=model,
        )
        return {
            "text": result if isinstance(result, str) else None,
            "json": result if not isinstance(result, str) else None,
            "raw": None,
            "usage": {"input_tokens": None, "output_tokens": None},
        }

    def chat(self, *, model, messages, user_id=None, temperature=None,
             max_tokens=None, response_format=None, extra_body=None):
        """把 chat-style messages 折叠成 system + 最后一条 user prompt 走 generate()。"""
        system = None
        user_parts: list[str] = []
        for m in messages:
            role = m.get("role")
            content = m.get("content", "")
            if isinstance(content, list):
                content = "\n".join(p.get("text", "") for p in content if isinstance(p, dict))
            if role == "system":
                system = (system + "\n\n" + content) if system else content
            else:
                user_parts.append(str(content))
        prompt = "\n\n".join(user_parts)
        schema = (response_format or {}).get("json_schema", {}).get("schema") \
                 if response_format and response_format.get("type") == "json_schema" else None
        return self.generate(
            model=model, prompt=prompt, user_id=user_id,
            system=system, response_schema=schema,
            temperature=temperature, max_output_tokens=max_tokens,
        )
```

- [ ] **Step 4.4: 写 `appcore/llm_providers/__init__.py`**

```python
from appcore.llm_providers.base import LLMAdapter
from appcore.llm_providers.gemini_adapter import GeminiAdapter
from appcore.llm_providers.openrouter_adapter import DoubaoAdapter, OpenRouterAdapter

PROVIDER_ADAPTERS: dict[str, LLMAdapter] = {
    "openrouter": OpenRouterAdapter(),
    "doubao": DoubaoAdapter(),
    "gemini": GeminiAdapter(),
}


def get_adapter(provider_code: str) -> LLMAdapter:
    if provider_code not in PROVIDER_ADAPTERS:
        raise KeyError(f"unknown provider: {provider_code}")
    return PROVIDER_ADAPTERS[provider_code]
```

- [ ] **Step 4.5: 写 openrouter 测试 `tests/test_llm_providers_openrouter.py`**

```python
from unittest.mock import MagicMock, patch

from appcore.llm_providers.openrouter_adapter import DoubaoAdapter, OpenRouterAdapter


def _mock_openai(mock_cls, content="hi", prompt_tokens=10, completion_tokens=5):
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock(message=MagicMock(content=content))]
    mock_resp.usage = MagicMock(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_resp
    mock_cls.return_value = mock_client
    return mock_client


def test_openrouter_chat_returns_text_and_usage(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    with patch("appcore.llm_providers.openrouter_adapter.OpenAI") as m_openai:
        _mock_openai(m_openai, content="hello", prompt_tokens=7, completion_tokens=3)
        adapter = OpenRouterAdapter()
        result = adapter.chat(
            model="anthropic/claude-sonnet-4.6",
            messages=[{"role": "user", "content": "hi"}],
            user_id=None,
            temperature=0.2, max_tokens=100,
        )
    assert result["text"] == "hello"
    assert result["usage"] == {"input_tokens": 7, "output_tokens": 3}


def test_doubao_chat_skips_response_format(monkeypatch):
    monkeypatch.setenv("DOUBAO_LLM_API_KEY", "test-key")
    with patch("appcore.llm_providers.openrouter_adapter.OpenAI") as m_openai:
        client = _mock_openai(m_openai)
        adapter = DoubaoAdapter()
        adapter.chat(
            model="doubao-seed-2-0-pro",
            messages=[{"role": "user", "content": "hi"}],
            response_format={"type": "json_object"},
        )
    kwargs = client.chat.completions.create.call_args.kwargs
    assert "extra_body" not in kwargs  # 豆包分支不应传 extra_body


def test_openrouter_missing_key_raises(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    import config
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "", raising=False)
    adapter = OpenRouterAdapter()
    import pytest
    with pytest.raises(RuntimeError, match="OpenRouter"):
        adapter.chat(
            model="x", messages=[{"role": "user", "content": "hi"}],
            user_id=None,
        )
```

- [ ] **Step 4.6: 写 gemini 测试 `tests/test_llm_providers_gemini.py`**

```python
from unittest.mock import patch

from appcore.llm_providers.gemini_adapter import GeminiAdapter


def test_gemini_generate_calls_underlying_module():
    adapter = GeminiAdapter()
    with patch("appcore.llm_providers.gemini_adapter.gemini_api.generate",
               return_value="result-text") as m:
        result = adapter.generate(
            model="gemini-3.1-pro-preview",
            prompt="hello",
            user_id=42,
            system="you are helpful",
            temperature=0.1,
        )
    assert result["text"] == "result-text"
    assert m.call_args.kwargs["model"] == "gemini-3.1-pro-preview"
    assert m.call_args.kwargs["user_id"] == 42


def test_gemini_generate_returns_json_when_schema_given():
    adapter = GeminiAdapter()
    with patch("appcore.llm_providers.gemini_adapter.gemini_api.generate",
               return_value={"score": 95}):
        result = adapter.generate(
            model="gemini-3.1-pro-preview",
            prompt="score this",
            response_schema={"type": "object"},
        )
    assert result["json"] == {"score": 95}
    assert result["text"] is None


def test_gemini_chat_folds_system_and_user_messages():
    adapter = GeminiAdapter()
    with patch("appcore.llm_providers.gemini_adapter.gemini_api.generate",
               return_value="ok") as m:
        adapter.chat(
            model="gemini-3.1-pro-preview",
            messages=[
                {"role": "system", "content": "S1"},
                {"role": "user", "content": "U1"},
                {"role": "user", "content": "U2"},
            ],
        )
    kwargs = m.call_args.kwargs
    assert kwargs["system"] == "S1"
    assert "U1" in kwargs["prompt"] and "U2" in kwargs["prompt"]
```

- [ ] **Step 4.7: 跑 adapter 测试**

```bash
pytest tests/test_llm_providers_openrouter.py tests/test_llm_providers_gemini.py -v
```
预期: 6 passed

- [ ] **Step 4.8: Commit**

```bash
git add appcore/llm_providers/ tests/test_llm_providers_openrouter.py tests/test_llm_providers_gemini.py
git commit -m "feat(llm): add provider adapters (openrouter/doubao/gemini)"
```

---

### Task 5: 统一入口 `appcore/llm_client.py`

**Files:**
- Create: `appcore/llm_client.py`
- Create: `tests/test_llm_client_invoke.py`

- [ ] **Step 5.1: 写测试**

```python
from unittest.mock import MagicMock, patch

from appcore import llm_client


def test_invoke_chat_resolves_binding_and_calls_adapter():
    fake_adapter = MagicMock()
    fake_adapter.chat.return_value = {
        "text": "ok", "raw": None,
        "usage": {"input_tokens": 5, "output_tokens": 3},
    }
    with patch("appcore.llm_client.llm_bindings.resolve",
               return_value={"provider": "openrouter", "model": "x",
                             "extra": {}, "source": "db"}), \
         patch("appcore.llm_client.get_adapter", return_value=fake_adapter), \
         patch("appcore.llm_client._log_usage") as m_log:
        result = llm_client.invoke_chat(
            "copywriting.generate",
            messages=[{"role": "user", "content": "hi"}],
            user_id=42,
        )
    assert result["text"] == "ok"
    fake_adapter.chat.assert_called_once()
    m_log.assert_called_once()


def test_invoke_generate_routes_to_adapter_generate():
    fake_adapter = MagicMock()
    fake_adapter.generate.return_value = {
        "text": None, "json": {"score": 80},
        "raw": None, "usage": {"input_tokens": None, "output_tokens": None},
    }
    with patch("appcore.llm_client.llm_bindings.resolve",
               return_value={"provider": "gemini", "model": "gemini-3.1-pro-preview",
                             "extra": {}, "source": "default"}), \
         patch("appcore.llm_client.get_adapter", return_value=fake_adapter), \
         patch("appcore.llm_client._log_usage"):
        result = llm_client.invoke_generate(
            "video_score.run",
            prompt="score this",
            user_id=1, project_id="proj-1",
            response_schema={"type": "object"},
        )
    assert result["json"] == {"score": 80}


def test_invoke_records_usage_with_correct_service():
    """usage_log 的 service 来自 USE_CASES[use_case].usage_log_service，不是 provider。"""
    fake_adapter = MagicMock()
    fake_adapter.chat.return_value = {"text": "x", "raw": None,
                                       "usage": {"input_tokens": 1, "output_tokens": 1}}
    with patch("appcore.llm_client.llm_bindings.resolve",
               return_value={"provider": "openrouter", "model": "m",
                             "extra": {}, "source": "db"}), \
         patch("appcore.llm_client.get_adapter", return_value=fake_adapter), \
         patch("appcore.llm_client.usage_log.record") as m_record:
        llm_client.invoke_chat(
            "video_translate.localize",
            messages=[{"role": "user", "content": "x"}],
            user_id=10, project_id="p1",
        )
    assert m_record.called
    kwargs = m_record.call_args.kwargs
    # 对应 USE_CASES["video_translate.localize"].usage_log_service == "openrouter"
    assert kwargs.get("service") == "openrouter" or m_record.call_args[0][2] == "openrouter"
```

- [ ] **Step 5.2: 跑测试确认失败**

```bash
pytest tests/test_llm_client_invoke.py -v
```
预期: `ModuleNotFoundError: appcore.llm_client`

- [ ] **Step 5.3: 实现 `appcore/llm_client.py`**

```python
"""统一 LLM 调用入口。

使用方式：
    llm_client.invoke_chat("video_translate.localize", messages=[...], user_id=42)
    llm_client.invoke_generate("video_score.run", prompt="...", user_id=1,
                               media=[video_path], response_schema={...})

内部流程：
  1. llm_bindings.resolve(use_case) → 取 provider + model
  2. get_adapter(provider) → 取 Adapter 实例
  3. adapter.chat() 或 adapter.generate()
  4. _log_usage() → 写 usage_logs
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Iterable

from appcore import llm_bindings, usage_log
from appcore.llm_providers import get_adapter
from appcore.llm_use_cases import get_use_case

log = logging.getLogger(__name__)


def _log_usage(*, use_case_code: str, user_id: int | None, project_id: str | None,
               model: str, success: bool, usage: dict | None,
               error: Exception | None = None) -> None:
    if user_id is None:
        return
    try:
        uc = get_use_case(use_case_code)
        extra: dict[str, Any] = {"use_case": use_case_code}
        if error is not None:
            extra["error"] = str(error)[:500]
        usage_log.record(
            user_id, project_id,
            service=uc["usage_log_service"],
            model_name=model, success=success,
            input_tokens=(usage or {}).get("input_tokens"),
            output_tokens=(usage or {}).get("output_tokens"),
            extra_data=extra,
        )
    except Exception:
        log.debug("usage_log failed for use_case=%s", use_case_code, exc_info=True)


def invoke_chat(
    use_case_code: str,
    *,
    messages: list[dict],
    user_id: int | None = None,
    project_id: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    response_format: dict | None = None,
    extra_body: dict | None = None,
    provider_override: str | None = None,
    model_override: str | None = None,
) -> dict:
    binding = llm_bindings.resolve(use_case_code)
    provider = provider_override or binding["provider"]
    model = model_override or binding["model"]
    adapter = get_adapter(provider)
    try:
        result = adapter.chat(
            model=model, messages=messages, user_id=user_id,
            temperature=temperature, max_tokens=max_tokens,
            response_format=response_format, extra_body=extra_body,
        )
    except Exception as e:
        _log_usage(use_case_code=use_case_code, user_id=user_id,
                   project_id=project_id, model=model,
                   success=False, usage=None, error=e)
        raise
    _log_usage(use_case_code=use_case_code, user_id=user_id,
               project_id=project_id, model=model,
               success=True, usage=result.get("usage"))
    return result


def invoke_generate(
    use_case_code: str,
    *,
    prompt: str,
    user_id: int | None = None,
    project_id: str | None = None,
    system: str | None = None,
    media: Iterable[str | Path] | str | Path | None = None,
    response_schema: dict | None = None,
    temperature: float | None = None,
    max_output_tokens: int | None = None,
    provider_override: str | None = None,
    model_override: str | None = None,
) -> dict:
    binding = llm_bindings.resolve(use_case_code)
    provider = provider_override or binding["provider"]
    model = model_override or binding["model"]
    adapter = get_adapter(provider)
    try:
        result = adapter.generate(
            model=model, prompt=prompt, user_id=user_id,
            system=system, media=media,
            response_schema=response_schema,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
    except Exception as e:
        _log_usage(use_case_code=use_case_code, user_id=user_id,
                   project_id=project_id, model=model,
                   success=False, usage=None, error=e)
        raise
    _log_usage(use_case_code=use_case_code, user_id=user_id,
               project_id=project_id, model=model,
               success=True, usage=result.get("usage"))
    return result
```

- [ ] **Step 5.4: 跑测试确认通过**

```bash
pytest tests/test_llm_client_invoke.py -v
```
预期: 3 passed

- [ ] **Step 5.5: 跑全量测试确认没破坏任何现有测试**

```bash
pytest tests/ -q
```
预期: 全部之前通过的测试仍通过（0 regression）

- [ ] **Step 5.6: Commit**

```bash
git add appcore/llm_client.py tests/test_llm_client_invoke.py
git commit -m "feat(llm): add unified llm_client.invoke_chat/invoke_generate entry"
```

---

# Phase 2 · 老路径转发（零行为变化）

### Task 6: `pipeline/translate.py:resolve_provider_config()` 前置查 bindings

**Files:**
- Modify: `pipeline/translate.py:36-65`
- Test: 现有 `tests/test_localization.py` / `tests/test_pipeline_text_translate.py` / `tests/test_runtime_multi_translate.py`

- [ ] **Step 6.1: 加一个集成测试验证 fallback 行为**

`tests/test_translate_resolve_bindings_fallback.py`：

```python
from unittest.mock import patch

from pipeline.translate import resolve_provider_config


def test_resolve_falls_back_to_legacy_when_no_binding(monkeypatch):
    """无 binding 时，行为与老代码一致（走 _OPENROUTER_PREF_MODELS）。"""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    with patch("pipeline.translate._binding_lookup", return_value=None):
        _, model = resolve_provider_config("gemini_31_flash", user_id=None)
    assert model == "google/gemini-3.1-flash-lite-preview"


def test_resolve_uses_binding_when_present(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    fake_binding = {"provider": "openrouter", "model": "openai/gpt-4o", "extra": {}}
    with patch("pipeline.translate._binding_lookup", return_value=fake_binding):
        _, model = resolve_provider_config("video_translate.localize", user_id=None)
    assert model == "openai/gpt-4o"


def test_resolve_legacy_provider_names_still_work(monkeypatch):
    """老代码传 'doubao' / 'openrouter' / 'claude_sonnet' 等字符串时保持兼容。"""
    monkeypatch.setenv("DOUBAO_LLM_API_KEY", "test-key")
    with patch("pipeline.translate._binding_lookup", return_value=None):
        _, model = resolve_provider_config("doubao", user_id=None)
    assert model  # 不抛异常，返回默认豆包模型
```

- [ ] **Step 6.2: 跑测试确认失败**

```bash
pytest tests/test_translate_resolve_bindings_fallback.py -v
```
预期：`_binding_lookup` 不存在

- [ ] **Step 6.3: 改 `pipeline/translate.py`**

在 `resolve_provider_config` 上方加一个内部辅助函数 `_binding_lookup`，然后在函数开头加一段查 bindings 的逻辑。修改后的函数：

```python
def _binding_lookup(provider_or_use_case: str) -> dict | None:
    """如果入参看起来像 use_case_code（含 '.'），尝试查 bindings；否则返回 None。"""
    if "." not in provider_or_use_case:
        return None
    try:
        from appcore import llm_bindings
        return llm_bindings.resolve(provider_or_use_case)
    except KeyError:
        return None


def resolve_provider_config(
    provider: str,
    user_id: int | None = None,
    api_key_override: str | None = None,
) -> tuple[OpenAI, str]:
    """Return (client, model_id). 
    
    provider 参数支持两种：
    1. 老风格: "doubao" / "openrouter" / "gemini_31_flash" / "claude_sonnet" 等，走 _OPENROUTER_PREF_MODELS
    2. 新风格: UseCase code，如 "video_translate.localize"，从 bindings 表查 provider+model
    """
    from appcore.api_keys import resolve_extra, resolve_key

    binding = _binding_lookup(provider)
    if binding:
        # 新路径：binding 决定真正的 provider + model
        real_provider = binding["provider"]
        real_model = binding["model"]
        if real_provider == "doubao":
            key = api_key_override or (
                resolve_key(user_id, "doubao_llm", "DOUBAO_LLM_API_KEY") if user_id else DOUBAO_LLM_API_KEY
            )
            extra = resolve_extra(user_id, "doubao_llm") if user_id else {}
            base_url = extra.get("base_url") or DOUBAO_LLM_BASE_URL
            return OpenAI(api_key=key, base_url=base_url), real_model
        # gemini 在本函数不适用（它走 appcore/gemini.py），按 openrouter 的通路处理 model id
        key = api_key_override or (
            resolve_key(user_id, "openrouter", "OPENROUTER_API_KEY") if user_id else OPENROUTER_API_KEY
        )
        extra = resolve_extra(user_id, "openrouter") if user_id else {}
        base_url = extra.get("base_url") or OPENROUTER_BASE_URL
        return OpenAI(api_key=key, base_url=base_url), real_model

    # ── 以下是完全保留的老代码 ──
    if provider == "doubao":
        key = api_key_override or (
            resolve_key(user_id, "doubao_llm", "DOUBAO_LLM_API_KEY") if user_id else DOUBAO_LLM_API_KEY
        )
        extra = resolve_extra(user_id, "doubao_llm") if user_id else {}
        base_url = extra.get("base_url") or DOUBAO_LLM_BASE_URL
        model = extra.get("model_id") or DOUBAO_LLM_MODEL
    else:
        key = api_key_override or (
            resolve_key(user_id, "openrouter", "OPENROUTER_API_KEY") if user_id else OPENROUTER_API_KEY
        )
        extra = resolve_extra(user_id, "openrouter") if user_id else {}
        base_url = extra.get("base_url") or OPENROUTER_BASE_URL
        user_override = (extra.get("model_id") or "").strip()
        if user_override:
            model = user_override
        else:
            model = _OPENROUTER_PREF_MODELS.get(provider, CLAUDE_MODEL)

    return OpenAI(api_key=key, base_url=base_url), model
```

- [ ] **Step 6.4: 跑新测试 + 回归**

```bash
pytest tests/test_translate_resolve_bindings_fallback.py tests/test_localization.py tests/test_pipeline_text_translate.py -v
```
预期: 全 pass

- [ ] **Step 6.5: 跑全量测试**

```bash
pytest tests/ -q
```
预期: 0 regression

- [ ] **Step 6.6: Commit**

```bash
git add pipeline/translate.py tests/test_translate_resolve_bindings_fallback.py
git commit -m "refactor(translate): resolve_provider_config supports use_case code lookup"
```

---

### Task 7: `appcore/gemini.py:resolve_config()` 支持 use_case

**Files:**
- Modify: `appcore/gemini.py:59-83`
- Test: `tests/test_gemini_client.py`（新增 case）

- [ ] **Step 7.1: 加测试到 `tests/test_gemini_resolve_use_case.py`（新文件）**

```python
from unittest.mock import patch

from appcore import gemini


def test_resolve_config_uses_use_case_binding_when_service_is_use_case(monkeypatch):
    """当 service 参数看起来像 use_case_code（含 '.'）时，优先从 bindings 表取 model。"""
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    with patch("appcore.gemini._binding_lookup",
               return_value={"provider": "gemini", "model": "gemini-custom-model"}):
        _, model = gemini.resolve_config(
            user_id=42, service="video_score.run",
            default_model="gemini-3.1-pro-preview",
        )
    assert model == "gemini-custom-model"


def test_resolve_config_fallback_when_no_binding(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    with patch("appcore.gemini._binding_lookup", return_value=None):
        _, model = gemini.resolve_config(
            user_id=None, service="gemini",
            default_model="default-model",
        )
    assert model == "default-model"


def test_resolve_config_ignores_binding_of_non_gemini_provider(monkeypatch):
    """如果 binding 指向非 gemini provider，应忽略并 fallback（防止 gemini.py 用错模型）。"""
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    with patch("appcore.gemini._binding_lookup",
               return_value={"provider": "openrouter", "model": "claude-sonnet"}):
        _, model = gemini.resolve_config(
            user_id=42, service="video_score.run",
            default_model="gemini-default",
        )
    assert model == "gemini-default"
```

- [ ] **Step 7.2: 跑测试确认失败**

```bash
pytest tests/test_gemini_resolve_use_case.py -v
```

- [ ] **Step 7.3: 改 `appcore/gemini.py:resolve_config()`**

在 `resolve_config` 上方加 `_binding_lookup`，函数中新增一段：

```python
def _binding_lookup(service: str) -> dict | None:
    if "." not in service:
        return None
    try:
        from appcore import llm_bindings
        return llm_bindings.resolve(service)
    except KeyError:
        return None


def resolve_config(user_id: int | None = None, service: str = "gemini",
                   default_model: str | None = None) -> tuple[str, str]:
    """返回 (api_key, model_id)。

    service 参数支持两种：
    1. 老风格服务名: "gemini" / "gemini_video_analysis" — 读 api_keys 里该服务的 model_id
    2. UseCase code（含 '.'）如 "video_score.run" — 读 bindings 表的 provider+model
       （仅当 binding.provider == "gemini" 时才覆盖 model，否则忽略 binding）
    """
    # 新路径：尝试 bindings
    binding = _binding_lookup(service)
    if binding and binding.get("provider") == "gemini":
        chosen_model = (binding.get("model") or "").strip()
        # key 仍然走 gemini 通道；只覆盖 model
        key = ""
        if user_id is not None:
            key = (resolve_key(user_id, "gemini", "GEMINI_API_KEY") or "").strip()
        if GEMINI_BACKEND == "cloud":
            key = key or GEMINI_CLOUD_API_KEY
        else:
            key = key or GEMINI_API_KEY
        return key, chosen_model or (default_model or GEMINI_MODEL)

    # ── 以下是完全保留的老代码 ──
    key = ""
    if user_id is not None:
        key = (resolve_key(user_id, service, "GEMINI_API_KEY") or "").strip()
        if not key and service != "gemini":
            key = (resolve_key(user_id, "gemini", "GEMINI_API_KEY") or "").strip()
    if GEMINI_BACKEND == "cloud":
        key = key or GEMINI_CLOUD_API_KEY
    else:
        key = key or GEMINI_API_KEY

    model = default_model or GEMINI_MODEL
    if user_id is not None:
        extra = resolve_extra(user_id, service) or {}
        chosen = (extra.get("model_id") or "").strip()
        if chosen:
            model = chosen
    return key, model
```

- [ ] **Step 7.4: 跑新测试 + 回归**

```bash
pytest tests/test_gemini_resolve_use_case.py tests/test_gemini_client.py tests/test_gemini_image.py -v
pytest tests/ -q
```
预期: 0 regression

- [ ] **Step 7.5: Commit**

```bash
git add appcore/gemini.py tests/test_gemini_resolve_use_case.py
git commit -m "refactor(gemini): resolve_config supports use_case code lookup"
```

---

# Phase 3 · 硬编码模块迁移（逐个切）

**约定**：以下每个 Task 的目标都是——把该模块的硬编码模型改为从 bindings 里取，但 UI 侧先不加新 binding 记录。所以在 DB 没有记录时，仍然走默认（即 USE_CASES 里的 `default_model`，和原硬编码值一致）。**行为上是零变化**，但代码层面已经解耦。

### Task 8: `appcore/link_check_gemini.py` → `link_check.analyze`

**Files:**
- Modify: `appcore/link_check_gemini.py:45-81`
- Test: 现有 `tests/test_link_check*.py`（如有；若无，新增一个）

- [ ] **Step 8.1: 检查是否有 link_check 测试**

```bash
ls tests/ | grep link
```

- [ ] **Step 8.2: 改 `appcore/link_check_gemini.py`**

把 `service="gemini", default_model=_FLASH_MODEL` 替换为走 bindings：

```python
# appcore/link_check_gemini.py（只 diff 关键处）
from appcore.llm_use_cases import get_use_case

_USE_CASE = "link_check.analyze"
# _FLASH_MODEL 保留作为 fallback 常量（USE_CASES 的 default_model 也是 gemini-2.5-flash）


def analyze_image(image_path: str | Path, *, target_language: str, target_language_name: str) -> dict:
    media_path = Path(image_path)
    uc = get_use_case(_USE_CASE)  # 仅用于获取 default_model，供 gemini.resolve_config fallback
    raw = gemini.generate(
        _build_prompt(
            target_language=target_language,
            target_language_name=target_language_name,
        ),
        media=[media_path],
        response_schema=_RESPONSE_SCHEMA,
        temperature=0,
        service=_USE_CASE,                    # ← 关键：传 use_case_code
        default_model=uc["default_model"],    # ← Gemini 层 fallback 用
    )
    # 下面逻辑不变
    payload = raw if isinstance(raw, dict) else {}
    # ...
```

- [ ] **Step 8.3: 跑相关测试**

```bash
pytest tests/test_link_check*.py -v
pytest tests/ -q
```

- [ ] **Step 8.4: Commit**

```bash
git add appcore/link_check_gemini.py
git commit -m "refactor(link_check): route through use_case binding for model selection"
```

---

### Task 9: `pipeline/shot_decompose.py` → `shot_decompose.run`

**Files:**
- Modify: `pipeline/shot_decompose.py:50-73`
- Test: `tests/test_shot_decompose.py`

- [ ] **Step 9.1: 改代码**

```python
# pipeline/shot_decompose.py
from appcore.llm_use_cases import get_use_case

_USE_CASE = "shot_decompose.run"
# DEFAULT_MODEL 常量保留做文档 / 兼容旧调用者


def decompose_shots(
    video_path: str,
    *,
    user_id: int,
    duration_seconds: float,
    model: str | None = None,   # 可选显式覆盖
) -> List[Dict[str, Any]]:
    uc = get_use_case(_USE_CASE)
    effective_model = model or uc["default_model"]
    prompt = SHOT_DECOMPOSE_PROMPT.format(duration=duration_seconds)
    response = gemini_generate(
        prompt,
        media=[video_path],
        user_id=user_id,
        model=effective_model,
        response_schema=SHOT_DECOMPOSE_SCHEMA,
        service=_USE_CASE,                    # ← 传 use_case_code
        default_model=effective_model,
    )
    shots = (response or {}).get("shots") or []
    _normalize_shots(shots, duration_seconds)
    return shots
```

- [ ] **Step 9.2: 跑测试**

```bash
pytest tests/test_shot_decompose.py -v
pytest tests/ -q
```

- [ ] **Step 9.3: Commit**

```bash
git add pipeline/shot_decompose.py
git commit -m "refactor(shot_decompose): route through use_case binding"
```

---

### Task 10: `pipeline/video_score.py` → `video_score.run`

**Files:**
- Modify: `pipeline/video_score.py:13,51-115`

- [ ] **Step 10.1: 改代码**

```python
# pipeline/video_score.py
from appcore.llm_use_cases import get_use_case

_USE_CASE = "video_score.run"
# SCORE_MODEL 常量保留（USE_CASES 里 default_model 值与之一致）


def score_video(video_path, *, user_id=None, project_id=None) -> dict:
    p = Path(video_path)
    if not p.is_file():
        raise FileNotFoundError(f"视频不存在：{p}")
    uc = get_use_case(_USE_CASE)

    raw = gemini.generate(
        USER_PROMPT,
        system=SYSTEM_PROMPT,
        media=p,
        temperature=0.2, max_output_tokens=4096,
        user_id=user_id, project_id=project_id,
        service=_USE_CASE,                    # ← 传 use_case_code
        default_model=uc["default_model"],
    )
    # ... 后续 DIMENSIONS 处理不变
    return {
        # ...
        "model": uc["default_model"],         # 改用 uc default；binding 覆盖时应该读实际调用模型
        "scored_at": datetime.utcnow().isoformat() + "Z",
    }
```

> **Note**：`"model"` 字段值改为从 binding resolver 取（更准确）。但当前 `gemini.generate()` 不返回实际 model，所以用 `get_use_case` 默认；若要精确记录，未来可以让 `gemini.generate` 返回实际模型名。本期保持 best-effort。

- [ ] **Step 10.2: 跑测试**

```bash
pytest tests/test_video_score.py -v 2>/dev/null || echo "(no test)"
pytest tests/ -q
```

- [ ] **Step 10.3: Commit**

```bash
git add pipeline/video_score.py
git commit -m "refactor(video_score): route through use_case binding"
```

---

### Task 11: `pipeline/video_review.py` → `video_review.analyze`

**Files:**
- Modify: `pipeline/video_review.py:16,197-end of analyze_video`
- Test: `tests/test_video_review*.py`（如有）

- [ ] **Step 11.1: 改代码**

找 `analyze_video` 函数入口，在里面把 `default_model` 改为读 `get_use_case("video_review.analyze")["default_model"]`，并把 `service` 参数由 `"gemini_video_analysis"` 改为 `"video_review.analyze"`。保留 `model` 参数（调用方如视频评测 UI 可能已经在传自定义模型）。

```python
# pipeline/video_review.py
from appcore.llm_use_cases import get_use_case

_USE_CASE = "video_review.analyze"


def analyze_video(
    video_path,
    *,
    user_id: int | None = None,
    model: str = None,                 # 改为 None，默认走 USE_CASES
    custom_prompt: str | None = None,
    prompt_lang: str = "en",
    # ... 其他参数
):
    uc = get_use_case(_USE_CASE)
    effective_model = model or uc["default_model"]
    # 下面把所有 gemini.generate / gemini.generate_stream 的调用里
    #   service="gemini_video_analysis" → service=_USE_CASE
    #   default_model=DEFAULT_MODEL → default_model=effective_model
```

- [ ] **Step 11.2: 跑测试**

```bash
pytest tests/test_video_review*.py -v 2>/dev/null
pytest tests/ -q
```

- [ ] **Step 11.3: Commit**

```bash
git add pipeline/video_review.py
git commit -m "refactor(video_review): route through use_case binding"
```

---

### Task 12: `pipeline/copywriting.py` → `copywriting.generate` / `copywriting.rewrite`

**Files:**
- Modify: `pipeline/copywriting.py`（`generate_copywriting` + `rewrite_segment`）
- Test: `tests/test_copywriting*.py`

**关键约束**：文案创作页面已经在前端提供 provider 下拉，调用后端时传的是老风格 `provider="openrouter"/"doubao"/"gemini"`。本期**保留前端临时覆盖能力**，但内部 resolve 改为走 `llm_bindings.resolve` 然后允许覆盖。

- [ ] **Step 12.1: 在 `generate_copywriting` 和 `rewrite_segment` 中引入 use_case + override 参数**

```python
# pipeline/copywriting.py
from appcore.llm_bindings import resolve as resolve_binding


def generate_copywriting(*args, provider=None, model=None, **kwargs):
    # 旧签名：provider/model 可能是 None
    if not provider or not model:
        binding = resolve_binding("copywriting.generate")
        provider = provider or binding["provider"]
        model = model or binding["model"]
    # ... 原流程，但 provider/model 走新拿到的值
```

对应的 `rewrite_segment` 用 `"copywriting.rewrite"`。

- [ ] **Step 12.2: 前端 UI 保留**

web/routes/copywriting.py 若前端显式传了 `provider:model`，代码要把它作为 override 优先。这里无需改后端路由（老逻辑已经会 parse）；只要确保 resolve 时 `provider/model != None` 不会进 fallback。

- [ ] **Step 12.3: 跑测试**

```bash
pytest tests/test_copywriting*.py -v
pytest tests/ -q
```

- [ ] **Step 12.4: Commit**

```bash
git add pipeline/copywriting.py
git commit -m "refactor(copywriting): default provider/model via use_case bindings, UI override preserved"
```

---

### Task 13: `pipeline/text_translate.py` → `text_translate.generate`

**Files:**
- Modify: `pipeline/text_translate.py:34-100`

- [ ] **Step 13.1: 改代码**

`translate_text()` 不直接做 use_case 解析，因为 `resolve_provider_config` 已经支持 use_case code。最简单做法：让调用方传 `provider="text_translate.generate"`（一个 use_case code），`resolve_provider_config` 的 `_binding_lookup` 会识别并走 bindings。

但现有调用方传的是 `"openrouter" / "doubao"`，保持兼容。这里**不改 `translate_text`，只在文档里注明支持 use_case code**：

```python
# pipeline/text_translate.py 顶部文档字符串补一句
"""...
provider 参数支持两种格式：
- 老风格: 'openrouter' | 'doubao'（沿用 _OPENROUTER_PREF_MODELS）
- 新风格: UseCase code 如 'text_translate.generate'（走 bindings 表）
"""
```

（代码无需改动，只是文档增补。）

- [ ] **Step 13.2: 跑测试 + 回归**

```bash
pytest tests/test_pipeline_text_translate.py -v
pytest tests/ -q
```

- [ ] **Step 13.3: Commit（含文档微调）**

```bash
git add pipeline/text_translate.py
git commit -m "docs(text_translate): note use_case code support via resolve_provider_config"
```

---

### Task 14: `appcore/gemini_image.py` → `image_translate.generate`

**Files:**
- Modify: `appcore/gemini_image.py:259-336`（`generate_image`）

- [ ] **Step 14.1: 改代码**

在 `generate_image()` 入口把 `service` 默认改为 `"image_translate.generate"`；让下游 `gemini.resolve_config` 的 `_binding_lookup` 识别走 bindings：

```python
# appcore/gemini_image.py
from appcore.llm_use_cases import get_use_case

_USE_CASE_GENERATE = "image_translate.generate"


def generate_image(...):
    uc = get_use_case(_USE_CASE_GENERATE)
    # service/default_model 改为新值；其余保留
    # 把 gemini.generate 的调用 service="gemini" 改为 service=_USE_CASE_GENERATE
    # default_model 改为 uc["default_model"]
```

同理 `link_check_gemini.py` 已经在 Task 8 切好。

- [ ] **Step 14.2: 跑测试**

```bash
pytest tests/test_gemini_image.py tests/test_image_translate_*.py -v
pytest tests/ -q
```

- [ ] **Step 14.3: Commit**

```bash
git add appcore/gemini_image.py
git commit -m "refactor(gemini_image): route through use_case binding"
```

---

# Phase 4 · UI 重构（3 Tab）

### Task 15: 新 Provider DAO `appcore/llm_providers_dao.py`

这是一个简单的 wrapper，统一"所有供应商"视角（含 ElevenLabs / 火山 ASR）。沿用 `api_keys` 表（用户级）和 `system_settings`（全局级，用于 ASR 管理员）。

**Files:**
- Create: `appcore/llm_providers_dao.py`
- Create: `tests/test_llm_providers_dao.py`

- [ ] **Step 15.1: 写 DAO**

```python
"""Provider 前端视图 DAO。

封装"供应商卡片"视角所需的全部读写：
- LLM 类 provider（openrouter / doubao / gemini）：用户级 api_keys
- ElevenLabs：用户级 api_keys（service='elevenlabs'）
- 火山 ASR：全局 system_settings（key='provider.volc_asr.api_key'），仅管理员可改
"""
from __future__ import annotations

from appcore.api_keys import get_all, set_key
from appcore.settings import get_setting, set_setting

USER_LEVEL_PROVIDERS = [
    # (code, label, fields_meta)
    ("openrouter", "OpenRouter", [
        ("key_value", "API Key", "password"),
        ("base_url", "Base URL", "text"),
    ]),
    ("doubao_llm", "豆包 ARK", [
        ("key_value", "API Key", "password"),
        ("base_url", "Base URL", "text"),
    ]),
    ("gemini", "Google Gemini", [
        ("key_value", "API Key", "password"),
    ]),
    ("elevenlabs", "ElevenLabs", [
        ("key_value", "API Key", "password"),
    ]),
]

GLOBAL_PROVIDERS = [
    ("volc_asr", "火山引擎 ASR", [
        ("api_key", "API Key", "password"),
    ]),
]

_VOLC_ASR_KEY = "provider.volc_asr.api_key"


def load_user_providers(user_id: int) -> dict:
    """返回 {provider_code: {field_key: value}} 供模板展示。"""
    raw = get_all(user_id)  # {service: {key_value, extra}}
    out: dict = {}
    for code, _, fields in USER_LEVEL_PROVIDERS:
        entry = raw.get(code) or {}
        field_values: dict = {"key_value": entry.get("key_value", "")}
        extra = entry.get("extra") or {}
        for fname, _, _ in fields:
            if fname != "key_value":
                field_values[fname] = extra.get(fname, "")
        out[code] = field_values
    return out


def save_user_provider(user_id: int, code: str, fields: dict) -> None:
    matched = next((f for c, _, f in USER_LEVEL_PROVIDERS if c == code), None)
    if matched is None:
        raise ValueError(f"unknown provider: {code}")
    key_value = (fields.get("key_value") or "").strip()
    extra = {}
    for fname, _, _ in matched:
        if fname == "key_value":
            continue
        v = (fields.get(fname) or "").strip()
        if v:
            extra[fname] = v
    set_key(user_id, code, key_value, extra or None)


def load_global_providers() -> dict:
    return {
        "volc_asr": {"api_key": get_setting(_VOLC_ASR_KEY) or ""},
    }


def save_global_provider(code: str, fields: dict) -> None:
    if code == "volc_asr":
        set_setting(_VOLC_ASR_KEY, (fields.get("api_key") or "").strip())
    else:
        raise ValueError(f"unknown global provider: {code}")
```

- [ ] **Step 15.2: 简单测试**

```python
# tests/test_llm_providers_dao.py
from unittest.mock import patch

from appcore import llm_providers_dao


def test_load_user_providers_shape():
    fake = {
        "openrouter": {"key_value": "k1", "extra": {"base_url": "u1"}},
        "elevenlabs": {"key_value": "k2", "extra": {}},
    }
    with patch("appcore.llm_providers_dao.get_all", return_value=fake):
        out = llm_providers_dao.load_user_providers(1)
    assert out["openrouter"] == {"key_value": "k1", "base_url": "u1"}
    assert out["elevenlabs"] == {"key_value": "k2"}
    assert out["doubao_llm"] == {"key_value": "", "base_url": ""}
    assert out["gemini"] == {"key_value": ""}


def test_save_user_provider_routes_extra_fields():
    with patch("appcore.llm_providers_dao.set_key") as m:
        llm_providers_dao.save_user_provider(
            1, "openrouter",
            {"key_value": "kkk", "base_url": "https://x"}
        )
    m.assert_called_once_with(1, "openrouter", "kkk", {"base_url": "https://x"})


def test_save_global_provider_volc_asr():
    with patch("appcore.llm_providers_dao.set_setting") as m:
        llm_providers_dao.save_global_provider("volc_asr", {"api_key": "kkk"})
    m.assert_called_once_with("provider.volc_asr.api_key", "kkk")
```

- [ ] **Step 15.3: 跑测试 + commit**

```bash
pytest tests/test_llm_providers_dao.py -v
git add appcore/llm_providers_dao.py tests/test_llm_providers_dao.py
git commit -m "feat(llm): add provider DAO covering user-level and global providers"
```

---

### Task 16: 新 `web/routes/settings.py`

**Files:**
- Modify: `web/routes/settings.py`（整个重写）
- Test: `tests/test_settings_routes_new.py`

- [ ] **Step 16.1: 写测试先**

```python
# tests/test_settings_routes_new.py
from unittest.mock import patch


def test_settings_get_renders_three_tabs(authed_client_no_db):
    with patch("web.routes.settings.llm_providers_dao.load_user_providers",
               return_value={}), \
         patch("web.routes.settings.llm_providers_dao.load_global_providers",
               return_value={"volc_asr": {"api_key": ""}}), \
         patch("web.routes.settings.llm_bindings.list_all",
               return_value=[]):
        resp = authed_client_no_db.get("/settings")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "服务商接入" in body
    assert "模块模型分配" in body
    assert "通用设置" in body


def test_settings_post_provider_tab_saves_user_keys(authed_client_no_db):
    with patch("web.routes.settings.llm_providers_dao.save_user_provider") as m:
        resp = authed_client_no_db.post("/settings", data={
            "tab": "providers",
            "openrouter_key_value": "new-or-key",
            "openrouter_base_url": "https://openrouter.ai/api/v1",
        }, follow_redirects=False)
    assert resp.status_code in (302, 303)
    m.assert_any_call(1, "openrouter",
                      {"key_value": "new-or-key",
                       "base_url": "https://openrouter.ai/api/v1"})


def test_settings_post_bindings_tab_saves_use_case(authed_client_no_db):
    with patch("web.routes.settings.llm_bindings.upsert") as m:
        resp = authed_client_no_db.post("/settings", data={
            "tab": "bindings",
            "binding_video_score.run_provider": "gemini",
            "binding_video_score.run_model": "gemini-3.1-pro-preview",
        })
    assert resp.status_code in (302, 303)
    m.assert_any_call(
        "video_score.run",
        provider="gemini", model="gemini-3.1-pro-preview",
        updated_by=1,
    )


def test_settings_post_bindings_restore_default_deletes_row(authed_client_no_db):
    with patch("web.routes.settings.llm_bindings.delete") as m:
        resp = authed_client_no_db.post("/settings", data={
            "tab": "bindings",
            "restore_default": "video_score.run",
        })
    assert resp.status_code in (302, 303)
    m.assert_called_once_with("video_score.run")


def test_settings_post_general_tab_saves_jianying(authed_client_no_db):
    with patch("web.routes.settings.set_key") as m:
        resp = authed_client_no_db.post("/settings", data={
            "tab": "general",
            "jianying_project_root": "C:\\\\new\\\\path",
        })
    assert resp.status_code in (302, 303)
    m.assert_any_call(1, "jianying", "", {"project_root": "C:\\new\\path"})
```

- [ ] **Step 16.2: 跑测试确认失败**

```bash
pytest tests/test_settings_routes_new.py -v
```

- [ ] **Step 16.3: 重写 `web/routes/settings.py`**

```python
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from appcore import llm_bindings, llm_providers_dao
from appcore.api_keys import DEFAULT_JIANYING_PROJECT_ROOT, set_key
from appcore.image_translate_settings import (
    CHANNEL_LABELS as IMAGE_TRANSLATE_CHANNEL_LABELS,
    CHANNELS as IMAGE_TRANSLATE_CHANNELS,
    get_channel as get_image_translate_channel,
    set_channel as set_image_translate_channel,
)
from appcore.llm_use_cases import MODULE_LABELS, USE_CASES

bp = Blueprint("settings", __name__)

_ALLOWED_PROVIDERS = ("openrouter", "doubao", "gemini")


@bp.route("/settings", methods=["GET", "POST"])
@login_required
def index():
    if request.method == "POST":
        tab = request.form.get("tab", "providers")
        if tab == "providers":
            _handle_providers_post()
        elif tab == "bindings":
            _handle_bindings_post()
        elif tab == "general":
            _handle_general_post()
        flash("配置已保存")
        return redirect(url_for("settings.index", tab=tab))

    # GET
    user_providers = llm_providers_dao.load_user_providers(current_user.id)
    global_providers = llm_providers_dao.load_global_providers() \
        if getattr(current_user, "role", "") == "admin" else {}
    bindings_rows = llm_bindings.list_all()
    # 按 module 分组
    grouped: dict[str, list] = {}
    for row in bindings_rows:
        grouped.setdefault(row["module"], []).append(row)

    jianying_project_root = _load_jianying_root()
    try:
        current_image_channel = get_image_translate_channel()
    except Exception:
        current_image_channel = "aistudio"

    return render_template(
        "settings.html",
        user_providers_defs=llm_providers_dao.USER_LEVEL_PROVIDERS,
        global_providers_defs=llm_providers_dao.GLOBAL_PROVIDERS,
        user_providers=user_providers,
        global_providers=global_providers,
        is_admin=getattr(current_user, "role", "") == "admin",
        bindings_grouped=grouped,
        module_labels=MODULE_LABELS,
        allowed_providers=_ALLOWED_PROVIDERS,
        jianying_project_root=jianying_project_root,
        default_jianying_project_root=DEFAULT_JIANYING_PROJECT_ROOT,
        image_translate_channel=current_image_channel,
        image_translate_channels=[
            (code, IMAGE_TRANSLATE_CHANNEL_LABELS.get(code, code))
            for code in IMAGE_TRANSLATE_CHANNELS
        ],
    )


def _handle_providers_post() -> None:
    for code, _, fields in llm_providers_dao.USER_LEVEL_PROVIDERS:
        # 只当至少一个字段非空才保存，避免误触清空
        submitted = {fname: request.form.get(f"{code}_{fname}", "")
                     for fname, _, _ in fields}
        if any(v.strip() for v in submitted.values()):
            llm_providers_dao.save_user_provider(current_user.id, code, submitted)

    # 管理员全局 provider
    if getattr(current_user, "role", "") == "admin":
        for code, _, fields in llm_providers_dao.GLOBAL_PROVIDERS:
            submitted = {fname: request.form.get(f"global_{code}_{fname}", "")
                         for fname, _, _ in fields}
            if any(v.strip() for v in submitted.values()):
                llm_providers_dao.save_global_provider(code, submitted)


def _handle_bindings_post() -> None:
    # 单条 "restore_default" 优先
    restore = request.form.get("restore_default", "").strip()
    if restore and restore in USE_CASES:
        llm_bindings.delete(restore)
        return

    for code in USE_CASES:
        provider = request.form.get(f"binding_{code}_provider", "").strip()
        model = request.form.get(f"binding_{code}_model", "").strip()
        if not provider or not model:
            continue
        if provider not in _ALLOWED_PROVIDERS:
            continue
        llm_bindings.upsert(
            code, provider=provider, model=model, updated_by=current_user.id,
        )


def _handle_general_post() -> None:
    jianying_project_root = (request.form.get("jianying_project_root", "").strip()
                              or DEFAULT_JIANYING_PROJECT_ROOT)
    set_key(current_user.id, "jianying", "", {"project_root": jianying_project_root})

    image_channel = request.form.get("image_translate_channel", "").strip().lower()
    if image_channel in IMAGE_TRANSLATE_CHANNELS:
        set_image_translate_channel(image_channel)


def _load_jianying_root() -> str:
    from appcore.api_keys import get_all
    keys = get_all(current_user.id)
    return (keys.get("jianying", {}).get("extra", {}).get("project_root")
            or DEFAULT_JIANYING_PROJECT_ROOT)
```

- [ ] **Step 16.4: 跑路由测试**

```bash
pytest tests/test_settings_routes_new.py -v
```

- [ ] **Step 16.5: Commit**

```bash
git add web/routes/settings.py tests/test_settings_routes_new.py
git commit -m "feat(settings): rewrite route to 3-tab provider/bindings/general structure"
```

---

### Task 17: 新 `web/templates/settings.html`（ocean-blue）

**Files:**
- Modify: `web/templates/settings.html`（整个重写）

- [ ] **Step 17.1: 确认 layout.html 的 token 变量都已就位**

```bash
grep -n "oklch" web/templates/layout.html
```

- [ ] **Step 17.2: 重写模板**

```html
{% extends "layout.html" %}
{% block title %}API 设置 - AutoVideoSrt{% endblock %}
{% block page_title %}API 设置{% endblock %}
{% block extra_style %}
.tabbar { display: flex; gap: var(--space-2); border-bottom: 1px solid var(--border); margin-bottom: var(--space-6); }
.tab-link { padding: var(--space-3) var(--space-4); font-size: var(--text-base); color: var(--fg-muted); border-bottom: 2px solid transparent; cursor: pointer; text-decoration: none; transition: color var(--duration-fast) var(--ease), border-color var(--duration-fast) var(--ease); }
.tab-link:hover { color: var(--fg); }
.tab-link.active { color: var(--accent); border-bottom-color: var(--accent); font-weight: 600; }
.card { background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius-lg); padding: var(--space-6); margin-bottom: var(--space-5); }
.card h3 { margin: 0 0 var(--space-2) 0; font-size: var(--text-md); color: var(--fg); }
.card .muted { color: var(--fg-muted); font-size: var(--text-sm); margin-bottom: var(--space-4); }
label.field-label { display: block; color: var(--fg-muted); font-size: var(--text-xs); font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: var(--space-1); margin-top: var(--space-3); }
input[type=text], input[type=password], select { width: 100%; background: var(--bg); border: 1px solid var(--border-strong); border-radius: var(--radius); color: var(--fg); padding: 0 var(--space-3); height: 32px; font-size: var(--text-base); font-family: inherit; outline: none; transition: border-color var(--duration-fast) var(--ease), box-shadow var(--duration-fast) var(--ease); }
input[type=text]:focus, input[type=password]:focus, select:focus { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent-ring); }
.success { background: var(--success-bg); border: 1px solid var(--success); color: var(--success-fg); font-size: var(--text-sm); padding: var(--space-2) var(--space-3); border-radius: var(--radius-md); margin-bottom: var(--space-4); }
.binding-row { display: grid; grid-template-columns: 1.5fr 1.2fr 1.5fr auto; gap: var(--space-3); align-items: end; padding: var(--space-3) 0; border-top: 1px dashed var(--border); }
.binding-row:first-child { border-top: none; }
.binding-label { color: var(--fg); font-size: var(--text-base); font-weight: 500; }
.binding-desc { color: var(--fg-subtle); font-size: var(--text-xs); }
.custom-tag { display: inline-block; background: var(--accent-subtle); color: var(--accent); font-size: var(--text-xs); padding: 2px var(--space-2); border-radius: var(--radius-md); margin-left: var(--space-2); }
.section-title { font-size: var(--text-sm); color: var(--fg-muted); font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; margin: var(--space-5) 0 var(--space-2); }
{% endblock %}
{% block content %}
{% set active_tab = request.args.get("tab") or "providers" %}

<div class="tabbar">
  <a class="tab-link {{ 'active' if active_tab == 'providers' }}" href="{{ url_for('settings.index', tab='providers') }}">服务商接入</a>
  <a class="tab-link {{ 'active' if active_tab == 'bindings' }}"  href="{{ url_for('settings.index', tab='bindings') }}">模块模型分配</a>
  <a class="tab-link {{ 'active' if active_tab == 'general' }}"   href="{{ url_for('settings.index', tab='general') }}">通用设置</a>
</div>

{% with messages = get_flashed_messages() %}
  {% if messages %}<div class="success">{{ messages[0] }}</div>{% endif %}
{% endwith %}

{# ─── Tab 1: Providers ─── #}
{% if active_tab == 'providers' %}
<form method="post">
  <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
  <input type="hidden" name="tab" value="providers">

  <div class="section-title">用户级凭证（每人独立）</div>
  {% for code, label, fields in user_providers_defs %}
  <div class="card">
    <h3>{{ label }}</h3>
    <p class="muted">代号 <code>{{ code }}</code></p>
    {% for fname, flabel, ftype in fields %}
    <label class="field-label">{{ flabel }}</label>
    <input type="{{ ftype }}" name="{{ code }}_{{ fname }}"
           placeholder="留空则保持当前值"
           value="{{ user_providers.get(code, {}).get(fname, '') }}">
    {% endfor %}
  </div>
  {% endfor %}

  {% if is_admin %}
  <div class="section-title">全局凭证（仅管理员可改）</div>
  {% for code, label, fields in global_providers_defs %}
  <div class="card">
    <h3>{{ label }}</h3>
    {% for fname, flabel, ftype in fields %}
    <label class="field-label">{{ flabel }}</label>
    <input type="{{ ftype }}" name="global_{{ code }}_{{ fname }}"
           placeholder="留空则保持当前值"
           value="{{ global_providers.get(code, {}).get(fname, '') }}">
    {% endfor %}
  </div>
  {% endfor %}
  {% endif %}

  <button class="btn btn-primary" type="submit">保存服务商配置</button>
</form>
{% endif %}

{# ─── Tab 2: Module × Model Bindings ─── #}
{% if active_tab == 'bindings' %}
<form method="post">
  <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
  <input type="hidden" name="tab" value="bindings">

  <p class="muted" style="margin-bottom:var(--space-5)">
    每个业务功能可指定使用哪个供应商和模型。留空保持当前。
    想恢复默认，点击行尾「恢复默认」按钮。
  </p>

  {% for module, label in module_labels.items() %}
    {% if bindings_grouped.get(module) %}
    <div class="card">
      <h3>{{ label }}</h3>
      {% for row in bindings_grouped[module] %}
      <div class="binding-row">
        <div>
          <div class="binding-label">{{ row.label }}
            {% if row.is_custom %}<span class="custom-tag">已自定义</span>{% endif %}
          </div>
          <div class="binding-desc">{{ row.description }}</div>
          <div class="binding-desc"><code>{{ row.code }}</code></div>
        </div>
        <div>
          <label class="field-label">Provider</label>
          <select name="binding_{{ row.code }}_provider">
            {% for p in allowed_providers %}
            <option value="{{ p }}" {{ 'selected' if row.provider == p }}>{{ p }}</option>
            {% endfor %}
          </select>
        </div>
        <div>
          <label class="field-label">Model ID</label>
          <input type="text" name="binding_{{ row.code }}_model" value="{{ row.model }}">
        </div>
        <div>
          <button class="btn btn-secondary" type="submit" name="restore_default" value="{{ row.code }}"
                  {% if not row.is_custom %}disabled{% endif %}>
            恢复默认
          </button>
        </div>
      </div>
      {% endfor %}
    </div>
    {% endif %}
  {% endfor %}

  <button class="btn btn-primary" type="submit">保存模型绑定</button>
</form>
{% endif %}

{# ─── Tab 3: General ─── #}
{% if active_tab == 'general' %}
<form method="post">
  <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
  <input type="hidden" name="tab" value="general">

  <div class="card">
    <h3>剪映项目根目录</h3>
    <p class="muted">CapCut 导出时素材路径会指向这个本机目录</p>
    <label class="field-label">路径</label>
    <input type="text" name="jianying_project_root" value="{{ jianying_project_root }}">
    <p class="muted" style="margin-top:var(--space-2)">默认：{{ default_jianying_project_root }}</p>
  </div>

  <div class="card">
    <h3>图片翻译通道</h3>
    <p class="muted">图片翻译走哪个 Gemini 通道</p>
    <label class="field-label">通道</label>
    <select name="image_translate_channel">
      {% for code, label in image_translate_channels %}
      <option value="{{ code }}" {{ 'selected' if image_translate_channel == code }}>{{ label }}</option>
      {% endfor %}
    </select>
  </div>

  <button class="btn btn-primary" type="submit">保存通用设置</button>
</form>
{% endif %}
{% endblock %}
```

- [ ] **Step 17.3: 启动 dev server 手工过一遍每个 Tab**

```bash
python main.py
# 浏览器打开 http://127.0.0.1:5000/settings
# - Tab 1 Providers: 填 openrouter key，保存 → 再进来应该显示保留值
# - Tab 2 Bindings: 改一个 binding，保存 → 显示「已自定义」tag + 恢复默认按钮激活
# - Tab 3 General: 改剪映目录，保存 → 再进来应该显示保留值
```

- [ ] **Step 17.4: 跑全量测试**

```bash
pytest tests/ -q
```

- [ ] **Step 17.5: Commit**

```bash
git add web/templates/settings.html
git commit -m "feat(settings): rewrite template with ocean-blue tokens and 3-tab layout"
```

---

### Task 18: 最终回归 + 文档

**Files:**
- Modify: `CLAUDE.md`（增补"LLM 统一调用"章节）
- Modify: `AGENTS.md`（同上）

- [ ] **Step 18.1: 跑全量测试（强制 0 regression）**

```bash
pytest tests/ -q 2>&1 | tail -20
```
预期: `x passed in Xs` / 0 failed。

- [ ] **Step 18.2: 在 `CLAUDE.md` 末尾加一节**

```markdown
## LLM 统一调用（2026-04-19 重构）

所有新代码调用 LLM 时一律走 `appcore.llm_client`，不要直接 `from openai import OpenAI` 或 `from appcore import gemini`：

```python
from appcore import llm_client

# Chat 风格（翻译、文案）
result = llm_client.invoke_chat(
    "video_translate.localize",
    messages=[{"role": "system", ...}, {"role": "user", ...}],
    user_id=42, project_id="task-xxx",
    temperature=0.2, max_tokens=4096,
    response_format={"type": "json_schema", ...},  # OpenRouter-only
)

# Generate 风格（视频 / 图片多模态、结构化 JSON）
result = llm_client.invoke_generate(
    "video_score.run",
    prompt="score this", media=[video_path],
    user_id=42, project_id="task-xxx",
    response_schema={...}, temperature=0.2,
)
```

新增 use_case 时，在 [appcore/llm_use_cases.py](appcore/llm_use_cases.py) 注册条目，管理员可以在 `/settings` 第二 Tab 修改 provider + model 绑定。
```

- [ ] **Step 18.3: 在 `AGENTS.md` 同步一份**

- [ ] **Step 18.4: Commit**

```bash
git add CLAUDE.md AGENTS.md
git commit -m "docs: document unified llm_client invocation pattern"
```

- [ ] **Step 18.5: 打 tag**

```bash
git log --oneline -30  # 确认所有提交到位
```

---

## 回滚策略

若 Phase 3 某个模块切换后跑 runtime 发现线上行为异常：
1. 找对应模块的 commit（如 `refactor(video_score): route through use_case binding`）
2. `git revert <commit-hash>`
3. 该模块退回老调用路径
4. Phase 2 的 translate.py / gemini.py 加了 `_binding_lookup` 但当 DB 表为空时与老代码行为完全一致，所以保留即可，不需回滚

全局回滚（极端情况）：直接 `git revert` 到 Phase 1 Task 1 之前即可，DB 表 `llm_use_case_bindings` 保留也无影响（没代码在读）。

---

## 自检清单（实施完成后对照）

- [ ] `pytest tests/ -q` 全绿
- [ ] `/settings` GET 返回 200，三 Tab 都能打开
- [ ] 每个 Tab POST 保存后字段保持（再次 GET 能看到值）
- [ ] 管理员能看到 Tab 1 全局 provider 部分；普通用户看不到
- [ ] Tab 2 修改一个 binding 保存后，「已自定义」tag 出现；点「恢复默认」删除该 binding 记录
- [ ] 跑一次视频翻译任务，监控日志确认 `usage_logs` 的 `service` 字段和 `extra_data.use_case` 都符合预期
- [ ] 跑一次视频评分（Tab 2 修改 `video_score.run` 的 model 后），日志确认调用了新模型
- [ ] `grep -r "gemini-3.1-pro-preview" pipeline/ appcore/` 仍会出现在 `USE_CASES` 默认值里（正确），但不在直接调用 `service=...default_model=...` 的 kwargs 里

---

# 2026-04-19 漂移修正记录

**同步时间**：2026-04-19，origin/master HEAD = `4f4a2e7 merge: link check binary review flow`。

plan 编写后，从主线同步时发现以下 commit 已经事实引入了"先选供应商再选模型"的一部分：
- `57b503a feat(translate): 视频翻译接入 Vertex AI — 先选供应商再选模型，默认 Vertex Gemini 3.1 Flash-Lite (#57)`
- `ee90531 feat(duration-loop): 头部展示翻译所用的模型 + 渠道 tag`
- `277d7f5 feat(translate-loop): 翻译本土化与时长迭代链路可视化全面升级`

**未改动**的关键文件（Phase 3 的 7 个硬编码迁移 Task 仍然按原样执行）：
`appcore/gemini.py`、`appcore/api_keys.py`、`config.py`、`db/schema.sql`、
`pipeline/video_score.py / shot_decompose.py / video_review.py / copywriting.py / text_translate.py / gemini_image.py`、`appcore/link_check_gemini.py`。

**已改动**的文件（影响 Phase 1/2/4，按下述覆盖项调整）：
`pipeline/translate.py`（+318/-107，重构为 `_call_openai_compat` + `_call_vertex_json` 两分支）、
`web/routes/settings.py`（+10 行，加 3 个 `vertex_*` provider）、
`web/templates/settings.html`（+57 行，双层下拉 JS）。

---

## 覆盖 1 · 核心设计决策第 5 条

**原文（前文「核心设计决策」第 5 条）**：  
"Gemini AIStudio vs Vertex backend 本期不拆：仍用全局 GEMINI_BACKEND env。标注二期"

**改为**：本期把 Gemini 拆成 **两个独立 `provider_code`**：
- `gemini_aistudio`：`genai.Client(api_key=GEMINI_API_KEY)`，用户级 key 存 `api_keys.service='gemini'`（保持老字段名）
- `gemini_vertex`：`genai.Client(vertexai=True, api_key=GEMINI_CLOUD_API_KEY)`，用户级 key 存 `api_keys.service='gemini_cloud'`（新字段）

老接口 `generate_localized_translation(provider="vertex_gemini_31_pro")` 完全保留；主线的 `_call_vertex_json` 和 `_VERTEX_PREF_MODELS` **不动**。新接口 `llm_client.invoke_chat()` 通过 bindings resolve 到 `(gemini_vertex, 具体 model)`，内部再映射为主线的 `vertex_*` provider 名调老函数。

---

## 覆盖 2 · PROVIDER_ADAPTERS 由 3 → 4 个

**替代**前文 Task 4 Step 4.4 的 `__init__.py`：

```python
# appcore/llm_providers/__init__.py
from appcore.llm_providers.base import LLMAdapter
from appcore.llm_providers.gemini_aistudio_adapter import GeminiAIStudioAdapter
from appcore.llm_providers.gemini_vertex_adapter import GeminiVertexAdapter
from appcore.llm_providers.openrouter_adapter import DoubaoAdapter, OpenRouterAdapter

PROVIDER_ADAPTERS: dict[str, LLMAdapter] = {
    "openrouter": OpenRouterAdapter(),
    "doubao": DoubaoAdapter(),
    "gemini_aistudio": GeminiAIStudioAdapter(),
    "gemini_vertex": GeminiVertexAdapter(),
}


def get_adapter(provider_code: str) -> LLMAdapter:
    if provider_code not in PROVIDER_ADAPTERS:
        raise KeyError(f"unknown provider: {provider_code}")
    return PROVIDER_ADAPTERS[provider_code]
```

Task 4 的文件结构变为：
```
appcore/llm_providers/
  __init__.py
  base.py
  openrouter_adapter.py            # 含 OpenRouterAdapter + DoubaoAdapter（同前）
  gemini_aistudio_adapter.py       # 替代原 gemini_adapter.py
  gemini_vertex_adapter.py         # 新增
```

---

## 覆盖 3 · `gemini_aistudio_adapter.py`（替代 Task 4 Step 4.3 的原 `gemini_adapter.py`）

```python
"""AI Studio Gemini 适配器，复用 appcore.gemini（当 GEMINI_BACKEND=aistudio）。"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from appcore import gemini as gemini_api
from appcore.llm_providers.base import LLMAdapter


class GeminiAIStudioAdapter(LLMAdapter):
    provider_code = "gemini_aistudio"

    def resolve_credentials(self, user_id):
        key, _ = gemini_api.resolve_config(user_id=user_id)
        return {"api_key": key or "", "base_url": None, "extra": {}}

    def generate(self, *, model, prompt, user_id=None, system=None,
                 media=None, response_schema=None, temperature=None,
                 max_output_tokens=None):
        media_list = None
        if media:
            media_list = [media] if isinstance(media, (str, Path)) else list(media)
        result = gemini_api.generate(
            prompt, system=system, model=model, media=media_list,
            response_schema=response_schema, temperature=temperature,
            max_output_tokens=max_output_tokens, user_id=user_id,
            service="gemini", default_model=model,
        )
        return {
            "text": result if isinstance(result, str) else None,
            "json": result if not isinstance(result, str) else None,
            "raw": None,
            "usage": {"input_tokens": None, "output_tokens": None},
        }

    def chat(self, *, model, messages, user_id=None, temperature=None,
             max_tokens=None, response_format=None, extra_body=None):
        system, user_parts = None, []
        for m in messages:
            role, content = m.get("role"), m.get("content", "")
            if isinstance(content, list):
                content = "\n".join(p.get("text", "") for p in content if isinstance(p, dict))
            if role == "system":
                system = (system + "\n\n" + content) if system else content
            else:
                user_parts.append(str(content))
        schema = (response_format or {}).get("json_schema", {}).get("schema") \
                 if response_format and response_format.get("type") == "json_schema" else None
        return self.generate(
            model=model, prompt="\n\n".join(user_parts), user_id=user_id,
            system=system, response_schema=schema,
            temperature=temperature, max_output_tokens=max_tokens,
        )
```

对应测试 `tests/test_llm_providers_gemini.py` 里所有 `from appcore.llm_providers.gemini_adapter import GeminiAdapter` 改为 `from appcore.llm_providers.gemini_aistudio_adapter import GeminiAIStudioAdapter`，函数名 `test_gemini_*` 保留即可。

---

## 覆盖 4 · `gemini_vertex_adapter.py`（新增，Task 4 新 Step 4.3b）

```python
"""Vertex AI（Google Cloud Express Mode）适配器。

复用主线 pipeline/translate._call_vertex_json 的底层实现以保证行为一致——
将来可把该函数抽象到本模块，本期保持循环引用最少。
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from appcore.api_keys import resolve_key
from appcore.llm_providers.base import LLMAdapter
from config import GEMINI_CLOUD_API_KEY


class GeminiVertexAdapter(LLMAdapter):
    provider_code = "gemini_vertex"

    def resolve_credentials(self, user_id):
        key = (resolve_key(user_id, "gemini_cloud", "GEMINI_CLOUD_API_KEY")
               if user_id is not None else GEMINI_CLOUD_API_KEY)
        return {"api_key": key or "", "base_url": None, "extra": {}}

    def _call(self, *, model, messages, response_format, temperature, max_output_tokens):
        # 函数内 import 防止导入时循环
        from pipeline.translate import _call_vertex_json
        return _call_vertex_json(
            messages, model, response_format,
            temperature=temperature if temperature is not None else 0.2,
            max_output_tokens=max_output_tokens or 4096,
        )

    def chat(self, *, model, messages, user_id=None, temperature=None,
             max_tokens=None, response_format=None, extra_body=None):
        payload, usage, raw = self._call(
            model=model, messages=messages,
            response_format=response_format,
            temperature=temperature, max_output_tokens=max_tokens,
        )
        import json as _json
        text_out = raw if isinstance(raw, str) else _json.dumps(payload, ensure_ascii=False)
        return {
            "text": text_out,
            "json": payload if not isinstance(payload, str) else None,
            "raw": raw,
            "usage": usage or {"input_tokens": None, "output_tokens": None},
        }

    def generate(self, *, model, prompt, user_id=None, system=None,
                 media=None, response_schema=None, temperature=None,
                 max_output_tokens=None):
        if media:
            raise NotImplementedError(
                "GeminiVertexAdapter 本期不支持多模态 media，请改用 gemini_aistudio 或 appcore.gemini_image"
            )
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        response_format = None
        if response_schema:
            response_format = {
                "type": "json_schema",
                "json_schema": {"name": "vx", "schema": response_schema},
            }
        return self.chat(
            model=model, messages=messages, user_id=user_id,
            temperature=temperature, max_tokens=max_output_tokens,
            response_format=response_format,
        )
```

新增测试 `tests/test_llm_providers_gemini_vertex.py`：

```python
from unittest.mock import patch

from appcore.llm_providers.gemini_vertex_adapter import GeminiVertexAdapter


def test_vertex_chat_delegates_to_translate_vertex_call():
    adapter = GeminiVertexAdapter()
    with patch("pipeline.translate._call_vertex_json",
               return_value=({"ok": True}, {"input_tokens": 5, "output_tokens": 3}, '{"ok":true}')):
        result = adapter.chat(
            model="gemini-3.1-flash-lite-preview",
            messages=[{"role": "user", "content": "hi"}],
        )
    assert result["json"] == {"ok": True}
    assert result["usage"] == {"input_tokens": 5, "output_tokens": 3}


def test_vertex_generate_rejects_media():
    import pytest
    adapter = GeminiVertexAdapter()
    with pytest.raises(NotImplementedError):
        adapter.generate(model="x", prompt="y", media=["/some/path"])
```

---

## 覆盖 5 · USE_CASES 默认值（Task 2 Step 2.3 里的 13 项）

视频翻译三项从 `openrouter` 改为 `gemini_vertex`，与主线 `DEFAULT_TRANSLATE_PROVIDER="vertex_gemini_31_flash_lite"` 对齐：

```python
"video_translate.localize": _uc(
    "video_translate.localize", "video_translate", "本土化改写",
    "视频翻译主流程中把中文转成目标语言本土化文本",
    "gemini_vertex", "gemini-3.1-flash-lite-preview", "gemini_vertex",
),
"video_translate.tts_script": _uc(
    "video_translate.tts_script", "video_translate", "TTS 脚本生成",
    "根据本土化文本切分成适合配音的 TTS 脚本段落",
    "gemini_vertex", "gemini-3.1-flash-lite-preview", "gemini_vertex",
),
"video_translate.rewrite": _uc(
    "video_translate.rewrite", "video_translate", "字数收敛重写",
    "TTS 时长不达标时回卷到文案重写的内循环",
    "gemini_vertex", "gemini-3.1-flash-lite-preview", "gemini_vertex",
),
```

Gemini 族的其他 use_case（`video_score.run / video_review.analyze / shot_decompose.run / image_translate.detect / image_translate.generate / link_check.analyze`）默认 provider 从 `gemini` 改为 `gemini_aistudio`，`usage_log_service` 同步改：
- `video_score.run` / `video_review.analyze` / `shot_decompose.run`：`usage_log_service="gemini_video_analysis"`（保留主线语义）
- `image_translate.*` / `link_check.analyze`：`usage_log_service="gemini"`

`text_translate.generate` 保持 `openrouter`，因为主线没改 `pipeline/text_translate.py`。

对应的测试断言（Task 2 Step 2.1）放宽：
```python
assert uc["default_provider"] in {"openrouter", "doubao", "gemini_aistudio", "gemini_vertex"}
```

---

## 覆盖 6 · `_ALLOWED_PROVIDERS`（Task 5 llm_client + Task 16 settings route）

```python
_ALLOWED_PROVIDERS = ("openrouter", "doubao", "gemini_aistudio", "gemini_vertex")
```

Task 16 Step 16.1 里的测试 `test_settings_post_bindings_tab_saves_use_case`：

```python
resp = authed_client_no_db.post("/settings", data={
    "tab": "bindings",
    "binding_video_score.run_provider": "gemini_aistudio",   # ← 改
    "binding_video_score.run_model": "gemini-3.1-pro-preview",
})
```

---

## 覆盖 7 · Task 6 Step 6.3 完全重写

**主线已有的 `resolve_provider_config` 内部逻辑保持不变**（Vertex 不走这里已经写在主线函数 docstring 里），不要再在它里面加 `_binding_lookup`。真正的前置查询放在三个业务函数入口：

```python
# pipeline/translate.py 新增工具（放在 _OPENROUTER_PREF_MODELS 后）

def _binding_lookup_for_use_case(code: str) -> dict | None:
    """若入参是 use_case_code（含 '.'），查 bindings；否则 None。"""
    if not isinstance(code, str) or "." not in code:
        return None
    try:
        from appcore import llm_bindings
        return llm_bindings.resolve(code)
    except KeyError:
        return None


def _resolve_use_case_provider(provider_arg: str) -> str:
    """入口映射：use_case code → 老式 provider 字符串。

    如果 binding.provider == gemini_vertex：
        - 查 _VERTEX_PREF_MODELS 反向表命中 → 返 vertex_*
        - 未命中 → 动态写入 _VERTEX_PREF_MODELS[vertex_custom] = binding.model，返 "vertex_custom"
    如果 binding.provider == gemini_aistudio：
        - translate.py 不直接走 AIStudio（主线也没这分支），转为 "openrouter" + 把 model 存到
          _OPENROUTER_PREF_MODELS 里的临时 key。实际这种场景应当由调用方避免——
          管理员不该把 video_translate.* 绑到 gemini_aistudio。此处做一个 best-effort。
    其他情况：直接返 binding.provider（openrouter / doubao）。
    """
    binding = _binding_lookup_for_use_case(provider_arg)
    if not binding:
        return provider_arg

    p = binding["provider"]
    m = binding["model"]
    if p == "gemini_vertex":
        reverse = {v: k for k, v in _VERTEX_PREF_MODELS.items()}
        if m in reverse:
            return reverse[m]
        # 动态新增一个临时 provider 键
        _VERTEX_PREF_MODELS["vertex_custom"] = m
        return "vertex_custom"
    if p == "gemini_aistudio":
        # translate.py 内没 AIStudio 分支；回退到 openrouter + 同名 gemini 模型
        _OPENROUTER_PREF_MODELS["_gemini_aistudio_fallback"] = f"google/{m}"
        return "_gemini_aistudio_fallback"
    return p  # openrouter / doubao
```

**三个业务函数入口加 2 行**：

```python
def generate_localized_translation(
    source_full_text_zh, script_segments, variant="normal",
    custom_system_prompt=None, *,
    provider="openrouter", user_id=None, openrouter_api_key=None,
) -> dict:
    provider = _resolve_use_case_provider(provider)  # ← 新增
    messages = build_localized_translation_messages(...)
    # 以下逻辑一字不改
```

`generate_tts_script` / `generate_localized_rewrite` 同处理。

**新测试 `tests/test_translate_use_case_binding.py`**：

```python
from unittest.mock import patch

from pipeline.translate import _resolve_use_case_provider


def test_resolves_use_case_to_vertex_provider():
    with patch("pipeline.translate._binding_lookup_for_use_case",
               return_value={"provider": "gemini_vertex",
                             "model": "gemini-3.1-pro-preview",
                             "extra": {}, "source": "db"}):
        p = _resolve_use_case_provider("video_translate.localize")
    assert p == "vertex_gemini_31_pro"


def test_resolves_use_case_custom_vertex_model():
    from pipeline import translate
    with patch("pipeline.translate._binding_lookup_for_use_case",
               return_value={"provider": "gemini_vertex",
                             "model": "gemini-experimental-xyz",
                             "extra": {}, "source": "db"}):
        p = _resolve_use_case_provider("video_translate.localize")
    assert p == "vertex_custom"
    assert translate._VERTEX_PREF_MODELS["vertex_custom"] == "gemini-experimental-xyz"


def test_non_use_case_passthrough():
    p = _resolve_use_case_provider("openrouter")
    assert p == "openrouter"
    p = _resolve_use_case_provider("vertex_gemini_31_flash_lite")
    assert p == "vertex_gemini_31_flash_lite"
```

---

## 覆盖 8 · Task 15 `USER_LEVEL_PROVIDERS` 增加 `gemini_cloud`

```python
USER_LEVEL_PROVIDERS = [
    ("openrouter", "OpenRouter", [
        ("key_value", "API Key", "password"),
        ("base_url", "Base URL", "text"),
    ]),
    ("doubao_llm", "豆包 ARK", [
        ("key_value", "API Key", "password"),
        ("base_url", "Base URL", "text"),
    ]),
    ("gemini", "Google Gemini (AI Studio)", [
        ("key_value", "API Key", "password"),
    ]),
    ("gemini_cloud", "Google Gemini (Vertex Express)", [
        ("key_value", "API Key", "password"),
    ]),
    ("elevenlabs", "ElevenLabs", [
        ("key_value", "API Key", "password"),
    ]),
]
```

---

## 覆盖 9 · Task 17 模板 Tab 2 的 provider 下拉改为"分组 + 模型"两级

Tab 2 "模块模型分配" 的每行 provider 选择，借鉴主线 [web/templates/settings.html:82-130](web/templates/settings.html#L82-L130) 的 `translate_provider_group` + `data-group` + JS 过滤方案。具体：

```html
<div class="binding-row">
  <!-- 业务标签、描述列保持原样 -->
  ...
  <div>
    <label class="field-label">Provider Group</label>
    <select class="binding-group-select" data-binding="{{ row.code }}">
      <option value="openrouter"       {{ 'selected' if row.provider == 'openrouter' }}>OpenRouter</option>
      <option value="doubao"           {{ 'selected' if row.provider == 'doubao' }}>豆包 ARK</option>
      <option value="gemini_aistudio"  {{ 'selected' if row.provider == 'gemini_aistudio' }}>Gemini (AI Studio)</option>
      <option value="gemini_vertex"    {{ 'selected' if row.provider == 'gemini_vertex' }}>Gemini (Vertex Express)</option>
    </select>
    <!-- 真正提交的 provider 值由 JS 写入这个 hidden 保持一致 -->
    <input type="hidden" name="binding_{{ row.code }}_provider" value="{{ row.provider }}">
  </div>
  <div>
    <label class="field-label">Model ID</label>
    <input type="text" name="binding_{{ row.code }}_model" value="{{ row.model }}"
           list="models-{{ row.code }}">
    <datalist id="models-{{ row.code }}">
      <!-- JS 根据 group 填充 -->
    </datalist>
  </div>
  ...
</div>
```

模型候选字典（放页面尾部 JSON `<script>`）：
```js
const PROVIDER_MODELS = {
  "openrouter": [
    "google/gemini-3.1-flash-lite-preview",
    "google/gemini-3.1-pro-preview",
    "google/gemini-3-flash-preview",
    "anthropic/claude-sonnet-4.6",
    "openai/gpt-4o-mini",
  ],
  "doubao": ["doubao-seed-2-0-pro-260215"],
  "gemini_aistudio": [
    "gemini-3.1-flash-lite-preview",
    "gemini-3.1-pro-preview",
    "gemini-2.5-flash",
  ],
  "gemini_vertex": [
    "gemini-3.1-flash-lite-preview",
    "gemini-3.1-pro-preview",
    "gemini-3-flash-preview",
  ],
};
```

JS 逻辑：下拉 change → 更新同行 datalist 选项 + hidden input。用户也可以在 input 里手输自定义 model_id。

---

## 勘误表应用方法（给执行者）

每完成 Task N 的 Step 代码编辑，**先**检查本勘误表里有没有对应覆盖：

| Task | 受影响的 Step | 勘误表条目 |
|------|--------------|-----------|
| Task 2（USE_CASES） | Step 2.3 的 13 条 `_uc(...)` 定义 | 覆盖 5 |
| Task 4（Adapter） | Step 4.3 / 4.4 / 4.6 | 覆盖 2、3、4 |
| Task 5（llm_client） | Step 5.3 `_ALLOWED_PROVIDERS` | 覆盖 6 |
| Task 6（translate.py） | Step 6.1-6.4 | 覆盖 7（取代原步骤） |
| Task 15（providers_dao） | Step 15.1 `USER_LEVEL_PROVIDERS` | 覆盖 8 |
| Task 16（settings route） | Step 16.1 测试 + Step 16.3 `_ALLOWED_PROVIDERS` | 覆盖 6 |
| Task 17（settings template） | Step 17.2 模板 Tab 2 | 覆盖 9 |

其他未列出 Task（1/3/7-14/18）**不受漂移影响，按原文执行**。
