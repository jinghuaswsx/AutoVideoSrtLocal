# 图片翻译接入 Seedream 5.0（豆包通道）设计

**日期**: 2026-04-22  
**状态**: 待评审

## 1. 背景与目标

当前图片翻译链路支持 `aistudio / cloud / openrouter` 三种全局通道，底层统一走 Gemini 图像模型；图片翻译页面与商品详情图一键翻译入口共用这套能力。现在需要把火山 ARK 的 Seedream 5.0 接入到系统中，并作为图片翻译的可选模型提供给用户使用。

本次目标不是重做整套图片翻译架构，而是在保持现有页面、任务结构、重试机制和存储链路稳定的前提下，新增一个清晰可控的“豆包图片通道”，让用户可以在系统设置中选择该通道，并在图片翻译任务里使用 `doubao-seedream-5-0-260128`。

本次交付目标：

- 在全局图片翻译通道中新增 `doubao`
- 在图片翻译模型列表中按通道返回可用模型
- 在豆包通道下接入 Seedream 5.0 图生图接口
- 让图片翻译页面和商品详情图一键翻译都能正确选中并调用 Seedream
- 复用现有任务状态、重试、熔断、存储和下载链路
- 把真实运行 key 存到本地环境中，并完成一次真实接口调用验证

## 2. 设计决策摘要

### 2.1 方案选择

采用“方案 2”：

- 全局图片翻译通道从 `aistudio / cloud / openrouter` 扩展为 `aistudio / cloud / openrouter / doubao`
- `doubao` 通道专门表示“图片翻译走火山 ARK 图片模型”
- 当通道为 `doubao` 时，图片翻译模型列表只暴露 Seedream 模型
- 当通道不为 `doubao` 时，继续使用现有 Gemini 图片模型与现有实现

### 2.2 不做的事情

本次不做以下改造：

- 不把图片翻译彻底迁入 `llm_client.invoke_generate()`
- 不新增独立的“Seedream 专用任务类型”
- 不改造图片翻译页面的核心交互结构
- 不为豆包图片翻译单独增加一块新的设置页卡片
- 不改变现有图片翻译的批处理、下载、归档与详情图回填机制

## 3. 用户可见行为

### 3.1 系统设置

用户在 [web/templates/settings.html](G:\Code\AutoVideoSrtLocal\.worktrees\seedream-image-translate-doubao\web\templates\settings.html) 的“全局配置”中看到图片翻译通道下拉框新增一项：

- `Google AI Studio`
- `Google Cloud (Vertex AI)`
- `OpenRouter`
- `豆包 ARK（Seedream）`

提示文案同步调整为：

- AI Studio / Google Cloud 继续走 Gemini 图像模型
- OpenRouter 继续走 OpenRouter 的 Gemini 图像能力
- 豆包 ARK 走 Seedream 图像接口
- 豆包通道复用现有豆包 ARK Key，不新增新的概念

### 3.2 图片翻译页面

在 [web/templates/image_translate_list.html](G:\Code\AutoVideoSrtLocal\.worktrees\seedream-image-translate-doubao\web\templates\image_translate_list.html) 中，模型区域仍是同一个“使用模型” pill 组，但返回内容按当前全局通道变化：

- `aistudio / cloud / openrouter`：显示现有 Gemini 图片模型
- `doubao`：只显示 `doubao-seedream-5-0-260128`

用户不需要理解额外的 provider 概念，只需要：

1. 在系统设置中切换通道
2. 在图片翻译页面选择该通道下可用的模型
3. 正常提交图片翻译任务

### 3.3 商品详情图一键翻译

[web/routes/medias.py](G:\Code\AutoVideoSrtLocal\.worktrees\seedream-image-translate-doubao\web\routes\medias.py) 的默认模型逻辑同步改为“按通道求默认模型”。因此当全局图片翻译通道切到 `doubao` 时，商品详情图一键翻译会自动默认使用 `doubao-seedream-5-0-260128`，无需用户另选。

## 4. 模块改动设计

