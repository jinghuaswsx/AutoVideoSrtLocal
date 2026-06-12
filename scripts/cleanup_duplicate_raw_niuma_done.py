"""清理重复的 raw_niuma_done 事件。

每次 reconcile_inflight_niuma_processing 调用 attach_niuma_result_to_parent_task
时，若 attach_niuma_result_to_parent_task 中的 mark_uploaded 因权限不匹配失败，
任务状态不会从 raw_in_progress 变更，导致下一次 reconciliation 再次写入 raw_niuma_done，
形成每分钟一条的重复事件。这些重复事件使 events API 返回数 MB 数据，导致浏览器卡死。

本脚本按 (task_id, subtitle_task_id) 去重，每个组合只保留最早的一条 raw_niuma_done。

用法（在服务器上执行）：
    cd /opt/AutoVideoSrtLocal
    sudo -u www-data python3 scripts/cleanup_duplicate_raw_niuma_done.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from appcore.db import query_all, execute

EVENT_TYPE = "raw_niuma_done"


def main():
    # Step 1: 统计当前重复情况
    dupes = query_all(
        """
        SELECT task_id,
               JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.subtitle_task_id')) AS subtitle_task_id,
               COUNT(*) AS cnt
        FROM task_events
        WHERE event_type = %s
        GROUP BY task_id, JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.subtitle_task_id'))
        HAVING cnt > 1
        ORDER BY cnt DESC
        """,
        (EVENT_TYPE,),
    )

    if not dupes:
        print("✓ 没有重复的 raw_niuma_done 事件，无需清理。")
        return

    total_dupes = sum(int(row["cnt"]) - 1 for row in dupes)
    print(f"发现 {len(dupes)} 个 (task_id, subtitle_task_id) 组合存在重复：")
    for row in dupes:
        print(f"  task_id={row['task_id']}, subtitle_task_id={row['subtitle_task_id']}, "
              f"重复数={row['cnt']} (将删除 {int(row['cnt']) - 1} 条)")

    # Step 2: 删除重复（保留最早的）
    print(f"\n共将删除 {total_dupes} 条重复事件...")
    for row in dupes:
        task_id = int(row["task_id"])
        subtitle_task_id = row["subtitle_task_id"]
        # 找到最早的那条 raw_niuma_done 的 id
        first = query_all(
            """
            SELECT id FROM task_events
            WHERE task_id = %s
              AND event_type = %s
              AND JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.subtitle_task_id')) = %s
            ORDER BY id ASC
            LIMIT 1
            """,
            (task_id, EVENT_TYPE, subtitle_task_id),
        )
        if not first:
            continue
        keep_id = int(first[0]["id"])

        deleted = execute(
            """
            DELETE FROM task_events
            WHERE task_id = %s
              AND event_type = %s
              AND JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.subtitle_task_id')) = %s
              AND id != %s
            """,
            (task_id, EVENT_TYPE, subtitle_task_id, keep_id),
        )
        print(f"  task_id={task_id}: 保留 id={keep_id}, 删除了 {deleted} 条")

    # Step 3: 验证
    remaining = query_all(
        """
        SELECT COUNT(*) AS cnt
        FROM task_events
        WHERE event_type = %s
        """,
        (EVENT_TYPE,),
    )
    print(f"\n✓ 清理完成。raw_niuma_done 事件总数: {remaining[0]['cnt']}")


if __name__ == "__main__":
    main()
