# Medias Raw Sources Modal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把素材管理里的“原始去字幕素材”从右侧抽屉改成约 2/3 宽度的居中大弹窗，并复刻编辑页的 9:16 素材卡预览效果，让每条素材都能在卡片内部通过“封面图 / 视频”双 tab 直接查看与播放。

**Architecture:** 保持 `web/routes/medias.py` 现有原始素材接口不变，改造集中在 `web/templates/medias_list.html` 和 `web/static/medias.js`。原始素材列表改为卡片网格，复用编辑页已有的 `oc-vitem / vtab / vpane` 视觉语义；视频 tab 不新增播放接口，而是在首次点击时懒插入 `src=video_url` 的 `<video controls>`。

**Tech Stack:** Flask, Jinja2 template, vanilla JavaScript, pytest, Playwright

---

### Task 1: 把原始素材入口从 drawer 改成 modal 壳子

**Files:**
- Modify: `tests/e2e/test_medias_raw_sources_flow.py:250-266`
- Modify: `web/templates/medias_list.html:163-253`
- Modify: `web/templates/medias_list.html:1219-1240`
- Modify: `web/static/medias.js:2882-2962`

- [ ] **Step 1: 先把浏览器测试改成期待 modal，而不是 drawer**

在 `tests/e2e/test_medias_raw_sources_flow.py` 里把现有的 drawer 断言替换成 modal 断言，先让测试失败：

```python
            raw_btn = page.get_by_role("button", name="原始视频 (0)")
            expect(raw_btn).to_be_visible()
            raw_btn.click()

            raw_modal = page.locator("#rsModal")
            expect(raw_modal).to_be_visible()
            expect(raw_modal.get_by_text("原始去字幕素材")).to_be_visible()
            expect(raw_modal.get_by_role("button", name="上传素材")).to_be_visible()
            expect(page.locator("#rsDrawer")).to_have_count(0)

            page.get_by_role("button", name="上传素材").click()
            expect(page.locator("#rsUploadMask")).to_be_visible()
```

- [ ] **Step 2: 跑 e2e，确认它先因为 `#rsModal` 不存在而失败**

Run:

```bash
pytest tests/e2e/test_medias_raw_sources_flow.py -q
```

Expected:

```text
FAILED tests/e2e/test_medias_raw_sources_flow.py::test_medias_raw_sources_flow
E   Locator expected to be visible: #rsModal
```

- [ ] **Step 3: 把模板里的 raw-source 容器替换成 modal 结构**

在 `web/templates/medias_list.html` 的 raw-source 样式区添加 modal 外壳样式，先只处理容器和滚动区，不在这一步做卡片 tab：

```html
.oc-rs-modal {
  width:min(66vw, 1200px);
  max-width:min(92vw, 1200px);
  max-height:min(88vh, 960px);
  display:flex;
  flex-direction:column;
}
.oc-rs-modal-head {
  display:flex;
  align-items:flex-start;
  justify-content:space-between;
  gap:var(--oc-sp-4);
  padding:var(--oc-sp-5);
  border-bottom:1px solid var(--oc-border);
}
.oc-rs-modal-toolbar {
  display:flex;
  justify-content:flex-start;
  padding:0 var(--oc-sp-5) var(--oc-sp-4);
}
.oc-rs-modal-body {
  flex:1;
  overflow:auto;
  padding:0 var(--oc-sp-5) var(--oc-sp-5);
}
.oc-rs-empty.err {
  border-color:var(--oc-danger);
  background:var(--oc-danger-bg);
  color:var(--oc-danger-fg);
}
@media (max-width: 1024px) {
  .oc-rs-modal { width:min(92vw, 1200px); }
}
```

把当前 drawer DOM 替换为：

