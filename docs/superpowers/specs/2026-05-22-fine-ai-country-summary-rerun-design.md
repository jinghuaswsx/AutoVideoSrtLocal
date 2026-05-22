# Fine AI country summary and rerun controls

## Context

The standalone fine AI evaluation page renders country step cards and a final summary card. A failed country can be rerun manually, but the UI tied that control to the whole run being in a terminal state. When one failed country is rerun, the run becomes `running`, so other failed countries lose their rerun button even though their own cards are still failed.

The final summary card also has low decision value. It shows run-level metrics such as completed and failed counts, but operators need a country-by-country decision list: which countries to do, which to test carefully, and which not to do or rerun first.

## Design

- Country step rerun is controlled by the step itself:
  - Show the rerun AI evaluation button for any `country_XX` step whose status is `failed`.
  - Do not require the whole run to be terminal.
  - When clicked, confirm first, then only mark that country card as requesting.
- The summary card becomes a country decision summary:
  - Green group: `GO`, meaning "do it".
  - Yellow group: `TEST`, meaning "small-budget test / consider carefully".
  - Red group: `HOLD` plus failed countries, meaning "do not do yet / rerun or fill missing data".
  - Each country row shows country name, code, decision, score, one-line reason or failure reason, top risk, and next action.
  - Failed rows include the same rerun control so JP, FR, or any other failed country can be retried from the summary.
- The implementation uses existing result payloads (`result.countries`, `summary.country_ranking`, `frontend.tables.country_overview`) and does not add another LLM call.

## Verification

- Static UI tests assert failed country rerun no longer depends on overall run terminal state.
- Static UI tests assert the summary renders green, yellow, and red decision groups plus summary-level failed-country rerun controls.
- Existing fine AI route and pipeline tests continue to pass.
