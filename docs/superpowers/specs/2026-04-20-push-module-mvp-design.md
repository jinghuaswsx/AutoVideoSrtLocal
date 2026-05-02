# Push Module 集成 MVP 设计

> **已废弃（2026-04-20 同日）**：纯前端直连方案因 CORS 无法放行，方案转向
> `AutoPush/` 本地代理子项目（FastAPI + 原生前端，部署在能访问内网的本地机器）。
> 本文档作为设计演进的历史保留，实际实现见 [AutoPush/README.md](../../../AutoPush/README.md)。

**日期**：2026-04-20
**目标**：把 `push-module/` 里的两个 React 组件（PushCreate / PushPayload）以最小成本集成到现有 `/pushes/` 推送管理页面，走**纯前端直连**模式验证端到端路径通不通。

## 1. 背景

- `push-module/` 是从另一个项目拆出来的包（见 `push-module/README.md`）。包含两个 React + JSX 组件：
  - `PushCreate.jsx`：按 `product_code` 从外部 AutoVideo OpenAPI（`http://172.30.254.14`）拉素材，展示并让用户编辑推送 JSON；当前版本没有"推送"按钮。
  - `PushPayload.jsx`：按 `product_code` + `lang` 从同一 OpenAPI 拿已组装好的推送载荷，预览视频/封面，校验通过后由浏览器直接 POST 到 `http://172.17.254.77:22400/dify/shopify/medias`。
- 本项目已有推送管理 [web/routes/pushes.py](../../../web/routes/pushes.py)、[pushes_list.html](../../../web/templates/pushes_list.html)、[pushes.js](../../../web/static/pushes.js)，但数据源是本项目自己的 `media_items` 表，和 push-module 的外部 OpenAPI 是两套独立数据。
- MVP 目的：让用户在一个页面里验证"浏览器 → 外部 OpenAPI → 浏览器 → 下游推送服务"这条纯前端路径在当前部署环境下是否可用。

## 2. 非目标（YAGNI）

- 不替换、不改现有基于本地 DB 的推送列表逻辑。
- 不引入 React / Babel / Vite 等前端构建链。
- 不处理 CORS（失败时由用户在 DevTools 看报错决定下一步）。
- 不做鉴权、不做表单持久化、不写回本地 DB。
- 不加后端代理（即 push-module README 第 7 节的"后端代理备案"不实施）。

## 3. 架构

### 3.1 页面结构

复用 `/pushes/` 路由，在 [pushes_list.html](../../../web/templates/pushes_list.html) 顶部加 3 个 tab：

| Tab | 内容 |
| --- | --- |
| 推送列表（默认） | 原有内容原封不动 |
| 推送创建 | 对应 `PushCreate.jsx` 的原生 JS 改写 |
| 推送载荷 | 对应 `PushPayload.jsx` 的原生 JS 改写 |

切 tab 纯前端切 DOM 显示/隐藏，URL 不变。默认展示「推送列表」。

### 3.2 文件清单

| 动作 | 文件 | 说明 |
| --- | --- | --- |
| 修改 | [config.py](../../../config.py) | 加 3 个常量：`AUTOVIDEO_BASE_URL` / `AUTOVIDEO_API_KEY` / `PUSH_MEDIAS_TARGET` |
| 修改 | [web/routes/pushes.py](../../../web/routes/pushes.py) | `index()` 多传一个 `push_direct_config` dict 给模板 |
| 修改 | [web/templates/pushes_list.html](../../../web/templates/pushes_list.html) | 加 tab 切换条 + 两个 tab 容器 `<div>` + 注入 `window.PUSH_DIRECT_CONFIG` |
| 修改 | [web/static/pushes.css](../../../web/static/pushes.css) | 尾部追加 tab 切换 + 两个新页面的表单布局样式（复用 `--oc-*` 变量） |
| 新增 | `web/static/pushes_direct.js` | ES module，内含 materials API + 两个渲染函数 |

不改动：[pushes.js](../../../web/static/pushes.js)、[appcore/pushes.py](../../../appcore/pushes.py)、任何数据库 schema。

### 3.3 `pushes_direct.js` 结构

单文件 ES module，按下列顺序组织：

```
// 1. config 读取
const CFG = window.PUSH_DIRECT_CONFIG;

// 2. 从 push-module/frontend/api/materials.js 搬过来的三个函数（改 import → 读 CFG）
async function fetchMaterials(productCode) { ... }
async function fetchPushPayload(productCode, lang) { ... }
async function pushMedias(payload) { ... }
async function requestUpstream(url) { ... }        // 原 materials.js 内部工具
function normalizeMaterialsResponse(raw) { ... }   // 原 materials.js 内部工具

// 3. 载荷校验（搬自 PushPayload.jsx 的 validatePayload）
function validatePayload(payload) { ... }

// 4. 两个页面渲染器
export function renderPushCreate(container) { ... }
export function renderPushPayload(container) { ... }

// 5. tab 初始化 + 路由启动
function initTabs() { ... }
initTabs();
```

**渲染方式**：不用 React。每个渲染器维护一个本地 `state` 对象，提供 `setState(partial)` 统一触发重渲。首次调用时 `container.innerHTML = <完整模板字符串>`，绑定事件；`setState` 只更新必要的子节点（表单大部分靠 `value` 双向绑定，不重建 DOM）。

**和 pushes.js 的共存**：`pushes.js` 继续管「推送列表」tab，`pushes_direct.js` 管另外两个 tab，互不 import、不共享全局变量（window 上只约定 `PUSH_DIRECT_CONFIG` 和 `PUSH_IS_ADMIN`）。

