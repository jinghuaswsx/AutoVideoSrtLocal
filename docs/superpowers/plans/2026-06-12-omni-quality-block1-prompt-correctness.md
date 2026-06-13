# Block 1 — Prompt 正确性 + Hook/CTA 职责 实施计划

> **For agentic workers:** 按 Task 顺序逐个完成，每个 Task 内 TDD：先写测试→跑失败→实现→跑通过→commit。Spec 与红线见 [specs/2026-06-12-omni-quality-block1-prompt-correctness-design.md](../specs/2026-06-12-omni-quality-block1-prompt-correctness-design.md) 与 [specs/2026-06-12-omni-quality-overview.md](../specs/2026-06-12-omni-quality-overview.md)。

**Goal:** 修复翻译 prompt 的源语言硬编码 / ASCII 措辞矛盾 / generic 模板错位，为 11 语种加入首句 Hook + 尾句收尾职责，交付 DB 重 seed 工具。

**Architecture:** 纯文本层改动：`pipeline/localization.py`（消息构建 + 遗留 prompt）、`pipeline/languages/prompt_defaults.py`（出厂默认）、`appcore/runtime_omni_steps.py`（调用方传参）、`scripts/reseed_prompt_defaults.py`（新工具）。不碰收敛/时长/multi。

**Tech Stack:** Python 3.12 / pytest / 现有 `appcore.llm_prompt_configs` DAO。

**分支**: 从 `origin/audit/video-translate-quality` 切出 `fix/omni-quality-block1-prompt`。

---

### Task 1: 失败测试先行 — prompt 文本质量断言

**Files:**
- Create: `tests/test_prompt_defaults_quality.py`

- [ ] **Step 1: 写测试**

```python
"""Block1: prompt 出厂默认文本质量断言。
Spec: docs/superpowers/specs/2026-06-12-omni-quality-block1-prompt-correctness-design.md
"""
from pipeline.languages.prompt_defaults import DEFAULTS

ALL_LANGS = ["en", "de", "fr", "es", "it", "pt", "ja", "nl", "sv", "fi"]
ACCENT_SENSITIVE = ["es", "it", "pt", "de"]


def _content(slot, lang):
    return DEFAULTS[(slot, lang)]["content"]


def test_no_ascii_only_wording_in_accent_sensitive_translation_prompts():
    for lang in ACCENT_SENSITIVE:
        assert "ASCII punctuation only" not in _content("base_translation", lang), lang


def test_accent_letters_declared_mandatory():
    assert "¿" in _content("base_translation", "es")
    assert "ñ" in _content("base_translation", "es")
    assert "à" in _content("base_translation", "it")
    assert "ã" in _content("base_translation", "pt")
    c_de = _content("base_translation", "de")
    assert ("Eszett" in c_de) or ("ß" in c_de)


def test_en_keeps_ascii_constraint():
    assert "ASCII punctuation only" in _content("base_translation", "en")


def test_all_translation_prompts_have_opening_ending_section():
    for lang in ALL_LANGS:
        assert "OPENING & ENDING" in _content("base_translation", lang), lang


def test_all_rewrite_prompts_have_protection_section():
    for lang in ALL_LANGS:
        assert "OPENING & ENDING PROTECTION" in _content("base_rewrite", lang), lang


def test_generic_template_is_source_language_neutral():
    for lang in ["nl", "sv", "fi"]:
        assert "English script" not in _content("base_translation", lang), lang
```

- [ ] **Step 2: 跑测试确认失败**：`pytest tests/test_prompt_defaults_quality.py -q` → 多条 FAIL（当前文本不满足）。
- [ ] **Step 3: commit 测试**：`git add tests/test_prompt_defaults_quality.py && git commit -m "test(block1): prompt defaults quality assertions (red)"`

### Task 2: 修改 `pipeline/languages/prompt_defaults.py`

**Files:**
- Modify: `pipeline/languages/prompt_defaults.py`

- [ ] **Step 1: 定义共享段常量**（文件顶部，`_DEFAULT_MODEL` 之后）：

