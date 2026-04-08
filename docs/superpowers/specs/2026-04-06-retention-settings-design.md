# 项目保留周期管理设计

## 1. 目标

为 admin 提供可视化界面，管理各模块项目的保留周期。支持全局默认值 + 按模块覆盖的两层配置。保留周期从项目完成时开始计算。

## 2. 关键决策

| # | 决策 | 选择 | 原因 |
|---|------|------|------|
| 1 | 配置粒度 | 全局默认 + 按模块覆盖 | 灵活但不过度复杂 |
| 2 | 起算点 | 项目完成（done/error）时 | 处理中的项目不应过期 |
| 3 | 存储方式 | system_settings KV 表 | 持久化，admin 页面即时生效，可扩展 |
| 4 | 管理入口 | /admin/settings 页面 | 与现有 admin 用户管理页面并列 |
| 5 | 全局默认初始值 | 7 天（168 小时） | 用户指定 |

## 3. 数据层

### 3.1 新增 system_settings 表

```sql
CREATE TABLE system_settings (
    `key` VARCHAR(100) PRIMARY KEY,
    `value` TEXT NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);
```

### 3.2 初始数据

| key | value | 含义 |
|-----|-------|------|
| `retention_default_hours` | `168` | 全局默认保留周期（7 天） |

按模块覆盖的 key 命名规则：`retention_{project_type}_hours`。不存在的 key 表示使用全局默认。

支持的 project_type：

| project_type | 模块名称 |
|-------------|---------|
| `translation` | 视频翻译（英文） |
| `de_translate` | 视频翻译（德语） |
| `fr_translate` | 视频翻译（法语） |
| `copywriting` | 文案创作 |
| `video_creation` | 视频生成 |
| `text_translate` | 文案翻译 |

### 3.3 expires_at 计算时机

- **创建项目时**：`expires_at = NULL`（未完成不过期）
- **项目 status 变为 done 或 error 时**：`expires_at = NOW() + get_retention_hours(project_type)`
- **兜底清理**：`expires_at IS NULL` 且 `status NOT IN ('uploaded', 'running')` 且创建超过 30 天的项目，视为僵尸项目清理

## 4. 业务逻辑层

### 4.1 新增 appcore/settings.py

```python
def get_setting(key: str, default: str = None) -> str | None:
    """从 system_settings 表读取配置值"""

def set_setting(key: str, value: str) -> None:
    """写入或更新配置值"""

def get_retention_hours(project_type: str) -> int:
    """查模块覆盖值，没有则返回全局默认值，都没有则 168"""
    override = get_setting(f"retention_{project_type}_hours")
    if override:
        return int(override)
    default = get_setting("retention_default_hours")
    return int(default) if default else 168

def get_all_retention_settings() -> dict:
    """返回全局默认值 + 各模块覆盖值，供 admin 页面展示"""
```

### 4.2 修改 appcore/task_state.py

- `_db_upsert()`：创建项目时 `expires_at = NULL`（原来是硬编码 48 小时）
- 新增 `set_expires_at(task_id: str, project_type: str)`：根据配置计算并写入 `expires_at`

### 4.3 修改 appcore/runtime.py（及 runtime_de.py 等）

- 流水线 status 变为 `done` 或 `error` 时，调用 `task_state.set_expires_at(task_id, project_type)`

### 4.4 修改 appcore/cleanup.py

- 现有逻辑不变：清理 `expires_at < NOW() AND deleted_at IS NULL`
- 新增僵尸项目兜底：清理 `expires_at IS NULL AND status NOT IN ('uploaded', 'running') AND created_at < NOW() - INTERVAL 30 DAY AND deleted_at IS NULL`

## 5. Web 层

### 5.1 路由

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/admin/settings` | 系统设置页面 |
| POST | `/admin/settings` | 保存设置 |

### 5.2 页面设计

在现有 admin 蓝图中新增。页面内容为一个表单：

```
系统设置
─────────────────────────────
项目保留周期

全局默认：[  7  ] 天

按模块覆盖（留空表示使用全局默认）：
  视频翻译（英文）：[    ] 天
  视频翻译（德语）：[    ] 天
  视频翻译（法语）：[    ] 天
  文案创作：        [    ] 天
  视频生成：        [    ] 天
  文案翻译：        [    ] 天

                    [ 保存 ]
─────────────────────────────
```

### 5.3 侧边栏

在 layout.html 的 admin 区域下新增"系统设置"导航项，指向 `/admin/settings`。

## 6. 新增/修改文件清单

### 新增文件

| 文件 | 说明 |
|------|------|
| `appcore/settings.py` | 系统配置读写（get_setting / set_setting / get_retention_hours） |
| `web/templates/admin_settings.html` | admin 系统设置页面模板 |
| `db/migrations/add_system_settings.sql` | 建表 + 初始数据迁移 |

### 修改文件

| 文件 | 改动内容 |
|------|---------|
| `appcore/task_state.py` | `_db_upsert()` 中 `expires_at` 改为 NULL；新增 `set_expires_at()` |
| `appcore/runtime.py` | done/error 时调用 `set_expires_at()` |
| `appcore/runtime_de.py` | 同上 |
| `appcore/cleanup.py` | 新增僵尸项目兜底清理逻辑 |
| `web/routes/admin.py` | 新增 `/admin/settings` GET/POST 路由 |
| `web/templates/layout.html` | 侧边栏新增"系统设置"导航项 |

## 7. 不做的事情（第一版）

- 不做按用户级别的保留周期配置（只有全局 + 按模块）
- 不做保留周期到期前的提醒通知
- 不做手动延期功能（用户手动续期）
- 不做 system_settings 的通用 admin CRUD 界面（只做保留周期这一项设置）
