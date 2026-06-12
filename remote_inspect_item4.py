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
    items = task.get("items", [])
    
    # 打印第 5 个 item (index 4)
    print("=== Item Index 4 (which is shown as '详情图 #5' in UI) ===")
    if len(items) > 4:
        item = items[4]
        print(json.dumps(item, indent=2))
        lp = item.get("_local_path", "")
        if lp:
            print("  _local_path Exists?", os.path.exists(lp))
            if os.path.exists(lp):
                print("  File size:", os.path.getsize(lp))
                
        orig_match = item.get("original_match", {})
        orig_id = orig_match.get("original_id", "")
        print(f"\nLooking for original_id '{orig_id}' in original_images:")
        orig_images = task.get("original_images", [])
        matched_orig = next((orig for orig in orig_images if orig.get("id") == orig_id), None)
        if matched_orig:
            print("Found in original_images:")
            print(json.dumps(matched_orig, indent=2))
            olp = matched_orig.get("local_path", "")
            if olp:
                print("  original local_path Exists?", os.path.exists(olp))
                if os.path.exists(olp):
                    print("  original File size:", os.path.getsize(olp))
        else:
            print("Error: original_id not found in original_images!")
    else:
        print("Error: items has less than 5 elements!")

if __name__ == "__main__":
    main()
