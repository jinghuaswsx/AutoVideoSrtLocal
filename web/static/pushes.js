(function () {
  const STATUS_LABELS = {
    not_ready: { text: '未就绪', cls: 'badge-gray' },
    pending:   { text: '待推送', cls: 'badge-blue' },
    pushed:    { text: '已推送', cls: 'badge-green' },
    failed:    { text: '推送失败', cls: 'badge-red' },
  };
  const READINESS_LABELS = {
    has_object: '视频',
    has_cover: '封面',
    has_copywriting: '文案',
    lang_supported: '链接',
    has_push_texts: '英文文案格式正确',
  };

  const PUSH_MODAL_MODES = {
    CONFIRM: 'confirm',
    JSON: 'json',
    LOCALIZED_TEXT: 'localized-text',
    LOCALIZED_JSON: 'localized-json',
  };

  const state = { page: 1, pageSize: 20, total: 0, items: [] };

  // ---------- 工具 ----------

  function el(tag, attrs = {}, children = []) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === 'class') node.className = v;
      else if (k === 'dataset') Object.assign(node.dataset, v);
      else if (k === 'style') node.setAttribute('style', v);
      else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2).toLowerCase(), v);
      else if (v === true) node.setAttribute(k, '');
      else if (v === false || v === null || v === undefined) continue;
      else node.setAttribute(k, v);
    }
    for (const c of [].concat(children)) {
      if (c === null || c === undefined || c === false) continue;
      node.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    }
    return node;
  }

  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

  async function fetchJSON(url, options) {
    const resp = await fetch(url, options);
    if (!resp.ok && resp.status !== 204) {
      const body = await resp.text();
      throw Object.assign(new Error(`HTTP ${resp.status}`), { status: resp.status, body });
    }
    if (resp.status === 204) return null;
    return resp.json();
  }

  // ---------- 筛选与列表 ----------

  async function loadLanguages() {
    try {
      const data = await fetchJSON('/medias/api/languages');
      const sel = document.getElementById('f-lang');
      const all = document.createElement('option');
      all.value = ''; all.textContent = '全部';
      sel.innerHTML = ''; sel.appendChild(all);
      (data.languages || []).forEach(l => {
        if (l.code === 'en') return;
        const opt = document.createElement('option');
        opt.value = l.code; opt.textContent = `${l.name_zh} (${l.code})`;
        sel.appendChild(opt);
      });
    } catch (e) {
      console.warn('load languages failed', e);
    }
  }

  function buildQuery() {
    const params = new URLSearchParams();
    const statusSel = document.getElementById('f-status');
    if (statusSel.value) params.set('status', statusSel.value);
    const langSel = document.getElementById('f-lang');
    if (langSel.value) params.set('lang', langSel.value);
    const product = document.getElementById('f-product').value.trim();
    if (product) params.set('product', product);
    const keyword = document.getElementById('f-keyword').value.trim();
    if (keyword) params.set('keyword', keyword);
    const df = document.getElementById('f-date-from').value;
    if (df) params.set('date_from', df);
    const dt = document.getElementById('f-date-to').value;
    if (dt) params.set('date_to', dt);
    params.set('page', String(state.page));
    return params.toString();
  }

  function renderReadinessText(readiness) {
    const parts = Object.entries(READINESS_LABELS).map(([key, label]) => {
      const ok = readiness[key];
      return `<span class="ready-item ${ok ? 'ready-ok' : 'ready-bad'}">${label}</span>`;
    });
    return `<div class="ready-row">${parts.join('<span class="ready-sep">|</span>')}</div>`;
  }

  function renderStatusBadge(status) {
    const s = STATUS_LABELS[status] || { text: status, cls: '' };
    return `<span class="badge ${s.cls}">${s.text}</span>`;
  }

  function renderActionCell(it) {
    if (!window.PUSH_IS_ADMIN) return '';
    if (it.status === 'pushed') {
      const date = (it.pushed_at || '').slice(0, 10);
      return `<span class="pushed-text">✓ 已推送 ${date}</span>
              <div class="action-menu">
                <button class="btn-mini" data-action="open-modal" data-id="${it.id}">查看/重推</button>
                <button class="btn-mini" data-action="view-logs" data-id="${it.id}">历史</button>
                <button class="btn-mini" data-action="reset" data-id="${it.id}">重置</button>
              </div>`;
    }
    if (it.status === 'not_ready') {
      const missing = Object.entries(it.readiness)
        .filter(([, v]) => !v).map(([k]) => READINESS_LABELS[k] || k).join(' / ');
      return `<button class="btn-push" disabled title="缺少：${missing}">推送</button>`;
    }
    const label = it.status === 'failed' ? '重试推送' : '推送';
    const historyBtn = it.status === 'failed'
      ? `<button class="btn-mini" data-action="view-logs" data-id="${it.id}" style="margin-left:8px">历史</button>` : '';
    return `<button class="btn-push" data-action="open-modal" data-id="${it.id}">${label}</button>${historyBtn}`;
  }

  function renderRow(it) {
    const thumb = it.cover_url
      ? `<img class="thumb" src="${it.cover_url}" alt="">`
      : `<div class="thumb thumb-empty"></div>`;
    const durStr = (typeof it.duration_seconds === 'number') ? it.duration_seconds.toFixed(1) + 's' : '';
    const sizeStr = (it.file_size || 0).toLocaleString() + ' B';
    return `<tr data-id="${it.id}">
      <td>${thumb}</td>
      <td>
        <div class="product-name">${it.product_name || ''}</div>
        <div class="product-code">${it.product_code || ''}</div>
      </td>
      <td>
        <div class="item-name">${it.display_name || it.filename || ''}</div>
        <div class="item-meta">${durStr} · ${sizeStr}</div>
      </td>
      <td><span class="lang-pill">${it.lang || ''}</span></td>
      <td class="ready-cell">${renderReadinessText(it.readiness)}</td>
      <td>${renderStatusBadge(it.status)}</td>
      <td class="time">${(it.created_at || '').replace('T', ' ').slice(0, 16)}</td>
      ${window.PUSH_IS_ADMIN ? `<td>${renderActionCell(it)}</td>` : ''}
    </tr>`;
  }

  async function load() {
    const tbody = document.getElementById('push-tbody');
    const colspan = window.PUSH_IS_ADMIN ? 8 : 7;
    tbody.innerHTML = `<tr><td colspan="${colspan}">加载中…</td></tr>`;
    try {
      const data = await fetchJSON('/pushes/api/items?' + buildQuery());
      state.total = data.total;
      state.items = data.items || [];
      if (!state.items.length) {
        tbody.innerHTML = `<tr><td colspan="${colspan}">无数据</td></tr>`;
      } else {
        tbody.innerHTML = state.items.map(renderRow).join('');
      }
      renderPagination();
    } catch (e) {
      tbody.innerHTML = `<tr><td colspan="${colspan}">加载失败: ${e.message}</td></tr>`;
    }
  }

  function renderPagination() {
    const box = document.getElementById('push-pagination');
    const totalPages = Math.ceil(state.total / state.pageSize) || 1;
    const parts = [`共 ${state.total} 条`];
    for (let p = 1; p <= totalPages; p++) {
      if (p === state.page) parts.push(`<strong>${p}</strong>`);
      else parts.push(`<a href="#" data-page="${p}">${p}</a>`);
    }
    box.innerHTML = parts.join(' ');
    box.querySelectorAll('a').forEach(a => {
      a.addEventListener('click', ev => {
        ev.preventDefault();
        state.page = Number(ev.target.getAttribute('data-page'));
        load();
      });
    });
  }

  function bindFilters() {
    document.getElementById('btn-apply').addEventListener('click', () => {
      state.page = 1; load();
    });
    document.getElementById('btn-reset').addEventListener('click', () => {
      document.querySelectorAll('.push-toolbar input').forEach(i => (i.value = ''));
      document.getElementById('f-status').value = 'pending';
      document.getElementById('f-lang').value = '';
      state.page = 1; load();
    });
  }

  // ---------- 弹窗 · 4 胶囊 ----------

  function renderPayloadView(payload) {
    const root = el('div');

    const kv = el('div', { class: 'pm-kv' });
    const pairs = [
      ['mode', payload.mode],
      ['product_name', payload.product_name],
      ['author', payload.author],
      ['push_admin', payload.push_admin],
      ['level', payload.level],
      ['roas', payload.roas],
      ['source', payload.source],
      ['platforms', JSON.stringify(payload.platforms || [])],
      ['selling_point', payload.selling_point || '(空)'],
      ['tags', JSON.stringify(payload.tags || [])],
    ];
    pairs.forEach(([k, v]) => {
      kv.appendChild(el('span', { class: 'k' }, k));
      kv.appendChild(el('span', { class: 'v' }, [el('code', {}, String(v ?? ''))]));
    });
    root.appendChild(kv);

    if (Array.isArray(payload.product_links) && payload.product_links.length) {
      const sub = el('div', { class: 'pm-sub' }, [
        el('div', { class: 'pm-sub-title' }, `product_links (${payload.product_links.length})`),
      ]);
      const ul = el('ul', { class: 'pm-list' });
      payload.product_links.forEach(link => ul.appendChild(el('li', {}, [el('code', {}, link)])));
      sub.appendChild(ul);
      root.appendChild(sub);
    }

    if (Array.isArray(payload.texts) && payload.texts.length) {
      const sub = el('div', { class: 'pm-sub' }, [
        el('div', { class: 'pm-sub-title' }, `texts (${payload.texts.length})`),
      ]);
      payload.texts.forEach((t, i) => {
        const tkv = el('div', { class: 'pm-kv', style: 'margin-top:4px' });
        ['title', 'message', 'description'].forEach(k => {
          tkv.appendChild(el('span', { class: 'k' }, `[${i}] ${k}`));
          tkv.appendChild(el('span', { class: 'v' }, [el('code', {}, String(t[k] || ''))]));
        });
        sub.appendChild(tkv);
      });
      root.appendChild(sub);
    }

    if (Array.isArray(payload.videos) && payload.videos.length) {
      const sub = el('div', { class: 'pm-sub' }, [
        el('div', { class: 'pm-sub-title' }, `videos (${payload.videos.length})`),
      ]);
      payload.videos.forEach((v, i) => {
        const vkv = el('div', { class: 'pm-kv', style: 'margin-top:8px' });
        ['name', 'size', 'width', 'height'].forEach(k => {
          vkv.appendChild(el('span', { class: 'k' }, `[${i}] ${k}`));
          vkv.appendChild(el('span', { class: 'v' }, [el('code', {}, String(v[k] ?? ''))]));
        });
        sub.appendChild(vkv);

        const preview = el('div', { class: 'pm-video-preview' });
        if (v.image_url) {
          preview.appendChild(el('img', { class: 'pm-thumb', src: v.image_url, alt: `cover-${i}` }));
        }
        if (v.url) {
          preview.appendChild(el('video', {
            class: 'pm-thumb', src: v.url, poster: v.image_url || null,
            controls: true, preload: 'metadata',
          }));
        }
        sub.appendChild(preview);
      });
      root.appendChild(sub);
    }

    return root;
  }

  function renderLocalizedPane(texts, targetUrl, mkId) {
    const root = el('div');

    const target = el('div', { class: 'pm-kv' });
    target.appendChild(el('span', { class: 'k' }, 'mk_id'));
    target.appendChild(el('span', { class: 'v' }, [el('code', {}, mkId ? String(mkId) : '-')]));
    target.appendChild(el('span', { class: 'k' }, '推送地址'));
    target.appendChild(el('span', { class: 'v' }, [el('code', {}, targetUrl || '(未配置 base_url)')]));
    root.appendChild(target);

    if (!texts.length) {
      root.appendChild(el('p', { class: 'pm-empty' }, '当前暂无可推送小语种文案（需要产品下有非英文且标题/文案/描述齐全的 copywriting）'));
      return root;
    }

    texts.forEach((t, index) => {
      const card = el('div', { class: 'pm-sub', style: index > 0 ? 'margin-top:12px' : '' });
      const kv = el('div', { class: 'pm-kv' });
      [['语种', t.lang || ''], ['标题', t.title || ''], ['文案', t.message || ''], ['描述', t.description || '']]
        .forEach(([k, v]) => {
          kv.appendChild(el('span', { class: 'k' }, k));
          kv.appendChild(el('span', { class: 'v' }, v));
        });
      card.appendChild(kv);
      root.appendChild(card);
    });
    return root;
  }

  function describeError(e) {
    let body = {};
    try { body = JSON.parse(e.body || '{}'); } catch (_) {}
    const err = body.error || '';
    const detail = body.detail || body.response_body || e.body || e.message || '';
    if (err === 'not_ready') {
      const missing = (body.missing || []).map(k => (READINESS_LABELS[k] || k)).join(' / ');
      return `素材未就绪：缺少 ${missing || '未知项'}`;
    }
    if (err === 'link_not_adapted') return `产品链接未适配：${body.url || ''}（${detail || ''}）`;
    if (err === 'already_pushed') return '该素材已推送过';
    if (err === 'copywriting_invalid') return `文案不合规：${detail}`;
    if (err === 'push_target_not_configured') return '后端未配置 PUSH_TARGET_URL（去 /settings?tab=push）';
    if (err === 'push_localized_texts_base_url_missing') return '未配置 wedev Base URL（去 /settings?tab=push）';
    if (err === 'push_localized_texts_credentials_missing') return '未配置 wedev 的 Authorization 或 Cookie（去 /settings?tab=push 或用 tools/wedev_sync.py）';
    if (err === 'mk_id_missing') return '该产品缺少 mk_id，不能推送小语种文案';
    if (err === 'localized_texts_empty') return '当前没有可推送的小语种文案';
    if (err === 'downstream_unreachable') return `下游不可达：${detail}`;
    if (err === 'downstream_error') {
      const preview = (body.response_body || '').slice(0, 200);
      return `下游返回 HTTP ${body.upstream_status}\n${preview}`;
    }
    return detail || e.message;
  }

  function openPushModal(itemId) {
    const item = state.items.find(i => i.id === itemId);
    if (!item) return;

    const overlay = el('div', { class: 'pm-overlay' });
    const modal = el('div', { class: 'pm-modal' });
    overlay.appendChild(modal);
    document.body.appendChild(overlay);

    const header = el('div', { class: 'pm-header' });
    const pillRow = el('div', { class: 'pm-pills' });
    const pillDefs = [
      { mode: PUSH_MODAL_MODES.CONFIRM, label: '推送确认' },
      { mode: PUSH_MODAL_MODES.JSON, label: 'JSON 预览' },
      { mode: PUSH_MODAL_MODES.LOCALIZED_TEXT, label: '推送小语种文案' },
      { mode: PUSH_MODAL_MODES.LOCALIZED_JSON, label: '小语种文案 JSON 预览' },
    ];
    const pills = pillDefs.map(({ mode, label }) => el('button', {
      type: 'button', class: 'pm-pill', dataset: { mode },
    }, label));
    pills.forEach(p => pillRow.appendChild(p));
    const btnClose = el('button', { type: 'button', class: 'pm-close', 'aria-label': '关闭' }, '×');
    header.appendChild(pillRow);
    header.appendChild(btnClose);
    modal.appendChild(header);

    const body = el('div', { class: 'pm-body' });
    modal.appendChild(body);

    const infoCard = el('section', { class: 'pm-section' }, [el('h4', {}, '素材信息')]);
    const infoKV = el('div', { class: 'pm-kv' });
    const mkIdValue = el('span', { class: 'v' }, '-');
    const addKV = (k, v) => {
      infoKV.appendChild(el('span', { class: 'k' }, k));
      if (v instanceof Node) infoKV.appendChild(v);
      else infoKV.appendChild(el('span', { class: 'v' }, v));
    };
    addKV('产品', `${item.product_name || ''}  ·  ${item.product_code || ''}`);
    addKV('语种', item.lang || '-');
    addKV('文件', item.display_name || item.filename || '-');
    addKV('item_id', String(item.id));
    addKV('mk_id', mkIdValue);
    addKV('状态', STATUS_LABELS[item.status]?.text || item.status);
    infoCard.appendChild(infoKV);
    body.appendChild(infoCard);

    const contentCard = el('section', { class: 'pm-section' }, [el('h4', {}, '推送内容')]);
    const loadingTip = el('p', { class: 'pm-empty' }, '加载中…');
    const paneConfirm = el('div', { class: 'pm-pane' });
    const paneJson = el('pre', { class: 'pm-pane pm-json', hidden: true });
    const paneLocalized = el('div', { class: 'pm-pane', hidden: true });
    const paneLocalizedJson = el('pre', { class: 'pm-pane pm-json', hidden: true });
    contentCard.appendChild(loadingTip);
    contentCard.appendChild(paneConfirm);
    contentCard.appendChild(paneJson);
    contentCard.appendChild(paneLocalized);
    contentCard.appendChild(paneLocalizedJson);
    body.appendChild(contentCard);

    const respWrap = el('section', { class: 'pm-section pm-response', hidden: true });
    const respTitle = el('h4', {}, '推送响应');
    const respPre = el('pre', { class: 'pm-json' });
    const respMkIdTip = el('div', { class: 'pm-mk-id-tip', hidden: true });
    respWrap.appendChild(respTitle);
    respWrap.appendChild(respPre);
    respWrap.appendChild(respMkIdTip);
    modal.appendChild(respWrap);

    const footer = el('div', { class: 'pm-footer' });
    const btnPush = el('button', { type: 'button', class: 'btn-push', disabled: true }, '推送');
    const btnCancel = el('button', { type: 'button', class: 'btn-mini' }, '关闭');
    footer.appendChild(btnPush);
    footer.appendChild(btnCancel);
    modal.appendChild(footer);

    let activeMode = PUSH_MODAL_MODES.CONFIRM;
    let payloadData = null;
    let mkId = null;
    let localizedTexts = [];
    let localizedTargetUrl = '';
    let materialPushed = item.status === 'pushed';
    let localizedPushed = false;
    let anyPushSucceeded = false;

    function isLocalizedMode(m = activeMode) {
      return m === PUSH_MODAL_MODES.LOCALIZED_TEXT || m === PUSH_MODAL_MODES.LOCALIZED_JSON;
    }

    function syncPushButton() {
      if (isLocalizedMode()) {
        const noTarget = !mkId || !localizedTargetUrl;
        const noTexts = !localizedTexts.length;
        btnPush.disabled = localizedPushed || !payloadData || noTarget || noTexts;
        btnPush.textContent = localizedPushed
          ? '文案已推送'
          : noTarget
            ? (!mkId ? '缺少 mk_id' : '未配 base_url')
            : noTexts ? '无可推送文案' : '推送小语种文案';
        return;
      }
      btnPush.disabled = materialPushed || !payloadData;
      btnPush.textContent = materialPushed ? '素材已推送' : '推送素材';
    }

    function setMode(mode) {
      activeMode = mode;
      pills.forEach(p => p.classList.toggle('active', p.dataset.mode === mode));
      paneConfirm.hidden = mode !== PUSH_MODAL_MODES.CONFIRM;
      paneJson.hidden = mode !== PUSH_MODAL_MODES.JSON;
      paneLocalized.hidden = mode !== PUSH_MODAL_MODES.LOCALIZED_TEXT;
      paneLocalizedJson.hidden = mode !== PUSH_MODAL_MODES.LOCALIZED_JSON;
      syncPushButton();
    }

    pills.forEach(p => p.addEventListener('click', () => setMode(p.dataset.mode)));

    function showResponse(obj, isError, title) {
      respWrap.hidden = false;
      respTitle.textContent = title || '推送响应';
      respPre.textContent = typeof obj === 'string' ? obj : JSON.stringify(obj, null, 2);
      respPre.classList.toggle('pm-json-error', !!isError);
      respMkIdTip.hidden = true;
      respMkIdTip.textContent = '';
    }

    function showMkIdMatch(match) {
      if (!match) return;
      respMkIdTip.hidden = false;
      if (match.status === 'ok' && match.mk_id) {
        respMkIdTip.textContent = `配对 mk_id : ${match.mk_id}`;
      } else if (match.status === 'credentials_expired') {
        respMkIdTip.textContent = '配对 mk_id 失败：wedev 登录凭据已失效，请在本机跑 tools/wedev_sync.py 重新同步';
      } else {
        respMkIdTip.textContent = '配对 mk_id 失败，请检查，当前无法完成小语种文案推送，缺失 mk_id';
      }
    }

    function close() {
      if (!overlay.parentNode) return;
      overlay.parentNode.removeChild(overlay);
      document.removeEventListener('keydown', onEsc);
      if (anyPushSucceeded) load();
    }
    function onEsc(e) { if (e.key === 'Escape') close(); }
    document.addEventListener('keydown', onEsc);
    overlay.addEventListener('click', e => { if (e.target === overlay) close(); });
    btnClose.addEventListener('click', close);
    btnCancel.addEventListener('click', close);

    (async () => {
      try {
        const data = await fetchJSON(`/pushes/api/items/${itemId}/payload`);
        payloadData = data.payload;
        mkId = data.mk_id || null;
        localizedTargetUrl = data.localized_push_target_url || '';
        localizedTexts = (data.localized_texts_request && data.localized_texts_request.texts) || [];

        mkIdValue.textContent = mkId ? String(mkId) : '-';

        clear(paneConfirm);
        // 顶部显式打印推送地址
        const pushUrlRow = el('div', { class: 'pm-kv', style: 'margin-bottom:12px' });
        pushUrlRow.appendChild(el('span', { class: 'k' }, '推送地址'));
        pushUrlRow.appendChild(el('span', { class: 'v' }, [
          el('code', {}, data.push_url || '(未配置)'),
        ]));
        paneConfirm.appendChild(pushUrlRow);
        paneConfirm.appendChild(renderPayloadView(payloadData));
        paneJson.textContent = JSON.stringify(payloadData, null, 2);

        clear(paneLocalized);
        paneLocalized.appendChild(renderLocalizedPane(localizedTexts, localizedTargetUrl, mkId));
        paneLocalizedJson.textContent = JSON.stringify({
          mk_id: mkId,
          target_url: localizedTargetUrl,
          texts: localizedTexts,
        }, null, 2);

        loadingTip.remove();
        setMode(PUSH_MODAL_MODES.CONFIRM);
      } catch (err) {
        loadingTip.textContent = `载荷加载失败：${describeError(err)}`;
        loadingTip.classList.add('pm-error');
      }
    })();

    btnPush.addEventListener('click', async () => {
      if (!payloadData) return;
      btnPush.disabled = true;
      btnCancel.disabled = true;
      btnPush.textContent = '推送中…';
      try {
        if (isLocalizedMode()) {
          const body = await fetchJSON(`/pushes/api/items/${itemId}/push-localized-texts`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
          });
          showResponse(body, false, '小语种文案推送响应');
          localizedPushed = true;
          anyPushSucceeded = true;
        } else {
          const body = await fetchJSON(`/pushes/api/items/${itemId}/push`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
          });
          showResponse(body, false, '素材推送响应');
          showMkIdMatch(body.mk_id_match);
          materialPushed = true;
          anyPushSucceeded = true;
          // mk_id 匹配成功 → 同步刷新顶部信息 + 小语种文案 pane + JSON 预览 + 推送按钮状态
          if (body.mk_id_match && body.mk_id_match.mk_id) {
            mkId = body.mk_id_match.mk_id;
            mkIdValue.textContent = String(mkId);
            localizedTargetUrl = body.mk_id_match.localized_push_target_url || '';
            clear(paneLocalized);
            paneLocalized.appendChild(
              renderLocalizedPane(localizedTexts, localizedTargetUrl, mkId),
            );
            paneLocalizedJson.textContent = JSON.stringify({
              mk_id: mkId,
              target_url: localizedTargetUrl,
              texts: localizedTexts,
            }, null, 2);
            syncPushButton();
          }
        }
      } catch (err) {
        showResponse(describeError(err), true,
          isLocalizedMode() ? '小语种文案推送失败' : '素材推送失败');
      } finally {
        btnCancel.disabled = false;
        syncPushButton();
      }
    });
  }

  // ---------- 历史抽屉 & 重置 ----------

  async function resetPush(itemId) {
    if (!confirm('确认重置这条素材的推送状态？之前的历史记录会保留。')) return;
    await fetchJSON(`/pushes/api/items/${itemId}/reset`, { method: 'POST' });
    await load();
  }

  async function viewLogs(itemId) {
    const drawer = document.getElementById('push-log-drawer');
    const content = document.getElementById('drawer-content');
    content.textContent = '加载中…';
    drawer.hidden = false;
    try {
      const data = await fetchJSON(`/pushes/api/items/${itemId}/logs`);
      if (!data.logs.length) {
        content.innerHTML = '<p>暂无记录</p>';
      } else {
        content.innerHTML = data.logs.map(l => `
          <div class="log-row">
            <div><strong>${l.status === 'success' ? '✓ 成功' : '✗ 失败'}</strong>
                 <span class="time">${l.created_at}</span></div>
            ${l.error_message ? `<div class="err">${l.error_message}</div>` : ''}
            ${l.response_body ? `<pre>${l.response_body.slice(0, 500)}</pre>` : ''}
          </div>
        `).join('');
      }
    } catch (e) {
      content.textContent = '加载失败: ' + e.message;
    }
  }

  // ---------- 绑定 ----------

  document.getElementById('push-tbody').addEventListener('click', ev => {
    const btn = ev.target.closest('button[data-action]');
    if (!btn) return;
    const action = btn.getAttribute('data-action');
    const id = Number(btn.getAttribute('data-id'));
    if (action === 'open-modal') openPushModal(id);
    else if (action === 'reset') resetPush(id);
    else if (action === 'view-logs') viewLogs(id);
  });

  document.getElementById('drawer-close').addEventListener('click', () => {
    document.getElementById('push-log-drawer').hidden = true;
  });

  window._pushesLoad = load;
  loadLanguages().then(() => { bindFilters(); load(); });
})();
