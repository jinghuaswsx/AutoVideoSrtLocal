"""审核 media_copywritings 里目标语言文案翻译是否合规。

两道关检测：
1) 规则扫（本地，快）— 嵌套标签 / 三段块槽位与英文源完全相同 / 单字段列与英文源完全相同
2) Gemini 3.1 Flash-Lite 二次确认（only on 规则命中行）— 输出"符合"/"不符合"

最终重译清单 = 规则命中 ∩ LLM 不符合。规则误报（LLM 复核为符合）单独记录，便于人工抽查。

用法（在 LocalServer 上）：
  cd /opt/autovideosrt
  python -m tools.audit_copywriting_translation --langs ja,it,pt,sv > audit_report.json

参数：
  --langs       逗号分隔目标语言（默认 ja,it,pt,sv）
  --limit N     每语言最多取 N 行（默认无限制，调试用）
  --concurrency K  LLM 并发度（默认 6）
  --output PATH JSON 报告输出路径（默认 stdout）
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

log = logging.getLogger("audit_copywriting_translation")


# ----------------------------------------------------------------------
# 标签解析（与 appcore.copywriting_translate_runtime 同步，独立内嵌避免依赖）
# ----------------------------------------------------------------------

_COPY_FIELD_LABELS = {
    "title": (
        "标题", "title", "headline", "subject",
        "titolo", "titel", "titre", "título", "titulo", "タイトル", "otsikko",
        "標題", "rubrik",
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
    + "|".join(re.escape(lbl) for lbl in sorted(_COPY_LABEL_TO_FIELD, key=len, reverse=True))
    + r")\s*(?:[:：]|[-—]\s*)(?P<value>.*)$",
    re.IGNORECASE,
)

# Rule A：行首中文标签 + 紧跟一个被本地化为外语的字段名前缀
_ZH_LABELS = {"标题", "文案", "描述"}
_FOREIGN_LABELS = sorted(
    {
        label
        for labels in _COPY_FIELD_LABELS.values()
        for label in labels
        if label not in _ZH_LABELS
    },
    key=len,
    reverse=True,
)
_NESTED_LABEL_RE = re.compile(
    r"(?:^|\n)\s*(?:标题|文案|描述)\s*[:：]\s*"
    + r"(?:" + "|".join(re.escape(lbl) for lbl in _FOREIGN_LABELS) + r")"
    + r"\s*[:：]",
    re.IGNORECASE,
)

LANG_NAME_ZH = {
    "ja": "日语",
    "it": "意大利语",
    "pt": "葡萄牙语",
    "sv": "瑞典语",
    "de": "德语",
    "fr": "法语",
    "es": "西班牙语",
    "nl": "荷兰语",
    "fi": "芬兰语",
}


def _canonical_field(label: str | None) -> str:
    return _COPY_LABEL_TO_FIELD.get((label or "").strip().casefold(), "")


def parse_block(raw: str) -> dict[str, str] | None:
    """三段块解析。源不是块格式时返回 None。"""
    text = str(raw or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return None
    fields = {"title": "", "body": "", "description": ""}
    seen: set[str] = set()
    active = ""
    has_label = False
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        m = _COPY_LABEL_RE.match(line)
        key = _canonical_field(m.group("label")) if m else ""
        if key:
            has_label = True
            active = key
            seen.add(key)
            v = (m.group("value") or "").strip()
            if v:
                fields[key] = (fields[key] + " " + v).strip() if fields[key] else v
            continue
        if active:
            fields[active] = (fields[active] + " " + line).strip() if fields[active] else line
    if not has_label or any(k not in seen for k in fields):
        return None
    return fields


def _normalize(s: str | None) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip())


_PLACEHOLDER_VALUES = {"", "-", "—", "n/a", "na", "tbd", "todo"}


def _looks_meaningful(s: str) -> bool:
    n = _normalize(s).casefold()
    return n not in _PLACEHOLDER_VALUES and len(n) >= 2


# ----------------------------------------------------------------------
# 规则扫（pure function）
# ----------------------------------------------------------------------

def regex_audit(row: dict[str, Any]) -> list[str]:
    """对单行做规则扫，返回命中的规则代号列表。空 = 没命中。

    入参 row 期待字段：t_title / t_body / t_description / t_ad_carrier / t_ad_copy
        / t_ad_keywords / s_title / s_body / s_description / s_ad_carrier / s_ad_copy
        / s_ad_keywords
    """
    hits: list[str] = []

    # Rule A：嵌套标签
    for col in ("t_title", "t_body", "t_description"):
        v = row.get(col) or ""
        if v and _NESTED_LABEL_RE.search(v):
            hits.append(f"A:{col[2:]}")

    # Rule B：三段块槽位与英文源完全相同
    t_block = parse_block(row.get("t_body") or "")
    s_block = parse_block(row.get("s_body") or "")
    if t_block and s_block:
        for slot in ("title", "body", "description"):
            tv = _normalize(t_block.get(slot))
            sv = _normalize(s_block.get(slot))
            if _looks_meaningful(sv) and tv == sv:
                hits.append(f"B:body.{slot}")

    # Rule C：单字段列与英文源完全相同
    for col in ("title", "description", "ad_carrier", "ad_copy", "ad_keywords"):
        tv = _normalize(row.get(f"t_{col}"))
        sv = _normalize(row.get(f"s_{col}"))
        if _looks_meaningful(sv) and tv == sv:
            hits.append(f"C:{col}")

    return hits


# ----------------------------------------------------------------------
# LLM 二次确认
# ----------------------------------------------------------------------

_AUDIT_PROMPT_TEMPLATE = """你是文案翻译质量审核员，判断给定的{lang_name}译文是否符合本项目的硬性翻译规则。