```python
_OPENING_ENDING_TRANSLATION = """
OPENING & ENDING (mandatory):
- Sentence 1 is the 3-second hook (roughly the first 7-10 words). Rewrite the
  source opening into a strong hook — clear outcome, obvious benefit, curiosity,
  or surprise contrast — WITHOUT inventing facts not implied by the source.
- The final sentence must properly close the script. If the source ends with a
  CTA or wrap-up, preserve that intent in natural local phrasing. Never invent
  a new CTA (a universal CTA clip is appended later). Never end mid-thought."""

_OPENING_ENDING_REWRITE = """
OPENING & ENDING PROTECTION (mandatory):
- Sentence 1 must keep functioning as the 3-second hook.
- The final sentence must keep its closing / CTA intent.
- When shrinking, remove modifiers, repetition, and secondary details from the
  MIDDLE of the script. Never delete or flatten the hook sentence or the
  closing sentence."""
```

- [ ] **Step 2: 11 个语种挂载共享段**。对 `_EN_TRANSLATION`、`_DE_TRANSLATION`、`_FR_TRANSLATION`、`_ES_TRANSLATION`、`_IT_TRANSLATION`、`_PT_TRANSLATION`、`_JA_TRANSLATION` 在常量定义后追加 `+ _OPENING_ENDING_TRANSLATION`（如 `_EN_TRANSLATION = """...""" + _OPENING_ENDING_TRANSLATION`）；7 个 `_XX_REWRITE` 同理追加 `_OPENING_ENDING_REWRITE`；`_build_generic_translation` / `_build_generic_rewrite` 的返回 f-string 末尾拼接对应常量（注意 f-string 内 `{` 转义不受影响，常量在 f-string 外拼接）。
- [ ] **Step 3: 修 ASCII 措辞**（逐处替换，保持上下文其余行不动）：
  - `_DE_TRANSLATION`：`No em-dashes, no en-dashes, ASCII punctuation only.` → `No em-dashes, no en-dashes. Standard punctuation is fine.`（umlaut/ß 行已有，保留）
  - `_ES_TRANSLATION`：`- No em/en dashes. ASCII punctuation plus ¿ ¡ only.` → `- No em/en dashes. Standard punctuation plus ¿ ¡. Accented letters (á é í ó ú ñ ü) are MANDATORY — never strip accents.`
  - `_IT_TRANSLATION`：`- No em/en dashes. ASCII punctuation only.` → `- No em/en dashes. Standard punctuation. Accented letters (à è é ì ò ù) are MANDATORY — never strip accents.`
  - `_PT_TRANSLATION`：`- No em/en dashes. ASCII punctuation only.` → `- No em/en dashes. Standard punctuation. Accented letters (ã õ á é ê ç) are MANDATORY — never strip accents.`
  - `_build_generic_translation`：`- No em-dashes or en-dashes; use plain punctuation only.` → `- No em-dashes or en-dashes. Standard punctuation. Letters with diacritics required by {language_name} orthography are MANDATORY — never strip them.`（用普通字符串拼接或在 f-string 中直接引用 `language_name`）
  - `_build_generic_translation` 首段：`Recreate the English script so it sounds like...` → `Recreate the source script (it may be in any language) so it sounds like...`
- [ ] **Step 4: 跑测试**：`pytest tests/test_prompt_defaults_quality.py -q` → 全 PASS。
- [ ] **Step 5: commit**：`git commit -am "feat(block1): fix ASCII wording, add OPENING & ENDING sections to all language prompts"`

### Task 3: `localization.py` 源语言动态标签 + 遗留 prompt 修复

**Files:**
- Modify: `pipeline/localization.py`
- Modify: `pipeline/translate.py`（透传参数）
- Modify: `appcore/runtime_omni.py`（标签表改引用）
- Create/Modify: `tests/test_localization_messages.py`

- [ ] **Step 1: 写失败测试**

```python
from pipeline.localization import build_localized_translation_messages

SEGS = [{"index": 0, "text": "hola"}]


def test_source_language_label_dynamic():
    msgs = build_localized_translation_messages("hola", SEGS, source_language="es")
    user = msgs[1]["content"]
    assert "Source Spanish full text" in user
    assert "Chinese" not in user


def test_source_language_default_keeps_chinese():
    msgs = build_localized_translation_messages("你好", SEGS)
    assert "Source Chinese full text" in msgs[1]["content"]
```

