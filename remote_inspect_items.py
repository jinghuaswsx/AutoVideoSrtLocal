import sys
import json

sys.path.insert(0, '/opt/autovideosrt')
from appcore import task_state

task_id = '0b630e09-2eb9-456b-82f6-38bfeebe8518'
task = task_state.get(task_id)

if not task:
    print("Task not found!")
    sys.exit(1)

print("=== Task resolved URL ===")
print("resolved_url:", task.get("resolved_url"))
print("page_language:", task.get("page_language"))

print("\n=== ALL ITEMS IN SQLite TASK STATE ===")
for idx, item in enumerate(task.get("items", [])):
    print(f"\nItem #{idx} (ID: {item.get('id')}, Kind: {item.get('kind')}):")
    print(f"  source_url: {item.get('source_url')}")
    print(f"  resolved_source_url: {item.get('resolved_source_url')}")
    print(f"  local_path: {item.get('_local_path')}")
    print(f"  decision: {item.get('analysis', {}).get('decision')}")
    print(f"  quality_reason: {item.get('analysis', {}).get('quality_reason')}")
    print(f"  reference_match: {item.get('reference_match')}")
    print(f"  original_match: {item.get('original_match')}")