```html
<div id="rsModalMask" class="oc-modal-mask oc" hidden>
  <div id="rsModal" class="oc-modal oc-rs-modal" role="dialog" aria-modal="true" aria-labelledby="rsModalTitle">
    <div class="oc-rs-modal-head">
      <div>
        <h3 id="rsModalTitle">原始去字幕素材</h3>
        <p id="rsSummary" class="oc-rs-summary">加载中</p>
      </div>
      <button type="button" id="rsModalClose" class="oc-icon-btn" aria-label="关闭">
        <svg width="16" height="16"><use href="#ic-close"/></svg>
      </button>
    </div>
    <div class="oc-rs-modal-toolbar">
      <button type="button" id="rsUploadBtn" class="oc-btn primary">
        <svg width="14" height="14"><use href="#ic-upload"/></svg>
        <span>上传素材</span>
      </button>
    </div>
    <div class="oc-rs-modal-body">
      <div id="rsList" class="oc-rs-list"></div>
    </div>
  </div>
</div>
```

- [ ] **Step 4: 同步调整 `web/static/medias.js`，让打开和关闭逻辑指向新 modal**

把 raw-source 相关 DOM 绑定改成 modal 命名，并保留其余行为不变：

```js
  const modalMask = $('rsModalMask');
  const modalClose = $('rsModalClose');
  const list = $('rsList');
  const summary = $('rsSummary');

  if (!modalMask || !modalClose || !list || !uploadMask || !uploadForm || !translateMask || !translateRsList || !translateLangs || !translatePreview || !translateSubmit) {
    return;
  }

  function renderRawSourceState(message, kind = '') {
    const isError = kind === 'error';
    list.innerHTML = `
      <div class="oc-rs-empty${isError ? ' err' : ''}">
        <div>${escapeHtml(message)}</div>
        ${isError ? '<button type="button" id="rsRetryBtn" class="oc-btn ghost sm">重新加载</button>' : ''}
      </div>`;
    const retryBtn = $('rsRetryBtn');
    if (retryBtn && uiState.currentPid) {
      retryBtn.addEventListener('click', () => refreshRawSourceList(uiState.currentPid));
    }
  }

  function openRawSourceModal(pid, name) {
    uiState.currentPid = String(pid);
    uiState.currentName = name || '';
    summary.textContent = '加载中';
    renderRawSourceState('加载原始去字幕素材中...');
    modalMask.hidden = false;
  }

  function closeRawSourceModal() {
    modalMask.hidden = true;
    uiState.currentPid = null;
    uiState.currentName = '';
    list.innerHTML = '';
    summary.textContent = '加载中';
  }
```

并把原来所有 `openRawSourceDrawer` / `closeRawSourceDrawer` 调用点替换成 `openRawSourceModal` / `closeRawSourceModal`，包括关闭按钮和遮罩点击逻辑。

- [ ] **Step 5: 重新跑 e2e，确认 modal 壳子改造通过**

Run:

```bash
pytest tests/e2e/test_medias_raw_sources_flow.py -q
```

Expected:

```text
1 passed
```

- [ ] **Step 6: 提交这一轮壳子改造**

Run:

```bash
git add tests/e2e/test_medias_raw_sources_flow.py web/templates/medias_list.html web/static/medias.js
git commit -m "feat: convert raw sources drawer to modal shell"
```

### Task 2: 把原始素材列表改成 180x320 的 9:16 卡片，并加封面图 / 视频双 tab

**Files:**
- Modify: `tests/e2e/test_medias_raw_sources_flow.py:256-275`
- Modify: `web/templates/medias_list.html:361-403`
- Modify: `web/templates/medias_list.html:1219-1240`
- Modify: `web/static/medias.js:2928-3019`

- [ ] **Step 1: 先写失败的浏览器断言，锁定卡片、tab 和视频播放器**

把 e2e 流程补成“上传后看到卡片、切视频 tab 后出现 `<video>`”：

```python
            expect(page.get_by_role("button", name="原始视频 (1)")).to_be_visible()

            card = page.locator("#rsList [data-rs-id='1001']")
            expect(card).to_be_visible()
            expect(card.get_by_role("button", name="封面图")).to_be_visible()
            expect(card.get_by_role("button", name="视频")).to_be_visible()
            expect(card.locator(".oc-rs-meta-line")).to_contain_text("时长")

            card.get_by_role("button", name="视频").click()
            expect(card.locator("video")).to_be_visible()
            expect(card.locator("video")).to_have_attribute("src", "/medias/raw-sources/1001/video")
```

