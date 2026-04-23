"""copywriting_translate 子任务 runtime。

把 media_copywritings.lang='en' 的英文文案翻译到目标语言。

不要与现有 appcore/copywriting_runtime.py(从视频生成文案)混淆——
后者是"创作"流程,本模块是"翻译"流程,完全独立。

设计文档: docs/superpowers/specs/2026-04-18-bulk-translate-design.md 第 2.2 节
"""
from __future__ import annotations

import json
import logging
import re

from appcore.bulk_translate_associations import mark_auto_translated
from appcore.db import execute, query_one
from appcore.events import Event, EventBus, EVT_CT_PROGRESS
from pipeline.text_translate import translate_text

log = logging.getLogger(__name__)

# 文案需要翻译的字段。title / body / description / ad_* 都是文本。
_TRANSLATABLE_FIELDS = ("title", "body", "description",
                         "ad_carrier", "ad_copy", "ad_keywords")


_COPY_FIELD_LABELS = {
    "title": (
        "标题", "title", "headline", "subject",
        "titolo", "titel", "titre", "título", "titulo", "タイトル", "otsikko",
    ),
    "body": (
        "文案", "copy", "body", "text", "content", "message",
        "testo", "messaggio", "corpo", "contenuto", "texte", "texto",
        "本文", "コピー", "tekst", "teksti", "inhalt",
    ),
    "description": (
        "描述", "description", "desc", "detail",
        "descrizione", "beschreibung", "descripción", "descripcion",
        "descrição", "descricao", "説明", "説明文", "beschrijving",
        "omschrijving", "beskrivning", "kuvaus",
    ),
}
_COPY_LABEL_TO_FIELD = {
    label.casefold(): field
    for field, labels in _COPY_FIELD_LABELS.items()
    for label in labels
}
_COPY_LABEL_RE = re.compile(
    r"^\s*(?P<label>"
    + "|".join(
        re.escape(label)
        for label in sorted(_COPY_LABEL_TO_FIELD, key=len, reverse=True)
    )
    + r")\s*(?:[:：]|[-—]\s*)(?P<value>.*)$",
    re.IGNORECASE,
)


def _canonical_copy_field(label: str | None) -> str:
    return _COPY_LABEL_TO_FIELD.get((label or "").strip().casefold(), "")


def _strip_leading_copy_field_label(raw_value: str, expected_key: str) -> str:
    value = str(raw_value or "").strip()
    for _ in range(3):
        match = _COPY_LABEL_RE.match(value)
        nested_key = _canonical_copy_field(match.group("label")) if match else ""
        if not nested_key or nested_key != expected_key:
            break
        next_value = (match.group("value") or "").strip()
        if not next_value or next_value == value:
            break
        value = next_value
    return value


def _append_copy_field_value(fields: dict[str, str], key: str, raw_value: str) -> None:
    value = str(raw_value or "").replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"\s+", " ", value).strip()
    value = _strip_leading_copy_field_label(value, key)
    if not value:
        return
    fields[key] = f"{fields[key]} {value}".strip() if fields[key] else value


