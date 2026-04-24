# 图片翻译接入 OpenRouter OpenAI Image 2 质量档位设计

**日期**：2026-04-24
**状态**：待评审

## 1. 背景与目标

当前图片翻译支持 `aistudio / cloud / openrouter / doubao` 四个通道。`openrouter` 通道目前只暴露 Gemini 图像模型，图片翻译页面和商品详情图一键翻译入口也都只把模型当作单一 `model_id` 处理。

本次需求是在不重做图片翻译任务结构的前提下，把 OpenRouter 上的 OpenAI Image 2 能力接入到图片翻译中，并满足以下业务要求：

- 作为图片翻译模型备选项出现，而不是单独开一个新流程
- 支持三档生图质量：`low / mid / high`
- 作为一个可选配置项由后台控制启用与否
- 后台可配置默认质量档位
- 历史任务和现有 Gemini / Seedream 流程不受影响

本次交付目标：

- 在系统设置中新增“启用 OpenAI Image 2”和“默认质量档位”配置
- 在 `openrouter` 通道下按配置动态暴露 3 个图片翻译模型备选项
- 在运行时把 3 个备选项映射到同一个 OpenRouter 模型，并透传不同质量参数
- 保持现有任务 state、路由协议、任务详情页和重试链路不变

## 2. 设计决策摘要

### 2.1 方案选择

采用“虚拟三档模型 + 后台开关 + 默认质量”的方案：

- 用户侧仍然只看见图片翻译模型备选项
- `low / mid / high` 表示为 3 个可选择的 `model_id`
- 运行时再把这 3 个 `model_id` 解析为同一个 OpenRouter 底层模型和不同 `quality`

该方案的优点是：

- 与现有“模型就是一个字符串”的前后端链路兼容最好
- 不需要为图片翻译新增独立字段或新的任务结构
- 对图片翻译列表页、商品详情图入口和任务详情页的改动最小

### 2.2 不做的事情

本次不做以下改造：

- 不新增数据库 schema
- 不给图片翻译任务新增单独的 `quality` 字段
- 不把图片翻译整体迁移到 `appcore.llm_client.invoke_generate()`
- 不重做图片翻译页面的交互结构
- 不把 OpenAI Image 2 扩展到非 `openrouter` 通道

## 3. 外部依赖与前提

基于截至 2026-04-24 已核实的官方资料：

- OpenAI 官方图像文档当前底层图像模型为 `gpt-image-2`，支持 `quality: low / medium / high`
- OpenRouter 当前公开的对应模型页为 `openai/gpt-5.4-image-2`
- OpenRouter 图像生成文档说明图像参数会通过 `chat/completions` 请求透传给底层图像模型

参考资料：

