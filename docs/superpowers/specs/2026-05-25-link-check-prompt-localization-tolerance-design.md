# 2026-05-25 Link Check Prompt Localization Tolerance Design

为了防止商品实物物理印刷英文、机身丝印、品牌LOGO、配料成分表、规格型号等物理实物本身带有且符合海关/翻译合规规范的固有英文被误判为“中英混杂”或“翻译不合格”，本设计对 `appcore/link_check_gemini.py` 中的大模型质检提示词进行优化。

## 目标与规范
1. **豁免物理印刷英文**：允许商品图片中存在产品实物固有的外包装印刷英文、瓶身丝印、说明书标签、认证标志（如 CE, FCC 等）、规格型号（如 Model: X1）以及品牌专有名词（如 Apple, Sony 等）。
2. **聚焦关键营销文本**：核心聚焦检查的营销文本应包含：大标题宣传语、核心功能标注、促销折扣信息、产品核心卖点文案等。这些关键营销性文案必须百分之百翻译为目标语种，且本地化自然流畅。
3. **消除误判**：只要这些关键营销文本翻译合格，即使背景中出现物理实物包装上的固有英文，也必须判定为 `decision=pass`，给以高分。

## 涉及文件
- [appcore/link_check_gemini.py](file:///g:/Code/AutoVideoSrtLocal/.worktrees/link-check-prompt-localization-tolerance/appcore/link_check_gemini.py)
