import json
import os
import sys
from pathlib import Path

# 添加当前目录以确保能 import appcore
sys.path.insert(0, str(Path(__file__).resolve().parent))

from appcore.db import query_one

def main():
    task_id = "c7e9e58e-2f79-4d82-bd88-fd0dd42ca784"
    row = query_one("SELECT state_json FROM projects WHERE id = %s", (task_id,))
    if not row:
        print("Error: Task not found in database!")
        return

    task = json.loads(row["state_json"])
    
    print("Task ID:", task.get("id"))
    print("Task Status:", task.get("status"))
    print("\n--- Original Images in Task ---")
    orig_images = task.get("original_images", [])
    for orig in orig_images:
        print(f"ID: {orig.get('id')}, Filename: {orig.get('filename')}, Local Path: {orig.get('local_path')}")
        if orig.get('local_path'):
            exists = os.path.exists(orig.get('local_path'))
            print(f"  -> File exists locally? {exists}")
            
    print("\n--- Items ---")
    items = task.get("items", [])
    print(f"Total items: {len(items)}")
    for idx, item in enumerate(items):
        print(f"\nItem #{idx+1} (ID: {item.get('id')}, Kind: {item.get('kind')}):")
        print(f"  Source URL: {item.get('source_url')}")
        print(f"  Site Preview URL: {item.get('site_preview_url')}")
        print(f"  _local_path: {item.get('_local_path')}")
        if item.get('_local_path'):
            exists = os.path.exists(item.get('_local_path'))
            print(f"    -> File exists locally? {exists}")
        
        orig_match = item.get("original_match", {})
        print(f"  Original Match: {orig_match}")
        
        ref_match = item.get("reference_match", {})
        print(f"  Reference Match: {ref_match}")

if __name__ == "__main__":
    main()
