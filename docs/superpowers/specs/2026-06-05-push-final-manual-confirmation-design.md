# 推送最终人工确认门禁设计

- 日期：2026-06-05
- 上位锚点：
  - `docs/superpowers/specs/2026-04-18-push-management-design.md`
  - `docs/superpowers/specs/2026-05-20-task-center-child-acceptance-design.md`
  - `docs/superpowers/specs/2026-05-22-push-manual-link-confirm-design.md`

## 背景

推送管理的 `pending` 状态由 `appcore.pushes.compute_readiness()` 动态计算。当前视频、封面、文案、链接、商品图等自动条件满足后，素材会自动进入待推送列表。实际流程里，翻译任务结束后运营人员可能还要重新调整视频或最终复核，因此不能把自动产物完成等同于可推送。

任务详情里已有逐项验收和人工确认事件 `manual_step_confirmed`，但现有“最终素材和链接确认”实际复用的是 `lang_supported`，它代表商品广告语种适配，会随产品配置自动通过，不能表达最终运营确认。

## 目标

1. 新增 `final_push_confirmed` 作为推送就绪必要条件。
2. 任务中心子任务详情在商品链接与图片状态之后展示“最终推送人工确认”验收项。
3. 只有运营人员点击该验收项的“人工确认”并写入任务事件后，对应素材才可进入推送管理待推送列表。
4. 推送管理列表的“推送必要条件状态”展示“推送人工确认”，缺失时禁用推送按钮并显示缺项。

## 非目标

- 不新增数据库表或迁移；复用 `task_events` 的人工确认事件。
- 不改变已推送素材的 `pushed` 状态。
- 不把 `/pushes` 弹窗里的 `manual_link_confirmed` 改成持久状态；它仍只用于本次跳过链接探活。
- 不把历史回填当作审核通过动作；历史回填只给已有目标素材的历史子任务补“最终推送人工确认”事件，用来兼容新门禁。

## 设计

### Readiness

`appcore.pushes.compute_readiness()` 返回新增布尔项：

```python
{
    "final_push_confirmed": False,
}
```

默认值为 `False`。当 `media_items.task_id` 对应子任务存在 `manual_step_confirmed` 事件，且 payload key 为 `final_push_confirmation` 时，`appcore.tasks.manual_confirmed_child_readiness_keys()` 映射出 `final_push_confirmed`，`compute_readiness()` 将该项置为 `True`。

`pushes.is_ready()` 继续对所有非 `_reason` 项做 `all()`，因此 `final_push_confirmed=False` 会让状态保持 `not_ready`。

### 任务中心

新增子任务验收步骤：

| step key | label | readiness key |
| --- | --- | --- |
| `final_push_confirmation` | 最终推送人工确认 | `final_push_confirmed` |

该步骤不提供“手动提交”文件/文案，只提供“人工确认”。确认后写入 `task_events`，并沿用现有 `confirm_child_step()` 的推送状态缓存刷新逻辑。

为兼容已经结束但尚未推送的历史子任务，该步骤允许在 `assigned`、`review`、`done` 状态下确认；其它人工兜底步骤仍只允许 `assigned` / `review`。

“最终推送人工确认”的按钮不放在标题右侧。它在验收项正文里渲染为大号蓝色胶囊按钮，按钮文案为“最终推送确认”，按钮上方或旁边展示蓝色加粗提示“确认后才可推送”，避免运营人员把该步骤误解成普通兜底标记。

点击该步骤的确认按钮后，服务端必须在同一个确认事务里把对应子任务置为 `done`，写入完成事件，并继续触发父任务完成汇总。这样最终确认是“任务完成”的最后动作，不能出现前端显示确认但任务仍停留在待处理/待审核的状态。

### 历史数据回填

上线后执行一次历史回填脚本。回填范围：

- 子任务存在目标素材 `media_items.task_id = tasks.id`；
- 子任务状态为 `assigned`、`review` 或 `done`；
- 尚未写入 `manual_step_confirmed` 且 payload 包含 `final_push_confirmation`。

脚本仅插入 `manual_step_confirmed` 事件并刷新对应推送状态缓存；不改已推送素材状态，也不把未完成的历史子任务批量改为 `done`。后续新任务仍必须由运营人员点击最终确认按钮，点击后才进入子任务完成状态。

### 推送管理

`web/static/pushes.js` 的 readiness 文案新增：

- key：`final_push_confirmed`
- label：`推送人工确认`

列表把它放在第二行，与“图片/链接确认”一起展示。缺失时，未就绪行的推送按钮 tooltip 包含“推送人工确认”。

### API 文档

`docs/明空素材推送接口.md` 的 readiness 说明补充“推送人工确认”，让开放接口调用方知道 `status=pending` 也受该人工门禁影响。

## 验证

1. `pytest tests/test_appcore_pushes.py::test_compute_status_requires_final_push_manual_confirmation tests/test_appcore_pushes.py::test_compute_status_pending_after_final_push_manual_confirmation -q`
2. `pytest tests/test_appcore_tasks.py::test_child_acceptance_payload_includes_final_push_confirmation_gate tests/test_appcore_tasks.py::test_final_push_confirmation_can_be_confirmed_after_child_done -q`
3. `pytest tests/test_task_center_manual_confirm_ui.py tests/test_pushes_ui_assets.py::test_pushes_script_shows_final_push_confirmation_readiness -q`
4. `pytest tests/test_final_push_confirmation_backfill.py -q`
5. `python -m compileall appcore/pushes.py appcore/tasks.py web/routes/tasks.py tools/backfill_final_push_confirmation.py`
