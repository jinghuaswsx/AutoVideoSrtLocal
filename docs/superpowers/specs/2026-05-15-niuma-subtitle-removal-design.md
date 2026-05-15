# Niuma Subtitle Removal Design

## Goal

Add "Niuma" as a selectable subtitle-removal backend alongside the current "Volc" option, while reusing the subtitle-removal API code that already exists in this project.

## Audit Result

The Niuma API document matches the existing third-party subtitle-removal API wrapper:

- `appcore/subtitle_removal_provider.py`
- `submit_task()` uses `biz=aiRemoveSubtitleSubmitTask`
- `query_progress()` uses `biz=aiRemoveSubtitleProgress`
- endpoint default is `https://goodline.simplemokey.com/api/openAi`

So this integration must not add a second provider/runtime directory for the same API. The implementation will extend the existing provider/runtime with a Niuma config source and a backend branch.

## Scope

- Add `subtitle_backend=niuma` to existing route validation, upload bootstrap/complete, list filtering, state payloads, and frontend labels.
- Reuse `appcore/subtitle_removal_provider.py`, adding a `credential_code` or equivalent selector so:
  - existing calls keep using `llm_provider_configs.subtitle_removal`
  - Niuma calls use `infra_credentials.niuma_main` synced into `config.NIUMA_ERASE_*`
- Reuse `appcore/subtitle_removal_runtime.py`, adding backend-aware submit/poll calls and the Niuma `videoName={task_id}_{x1}_{y1}_{x2}_{y2}` format.
- Keep VOD and local VSR files unchanged unless dispatch/labels require a small compatibility tweak.
- Add `infra_credentials.niuma_main` and a migration seed row without committing the API key.

## Data Flow

1. Upload page sends `subtitle_backend=niuma`.
2. Upload complete treats Niuma like Volc for storage: local upload through server, then push to TOS and store `source_tos_key`.
3. Submit stores the normalized removal area and queues the task.
4. Runner starts the existing `SubtitleRemovalRuntime`.
5. Runtime sees `subtitle_backend=niuma`, uses Niuma credentials, builds `videoName` without the `sr_` prefix, polls the existing progress API, downloads `resultUrl`, and marks the task done.

## Configuration

`infra_credentials` owns the Niuma credential:

- code: `niuma_main`
- group: `external_api`
- fields: `api_key`, `base_url`

`config.py` keeps env fallbacks:

- `NIUMA_ERASE_API_KEY`
- `NIUMA_ERASE_BASE_URL`

The migration seeds `niuma_main` with an empty API key and the default base URL.

## UI

- Upload backend radio adds a visible "牛马" option.
- List filter adds a visible "牛马" pill.
- Erase type remains Volc-only. Niuma submits only the selected full-frame/box area because the provided Niuma API document does not define the Volc text-erase `operation` extension.

## Testing

- Provider tests cover both existing default config and Niuma config selection.
- Runtime tests verify Niuma uses Niuma credentials and `task_id_x1_y1_x2_y2` video names through the existing runtime.
- Route tests verify `niuma` backend validation, TOS-backed upload behavior, labels, filtering, and Volc-only erase type behavior.
- UI tests verify visible upload/list Niuma controls and script label handling.
