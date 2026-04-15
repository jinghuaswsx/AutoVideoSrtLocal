# Medias 多语种管理设计

日期：2026-04-16
作者：brainstorming 产出
状态：已确认，待 writing-plans 生成实施计划

## 背景

当前 `/medias/` 页面把"产品 - 视频素材 - 文案"捆成三张表（`media_products` / `media_items` / `media_copywritings`）。上线时只考虑了单语种场景。现需要扩展为**一个产品管理多语种（德/法/西/意/日/韩，默认英语）的视频素材、文案、产品主图**。

## 核心设计决策

| 决策点 | 选择 |
|---|---|
| 产品与语言关系 | **A. 单产品跨语种**。`product_code` 全局唯一，跨语种共享。 |
| 支持语种 | 英语（默认）+ 德/法/西/意/日/韩，共 7 种，配置表可扩展 |
| 视频素材语种粒度 | 每条素材必带 `lang`，无则默认 `en`；不设"通用"档 |
| 文案语种粒度 | 每条文案必带 `lang`；同一产品同一语种可有多条 |
| 产品主图语种粒度 | 按语种分；英语主图**硬必填**；其他语种缺失 fallback 到英语 |
| 视频封面 | 跟视频走（视频本身已归属某语种）；不独立建语种维度 |
| 编辑页组织方式 | **语种 tab 优先**：tab 切换整套内容（主图+视频+文案） |
| Tab 角标 | 视频数 0 红 `--danger` / >0 绿 `--success`，数字贴右 |
| 列表页展示 | 每行右侧 7 个语种 chip，有素材海洋蓝实心 / 无灰描边；缺 en 主图左侧竖条红色警示 |
| 老数据迁移 | 全部标 `lang='en'` |
| 语种存储 | 配置表 `media_languages`，未来加语种只需插行 |

## 数据模型

### 新增：`media_languages`

```sql
CREATE TABLE media_languages (
  code       VARCHAR(8)  PRIMARY KEY,
  name_zh    VARCHAR(32) NOT NULL,
  sort_order INT         NOT NULL DEFAULT 0,
  enabled    TINYINT(1)  NOT NULL DEFAULT 1
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO media_languages (code, name_zh, sort_order) VALUES
  ('en','英语',1),('de','德语',2),('fr','法语',3),
  ('es','西班牙语',4),('it','意大利语',5),
  ('ja','日语',6),('ko','韩语',7);
```

### 修改：`media_items`

```sql
ALTER TABLE media_items
  ADD COLUMN lang VARCHAR(8) NOT NULL DEFAULT 'en' AFTER product_id,
  ADD KEY idx_product_lang (product_id, lang, deleted_at);
```

老行自动落 `en`。

### 修改：`media_copywritings`

```sql
ALTER TABLE media_copywritings
  ADD COLUMN lang VARCHAR(8) NOT NULL DEFAULT 'en' AFTER product_id,
  ADD KEY idx_product_lang (product_id, lang, idx);
```

### 新增：`media_product_covers`（取代列 `media_products.cover_object_key`）

