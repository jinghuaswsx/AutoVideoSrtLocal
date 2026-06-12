import sys
sys.path.append('/opt/autovideosrt')
from appcore.db import query

def main():
    try:
        # Group by source
        rows = query("""
            SELECT source, COUNT(*) AS cnt 
            FROM mingkong_outbound_request_logs 
            WHERE called_at >= '2026-06-11 14:33:00' AND called_at < '2026-06-11 14:34:00'
            GROUP BY source
        """)
        print("14:33:00-14:34:00 requests by source:")
        for r in rows:
            print(f"  {r['source']}: {r['cnt']}")
            
        # Group by path for meta_hot_posts.client
        rows_path = query("""
            SELECT path, COUNT(*) AS cnt 
            FROM mingkong_outbound_request_logs 
            WHERE called_at >= '2026-06-11 14:33:00' AND called_at < '2026-06-11 14:34:00'
              AND source = 'meta_hot_posts.client'
            GROUP BY path
        """)
        print("\n14:33:00-14:34:00 meta_hot_posts.client requests by path:")
        for r in rows_path:
            print(f"  {r['path']}: {r['cnt']}")

        # Group by path for other sources
        rows_other = query("""
            SELECT source, path, COUNT(*) AS cnt 
            FROM mingkong_outbound_request_logs 
            WHERE called_at >= '2026-06-11 14:33:00' AND called_at < '2026-06-11 14:34:00'
              AND source <> 'meta_hot_posts.client'
            GROUP BY source, path
            ORDER BY cnt DESC
            LIMIT 10
        """)
        print("\n14:33:00-14:34:00 other sources top 10 requests:")
        for r in rows_other:
            print(f"  {r['source']} -> {r['path']}: {r['cnt']}")

    except Exception as e:
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    main()