- [OpenAI Image Generation Guide](https://developers.openai.com/api/docs/guides/image-generation)
- [OpenAI GPT Image 2 Model](https://developers.openai.com/api/docs/models/gpt-image-2)
- [OpenRouter Image Generation](https://openrouter.ai/docs/guides/overview/multimodal/image-generation)
- [OpenRouter GPT-5.4 Image 2 API](https://openrouter.ai/openai/gpt-5.4-image-2/api)

推断说明：

- 这里采用的实际 OpenRouter 模型 ID 为 `openai/gpt-5.4-image-2`
- 质量枚举仍按 OpenAI 官方能力使用 `low / medium / high`
- 为了与产品文案保持一致，UI 文案使用 `low / mid / high`，运行时映射为 `low / medium / high`

## 4. 用户可见行为

### 4.1 系统设置

在 [web/templates/settings.html](G:\Code\AutoVideoSrtLocal\.worktrees\codex-openrouter-openai-image2\web\templates\settings.html) 的图片翻译设置区新增两项：

- `启用 OpenAI Image 2`
- `OpenAI Image 2 默认质量`

展示规则：

- 仅当全局图片翻译通道为 `openrouter` 时显示这两项
- 开关关闭时，“默认质量”控件禁用或隐藏
- 开关开启时，“默认质量”可选 `low / mid / high`

### 4.2 图片翻译新建页

在 [web/templates/_image_translate_scripts.html](G:\Code\AutoVideoSrtLocal\.worktrees\codex-openrouter-openai-image2\web\templates\_image_translate_scripts.html) 中，模型 pill 组继续使用现有渲染逻辑，但在 `openrouter` 通道且开关开启时追加 3 个可选模型：

- `OpenAI Image 2（Low）`
- `OpenAI Image 2（Mid）`
- `OpenAI Image 2（High）`

当开关关闭时，这 3 个模型不出现。

### 4.3 商品详情图一键翻译

[web/routes/medias.py](G:\Code\AutoVideoSrtLocal\.worktrees\codex-openrouter-openai-image2\web\routes\medias.py) 的默认模型逻辑同步支持这 3 个模型项，但是否可以作为新任务默认值仍受后台开关控制。

### 4.4 历史任务

历史任务若已经保存了 OpenAI Image 2 的质量档位模型：

- 详情页继续显示原模型 ID
- 重新生成和重试继续按原档位执行
- 即使管理员后来关闭了该功能，也不影响旧任务执行

## 5. 模型与配置表示

## 5.1 新增系统配置项

在 `system_settings` 中新增两个 key：

- `image_translate.openrouter_openai_image2_enabled`
- `image_translate.openrouter_openai_image2_default_quality`

推荐语义：

- `enabled`：布尔值，默认 `false`
- `default_quality`：字符串，允许 `low / mid / high`，默认 `mid`

不需要数据库迁移，只沿用现有 `system_settings` 读写机制。

## 5.2 虚拟模型 ID

为保持任务链路兼容，OpenAI Image 2 的三档质量使用 3 个可解析的虚拟模型 ID：

- `openai/gpt-5.4-image-2:low`
- `openai/gpt-5.4-image-2:mid`
- `openai/gpt-5.4-image-2:high`

这些值既用于：

- 设置页默认模型
- 图片翻译模型备选列表
- 图片翻译任务 `model_id`
- 商品详情图一键翻译默认模型

## 5.3 运行时映射

运行时需要把虚拟模型 ID 解析成：

- 实际 OpenRouter 模型：`openai/gpt-5.4-image-2`
- 质量参数：
  - `low -> low`
  - `mid -> medium`
  - `high -> high`

这样可以保持产品文案和底层 API 参数各自清晰。

## 6. 模块改动设计

## 6.1 配置层

修改 [appcore/image_translate_settings.py](G:\Code\AutoVideoSrtLocal\.worktrees\codex-openrouter-openai-image2\appcore\image_translate_settings.py)，新增围绕 OpenAI Image 2 的配置辅助函数，建议包括：

- `is_openrouter_openai_image2_enabled() -> bool`
- `set_openrouter_openai_image2_enabled(value: bool) -> None`
- `get_openrouter_openai_image2_default_quality() -> str`
- `set_openrouter_openai_image2_default_quality(value: str) -> None`

职责：

- 统一做默认值回退
- 统一做 `low / mid / high` 枚举校验
- 为设置页和模型注册层提供稳定接口

## 6.2 模型注册层

修改 [appcore/gemini_image.py](G:\Code\AutoVideoSrtLocal\.worktrees\codex-openrouter-openai-image2\appcore\gemini_image.py) 的模型注册逻辑。

当前 `openrouter` 通道模型列表是固定的 Gemini 列表；本次改为：

- 基础列表仍保留现有 OpenRouter Gemini 模型
- 若配置开启，则在 `openrouter` 通道列表后追加 3 个 OpenAI Image 2 质量档位模型

建议新增辅助能力：

- `is_openrouter_openai_image2_model(model_id: str) -> bool`
- `parse_openrouter_openai_image2_model(model_id: str) -> tuple[str, str] | None`

`list_image_models("openrouter")` 的返回值将受开关影响，但其他通道不变。

## 6.3 设置页路由层

修改 [web/routes/settings.py](G:\Code\AutoVideoSrtLocal\.worktrees\codex-openrouter-openai-image2\web\routes\settings.py)：

- GET：把开关状态和默认质量传给模板
- POST：保存开关和默认质量
- 当管理员关闭开关后，如果当前 `image_translate.default_model.openrouter` 指向某个 OpenAI Image 2 档位，则自动回退到普通 OpenRouter 默认模型

该回退动作只影响“新建任务默认值”，不影响历史任务。

## 6.4 图片翻译路由层

修改 [web/routes/image_translate.py](G:\Code\AutoVideoSrtLocal\.worktrees\codex-openrouter-openai-image2\web\routes\image_translate.py)：

- `/api/image-translate/models`：按当前通道和功能开关返回模型列表
- `/api/image-translate/upload/complete`：只允许新任务提交当前通道下合法的模型
- 默认模型返回逻辑：若当前配置的默认模型已不可用，则自动回退到当前通道的普通默认模型

重点规则：

- 开关关闭时，新建任务不允许提交 OpenAI Image 2 质量档位模型
- 开关开启时，3 个质量档位模型视为当前通道合法模型

## 6.5 商品详情图一键翻译入口

修改 [web/routes/medias.py](G:\Code\AutoVideoSrtLocal\.worktrees\codex-openrouter-openai-image2\web\routes\medias.py) 中默认模型获取逻辑：

- 继续按“当前图片翻译通道”返回默认模型
- 如果当前通道是 `openrouter` 且功能开启，则允许默认值落到某个 OpenAI Image 2 档位
- 如果功能关闭，则自动回退到普通 OpenRouter 默认模型

## 6.6 OpenRouter 调用层

修改 [appcore/gemini_image.py](G:\Code\AutoVideoSrtLocal\.worktrees\codex-openrouter-openai-image2\appcore\gemini_image.py) 中 `_generate_via_openrouter()`：

- 对普通 Gemini OpenRouter 模型，继续走现有逻辑
- 对 OpenAI Image 2 质量档位模型，在请求前先解析出真实模型和质量参数
- 请求体继续使用当前的 `chat.completions.create(...)` 路径，保持响应提取逻辑不变

建议请求映射：

```python
client.chat.completions.create(
    model="openai/gpt-5.4-image-2",
    messages=[...文本 + 源图...],
    modalities=["image", "text"],
    extra_body={
        "quality": "medium",
        "usage": {"include": True},
    },
)
```

如 SDK 版本不接受 `modalities` 顶层参数，则继续沿用现有 fallback，把 `modalities` 放入 `extra_body`。

## 7. 兼容性与回退规则

### 7.1 新任务

- 开关关闭：OpenAI Image 2 质量档位不出现在模型列表，也不通过接口校验
- 开关开启：模型列表追加 3 个档位，默认质量决定默认选中项

### 7.2 历史任务

- 历史任务中的 `model_id` 原样保留
- 执行、重试、详情页展示都继续按原 `model_id` 处理
- 功能开关只影响“新建任务入口”，不影响历史任务运行

### 7.3 默认值回退

以下场景回退到普通 OpenRouter 默认模型：

- 开关关闭但配置中的默认模型仍是某个 OpenAI Image 2 档位
- 默认质量配置非法
- 保存的默认模型不属于当前通道有效模型集合

## 8. 错误处理

OpenAI Image 2 仍复用现有 `GeminiImageError / GeminiImageRetryable` 两类异常语义。

建议分类：

- OpenRouter 鉴权失败、请求参数非法、响应无图、质量参数无法解析
  归类为 `GeminiImageError`
- OpenRouter `429 / 5xx / timeout`
  归类为 `GeminiImageRetryable`

这样无需修改 [appcore/image_translate_runtime.py](G:\Code\AutoVideoSrtLocal\.worktrees\codex-openrouter-openai-image2\appcore\image_translate_runtime.py) 的三次重试、熔断和任务进度逻辑。

## 9. 测试方案

## 9.1 单元测试

新增或调整以下测试：

- [tests/test_gemini_image.py](G:\Code\AutoVideoSrtLocal\.worktrees\codex-openrouter-openai-image2\tests\test_gemini_image.py)
  - 解析虚拟模型 ID 为真实模型和质量参数
  - OpenRouter 调用时对 `mid` 正确映射为 `medium`
  - 普通 Gemini OpenRouter 模型不受影响
- [tests/test_image_translate_settings.py](G:\Code\AutoVideoSrtLocal\.worktrees\codex-openrouter-openai-image2\tests\test_image_translate_settings.py)
  - 开关与默认质量的读写
  - 默认模型在功能关闭时正确回退
- [tests/test_image_translate_routes.py](G:\Code\AutoVideoSrtLocal\.worktrees\codex-openrouter-openai-image2\tests\test_image_translate_routes.py)
  - `/api/image-translate/models` 在开关关闭和开启时的返回差异
  - 新建任务时对 3 个质量档位模型的合法性校验
- [tests/test_settings_routes_new.py](G:\Code\AutoVideoSrtLocal\.worktrees\codex-openrouter-openai-image2\tests\test_settings_routes_new.py)
  - 设置页展示和保存新配置项

### 9.2 集成验证

按项目约定，在测试环境 `http://172.30.254.14:8080/` 验证：

1. 打开开关并设置默认质量
2. 在图片翻译页确认 3 个质量档位模型出现
3. 分别创建 `low / mid / high` 三个任务
4. 确认任务详情页能看到正确的模型标识
5. 确认任务可正常产出图片并保持现有下载、重试链路可用

## 10. 已知风险与边界

- 当前仓库里的 `tests/test_image_translate_routes.py` 存在与本需求无关的旧失败；本次验收应以新增/受影响用例通过为准，不把旧问题混入本需求范围
- OpenRouter 对图像参数的兼容行为可能继续演进，因此 OpenAI Image 2 请求体应尽量收敛在当前已验证的最小字段集合上
- 若后续 OpenRouter 模型别名从 `openai/gpt-5.4-image-2` 调整，需要只改一处模型常量，不应散落在多个路由与模板中

## 11. 触碰文件

- [appcore/image_translate_settings.py](G:\Code\AutoVideoSrtLocal\.worktrees\codex-openrouter-openai-image2\appcore\image_translate_settings.py)
- [appcore/gemini_image.py](G:\Code\AutoVideoSrtLocal\.worktrees\codex-openrouter-openai-image2\appcore\gemini_image.py)
- [web/routes/settings.py](G:\Code\AutoVideoSrtLocal\.worktrees\codex-openrouter-openai-image2\web\routes\settings.py)
- [web/routes/image_translate.py](G:\Code\AutoVideoSrtLocal\.worktrees\codex-openrouter-openai-image2\web\routes\image_translate.py)
- [web/routes/medias.py](G:\Code\AutoVideoSrtLocal\.worktrees\codex-openrouter-openai-image2\web\routes\medias.py)
- [web/templates/settings.html](G:\Code\AutoVideoSrtLocal\.worktrees\codex-openrouter-openai-image2\web\templates\settings.html)
- [tests/test_gemini_image.py](G:\Code\AutoVideoSrtLocal\.worktrees\codex-openrouter-openai-image2\tests\test_gemini_image.py)
- [tests/test_image_translate_settings.py](G:\Code\AutoVideoSrtLocal\.worktrees\codex-openrouter-openai-image2\tests\test_image_translate_settings.py)
- [tests/test_image_translate_routes.py](G:\Code\AutoVideoSrtLocal\.worktrees\codex-openrouter-openai-image2\tests\test_image_translate_routes.py)
- [tests/test_settings_routes_new.py](G:\Code\AutoVideoSrtLocal\.worktrees\codex-openrouter-openai-image2\tests\test_settings_routes_new.py)

## 12. 回滚方案

本次改动不涉及数据库迁移。若需要回滚：

- 回滚代码即可恢复旧的模型注册和设置页行为
- 已有历史任务中的 OpenAI Image 2 `model_id` 会变成旧版本无法新建但可保留的历史值
- 若希望回滚后仍能查看历史任务，详情页应继续容忍未知 `model_id` 显示原值
