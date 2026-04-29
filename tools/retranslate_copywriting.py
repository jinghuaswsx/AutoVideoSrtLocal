"""按 id 重译 media_copywritings 行。

用 appcore.copywriting_translate_runtime.translate_copy_text 走修复后的
title_translate.generate 链路。原地 UPDATE 目标行的 title / body / description /
ad_carrier / ad_copy / ad_keywords 字段。

用法：
  cd /opt/autovideosrt
  # 1) 先 dry-run 看 diff，不写库
  python -m tools.retranslate_copywriting --ids 5941,6267,2612,5600,5633,5667,3997,5575,5522,5476,5513,5534
  # 2) 看没问题再 --apply 真写
  python -m tools.retranslate_copywriting --ids 5941,... --apply

参数：
  --ids X,Y,Z       逗号分隔 media_copywritings.id（必填）
  --apply           真写 DB；缺省为 dry-run
  --output PATH     diff 报告输出路径（默认 stdout）
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any

log = logging.getLogger("retranslate_copywriting")

_TRANSLATABLE_FIELDS = (
    "title",
    "body",
    "description",
    "ad_carrier",
    "ad_copy",
    "ad_keywords",
)


def _diff_summary(old: str | None, new: str | None) -> str:
    o = (old or "").strip()
    n = (new or "").strip()
    if o == n:
        return "(unchanged)"
    short_o = o[:120].replace("\n", "⏎")
    short_n = n[:120].replace("\n", "⏎")
    return f"  - old: {short_o}\n  - new: {short_n}"


def _retranslate_one(
    *,
    target_id: int,
    query_one,
    execute,
    translate_fn,
) -> dict[str, Any]:
    """重译一条 media_copywritings 行。返回该行的 diff payload。
    不在此处 UPDATE；调用方根据 apply 标志决定是否落盘。
    """
    target = query_one(
        "SELECT id, product_id, lang, idx, title, body, description, "
        "       ad_carrier, ad_copy, ad_keywords "
        "FROM media_copywritings WHERE id = %s",
        (target_id,),
    )
    if not target:
        return {"id": target_id, "status": "missing", "fields": {}}

    source = query_one(
        "SELECT id, title, body, description, ad_carrier, ad_copy, ad_keywords "
        "FROM media_copywritings "
        "WHERE product_id = %s AND idx = %s AND lang = 'en' "
        "ORDER BY id ASC LIMIT 1",
        (target["product_id"], target["idx"]),
    )
    if not source:
        return {
            "id": target_id,
            "product_id": target["product_id"],
            "lang": target["lang"],
            "status": "missing_source",
            "fields": {},
        }

    new_values: dict[str, str | None] = {}
    fields_diff: dict[str, dict[str, str | None]] = {}
    total_tokens = 0
    for field in _TRANSLATABLE_FIELDS:
        original = source.get(field)
        old_value = target.get(field)
        if not original or not str(original).strip():
            new_values[field] = old_value
            continue
        text, tokens = translate_fn(str(original), "en", target["lang"])
        total_tokens += tokens
        new_values[field] = text
        if (text or "").strip() != (old_value or "").strip():
            fields_diff[field] = {"old": old_value, "new": text}

    return {
        "id": target_id,
        "product_id": target["product_id"],
        "lang": target["lang"],
        "idx": target["idx"],
        "source_id": source["id"],
        "tokens_used": total_tokens,
        "status": "translated",
        "fields": fields_diff,
        "_pending_values": new_values,
    }


def _apply_update(execute, target_id: int, values: dict[str, str | None]) -> None:
    execute(
        "UPDATE media_copywritings SET "
        "  title = %s, body = %s, description = %s, "
        "  ad_carrier = %s, ad_copy = %s, ad_keywords = %s "
        "WHERE id = %s",
        (
            values.get("title"),
            values.get("body"),
            values.get("description"),
            values.get("ad_carrier"),
            values.get("ad_copy"),
            values.get("ad_keywords"),
            target_id,
        ),
    )


def retranslate_ids(
    target_ids: list[int],
    *,
    query_one,
    execute,
    translate_fn,
    apply: bool,
) -> dict[str, Any]:
    """对一组 id 重译。dry_run（apply=False）时只产出 diff，不写 DB。
    单条 id 失败（语言未启用 / LLM 报错等）会被捕获并记录，不影响其它 id。
    """
    items: list[dict[str, Any]] = []
    applied: list[int] = []
    skipped_unchanged: list[int] = []
    missing: list[int] = []
    errored: list[int] = []

    for tid in target_ids:
        try:
            result = _retranslate_one(
                target_id=tid,
                query_one=query_one,
                execute=execute,
                translate_fn=translate_fn,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("retranslate id=%s failed: %s", tid, exc)
            errored.append(tid)
            items.append({"id": tid, "status": "error", "error": str(exc)[:200], "fields": {}})
            continue
        if result["status"] == "missing":
            missing.append(tid)
            items.append({k: v for k, v in result.items() if k != "_pending_values"})
            continue
        if result["status"] == "missing_source":
            items.append({k: v for k, v in result.items() if k != "_pending_values"})
            continue

        pending = result.pop("_pending_values")
        if not result["fields"]:
            skipped_unchanged.append(tid)
            items.append(result)
            continue

        if apply:
            try:
                _apply_update(execute, tid, pending)
            except Exception as exc:  # noqa: BLE001
                log.warning("UPDATE id=%s failed: %s", tid, exc)
                errored.append(tid)
                items.append({"id": tid, "status": "update_error", "error": str(exc)[:200],
                              "fields": result["fields"]})
                continue
            applied.append(tid)
        items.append(result)

    return {
        "applied": apply,
        "applied_ids": applied,
        "skipped_unchanged_ids": skipped_unchanged,
        "missing_ids": missing,
        "errored_ids": errored,
        "total": len(target_ids),
        "items": items,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--ids",
        required=True,
        help="逗号分隔的 media_copywritings.id",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="实际写库（缺省为 dry-run）",
    )
    p.add_argument(
        "--output",
        default="-",
        help="diff 报告输出路径，默认 stdout",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)

    try:
        ids = [int(s.strip()) for s in args.ids.split(",") if s.strip()]
    except ValueError as exc:
        log.error("--ids 解析失败: %s", exc)
        return 2
    if not ids:
        log.error("--ids 不能为空")
        return 2

    from appcore import db
    from appcore.copywriting_translate_runtime import translate_copy_text

    log.info("retranslate %d rows (apply=%s)", len(ids), args.apply)
    report = retranslate_ids(
        ids,
        query_one=db.query_one,
        execute=db.execute,
        translate_fn=translate_copy_text,
        apply=args.apply,
    )
    log.info(
        "applied=%d, unchanged=%d, missing=%d, errored=%d",
        len(report["applied_ids"]),
        len(report["skipped_unchanged_ids"]),
        len(report["missing_ids"]),
        len(report["errored_ids"]),
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