- [ ] **Step 2: 跑 e2e，确认先因为没有 tab 和 video 节点而失败**

Run:

```bash
pytest tests/e2e/test_medias_raw_sources_flow.py -q
```

Expected:

```text
FAILED tests/e2e/test_medias_raw_sources_flow.py::test_medias_raw_sources_flow
E   Locator expected to be visible: get_by_role("button", name="封面图")
```

- [ ] **Step 3: 在模板样式里新增 raw-source 卡片网格和 180x320 预览框**

在 `web/templates/medias_list.html` 中加入 raw-source 专用样式，复用编辑页的 `.oc-vitem / .vtab / .vpane` 语言，但把卡片宽度锁到 `180px`：

```html
.oc-rs-list {
  display:grid;
  grid-template-columns:repeat(auto-fill, 180px);
  gap:var(--oc-sp-4);
  justify-content:flex-start;
}
.oc-rs-card {
  width:180px;
  border:1px solid var(--oc-border);
  border-radius:var(--oc-r-md);
  background:var(--oc-bg);
  padding:var(--oc-sp-3);
  display:flex;
  flex-direction:column;
  gap:var(--oc-sp-2);
}
.oc-rs-card .vbody {
  width:180px;
  max-width:100%;
  aspect-ratio:9 / 16;
}
.oc-rs-meta-line {
  font-size:12px;
  color:var(--oc-fg-muted);
  line-height:1.5;
}
.oc-rs-empty {
  grid-column:1 / -1;
  padding:var(--oc-sp-6);
  border:1px dashed var(--oc-border-strong);
  border-radius:var(--oc-r-md);
  text-align:center;
  color:var(--oc-fg-subtle);
}
.oc-rs-empty.err {
  border-style:solid;
  border-color:var(--oc-danger);
  background:var(--oc-danger-bg);
  color:var(--oc-danger-fg);
}
```

- [ ] **Step 4: 在 `web/static/medias.js` 里把单行 row 渲染改成卡片渲染**

把 `renderRawSourceRow` 改成卡片版，并新增 tab 绑定逻辑。这里不要请求新接口，直接懒插入 `it.video_url`：

```js
  function renderRawSourceCard(it) {
    const title = escapeHtml(it.display_name || `原始视频 #${it.id}`);
    const coverPane = it.cover_url
      ? `<img src="${escapeHtml(it.cover_url)}" alt="${title}" loading="lazy">`
      : `<div class="thumb-ph"><svg width="20" height="20" aria-hidden="true"><use href="#ic-film"/></svg></div>`;
    return `
      <article class="oc-rs-card oc-vitem" data-rs-id="${it.id}" data-video-url="${escapeHtml(it.video_url)}">
        <div class="vname" title="${title}">${title}</div>
        <div class="vtabs">
          <button type="button" class="vtab active" data-tab="cover">封面图</button>
          <button type="button" class="vtab" data-tab="video">视频</button>
        </div>
        <div class="vbody">
          <div class="vpane active" data-pane="cover">${coverPane}</div>
          <div class="vpane" data-pane="video">
            <div class="vvideo-ph">点击“视频”后加载播放</div>
          </div>
        </div>
        <div class="oc-rs-meta-line">${fmtRawDuration(it.duration_seconds)} · ${fmtRawSize(it.file_size)}</div>
        <div class="vactions">
          <span class="oc-hint">9:16 预览</span>
          <button type="button" class="oc-btn text sm danger-txt js-rs-del" data-rid="${it.id}">删除</button>
        </div>
      </article>`;
  }

  function ensureRawSourceVideoLoaded(card) {
    const pane = card.querySelector('[data-pane="video"]');
    if (!pane || pane.dataset.loaded === '1') return;
    const videoUrl = card.dataset.videoUrl || '';
    pane.innerHTML = `<div class="vvideo-ph">加载视频中...</div>`;
    const loading = pane.firstElementChild;
    const video = document.createElement('video');
    video.controls = true;
    video.preload = 'metadata';
    video.src = videoUrl;
    video.hidden = true;
    video.addEventListener('loadedmetadata', () => {
      if (loading) loading.remove();
      video.hidden = false;
      pane.dataset.loaded = '1';
    }, { once: true });
    video.addEventListener('error', () => {
      pane.innerHTML = '<div class="vvideo-ph err">视频加载失败，请重试</div>';
      pane.dataset.loaded = '';
    }, { once: true });
    pane.appendChild(video);
  }

  function bindRawSourceCards() {
    list.querySelectorAll('[data-rs-id]').forEach((card) => {
      const tabs = card.querySelectorAll('.vtab');
      const panes = card.querySelectorAll('.vpane');
      tabs.forEach((tab) => {
        tab.addEventListener('click', () => {
          tabs.forEach((node) => node.classList.toggle('active', node === tab));
          panes.forEach((pane) => pane.classList.toggle('active', pane.dataset.pane === tab.dataset.tab));
          if (tab.dataset.tab === 'video') ensureRawSourceVideoLoaded(card);
        });
      });
    });
  }
