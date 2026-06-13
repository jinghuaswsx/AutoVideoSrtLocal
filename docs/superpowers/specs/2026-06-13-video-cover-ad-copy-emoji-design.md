# 文案封面广告文案 Emoji 生成规则

日期：2026-06-13
状态：已确认

## 背景

文案封面创作第 3 步已经输出 `ad_copy_sets`，字段为 `english.title / message / description`。现有 prompt 只写了“Emoji 可用，但每组最多 1 个；如果会影响严肃感或 Meta 风险，则不用”，实际约束偏弱，模型容易生成全无 emoji 的英文广告文案。

生产只读抽样统计（2026-06-13，`media_copywritings` 英文文案）：

- 英文文案总数：299 条。
- 含 emoji 文案：178 条。
- emoji 主要出现在 `body` 字段，常见符号包括 `✨`、`✅`、`🎁`、`🚗`、`🛠`、`🐾`。

这说明当前产品英语文案的投放语气已经接受适量 emoji；文案封面创作不应只是“允许使用”，而应默认主动、克制地使用。

## 目标

1. 第 3 步生成英文广告文案时，5 组中默认 2 到 4 组带 emoji。
2. 每组英文文案最多 1 个 emoji，避免堆砌和廉价感。
3. emoji 必须与产品品类、使用场景或具体利益点匹配，不能随机装饰。
4. Meta 风险、严肃/敏感品类、医疗/身体缺陷/恐吓表达相关文案可以不用 emoji。
5. 不改变 `ad_copy_sets` JSON 结构，不新增字段，不影响历史 `title/message/description` 合同。

## 设计

### 生成规则

- 默认 5 组中 2 到 4 组使用 emoji；只有产品调性严肃、敏感或 emoji 会削弱可信度时，才允许更少。
- 每组最多 1 个 emoji，且只放在 `english.title`、`english.message`、`english.description` 三个字段之一。
- 优先放在 `english.message` 的自然短语前后，或放在 `english.title` 的开头/结尾作为轻量 hook。
- `description` 只有在短描述仍自然时才使用 emoji，不要为了凑数量破坏短描述质感。
- emoji 不计入字段语义，删除 emoji 后英文仍必须完整、自然、可投放。

### 选用原则

- 家居/收纳/厨房：优先使用 `✨`、`✅`、`🏡`、`💡` 等表达整洁、省事、日常改善。
- 车品/户外/旅行：优先使用 `🚗`、`✈️`、`⚡`、`✅` 等表达出行、应急、便利。
- 工具/维修/安装：优先使用 `🛠`、`✅`、`💪` 等表达实用、耐用、省力。
- 宠物用品：优先使用 `🐾`、`✨`、`✅` 等表达宠物场景和轻松护理。
- 礼品/家庭：优先使用 `🎁`、`🏡`、`✨` 等表达送礼和家庭使用。
- 不要使用与产品无关、过度情绪化、可能暗示医疗功效或恐吓用户的 emoji。

### 风险约束

- 不得用 emoji 强化夸张承诺，例如 miracle、cure、guaranteed、100% 等。
- 不得用 emoji 制造焦虑、羞辱用户或暗示身体缺陷。
- 不得连续使用多个 emoji，不得把 emoji 当作列表项目符号。
- 不得让 5 组都使用同一个 emoji。
- 中文翻译只忠实表达英文含义，不需要复制或解释 emoji。

## 验收

- `build_ad_copy_prompt()` 输出中明确包含“5 组中 2 到 4 组使用 emoji”的规则。
- prompt 明确限制每组最多 1 个 emoji。
- prompt 明确要求 emoji 与产品品类、使用场景或利益点匹配。
- prompt 明确说明严肃/敏感/Meta 风险场景可以少用或不用。
- `generate_ad_copy_sets()` 仍返回标准 `ad_copy_sets`，结构保持不变。

## 测试

- `tests/test_video_cover_generation.py::test_generate_ad_copy_sets_uses_user_prompt_and_validates_json`
  - 断言文案创作 prompt 包含 emoji 数量、位置、品类匹配和风险约束。
- 按 `docs/superpowers/specs/2026-06-08-targeted-pytest-verification.md` 运行 focused tests，不默认跑全量 pytest。
