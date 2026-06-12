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
    
    print("=== Task Main Info ===")
    print("ID:", task.get("id"))
    print("Type:", task.get("type"))
    print("Status:", task.get("status"))
    print("Page Language:", task.get("page_language"))
    print("Target Language:", task.get("target_language"))
    
    print("\n=== Reference Images ===")
    for ref in task.get("reference_images", []):
        lp = ref.get("local_path", "")
        exists = os.path.exists(lp) if lp else False
        print(f"ID: {ref.get('id')}, Filename: {ref.get('filename')}, Local Path: {lp} (Exists? {exists})")
        
    print("\n=== Original Images ===")
    for orig in task.get("original_images", []):
        lp = orig.get("local_path", "")
        exists = os.path.exists(lp) if lp else False
        print(f"ID: {orig.get('id')}, Filename: {orig.get('filename')}, Local Path: {lp} (Exists? {exists})")

    print("\n=== Items (Landing Page Images) ===")
    items = task.get("items", [])
    print(f"Total items: {len(items)}")
    for idx, item in enumerate(items):
        print(f"\nItem #{idx+1} (ID: {item.get('id')}, Kind: {item.get('kind')}):")
        print(f"  Source URL: {item.get('source_url')}")
        print(f"  Site Preview URL: {item.get('site_preview_url')}")
        print(f"  _local_path: {item.get('_local_path')} (Exists? {os.path.exists(item.get('_local_path')) if item.get('_local_path') else False})")
        
        orig_match = item.get("original_match", {})
        print(f"  Original Match: {orig_match}")
        
        ref_match = item.get("reference_match", {})
        print(f"  Reference Match: {ref_match}")

if __name__ == "__main__":
    main()