## 4.1 通道配置层

修改 [appcore/image_translate_settings.py](G:\Code\AutoVideoSrtLocal\.worktrees\seedream-image-translate-doubao\appcore\image_translate_settings.py)：

- `CHANNELS` 从 `("aistudio", "cloud", "openrouter")` 扩展为 `("aistudio", "cloud", "openrouter", "doubao")`
- `CHANNEL_LABELS` 增加 `doubao`
- `get_channel()` / `set_channel()` 继续保持现有语义，只是接受并返回新值

`system_settings.key = image_translate.channel` 的存储结构不变，不需要数据库迁移。

## 4.2 模型注册层

当前 [appcore/gemini_image.py](G:\Code\AutoVideoSrtLocal\.worktrees\seedream-image-translate-doubao\appcore\gemini_image.py) 的 `IMAGE_MODELS` 是一个统一列表，这会导致通道与模型错配。需要改成“按通道组织的模型注册表”，推荐结构：

```python
IMAGE_MODELS_BY_CHANNEL = {
    "aistudio": [
        ("gemini-3.1-flash-image-preview", "Nano Banana 2（快速）"),
        ("gemini-3-pro-image-preview", "Nano Banana Pro（高保真）"),
    ],
    "cloud": [
        ("gemini-3.1-flash-image-preview", "Nano Banana 2（快速）"),
        ("gemini-3-pro-image-preview", "Nano Banana Pro（高保真）"),
    ],
    "openrouter": [
        ("gemini-3.1-flash-image-preview", "Nano Banana 2（快速）"),
        ("gemini-3-pro-image-preview", "Nano Banana Pro（高保真）"),
    ],
    "doubao": [
        ("doubao-seedream-5-0-260128", "Seedream 5.0（豆包）"),
    ],
}
```

同时提供三个辅助入口：

- `list_image_models(channel: str) -> list[tuple[str, str]]`
- `default_image_model(channel: str) -> str`
- `is_valid_image_model(model_id: str, channel: str | None = None) -> bool`

保留对旧调用点的兼容：

- 旧的 `IMAGE_MODELS` 可以保留为 Gemini 默认通道的兼容别名，或替换为 `list_image_models("aistudio")`
- 但新代码不要再直接依赖“全局统一列表”

## 4.3 路由层

修改 [web/routes/image_translate.py](G:\Code\AutoVideoSrtLocal\.worktrees\seedream-image-translate-doubao\web\routes\image_translate.py)：

- `/api/image-translate/models` 根据当前全局通道返回模型列表和默认模型
- `/api/image-translate/upload/complete` 校验 `model_id` 时，使用“当前通道下合法模型”的规则
- 页面上的“当前通道” badge 文案中加入 `doubao`

默认模型返回规则：

1. 读取用户保存的 `api_keys.service = image_translate` 下的 `extra.default_model_id`
2. 若该模型仍属于当前通道，作为默认值返回
3. 若不属于当前通道，则回退到 `default_image_model(current_channel)`

这样可以兼容历史上保存过 Gemini 模型偏好的用户。用户切到豆包通道时，页面不会因为旧偏好而报错。

## 4.4 商品详情图一键翻译入口

修改 [web/routes/medias.py](G:\Code\AutoVideoSrtLocal\.worktrees\seedream-image-translate-doubao\web\routes\medias.py) 中的 `_default_image_translate_model_id()`：

- 读取当前全局图片翻译通道
- 优先采用该通道下仍合法的用户偏好模型
- 否则回退到该通道默认模型

这保证详情图一键翻译与图片翻译页面行为一致。

## 4.5 图片生成分发层

当前 [appcore/gemini_image.py](G:\Code\AutoVideoSrtLocal\.worktrees\seedream-image-translate-doubao\appcore\gemini_image.py) 不是纯 Gemini SDK 封装，而是图片翻译的统一图片生成入口。这里保留函数名 `generate_image()`，但职责扩展为“按通道分发到不同图片模型实现”。

新的分发规则：

