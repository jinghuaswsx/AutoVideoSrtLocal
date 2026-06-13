# Block 5 — 质量评估闭环升级 实施计划

> **For agentic workers:** 按 Task 顺序 TDD 执行。Spec：[specs/2026-06-12-omni-quality-block5-eval-loop-design.md](../specs/2026-06-12-omni-quality-block5-eval-loop-design.md)；红线：[specs/2026-06-12-omni-quality-overview.md](../specs/2026-06-12-omni-quality-overview.md)。

**Goal:** 裁判升级 gemini-3.5-flash；评估新增 hook_strength / ending_integrity 维度（联动 Block 3 的截断告警）；低分标红；后台近 30 天聚合视图。

**Architecture:** `pipeline/translation_quality.py`（prompt + schema）、`appcore/quality_assessment.py`（输入增强 + 聚合查询）、`appcore/llm_use_cases.py` + `config.py`（绑定与阈值）、详情/列表模板与一个 admin 区块。

**分支**: 从 `origin/audit/video-translate-quality`（或已合 master）切出 `fix/omni-quality-block5-eval`。建议在 Block 3 合并后实施（`quality_warnings` 联动）；若 Block 3 未合，Task 3 的告警联动步骤照写（字段缺省即跳过，天然兼容）。

---

### Task 1: 裁判绑定 + 阈值配置

**Files:**
- Modify: `appcore/llm_use_cases.py`、`appcore/quality_assessment.py`（`_DEFAULT_MODEL`）、`config.py`
- Test: `tests/test_llm_use_cases_registry.py` 追加

- [ ] **Step 1**: 测试先行：

```python
def test_translation_quality_judge_upgraded():
    from appcore.llm_use_cases import get_use_case
    uc = get_use_case("translation_quality.assess")
    assert uc["default_provider"] == "openrouter"
    assert uc["default_model"] == "google/gemini-3.5-flash"
```

- [ ] **Step 2**: 实现：`translation_quality.assess` 默认 provider/model 改 `"openrouter"` / `"google/gemini-3.5-flash"`（计费通道字段同步 `"openrouter"`）；`quality_assessment.py` 顶部 `_DEFAULT_MODEL = "gemini-3.5-flash"`；`config.py` 追加：

```python
# Block5: 翻译质量评估阈值（docs/superpowers/specs/2026-06-12-omni-quality-block5-eval-loop-design.md）
TRANSLATION_QUALITY_RED_SCORE = 70
TRANSLATION_QUALITY_ENDING_RED = 60
```

- [ ] **Step 3**: 跑 PASS（注意更新 registry 测试里断言旧值的用例）→ `git commit -am "feat(block5): upgrade quality judge to gemini-3.5-flash; red thresholds"`

### Task 2: 新评估维度

**Files:**
- Modify: `pipeline/translation_quality.py`
- Test: `tests/test_translation_quality_schema.py`（新或追加现有）

- [ ] **Step 1: 写失败测试**（mock invoke_chat 返回含新字段的响应 → assess 成功且结果透出两个新分；缺 `ending_integrity` → 抛 `AssessmentResponseInvalidError`）：

```python
import json
from unittest.mock import patch
import pytest
from pipeline.translation_quality import assess, AssessmentResponseInvalidError

FULL = {
    "translation_dimensions": {"semantic_fidelity": 90, "completeness": 88,
                               "naturalness": 85, "hook_strength": 80,
                               "ending_integrity": 75},
    "tts_dimensions": {"text_recall": 95, "pronunciation_fidelity": 90, "rhythm_match": 88},
    "translation_score": 86, "tts_score": 91,
    "verdict": "pass", "verdict_reason": "整体良好",
    "translation_issues": [], "translation_highlights": [],
    "tts_issues": [], "tts_highlights": [],
}
KW = dict(original_asr="src", translation="dst", tts_recognition="dst2",
          source_language="zh", target_language="en", task_id="t", user_id=1)


def _resp(payload):
    return {"text": json.dumps(payload), "usage": {}}


def test_new_dimensions_parsed():
    with patch("pipeline.translation_quality.llm_client") as m:
        m.invoke_chat.return_value = _resp(FULL)
        r = assess(**KW)
    assert r["translation_dimensions"]["hook_strength"] == 80
    assert r["translation_dimensions"]["ending_integrity"] == 75


def test_missing_new_dimension_rejected():
    bad = json.loads(json.dumps(FULL))
    del bad["translation_dimensions"]["ending_integrity"]
    with patch("pipeline.translation_quality.llm_client") as m:
        m.invoke_chat.return_value = _resp(bad)
        with pytest.raises(AssessmentResponseInvalidError):
            assess(**KW)
```

