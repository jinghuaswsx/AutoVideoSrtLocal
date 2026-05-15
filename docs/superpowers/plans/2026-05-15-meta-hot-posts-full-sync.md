# Meta Hot Posts Full Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Change Meta hot posts daily sync from a 500-row sample to the full upstream result set, then let existing analysis/localization/Europe-fit jobs drain the complete dataset.

**Architecture:** The daily sync should read `/api/spy/hot/posts` until the upstream reported `total` is reached or the first empty page is returned, with a defensive page cap. Existing upsert logic remains the dedupe boundary, and existing 10-minute workers continue processing pending product analysis, video download, copyability, and Europe-fit queues.

**Tech Stack:** Python 3.12, APScheduler, MySQL on server only, Flask service layer, existing `MetaHotPostsClient`, pytest.

---

## Context

Live read-only upstream probe on 2026-05-15 with the current production filters:

- Endpoint: `/api/spy/hot/posts`
- Params: `period_hours=72`, `fans_max=10000`, `ads_min=5`, `creatives_min=5`
- Upstream reported `total=2307`, `size=30`
- Pages 1-76 returned 30 rows, page 77 returned 27 rows, page 78 returned 0 rows
- Unique upstream IDs observed: 2307
- Current daily sync stops at `target_count=500`, so it only captures the first 17 pages

The implementation must not use Windows local MySQL. DB verification must run on the server.

## File Map

- Modify `appcore/meta_hot_posts/scheduler.py`
  - Make sync default to full upstream capture.
  - Add stop reasons and reported total to scheduled run summary.
- Modify `appcore/meta_hot_posts/service.py`
  - Make manual sync API use the same full-sync default.
- Modify `tools/meta_hot_posts/main.py`
  - Let CLI run full sync with `--target-count 0`.
- Modify `appcore/scheduled_tasks.py`
  - Update task registry copy from “目标采集 500 条” to “按接口全集采集”.
- Modify `tests/test_meta_hot_posts_scheduler.py`
  - Cover full-sync pagination, empty page stop, target-count compatibility, and scheduler defaults.
- Modify `tests/test_meta_hot_posts_routes.py`
  - Cover manual sync delegates to the full-sync default if existing route tests expose it.
- Create `docs/superpowers/specs/2026-05-15-meta-hot-posts-full-sync-design.md`
  - Document the production behavior and capacity assumptions.

---

### Task 1: Add Design Spec Anchor

**Files:**
- Create: `docs/superpowers/specs/2026-05-15-meta-hot-posts-full-sync-design.md`

- [ ] **Step 1: Create the design spec**

Add this file:

```markdown
# Meta 热帖全集同步设计

日期：2026-05-15

## 背景

Meta 热帖同步任务当前每天 07:00 只采集 500 条。线上只读探测显示，在当前筛选条件下，上游接口返回 total=2307、size=30，第 77 页结束，第 78 页为空。500 条只覆盖前 17 页，不足以支持“今日新增”和欧洲投放评估覆盖全集素材。

## 口径

- 每天 07:00 同步 `/api/spy/hot/posts` 当前筛选条件下的全集。
- 同步停止条件按优先级：
  1. 上游返回空 items；
  2. 已写入数量达到上游首个有效 `total`；
  3. 达到防御性 `max_pages` 上限。
- `first_seen_at` 仍表示本地首次入库时间，“今日新增”只按当天 `first_seen_at` 展示。
- 商品分析、视频下载、美国可搬运分析、欧洲适配评估沿用现有 10 分钟队列任务，直到 pending 队列清空。

## 默认参数

- `FULL_SYNC_MAX_PAGES = 120`
- 当前 page size 为 30，120 页可覆盖 3600 条，足够覆盖当前 2307 条，并为短期增长留余量。
- 保留 `target_count` 兼容参数，`target_count=None` 或 `target_count<=0` 表示全集。

## 验收

- 每日同步 summary 包含 `reported_total`、`posts`、`pages`、`stop_reason`。
- 在当前接口规模下，同步应写入约 2307 条，stop_reason 为 `reported_total_reached` 或 `empty_page`。
- 若接口增长超过 120 页，summary 必须返回 `stop_reason=max_pages_reached`，方便后台观察并调高上限。
```

- [ ] **Step 2: Commit the spec**

Run:

```bash
git add docs/superpowers/specs/2026-05-15-meta-hot-posts-full-sync-design.md
git commit -m "docs: add Meta hot posts full sync design"
```

Expected: one docs commit.

---

### Task 2: Write Scheduler Pagination Tests

**Files:**
- Modify: `tests/test_meta_hot_posts_scheduler.py`

- [ ] **Step 1: Add a full-sync test**

Append this test near the existing `test_sync_hot_posts_fetches_until_target_count` tests:

