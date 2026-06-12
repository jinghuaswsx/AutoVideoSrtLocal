import json
import pymysql

def main():
    conn = pymysql.connect(
        host='127.0.0.1',
        user='root',
        password='wylf1109',
        database='auto_video',
        cursorclass=pymysql.cursors.DictCursor
    )
    cur = conn.cursor()
    
    # Get all tables
    cur.execute("SHOW TABLES")
    tables = [list(row.values())[0] for row in cur.fetchall()]
    
    target_str = 'face-framing-layered-wig-collection'
    print(f"Scanning all tables for: '{target_str}'")
    
    for table in tables:
        # Get text/varchar/json columns for this table
        cur.execute(f"DESCRIBE `{table}`")
        cols = cur.fetchall()
        search_cols = []
        for col in cols:
            col_type = col['Type'].lower()
            if 'char' in col_type or 'text' in col_type or 'json' in col_type or 'varchar' in col_type:
                search_cols.append(col['Field'])
        
        if not search_cols:
            continue
            
        # Construct query
        where_clauses = [f"`{col}` LIKE %s" for col in search_cols]
        query = f"SELECT * FROM `{table}` WHERE " + " OR ".join(where_clauses)
        params = [f"%{target_str}%"] * len(search_cols)
        
        try:
            cur.execute(query, params)
            rows = cur.fetchall()
            if rows:
                print(f"\n[Table: {table}] Found {len(rows)} rows:")
                for r in rows:
                    # Print primary key or some descriptive columns
                    descriptive = {}
                    for k, v in r.items():
                        if k in ('id', 'media_product_id', 'product_id', 'media_item_id', 'product_code', 'mk_id', 'video_name', 'filename', 'video_path', 'source_raw_id'):
                            descriptive[k] = v
                        elif 'title' in k or 'name' in k or 'url' in k or 'handle' in k:
                            descriptive[k] = str(v)[:150]
                    print("  ", descriptive)
        except Exception as e:
            # Skip tables with issues or regex mismatch
            pass

if __name__ == "__main__":
    main()
