# 链接检查功能设计

**日期**: 2026-04-18  
**最近更新**: 2026-04-19  
**状态**: 已确认，已补充二值快检与大模型相同图片判断

## 1. 背景与目标

当前站点基于 Shopify，多语种主体文案和菜单可由 Shopify 自带翻译能力处理，但商品主图轮播图与详情页说明图中的嵌字内容，需要由运营人员手动替换为对应语种版本。现阶段缺少一个统一的检查入口，无法快速判断某个商品链接里的图片是否已经完成目标语种适配。

第一版目标是新增一个后台菜单 **“链接检查”**，允许用户输入一个商品链接并选择目标语言，并可选上传一组该语种版本的参考图片。系统自动抓取当前页面里的商品相关图片，在当前页可视化展示：

- 抓取到了哪些图片
- 每张图片的检测结果
- 每张网站图片与参考图的匹配结果
- 二值快检的精确结果
- 大模型相同图片判断结果
- 哪些图片仍然疑似未替换
- 当前链接整体是否可以判定为“已完成归档”

## 2. 范围与非目标

### 2.1 范围

- 左侧菜单新增一级入口“链接检查”，层级与“视频翻译”同级
- 新增一个单页模块，页面内完成输入、执行、进度展示和结果展示
- 输入项包含四项，其中参考图上传为可选：
  - 商品链接输入框
  - 目标语言下拉框
  - 参考图片文件选择框（可选）
  - 检查按钮
- 目标语言选项来自 `media_languages` 表中 `enabled=1` 的记录
- 抓取时必须锁定到目标语种页面上下文，不能误抓跳转后的英语默认站页面
- 抓取范围限定为商品页当前可见的：
  - 主图轮播图
  - 商品详情说明区图片
- 如果上传了参考图片，则先做“同图匹配”，再进入“参考图优先判定”
- 对 `matched` 的图片对，执行：
  - 二值快检
  - 大模型相同图片判断
- 对没有参考图或没有匹配上参考图的网站图，继续执行原有 Gemini 语言与质量判断
- 页面给出整体判断：`已完成归档` 或 `未完成归档`

### 2.2 非目标

- 不与内部素材库、原图库做逐张比对
- 不做图片自动替换，只做检测和标记
- 不做历史记录列表，不进入 `projects` 表，不需要任务归档页
- 不做跨站点通用爬虫平台，第一版优先覆盖 Shopify 商品详情页
- 不做文件名匹配或二进制哈希匹配
- 不把“大模型相同图片判断”作为最终裁决依据
- 不要求上传图与站点图二进制完全一致
- 不接受“请求的是小语种链接，但实际抓的是英语落地页”这种静默降级

## 3. 用户流程

### 3.1 页面流程

1. 用户进入“链接检查”页
2. 输入商品链接
3. 从下拉菜单选择目标语言
4. 可选上传该语种版本的轮播图和详情图
5. 点击“开始检查”
6. 系统先确认链接已被锁定到目标语种页面上下文
7. 页面原位进入检查中状态，展示任务进度
8. 系统完成抓图后，逐张展示图片和检测结论
9. 如果存在参考图，则同步展示逐张匹配、二值快检和大模型同图判断结果
10. 页面底部给出整体判断

### 3.2 判断口径

- 图片中 **没有可识别文字**：
  - 若没有参考图或没有匹配上参考图，则交给语言模型判断并通常记为“无需替换”
- 图片中 **有参考图且成功匹配上**：
  - 最终通过/不通过由二值快检负责
  - 大模型相同图片判断只展示，不参与最终裁决
- 图片中 **没有参考图** 或 **没匹配上参考图**：
  - 继续走原有语言与质量 Gemini 判断
- 只有当全部需要语言适配的图片都通过时，整体才判定为 `已完成归档`

## 4. 页面设计

### 4.1 信息架构

页面分为三块：

- 顶部输入区
- 中部任务进度区
- 底部结果区

不单独开详情页，不做历史列表，所有数据在当前页完成展示。

