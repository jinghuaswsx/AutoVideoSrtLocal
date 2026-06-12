# Realtime Dashboard Purchase Sanity Guard

Date: 2026-06-12

## Background

Realtime dashboard profit cards and global break-even ROAS are derived from
`order_profit_lines`, which are built by `tools/order_profit_backfill.py`.
Purchase cost currently uses this priority:

1. `dianxiaomi_order_lines.purchase_price_cny`
2. `media_products.purchase_price`
3. missing-purchase estimate: `revenue * 10%`

This priority is correct for normal historical snapshots, but a bad purchase
snapshot can be much worse than a missing value. On 2026-06-12, several order
lines received purchase snapshots around 680 CNY for products whose line revenue
was about 17 USD. Those rows were treated as complete costs and pushed realtime
break-even ROAS far above the useful operating range.

Latest `origin/master` already adds a product-level price-unit guard in
`docs/superpowers/specs/2026-06-12-realtime-breakeven-roas-price-unit-guard.md`.
That guard blocks future cents-like `standalone_price` writes. This spec covers
the order-profit backfill layer, where historical order snapshots or imported
rows can still contain impossible purchase costs and must not be treated as
complete actual costs.

## Requirement

Order profit calculation must treat an obviously impossible purchase value as an
estimated purchase, not as a complete actual purchase.

For each order line:

- Compute line revenue as `line_amount_usd + shipping_allocated_usd`.
- Compute purchase in USD as `purchase_price_cny * quantity / rmb_per_usd`.
- If computed purchase USD exceeds line revenue, mark the purchase input as
  suspicious and calculate purchase by the existing missing-purchase estimate
  (`revenue * 10%`).
- Preserve the existing incomplete status path so the dashboard can show that
  the purchase component is estimated.
- Record a structured `purchase_price_sanity` object in `cost_basis` so the
  offending value can be audited later.

This guard is deliberately narrow. It does not reject merely high costs, high
shipping, low-margin products, or products whose break-even ROAS is legitimately
high. It only blocks purchase cost that is already larger than the entire line
revenue before fees, logistics, returns, and ad cost.

It is a second-line guard and does not replace the product-level price-unit guard
on `media_products`.

## Cache Freshness

Realtime dashboard cache freshness must include order-profit and product-cost
state, not only realtime ad snapshots and order-line IDs.

The freshness marker should include:

- `roi_realtime_daily_snapshots` max ID / max snapshot time.
- `dianxiaomi_order_lines` max ID.
- `order_profit_lines` max ID / max updated time.
- `media_products` max updated time.

This prevents stale dashboard cards after a profit backfill, cost correction, or
product cost update.

## Verification

Focused tests:

- `tests/test_order_profit_backfill.py`
- `tests/test_profit_calculation.py`
- `tests/test_order_analytics_realtime_cache.py`

Repository default focused verification may also be run with:

```bash
python scripts/pytest_related.py --base origin/master --run
```
