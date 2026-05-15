# 文案封面第四步图片供应商与并发策略设计

日期：2026-05-15
状态：已确认

## 背景

`docs/superpowers/specs/2026-05-14-video-cover-generation-design.md` 定义了「文案封面生成」四步工作流和默认模型配置。当前第四步 `cover_generation` 只支持本地图片接口与 OpenRouter 图片模型，且 1 到 4 张封面按串行循环生成。

本次补充要求：

- 第四步新增 `GOOGLE VERTEX ADC` 图片供应商，使用 Google Vertex ADC 图片凭据。
- OpenRouter 与 Google Vertex ADC 的 Gemini 图片模型 ID 必须区分：OpenRouter 保存 `google/...` 前缀模型 ID，Google Vertex ADC 保存裸 Gemini 模型 ID。
- 第四步供应商选择 OpenRouter 时，默认配置弹窗在模型 ID 后增加“执行方式”下拉，可选“并发执行 / 串行执行”，默认并发执行。
- 除 OpenRouter 外，其他第四步图片通道固定串行，不展示并发选项，后端也不可启用并发。
- 生成几张图就提交几个并发图片生成请求；最终仍按图片序号稳定展示和保存。

## 目标

1. 第四步模型池新增 `gemini_vertex_adc` 供应商，UI 展示为 `GOOGLE VERTEX ADC`。
2. `gemini_vertex_adc` 供应商包含三个 Gemini 图片模型：
   - `gemini-3.1-flash-image-preview`
   - `gemini-3-pro-image-preview`
   - `gemini-2.5-flash-image-preview`
3. OpenRouter 下的同名 Gemini 图片模型保存为：
   - `google/gemini-3.1-flash-image-preview`
   - `google/gemini-3-pro-image-preview`
   - `google/gemini-2.5-flash-image-preview`
4. 默认配置结构保留 `cover_generation.provider` 和 `cover_generation.model_id`，并新增 `cover_generation.execution_mode`。
5. `execution_mode` 只对 `provider=openrouter` 生效；合法值为 `parallel` / `serial`。
6. `provider=openrouter` 且缺失 `execution_mode` 时按 `parallel` 归一；非 OpenRouter 一律归一为 `serial`。
7. OpenRouter 并发执行时，`image_count=N` 即并发提交 N 个图片生成请求。
8. 并发结果写回仍按 `index` 从小到大排序，确保前端缩略图、文案、prompt 和下载链接一一对应。

## 非目标

- 不改变第一到第三步文本模型供应商与模型池。
- 不改变新建项目的封面张数规则，仍为 1 到 4 张，默认 4 张。
- 不为本地接口或 Google Vertex ADC 提供并发开关。
- 不改变封面原生 hook 合同、复制文案格式或最终 PNG 尺寸。
- 不引入任务级取消、单图重试或部分成功保存语义。

## 设计

### 供应商与模型映射

沿用代码内既有 provider key `gemini_vertex_adc`，不新增 `google_vertex_adc`。原因是仓库中 LLM 和图片凭据已经使用 `gemini_vertex_adc` / `gemini_vertex_adc_image` 命名，复用该 key 可避免配置、billing 和历史值兼容分叉。

第四步 `COVER_MODEL_OPTIONS` 扩展为：

```json
{
  "providers": {
    "local": "本地接口",
    "openrouter": "OPENROUTER",
    "gemini_vertex_adc": "GOOGLE VERTEX ADC"
  },
  "models": {
    "openrouter": {
      "nano_banana_2": "google/gemini-3.1-flash-image-preview",
      "nano_banana_pro": "google/gemini-3-pro-image-preview",
      "nano_banana_1": "google/gemini-2.5-flash-image-preview"
    },
    "gemini_vertex_adc": {
      "nano_banana_2": "gemini-3.1-flash-image-preview",
      "nano_banana_pro": "gemini-3-pro-image-preview",
      "nano_banana_1": "gemini-2.5-flash-image-preview"
    }
  }
}
```

OpenRouter 原有 `openai/gpt-5.4-image-2:low|mid|high` 继续保留。`local` 原有模型继续保留。

### 默认配置结构

保存到 `system_settings.video_cover_model_defaults` 的 `cover_generation` 示例：

```json
{
  "cover_generation": {
    "provider": "openrouter",
    "model_id": "google/gemini-3.1-flash-image-preview",
    "execution_mode": "parallel"
  }
}
```

归一规则：

- `provider=openrouter`：`execution_mode` 缺失或非法时归一为 `parallel`。
- `provider=local` 或 `provider=gemini_vertex_adc`：忽略提交值并归一为 `serial`。
- 历史配置没有 `execution_mode` 时仍可读取；OpenRouter 历史配置按新默认并发，其他历史配置按串行。