### 4.2 输入区

字段如下：

- `link_url`
  - 文本输入框
  - 占位符示例：`https://example.com/de/products/xxx`
- `target_language`
  - 下拉框
  - 数据来源：`media_languages.enabled=1`
  - 显示值：`name_zh`
  - 提交值：`code`
- `reference_images`
  - 文件选择框
  - 可多选
  - 仅允许图片格式：`jpg`、`jpeg`、`png`、`webp`
  - 用途：上传该语种版本的轮播图和详情图，作为网站图片的参考集合
  - 非必填
- `开始检查`
  - 主按钮
  - 点击后禁用，直到任务结束或失败

### 4.3 结果区

结果区采用卡片化布局，每张图片一张卡片，字段如下：

- 图片预览
- 来源类型：`轮播图` / `详情图`
- 原始图片 URL
- 参考图匹配结果
  - 最相似参考图预览
  - 匹配状态：`matched | weak_match | not_matched | not_provided`
  - 综合分数
- 二值快检结果
  - 执行状态：`通过 | 不通过 | 未执行 | 执行失败`
  - 二值相似度
  - 前景重合度
  - 阈值
  - 说明
- 大模型相同图片判断
  - 返回值：`是 | 不是 | 未执行 | 执行失败`
  - 使用通道：`AI Studio | Vertex AI | OpenRouter`
  - 使用模型：`Gemini 3.1 Flash-Lite Preview`
- 语言与质量判断结果
  - 仅对“无参考图”或“未匹配上参考图”的图片执行
  - 识别到的文字摘要
  - 模型判断的主要语言
  - 是否匹配目标语言
  - 文案质量评分
  - 文案质量说明
- 最终状态：
  - `通过`
  - `无需替换`
  - `疑似未替换`
  - `质量待确认`
  - `参考图未匹配`
  - `检测失败`

页面顶部额外展示一个汇总卡片：

- 抓取图片总数
- 参考图片总数
- 已匹配参考图数
- 已执行二值快检数
- 已执行大模型同图判断数
- 通过数
- 待处理数
- 异常数
- 整体结论

### 4.4 三态

页面必须覆盖三态：

- `loading`
  - 展示抓取和分析进度
- `empty`
  - 还未开始检查时显示引导文案
- `error`
  - 链接无法访问、页面抓图失败、模型调用失败时显示错误块

## 5. 抓图策略与目标语种锁定

第一版目标站点为 Shopify 商品页。抓图前必须先完成“目标语种页面锁定”，避免用户提交的是 `/de/` 等小语种链接，但最终实际抓到英语站页面。

### 5.1 抓图范围

- 商品媒体区域
  - `product__media`
  - `featured_media`
  - 常见商品图库节点
- 商品描述区域
  - 富文本内容区
  - 商品描述节点中的 `img`

### 5.2 回退规则

如果优先规则未抓到足够图片，则回退扫描主内容区中的大图，并排除：

- logo
- icon
- 支付图标
- sprite
- 过小图片
- 站点装饰图

### 5.3 去重规则

同一图片可能同时出现在 `src`、`data-src`、`srcset` 中，抓取时需按规范化 URL 去重：

- 移除 query 中与尺寸裁剪相关的冗余参数
- 统一协议和主机写法
- 同一主图只保留一份最高优先级 URL

### 5.4 目标语种链接锁定

抓取流程必须加入以下步骤：

1. 根据用户输入链接和目标语言，请求页面
2. 请求时显式附带目标语种请求头
   - 例如 `Accept-Language: de-DE,de;q=0.9,en;q=0.8`
3. 真正解析图片前，必须校验至少一项：
   - 最终 URL 路径仍带目标语种目录，例如 `/de/`
   - 页面 `<html lang>` 与目标语种一致
4. 如果校验失败，任务直接失败，不允许静默继续抓图

## 6. 参考图匹配与参考图优先判定

这一节是本次升级的核心。

### 6.1 第一层：确定性参考图匹配

如果用户上传了参考图，系统先沿用现有确定性图片指纹比对逻辑：

