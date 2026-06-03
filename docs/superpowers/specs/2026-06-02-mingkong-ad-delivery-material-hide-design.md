# 明空投放素材隐藏设计

## 背景

`/xuanpin/mk` 的视频素材库和昨天消耗前 300 会展示明空素材卡片，并提供加入素材库、创建小语种任务、AI 评估等操作。部分素材已经是内部投放素材，继续展示容易被重复处理或误操作。

## 目标

在明空选品前端展示素材卡片时，默认隐藏以下投放素材：

1. 视频文件名包含 `蔡靖华`。
2. 产品 `product_code` 以 `-rjc` 结尾，大小写不敏感。

页面新增投放素材筛选项：

- `不包含RJC`：默认选项，执行上面的隐藏规则。
- `全部`：不执行投放素材隐藏规则，`-rjc` 产品 code 和 `蔡靖华` 文件名素材都会展示。

隐藏时分页总数也必须按隐藏后的结果计算，避免页面显示数量和实际卡片不一致。

## 范围

- 覆盖 `/xuanpin/api/mk-material-library` 的单日、时间范围、全部历史、全部实时搜索结果。
- 覆盖 `/xuanpin/api/mk-yesterday-top300` / `/xuanpin/api/mk-yesterday-top100`。
- 后端服务层统一过滤，默认情况下前端不再收到这些素材卡片。
- 前端通过 `ad_delivery=all` 请求全部素材；默认状态不需要传参。

## 非目标

- 不删除数据库中的历史素材记录。
- 不改变明空实时搜索仍会查询 `-rjc` 变体的行为；查询可以发生，默认展示结果必须过滤，选择 `全部` 时才展示。
- 不把 `mk_product_link` 中的 handle 当作本规则的 `product_code`，避免误伤链接带 `-rjc` 但本地产品 code 不带后缀的测试或历史数据。

## 验证

```bash
pytest tests/test_mingkong_materials.py tests/test_xuanpin_routes.py::test_xuanpin_mk_page_uses_xuanpin_tabs_and_api tests/test_xuanpin_routes.py::test_xuanpin_mk_material_library_api_reads_local_archive tests/test_xuanpin_routes.py::test_xuanpin_mk_yesterday_top300_api_reads_archive -q
python -m py_compile appcore/mingkong_materials.py
git diff --check
```