项目创建时继续把全局默认配置快照写入 `state_json.model_defaults`。失败重试和强制重新开始仍使用项目级快照，不受后续全局默认配置变更影响。

### UI

默认配置弹窗加宽，避免“供应商 / 模型 ID / 执行方式”三列拥挤或截断。

第四步配置行展示规则：

- 供应商下拉：本地接口、OPENROUTER、GOOGLE VERTEX ADC。
- 模型 ID 下拉：随供应商联动，展示实际保存的模型 ID。
- 执行方式下拉：只在第四步且供应商为 OPENROUTER 时显示，位于模型 ID 后面；可选“并发执行”和“串行执行”，默认“并发执行”。
- 供应商切换为非 OpenRouter 时，执行方式控件隐藏并提交/保存为 `serial`。

普通管理员仍不可见默认配置入口，默认配置 API 仍由 `@superadmin_required` 保护。

### 后端执行

`generate_video_covers()` 新增 `cover_execution_mode` 参数。有效值：

- `parallel`：仅当 `cover_provider=openrouter` 时启用。
- `serial`：所有供应商都支持，也是非 OpenRouter 唯一执行方式。

第四步调用逻辑：

1. 先构造所有候选封面的 prompt、`copy_item`、`index` 和 `platform` 元数据。
2. 如果 `cover_provider=openrouter` 且 `cover_execution_mode=parallel`，使用 `ThreadPoolExecutor(max_workers=image_count)` 并发调用图片模型。
3. 其他情况沿用串行循环。
4. 每个任务完成后执行同样的 `normalize_cover_png()` 和 `local_media_storage.write_bytes()`；封面文字由图片模型原生嵌入，不再调用后端固定叠字。
5. 所有结果按 `index` 排序后写入 `covers`。
6. 任一图片生成失败则第四步失败，整个步骤写 `error`；不引入部分成功状态。

并发只覆盖图片生成与后处理，不改变前三步文本分析链路。`step_requests.cover_generation` 继续保存 `image_prompts`，并新增 `execution_mode` 到请求快照和 `models.cover_generation`，便于排查本次执行到底是并发还是串行。

### Vertex ADC 图片调用

第四步 `provider=gemini_vertex_adc` 复用 `appcore.gemini_image.generate_image()`：

- `channel="cloud_adc"`
- 凭据读取 `llm_provider_configs.gemini_vertex_adc_image`
- 模型 ID 使用裸 Gemini ID
- billing provider 仍记录为 `gemini_vertex_adc`

## 错误处理

- `execution_mode` 非法时按归一规则兜底，不向用户报错。
- 非 OpenRouter 即使收到 `parallel` 也强制串行，避免本地接口或 Vertex ADC 被意外并发打爆。
- OpenRouter 并发中任一请求失败时，第四步错误信息沿用现有 `封面生成失败：...` 格式。
- 并发结果如果乱序返回，后端按 `index` 排序后再保存到状态。

## 验收

- 默认配置弹窗中第四步供应商包含 `GOOGLE VERTEX ADC`。
- 第四步 OpenRouter 的 Gemini 图片模型 ID 带 `google/` 前缀；Google Vertex ADC 的 Gemini 图片模型 ID 不带 `google/` 前缀。
- 第四步选择 OpenRouter 时显示执行方式下拉，默认值为并发执行。
- 第四步选择本地接口或 Google Vertex ADC 时不显示执行方式下拉，保存后状态为 `serial`。
- OpenRouter 并发模式下，生成 4 张图会并发提交 4 次图片生成调用，最终 `covers` 仍按 `index=1..4` 排序。
- 串行模式下，图片生成调用按 `index=1..N` 顺序执行。
- 状态接口和“全部报文预览”能看到第四步实际使用的 `provider`、`model_id` 和 `execution_mode`。

## 测试

- `tests/test_video_cover_generation.py`
  - 覆盖 `resolve_cover_model_selection()` 对 OpenRouter 与 Google Vertex ADC 模型 ID 的差异。
  - 覆盖默认配置归一：OpenRouter 缺失执行方式 → `parallel`；非 OpenRouter → `serial`。
  - 覆盖默认配置页面：弹窗加宽、OpenRouter 执行方式控件存在、非 OpenRouter 隐藏逻辑存在。
  - 覆盖项目创建快照保存 `execution_mode`。
  - 覆盖 `_run_cover_generation_step()` 向 `generate_video_covers()` 传递 `cover_execution_mode`。
  - 覆盖 OpenRouter 并发模式提交次数、结果排序和失败传播。