- `pHash`
- `dHash`
- `SSIM`
- 宽高比差异

处理目标是判断：

- 网站图与哪一张参考图最接近
- 它们是否属于同一张图的不同导出版本

输出状态：

- `matched`
- `weak_match`
- `not_matched`
- `not_provided`

### 6.2 第二层：二值快检

仅当 `reference_match.status == "matched"` 时，才执行二值快检。

#### 处理方式

- 不实际落盘生成 `100x100 jpg` 文件
- 只在内存里完成等效处理
- 处理步骤：
  1. 统一转 `RGB`
  2. 应用 EXIF 方向修正
  3. 等比缩放并 padding 到 `100x100`
  4. 转灰度
  5. 做自适应二值化
  6. 计算黑白对比指标

#### 需要输出的精确指标

- `binary_similarity`
  - 全图二值相似度
- `foreground_overlap`
  - 黑色前景区域重合度
- `threshold`
  - 当前阈值，第一版固定为 `0.90`
- `status`
  - `pass | fail | skipped | error`
- `reason`
  - 简短中文说明

#### 判定规则

- `binary_similarity >= 0.90`
  - 直接判定该图片 `pass`
  - 不再执行原有语言与质量 Gemini 判断
- `binary_similarity < 0.90`
  - 直接判定该图片 `replace`
  - 不再执行原有语言与质量 Gemini 判断

#### 重要约束

- 最终 pass/fail 由二值快检负责
- 二值快检是“参考图已匹配”后的强裁决规则
- 这套规则是后续调阈值的基础，因此必须把精确结果展示到前端

### 6.3 第三层：大模型相同图片判断

仅当 `reference_match.status == "matched"` 时，在二值快检之外，再额外调用一次 Gemini 做“同图判断”。

#### 目的

- 给运营一个“模型视角”的辅助判断
- 便于后续调优二值快检逻辑
- 不作为最终裁决依据

#### 通道来源

复用现有 **图片翻译通道** 配置，而不是视频翻译模型 provider。

可选通道：

- `aistudio`
- `cloud`
- `openrouter`

对应展示名称：

- `Google AI Studio`
- `Google Cloud (Vertex AI)`
- `OpenRouter`

#### 模型

- AI Studio / Vertex AI：
  - `gemini-3.1-flash-lite-preview`
- OpenRouter：
  - `google/gemini-3.1-flash-lite-preview`

说明：

- 这里按系统当前已接入和 provider 可用性，统一接 `Gemini 3.1 Flash-Lite Preview`
- 不把它描述为必须是官方稳定 GA 型号

#### 提示词要求

输入为两张图：

- 网站抓取图
- 参考图

提示词核心要求：

- 忽略尺寸差异
- 忽略压缩差异
- 忽略导出格式差异
- 只判断从视觉上看，它们是否属于同一张基础图片
- 不做语言质量分析
- 只返回：
  - `是`
  - `不是`

#### 输出结构

建议输出：

```json
{
  "status": "done",
  "answer": "是",
  "channel": "cloud",
  "channel_label": "Google Cloud (Vertex AI)",
  "model": "gemini-3.1-flash-lite-preview",
  "reason": ""
}
```

允许状态：

- `done`
- `skipped`
- `error`

#### 判定口径

- 大模型相同图片判断 **只展示**
- 不参与最终 pass/fail
- 如果调用失败：
  - 当前图片仍按二值快检结果裁决
  - 前端展示为“执行失败”

### 6.4 参考图优先总体流程

当用户上传了参考图后，每张网站图的处理顺序如下：

1. 先找最佳参考图
2. 如果 `matched`
   - 执行二值快检
   - 执行大模型相同图片判断
   - 若二值快检正常执行，最终结果由二值快检直接给出
   - 若二值快检执行失败，则回退到原来的语言与质量 Gemini 判断
3. 如果 `weak_match / not_matched`
   - 不执行二值快检
   - 不执行大模型相同图片判断
   - 回退到原来的语言与质量 Gemini 判断
