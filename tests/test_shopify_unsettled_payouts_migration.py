import re
from pathlib import Path

import pytest


pytestmark = pytest.mark.allow_shopify_browser_automation


def test_unsettled_payout_migration_uses_independent_archive_tables():
    sql = Path("db/migrations/2026_06_10_shopify_unsettled_payouts.sql").read_text()
    lowered = sql.lower()

    assert "docs/superpowers/specs/2026-06-10-shopify-unsettled-payout-ledger-design.md" in sql
    assert "create table if not exists shopify_unsettled_payout_projects" in lowered
    assert "create table if not exists shopify_unsettled_payout_rows" in lowered
    assert "source_row_number int not null" in lowered
    assert re.search(r"^\s*row_number\s+int\s+not\s+null", lowered, re.MULTILINE) is None
    assert "idx_shopify_unsettled_store_created" in lowered
    assert "idx_shopify_unsettled_rows_status" in lowered

    assert "shopify_orders" not in lowered
    assert "dianxiaomi_order_lines" not in lowered
    assert "order_profit_lines" not in lowered
    assert "shopify_payments_transactions" not in lowered
