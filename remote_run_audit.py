import sys
import os
import json
import logging

logging.basicConfig(level=logging.INFO)

# Make sure we can import appcore
sys.path.insert(0, '/opt/autovideosrt')

from appcore.link_check_runtime import LinkCheckRuntime
from appcore import task_state

task_id = 'b69aab1d-09b3-45a8-88f9-4dd42fbad2ab'

print(f"--- Triggering Link Check Audit for Task {task_id} ---")
runtime = LinkCheckRuntime()
runtime.start(task_id)

print("\n--- Reading Task State Post-Audit ---")
task = task_state.get(task_id)
if not task:
    print("Error: Task not found in DB!")
    sys.exit(1)

print(f"Task ID: {task['id']}")
print(f"Target Lang: {task['target_language']}")
print(f"Status: {task['status']}")

print("\n--- Image Analysis Decisions ---")
for item in task.get('items', []):
    img_id = item.get("id")
    kind = item.get("kind")
    source_url = item.get("source_url")
    analysis = item.get("analysis", {})
    decision = analysis.get("decision")
    reason = analysis.get("quality_reason")
    
    print(f"ID: {img_id} ({kind})")
    print(f"  Source URL: {source_url}")
    print(f"  Decision: {decision}")
    print(f"  Reason: {reason}")
    print(f"  Reference Match: {item.get('reference_match')}")
    print(f"  Original Match: {item.get('original_match')}")
