"""分析 task #1039 的 2472 条 events 构成"""
import requests, re, json
from collections import Counter

BASE = "http://172.16.254.106"
session = requests.Session()

# 登录
login_page = session.get(f"{BASE}/login", timeout=15)
csrf_match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', login_page.text)
csrf = csrf_match.group(1) if csrf_match else ""
session.post(f"{BASE}/login", data={"username": "admin", "password": "709709@", "csrf_token": csrf}, timeout=15, allow_redirects=True)

# 获取 events
print("获取 events...")
resp = session.get(f"{BASE}/tasks/api/1039/events", timeout=60)
data = resp.json()
events = data.get("events", [])
print(f"总数: {len(events)}")
print(f"原始大小: {len(resp.content)/1024:.1f}KB\n")

# 按 event_type 统计
type_counts = Counter(e.get("event_type", "unknown") for e in events)
print("=== 按事件类型统计 ===")
for event_type, count in type_counts.most_common():
    pct = count / len(events) * 100
    flag = " <== 异常多!" if count > 100 else ""
    print(f"  {event_type}: {count} ({pct:.1f}%){flag}")

# 按日期统计
print("\n=== 按创建日期统计 ===")
date_counts = Counter((e.get("created_at") or "")[:10] for e in events)
for date, count in sorted(date_counts.items()):
    print(f"  {date}: {count}")

# 检查 payload_json 大小
print("\n=== Payload 大小分析 ===")
payload_sizes = []
for e in events:
    p = e.get("payload_json")
    if p:
        if isinstance(p, str):
            payload_sizes.append(len(p))
        else:
            payload_sizes.append(len(json.dumps(p)))
    else:
        payload_sizes.append(0)

total_payload = sum(payload_sizes)
avg_payload = total_payload / len(payload_sizes)
max_payload = max(payload_sizes)
print(f"  Payload 总计: {total_payload/1024:.1f}KB")
print(f"  Payload 平均: {avg_payload:.0f} bytes")
print(f"  Payload 最大: {max_payload} bytes")

# 找 payload 最大的前5条
print("\n=== Payload 最大的5条 event ===")
sorted_events = sorted(enumerate(events), key=lambda x: payload_sizes[x[0]], reverse=True)
for idx, e in sorted_events[:5]:
    p = e.get("payload_json")
    p_str = p if isinstance(p, str) else json.dumps(p)
    print(f"  #{idx} type={e.get('event_type')}, created={e.get('created_at')}, payload={len(p_str)} bytes")
    if len(p_str) < 500:
        print(f"    payload: {p_str[:200]}")

# 检查是否有大量重复事件
print("\n=== 最近50条事件（看是否有重复模式）===")
for e in events[-50:]:
    p_str = e.get("payload_json", "")
    if isinstance(p_str, dict):
        p_str = json.dumps(p_str)
    p_preview = (str(p_str)[:80]) if p_str else "(empty)"
    print(f"  {e.get('created_at')} | {e.get('event_type'):30s} | {e.get('actor_username', '?')} | {p_preview}")

# 对比：检查另一个普通任务有多少 events
print("\n=== 对比：其他任务的 events 数量 ===")
test_tasks = [1228, 1066, 1065, 1064]  # 从之前 page=1/2 看到的任务
for tid in test_tasks:
    try:
        r = session.get(f"{BASE}/tasks/api/{tid}/events", timeout=15)
        d = r.json()
        count = len(d.get("events", []))
        size = len(r.content)
        flag = " <== 也很大!" if count > 100 else ""
        print(f"  task #{tid}: {count} events, {size/1024:.1f}KB{flag}")
    except Exception as ex:
        print(f"  task #{tid}: error - {ex}")

print("\n完成!")
