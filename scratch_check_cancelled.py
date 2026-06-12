import sys
sys.path.insert(0, '/opt/autovideosrt')
from appcore.tasks import list_task_center_items

result = list_task_center_items(
    tab='all',
    user_id=33,
    can_process_raw_video=True,
    keyword='',
    high_status='',
    bucket='cancelled',
    page=1,
    page_size=50,
)

print(f"list_task_center_items 返回总数: {result['total']}")
for item in result['items']:
    print(f"ID: {item['id']}, Product: {item['product_name']}, Status: {item['status']}, Assignee: {item['assignee_username']}")
