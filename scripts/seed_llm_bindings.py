"""Seed llm_use_case_bindings with USE_CASES defaults.

Run once to materialize all default bindings into DB so admins see every row
marked as configured in /settings?tab=bindings. After seeding:
 - all rows show the "已自定义" tag
 - the "恢复默认" button is enabled per row
 - future changes to USE_CASES defaults don't affect already-running tasks

Idempotent: uses INSERT ... ON DUPLICATE KEY UPDATE under the hood (llm_bindings.upsert),
so re-running is safe.

Usage:
    python scripts/seed_llm_bindings.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from appcore import llm_bindings
from appcore.llm_use_cases import USE_CASES


def main() -> None:
    print(f"Seeding {len(USE_CASES)} use_case bindings...")
    for code, uc in USE_CASES.items():
        llm_bindings.upsert(
            code,
            provider=uc["default_provider"],
            model=uc["default_model"],
            updated_by=None,
        )
        print(f"  OK  {code:36s}  ->  {uc['default_provider']:18s}  /  {uc['default_model']}")
    print("Done.")


if __name__ == "__main__":
    main()
