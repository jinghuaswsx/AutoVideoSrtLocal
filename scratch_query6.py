import sys
sys.path.append('/opt/autovideosrt')
import requests
from appcore.pushes import get_localized_texts_base_url, build_localized_texts_headers
import json

base_url = get_localized_texts_base_url()
headers = dict(build_localized_texts_headers())
headers.pop("Content-Type", None)
headers["Accept"] = "application/json"

url = f"{base_url}/api/marketing/medias/315"
print("Sending direct ID query to:", url)
try:
    resp = requests.get(url, headers=headers, timeout=90)
    print("Status code:", resp.status_code)
    payload = resp.json() or {}
    item = (payload.get("data") or {}).get("item") or {}
    print(json.dumps(item, indent=2, ensure_ascii=False))
except Exception as e:
    print("Request failed:", e)
