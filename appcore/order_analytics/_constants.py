"""模块级常量集合。

由 ``appcore.order_analytics`` package 在 PR 1.1b 从单文件抽出，字面量、
字段顺序、类型 100% 与原 ``order_analytics.py`` 一致。后续 sub-module
通过 ``from ._constants import _XXX`` 引用；``__init__.py`` 用显式
re-export 把它们带回 ``appcore.order_analytics`` 命名空间。
"""
from __future__ import annotations

import re

META_ATTRIBUTION_CUTOVER_HOUR_BJ = 16
META_ATTRIBUTION_TIMEZONE = "Asia/Shanghai"

# Shopify CSV 列名映射
_SHOPIFY_COLS = {
    "Id":                   "shopify_order_id",
    "Name":                 "order_name",
    "Created at":           "created_at_order",
    "Lineitem name":        "lineitem_name",
    "Lineitem sku":         "lineitem_sku",
    "Lineitem quantity":    "lineitem_quantity",
    "Lineitem price":       "lineitem_price",
    "Billing Country":      "billing_country",
    "Total":                "total",
    "Subtotal":             "subtotal",
    "Shipping":             "shipping",
    "Currency":             "currency",
    "Financial Status":     "financial_status",
    "Fulfillment Status":   "fulfillment_status",
    "Vendor":               "vendor",
}

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_SHOP_TS_FMT = "%Y-%m-%d %H:%M:%S %z"  # "2026-04-22 23:00:14 -0700"

_META_AD_REQUIRED_COLS = [
    "报告开始日期",
    "报告结束日期",
    "广告系列名称",
    "已花费金额 (USD)",
]

_META_AD_NUMERIC_FIELDS: dict[str, tuple[str, str]] = {
    "成效": ("result_count", "int"),
    "已花费金额 (USD)": ("spend_usd", "float"),
    "购物转化价值": ("purchase_value_usd", "float"),
    "广告花费回报 (ROAS) - 购物": ("roas_purchase", "float"),
    "CPM（千次展示费用） (USD)": ("cpm_usd", "float"),
    "单次链接点击费用 - 独立用户 (USD)": ("unique_link_click_cost_usd", "float"),
    "链接点击率": ("link_ctr", "float"),
    "链接点击量": ("link_clicks", "int"),
    "加入购物车次数": ("add_to_cart_count", "int"),
    "结账发起次数": ("initiate_checkout_count", "int"),
    "单次加入购物车费用 (USD)": ("add_to_cart_cost_usd", "float"),
    "单次发起结账费用 (USD)": ("initiate_checkout_cost_usd", "float"),
    "单次成效费用": ("cost_per_result_usd", "float"),
    "平均购物转化价值": ("average_purchase_value_usd", "float"),
    "展示次数": ("impressions", "int"),
    "视频平均播放时长": ("video_avg_play_time", "float"),
}

_DIANXIAOMI_SITE_DOMAINS: dict[str, tuple[str, ...]] = {
    "newjoy": ("newjoyloo.com",),
    "omurio": ("omurio.com", "omurio"),
}
_DIANXIAOMI_EXCLUDED_DOMAINS = ("smartgearx.com", "smartgearx")


_META_AD_SUMMARY_NUMERIC_FIELDS = (
    "result_count",
    "spend_usd",
    "purchase_value_usd",
    "link_clicks",
    "add_to_cart_count",
    "initiate_checkout_count",
    "impressions",
)


COUNTRY_TO_LANG: dict[str, str] = {
    "US": "en", "GB": "en", "UK": "en",
    "AU": "en", "CA": "en", "IE": "en", "NZ": "en",
    "DE": "de", "AT": "de",
    "FR": "fr",
    "ES": "es",
    "IT": "it",
    "NL": "nl",
    "SE": "sv",
    "FI": "fi",
    "JP": "ja",
    "KR": "ko",
    "BR": "pt-BR",
    "PT": "pt",
}


# 同语种多国家时，列输出顺序固定走这张表（不出现的语种按 dict 插入序）
LANG_PRIORITY_COUNTRIES: dict[str, list[str]] = {
    "en": ["US", "GB", "AU", "CA", "IE", "NZ"],
    "de": ["DE", "AT"],
}


_DASHBOARD_SORT_FIELDS = frozenset({"spend", "revenue", "orders", "units", "roas"})
