# 2026-05-25 Product Detail Image Quality Inspection Localization Tolerance Design

为了防止商品实物物理印刷英文、机身丝印、外包装固有标签、配料成分表、规格型号以及品牌LOGO专有名词等固有物理英文字符被误判为“中英混杂”或“翻译不合格”，本设计对 `appcore/image_translate_runtime.py` 中的详情图大模型质检提示词 `_EVAL_PROMPT` 进行优化，并彻底屏蔽视觉排版及布局考核。

## 目标与规范

1. **豁免实物包装物理英文**：
   - 允许且鼓励在翻译后的目标语种（如德语、法语、日语等）商品详情图中保留商品实物包装原本印刷的英文、瓶身/机身物理丝印、产品固有标签/说明书配料表、认证标志（如 CE, FCC 等）、Model 规格型号以及品牌专有名词 LOGO 等。
   - 这些实物固有的物理英文是合格的（应当保持原有，防止买家收到实物后产生“货不对版”的纠纷），绝不能因为它们而判定为“多语言混杂”或“翻译不合格”并导致扣分。

2. **核心聚焦后期营销文案**：
   - 核心质检任务是检查图片后期添加的非实物营销性文案、核心卖点宣传语、大字标题、功能标注、促销折扣等电商宣传性质文案是否已被完美翻译并本土化为目标语种。
   - 只要后期营销宣传文案翻译语种正确且本地化合格，就应当判定为通过。

3. **排版视觉效果完全免检**：
   - 完全不评估任何排版布局、文字重叠、溢出、遮挡、字体大小或排版美观度等视觉问题。排版保持与原图一致即为 100% 合格。
   - 强制将大模型评估 Schema 中的 `has_layout_issue` 设为 `false`，且 `layout_issue_details` 设为空字符串 `""`。

4. **评分唯一决定性因素**：
   - `translation_quality_score` 质量评分必须纯粹且唯一由翻译质量本身决定，不得受到任何布局或产品固有英文的扣分干扰。

## 涉及文件

- [appcore/image_translate_runtime.py](file:///g:/Code/AutoVideoSrtLocal/appcore/image_translate_runtime.py) (定义 `_EVAL_PROMPT` 常量，供批量与单图实时质检模块共用)
- [tests/test_detail_image_quality_prompt.py](file:///g:/Code/AutoVideoSrtLocal/tests/test_detail_image_quality_prompt.py) (新增质检提示词内容规则单元测试)
