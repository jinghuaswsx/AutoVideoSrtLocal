-- 实时大盘 / 新品投放分析 overview 跨进程缓存表。
-- 取代进程内存 dict：生产 gunicorn 多 worker 各有独立内存缓存、预热只填一个进程，
-- 用户并发请求落到未填充的 worker 大量 MISS 现算（根因，2026-06-14 实测坐实）。
-- 改用本表后所有 worker 共享同一份缓存。per-range TTL 存为 expires_at。
-- Docs anchor: docs/superpowers/specs/2026-06-14-realtime-dashboard-load-optimization-design.md

CREATE TABLE IF NOT EXISTS roi_realtime_overview_cache (
  cache_key VARCHAR(48) NOT NULL PRIMARY KEY,
  payload LONGTEXT NOT NULL,
  expires_at DATETIME(3) NOT NULL,
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  KEY idx_expires_at (expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
