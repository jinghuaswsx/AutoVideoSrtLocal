import sys
sys.path.append('/opt/autovideosrt')
from appcore.db import query

def main():
    try:
        # Check logs around 14:33:00
        rows = query("""
            SELECT called_at, source, method, path, status_code, duration_ms
            FROM mingkong_outbound_request_logs
            WHERE called_at >= '2026-06-11 14:30:00' AND called_at <= '2026-06-11 14:40:00'
            ORDER BY called_at ASC
        """)
        print(f"Total requests logged between 14:30 and 14:40: {len(rows)}")
        
        # Group by source and path
        stats = {}
        for r in rows:
            key = (r['source'], r['path'])
            stats[key] = stats.get(key, 0) + 1
            
        print("\nRequest stats by (source, path):")
        for (src, path), count in sorted(stats.items(), key=lambda x: x[1], reverse=True):
            print(f"  {src} -> {path}: {count} requests")
            
        # Print first 20 requests detailed
        print("\nFirst 20 requests details:")
        for r in rows[:20]:
            print(f"  {r['called_at']} | {r['source']} | {r['method']} | {r['path']} | status={r['status_code']} | dur={r['duration_ms']}ms")

        # Print last 20 requests detailed
        print("\nLast 20 requests details:")
        for r in rows[-20:]:
            print(f"  {r['called_at']} | {r['source']} | {r['method']} | {r['path']} | status={r['status_code']} | dur={r['duration_ms']}ms")

    except Exception as e:
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    main()
