import sys
import os
import json
import uuid
import shutil
from pathlib import Path

# Make sure we can import appcore from production
sys.path.insert(0, '/opt/autovideosrt')

from appcore.link_check_runtime import LinkCheckRuntime
from appcore import task_state

old_task_id = 'b69aab1d-09b3-45a8-88f9-4dd42fbad2ab'
new_task_id = str(uuid.uuid4())

print(f"--- Cloning Task {old_task_id} to Fresh Task {new_task_id} ---")
old_task = task_state.get(old_task_id)
if not old_task:
    print("Error: Old task not found!")
    sys.exit(1)

# Create a new task directory
old_task_dir = Path(old_task["task_dir"])
new_task_dir = old_task_dir.parent / new_task_id
new_task_dir.mkdir(parents=True, exist_ok=True)

# Copy reference and original images if they exist
if (old_task_dir / "reference").exists():
    shutil.copytree(old_task_dir / "reference", new_task_dir / "reference")
if (old_task_dir / "original").exists():
    shutil.copytree(old_task_dir / "original", new_task_dir / "original")

# Update paths in reference/original images list
new_references = []
for ref in old_task.get("reference_images", []):
    new_ref = dict(ref)
    new_ref["local_path"] = str(new_task_dir / "reference" / Path(ref["local_path"]).name)
    new_references.append(new_ref)

new_originals = []
for orig in old_task.get("original_images", []):
    new_orig = dict(orig)
    new_orig["local_path"] = str(new_task_dir / "original" / Path(orig["local_path"]).name)
    new_originals.append(new_orig)

valid_user_id = old_task.get("_user_id") or old_task.get("user_id") or 1
print(f"Valid User ID found: {valid_user_id}")

# Create a fresh task in store / state
new_task = {
    "id": new_task_id,
    "type": "link_check",
    "status": "pending",
    "link_url": old_task.get("link_url", ""),
    "resolved_url": "",
    "page_language": "",
    "target_language": old_task.get("target_language", ""),
    "target_language_name": old_task.get("target_language_name", ""),
    "task_dir": str(new_task_dir),
    "reference_images": new_references,
    "original_images": new_originals,
    "steps": {
        "lock_locale": "pending",
        "download": "pending",
        "analyze": "pending",
        "summarize": "pending",
    },
    "step_messages": {
        "lock_locale": "",
        "download": "",
        "analyze": "",
        "summarize": "",
    },
    "items": [],
    "progress": {
        "total": 0,
        "downloaded": 0,
        "analyzed": 0,
        "compared": 0,
        "binary_checked": 0,
        "same_image_llm_done": 0,
        "failed": 0
    },
    "error": "",
    "created_at": old_task.get("created_at"),
    "_user_id": valid_user_id,
    "display_name": old_task.get("display_name", "Fresh Verification Task")
}

# Safely insert task and write to SQLite DB
task_state._tasks[new_task_id] = new_task
task_state._db_upsert(new_task_id, valid_user_id, new_task, "")

print(f"Fresh Task {new_task_id} successfully created and persisted.")
print("--- Running Fresh Link Check Audit ---")

runtime = LinkCheckRuntime()
runtime.start(new_task_id)

print("\n--- Fresh Task State Post-Audit ---")
fresh_task = task_state.get(new_task_id)
print(f"Task ID: {fresh_task['id']}")
print(f"Target Lang: {fresh_task['target_language']}")
print(f"Status: {fresh_task['status']}")

print("\n--- Image Analysis Decisions ---")
for item in fresh_task.get('items', []):
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
    print(f"  Reference Match Score: {item.get('reference_match', {}).get('score')}")
    print(f"  Original Match Score: {item.get('original_match', {}).get('score')}")