```sql
CREATE TABLE media_product_covers (
  product_id INT          NOT NULL,
  lang       VARCHAR(8)   NOT NULL,
  object_key VARCHAR(255) NOT NULL,
  updated_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
                                    ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (product_id, lang)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

**回填**：

```sql
INSERT INTO media_product_covers (product_id, lang, object_key)
SELECT id, 'en', cover_object_key
FROM media_products
WHERE cover_object_key IS NOT NULL AND deleted_at IS NULL;
```

`media_products.cover_object_key` **暂保留**以防部署期间旧代码读取；稳定后再追加迁移 DROP。

### 未变

- `media_items.cover_object_key`（视频封面）——视频本身已属某语种，封面跟视频走。
- `media_products.product_code`——全局唯一，跨语种共享。

## API 改动

### 新增：`GET /api/languages`
返回启用语种列表：
```json
[{"code":"en","name_zh":"英语","sort_order":1}, ...]
```
前端渲染 tab 顺序。

### 列表：`GET /api/products`
返回项增加 `lang_coverage` 字段：
```json
{
  "id": 123,
  "name": "...",
  "lang_coverage": {
    "en": {"items": 3, "copy": 2, "cover": true},
    "de": {"items": 0, "copy": 0, "cover": false},
    ...
  },
  ...
}
```
驱动列表页 7 chip 渲染。

### 详情：`GET /api/products/:pid`
仍扁平返回 items/copywritings 数组，但每项带 `lang` 字段，前端按当前 tab 过滤。响应加 `covers: {"en":"...","de":"...",...}`（key = lang，value = object_key）。

### 产品写接口

- `POST /api/products` 创建可选附带 en 主图；未附带时产品处于"缺 en 主图"状态，列表红警。
- `PUT /api/products/:pid` **硬校验** `media_product_covers` 存在 `lang='en'` 记录，否则 400。
- `DELETE /api/products/:pid` 不变，软删。

### 视频素材与文案写接口

所有写接口（bootstrap/complete/replace）增加 `lang` 参数，默认 `en`。服务端校验 `lang` 属于 `media_languages` 且 `enabled=1`。

### 产品主图接口（从单图升级为 per-lang）

| 接口 | 改动 |
|---|---|
| `POST /api/products/:pid/cover/bootstrap` | body 增加 `lang` |
| `POST /api/products/:pid/cover/complete`  | body 增加 `lang`，写入 `media_product_covers` |
| `POST /api/products/:pid/cover/from-url`  | body 增加 `lang` |
| `DELETE /api/products/:pid/cover?lang=xx` | 新增；`lang='en'` 禁止删（返回 400） |
| `GET /medias/cover/:pid?lang=de` | 找 de 主图，缺则 fallback 到 en；都缺 404 |

## UI 改动

### 列表页 `/medias/`

每行追加**语种覆盖条**：
- 7 个小 chip，按 `media_languages.sort_order` 排列
- 有视频素材：海洋蓝实心（`--accent`）
- 无视频素材：灰描边（`--border-strong`）
- Hover chip 显示 tooltip："德语 · 3 个视频 / 2 条文案 / 已设主图"
- 缺 en 主图：行左侧竖条 `--danger` 警示

### 编辑产品弹窗

顶部增加**语种 tab 栏**：

```
┌────────────────────────────────────────────────────────┐
│ 产品名：XX 精华液    code: xx-essence          [保存] │
├────────────────────────────────────────────────────────┤
│ [EN ·3·] [DE ·0·] [FR ·2·] [ES ·0·] [IT ·1·] [JA] [KO]│
├────────────────────────────────────────────────────────┤
│  ▸ 产品主图（此语种） ────────── [上传] [URL 导入]     │
│  ▸ 视频素材（此语种） ────────── [+ 上传视频]          │
│  ▸ 文案（此语种） ────────────── [+ 新增]              │
└────────────────────────────────────────────────────────┘
```

**Tab 样式**：
- 激活 tab：`--sidebar-bg-active` 底 + 白字
- 未激活 tab：`--bg-subtle` 底 + `--fg` 字
- 角标数字：tab 文字右侧，视频数 0 红 dot、>0 绿 dot + 数字

**EN tab 特殊**：
- 主图缺失时红色警示条"必须上传英文产品主图才能保存"
- 主图块不提供"使用 EN 默认"选项

**其他语种 tab 主图块**：
- 未上传时占位图右上小字"当前使用 EN 默认主图"，整块可点击上传
- 已上传时旁边出现"删除（回退到 EN）"按钮

**切 tab 前**：若有未保存改动，弹确认框。

**所有视频/文案上传与保存**：自动带当前激活 `lang`。

## 迁移脚本

`db/migrations/2026_04_16_medias_multi_lang.sql`：

1. 建 `media_languages` 并 seed 7 行
2. `ALTER media_items ADD lang + idx_product_lang`
3. `ALTER media_copywritings ADD lang + idx_product_lang`
4. 建 `media_product_covers`
5. 回填 `media_product_covers`（见上）
6. **不** DROP `media_products.cover_object_key`；下一次迁移再清理

## 代码落地顺序

1. **migration** — SQL + `appcore/medias.py` DAO 新增 `list_languages()` / 封面表 CRUD / item & copy 写接口加 lang
2. **API** — `web/routes/medias.py` 所有写接口接 lang；列表接口拼 `lang_coverage`；封面接口走新表；新增 `GET /api/languages`
3. **前端列表页** — `web/templates/medias_list.html` + `web/static/medias.js`：语种覆盖条
4. **前端编辑页** — 语种 tab 栏 + tab 切换过滤 + 三块内容 + EN 主图硬校验
5. **回归观察后** — 追加迁移 DROP 旧 cover 列

## 兼容性策略

- 新代码写入封面**只**写 `media_product_covers`；读取时若表里没有 en 记录，fallback 读 `media_products.cover_object_key`（作为只读兼容窗口）。
- 本项目单实例部署，无需灰度。

## 非目标（YAGNI）

- 下游视频生成 pipeline 如何按语种取素材：不在本次 scope，等这里数据结构稳定后再设计
- 语种间素材/文案的"一键复制"：先不做，观察使用习惯
- 跨语种通用素材（无口播 B-roll）：明确否决
- 语种级归档/禁用：先靠 `media_languages.enabled` 控制全局开关即可
