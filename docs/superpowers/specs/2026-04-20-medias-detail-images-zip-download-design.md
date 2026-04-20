# 素材管理商品详情图 ZIP 打包下载设计

## 背景

素材管理的编辑弹窗里，当前语种的“商品详情图”已经支持：

- 批量上传
- 从商品链接一键下载
- 非英语语种从英语版一键翻译

但缺少一个高频导出动作：把当前语种的全部详情图按展示顺序打成压缩包，一次性下载给运营、设计或外包同事继续处理。

## 目标

在素材管理编辑页的“商品详情图”区域新增一个 `一键打包下载` 按钮，下载当前语种全部详情图的 ZIP 压缩包。

## 非目标

- 不支持跨语种一次打包
- 不支持用户自定义压缩包命名
- 不新增异步任务或后台排队
- 不改变现有详情图上传、导入、翻译逻辑

## 用户体验

### 按钮位置

按钮放在编辑弹窗的“商品详情图”操作区，与现有按钮同级：

- `选择图片批量上传`
- `从商品链接一键下载`
- `一键打包下载`

保持现有 Ocean Blue Admin 风格，沿用 `oc-btn ghost sm`。

### 可用性

- 当前语种没有详情图时，按钮禁用
- 当前语种有详情图时，按钮可点
- 点击后直接触发浏览器下载，不弹二次确认

### 下载内容

只下载**当前语种**的详情图，按当前展示顺序导出。

ZIP 结构：

```text
{product_code}_{lang}_detail-images.zip
└─ {product_code}_{lang}_detail-images/
   ├─ 01.jpg
   ├─ 02.png
   └─ 03.webp
```

命名规则：

- 压缩包目录名：`{product_code}_{lang}_detail-images`
- ZIP 文件名：`{product_code}_{lang}_detail-images.zip`
- 图片文件名：按当前排序重命名为 `01`、`02`、`03`，保留原扩展名

若 `product_code` 为空，则回退到 `product-{pid}`。

## 后端设计

在 `web/routes/medias.py` 新增下载路由：

- `GET /medias/api/products/<pid>/detail-images/download-zip?lang=en`

行为：

1. 校验产品存在且当前用户可访问
2. 校验语种合法
3. 查询该产品该语种下的详情图，按 `sort_order ASC, id ASC`
4. 若没有图片，返回 `404`
5. 逐张从 TOS media bucket 下载到临时文件
6. 将图片写入 ZIP 的子目录，使用顺序重命名
7. 返回 `application/zip` 附件响应
8. 清理临时文件

实现约束：

- 不依赖前端预览图缓存
- 不把原始 object key 暴露给前端
- 不改变详情图表结构
- 复用现有 `tos_clients.download_media_file` 能力

## 前端设计

在 `web/templates/_medias_edit_detail_modal.html` 新增按钮节点：

- `id="edDetailImagesDownloadZipBtn"`

在 `web/static/medias.js` 中：

- 基于当前详情图列表数量控制按钮 `disabled`
- 点击时读取当前产品 ID 与激活语种
- 通过 `window.location` 或动态创建 `<a>` 指向后端 ZIP 路由
- 详情图刷新后同步按钮状态

## 错误处理

- 无图片：按钮禁用；若直调接口，后端返回 `404`
- 非法语种：返回 `400`
- TOS 下载失败：返回 `502` 或 `500`，前端提示“打包下载失败”

## 测试

### 路由测试

新增测试覆盖：

- 有详情图时返回 ZIP
- ZIP `Content-Type` 为 `application/zip`
- ZIP 内包含一级目录
- 目录内文件名按 `01.jpg` / `02.png` 顺序重命名
- 无详情图时返回 `404`

### 页面/脚本测试

新增测试覆盖：

- 编辑弹窗模板包含 `edDetailImagesDownloadZipBtn`
- `medias.js` 包含下载按钮事件绑定和目标路由字符串

## 风险与取舍

### 临时文件清理

采用和现有 ZIP 下载类似的“写临时文件 -> 读入 ZIP -> 删除”的方式，避免一次性把远端对象长期落盘在仓库目录。

### 内存占用

单次最多 20 张详情图，当前约束下可接受直接构建 ZIP 返回；暂不引入流式 ZIP。

## 结论

采用“当前语种、同步打包、顺序重命名、ZIP 内单目录”的方案。它最符合现有编辑页心智，也最适合素材交接场景。
