import os
import hashlib

downloaded_md5 = "5859c28d3e4c20414d37efba0f53138e"

task_dir = "/opt/autovideosrt/output/link_check/b69aab1d-09b3-45a8-88f9-4dd42fbad2ab"

print("--- COMPARING MD5 OF TASK IMAGES ---")
for folder in ("original", "reference"):
    dir_path = os.path.join(task_dir, folder)
    if os.path.exists(dir_path):
        for filename in os.listdir(dir_path):
            file_path = os.path.join(dir_path, filename)
            with open(file_path, "rb") as f:
                content = f.read()
                file_md5 = hashlib.md5(content).hexdigest()
                is_match = "MATCH!!!" if file_md5 == downloaded_md5 else ""
                print(f"[{folder}] {filename}: Size={len(content)} MD5={file_md5} {is_match}")
