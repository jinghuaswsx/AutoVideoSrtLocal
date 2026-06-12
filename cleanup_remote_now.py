"""直接连接远程 MySQL 清理 task #1039 的重复 raw_niuma_done 事件"""
import pymysql
import json

# 连接远程数据库
conn = pymysql.connect(
    host="172.16.254.106",
    port=3306,
    user="root",
    password="",  # 尝试空密码，如果不是请填写正确密码
    database="auto_video",
    charset="utf8mb4",
    connect_timeout=10,
)

try:
    with conn.cursor() as cur:
        # 1. 先查看重复情况
        cur.execute("""
            SELECT task_id,
                   JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.subtitle_task_id')) AS subtitle_task_id,
                   COUNT(*) AS cnt
            FROM task_events
            WHERE event_type = 'raw_niuma_done'
            GROUP BY task_id, JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.subtitle_task_id'))
            HAVING cnt > 1
            ORDER BY cnt DESC
        """)
        dupes = cur.fetchall()

        if not dupes:
            print("没有找到重复的 raw_niuma_done 事件")
        else:
            total_to_delete = sum(row[2] - 1 for row in dupes)
            print(f"找到 {len(dupes)} 个重复组合，共需删除 {total_to_delete} 条:")
            for row in dupes:
                print(f"  task_id={row[0]}, subtitle_task_id={row[1]}, count={row[2]} (保留1条，删{row[2]-1}条)")

            # 2. 执行清理
            print(f"\n开始清理...")
            deleted_total = 0
            for row in dupes:
                task_id = row[0]
                subtitle_task_id = row[1]

                # 找到保留的那条
                cur.execute("""
                    SELECT id FROM task_events
                    WHERE task_id = %s
                      AND event_type = 'raw_niuma_done'
                      AND JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.subtitle_task_id')) = %s
                    ORDER BY id ASC
                    LIMIT 1
                """, (task_id, subtitle_task_id))
                first = cur.fetchone()
                if not first:
                    continue
                keep_id = first[0]

                # 删除其他重复
                cur.execute("""
                    DELETE FROM task_events
                    WHERE task_id = %s
                      AND event_type = 'raw_niuma_done'
                      AND JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.subtitle_task_id')) = %s
                      AND id != %s
                """, (task_id, subtitle_task_id, keep_id))
                deleted = cur.rowcount
                deleted_total += deleted
                print(f"  task_id={task_id}: 保留 id={keep_id}, 删除 {deleted} 条")

            conn.commit()
            print(f"\n共删除 {deleted_total} 条重复事件")

            # 3. 验证
            cur.execute("SELECT COUNT(*) FROM task_events WHERE event_type = 'raw_niuma_done'")
            remaining = cur.fetchone()[0]
            print(f"清理后 raw_niuma_done 事件总数: {remaining}")

finally:
    conn.close()