【规则（任一违反即"不符合"）】
1. 三段式输出每行必须以中文标签「标题: 」「文案: 」「描述: 」开头；禁止改写为「Title:」「Titel:」「Titolo:」「タイトル:」「標題:」等其它语言形式。
2. 冒号后不得再有任何字段名前缀（例：「标题: 標題: ...」「标题: タイトル: ...」「标题: Titolo: ...」都判"不符合"）。
3. 三段（标题/文案/描述）都必须翻译成{lang_name}。即使是疑问句、品牌名、口号或单个英文短语也必须翻译为{lang_name}。允许保留的极少例外：纯商标 / 产品代号 / 通用专有名词（如 "iPhone" "USB" "3-in-1" "TikTok"）。
4. 译文不应在任一段上与英文源完全相同（上一条允许保留的专有名词除外）。

【英文源】
{source_en}

【{lang_name}译文】
{translated}

【输出】只输出一个 JSON 对象，不要任何 markdown 围栏、解释或多余文字：
{{"verdict": "符合"或"不符合", "reason": "<不超过 30 字简短理由>"}}
"""


def build_audit_prompt(lang_code: str, source_en: str, translated: str) -> str:
    lang_name = LANG_NAME_ZH.get(lang_code, lang_code)
    return _AUDIT_PROMPT_TEMPLATE.format(
        lang_name=lang_name,
        source_en=source_en,
        translated=translated,
    )


_VERDICT_RE = re.compile(r'"verdict"\s*:\s*"(符合|不符合)"')
_REASON_RE = re.compile(r'"reason"\s*:\s*"([^"]*)"')


def parse_audit_verdict(raw: str) -> dict[str, str]:
    """解析 LLM 返回。优先用 json.loads；失败时退回正则提取。

    返回 {"verdict": "符合"|"不符合"|"未知", "reason": "..."}.
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        # 去 markdown 围栏
        m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if m:
            text = m.group(1).strip()
    try:
        data = json.loads(text)
        verdict = str(data.get("verdict") or "").strip()
        reason = str(data.get("reason") or "").strip()
        if verdict in ("符合", "不符合"):
            return {"verdict": verdict, "reason": reason[:80]}
    except json.JSONDecodeError:
        pass
    v_match = _VERDICT_RE.search(raw or "")
    r_match = _REASON_RE.search(raw or "")
    if v_match:
        return {"verdict": v_match.group(1), "reason": (r_match.group(1) if r_match else "")[:80]}
    return {"verdict": "未知", "reason": "解析失败"}


