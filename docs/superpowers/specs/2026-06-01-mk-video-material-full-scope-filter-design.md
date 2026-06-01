# 明空视频素材库全量筛选设计

## 背景

`/xuanpin/mk#videos` 的「视频素材库」此前默认读取本地
`mingkong_material_daily_snapshots` 快照。该快照由定时任务按店小秘
Listing 候选产品同步，因此适合高消耗候选池，但不是明空后台视频素材库
的全量索引。

线上排查中，`scratch-free-5-finger-wash-mitt-rjc` 对应的原始视频
`2026.03.20-五指洗车手套-原素材-指派-李文龙.mp4` 已存在于素材管理
和明空后台，但因为当前 Listing 快照没有覆盖对应明空产品，导致明空选品
视频素材库按本地快照搜索不到。

## 目标

1. 页面筛选口径拆成明确的「单日 / 时间范围 / 全部」三类。
2. 默认模式为「单日」，默认读取当前可用的最新单日快照；选择单日时才显示
   具体日期下拉框。
3. 选择本周、上周、本月、上月时，按对应时间范围查询所有符合条件的本地
   快照素材。
4. 新增「全部」选项。选择全部时，所有进入明空视频素材池的素材都满足筛选
   条件；带关键词搜索时必须补查明空实时素材库，避免仅靠本地快照漏掉已存在
   的素材。
5. 将模式下拉框移动到日期下拉框左侧。

## 行为约定

- `range` 为空表示「单日」模式。前端必须传当前选中的 `snapshot`，后端仍
  兼容未传 `snapshot` 时回退到最新成功快照。
- `range=this_week|last_week|this_month|last_month` 表示时间范围模式。
- `range=all` 表示全部素材池模式：
  - 有关键词时，后端用明空实时接口搜索关键词及必要的产品 code 变体，例如
    `-rjc` 与非 `-rjc` 互补搜索，并把返回的产品详情视频扁平化为视频卡片。
  - 无关键词时，后端读取本地历史快照的去重结果，按视频 90 天消耗倒序分页。
- 全部模式返回结构继续兼容 `/xuanpin/api/mk-material-library` 现有卡片契约，
  不恢复旧的 `/xuanpin/api/mk-video-materials` 主列表调用。

## 验证

```bash
pytest tests/test_mingkong_materials.py tests/test_mk_selection_routes.py tests/test_xuanpin_routes.py -q
```
