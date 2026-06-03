from __future__ import annotations

import logging
from typing import Callable

from appcore.db import execute, query

logger = logging.getLogger(__name__)


def get_names(product_codes: list[str], *, query_fn: Callable = None) -> dict[str, dict[str, str]]:
    """Query Chinese and English names for a list of product codes from the dictionary."""
    if not product_codes:
        return {}
    clean_codes = [c.strip().lower() for c in product_codes if c and isinstance(c, str) and c.strip()]
    if not clean_codes:
        return {}

    q_fn = query_fn or query
    placeholders = ",".join(["%s"] * len(clean_codes))
    try:
        rows = q_fn(
            f"""
            SELECT product_code, product_cn_name, product_en_name
            FROM product_name_dictionary
            WHERE product_code IN ({placeholders})
            """,
            tuple(clean_codes),
        )
        return {
            str(row["product_code"]).lower(): {
                "cn_name": str(row["product_cn_name"] or "").strip(),
                "en_name": str(row["product_en_name"] or "").strip(),
            }
            for row in rows or []
        }
    except Exception as e:
        logger.warning("Failed to query product name dictionary for codes %s: %s", clean_codes, e)
        return {}


def sync_names(
    product_code: str,
    cn_name: str | None,
    en_name: str | None,
    *,
    execute_fn: Callable = None,
) -> None:
    """Synchronize (insert or update) Chinese and/or English names for a product code.
    
    If name values are empty or None, they will not overwrite any existing non-empty values.
    """
    if not product_code:
        return
    code = product_code.strip().lower()
    if not code:
        return
    cn = (cn_name or "").strip()
    en = (en_name or "").strip()
    if not cn and not en:
        return

    exec_fn = execute_fn or execute
    try:
        exec_fn(
            """
            INSERT INTO product_name_dictionary (product_code, product_cn_name, product_en_name)
            VALUES (%s, NULLIF(%s, ''), NULLIF(%s, ''))
            ON DUPLICATE KEY UPDATE
              product_cn_name = COALESCE(NULLIF(VALUES(product_cn_name), ''), product_cn_name),
              product_en_name = COALESCE(NULLIF(VALUES(product_en_name), ''), product_en_name),
              updated_at = CURRENT_TIMESTAMP
            """,
            (code, cn, en),
        )
    except Exception as e:
        logger.warning("Failed to sync product name dictionary for code %s (cn=%s, en=%s): %s", code, cn, en, e)