4. 如果 `not_provided`
   - 直接走原来的语言与质量 Gemini 判断

## 7. Gemini 语言与质量判断

### 7.1 适用范围

原有 Gemini 语言与质量判断逻辑继续保留，但只用于：

- 没有上传参考图的图片
- 上传了参考图但未匹配上的图片

### 7.2 模型与职责

继续沿用现有单图结构化分析逻辑：

- 识别图片中是否有文字
- 判断主要语言是否为目标语言
- 判断文案质量是否合格
- 给出 `pass | review | replace | no_text`

### 7.3 结果字段

结构保持不变：

```json
{
  "has_text": true,
  "detected_language": "en",
  "language_match": false,
  "text_summary": "Organize Your Hat Collection Effortlessly",
  "quality_score": 22,
  "quality_reason": "图片里文案主体仍为英语，不符合目标语种页面要求",
  "needs_replacement": true,
  "decision": "replace"
}
```

### 7.4 与参考图优先链路的关系

- `matched` 图片对：
  - 不再执行这里的单图语言 Gemini
- `not_matched / not_provided` 图片：
  - 继续执行这里的单图语言 Gemini

## 8. 任务与运行时设计

### 8.1 任务类型

第一版继续使用“轻量异步临时任务”，不入库，不写历史。

### 8.2 生命周期

任务生命周期为：

- 创建
- 目标语种锁定中
- 抓图中
- 参考图预处理中
- 分析中
- 已完成
- 已失败

### 8.3 单图结果结构

建议每张图片结构扩充为：

```json
{
  "id": "site-1",
  "kind": "carousel",
  "source_url": "https://img.example.com/a.jpg",
  "status": "done",
  "reference_match": {
    "status": "matched",
    "score": 0.91,
    "reference_id": "ref-1",
    "reference_filename": "hero-de.jpg"
  },
  "binary_quick_check": {
    "status": "pass",
    "binary_similarity": 0.924,
    "foreground_overlap": 0.891,
    "threshold": 0.90,
    "reason": "参考图已匹配，二值相似度达到阈值，直接通过"
  },
  "same_image_llm": {
    "status": "done",
    "answer": "是",
    "channel": "cloud",
    "channel_label": "Google Cloud (Vertex AI)",
    "model": "gemini-3.1-flash-lite-preview",
    "reason": ""
  },
  "analysis": {
    "decision": "pass",
    "decision_source": "binary_quick_check",
    "quality_reason": "参考图已匹配且二值快检通过，跳过语言模型"
  },
  "error": ""
}
```

### 8.4 汇总结构

建议新增汇总计数：

- `reference_matched_count`
- `binary_checked_count`
- `binary_direct_pass_count`
- `binary_direct_replace_count`
- `same_image_llm_done_count`
- `same_image_llm_yes_count`

## 9. 后端设计

### 9.1 新增或调整模块

- `appcore/link_check_compare.py`
  - 保留现有参考图匹配逻辑
  - 追加二值快检能力
- `appcore/link_check_gemini.py`
  - 保留原有单图语言与质量分析
- 新增 `appcore/link_check_same_image.py`
  - 负责“大模型相同图片判断”
  - 复用图片翻译通道的 `AI Studio / Vertex / OpenRouter` 链路
- `appcore/link_check_runtime.py`
  - 编排：
    - 抓图
    - 参考图匹配
    - 二值快检
    - 大模型同图判断
    - 原有语言 Gemini 回退逻辑

### 9.2 复用模块

尽量复用：

- `appcore.gemini`
- `appcore.gemini_image`
- `appcore.image_translate_settings`
- `appcore.medias`
- `web.store`

### 9.3 关键边界

- 二值快检负责最终裁决
- 大模型同图判断只负责展示
- 原有单图语言 Gemini 只在没有可信参考图匹配时执行

## 10. API 与前端返回

现有 `GET /api/link-check/tasks/<task_id>` 返回结构基础上，为每个 item 增加：

