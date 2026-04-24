(function () {
  'use strict';

  const mask = document.getElementById('mtTranslateMask');
  if (!mask) return;

  const dialog = document.getElementById('mtTranslateDialog');
  const titleMeta = document.getElementById('mtTitleMeta');
  const closeBtn = document.getElementById('mtClose');
  const tabCreate = document.getElementById('mtTabCreate');
  const tabTasks = document.getElementById('mtTabTasks');
  const panelCreate = document.getElementById('mtPanelCreate');
  const panelTasks = document.getElementById('mtPanelTasks');
  const contentTypesBox = document.getElementById('mtContentTypes');
  const rawList = document.getElementById('mtRawList');
  const langList = document.getElementById('mtLangList');
  const videoFont = document.getElementById('mtVideoFont');
  const videoSizeGroup = document.getElementById('mtVideoSizeGroup');
  const subtitlePosition = document.getElementById('mtSubtitlePosition');
  const subtitleHint = document.getElementById('mtSubtitleHint');
  const previewFrame = document.getElementById('mtPreviewFrame');
  const previewVideo = document.getElementById('mtPreviewVideo');
  const subtitleOverlay = document.getElementById('mtSubtitleOverlay');
  const previewEmpty = document.getElementById('mtPreviewEmpty');
  const tasksMount = document.getElementById('mtTasksMount');
  const summary = document.getElementById('mtSummary');
  const cancelBtn = document.getElementById('mtCancel');
  const submitBtn = document.getElementById('mtSubmit');

  if (
    !dialog || !titleMeta || !closeBtn || !tabCreate || !tabTasks || !panelCreate || !panelTasks ||
    !contentTypesBox || !rawList || !langList || !videoFont || !videoSizeGroup || !subtitlePosition ||
    !subtitleHint || !previewFrame || !previewVideo || !subtitleOverlay || !previewEmpty ||
    !tasksMount || !summary || !cancelBtn || !submitBtn
  ) {
    return;
  }

  const CONTENT_TYPES = [
    { code: 'copywriting', label: '文案翻译', note: '调用多语言标题翻译并直接回填到小语种文案' },
    { code: 'detail_images', label: '商品详情图翻译', note: '按语种排队创建图片翻译任务，两种语言之间间隔 10 秒' },
    { code: 'video_covers', label: '视频封面翻译', note: '为所选语种批量创建封面图翻译任务，完成后自动回填' },
    { code: 'videos', label: '视频翻译', note: '按原始视频和语种依次创建任务，每个任务之间间隔 5 秒，并停在选声音步骤' },
  ];

  const FONT_FAMILIES = {
    Impact: 'Impact, Haettenschweiler, "Arial Narrow Bold", sans-serif',
    'Oswald Bold': '"Oswald", Impact, "Arial Narrow Bold", sans-serif',
    'Bebas Neue': '"Bebas Neue", Impact, "Arial Narrow Bold", sans-serif',
    'Montserrat ExtraBold': '"Montserrat", "Arial Black", sans-serif',
    'Poppins Bold': '"Poppins", "Arial Black", sans-serif',
    Anton: '"Anton", Impact, sans-serif',
  };

  const state = {
    productId: null,
    productName: '',
    product: null,
    rawSources: [],
    languages: [],
    selectedContentTypes: new Set(CONTENT_TYPES.map((item) => item.code)),
    selectedRawIds: new Set(),
    selectedLangs: new Set(),
    videoFont: 'Impact',
    videoSize: 10,
    subtitlePositionY: 0.68,
    activeTab: 'create',
    busy: false,
    tasksController: null,
    pointerDragging: false,
  };

  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, (ch) => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;',
    }[ch]));
  }

  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  function fmtBytes(value) {
    const size = Number(value || 0);
    if (!size) return '大小未知';
    if (size < 1024 * 1024) return `${Math.max(1, Math.round(size / 1024))} KB`;
    return `${(size / (1024 * 1024)).toFixed(size >= 100 * 1024 * 1024 ? 0 : 1)} MB`;
  }

  function fmtDuration(seconds) {
    const total = Number(seconds || 0);
    if (!Number.isFinite(total) || total <= 0) return '时长未知';
    if (total < 60) return `${total.toFixed(1)}s`;
    return `${(total / 60).toFixed(1)}m`;
  }

  async function requestJSON(url, options) {
    const response = await fetch(url, options);
    if (response.ok) return response.json();
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.error || payload.message || String(response.status));
  }

  function enabledLanguages() {
    return (state.languages || []).filter((lang) => lang && lang.code !== 'en' && lang.enabled !== false);
  }

  function rawHasTranslation(raw, langCode) {
    const code = String(langCode || '').trim().toLowerCase();
    const translations = (raw && raw.translations) || {};
    const status = translations[code] || null;
    return Boolean(status && status.status === 'translated');
  }

  function selectedRawSources() {
    return (state.rawSources || []).filter((raw) => state.selectedRawIds.has(Number(raw.id)));
  }

  function selectedRawTranslationStats(langCode) {
    const selected = selectedRawSources();
    const translated = selected.filter((raw) => rawHasTranslation(raw, langCode)).length;
    return {
      total: selected.length,
      translated,
      missing: Math.max(0, selected.length - translated),
      complete: selected.length > 0 && translated === selected.length,
      partial: translated > 0 && translated < selected.length,
    };
  }

  function missingVideoPairCount() {
    if (!state.selectedContentTypes.has('videos')) return 0;
    let count = 0;
    selectedRawSources().forEach((raw) => {
      state.selectedLangs.forEach((langCode) => {
        if (!rawHasTranslation(raw, langCode)) count += 1;
      });
    });
    return count;
  }

  function pruneCompletedSelectedLangs() {
    if (!state.selectedContentTypes.has('videos')) return;
    state.selectedLangs = new Set(
      Array.from(state.selectedLangs).filter((langCode) => !selectedRawTranslationStats(langCode).complete)
    );
  }

  function syncVideoConfigState() {
    const enabled = state.selectedContentTypes.has('videos');
    dialog.classList.toggle('mt-video-disabled', !enabled);
  }

  function renderContentTypes() {
    contentTypesBox.innerHTML = CONTENT_TYPES.map((item) => `
      <label class="mt-choice mt-choice--type">
        <input type="checkbox" value="${item.code}" ${state.selectedContentTypes.has(item.code) ? 'checked' : ''}>
        <span class="mt-choice__body">
          <strong>${esc(item.label)}</strong>
          <small>${esc(item.note)}</small>
        </span>
      </label>
    `).join('');
  }

  function languageName(code) {
    const normalized = String(code || '').trim().toLowerCase();
    const row = enabledLanguages().find((lang) => lang.code === normalized);
    return row ? (row.name_zh || row.code.toUpperCase()) : normalized.toUpperCase();
  }

  function rawTranslationSummary(raw) {
    const translations = (raw && raw.translations) || {};
    const translatedCodes = Object.keys(translations)
      .filter((code) => translations[code] && translations[code].status === 'translated')
      .sort();
    if (!translatedCodes.length) return '';
    return `已翻译：${translatedCodes.map(languageName).join(' / ')}`;
  }

  function renderRawSources() {
    if (!state.rawSources.length) {
      rawList.innerHTML = '<div class="mt-empty">当前商品还没有原始视频素材，先补充原始视频后才能发起翻译。</div>';
      return;
    }
    rawList.innerHTML = state.rawSources.map((item, index) => {
      const translatedSummary = rawTranslationSummary(item);
      return `
      <label class="mt-choice mt-choice--raw ${translatedSummary ? 'mt-choice--has-status' : ''}">
        <input type="checkbox" value="${item.id}" ${state.selectedRawIds.has(Number(item.id)) ? 'checked' : ''}>
        <span class="mt-choice__cover">
          ${item.cover_url ? `<img src="${esc(item.cover_url)}" alt="${esc(item.display_name || `原始视频 #${item.id}`)}" loading="lazy">` : '<span class="mt-choice__cover-ph">VIDEO</span>'}
        </span>
        <span class="mt-choice__body">
          <strong>${esc(item.display_name || `原始视频 #${item.id}`)}</strong>
          <small>${esc(fmtDuration(item.duration_seconds))} · ${esc(fmtBytes(item.file_size))}${index === 0 ? ' · 默认优先预览' : ''}</small>
          ${translatedSummary ? `<small class="mt-status-line">${esc(translatedSummary)}</small>` : ''}
        </span>
      </label>
    `;
    }).join('');
  }

  function renderLanguages() {
    const langs = enabledLanguages();
    if (!langs.length) {
      langList.innerHTML = '<div class="mt-empty">当前没有可用的小语种。</div>';
      return;
    }
    pruneCompletedSelectedLangs();
    langList.innerHTML = langs.map((lang) => {
      const stats = selectedRawTranslationStats(lang.code);
      const checkVideoCompletion = state.selectedContentTypes.has('videos');
      const disabled = checkVideoCompletion && stats.complete;
      const statusText = !checkVideoCompletion
        ? lang.code.toUpperCase()
        : (stats.complete
        ? `已完成 ${stats.translated}/${stats.total}，本次跳过`
        : (stats.partial ? `已完成 ${stats.translated}/${stats.total}，仅补未完成` : '未翻译'));
      return `
      <label class="mt-choice mt-choice--lang ${disabled ? 'mt-choice--done' : ''}">
        <input type="checkbox" value="${lang.code}" ${state.selectedLangs.has(lang.code) ? 'checked' : ''} ${disabled ? 'disabled' : ''}>
        <span class="mt-choice__body">
          <strong>${esc(lang.name_zh || lang.code.toUpperCase())}</strong>
          <small>${esc(statusText)}</small>
        </span>
      </label>
    `;
    }).join('');
  }

  function renderSizeButtons() {
    Array.from(videoSizeGroup.querySelectorAll('[data-size]')).forEach((button) => {
      const active = Number(button.dataset.size) === Number(state.videoSize);
      button.classList.toggle('active', active);
    });
  }

  function updateSubtitleHint() {
    subtitleHint.textContent = `${Math.round(Number(state.subtitlePositionY) * 100)}%`;
  }

  function renderSummary(extra) {
    if (extra) {
      summary.textContent = extra;
      return;
    }
    if (state.selectedContentTypes.has('videos') && state.selectedRawIds.size > 0 && state.selectedLangs.size === 0) {
      summary.textContent = '所选原始视频的可用目标语种都已有视频成品，本次不会重复创建视频翻译';
      return;
    }
    if (!state.selectedContentTypes.size || !state.selectedRawIds.size || !state.selectedLangs.size) {
      summary.textContent = '请选择翻译范围、原始视频和目标语言';
      return;
    }
    const rawCount = state.selectedRawIds.size;
    const langCount = state.selectedLangs.size;
    const types = CONTENT_TYPES.filter((item) => state.selectedContentTypes.has(item.code)).map((item) => item.label);
    const missingPairs = missingVideoPairCount();
    if (state.selectedContentTypes.has('videos') && missingPairs === 0) {
      summary.textContent = '所选原始视频的目标语种都已有视频成品，本次不会重复创建视频翻译';
      return;
    }
    const suffix = state.selectedContentTypes.has('videos') ? `，其中 ${missingPairs} 个视频组合待翻译` : '';
    summary.textContent = `将为 ${rawCount} 条原始视频发起 ${langCount} 个语种的 ${types.join(' / ')} 任务${suffix}，实际费用将在任务成功后生成`;
  }

  function updateSubmitState() {
    const hasVideoWork = !state.selectedContentTypes.has('videos') || missingVideoPairCount() > 0;
    const ready = !state.busy && state.selectedContentTypes.size > 0 && state.selectedRawIds.size > 0 && state.selectedLangs.size > 0 && hasVideoWork;
    submitBtn.disabled = !ready;
    submitBtn.textContent = state.busy ? '创建中...' : '创建翻译任务';
  }

  function applyPreviewStyles() {
    subtitleOverlay.style.fontFamily = FONT_FAMILIES[state.videoFont] || FONT_FAMILIES.Impact;
    subtitleOverlay.style.fontSize = `${Number(state.videoSize)}px`;
    subtitleOverlay.style.top = `${Number(state.subtitlePositionY) * 100}%`;
    subtitleOverlay.style.left = '50%';
    subtitleOverlay.style.transform = 'translate(-50%, -50%)';
    subtitleOverlay.style.visibility = state.selectedContentTypes.has('videos') ? 'visible' : 'hidden';
    updateSubtitleHint();
  }

  function setSubtitlePositionY(next) {
    state.subtitlePositionY = clamp(Number(next) || 0.68, 0.12, 0.92);
    subtitlePosition.value = String(state.subtitlePositionY);
    applyPreviewStyles();
  }

  function updatePositionFromClientY(clientY) {
    const rect = previewFrame.getBoundingClientRect();
    if (!rect.height) return;
    const ratio = (clientY - rect.top) / rect.height;
    setSubtitlePositionY(ratio);
  }

  function setPreviewMessage(message, mode) {
    previewEmpty.textContent = message;
    previewEmpty.dataset.mode = mode || 'note';
    previewEmpty.hidden = false;
  }

  function setPreviewIdle(message) {
    previewVideo.pause();
    previewVideo.removeAttribute('src');
    previewVideo.load();
    setPreviewMessage(message || '默认会加载当前商品的第一条英文视频，方便直接预览字幕效果。', 'note');
  }

  async function loadPreviewVideo() {
    const items = (((state.product || {}).items) || []).filter((item) => String(item.lang || '').trim().toLowerCase() === 'en');
    if (!items.length) {
      setPreviewIdle('当前商品还没有英文视频，暂时只能预览字幕样式，无法叠加到真实视频上。');
      return;
    }

    const previewItem = items[0];
    try {
      const payload = await requestJSON(`/medias/api/items/${previewItem.id}/play_url`);
      if (!payload.url) throw new Error('预览地址为空');
      previewVideo.src = payload.url;
      previewVideo.currentTime = 0;
      previewVideo.load();
      previewVideo.play().catch(() => {});
      setPreviewMessage(`已加载英文视频「${previewItem.display_name || previewItem.filename || `视频 #${previewItem.id}` }」，字幕会直接叠加在画面上。`, 'success');
    } catch (error) {
      setPreviewIdle(`英文视频预览加载失败：${error.message || error}`);
    }
  }

  function collectExistingDefaults() {
    state.selectedContentTypes = new Set(CONTENT_TYPES.map((item) => item.code));
    state.selectedRawIds = new Set((state.rawSources || []).map((item) => Number(item.id)));
    state.selectedLangs = new Set();

    state.videoFont = 'Impact';
    state.videoSize = 10;
    state.subtitlePositionY = 0.68;
    videoFont.value = state.videoFont;
    renderSizeButtons();
    setSubtitlePositionY(state.subtitlePositionY);
  }

  function renderAll() {
    renderContentTypes();
    renderRawSources();
    renderLanguages();
    renderSizeButtons();
    syncVideoConfigState();
    applyPreviewStyles();
    renderSummary();
    updateSubmitState();
  }

  function destroyTasksController() {
    if (state.tasksController && typeof state.tasksController.destroy === 'function') {
      state.tasksController.destroy();
    }
    state.tasksController = null;
  }

  function ensureTasksController() {
    if (!state.productId) return;
    if (!window.MediasTranslationTasks || typeof window.MediasTranslationTasks.mount !== 'function') {
      tasksMount.innerHTML = '<div class="mt-empty">任务管理组件还没加载完成，请稍后再试。</div>';
      return;
    }
    if (state.tasksController) return;
    tasksMount.innerHTML = '';
    state.tasksController = window.MediasTranslationTasks.mount(tasksMount, state.productId, { compact: true });
  }

  function switchTab(tab) {
    state.activeTab = tab === 'tasks' ? 'tasks' : 'create';
    const isCreate = state.activeTab === 'create';
    tabCreate.classList.toggle('active', isCreate);
    tabTasks.classList.toggle('active', !isCreate);
    panelCreate.classList.toggle('active', isCreate);
    panelTasks.classList.toggle('active', !isCreate);
    submitBtn.hidden = !isCreate;
    if (isCreate) {
      renderSummary();
      updateSubmitState();
    } else {
      renderSummary('这里会持续汇总文案、详情图、视频封面、视频翻译任务状态；实际费用会在任务成功后生成。');
      ensureTasksController();
      if (state.tasksController && typeof state.tasksController.refresh === 'function') {
        state.tasksController.refresh();
      }
    }
  }

  function resetModal(productId, productName) {
    state.productId = Number(productId) || null;
    state.productName = productName || '';
    state.product = null;
    state.rawSources = [];
    state.languages = [];
    state.selectedContentTypes = new Set(CONTENT_TYPES.map((item) => item.code));
    state.selectedRawIds = new Set();
    state.selectedLangs = new Set();
    state.videoFont = 'Impact';
    state.videoSize = 10;
    state.subtitlePositionY = 0.68;
    state.activeTab = 'create';
    state.busy = false;
    titleMeta.textContent = state.productName ? ` · ${state.productName}` : '';
    rawList.innerHTML = '<div class="mt-empty">原始视频加载中...</div>';
    langList.innerHTML = '<div class="mt-empty">目标语言加载中...</div>';
    contentTypesBox.innerHTML = '<div class="mt-empty">翻译范围加载中...</div>';
    tasksMount.innerHTML = '<div class="mt-empty">切换到任务管理后会自动加载当前商品的任务汇总。</div>';
    destroyTasksController();
    videoFont.value = state.videoFont;
    renderSizeButtons();
    setSubtitlePositionY(0.68);
    setPreviewIdle('默认会加载当前商品的第一条英文视频，方便直接预览字幕效果。');
    switchTab('create');
  }

  async function loadBootstrap(productId) {
    const [rawData, langData, productData] = await Promise.all([
      requestJSON(`/medias/api/products/${productId}/raw-sources`),
      requestJSON('/medias/api/languages'),
      requestJSON(`/medias/api/products/${productId}`),
    ]);
    state.rawSources = Array.isArray(rawData.items) ? rawData.items : [];
    state.languages = Array.isArray(langData.items) ? langData.items : [];
    state.product = productData || {};
    collectExistingDefaults();
    renderAll();
    await loadPreviewVideo();
  }

  async function openModal(productId, productName, initialTab) {
    resetModal(productId, productName);
    mask.hidden = false;
    switchTab(initialTab || 'create');
    try {
      await loadBootstrap(productId);
      if (state.activeTab === 'tasks') {
        ensureTasksController();
      }
    } catch (error) {
      renderSummary(`初始化失败：${error.message || error}`);
      rawList.innerHTML = `<div class="mt-empty">加载失败：${esc(error.message || error)}</div>`;
      langList.innerHTML = '<div class="mt-empty">请稍后再试。</div>';
      contentTypesBox.innerHTML = '<div class="mt-empty">初始化异常，暂时无法创建任务。</div>';
      setPreviewIdle(`预览初始化失败：${error.message || error}`);
      updateSubmitState();
    }
  }

  function closeModal() {
    destroyTasksController();
    previewVideo.pause();
    mask.hidden = true;
    state.pointerDragging = false;
  }

  function collectCheckedValues(container, mapper) {
    return new Set(
      Array.from(container.querySelectorAll('input[type="checkbox"]:checked'))
        .map((input) => mapper(input))
        .filter((value) => value !== null && value !== undefined && value !== '')
    );
  }

  async function submitTask() {
    if (!state.productId || state.busy) return;
    state.busy = true;
    updateSubmitState();
    renderSummary('正在创建翻译任务...');

    const payload = {
      raw_ids: Array.from(state.selectedRawIds),
      target_langs: Array.from(state.selectedLangs),
      content_types: Array.from(state.selectedContentTypes),
      video_params: {
        subtitle_font: state.videoFont,
        subtitle_size: state.videoSize,
        subtitle_position_y: state.subtitlePositionY,
        subtitle_position: 'bottom',
      },
    };

    try {
      const result = await requestJSON(`/medias/api/products/${state.productId}/translate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      state.busy = false;
      updateSubmitState();
      switchTab('tasks');
      renderSummary(`任务已创建：${result.task_id}，现在可以在右侧任务管理里持续跟进状态；实际费用会在任务成功后生成。`);
      if (state.tasksController && typeof state.tasksController.refresh === 'function') {
        state.tasksController.refresh();
      }
    } catch (error) {
      state.busy = false;
      updateSubmitState();
      renderSummary(`创建失败：${error.message || error}`);
      window.alert(error.message || error);
    }
  }

  function injectStyles() {
    if (document.getElementById('mtTranslateStyles')) return;
    const style = document.createElement('style');
    style.id = 'mtTranslateStyles';
    style.textContent = `
      #mtTranslateDialog {
        width: min(1180px, calc(100vw - 48px));
        max-width: 1180px;
      }
      #mtTranslateDialog .oc-modal-body {
        max-height: calc(100vh - 220px);
        overflow: auto;
      }
      #mtTranslateDialog .oc-panel {
        display: none;
      }
      #mtTranslateDialog .oc-panel.active {
        display: block;
      }
      .mt-grid {
        display: grid;
        grid-template-columns: minmax(0, 1.4fr) minmax(320px, 420px);
        gap: 18px;
        align-items: start;
      }
      .mt-main,
      .mt-side,
      .mt-form {
        display: flex;
        flex-direction: column;
        gap: 16px;
      }
      .mt-card {
        border: 1px solid var(--border, oklch(91% 0.012 230));
        border-radius: 16px;
        background: var(--bg, oklch(99% 0.004 230));
        padding: 16px;
        display: flex;
        flex-direction: column;
        gap: 14px;
      }
      .mt-card__head {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 12px;
      }
      .mt-card__head h4 {
        margin: 0;
        font-size: 16px;
        line-height: 1.3;
      }
      .mt-hint {
        color: var(--fg-muted, oklch(48% 0.018 230));
        font-size: 12px;
        line-height: 1.55;
      }
      .mt-empty {
        padding: 18px 14px;
        border: 1px dashed var(--border-strong, oklch(84% 0.015 230));
        border-radius: 12px;
        background: var(--bg-subtle, oklch(97% 0.006 230));
        color: var(--fg-muted, oklch(48% 0.018 230));
        text-align: center;
        font-size: 13px;
        line-height: 1.6;
      }
      .mt-type-grid,
      .mt-raw-list,
      .mt-lang-list {
        display: grid;
        gap: 12px;
      }
      .mt-type-grid {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }
      .mt-lang-list {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }
      .mt-choice {
        display: flex;
        gap: 12px;
        align-items: flex-start;
        border: 1px solid var(--border, oklch(91% 0.012 230));
        border-radius: 14px;
        padding: 14px;
        background: var(--bg-subtle, oklch(97% 0.006 230));
        cursor: pointer;
      }
      .mt-choice:hover {
        border-color: var(--accent, oklch(56% 0.16 230));
      }
      .mt-choice--done {
        opacity: 0.62;
        cursor: not-allowed;
        background: var(--bg-muted, oklch(94% 0.010 230));
      }
      .mt-choice--done:hover {
        border-color: var(--border, oklch(91% 0.012 230));
      }
      .mt-choice input {
        margin-top: 3px;
        width: 16px;
        height: 16px;
        accent-color: var(--accent, oklch(56% 0.16 230));
      }
      .mt-choice__body {
        min-width: 0;
        display: flex;
        flex-direction: column;
        gap: 4px;
      }
      .mt-choice__body strong {
        font-size: 14px;
        line-height: 1.4;
      }
      .mt-choice__body small {
        color: var(--fg-muted, oklch(48% 0.018 230));
        font-size: 12px;
        line-height: 1.55;
      }
      .mt-status-line {
        color: var(--success-fg, oklch(38% 0.09 165)) !important;
      }
      .mt-choice--raw {
        align-items: center;
      }
      .mt-choice__cover {
        width: 54px;
        height: 96px;
        border-radius: 12px;
        overflow: hidden;
        background: oklch(90% 0.02 230);
        flex: 0 0 auto;
        display: inline-flex;
        align-items: center;
        justify-content: center;
      }
      .mt-choice__cover img {
        width: 100%;
        height: 100%;
        object-fit: cover;
        display: block;
      }
      .mt-choice__cover-ph {
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 0.08em;
        color: var(--fg-muted, oklch(48% 0.018 230));
      }
      .mt-size-group {
        display: flex;
        gap: 8px;
        flex-wrap: wrap;
      }
      .mt-size-group button {
        min-width: 44px;
        height: 32px;
        border: 1px solid var(--border-strong, oklch(84% 0.015 230));
        border-radius: 999px;
        background: var(--bg, oklch(99% 0.004 230));
        color: var(--fg, oklch(22% 0.02 235));
        cursor: pointer;
      }
      .mt-size-group button.active {
        border-color: var(--accent, oklch(56% 0.16 230));
        background: var(--accent-subtle, oklch(94% 0.04 225));
        color: var(--accent, oklch(56% 0.16 230));
      }
      .mt-preview-shell {
        display: flex;
        flex-direction: column;
        gap: 10px;
      }
      .mt-preview-frame {
        width: 270px;
        height: 480px;
        margin: 0 auto;
        position: relative;
        overflow: hidden;
        border-radius: 30px;
        background:
          linear-gradient(180deg, rgba(8, 15, 32, 0.12), rgba(8, 15, 32, 0) 72px),
          linear-gradient(180deg, oklch(17% 0.025 235), oklch(12% 0.02 235));
        box-shadow: 0 16px 40px rgba(8, 15, 32, 0.18);
      }
      .mt-preview-frame::before {
        content: '';
        position: absolute;
        top: 10px;
        left: 50%;
        width: 96px;
        height: 18px;
        border-radius: 999px;
        transform: translateX(-50%);
        background: rgba(8, 15, 32, 0.9);
        z-index: 3;
      }
      .mt-preview-video {
        position: absolute;
        inset: 0;
        width: 100%;
        height: 100%;
        object-fit: cover;
        display: block;
        background: oklch(12% 0.02 235);
      }
      .mt-preview-topbar {
        position: absolute;
        top: 18px;
        left: 0;
        right: 0;
        z-index: 4;
        display: flex;
        justify-content: space-between;
        padding: 0 24px;
        color: rgba(255, 255, 255, 0.96);
        font-size: 12px;
        font-weight: 700;
        letter-spacing: 0.04em;
        pointer-events: none;
      }
      .mt-subtitle-overlay {
        position: absolute;
        left: 50%;
        width: calc(100% - 28px);
        max-width: calc(100% - 28px);
        z-index: 5;
        display: flex;
        flex-direction: column;
        gap: 4px;
        text-align: center;
        color: #ffffff;
        text-shadow:
          0 2px 0 rgba(0, 0, 0, 0.75),
          0 0 18px rgba(0, 0, 0, 0.34);
        line-height: 1.08;
        letter-spacing: 0.01em;
        user-select: none;
        cursor: grab;
      }
      .mt-subtitle-overlay:active {
        cursor: grabbing;
      }
      .mt-subtitle-line {
        padding: 0 6px;
        font-weight: 900;
      }
      .mt-preview-empty {
        font-size: 12px;
        line-height: 1.6;
        color: var(--fg-muted, oklch(48% 0.018 230));
      }
      .mt-preview-empty[data-mode="success"] {
        color: var(--success-fg, oklch(38% 0.09 165));
      }
      .mt-preview-empty[data-mode="error"] {
        color: var(--danger-fg, oklch(42% 0.14 25));
      }
      #mtTranslateDialog.mt-video-disabled .mt-side {
        opacity: 0.7;
      }
      @media (max-width: 1100px) {
        .mt-grid {
          grid-template-columns: 1fr;
        }
      }
      @media (max-width: 760px) {
        #mtTranslateDialog {
          width: calc(100vw - 20px);
        }
        .mt-type-grid,
        .mt-lang-list {
          grid-template-columns: 1fr;
        }
      }
    `;
    document.head.appendChild(style);
  }

  function ensureTaskButtons() {
    document.querySelectorAll('.oc-row-actions .js-translate[data-pid]').forEach((button) => {
      const rowActions = button.closest('.oc-row-actions');
      if (!rowActions) return;
      const pid = button.dataset.pid || '';
      if (!pid) return;
      if (rowActions.querySelector(`.js-translation-tasks[data-pid="${pid}"]`)) return;
      const taskButton = document.createElement('button');
      taskButton.type = 'button';
      taskButton.className = 'bt-row-btn js-translation-tasks';
      taskButton.dataset.pid = pid;
      taskButton.dataset.name = button.dataset.name || '';
      taskButton.textContent = '翻译任务管理';
      const aiEvalBtn = rowActions.querySelector('[data-ai-evaluate]');
      if (aiEvalBtn) {
        rowActions.insertBefore(taskButton, aiEvalBtn);
      } else {
        rowActions.appendChild(taskButton);
      }
    });
  }

  injectStyles();
  ensureTaskButtons();
  new MutationObserver(ensureTaskButtons).observe(document.body, { childList: true, subtree: true });

  contentTypesBox.addEventListener('change', () => {
    state.selectedContentTypes = collectCheckedValues(contentTypesBox, (input) => String(input.value || ''));
    syncVideoConfigState();
    renderLanguages();
    renderSummary();
    updateSubmitState();
    applyPreviewStyles();
  });

  rawList.addEventListener('change', () => {
    state.selectedRawIds = collectCheckedValues(rawList, (input) => Number(input.value));
    renderLanguages();
    renderSummary();
    updateSubmitState();
  });

  langList.addEventListener('change', () => {
    state.selectedLangs = collectCheckedValues(langList, (input) => (input.disabled ? '' : String(input.value || '')));
    renderSummary();
    updateSubmitState();
  });

  videoFont.addEventListener('change', () => {
    state.videoFont = videoFont.value || 'Impact';
    applyPreviewStyles();
  });

  videoSizeGroup.addEventListener('click', (event) => {
    const button = event.target.closest('[data-size]');
    if (!button) return;
    state.videoSize = Number(button.dataset.size) || 10;
    renderSizeButtons();
    applyPreviewStyles();
  });

  subtitlePosition.addEventListener('input', () => {
    setSubtitlePositionY(subtitlePosition.value);
  });

  subtitleOverlay.addEventListener('pointerdown', (event) => {
    state.pointerDragging = true;
    subtitleOverlay.setPointerCapture(event.pointerId);
    updatePositionFromClientY(event.clientY);
  });

  subtitleOverlay.addEventListener('pointermove', (event) => {
    if (!state.pointerDragging) return;
    updatePositionFromClientY(event.clientY);
  });

  function endDrag(event) {
    state.pointerDragging = false;
    try {
      subtitleOverlay.releasePointerCapture(event.pointerId);
    } catch (_error) {
      // ignore
    }
  }

  subtitleOverlay.addEventListener('pointerup', endDrag);
  subtitleOverlay.addEventListener('pointercancel', endDrag);

  tabCreate.addEventListener('click', () => switchTab('create'));
  tabTasks.addEventListener('click', () => switchTab('tasks'));
  closeBtn.addEventListener('click', closeModal);
  cancelBtn.addEventListener('click', closeModal);
  submitBtn.addEventListener('click', submitTask);

  mask.addEventListener('click', (event) => {
    if (event.target === mask) closeModal();
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !mask.hidden) {
      closeModal();
    }
  });

  document.addEventListener('click', async (event) => {
    const translateButton = event.target.closest('.js-translate[data-pid]');
    if (translateButton) {
      event.preventDefault();
      event.stopPropagation();
      event.stopImmediatePropagation();
      await openModal(translateButton.dataset.pid, translateButton.dataset.name || '', 'create');
      return;
    }

    const tasksButton = event.target.closest('.js-translation-tasks[data-pid]');
    if (tasksButton) {
      event.preventDefault();
      event.stopPropagation();
      event.stopImmediatePropagation();
      const pid = tasksButton.dataset.pid;
      if (pid) {
        window.location.href = `/medias/products/${encodeURIComponent(pid)}/translation-tasks`;
      }
    }
  }, true);
})();
