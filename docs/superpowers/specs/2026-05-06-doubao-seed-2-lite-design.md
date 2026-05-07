# Doubao Seed 2.0 Lite 接入设计

> 日期：2026-05-06
> 范围：只把 Doubao Seed 2.0 Lite 接成可选文本模型，并写入精确价格；不改变任何 use case 默认绑定。

## 1. 背景

火山方舟已提供 Doubao Seed 2.0 Lite 文本模型。当前项目已经有 `doubao` provider adapter，走 ARK 的 OpenAI-compatible `chat.completions` 协议，因此本次不新增 adapter、不新增 provider code。

官方参考：

- 火山方舟模型列表：<https://www.volcengine.com/docs/82379/1330310>
- 火山方舟模型价格：<https://www.volcengine.com/docs/82379/1544106>
- 豆包 2.0 API 发布说明：<https://developer.volcengine.com/articles/7610285824933445675>

## 2. 接入目标

- 新增可选模型 ID：`doubao-seed-2-0-lite-260215`。
- 后台 `/settings?tab=bindings` 中，`doubao` provider 的模型候选包含 Seed 2.0 Lite。
- `ai_model_prices` 写入 Seed 2.0 Lite 精确 token 价格，避免继续走 `doubao/*` 通配兜底价。
- 不修改 `appcore/llm_use_cases.py` 中任何 default provider/model。
- 不新增 `doubao_lite` 之类的 provider 字符串。

## 3. 价格口径

官方价格文档列出的 Doubao Seed 2.0 Lite 价格为：

- 输入：0.6 元 / 百万 tokens
- 输出：3.6 元 / 百万 tokens

项目 `ai_model_prices` 按 CNY/token 存储，因此写入：

- `unit_input_cny = 0.00000060`
- `unit_output_cny = 0.00000360`

## 4. 实现范围

- `web/templates/settings.html`
  - 在 `DOUBAO_MODELS` 中加入 `doubao-seed-2-0-lite-260215`。
- `db/migrations/*_doubao_seed_2_lite_price.sql`
  - upsert `provider='doubao'`、`model='doubao-seed-2-0-lite-260215'`、`units_type='tokens'` 的精确价格。
- 测试
  - 覆盖后台绑定页模型候选中出现 Seed 2.0 Lite。
  - 覆盖迁移 SQL 写入精确价格。

## 5. 验证

- 单元测试：
  - `pytest tests/test_settings_routes_new.py tests/test_pricing.py -q`
  - 相关 LLM 回归：`pytest tests/test_llm_providers_openrouter.py tests/test_llm_client_invoke.py tests/test_llm_provider_configs.py -q`
- 架构守卫：
  - `pytest tests/test_architecture_boundaries.py::test_direct_provider_sdk_imports_stay_in_adapter_or_legacy_files -v`
- 真实调用：
  - 使用现有 `DoubaoAdapter.chat(...)`，指定 `model='doubao-seed-2-0-lite-260215'`，发起最小 chat 请求。
  - 调用凭据只从现有 `doubao_llm` provider 配置读取，不新增环境变量或直连 SDK 路径。