（先读 `pipeline/translation_quality.py` 现有 `assess` 签名与校验方式，测试细节对齐现状；上面为意图样板。）
- [ ] **Step 2: 实现**：system prompt 的 `[TRANSLATION_SCORE]` 维度清单追加两行（Spec R2 文案）；`_response_format` 的 `translation_dimensions.properties` 加 `hook_strength` / `ending_integrity`（integer 0-100）并加入 `required`；响应校验逻辑同步（若有手写字段检查清单则补全）。确认 `translation_score` 总分来源：模型直出则不动；代码均值则把新维度纳入（commit message 注明结论）。
- [ ] **Step 3: 截断告警联动**：`appcore/quality_assessment.py::_build_inputs` 返回 dict 增加 `notes`：task 的 `quality_warnings` 里存在 `type=="tail_truncated"` 时生成 `f"NOTE: the final audio was tail-truncated, {removed_count} sentences removed before export."`，否则空串；`_run_assessment_job` 调 `assess` 时透传（`assess` 加可选参数 `notes: str = ""`，非空时拼到 user 消息末尾）。
- [ ] **Step 4**: 跑 PASS → `git commit -am "feat(block5): hook_strength + ending_integrity dimensions with truncation note"`

### Task 3: 低分标红

**Files:**
- Modify: 详情页评估卡模板/JS（`grep -rn "translation_score\|quality_assessment" web/templates web/static` 定位现有渲染处）
- Modify: 列表接口/模板（omni / omni_v2 列表）
- Test: 后端阈值 helper 单测

- [ ] **Step 1**: 后端 helper（放 `appcore/quality_assessment.py`）：

```python
def is_red_assessment(row: dict) -> bool:
    import config
    try:
        if int(row.get("translation_score") or 100) < int(getattr(config, "TRANSLATION_QUALITY_RED_SCORE", 70)):
            return True
        dims = row.get("translation_dimensions")
        dims = json.loads(dims) if isinstance(dims, str) else (dims or {})
        return int(dims.get("ending_integrity") or 100) < int(getattr(config, "TRANSLATION_QUALITY_ENDING_RED", 60))
    except (TypeError, ValueError):
        return False
```

单测三态（总分触红 / ending 触红 / 不触红）。
- [ ] **Step 2**: 详情页：评估卡渲染处对触红分项加 `style` 红色 + 「⚠️ 低于质量线」文案（跟随现有评估卡的实现方式，前端拿到 `is_red` 字段或前端用同阈值判断——选后端下发 `is_red`，阈值只在后端一处）。
- [ ] **Step 3**: 列表页：调研列表 API 是否已带评估分（`grep -rn "translation_quality_assessments" web/`）；已带 → 模板加红 badge；未带 → 列表查询 LEFT JOIN 最新 done 评估行的 score + is_red，行内红点 + title 提示。**不做大改版**。
- [ ] **Step 4**: 跑相关 pytest + 手动 `python -m web.app` 起服务看列表/详情渲染 → `git commit -am "feat(block5): red-flag low quality assessments in detail and list views"`

### Task 4: 聚合视图

**Files:**
- Modify: `appcore/quality_assessment.py`（`summarize_recent`）
- Modify/Create: admin route + 模板区块（跟随 `admin_ai_billing` 模式）
- Test: `tests/test_quality_summary.py`

- [ ] **Step 1**: `summarize_recent(days: int = 30) -> list[dict]`：

```python
def summarize_recent(days: int = 30) -> list[dict]:
    return db_query(
        "SELECT project_type, COUNT(*) AS n, "
        "       AVG(translation_score) AS avg_translation, "
        "       AVG(tts_score) AS avg_tts "
        "FROM translation_quality_assessments "
        "WHERE status='done' AND completed_at >= DATE_SUB(NOW(), INTERVAL %s DAY) "
        "GROUP BY project_type ORDER BY n DESC",
        (days,),
    )
```

（按 target_lang 细分：调研 task/projects 表是否可低成本 join 出 `target_lang`；可行则按 (project_type, target_lang) 分组，不可行保持 project_type 粒度——结论写 commit message。）触红率：python 侧对近 N 天行复用 `is_red_assessment` 计数（行数有限，可接受）。
- [ ] **Step 2**: admin route（`@login_required + @admin_required`，参考现有 admin 蓝图）+ 模板表格：语言/类型、任务数、平均翻译分、平均 TTS 分、触红率。
- [ ] **Step 3**: 单测（mock db_query）+ 路由 smoke（未登录 302 / admin 200，按仓库 Verification 规范）→ `git commit -am "feat(block5): 30-day quality assessment summary for admin"`

### Task 5: 收尾验证

- [ ] `python3 scripts/pytest_related.py --base origin/master --run` 全 PASS；dev server 起服务：未登录新路由 302、登录 200、POST 带 CSRF（如有）。
- [ ] push `fix/omni-quality-block5-eval`，停下等验收。汇报注明：现网 DB binding `translation_quality.assess` 需管理员改为 `openrouter / google/gemini-3.5-flash`。
