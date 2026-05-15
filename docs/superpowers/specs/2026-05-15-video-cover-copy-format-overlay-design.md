# 文案封面文案格式与原生 Hook 修订设计

日期：2026-05-15
状态：已确认

## 背景

`docs/superpowers/specs/2026-05-14-video-cover-generation-design.md` 定义了文案封面生成的 4 步流程：视频分析、产品分析、文案创作、封面生成。实际使用中，第 3 步输出的 `headline / body_text / cta` 与业务需要的三段式文案不一致；第 4 步必须让图片模型直接把封面 hook 融入构图，而不是后端用固定模板叠字。

目标文案格式必须是：

```text
标题: Don’t Get Stuck Unprepared
文案: Add high-visibility warning light, hands-free work lighting, and backup phone charging to your trunk, RV, or emergency kit.
描述: Road Trips Made Safer
```

这与 `docs/superpowers/specs/2026-04-21-push-english-texts-design.md` 已定义的推送文案合同一致：`title / message / description` 三个字段必须非空。

## 目标

1. 第 3 步输出和保存 5 组 `ad_copy_sets`，每组英文文案字段为 `title / message / description`。
2. 第 4 步图片模型必须把当前 `selected_ad_copy.english.title` 原生嵌入封面，作为唯一可读 hook。
3. 后端只对图片模型结果做 `1080x1920` PNG 规范化和 artifact 保存，不再进行 PIL 固定叠字。
4. 最终结果不写入 `overlay_text`、`overlay_box`、`overlay_font_size`、`overlay_lines` 等固定叠字元数据。
5. 前端最终文案展示与复制必须输出三段式：
   `标题: ...`
   `文案: ...`
   `描述: ...`
6. 兼容历史任务的 `headline / body_text / cta`：读取旧结构时映射为 `title / message / description`，不让旧项目失效。
7. 第 4 步保存每张封面实际使用的 prompt 和标准化文案，提示词弹窗看到的是实际请求。

## 非目标

- 本次不改商品链接抓取、默认模型配置、权限、项目列表或部署流程。
- 本次不引入人工可拖拽文字编辑器。
- 本次不保留固定位置半透明背景框、整条黑色横幅或模板化标题栏。
- 本次不承诺图片模型能完全复刻商品外观，只修正文案合同、封面文字生成方式和可审计性。

## 设计

### 文案合同

`ad_copy_sets` 结构调整为：

```json
{
  "ad_copy_sets": [
    {
      "id": 1,
      "angle": "痛点解决型",
      "english": {
        "title": "Don’t Get Stuck Unprepared",
        "message": "Add high-visibility warning light, hands-free work lighting, and backup phone charging to your trunk, RV, or emergency kit.",
        "description": "Road Trips Made Safer"
      },
      "chinese_translation": {
        "title": "别在紧急时毫无准备",
        "message": "为后备箱、房车或应急包增加高可见警示灯、免手持工作照明和备用手机充电。",
        "description": "让自驾出行更安全"
      },
      "usage_note": "适合车尾箱、路边停车或应急包场景。"
    }
  ]
}
```

字段含义：

- `title`：封面唯一可读 hook，只能是一句英文短标题，优先 3 到 7 个词，最多 42 个字符。
- `message`：广告正文，用于复制文案和下游推送，不画到封面上。
- `description`：短描述/副标题，用于复制文案和下游推送，不画到封面上。

兼容规则：

- 旧结构 `headline` → `title`
- 旧结构 `body_text` → `message`
- 旧结构 `cta` → `description`

### 生图 Prompt

封面生成 prompt 改为“图片模型原生 hook”合同：

- 图片模型必须生成真实 UGC 风格 9:16 完整封面图。
- 图片中必须且只能有一个可读英文 hook，内容严格等于当前 `selected_ad_copy.english.title`。
- 除该 `title` 外，禁止生成任何可读文字、字幕、UI、价格、按钮、贴纸、箭头、红圈、水印、品牌字样或评论框。
- 禁止固定位置半透明背景框、整条黑色横幅、模板化标题栏。
- 允许字体、位置、字号、阴影和局部轻量托底随产品、动作和构图变化，只要 hook 清晰可读且像封面原生组成部分。
- prompt 中只传入当前 `selected_ad_copy`，不重复塞全部长文案。

### 后处理

后处理只做确定性图片规范化和保存：

- 图片模型输出统一进入 `normalize_cover_png()`。
- 最终画布固定为 `1080x1920` PNG。
- 保存到 `local_media_storage` 的 `artifacts/video_cover/<user>/<task_id>/`。
- 不调用 PIL 固定叠字逻辑。
- 不生成或保存 `overlay_text`、`overlay_box`、`overlay_font_size`、`overlay_lines`。

### 可审计性

每张封面记录：

- `prompt`：本张图实际发送给图片模型的 prompt。
- `copy`：本张图使用的标准化文案。
- `formatted_copy`：三段式文本。
- `hook`：当前 `selected_ad_copy.english.title`。

第 4 步的 `step_requests.cover_generation` 保存 `image_prompts` 数组，而不是只保存一个预估 prompt。

### 最终结果布局

第 4 步封面生成卡片内不再使用缩略图切换和左图右文案布局。前端必须直接读取当前 `state.result.covers`，按数组顺序从左到右渲染 1 到 4 张结果卡片。

每张结果卡片内部结构固定为：

1. 上方文案区：展示对应封面的三段式 `标题 / 文案 / 描述` 文案。
2. 文案区底部：一个蓝色胶囊“复制文案”按钮，复制本卡片对应的 `formatted_copy`，没有该字段时用标准化 `copy` 现场拼接。
3. 下方封面区：展示本卡片对应封面图。
4. 图片操作区：只保留一个“保存图片”胶囊按钮，不再显示“复制图片”。

文案区、复制文案按钮、封面图和保存图片按钮使用同一宽度约束；保存图片按钮在封面图下方居中对齐。多张图的结果区使用自适应网格排列，左右间距一致；运行中已有部分封面时立即展示已有卡片，全部封面完成后同一网格自动重新对齐，不出现缩略图切换、卡片跳宽或上下错位。

## 验收

- 给定示例文案时，封面结果的 `copy.english.title/message/description` 与输入字段一一对应。
- “复制文案”按钮输出：
  `标题: ...`
  `文案: ...`
  `描述: ...`
- 生图 prompt 明确要求图片模型原生嵌入 `selected_ad_copy.english.title` 作为唯一可读 hook。
- 生图 prompt 明确禁止固定位置半透明背景框、整条黑色横幅和模板化标题栏。
- 最终 PNG 尺寸仍是 `1080x1920`。
- cover 结果不包含 `overlay_text`、`overlay_box`、`overlay_font_size`、`overlay_lines`。
- 旧的 `headline/body_text/cta` 测试数据仍能生成封面和复制文案。
- 第 4 步运行中只要 `state.result.covers` 已有封面，前端就显示已有卡片。
- 多张封面结果直接从左到右排列，每张卡片上文案、下封面图，蓝色“复制文案”按钮与图片等宽。
- 封面图下方不再出现“复制图片”；只保留与封面图等宽并居中对齐的“保存图片”按钮。

## 测试

- `tests/test_video_cover_generation.py`
  - 覆盖新文案 schema 的解析与校验。
  - 覆盖旧 schema 的兼容映射。
  - 覆盖生图 prompt 要求原生嵌入唯一 hook，并禁止固定模板化标题背景。
  - 覆盖后端只做 `1080x1920` 规范化保存，不再记录 overlay 元数据。
- 模板断言更新为 `title/message/description` 和三段式复制文案。