跑 `pytest tests/test_localization_messages.py -q` → FAIL（TypeError: unexpected keyword）。

- [ ] **Step 2: 实现**。`localization.py`：

```python
SOURCE_LANG_PROMPT_LABEL: dict[str, str] = {
    "zh": "Chinese", "en": "English", "es": "Spanish", "pt": "Portuguese",
    "fr": "French", "it": "Italian", "ja": "Japanese", "de": "German",
    "nl": "Dutch", "sv": "Swedish", "fi": "Finnish",
}
```

`build_localized_translation_messages` 加 `source_language: str = "zh"` 形参，user content 用 `label = SOURCE_LANG_PROMPT_LABEL.get((source_language or "zh").strip().lower(), (source_language or "zh").upper())` 渲染 `f"Source {label} full text:\n..."` 与 `f"Source {label} segments:\n..."`。
- [ ] **Step 3: 透传**。`pipeline/translate.py`：`generate_localized_translation` / `_generate_localized_translation_single` / `_generate_localized_translation_batched` 各加 `source_language: str = "zh"` 并一路传给 messages builder（batched 的每个 batch 调 `_single` 时也传）。
- [ ] **Step 4: 调用方**。`appcore/runtime_omni_steps.py::step_translate_standard` 的 `generate_localized_translation(...)` 调用加 `source_language=source_language,`。
- [ ] **Step 5: 标签表去重**。`appcore/runtime_omni.py`：`OmniLocalizationAdapter._SOURCE_LANG_LABEL` 改为 `from pipeline.localization import SOURCE_LANG_PROMPT_LABEL as _SOURCE_LANG_LABEL` 引用（类属性赋值 `_SOURCE_LANG_LABEL = SOURCE_LANG_PROMPT_LABEL`）。
- [ ] **Step 6: 遗留 prompt 修复**（同文件 `localization.py`）：
  - `LOCALIZED_REWRITE_SYSTEM_PROMPT` 中 `- No em/en dashes. Plain ASCII punctuation only.` → `- No em/en dashes. Standard punctuation; accented letters required by the target language are MANDATORY.`
  - `LOCALIZED_TRANSLATION_SYSTEM_PROMPT_ZH` / `HOOK_CTA_TRANSLATION_SYSTEM_PROMPT_ZH` 中 `不要使用破折号。仅使用纯 ASCII 标点，优选逗号、句号和问号。` → `不要使用破折号。使用常规标点；目标语言正字法要求的重音/特殊字母必须保留。`
  - `grep -rn "build_localized_rewrite_messages" --include="*.py" | grep -v adapter` 确认 `localization.build_localized_rewrite_messages` 的运行时调用方；若仅被 adapter 覆盖版遮蔽，在该函数与 `LOCALIZED_REWRITE_SYSTEM_PROMPT` 上方加注释 `# DEPRECATED fallback: omni/multi 运行时走 per-language base_rewrite（DB 配置）`。
- [ ] **Step 7: 跑测试**：`pytest tests/test_localization_messages.py tests/test_prompt_defaults_quality.py -q` → PASS。
- [ ] **Step 8: commit**：`git commit -am "feat(block1): dynamic source-language label in translation messages; fix legacy ASCII wording"`

### Task 4: reseed 工具

**Files:**
- Create: `scripts/reseed_prompt_defaults.py`
- Create: `tests/test_reseed_prompt_defaults.py`

- [ ] **Step 1: 写失败测试**（mock `appcore.llm_prompt_configs` 的 `get_one`/`upsert`）：