1. 读取当前 `image_translate.channel`
2. 若通道是 `doubao`，走新的 Seedream 分支
3. 否则维持现有 Gemini / OpenRouter 逻辑

运行时签名不变：

```python
generate_image(
    prompt: str,
    source_image: bytes,
    source_mime: str,
    model: str,
    user_id: int | None = None,
    project_id: str | None = None,
    service: str = "image_translate.generate",
) -> tuple[bytes, str]
```

这样 [appcore/image_translate_runtime.py](G:\Code\AutoVideoSrtLocal\.worktrees\seedream-image-translate-doubao\appcore\image_translate_runtime.py) 无需改调用协议，只要拿到 `(bytes, mime)` 即可继续原有存储流程。

## 5. Seedream 5.0 调用设计

## 5.1 接口

目标接口：

- `POST https://ark.cn-beijing.volces.com/api/v3/images/generations`

模型 ID：

- `doubao-seedream-5-0-260128`

## 5.2 请求体

图片翻译场景下的推荐请求体如下：

```json
{
  "model": "doubao-seedream-5-0-260128",
  "prompt": "<图片翻译 prompt>",
  "image": "data:image/png;base64,<...>",
  "size": "2048x2048",
  "sequential_image_generation": "disabled",
  "output_format": "png",
  "response_format": "b64_json",
  "stream": false,
  "watermark": false
}
```

说明：

- `image` 使用 `data:image/<mime>;base64,<...>` 格式，避免依赖外部临时可访问 URL
- `response_format` 优先使用 `b64_json`，这样后端不需要再二次下载 URL
- `output_format` 固定为 `png`
- `watermark` 固定为 `false`
- `sequential_image_generation` 固定为 `disabled`
- `stream` 固定为 `false`

## 5.3 输出尺寸策略

图片翻译的目标是尽量保持原图版面稳定，因此优先传入“精确像素尺寸”而不是抽象 `2K/3K` 档位：

1. 用 Pillow 读取原图宽高
2. 若宽高合法且满足 Seedream 文档限制，则直接传 `"{width}x{height}"`
3. 若解析失败、宽高不合法或超出限制，则回退到 `"2K"`

这能最大限度地贴近“原图尺寸保持一致”的现有任务预期。

## 5.4 凭据解析

Seedream 分支复用现有豆包 ARK 凭据链：

1. 用户级 `api_keys.service = doubao_llm`
2. `.env` 中的 `DOUBAO_LLM_API_KEY`
3. 若未配置 `DOUBAO_LLM_API_KEY`，继续允许退回 `VOLC_API_KEY`

Base URL 复用现有：

- `DOUBAO_LLM_BASE_URL = https://ark.cn-beijing.volces.com/api/v3`

不新增新的数据库 service，不拆出新的图片 provider 配置项。

## 6. 错误分类与重试语义

为了兼容现有 [appcore/image_translate_runtime.py](G:\Code\AutoVideoSrtLocal\.worktrees\seedream-image-translate-doubao\appcore\image_translate_runtime.py) 的三次重试与熔断机制，Seedream 分支需要继续抛出现有的两类异常：

- `GeminiImageError`：不可重试
- `GeminiImageRetryable`：可重试

分类规则：

- `401 / 403 / 422 / 参数错误 / 响应缺图 / base64 解析失败 / 安全拒绝`  
  归类为 `GeminiImageError`
- `429 / 500 / 502 / 503 / 504 / 网络超时 / 临时性上游异常`  
  归类为 `GeminiImageRetryable`

这样 runtime 无需知道当前到底是 Gemini 还是 Seedream，仍能保持一致的任务行为：

- 可重试错误：最多 3 次
- 持续 `429/5xx`：触发现有熔断逻辑
- 不可重试错误：立即标记该 item 失败

## 7. 配置与本地 key 存储策略

用户给出的真实 key 只写入本地开发环境，不进入仓库默认值，不写入 `.env.example`。

本地存储策略：

- 在工作区 `.env` 中设置 `DOUBAO_LLM_API_KEY=<real_key>`