```

把 `refreshRawSourceList()` 改成：

```js
  async function refreshRawSourceList(pid) {
    try {
      const data = await requestJSON(`/medias/api/products/${pid}/raw-sources`);
      const items = data.items || [];
      setSummary(items);
      list.innerHTML = items.length
        ? items.map(renderRawSourceCard).join('')
        : '<div class="oc-rs-empty">还没有原始去字幕素材，先上传第一条再发起视频翻译。</div>';
      bindRawSourceCards();
      syncRawSourceCount(pid, items.length);
      return items;
    } catch (err) {
      summary.textContent = '加载失败';
      renderRawSourceState(`加载失败：${err.message || err}`, 'error');
      throw err;
    }
  }
```

- [ ] **Step 5: 跑 route 回归和 e2e，确认列表和视频 tab 都可用**

Run:

```bash
pytest tests/test_medias_raw_sources_routes.py tests/e2e/test_medias_raw_sources_flow.py -q
```

Expected:

```text
10+ passed
```

- [ ] **Step 6: 提交卡片和 tab 改造**

Run:

```bash
git add tests/e2e/test_medias_raw_sources_flow.py web/templates/medias_list.html web/static/medias.js
git commit -m "feat: add raw source preview cards"
```

### Task 3: 把原始素材上传弹窗改成编辑页风格的双列上传区

**Files:**
- Modify: `tests/e2e/test_medias_raw_sources_flow.py:257-264`
- Modify: `web/templates/medias_list.html:943-999`
- Modify: `web/templates/medias_list.html:1242-1270`
- Modify: `web/static/medias.js:2964-3005`

- [ ] **Step 1: 先写失败的浏览器断言，锁定新的上传区结构和预览反馈**

在 e2e 里点击“上传素材”后，断言新的双列上传区存在，并在设置文件后看到封面预览和视频文件名：

```python
            page.get_by_role("button", name="上传素材").click()
            expect(page.locator("#rsUploadMask")).to_be_visible()
            expect(page.locator("#rsUploadCoverBox")).to_be_visible()
            expect(page.locator("#rsUploadVideoBox")).to_be_visible()

            page.locator("#rsVideoInput").set_input_files(str(video_path))
            page.locator("#rsCoverInput").set_input_files(str(cover_path))

            expect(page.locator("#rsUploadCoverPreview")).to_be_visible()
            expect(page.locator("#rsUploadVideoName")).to_contain_text("sample.mp4")