def _parse_copywriting_fields(raw: str) -> dict[str, str] | None:
    text = str(raw or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return None

    fields = {"title": "", "body": "", "description": ""}
    seen: set[str] = set()
    active_key = ""
    has_labeled_field = False

    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        match = _COPY_LABEL_RE.match(line)
        key = _canonical_copy_field(match.group("label")) if match else ""
        if key:
            has_labeled_field = True
            active_key = key
            seen.add(key)
            _append_copy_field_value(fields, key, match.group("value") or "")
            continue
        if active_key:
            _append_copy_field_value(fields, active_key, line)

    if not has_labeled_field:
        return None
    if any(key not in seen for key in fields):
        return None
    return fields


def _normalize_copywriting_translation(source_text: str, translated_text: str) -> str:
    if not _parse_copywriting_fields(source_text):
        return translated_text
    translated_fields = _parse_copywriting_fields(translated_text)
    if not translated_fields:
        return translated_text
    return "\n".join(
        [
            f"标题: {translated_fields['title']}",
            f"文案: {translated_fields['body']}",
            f"描述: {translated_fields['description']}",
        ]
    )


def _llm_translate(source_text: str, source_lang: str, target_lang: str) -> tuple[str, int]:
    """调用 LLM 翻译,返回 (译文, token 总数)。

    作为独立函数是为了让上层测试可以 monkeypatch 此处,
    无需 mock 到 pipeline 层。
    """
    r = translate_text(source_text, source_lang, target_lang)
    total_tokens = (r.get("input_tokens") or 0) + (r.get("output_tokens") or 0)
    return r["text"], total_tokens


def translate_copy_text(source_text: str, source_lang: str, target_lang: str) -> tuple[str, int]:
    """翻译单条文本。空输入短路。返回 (译文, 消耗 token)。"""
    if not source_text or not source_text.strip():
        return "", 0
    text, tokens = _llm_translate(source_text, source_lang, target_lang)
    return _normalize_copywriting_translation(source_text, text), tokens


# ============================================================
# CopywritingTranslateRunner — 子任务 runtime
# ============================================================
class CopywritingTranslateRunner:
    """执行 copywriting_translate 子任务。

    输入(从 projects.state_json 读):
        - source_copy_id: int        源英文 media_copywritings.id
        - source_lang: str           源语言(通常 'en')
        - target_lang: str           目标语言
        - parent_task_id: str|None   父任务 projects.id(可选)

    输出(写入):
        - 新插入一行 media_copywritings(lang=target_lang,字段翻译版)
        - mark_auto_translated 写三字段
        - 更新本子任务 projects.status / state_json
    """

    def __init__(self, task_id: str, bus: EventBus | None = None):
        self.task_id = task_id
        self.bus = bus
        self.state = self._load_state()

    def _emit(self, status: str, **extra) -> None:
        """发一条 CT 进度事件(给 SocketIO 桥接),bus 未挂接时静默。"""
        if self.bus is None:
            return
        payload = {
            "status": status,
            "parent_task_id": self.state.get("parent_task_id"),
            "target_lang": self.state.get("target_lang"),
            **extra,
        }
        try:
            self.bus.publish(Event(
                type=EVT_CT_PROGRESS,
                task_id=self.task_id,
                payload=payload,
            ))
        except Exception:
            log.exception("EventBus publish failed task_id=%s", self.task_id)

    # --- DB 读/写 ---

    def _load_state(self) -> dict:
        row = query_one(
            "SELECT user_id, state_json FROM projects WHERE id = %s",
            (self.task_id,),
        )
        if not row:
            raise ValueError(f"Project {self.task_id} not found")
        raw = row["state_json"]
        state = raw if isinstance(raw, dict) else json.loads(raw or "{}")
        state["_user_id"] = row["user_id"]
        return state

    def _save_state(self, patch: dict) -> None:
        self.state.update(patch)
        persist = {k: v for k, v in self.state.items() if not k.startswith("_")}
        execute(
            "UPDATE projects SET state_json = %s WHERE id = %s",
            (json.dumps(persist, ensure_ascii=False, default=str), self.task_id),
        )

    def _set_status(self, status: str) -> None:
        execute(
            "UPDATE projects SET status = %s WHERE id = %s",
            (status, self.task_id),
        )

    # --- 主流程 ---

    def start(self) -> None:
        self._set_status("running")
        self._emit("running")
        try:
            src = self._load_source_copy()
            translated, tokens = self._translate_fields(src)
            target_id = self._insert_target_copy(src, translated)
            mark_auto_translated(
                table="media_copywritings",
                target_id=target_id,
                source_ref_id=src["id"],
                bulk_task_id=self.state.get("parent_task_id"),
            )
            self._save_state({
                "target_copy_id": target_id,
                "tokens_used": tokens,
            })
            self._set_status("done")
            self._emit("done", tokens_used=tokens, target_copy_id=target_id)
            log.info(
                "copywriting_translate done task_id=%s src=%d tgt=%d tokens=%d",
                self.task_id, src["id"], target_id, tokens,
            )
        except Exception as e:
            self._save_state({"last_error": str(e)})
            self._set_status("error")
            self._emit("error", error=str(e))
            log.exception("copywriting_translate failed task_id=%s", self.task_id)
            raise

    def _load_source_copy(self) -> dict:
        src = query_one(
            "SELECT * FROM media_copywritings WHERE id = %s",
            (self.state["source_copy_id"],),
        )
        if not src:
            raise ValueError(
                f"Source copywriting {self.state['source_copy_id']} not found"
            )
        return src

    def _translate_fields(self, src: dict) -> tuple[dict, int]:
        """翻译源文案所有文本字段,返回(翻译字典, 总 token 消耗)。"""
        src_lang = self.state["source_lang"]
        tgt_lang = self.state["target_lang"]
        translated = {}
        total_tokens = 0
        for field in _TRANSLATABLE_FIELDS:
            original = src.get(field)
            if not original:
                translated[field] = original
                continue
            text, tokens = translate_copy_text(original, src_lang, tgt_lang)
            translated[field] = text
            total_tokens += tokens
        return translated, total_tokens

    def _insert_target_copy(self, src: dict, translated: dict) -> int:
        """新插入目标语言的 media_copywritings 行,返回新 id。"""
        new_id = execute(
            """
            INSERT INTO media_copywritings
                (product_id, lang, idx,
                 title, body, description,
                 ad_carrier, ad_copy, ad_keywords)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                src["product_id"],
                self.state["target_lang"],
                src.get("idx") or 1,
                translated.get("title"),
                translated.get("body"),
                translated.get("description"),
                translated.get("ad_carrier"),
                translated.get("ad_copy"),
                translated.get("ad_keywords"),
            ),
        )
        return new_id
