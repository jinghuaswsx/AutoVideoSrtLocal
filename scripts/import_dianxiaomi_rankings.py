"""
导入店小秘排行榜数据到 dianxiaomi_rankings 表，并自动关联 media_products。

用法：
    python scripts/import_dianxiaomi_rankings.py [--date 2026-04-23] [--file C:/店小秘/top300_raw.json]
"""
import sys
import os
import json
import re
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from appcore.db import execute, query, query_one


def clean_name(name: str) -> str:
    """去掉特殊字符、emoji、多余空格，用于模糊匹配"""
    name = re.sub(r'[^\w\s\-]', '', name)
    name = re.sub(r'\s+', ' ', name).strip().lower()
    return name


def find_media_product(product_name: str) -> int | None:
    """用产品名匹配 media_products，返回 id 或 None"""
    # 1. 精确匹配
    row = query_one(
        "SELECT id FROM media_products WHERE name = %s AND deleted_at IS NULL",
        (product_name,)
    )
    if row:
        return row["id"]

    # 2. 模糊匹配：name LIKE '%关键词%'
    # 取产品名的前 30 个字符作为关键词（去掉品牌前缀）
    keywords = clean_name(product_name)
    if len(keywords) > 40:
        keywords = keywords[:40]

    row = query_one(
        "SELECT id FROM media_products WHERE LOWER(name) LIKE %s AND deleted_at IS NULL LIMIT 1",
        (f"%{keywords}%",)
    )
    if row:
        return row["id"]

    # 3. 用 product_code（Shopify handle）匹配
    # 从 URL 提取 handle
    return None


def find_media_product_by_url(product_url: str) -> int | None:
    """用产品 URL 中的 Shopify handle 匹配 media_products.product_code"""
    if not product_url:
        return None
    match = re.search(r'/products/([^/?#]+)', product_url)
    if not match:
        return None
    handle = match.group(1)
    row = query_one(
        "SELECT id FROM media_products WHERE product_code = %s AND deleted_at IS NULL",
        (handle,)
    )
    return row["id"] if row else None


def import_rankings(rows: list[dict], snapshot_date: str):
    matched = 0
    for i, row in enumerate(rows):
        rank = i + 1
        product_id = row.get("productId", "")
        product_name = row.get("productName", "")
        product_url = row.get("productUrl", "")

        # 尝试关联 media_products
        media_pid = find_media_product_by_url(product_url)
        if not media_pid:
            media_pid = find_media_product(product_name)
        if media_pid:
            matched += 1

        # 解析销售额
        revenue_raw = row.get("revenue", "")
        revenue_main = ""
        revenue_split = ""
        if "均摊" in revenue_raw or "分摊" in revenue_raw:
            sep = "均摊" if "均摊" in revenue_raw else "分摊"
            parts = revenue_raw.split(sep)
            revenue_main = parts[0].replace("付款：", "").replace("毛利：", "").strip()
            revenue_split = parts[1].replace("：", "").replace(":", "").strip() if len(parts) > 1 else ""
        else:
            revenue_main = revenue_raw

        # 解析数值
        def parse_int(val):
            if not val:
                return 0
            val = str(val).replace(",", "").strip()
            try:
                return int(val)
            except ValueError:
                return 0

        execute("""
            INSERT INTO dianxiaomi_rankings
                (product_id, product_name, product_url, store, platform, parent_sku,
                 order_count, sales_count, revenue_main, revenue_split,
                 refund_orders, refund_qty, refund_amt, refund_rate,
                 media_product_id, snapshot_date, rank_position)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                product_name=VALUES(product_name),
                product_url=VALUES(product_url),
                store=VALUES(store),
                platform=VALUES(platform),
                parent_sku=VALUES(parent_sku),
                order_count=VALUES(order_count),
                sales_count=VALUES(sales_count),
                revenue_main=VALUES(revenue_main),
                revenue_split=VALUES(revenue_split),
                refund_orders=VALUES(refund_orders),
                refund_qty=VALUES(refund_qty),
                refund_amt=VALUES(refund_amt),
                refund_rate=VALUES(refund_rate),
                media_product_id=VALUES(media_product_id),
                rank_position=VALUES(rank_position)
        """, (
            product_id, product_name, product_url,
            row.get("store", ""), row.get("platform", ""),
            row.get("parentSku", ""),
            parse_int(row.get("orderCount")),
            parse_int(row.get("salesCount")),
            revenue_main, revenue_split,
            parse_int(row.get("refundOrders")),
            parse_int(row.get("refundQty")),
            row.get("refundAmt", ""),
            row.get("refundRate", ""),
            media_pid, snapshot_date, rank
        ))

    return matched


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="2026-04-23", help="数据快照日期")
    parser.add_argument("--file", default="C:/店小秘/top300_raw.json", help="JSON 数据文件路径")
    args = parser.parse_args()

    with open(args.file, "r", encoding="utf-8") as f:
        rows = json.load(f)

    print(f"加载 {len(rows)} 条数据，快照日期 {args.date}")

    matched = import_rankings(rows, args.date)

    # 统计
    stats = query_one("""
        SELECT COUNT(*) as total,
               SUM(media_product_id IS NOT NULL) as matched
        FROM dianxiaomi_rankings
        WHERE snapshot_date = %s
    """, (args.date,))

    print(f"导入完成：共 {stats['total']} 条，已关联素材库 {stats['matched']} 条，未关联 {stats['total'] - stats['matched']} 条")


if __name__ == "__main__":
    main()
