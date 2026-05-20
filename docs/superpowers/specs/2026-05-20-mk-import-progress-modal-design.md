# Mingkong Import Progress Modal Design

## Goal

When an operator clicks `加入素材库` in the Mingkong selection center, show a modal that makes the import workflow understandable, including current step, final state, failure reason, and next actions.

## Scope

This change visualizes the existing `加入素材库` flow. It does not change the business action into task creation, and it does not submit Niuma subtitle removal during import. Niuma remains part of the downstream task flow: after a small-language task is created, the raw-video processor claims the parent task and then the system submits Niuma processing.

## User Flow

1. The user clicks `加入素材库`.
2. A centered modal opens with the material filename and a step list.
3. The modal marks local preparation as complete, then shows server import as running.
4. When the API returns successfully, the modal shows:
   - product creation or existing product reuse,
   - product link warning if one exists,
   - material and product IDs,
   - next action buttons: `下一步：创建小语种任务`, `去任务中心`, `去素材管理`, `关闭`.
5. After publish domains are confirmed, `下一步：创建小语种任务` opens the same country-selection modal as the video-card small-language action, keeps the import translator locked, and creates a parent task through `POST /tasks/api/parent`.
6. When the API fails, the modal marks the failed step and displays the exact backend error text with retry and close actions.

## Step Model

The modal uses a client-side step model because the current import API is synchronous and does not stream backend progress. The visual steps are:

- `准备素材信息`
- `检查产品与链接`
- `下载明空原视频`
- `写入素材库`
- `后续任务入口`

The step labels stay honest: Niuma is shown only in the completion guidance as the next downstream automation after task creation and raw-video claim.

## Files

- `web/templates/mk_selection.html`: add modal markup, styles, and JavaScript workflow helpers.
- `tests/test_mk_selection_routes.py`: static template test for modal elements, step labels, error handling, and next-action buttons.

## Error Handling

The modal shows the backend response detail/error/status text. On failure, the original card button is restored so the operator can retry after fixing the issue.