- `binary_quick_check`
- `same_image_llm`

前端结果卡片必须新增以下展示字段：

- `二值快检结果`
- `二值相似度`
- `前景重合度`
- `当前阈值`
- `二值快检说明`
- `大模型相同图片判断`
- `大模型判断通道`
- `大模型判断模型`

## 11. 错误处理

| 场景 | 行为 |
|---|---|
| 链接为空或格式不合法 | 前端直接拦截 |
| 页面跳转后丢失目标语种上下文 | 任务失败，并提示当前落地页语言 |
| 页面里未找到有效图片 | 返回空结果并标记失败 |
| 参考图上传格式不合法 | 创建任务失败，前端提示修正 |
| 网站图与全部参考图都无法建立可信匹配 | 回退到语言 Gemini，而不是直接失败 |
| 二值快检执行失败 | 当前图片标记快检失败，并回退到语言 Gemini |
| 大模型相同图片判断执行失败 | 不影响最终裁决，只在前端展示“执行失败” |
| 单图语言 Gemini 调用失败 | 该图片标记 `检测失败`，其余继续 |

重要补充：

- 二值快检失败是算法执行失败，不等同于快检结果“不通过”
- 只有快检正常执行且低于阈值，才直接判 `replace`

## 12. 测试策略

### 12.1 二值快检测试

新增或扩展 `tests/test_link_check_compare.py`，覆盖：

- 同一图片的不同尺寸版本，二值快检可通过
- 同一图片的不同压缩版本，二值快检可通过
- 版式相同但文字不同的图片，二值快检应不通过
- 返回精确指标：
  - `binary_similarity`
  - `foreground_overlap`
  - `threshold`

### 12.2 大模型同图判断测试

新增 `tests/test_link_check_same_image.py`，覆盖：

- 复用图片翻译通道配置
- AI Studio / Vertex / OpenRouter 三通道路由正确
- prompt 强制只返回 `是` / `不是`
- 结果解析正确
- 调用失败时返回 `error` 状态但不抛崩全任务

### 12.3 运行时测试

扩展 `tests/test_link_check_runtime.py`，覆盖：

- `matched + 二值快检通过` 时直接 `pass`，不调用语言 Gemini
- `matched + 二值快检不通过` 时直接 `replace`
- `matched` 时会额外执行大模型同图判断
- `not_matched` 时继续走原语言 Gemini
- 汇总统计新增字段正确

### 12.4 路由与前端测试

扩展 `tests/test_link_check_routes.py`，覆盖：

- 返回体包含 `binary_quick_check`
- 返回体包含 `same_image_llm`
- 结果页可以渲染二值快检精确结果
- 结果页可以渲染大模型同图判断结果

## 13. 风险与取舍

- 全图二值相似度可能对“文字区域占比很小”的图片不够敏感
  - 取舍：额外输出 `foreground_overlap`，方便后续调阈值
- 大模型同图判断与二值快检可能结论不一致
  - 取舍：明确只让二值快检负责裁决，大模型只展示
- OpenRouter 与 Google 官方对 `3.1 Flash-Lite Preview` 的命名和可用性可能随时间波动
  - 取舍：实现按当前 provider 可用 model id 接入，并把实际模型名回显到前端
- 参考图匹配不到的情况仍需走语言模型，整体耗时不会完全消失
  - 取舍：优先保证准确性，不强行把所有图都塞进参考图优先逻辑

## 14. 相关文件

预计会新增或修改以下文件：

- 新增：`appcore/link_check_same_image.py`
- 修改：`appcore/link_check_compare.py`
- 修改：`appcore/link_check_runtime.py`
- 修改：`appcore/link_check_gemini.py`
- 修改：`web/routes/link_check.py`
- 修改：`web/static/link_check.js`
- 修改：`web/templates/link_check.html`
- 新增：`tests/test_link_check_same_image.py`
- 修改：`tests/test_link_check_compare.py`
- 修改：`tests/test_link_check_runtime.py`
- 修改：`tests/test_link_check_routes.py`