def llm_audit_one(
    *,
    invoke_chat,
    lang_code: str,
    source_en: str,
    translated: str,
) -> dict[str, str]:
    """调一次 LLM，返回 verdict dict。失败时 verdict='未知'。"""
    prompt = build_audit_prompt(lang_code, source_en, translated)
    try:
        response = invoke_chat(
            "copywriting_translate.audit",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=128,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("audit LLM call failed: %s", exc)
        return {"verdict": "未知", "reason": f"LLM error: {exc}"[:80]}
    raw = response.get("text") if isinstance(response, dict) else None
    return parse_audit_verdict(raw or "")


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------

_ROW_QUERY = """
SELECT
  t.id, t.product_id, t.lang, t.idx,
  t.title       AS t_title,
  t.body        AS t_body,
  t.description AS t_description,
  t.ad_carrier  AS t_ad_carrier,
  t.ad_copy     AS t_ad_copy,
  t.ad_keywords AS t_ad_keywords,
  s.title       AS s_title,
  s.body        AS s_body,
  s.description AS s_description,
  s.ad_carrier  AS s_ad_carrier,
  s.ad_copy     AS s_ad_copy,
  s.ad_keywords AS s_ad_keywords,
  p.product_code AS product_code,
  p.name         AS product_name
FROM media_copywritings t
LEFT JOIN media_copywritings s
  ON s.product_id = t.product_id AND s.idx = t.idx AND s.lang = 'en'
LEFT JOIN media_products p ON p.id = t.product_id
WHERE t.lang IN ({placeholders})
ORDER BY t.lang, t.product_id, t.idx, t.id
"""


def fetch_rows(query_all, langs: list[str], limit_per_lang: int | None) -> list[dict]:
    placeholders = ",".join(["%s"] * len(langs))
    sql = _ROW_QUERY.format(placeholders=placeholders)
    rows = list(query_all(sql, tuple(langs)))
    if limit_per_lang is None or limit_per_lang <= 0:
        return rows
    bucket: dict[str, list[dict]] = {}
    for r in rows:
        bucket.setdefault(r["lang"], []).append(r)
    out: list[dict] = []
    for lang in langs:
        out.extend(bucket.get(lang, [])[:limit_per_lang])
    return out


def _format_source_for_audit(row: dict) -> str:
    """优先用英文 body（三段式），否则拼字段。"""
    s_body = (row.get("s_body") or "").strip()
    if s_body:
        return s_body
    parts = []
    for col in ("title", "body", "description"):
        v = (row.get(f"s_{col}") or "").strip()
        if v:
            parts.append(f"{col}: {v}")
    return "\n".join(parts)


def _format_translated_for_audit(row: dict) -> str:
    t_body = (row.get("t_body") or "").strip()
    if t_body:
        return t_body
    parts = []
    for col in ("title", "body", "description"):
        v = (row.get(f"t_{col}") or "").strip()
        if v:
            parts.append(f"{col}: {v}")
    return "\n".join(parts)


def audit_rows(
    rows: list[dict],
    *,
    invoke_chat,
    concurrency: int = 6,
) -> dict[str, Any]:
    """对所有行做规则扫 + LLM 复核（仅命中行）。"""
    stats: dict[str, dict[str, int]] = {}
    items: list[dict] = []
    candidates: list[tuple[int, dict, list[str]]] = []  # (idx_in_items, row, hits)

    # 第一道：规则扫
    for r in rows:
        lang = r["lang"]
        stats.setdefault(lang, {"total": 0, "regex_hit": 0, "llm_broken": 0, "llm_disagree": 0, "llm_unknown": 0})
        stats[lang]["total"] += 1

        hits = regex_audit(r)
        if hits:
            stats[lang]["regex_hit"] += 1
            candidates.append((len(items), r, hits))
            items.append({
                "id": r["id"],
                "product_id": r["product_id"],
                "product_code": r.get("product_code"),
                "product_name": r.get("product_name"),
                "lang": lang,
                "idx": r["idx"],
                "rules": hits,
                "verdict": "未知",
                "reason": "",
                "sample_translated": _format_translated_for_audit(r)[:240],
                "sample_source": _format_source_for_audit(r)[:240],
            })

    # 第二道：LLM 复核（仅命中候选）
    if candidates and invoke_chat is not None:
        def _task(payload):
            i, r, _hits = payload
            return i, llm_audit_one(
                invoke_chat=invoke_chat,
                lang_code=r["lang"],
                source_en=_format_source_for_audit(r),
                translated=_format_translated_for_audit(r),
            )

        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [pool.submit(_task, c) for c in candidates]
            for fut in as_completed(futures):
                i, verdict_dict = fut.result()
                items[i]["verdict"] = verdict_dict["verdict"]
                items[i]["reason"] = verdict_dict["reason"]
                lang = items[i]["lang"]
                if verdict_dict["verdict"] == "不符合":
                    stats[lang]["llm_broken"] += 1
                elif verdict_dict["verdict"] == "符合":
                    stats[lang]["llm_disagree"] += 1
                else:
                    stats[lang]["llm_unknown"] += 1

    # 汇总
    summary = {
        "regex_hits_total": sum(s["regex_hit"] for s in stats.values()),
        "llm_confirmed_broken_total": sum(s["llm_broken"] for s in stats.values()),
        "llm_disagreed_total": sum(s["llm_disagree"] for s in stats.values()),
        "llm_unknown_total": sum(s["llm_unknown"] for s in stats.values()),
        "rows_scanned_total": sum(s["total"] for s in stats.values()),
    }
    # 重译清单 = 规则命中 ∩ LLM 不符合
    retranslate_ids = [it["id"] for it in items if it["verdict"] == "不符合"]
    return {
        "stats": stats,
        "summary": summary,
        "retranslate_ids": retranslate_ids,
        "retranslate_count": len(retranslate_ids),
        "items": items,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--langs", default="ja,it,pt,sv", help="逗号分隔目标语言")
    p.add_argument("--limit", type=int, default=0, help="每语言最多取多少行（0 = 不限制）")
    p.add_argument("--concurrency", type=int, default=6, help="LLM 并发度")
    p.add_argument("--output", default="-", help="JSON 报告输出路径，默认 stdout")
    p.add_argument("--no-llm", action="store_true", help="跳过 LLM 复核（只做规则扫）")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)

    langs = [c.strip() for c in args.langs.split(",") if c.strip()]
    if not langs:
        log.error("--langs 不能为空")
        return 2

    from appcore import db, llm_client

    invoke_chat = None if args.no_llm else llm_client.invoke_chat

    rows = fetch_rows(db.query_all, langs, args.limit or None)
    log.info("rows fetched: %d (langs=%s)", len(rows), ",".join(langs))

    report = audit_rows(rows, invoke_chat=invoke_chat, concurrency=args.concurrency)
    log.info(
        "regex hits=%d, llm broken=%d, disagreed=%d, unknown=%d",
        report["summary"]["regex_hits_total"],
        report["summary"]["llm_confirmed_broken_total"],
        report["summary"]["llm_disagreed_total"],
        report["summary"]["llm_unknown_total"],
    )

    payload = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    if args.output == "-":
        sys.stdout.write(payload + "\n")
    else:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(payload)
        log.info("report written to %s", args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