```

- [ ] **Step 2: 跑 e2e，确认先因为上传区新选择器不存在而失败**

Run:

```bash
pytest tests/e2e/test_medias_raw_sources_flow.py -q
```

Expected:

```text
FAILED tests/e2e/test_medias_raw_sources_flow.py::test_medias_raw_sources_flow
E   Locator expected to be visible: #rsUploadCoverBox
```

- [ ] **Step 3: 在模板里把上传表单重排成“封面 9:16 + 视频选择框 + 名称输入”**

在 `web/templates/medias_list.html` 里把当前上传表单替换成编辑页风格的两列布局：

```html
.oc-rs-upload-grid {
  display:grid;
  grid-template-columns:270px 270px;
  gap:var(--oc-sp-5);
  justify-content:center;
}
.oc-rs-upload-video-fill {
  position:absolute;
  inset:0;
  display:flex;
  flex-direction:column;
  align-items:center;
  justify-content:center;
  gap:var(--oc-sp-2);
  padding:var(--oc-sp-4);
  background:var(--oc-bg-muted);
}
@media (max-width: 720px) {
  .oc-rs-upload-grid { grid-template-columns:1fr; }
}
```

上传表单主体改成：

```html
<form id="rsUploadForm" class="oc-rs-upload-form">
  <div class="oc-modal-body oc-rs-upload-body">
    <div class="oc-rs-upload-grid">
      <div class="oc-new-item-col">
        <div class="oc-new-item-label">封面图<span class="req">*</span></div>
        <div id="rsUploadCoverBox" class="oc-cover-9-16">
          <label for="rsCoverInput" id="rsUploadCoverDropzone" class="cover-dz">
            <div class="dz-icon"><svg width="18" height="18"><use href="#ic-upload"/></svg></div>
            <div class="dz-title">点击或拖拽上传封面</div>
            <div class="dz-hint">建议 9:16 / 1080x1920</div>
          </label>
          <img id="rsUploadCoverPreview" alt="原始素材封面预览" hidden>
        </div>
      </div>
      <div class="oc-new-item-col">
        <div class="oc-new-item-label">视频<span class="req">*</span></div>
        <label id="rsUploadVideoBox" class="oc-video-pick" for="rsVideoInput">
          <div id="rsUploadVideoEmpty" class="cover-dz">
            <div class="dz-icon"><svg width="18" height="18"><use href="#ic-upload"/></svg></div>
            <div class="dz-title">点击或拖拽选择视频</div>
            <div class="dz-hint">支持 MP4 / MOV</div>
          </div>
          <div id="rsUploadVideoFilled" class="oc-rs-upload-video-fill" hidden>
            <div class="oc-video-filled-icon"><svg width="28" height="28"><use href="#ic-film"/></svg></div>
            <div id="rsUploadVideoName" class="oc-video-filled-name"></div>
            <div id="rsUploadVideoSize" class="oc-video-filled-size"></div>
          </div>
        </label>
      </div>
    </div>
    <label class="oc-field">
      <span class="oc-label">名称</span>
      <input id="rsDisplayName" class="oc-input" type="text" name="display_name" maxlength="64" placeholder="例如：英文原始主视频">
    </label>
    <input id="rsVideoInput" type="file" name="video" accept="video/mp4,video/quicktime" required hidden>
    <input id="rsCoverInput" type="file" name="cover" accept="image/jpeg,image/png,image/webp" required hidden>
  </div>
  <div class="oc-modal-foot">
    <button type="button" id="rsUploadCancel" class="oc-btn ghost">取消</button>
    <button type="submit" id="rsUploadSubmit" class="oc-btn primary">提交</button>
  </div>