```python
def test_sync_hot_posts_full_sync_uses_reported_total(monkeypatch):
    pages = []
    upserts = []
    queued = []

    class FakeClient:
        def fetch_page(self, *, page, **params):
            pages.append((page, params))
            if page <= 3:
                count = 30
            elif page == 4:
                count = 7
            else:
                count = 0
            start = (page - 1) * 30
            return {
                "total": 97,
                "size": 30,
                "items": [
                    {
                        "wedev_post_id": start + idx + 1,
                        "product_url": f"https://example.com/products/{start + idx + 1}",
                    }
                    for idx in range(count)
                ],
            }

    monkeypatch.setattr(scheduler.store, "upsert_hot_post", lambda item: upserts.append(item))
    monkeypatch.setattr(scheduler.store, "ensure_product_analysis", lambda url: queued.append(url))

    summary = scheduler.sync_hot_posts(client=FakeClient(), target_count=None, max_pages=20)

    assert [page for page, _params in pages] == [1, 2, 3, 4]
    assert len(upserts) == 97
    assert len(queued) == 97
    assert summary["posts"] == 97
    assert summary["reported_total"] == 97
    assert summary["page_size"] == 30
    assert summary["stop_reason"] == "reported_total_reached"
```

- [ ] **Step 2: Add an empty-page stop test**

Add:

```python
def test_sync_hot_posts_full_sync_stops_on_empty_page_before_reported_total(monkeypatch):
    pages = []

    class FakeClient:
        def fetch_page(self, *, page, **params):
            pages.append(page)
            if page == 1:
                return {
                    "total": 200,
                    "size": 30,
                    "items": [{"wedev_post_id": 1, "product_url": ""}],
                }
            return {"total": 200, "size": 30, "items": []}

    monkeypatch.setattr(scheduler.store, "upsert_hot_post", lambda item: None)
    monkeypatch.setattr(scheduler.store, "ensure_product_analysis", lambda url: None)

    summary = scheduler.sync_hot_posts(client=FakeClient(), target_count=None, max_pages=20)

    assert pages == [1, 2]
    assert summary["posts"] == 1
    assert summary["reported_total"] == 200
    assert summary["stop_reason"] == "empty_page"
```

- [ ] **Step 3: Add a max-pages stop test**

Add:

```python
def test_sync_hot_posts_full_sync_reports_max_pages_reached(monkeypatch):
    class FakeClient:
        def fetch_page(self, *, page, **params):
            return {
                "total": 1000,
                "size": 30,
                "items": [
                    {"wedev_post_id": page * 100 + idx, "product_url": ""}
                    for idx in range(30)
                ],
            }

    monkeypatch.setattr(scheduler.store, "upsert_hot_post", lambda item: None)
    monkeypatch.setattr(scheduler.store, "ensure_product_analysis", lambda url: None)

    summary = scheduler.sync_hot_posts(client=FakeClient(), target_count=None, max_pages=2)

    assert summary["pages"] == 2
    assert summary["posts"] == 60
    assert summary["reported_total"] == 1000
    assert summary["stop_reason"] == "max_pages_reached"
```

- [ ] **Step 4: Verify tests fail before implementation**

Run:

```bash
pytest tests/test_meta_hot_posts_scheduler.py::test_sync_hot_posts_full_sync_uses_reported_total tests/test_meta_hot_posts_scheduler.py::test_sync_hot_posts_full_sync_stops_on_empty_page_before_reported_total tests/test_meta_hot_posts_scheduler.py::test_sync_hot_posts_full_sync_reports_max_pages_reached -q
```

Expected: FAIL because `reported_total`, `page_size`, and `stop_reason` are not yet in the summary.

---

### Task 3: Implement Full-Sync Defaults

**Files:**
- Modify: `appcore/meta_hot_posts/scheduler.py`

- [ ] **Step 1: Add constants**

Near the current sync constants, add:

```python
FULL_SYNC_MAX_PAGES = 120
```

- [ ] **Step 2: Replace `sync_hot_posts` with full-sync aware logic**

Replace the current function body with:

```python
def sync_hot_posts(
    *,
    target_count: int | None = None,
    max_pages: int = FULL_SYNC_MAX_PAGES,
    client: MetaHotPostsClient | None = None,
) -> dict[str, Any]:
    api = client or MetaHotPostsClient()
    safe_target = None
    if target_count is not None:
        parsed_target = int(target_count)
        if parsed_target > 0:
            safe_target = max(1, parsed_target)
    safe_max_pages = max(1, int(max_pages))
    summary = {
        "pages": 0,
        "posts": 0,
        "queued_products": 0,
        "target_count": safe_target,
        "reported_total": None,
        "page_size": None,
        "stop_reason": None,
    }
    for page in range(1, safe_max_pages + 1):
        payload = api.fetch_page(
            page=page,
            period_hours=72,
            fans_max=10000,
            ads_min=5,
            creatives_min=5,
        )
        summary["pages"] += 1
        if summary["reported_total"] is None and payload.get("total") is not None:
            summary["reported_total"] = int(payload.get("total") or 0)
        if summary["page_size"] is None and payload.get("size") is not None:
            summary["page_size"] = int(payload.get("size") or 0)

        items = payload.get("items") or []
        if not items:
            summary["stop_reason"] = "empty_page"
            break

        for item in items:
            if safe_target is not None and summary["posts"] >= safe_target:
                summary["stop_reason"] = "target_count_reached"
                break
            store.upsert_hot_post(item)
            summary["posts"] += 1
            if item.get("product_url"):
                store.ensure_product_analysis(str(item["product_url"]))
                summary["queued_products"] += 1

        if summary["stop_reason"]:
            break
        if safe_target is not None and summary["posts"] >= safe_target:
            summary["stop_reason"] = "target_count_reached"
            break
        reported_total = summary.get("reported_total")
        if safe_target is None and reported_total and summary["posts"] >= int(reported_total):
            summary["stop_reason"] = "reported_total_reached"
            break
    if not summary["stop_reason"]:
        summary["stop_reason"] = "max_pages_reached"
    return summary
```

- [ ] **Step 3: Update `sync_tick_once` defaults**

Find `sync_tick_once` and change its signature to:

```python
def sync_tick_once(*, target_count: int | None = None, max_pages: int = FULL_SYNC_MAX_PAGES) -> dict[str, Any]:
```

Keep the existing `scheduled_tasks.start_run` / `finish_run` wrapping.

- [ ] **Step 4: Run scheduler tests**

Run:

```bash
pytest tests/test_meta_hot_posts_scheduler.py -q
```

Expected: PASS.

---

### Task 4: Update Manual Trigger and CLI

**Files:**
- Modify: `appcore/meta_hot_posts/service.py`
- Modify: `tools/meta_hot_posts/main.py`
- Modify: `tests/test_meta_hot_posts_routes.py`

- [ ] **Step 1: Update manual sync response**

In `appcore/meta_hot_posts/service.py`, replace:

```python
return MetaHotPostsResponse({"ok": True, "result": scheduler.sync_tick_once(target_count=500)}, 202)
```

with:

```python
return MetaHotPostsResponse({"ok": True, "result": scheduler.sync_tick_once()}, 202)
```

- [ ] **Step 2: Update CLI defaults**

In `tools/meta_hot_posts/main.py`, set:

```python
parser.add_argument("--target-count", type=int, default=0, help="0 means sync the full upstream result set")
parser.add_argument("--max-pages", type=int, default=scheduler.FULL_SYNC_MAX_PAGES)
```

Then call:

```python
target_count = None if args.target_count <= 0 else args.target_count
result = scheduler.sync_tick_once(target_count=target_count, max_pages=args.max_pages)
```

- [ ] **Step 3: Add or adjust route test**

If `tests/test_meta_hot_posts_routes.py` already mocks the manual sync route, assert the delegate call receives no `target_count=500`. Use:

```python
def fake_sync_tick_once(**kwargs):
    captured["kwargs"] = kwargs
    return {"posts": 2307}

assert captured["kwargs"] == {}
```

- [ ] **Step 4: Run route and CLI-adjacent tests**

Run:

```bash
pytest tests/test_meta_hot_posts_routes.py tests/test_meta_hot_posts_scheduler.py -q
```

Expected: PASS.

---

### Task 5: Update Scheduled Task Registry Copy

**Files:**
- Modify: `appcore/scheduled_tasks.py`
- Modify: `tests/test_appcore_scheduled_tasks.py`

- [ ] **Step 1: Update registry text**

In `meta_hot_posts_sync_tick`, change description and schedule from the 500-row wording to:

```python
"每天北京时间 07:00 使用已同步的 wedev Cookie/Bearer 拉取 /api/spy/hot/posts，"
"按上游接口 total/空页停止条件采集全集，单请求最小间隔 3 秒，并把热帖卡片字段与商品链接写入本地表。"
"Docs-anchor: docs/superpowers/specs/2026-05-15-meta-hot-posts-full-sync-design.md"
```

and:

```python
"schedule": "每天 07:00（北京时间），按上游接口全集采集",
```

- [ ] **Step 2: Adjust registry tests if they assert the old wording**

Search:

```bash
rg -n "目标采集 500|500 条|meta_hot_posts_sync_tick" tests/test_appcore_scheduled_tasks.py
```

Replace expected snippets with `按上游接口全集采集`.

- [ ] **Step 3: Run scheduled task tests**

Run:

```bash
pytest tests/test_appcore_scheduled_tasks.py tests/test_meta_hot_posts_scheduler.py -q
```

Expected: PASS.

---

### Task 6: Server Verification and One-Time Backfill

**Files:**
- No source files.

- [ ] **Step 1: Run focused tests locally**

Run:

```bash
pytest tests/test_meta_hot_posts_scheduler.py tests/test_meta_hot_posts_routes.py tests/test_appcore_scheduled_tasks.py -q
python -m py_compile appcore/meta_hot_posts/scheduler.py appcore/meta_hot_posts/service.py tools/meta_hot_posts/main.py appcore/scheduled_tasks.py
git diff --check
```

Expected: all commands exit 0.

- [ ] **Step 2: Commit and push**

Run:

```bash
git add appcore/meta_hot_posts/scheduler.py appcore/meta_hot_posts/service.py tools/meta_hot_posts/main.py appcore/scheduled_tasks.py tests/test_meta_hot_posts_scheduler.py tests/test_meta_hot_posts_routes.py tests/test_appcore_scheduled_tasks.py docs/superpowers/specs/2026-05-15-meta-hot-posts-full-sync-design.md
git commit -m "feat: sync full Meta hot posts dataset"
git push origin HEAD:master
```

Expected: push succeeds.

- [ ] **Step 3: Publish to test and production**

Run from Windows PowerShell:

```powershell
$script = @'
set -e
cd /opt/autovideosrt-test
git pull origin master --ff-only
systemctl restart autovideosrt-test
sleep 5
systemctl is-active autovideosrt-test
curl -s -o /dev/null -w "TEST HTTP %{http_code}\n" --max-time 20 http://127.0.0.1:8080/
cd /opt/autovideosrt
git pull origin master --ff-only
if ! cmp -s deploy/autovideosrt.service /etc/systemd/system/autovideosrt.service; then
  cp deploy/autovideosrt.service /etc/systemd/system/
  systemctl daemon-reload
fi
systemctl restart autovideosrt
sleep 10
systemctl is-active autovideosrt
curl -s -o /dev/null -w "PROD HTTP %{http_code}\n" --max-time 30 http://127.0.0.1/
'@
$script | ssh -i C:/Users/admin/.ssh/CC.pem root@172.30.254.14 "bash -s"
```

Expected: both services active, test and production return 200 or 302.

- [ ] **Step 4: Run one-time full backfill on production**

Run:

```powershell
ssh -i C:/Users/admin/.ssh/CC.pem root@172.30.254.14 "cd /opt/autovideosrt && /opt/autovideosrt/venv/bin/python -m tools.meta_hot_posts.main --mode sync --target-count 0 --max-pages 120"
```

Expected summary under current interface size:

```json
{
  "pages": 77,
  "posts": 2307,
  "queued_products": 2307,
  "target_count": null,
  "reported_total": 2307,
  "page_size": 30,
  "stop_reason": "reported_total_reached"
}
```

If upstream returns page 78 empty before the reported total condition is checked, `stop_reason` may be `empty_page`; that is also acceptable when `posts == reported_total`.

- [ ] **Step 5: Verify production DB state from server**

Run:

```powershell
$py = @'
from appcore.db import query_one
print(query_one("SELECT COUNT(*) AS c FROM meta_hot_posts"))
print(query_one("SELECT COUNT(*) AS c FROM meta_hot_posts WHERE first_seen_at >= CURDATE() AND first_seen_at < DATE_ADD(CURDATE(), INTERVAL 1 DAY)"))
print(query_one("SELECT task_code,status,summary_json,error_message FROM scheduled_task_runs WHERE task_code='meta_hot_posts_sync_tick' ORDER BY id DESC LIMIT 1"))
'@
$py | ssh -i C:/Users/admin/.ssh/CC.pem root@172.30.254.14 "cd /opt/autovideosrt && /opt/autovideosrt/venv/bin/python -"
```

Expected:

- Total hot posts increases toward the upstream total.
- Today-new count increases by the number of posts first seen during the backfill.
- Latest sync run has `status='success'` and includes `reported_total`.

---

## Operational Notes

- Full sync at 2307 rows and 3 seconds per page takes about 4 minutes.
- Product analysis runs 30 items every 10 minutes with 20 seconds between items, so a 2307-item backlog can take about 13 hours.
- Video localization runs 30 items with 30 seconds spacing; if every post has a video, full catch-up can take close to 20 hours because overlapping runs are guarded.
- Europe fit runs 30 items every 10 minutes and takes over from older running jobs by design. It will eventually drain all localized video candidates.
- If upstream grows beyond 3600 rows, `max_pages_reached` will appear in the sync summary. Raise `FULL_SYNC_MAX_PAGES` after confirming upstream total and acceptable runtime.
