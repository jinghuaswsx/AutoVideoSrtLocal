import json
import pymysql
import os

def main():
    conn = pymysql.connect(
        host='127.0.0.1', 
        user='root', 
        password='wylf1109', 
        database='auto_video',
        cursorclass=pymysql.cursors.DictCursor
    )
    cur = conn.cursor()
    cur.execute("SELECT state_json FROM projects WHERE id='c7e9e58e-2f79-4d82-bd88-fd0dd42ca784'")
    row = cur.fetchone()
    if not row:
        print("Error: Task not found in remote DB!")
        return

    task = json.loads(row["state_json"])
    
    print("=== Original Images (Total: {}) ===".format(len(task.get("original_images", []))))
    for orig in task.get("original_images", []):
        if orig.get("id") == "detail-18114" or "18114" in orig.get("id", "") or "015" in orig.get("filename", ""):
            print("Found Matching Original Image:")
            print(json.dumps(orig, indent=2))
            lp = orig.get("local_path", "")
            if lp:
                print("  Local Path Exists?", os.path.exists(lp))
                if os.path.exists(lp):
                    print("  File size:", os.path.getsize(lp))
                    
    print("\n=== Item site-14 (Details) ===")
    items = task.get("items", [])
    item_site14 = next((it for it in items if it.get("id") == "site-14"), None)
    if item_site14:
        print(json.dumps(item_site14, indent=2))
        lp = item_site14.get("_local_path", "")
        if lp:
            print("  _local_path Exists?", os.path.exists(lp))
            if os.path.exists(lp):
                print("  File size:", os.path.getsize(lp))
    else:
        print("Error: site-14 not found in items!")
        print("Available item IDs:", [it.get("id") for it in items])

if __name__ == "__main__":
    main()