### 3.4 配置注入

`config.py` 新增：
```python
# 推送管理 - 纯前端直连模式（push-module 方案）
AUTOVIDEO_BASE_URL = _env("AUTOVIDEO_BASE_URL", "http://172.30.254.14")
AUTOVIDEO_API_KEY = _env("AUTOVIDEO_API_KEY", "")
PUSH_MEDIAS_TARGET = _env("PUSH_MEDIAS_TARGET", "http://172.17.254.77:22400/dify/shopify/medias")
```

Flask `index()` 视图：
```python
push_direct_config = {
    "autovideoBaseUrl": config.AUTOVIDEO_BASE_URL,
    "autovideoApiKey":  config.AUTOVIDEO_API_KEY,
    "pushMediasTarget": config.PUSH_MEDIAS_TARGET,
}
return render_template(..., push_direct_config=push_direct_config)
```

模板里：
```html
<script>window.PUSH_DIRECT_CONFIG = {{ push_direct_config | tojson }};</script>
<script type="module" src="/static/pushes_direct.js"></script>
```

**已知限制**：`AUTOVIDEO_API_KEY` 会随模板被用户浏览器看到。push-module README 第 2.3 节已经明确可接受此风险（内网只读 key）。

### 3.5 样式

**不引入** `push-module/frontend/push-styles.css`。该文件用 24px 圆角 + `#1677ff` 硬编码蓝 + `#087443` 硬编码绿，和项目 `CLAUDE.md` 的 Ocean Blue 规范（hue 200-240、圆角 ≤ 12px）冲突。

在 [pushes.css](../../../web/static/pushes.css) 尾部追加新样式段：
- `.push-tabs` / `.push-tab` / `.push-tab.active`：tab 切换条（与 `.badge` / `.btn-push` 同色系）
- `.push-tab-panel[hidden]`：隐藏态
- `.push-form-card`：对应 push-module 的 `.editor-card`，改成 `--oc-r-lg` 圆角 + `--oc-shadow-sm`
- `.push-form-grid` / `.push-array-item` / `.push-media-preview`：布局类
- `.push-json-preview`：对应 `.json-preview`，改成 `--oc-bg-subtle` 底色 + `--oc-fg` 文字

JSX 里的 class 全部映射到新前缀（`editor-card` → `push-form-card` 等），避免和现有全局类名冲突。

## 4. 用户可见行为

### 4.1 推送创建 tab
- 顶部「查询区」：输入 `product_code` + 「获取」按钮
- 点击后调 `fetchMaterials(code)`，成功则把 `product.name` 回填到表单 `product_name`，并在下方文本区里展示完整原始 JSON
- 下方大表单（两列网格）：`mode` / `product_name` / `source` / `level` / `author` / `push_admin` / `roas` / `selling_point` + 数组编辑区（texts / product_links / videos / platforms / tags）
- 最底部实时 JSON 预览（跟随表单状态变化重新 `JSON.stringify`）
- **没有「推送」按钮**（跟原版一致，保持 MVP 纯展示）

### 4.2 推送载荷 tab
- 两列输入：`product_code` + `lang`
- 「加载数据」→ 调 `fetchPushPayload(code, lang)`，在下方 JSON 文本区展示原始返回，同时把 `videos[]` 解出来渲染封面 + 视频预览
- 「推送」按钮（loading 时 disabled）→ 先 `validatePayload` 校验，失败则红色 banner 列出每个字段错；通过则 POST 到 `CFG.pushMediasTarget`，把响应/错误 JSON 展示到下方

### 4.3 CORS / Mixed Content 失败态
- `fetch` 抛 `TypeError: Failed to fetch` 时，banner 展示：
  > 网络请求失败：{原始 message}（可能是 CORS 未放行或地址不可达。请在 DevTools Network 查看详情。）

## 5. 验证标准

MVP 算通过，当且仅当：

1. 打开 `/pushes/` 能看到 3 个 tab，默认「推送列表」与改动前完全一致。
2. 切到「推送创建」，输入一个 `product_code`，点「获取」后能看到下方原始 JSON 响应（200 OK 或有意义的错误）。如果是 CORS 错，能在 DevTools Network 看到红色 preflight 失败——说明前端侧代码是对的，问题在上游服务端。
3. 切到「推送载荷」，输入 `product_code` + `lang`，点「加载数据」能看到视频/封面预览 + JSON；点「推送」能打到下游（成功/失败都算路径打通，关键是看到 Network 上真的发出了请求）。

## 6. 开放问题与风险

- **风险 A — CORS**：验证路径通不通的核心拦路虎。按 push-module README 第 2.1 节，浏览器直连 `172.30.254.14` 和 `172.17.254.77:22400` 需要两个服务端都加 `Access-Control-Allow-*` 头。本 MVP 不解决 CORS，跑不通时由用户决定是否让对方服务端加头，或回退走后端代理。
- **风险 B — Mixed Content**：如果 `/pushes/` 是通过 HTTPS 访问的，浏览器会拒绝调 HTTP 地址。MVP 不处理，用户部署时按需选 HTTP 访问。
- **风险 C — API Key 暴露**：已在 3.4 注明，MVP 接受风险。

## 7. 非本期工作（后续可能）

- 把 push-module 的"推送创建"表单也加上「直接推送」按钮（push-module README 提示：注意提交前把 `source/level/roas/size/width/height` 从字符串转数字）。
- 把纯前端两个 tab 和本地 DB 推送列表做数据联动（例如列表上点素材 → 跳到载荷 tab 并预填）。
- 加后端代理 fallback，供 CORS 不通的部署使用。
- 表单持久化（localStorage）。
