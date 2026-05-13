# Image Translate Banana Regenerate Design

## Context

APIMART can return a successful image generation response whose content is unrelated to the source image. The image translate detail page currently treats this as a completed item, so the only per-image recovery action is the normal regenerate button.

The normal regenerate path resets the item and restarts the task runner, but the item may still carry an APIMART `provider_task_id`. When that snapshot remains, APIMART recovery can poll the same upstream task and return the same wrong image again.

## Design

Add a second per-item action named `banana重新生成` next to the existing regenerate button. It resets the selected item, deletes the old result artifact, clears any saved APIMART task snapshot, and marks the item with a one-shot generation override:

- channel: `cloud_adc`
- model: `gemini-3.1-flash-image-preview`

The runner honors the per-item override only for image generation. Text detection and task ownership stay unchanged. Normal regenerate clears the override so it continues to use the task's original channel.

## Runtime

`appcore.gemini_image.generate_image()` accepts an optional channel override and supports `cloud_adc` by resolving `gemini_vertex_adc_image` provider config, requiring `extra_config.project`, and creating the Gen AI image client with Vertex ADC credentials.

## Verification

Unit coverage:

- normal retry clears APIMART snapshot fields;
- banana retry sets the ADC/Nano Banana 2 override and starts the runner;
- `generate_image(..., channel_override="cloud_adc")` uses the Vertex backend with no API key.
