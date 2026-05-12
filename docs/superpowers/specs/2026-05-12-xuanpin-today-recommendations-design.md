# Xuanpin Today Recommendations Design

## Goal

Build a minimal "今日推荐" lane in 选品中心 that can be populated by an operator-run AI script. The first delivery is pragmatic: scan the latest 店小秘 Top500 listing sales rows, fetch matching 明空素材, ask Gemini Flash-Lite to pick 20 product opportunities, and store up to 100 material recommendations for review the next day.

## Scope

- Add a Today Recommendations tab under `/xuanpin/today-recommendations`.
- Store recommendation runs and material-level recommendation rows in MySQL.
- Hide adopted recommendations from the default list.
- Allow admins to select recommendation rows, choose a translator, and create task-center parent/child tasks from the recommended countries.
- Provide a server-side script for the first AI batch. It is intentionally operator-driven, not a hard daily pipeline.

## Data Flow

1. Read the latest available `dianxiaomi_rankings` snapshot and take the top 500 rows.
2. Derive the Shopify handle from each listing URL.
3. Query 明空 `/api/marketing/medias` with that handle using the existing wedev credentials.
4. Keep candidates with visible videos, summarize sales, ads, spend, copy, and top video metadata.
5. Batch candidates through Gemini Flash-Lite and run a final selection pass for 20 products.
6. Store selected products and up to five videos per product in `xuanpin_today_recommendations`.
7. The UI lists pending recommendations. Adoption imports/caches the selected video, creates a local `media_products`/`media_items` record when needed, creates task-center tasks, and marks the recommendation adopted.

## Autonomous Decisions

- If 明空 returns multiple rows for the same handle, prefer exact link-tail matches, then rows with more visible videos, higher material spend, and newer IDs.
- If Gemini returns invalid JSON or is unavailable, the script can fall back to deterministic scoring so the library still has reviewable candidates, but the run summary must mark the fallback.
- Countries are stored as lowercase language codes in the recommendation row and converted to uppercase task country codes at adoption.
- Adoption is material-level: adopting one recommended video hides that row; other material rows for the same product may remain unless selected.

## Verification

- Unit tests cover data serialization, listing filters, adoption handoff, route permissions, and migration shape.
- Server verification must run against the production/test server DB only. Windows local MySQL is not allowed for this project.