```python
from unittest.mock import patch
from scripts.reseed_prompt_defaults import diff_defaults, apply_defaults


def test_diff_reports_same_diff_missing():
    fake_defaults = {
        ("base_translation", "en"): {"provider": "p", "model": "m", "content": "NEW"},
        ("base_translation", "de"): {"provider": "p", "model": "m", "content": "X"},
    }
    def fake_get_one(slot, lang):
        if lang == "en":
            return {"content": "OLD", "model_provider": "p", "model_name": "m"}
        return None
    with patch("scripts.reseed_prompt_defaults.DEFAULTS", fake_defaults), \
         patch("scripts.reseed_prompt_defaults.get_one", side_effect=fake_get_one):
        rows = diff_defaults()
    status = {(r["slot"], r["lang"]): r["status"] for r in rows}
    assert status[("base_translation", "en")] == "DIFF"
    assert status[("base_translation", "de")] == "MISSING"


def test_apply_upserts_filtered_rows():
    fake_defaults = {("base_rewrite", "it"): {"provider": "p", "model": "m", "content": "C"}}
    with patch("scripts.reseed_prompt_defaults.DEFAULTS", fake_defaults), \
         patch("scripts.reseed_prompt_defaults.upsert") as up:
        n = apply_defaults(slot="base_rewrite", lang="it")
    assert n == 1
    up.assert_called_once()
```

- [ ] **Step 2: 实现脚本**

```python
"""对比 / 重 seed llm_prompt_configs 与代码出厂默认。

用法：
  python3 scripts/reseed_prompt_defaults.py                 # dry-run 列 SAME/DIFF/MISSING
  python3 scripts/reseed_prompt_defaults.py --apply --yes   # 全量覆盖（慎用）
  python3 scripts/reseed_prompt_defaults.py --apply --yes --slot base_translation --lang it
退出码：dry-run 存在 DIFF/MISSING → 1，否则 0。
"""
from __future__ import annotations
import argparse
import difflib
import sys

from appcore.llm_prompt_configs import get_one, upsert
from pipeline.languages.prompt_defaults import DEFAULTS


def diff_defaults(slot: str | None = None, lang: str | None = None) -> list[dict]:
    rows = []
    for (s, l), d in sorted(DEFAULTS.items(), key=lambda kv: (kv[0][0], kv[0][1] or "")):
        if slot and s != slot:
            continue
        if lang is not None and l != lang:
            continue
        db = get_one(s, l)
        if db is None:
            rows.append({"slot": s, "lang": l, "status": "MISSING", "diff": ""})
            continue
        if (db.get("content") or "") == d["content"]:
            rows.append({"slot": s, "lang": l, "status": "SAME", "diff": ""})
        else:
            diff = "\n".join(difflib.unified_diff(
                (db.get("content") or "").splitlines(), d["content"].splitlines(),
                fromfile="db", tofile="default", lineterm="", n=1,
            ))
            rows.append({"slot": s, "lang": l, "status": "DIFF", "diff": diff})
    return rows


def apply_defaults(slot: str | None = None, lang: str | None = None) -> int:
    n = 0
    for (s, l), d in DEFAULTS.items():
        if slot and s != slot:
            continue
        if lang is not None and l != lang:
            continue
        upsert(s, l, provider=d["provider"], model=d["model"], content=d["content"], updated_by=None)
        n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--slot")
    ap.add_argument("--lang")
    args = ap.parse_args()
    if args.apply:
        if not args.yes:
            print("拒绝执行：--apply 必须配合 --yes"); return 2
        n = apply_defaults(args.slot, args.lang)
        print(f"已覆盖 {n} 行"); return 0
    rows = diff_defaults(args.slot, args.lang)
    dirty = 0
    for r in rows:
        print(f"[{r['status']}] {r['slot']} / {r['lang']!r}")
        if r["status"] != "SAME":
            dirty += 1
            if r["diff"]:
                print(r["diff"])
    print(f"共 {len(rows)} 行，{dirty} 行需关注")
    return 1 if dirty else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3: 跑测试**：`pytest tests/test_reseed_prompt_defaults.py -q` → PASS。
- [ ] **Step 4: commit**：`git commit -am "feat(block1): reseed_prompt_defaults tool for DB/default prompt sync"`

### Task 5: 收尾验证

- [ ] **Step 1**: `python3 scripts/pytest_related.py --base origin/master --run` → 全 PASS（失败则修复后重跑）。
- [ ] **Step 2**: `git diff origin/audit/video-translate-quality --stat` 自查：改动不含 `runtime_multi.py`、`_pipeline_runner.py`、tts/时长文件。
- [ ] **Step 3**: push 分支：`git push origin fix/omni-quality-block1-prompt`，停下等人工验收。汇报中注明：**上线部署后需运行 reseed dry-run 并人工确认 apply**，否则 DB 旧 prompt 仍生效。