</form>
```

- [ ] **Step 4: 在 `web/static/medias.js` 里补封面预览、视频文件元信息和关闭时的 reset**

给上传弹窗增加轻量预览状态，不改变接口提交方式：

```js
  const uploadUi = {
    coverPreview: $('rsUploadCoverPreview'),
    coverDropzone: $('rsUploadCoverDropzone'),
    videoEmpty: $('rsUploadVideoEmpty'),
    videoFilled: $('rsUploadVideoFilled'),
    videoName: $('rsUploadVideoName'),
    videoSize: $('rsUploadVideoSize'),
  };
  let rsUploadCoverObjectUrl = '';

  function fmtUploadSize(bytes) {
    const value = Number(bytes || 0);
    if (!value) return '';
    return `${(value / (1024 * 1024)).toFixed(1)} MB`;
  }

  function setRawSourceUploadCover(file) {
    if (rsUploadCoverObjectUrl) {
      URL.revokeObjectURL(rsUploadCoverObjectUrl);
      rsUploadCoverObjectUrl = '';
    }
    if (!file) {
      uploadUi.coverPreview.hidden = true;
      uploadUi.coverPreview.removeAttribute('src');
      uploadUi.coverDropzone.hidden = false;
      return;
    }
    rsUploadCoverObjectUrl = URL.createObjectURL(file);
    uploadUi.coverPreview.src = rsUploadCoverObjectUrl;
    uploadUi.coverPreview.hidden = false;
    uploadUi.coverDropzone.hidden = true;
  }

  function setRawSourceUploadVideo(file) {
    const hasFile = !!file;
    uploadUi.videoEmpty.hidden = hasFile;
    uploadUi.videoFilled.hidden = !hasFile;
    uploadUi.videoName.textContent = hasFile ? file.name : '';
    uploadUi.videoSize.textContent = hasFile ? fmtUploadSize(file.size) : '';
  }

  $('rsCoverInput').addEventListener('change', (event) => {
    setRawSourceUploadCover(event.target.files[0] || null);
  });
  $('rsVideoInput').addEventListener('change', (event) => {
    setRawSourceUploadVideo(event.target.files[0] || null);
  });

  function closeRawSourceUpload() {
    uploadMask.hidden = true;
    uploadForm.reset();
    uploadSubmit.disabled = false;
    setRawSourceUploadCover(null);
    setRawSourceUploadVideo(null);
  }
```

- [ ] **Step 5: 重新跑 e2e，确认上传弹窗的新结构和预览行为通过**

Run:

```bash
pytest tests/e2e/test_medias_raw_sources_flow.py -q
```

Expected:

```text
1 passed
```

- [ ] **Step 6: 提交上传弹窗重排**

Run:

```bash
git add tests/e2e/test_medias_raw_sources_flow.py web/templates/medias_list.html web/static/medias.js
git commit -m "feat: restyle raw source upload modal"
```

### Task 4: 跑聚焦验证，确认没有把现有原始素材流程带坏

**Files:**
- Verify: `web/templates/medias_list.html`
- Verify: `web/static/medias.js`
- Verify: `tests/test_medias_raw_sources_routes.py`
- Verify: `tests/test_medias_raw_sources_translate.py`
- Verify: `tests/e2e/test_medias_raw_sources_flow.py`

- [ ] **Step 1: 跑当前环境可用的聚焦测试组合**

Run:

```bash
pytest tests/test_medias_raw_sources_routes.py tests/test_medias_raw_sources_translate.py tests/e2e/test_medias_raw_sources_flow.py -q
```

Expected:

```text
all selected tests passed
```

备注：

- 这一步不要把 `tests/test_appcore_medias_raw_sources.py` 放进成功口径，因为本地 `127.0.0.1:3306` 未启动时它会失败
- 如果 `tests/test_medias_raw_sources_routes.py` 再次超时，先单独记录环境限制，不要误判成 modal 改造回归

- [ ] **Step 2: 跑静态自检，确认没有残留 drawer 选择器和语义**

Run:

```bash
git diff --check
Get-ChildItem web -Recurse -File | Select-String -Pattern 'rsDrawer|oc-rs-drawer'
```

Expected:

```text
git diff --check has no output
no matches for rsDrawer or oc-rs-drawer in source files
```

- [ ] **Step 3: 目检最终 diff，确认关键要求全部命中**

逐项核对以下点：

```text
1. 原始素材入口打开的是居中 modal，不是右侧 drawer
2. modal 宽度约为页面 2/3
3. 每条素材卡是 180x320 的 9:16 预览框
4. 每卡都有“封面图 / 视频”双 tab
5. 点击“视频”tab 后能在卡内直接播放
6. 上传弹窗是双列结构，封面和视频选择反馈正常
```

- [ ] **Step 4: 如果 verification 没有再产生新修改，直接保留 Task 3 的最后一次 commit 作为交付 commit**

Run:

```bash
git status --short
```

Expected:

```text
working tree clean
```
