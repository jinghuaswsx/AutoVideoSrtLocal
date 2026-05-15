# Meta Hot Posts Today New Tab Design

## Context

Operators need a quick way to see Meta hot posts that were first discovered by today's sync. The source post publish time (`creation_time`) is not the right signal because an older Facebook post can be newly discovered today. The table already has `first_seen_at`, which records the first time this system inserted the hot post.

## Design

- Add a Meta hot posts sub tab named `今日新增`.
- Back the tab with `GET /xuanpin/api/meta-hot-posts/today-new`.
- Select rows where `meta_hot_posts.first_seen_at` is within the server's current day.
- Reuse the same card rendering and hydration as the material library.
- Order today's new rows by latest discovery first, then interaction growth, then id.

## SQL Semantics

```sql
WHERE p.first_seen_at >= CURDATE()
  AND p.first_seen_at < DATE_ADD(CURDATE(), INTERVAL 1 DAY)
ORDER BY p.first_seen_at DESC,
         COALESCE(p.sync_period_likes, 0) DESC,
         p.id DESC
```

## Verification

- Store test covers the `first_seen_at` filter and ordering.
- Service test covers card hydration.
- Route test covers the new admin API.
- Template test covers the new sub tab and loader.
