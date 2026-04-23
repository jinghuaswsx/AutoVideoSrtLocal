(function () {
  const root = document.getElementById('translationTasksApp');
  if (!root) return;

  const productId = root.dataset.productId;
  const productName = root.dataset.productName || '';
  const statusEl = document.getElementById('translationTasksStatus');
  const listEl = document.getElementById('translationTasksList');
  const actionLabels = {
    retryItem: '重新启动',
    resume: '从中断点继续',
    chooseVoice: '去选声音',
  };

  const groupTitles = {
    copywriting: '文案',
    detail_images: '详情图',
    video_covers: '视频封面',
    videos: '视频',
  };

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;',
    }[ch]));
  }

  function setStatus(message, kind) {
    statusEl.textContent = message;
    statusEl.classList.remove('is-loading', 'is-error');
    if (kind) {
      statusEl.classList.add(kind === 'error' ? 'is-error' : 'is-loading');
    }
  }

  function renderAction(action) {
    if (!action || Object.keys(action).length === 0) return '';
    if (action.href) {
      const label = action.label || actionLabels.chooseVoice;
      return `<a class="mt-btn mt-action mt-action--primary" href="${escapeHtml(action.href)}" target="_blank" rel="noopener noreferrer">${escapeHtml(label)}</a>`;
    }
    const label = action.label || (String(action.endpoint || '').includes('/retry-item') ? actionLabels.retryItem : actionLabels.resume);
    const payload = JSON.stringify(action.payload || {});
    return `<button type="button" class="mt-btn mt-action mt-action--primary js-task-action" data-endpoint="${escapeHtml(action.endpoint || '')}" data-payload='${escapeHtml(payload)}'>${escapeHtml(label)}</button>`;
  }

  function renderItem(item) {
    return `
      <div class="mt-item">
        <div class="mt-item__body">
          <div class="mt-item__label">${escapeHtml(item.label || '')}</div>
          <div class="mt-item__meta">
            状态：${escapeHtml(item.status || '')} · 语种：${escapeHtml(item.lang || '')} · 索引：${escapeHtml(item.idx)}
          </div>
        </div>
        <div class="mt-item__actions">
          ${renderAction(item.action)}
        </div>
      </div>
    `;
  }

  function renderGroup(kind, items) {
    const safeItems = Array.isArray(items) ? items : [];
    if (!safeItems.length) {
      return `
        <section class="mt-group">
          <h3 class="mt-group__title">${escapeHtml(groupTitles[kind] || kind)}</h3>
          <div class="mt-group__empty">暂无任务</div>
        </section>
      `;
    }
    return `
      <section class="mt-group">
        <h3 class="mt-group__title">${escapeHtml(groupTitles[kind] || kind)} <span class="mt-pill">${safeItems.length}</span></h3>
        <div class="mt-items">
          ${safeItems.map(renderItem).join('')}
        </div>
      </section>
    `;
  }

  function renderBatch(batch) {
    const groups = batch.groups || {};
    return `
      <article class="mt-batch" data-task-id="${escapeHtml(batch.task_id || '')}">
        <header class="mt-batch__head">
          <div>
            <h2 class="mt-batch__task">${escapeHtml(batch.task_id || '')}</h2>
            <div class="mt-batch__meta">
              状态：${escapeHtml(batch.status || '')} · 创建时间：${escapeHtml(batch.created_at || '')}
            </div>
          </div>
          <span class="mt-pill">${escapeHtml(batch.status || '')}</span>
        </header>
        ${Object.keys(groupTitles).map((kind) => renderGroup(kind, groups[kind] || [])).join('')}
      </article>
    `;
  }

  function renderEmpty() {
    listEl.innerHTML = `
      <div class="mt-empty">
        <h3 class="mt-empty__title">当前产品还没有翻译批次</h3>
        <p class="mt-empty__desc">创建新的 bulk_translate 任务后，这里会按最近批次展示文案、详情图、视频封面和视频四类任务。</p>
      </div>
    `;
  }

  function renderError(message) {
    listEl.innerHTML = '';
    setStatus(message || '加载失败', 'error');
  }

  async function load() {
    setStatus(`正在加载 ${productName || '当前产品'} 的翻译任务…`, 'loading');
    listEl.innerHTML = '';
    try {
      const resp = await fetch(`/medias/api/products/${productId}/translation-tasks`, {
        credentials: 'same-origin',
        headers: { 'Accept': 'application/json' },
      });
      if (!resp.ok) {
        let message = `加载失败：HTTP ${resp.status}`;
        try {
          const data = await resp.json();
          message = data.error || message;
        } catch (err) {
          // ignore parse issues
        }
        throw new Error(message);
      }
      const data = await resp.json();
      const batches = Array.isArray(data.batches) ? data.batches : [];
      if (!batches.length) {
        setStatus(`产品 ${data.product?.name || productName} 暂无翻译批次。`, 'loading');
        renderEmpty();
        return;
      }
      setStatus(`产品 ${data.product?.name || productName} 共 ${batches.length} 个最近批次。`, 'loading');
      listEl.innerHTML = batches.map(renderBatch).join('');
    } catch (error) {
      renderError(error.message || '加载失败');
    }
  }

  listEl.addEventListener('click', async (event) => {
    const button = event.target.closest('.js-task-action');
    if (!button) return;

    const endpoint = button.dataset.endpoint;
    let payload = {};
    try {
      payload = JSON.parse(button.dataset.payload || '{}');
    } catch (error) {
      payload = {};
    }

    button.disabled = true;
    try {
      const resp = await fetch(endpoint, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!resp.ok) {
        let message = `操作失败：HTTP ${resp.status}`;
        try {
          const data = await resp.json();
          message = data.error || message;
        } catch (error) {
          // ignore parse issues
        }
        throw new Error(message);
      }
      await load();
    } catch (error) {
      setStatus(error.message || '操作失败', 'error');
    } finally {
      button.disabled = false;
    }
  });

  load();
})();