不做的事情：

- 不把真实 key 写进代码常量
- 不把真实 key 写进 `.env.example`
- 不把真实 key 写进 spec、测试代码或任何可提交文档

## 8. 测试设计

## 8.1 单元测试

需要更新以下测试：

### [tests/test_image_translate_settings.py](G:\Code\AutoVideoSrtLocal\.worktrees\seedream-image-translate-doubao\tests\test_image_translate_settings.py)

- `get_channel()` 能返回 `doubao`
- `set_channel("DOUBAO")` 能正确落成 `doubao`
- 非法通道仍会被拒绝

### [tests/test_image_translate_routes.py](G:\Code\AutoVideoSrtLocal\.worktrees\seedream-image-translate-doubao\tests\test_image_translate_routes.py)

- `/api/image-translate/models` 在 `doubao` 通道下返回 Seedream 模型
- 用户历史默认模型与当前通道不匹配时，能正确回退到该通道默认模型
- `upload/complete` 在 `doubao` 通道下拒绝 Gemini 模型
- `upload/complete` 在 Gemini 通道下拒绝 Seedream 模型
- `medias` 的默认模型逻辑在 `doubao` 通道下能正确回退到 Seedream

### [tests/test_gemini_image.py](G:\Code\AutoVideoSrtLocal\.worktrees\seedream-image-translate-doubao\tests\test_gemini_image.py)

- 豆包通道下成功返回 `bytes + mime`
- 豆包通道缺 key 时抛出不可重试错误
- Seedream 返回 `b64_json` 时能正确解码
- `429` 被归为可重试错误
- `401/403` 被归为不可重试错误
- 使用 `size=WxH` 的优先策略

## 8.2 回归测试

至少执行以下命令：

```bash
pytest tests/test_image_translate_settings.py tests/test_gemini_image.py tests/test_image_translate_routes.py tests/test_image_translate_runtime.py -q
```

若整组太慢，可分两段执行，但最终要覆盖：

- 图片翻译设置
- 图片翻译路由
- 图片翻译 runtime
- 图片生成分发层

## 8.3 真实调用验证

本次需要额外做一次非自动化的真实联调：

1. 将真实 `DOUBAO_LLM_API_KEY` 写入本地 `.env`
2. 用最小化 prompt 构造一次 Seedream 图生图请求
3. 验证：
   - 鉴权成功
   - 接口返回有效图像
   - 代码封装能正确解析并拿到图片字节
   - 返回图片能被系统当前存储链路正常落盘

该验证不进入自动化测试，只作为本地联调证据。

## 9. 风险与约束

### 9.1 通道与模型错配

这是本次最大的产品一致性风险。若只新增 Seedream 模型、不按通道裁剪模型列表，前端与后台会出现“页面能选、提交时报错”的体验问题。本设计通过“按通道返回模型列表 + 按通道校验默认模型”解决。

### 9.2 文档中的 `response_format`

Seedream 文档明确支持 `url` 与 `b64_json` 两种响应格式。图片翻译链路更适合 `b64_json`，但真实接口联调时需要确认返回结构与 OpenAI 兼容程度；如果响应字段名与预期不同，应在分支内做兼容解析，但不改变对外接口。

### 9.3 输出尺寸保持

Seedream 支持像素尺寸与 `2K/3K` 两种方式。为减少布局漂移，本设计优先使用像素尺寸，但真实联调需验证某些极端尺寸下是否会被上游拒绝；若被拒绝，则按既定策略安全回退到 `2K`。

## 10. 实施边界

本设计完成后，下一阶段实现工作应聚焦于：

- 通道配置扩展
- 通道化模型注册
- 路由默认值与校验修正
- Seedream 调用分支实现
- 测试补齐
- 本地 key 存储与真实调用验证

不应在本任务中顺手推进：

- 图片翻译与 `llm_client` 的彻底统一
- 新的数据库 provider 结构
- 图片翻译 UI 重构
- 非图片翻译模块的豆包图片能力开放
