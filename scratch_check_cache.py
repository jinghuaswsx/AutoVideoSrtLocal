import sys
sys.path.append('/opt/autovideosrt')
from appcore import db
import time

def check():
    threshold_value = 1.4
    conditions = [
        "c.ad_roas IS NOT NULL",
        "c.ad_roas < %(threshold)s",
        "c.active_7d_ad_spend_usd > 0",
        "c.ad_spend_usd > 0",
    ]
    where_clause = " AND ".join(conditions)
    sql = f"""
        SELECT c.product_id, c.lang, c.ad_spend_usd, c.purchase_value_usd,
               c.ad_roas, c.active_7d_ad_spend_usd, c.computed_at,
               p.product_code, p.name AS product_name
        FROM media_product_lang_ad_summary_cache c
        JOIN media_products p ON p.id = c.product_id AND p.deleted_at IS NULL
        WHERE {where_clause}
        ORDER BY c.ad_roas ASC, c.active_7d_ad_spend_usd DESC
    """
    rows = db.query(sql, {"threshold": threshold_value})
    print(f"Total rows: {len(rows)}")
    
    slow_count = 0
    total_time = 0
    
    for idx, r in enumerate(rows):
        pid = r['product_id']
        lang = r['lang']
        t0 = time.time()
        
        # Run active window query
        db.query_one(
            """
            SELECT
              MIN(DATE(COALESCE(m.meta_business_date, m.report_date))) AS delivery_start,
              MAX(DATE(COALESCE(m.meta_business_date, m.report_date))) AS delivery_end,
              COUNT(DISTINCT DATE(COALESCE(m.meta_business_date, m.report_date))) AS active_days
            FROM meta_ad_daily_ad_metrics m
            JOIN media_items i
              ON i.product_id = m.product_id
             AND i.deleted_at IS NULL
             AND LOWER(i.lang) = %(lang)s
             AND (
               m.ad_name LIKE CONCAT('%%', i.filename, '%%')
               OR m.normalized_ad_code LIKE CONCAT('%%', i.filename, '%%')
               OR (i.display_name IS NOT NULL AND i.display_name <> '' AND m.ad_name LIKE CONCAT('%%', i.display_name, '%%'))
               OR (i.display_name IS NOT NULL AND i.display_name <> '' AND m.normalized_ad_code LIKE CONCAT('%%', i.display_name, '%%'))
             )
            WHERE m.product_id = %(product_id)s
              AND COALESCE(m.spend_usd, 0) > 0
            """,
            {"product_id": pid, "lang": lang}
        )
        
        t_active = time.time() - t0
        
        # Run trend series query
        t1 = time.time()
        db.query(
            """
            SELECT
              DATE(COALESCE(m.meta_business_date, m.report_date)) AS ad_date,
              COALESCE(SUM(COALESCE(m.spend_usd, 0)), 0) AS spend_usd,
              COALESCE(SUM(COALESCE(m.purchase_value_usd, 0)), 0) AS purchase_value_usd
            FROM meta_ad_daily_ad_metrics m
            JOIN media_items i
              ON i.product_id = m.product_id
             AND i.deleted_at IS NULL
             AND LOWER(i.lang) = %(lang)s
             AND (
               m.ad_name LIKE CONCAT('%%', i.filename, '%%')
               OR m.normalized_ad_code LIKE CONCAT('%%', i.filename, '%%')
               OR (i.display_name IS NOT NULL AND i.display_name <> '' AND m.ad_name LIKE CONCAT('%%', i.display_name, '%%'))
               OR (i.display_name IS NOT NULL AND i.display_name <> '' AND m.normalized_ad_code LIKE CONCAT('%%', i.display_name, '%%'))
             )
            WHERE m.product_id = %(product_id)s
              AND COALESCE(m.spend_usd, 0) > 0
              AND DATE(COALESCE(m.meta_business_date, m.report_date)) >= DATE_SUB(CURDATE(), INTERVAL 14 DAY)
              AND DATE(COALESCE(m.meta_business_date, m.report_date)) < CURDATE()
            GROUP BY ad_date
            ORDER BY ad_date DESC
            """,
            {"product_id": pid, "lang": lang}
        )
        t_trend = time.time() - t1
        
        duration = t_active + t_trend
        total_time += duration
        
        if duration > 0.05:
            print(f"Row {idx} (pid={pid}, lang={lang}) is SLOW: active={t_active:.4f}s, trend={t_trend:.4f}s. Total={duration:.4f}s")
            slow_count += 1
            
        if idx >= 100:
            print("Checked 100 rows. Stopping check.")
            break
            
    print(f"\nSummary: checked {idx+1} rows, {slow_count} rows took > 0.05s. Total time: {total_time:.4f}s")

if __name__ == '__main__':
    check()
