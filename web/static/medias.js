(function() {
  window.MEDIAS_UPLOAD_READY = window.MEDIAS_UPLOAD_READY !== false;
  const state = { page: 1, current: null, pendingItemCover: null, listRequestSeq: 0, roasProduct: null };
  const AI_EVALUATION_TIMEOUT_MS = 5 * 60 * 1000;
  const AI_EVAL_REQUEST_PREVIEW_ENDPOINT = (pid) => `/medias/api/products/${pid}/evaluate/request-preview`;
  const $ = (id) => document.getElementById(id);

  let LANGUAGES = [];
  let liveSearchTimer = null;

  async function ensureLanguages() {
    if (LANGUAGES.length) return LANGUAGES;
    const data = await fetchJSON('/medias/api/languages');
    LANGUAGES = data.items || [];
    return LANGUAGES;
  }

  function langDisplayName(code) {
    const raw = String(code || '').trim();
    const normalized = raw.toLowerCase();
    if (!normalized) return '';
    const l = (LANGUAGES || []).find(x => x && x.code === normalized);
    if (l && l.name_zh) return `${l.name_zh} (${l.code})`;
    return raw;
  }

  function resolveMaterialFilenameLang(filename, fallbackLang) {
    const lang = String(fallbackLang || 'en').trim().toLowerCase() || 'en';
    if (lang !== 'en') return lang;
    const fn = String(filename || '');
    if (!fn.includes('补充素材')) return lang;
    const detected = (LANGUAGES || []).find((item) => {
      if (!item || item.code === 'en' || !item.name_zh) return false;
      return fn.includes(item.name_zh);
    });
    return detected ? detected.code : lang;
  }

  // 新增产品首屏/编辑页英语素材：只要求 YYYY.MM.DD-产品名-xxxxx.mp4。
  function validateSimpleMaterialFilename(filename, productName) {
    const fn = String(filename || '');
    const errors = [];
    if (!productName) {
      errors.push('当前产品尚未加载，请重试');
      return errors;
    }
    if (fn.length < 12 || fn[10] !== '-') {
      errors.push('文件名必须是 "YYYY.MM.DD-产品名-xxxxx.mp4" 格式');
      return errors;
    }
    const dateSegment = fn.slice(0, 10);
    const match = /^(\d{4})\.(\d{2})\.(\d{2})$/.exec(dateSegment);
    if (!match) {
      errors.push(`日期段 "${dateSegment}" 必须是合法的 YYYY.MM.DD`);
      return errors;
    }
    const year = +match[1], month = +match[2], day = +match[3];
    const parsed = new Date(year, month - 1, day);
    if (parsed.getFullYear() !== year || parsed.getMonth() !== month - 1 || parsed.getDate() !== day) {
      errors.push(`日期 "${dateSegment}" 不是合法日期`);
      return errors;
    }
    const restSegment = fn.slice(11);
    const productPrefix = `${productName}-`;
    if (!restSegment.startsWith(productPrefix)) {
      errors.push(`日期之后必须紧跟 "${productName}-"`);
      return errors;
    }
    const tailSegment = restSegment.slice(productPrefix.length);
    if (!tailSegment || tailSegment.toLowerCase() === '.mp4') {
      errors.push('产品名之后必须保留一段文件说明，例如 "混剪-李文龙"');
      return errors;
    }
    if (!fn.toLowerCase().endsWith('.mp4')) {
      errors.push('文件扩展名必须是 ".mp4"');
    }
    return errors;
  }

  // 编辑页小语种素材沿用严格命名规范。
  // 模板：YYYY.MM.DD-{商品名中文}-原素材-补充素材({语种中文名})-指派-蔡靖华.mp4
  // 固定字段：原素材 / 补充素材 / 指派 / 蔡靖华（一字不差，半角括号）
  function validateMaterialFilename(filename, productName, langCode) {
    if (langCode === 'en') return validateSimpleMaterialFilename(filename, productName);

    const fn = String(filename || '');
    const TAIL = '-指派-蔡靖华.mp4';
    const MID_PREFIX = '-原素材-补充素材(';
    const errs = [];

    const lang = (LANGUAGES || []).find(l => l.code === langCode);
    const langZh = (lang && lang.name_zh) || '';
    if (!langZh) {
      errs.push(`未知语种 code='${langCode}'，无法校验`);
      return errs;
    }
    if (!productName) {
      errs.push('当前产品尚未加载，请重试');
      return errs;
    }

    if (!fn.endsWith(TAIL)) {
      errs.push(`结尾必须是 "${TAIL}"`);
      return errs;
    }
    const headMid = fn.slice(0, fn.length - TAIL.length);

    if (headMid.length < 11 || headMid[10] !== '-') {
      errs.push('开头必须是 "YYYY.MM.DD-" 格式');
      return errs;
    }
    const dateStr = headMid.slice(0, 10);
    const dateMatch = /^(\d{4})\.(\d{2})\.(\d{2})$/.exec(dateStr);
    if (!dateMatch) {
      errs.push(`日期段 "${dateStr}" 格式必须是 YYYY.MM.DD`);
      return errs;
    }
    const y = +dateMatch[1], mo = +dateMatch[2], d = +dateMatch[3];
    const dObj = new Date(y, mo - 1, d);
    if (dObj.getFullYear() !== y || dObj.getMonth() !== mo - 1 || dObj.getDate() !== d) {
      errs.push(`日期 "${dateStr}" 不是合法日期`);
    }

    const rest = headMid.slice(11);

    if (!rest.endsWith(')')) {
      errs.push('在 "-指派-蔡靖华.mp4" 之前必须紧跟 ")"（常见问题：多了空格、或用了中文全角括号 "）"）');
      return errs;
    }

    const midStart = rest.lastIndexOf(MID_PREFIX);
    if (midStart < 0) {
      errs.push(`中间必须包含 "${MID_PREFIX}语种中文名)"（常见问题：多了/少了连字符、或用了全角括号）`);
      return errs;
    }

    const productPart = rest.slice(0, midStart);
    const langPart = rest.slice(midStart + MID_PREFIX.length, -1);

    if (productPart !== productName) {
      errs.push(`商品名不符：文件名写的是 "${productPart}"，应为 "${productName}"（注意前后不能有空格）`);
    }
    if (langPart !== langZh) {
      errs.push(`语种中文名不符：文件名写的是 "${langPart}"，应为 "${langZh}"`);
    }

    return errs;
  }

  function assertMaterialFilenameOrAlert(filename, productName, langCode) {
    const effectiveLang = resolveMaterialFilenameLang(filename, langCode);
    const errs = validateMaterialFilename(filename, productName, effectiveLang);
    if (!errs.length) return true;
    showFilenameErrorModal(String(filename || ''), errs, productName, effectiveLang);
    return false;
  }

  function todayYMD() {
    const d = new Date();
    const pad = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}.${pad(d.getMonth() + 1)}.${pad(d.getDate())}`;
  }

  // 从原文件名抽取 YYYY.MM.DD（合法才用，否则用今天），再按当前入口规则生成建议文件名。
  function buildSuggestedFilename(filename, productName, langCode) {
    const fn = String(filename || '');
    const sourceDate = fn.slice(0, 10);
    const sourceMatch = /^(\d{4})\.(\d{2})\.(\d{2})$/.exec(sourceDate);
    let suggestedDate = todayYMD();
    if (sourceMatch) {
      const sourceYear = +sourceMatch[1], sourceMonth = +sourceMatch[2], sourceDay = +sourceMatch[3];
      const sourceParsed = new Date(sourceYear, sourceMonth - 1, sourceDay);
      if (sourceParsed.getFullYear() === sourceYear && sourceParsed.getMonth() === sourceMonth - 1 && sourceParsed.getDate() === sourceDay) {
        suggestedDate = sourceDate;
      }
    }
    const pn = productName || '{产品名}';
    if (langCode === 'en') return `${suggestedDate}-${pn}-素材.mp4`;
    const lang = (LANGUAGES || []).find(l => l.code === langCode);
    const langZh = (lang && lang.name_zh) || langCode;
    return `${suggestedDate}-${pn}-原素材-补充素材(${langZh})-指派-蔡靖华.mp4`;
  }

  // 将原文件名拆成段，合规的正常显示、不合规的红底粗体
  function renderHighlightedFilename(filename, productName, langCode) {
    const fn = String(filename || '');
    const errorStyle = 'color:#d64045;font-weight:700;background:#fde8ea;padding:0 2px;border-radius:3px;';
    const okStyle = 'color:#1b5e20;';
    const paint = (text, ok) => {
      if (!text) return '';
      return `<span style="${ok ? okStyle : errorStyle}">${escapeHtml(text)}</span>`;
    };
    const dateSegment = fn.slice(0, 10);
    const separatorSegment = fn.slice(10, 11);
    const restSegment = fn.slice(11);
    const dateMatchOnly = /^(\d{4})\.(\d{2})\.(\d{2})$/.exec(dateSegment);
    let validDate = false;
    if (dateMatchOnly) {
      const ymdYear = +dateMatchOnly[1], ymdMonth = +dateMatchOnly[2], ymdDay = +dateMatchOnly[3];
      const ymdParsed = new Date(ymdYear, ymdMonth - 1, ymdDay);
      validDate = ymdParsed.getFullYear() === ymdYear
        && ymdParsed.getMonth() === ymdMonth - 1
        && ymdParsed.getDate() === ymdDay;
    }
    const expectedPrefix = `${productName || ''}-`;
    const prefixOk = !!productName && restSegment.startsWith(expectedPrefix);
    const tail = prefixOk ? restSegment.slice(expectedPrefix.length) : '';
    if (langCode !== 'en') {
      const lang = (LANGUAGES || []).find(l => l.code === langCode);
      const langZh = (lang && lang.name_zh) || '';
      const strictTail = `-原素材-补充素材(${langZh})-指派-蔡靖华.mp4`;
      return [
        paint(dateSegment || '(缺日期)', validDate),
        paint(separatorSegment || '(缺-)', separatorSegment === '-'),
        prefixOk
          ? paint(productName, true) + paint(tail || '(缺后缀)', tail === strictTail)
          : paint(restSegment || '(缺产品名)', false),
      ].join('');
    }
    return [
      paint(dateSegment || '(缺日期)', validDate),
      paint(separatorSegment || '(缺-)', separatorSegment === '-'),
      prefixOk
        ? paint(expectedPrefix, true) + paint(tail || '(缺说明)', !!tail && tail.toLowerCase() !== '.mp4' && fn.toLowerCase().endsWith('.mp4'))
        : paint(restSegment || '(缺产品名)', false),
    ].join('');
  }

  function showFilenameErrorModal(filename, errs, productName, langCode) {
    const fn = String(filename || '');
    // 产品名对不对：文件名里是否包含完整的 productName
    const productOk = !!(productName && fn.includes(productName));
    const suggestion = productOk ? buildSuggestedFilename(fn, productName, langCode) : '';

    const old = document.getElementById('filenameErrModal');
    if (old) old.remove();

    const mask = document.createElement('div');
    mask.id = 'filenameErrModal';
    mask.setAttribute('style',
      'position:fixed;inset:0;background:rgba(15,23,42,0.45);z-index:9999;'
      + 'display:flex;align-items:center;justify-content:center;padding:24px;');

    let bodyHtml;
    if (!productOk) {
      // 产品名不对：突出提示，不给建议文件名
      const highlighted = renderHighlightedFilename(fn, productName, langCode);
      bodyHtml = `
        <div style="background:#fef2f2;border:1px solid #fecaca;border-radius:8px;
                    padding:14px 16px;margin-bottom:14px;">
          <div style="font-size:15px;font-weight:700;color:#b91c1c;margin-bottom:6px;">
            ⚠ 产品名不对
          </div>
          <div style="font-size:13px;color:#374151;line-height:1.6;">
            当前产品名是
            <code style="background:#fff;padding:2px 6px;border-radius:4px;color:#b91c1c;font-weight:600;">${escapeHtml(productName || '(未加载)')}</code>，
            但上传的文件名里没有匹配到该产品名。请确认上传到了正确的产品页，或重命名文件。
          </div>
        </div>
        <div style="margin-bottom:6px;color:#6b7280;font-size:12px;">你的文件名：</div>
        <div style="font-family:Consolas,'SF Mono',ui-monospace,monospace;font-size:13px;
                    background:#f9fafb;border:1px solid #e5e7eb;border-radius:6px;
                    padding:10px 12px;word-break:break-all;user-select:text;">
          ${highlighted}
        </div>
      `;
    } else {
      // 产品名对，只给出可复制的正确文件名
      bodyHtml = `
        <div style="margin-bottom:10px;font-size:13px;color:#374151;line-height:1.6;">
          产品名对，文件名其他部分不符合规范。直接复制下面这个正确文件名，用它重命名你的视频再重新上传：
        </div>
        <div style="display:flex;gap:8px;align-items:stretch;">
          <code id="filenameErrSuggestion" style="flex:1;font-family:Consolas,'SF Mono',ui-monospace,monospace;
                 font-size:13px;background:#ecfdf5;border:1px solid #a7f3d0;color:#065f46;
                 border-radius:6px;padding:12px 14px;word-break:break-all;user-select:all;">${escapeHtml(suggestion)}</code>
          <button type="button" data-act="copy" style="flex-shrink:0;border:1px solid #2563eb;
                  background:#2563eb;color:#fff;border-radius:6px;padding:0 16px;
                  font-size:13px;cursor:pointer;font-weight:500;">复制</button>
        </div>
        <div id="filenameErrCopyTip" style="margin-top:6px;font-size:12px;color:#16a34a;height:16px;"></div>
      `;
    }

    mask.innerHTML = `
      <div role="dialog" aria-modal="true" style="background:#fff;border-radius:12px;
           max-width:720px;width:100%;max-height:90vh;overflow:auto;
           box-shadow:0 12px 32px -6px rgba(15,23,42,0.25);padding:20px 22px;
           font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;">
          <h3 style="margin:0;font-size:16px;color:#1f2937;">文件名不符合命名规范</h3>
          <button type="button" data-act="close" style="border:none;background:transparent;
                  font-size:20px;line-height:1;color:#6b7280;cursor:pointer;padding:4px 8px;">×</button>
        </div>
        <div style="font-size:13px;color:#374151;line-height:1.55;">
          ${bodyHtml}
        </div>
        <div style="text-align:right;margin-top:16px;">
          <button type="button" data-act="close" style="border:1px solid #d1d5db;background:#fff;
                  color:#374151;border-radius:6px;padding:7px 18px;font-size:13px;cursor:pointer;">关闭</button>
        </div>
      </div>
    `;
    document.body.appendChild(mask);

    function close() { mask.remove(); document.removeEventListener('keydown', onKey); }
    function onKey(e) { if (e.key === 'Escape') close(); }
    document.addEventListener('keydown', onKey);
    mask.addEventListener('click', (e) => {
      if (e.target === mask) { close(); return; }
      const act = e.target.getAttribute('data-act');
      if (act === 'close') close();
      else if (act === 'copy' && suggestion) {
        const text = suggestion;
        const tip = mask.querySelector('#filenameErrCopyTip');
        const done = () => { if (tip) { tip.textContent = '已复制 ✓'; setTimeout(() => { if (tip) tip.textContent = ''; }, 2000); } };
        const fail = () => { if (tip) { tip.textContent = '复制失败，请手动选择并 Ctrl+C'; tip.style.color = '#b91c1c'; } };
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(done).catch(fail);
        } else {
          try {
            const ta = document.createElement('textarea');
            ta.value = text; document.body.appendChild(ta); ta.select();
            document.execCommand('copy'); ta.remove(); done();
          } catch (_) { fail(); }
        }
      }
    });
  }

  function renderLangBar(coverage) {
    if (!LANGUAGES.length) return '';
    const chips = LANGUAGES.map(l => {
      const c = (coverage || {})[l.code] || { items: 0, copy: 0, cover: false };
      const filled = c.items > 0;
      const cls = filled ? 'filled' : 'empty';
      const title = `${langDisplayName(l.code)}: ${c.items} 视频 / ${c.copy} 文案 / ${c.cover ? '有主图' : '无主图'}`;
      return `<span class="oc-lang-chip ${cls}" title="${escapeHtml(title)}">`
           + `${escapeHtml(langDisplayName(l.code))}`
           + `</span>`;
    });
    const rows = [];
    for (let i = 0; i < chips.length; i += 4) rows.push(chips.slice(i, i + 4));
    return `<div class="oc-lang-bar">`
         + rows.filter((row) => row.length).map((row) => `<div class="oc-lang-row">${row.join('')}</div>`).join('')
         + `</div>`;
  }

  function icon(name, size = 14) {
    return `<svg width="${size}" height="${size}" aria-hidden="true"><use href="#ic-${name}"/></svg>`;
  }

  async function fetchJSON(url, opts) {
    const res = await fetch(url, opts);
    if (!res.ok) {
      const text = await res.text().catch(() => '');
      let msg = '';
      if (text) {
        try {
          const data = JSON.parse(text);
          msg = data.error || data.message || text;
        } catch {
          msg = text;
        }
      }
      throw new Error(msg || `HTTP ${res.status}`);
    }
    return res.json();
  }

  function fmtDate(s) {
    if (!s) return '';
    const d = new Date(s);
    const pad = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  function escapeHtml(s) {
    return String(s || '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function compactCellText(value) {
    const text = String(value || '').trim();
    return text ? escapeHtml(text) : '<span class="muted">—</span>';
  }

  const ROAS_FIELDS = [
    'purchase_1688_url',
    'purchase_price',
    'packet_cost_estimated',
    'packet_cost_actual',
    'package_length_cm',
    'package_width_cm',
    'package_height_cm',
    'tk_sea_cost',
    'tk_air_cost',
    'tk_sale_price',
    'standalone_price',
    'standalone_shipping_fee',
  ];

  function numberOrNull(value) {
    const raw = String(value ?? '').trim();
    if (!raw) return null;
    const parsed = Number(raw);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function formatRoas(value) {
    return Number.isFinite(value) ? value.toFixed(2) : '无法保本';
  }

  function currentRoasRmbPerUsd() {
    const parsed = Number(window.MATERIAL_ROAS_RMB_PER_USD || 6.83);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : 6.83;
  }

  function calculateRoasBreakEven(values) {
    const price = numberOrNull(values.standalone_price);
    const shippingFee = numberOrNull(values.standalone_shipping_fee) || 0;
    const revenue = price === null ? null : price + shippingFee;
    const rmbPerUsd = currentRoasRmbPerUsd();
    const purchase = numberOrNull(values.purchase_price);
    const estimatedPacket = numberOrNull(values.packet_cost_estimated);
    const actualPacket = numberOrNull(values.packet_cost_actual);
    const calc = (packetCost) => {
      if (revenue === null || purchase === null || packetCost === null) return null;
      const available = revenue * 0.9 - (purchase / rmbPerUsd) - (packetCost / rmbPerUsd);
      if (available <= 0) return null;
      return revenue / available;
    };
    const estimated = calc(estimatedPacket);
    const actual = calc(actualPacket);
    const useActual = actualPacket !== null;
    return {
      estimated_roas: estimated,
      actual_roas: actual,
      effective_basis: useActual ? 'actual' : 'estimated',
      effective_roas: useActual ? actual : estimated,
      rmb_per_usd: rmbPerUsd,
    };
  }

  function parseAverageShippingValues(text) {
    return String(text || '')
      .split(/\r?\n/)
      .map((line) => {
        const normalized = line.replace(/[￥¥,\s]/g, '');
        const match = normalized.match(/[-+]?\d+(?:\.\d+)?/);
        if (!match) return null;
        const value = Number(match[0]);
        return Number.isFinite(value) ? value : null;
      })
      .filter((value) => value !== null);
  }

  function calculateAverageShippingText(text) {
    const values = parseAverageShippingValues(text);
    const total = values.reduce((sum, value) => sum + value, 0);
    if (!values.length) {
      return { display: '--', count: 0, total: 0, average: null };
    }
    const average = total / values.length;
    return {
      display: average.toFixed(1),
      count: values.length,
      total,
      average,
    };
  }

  function updateRoasAverageShipping() {
    const input = $('roasAverageShippingInput');
    const resultEl = $('roasAverageShippingResult');
    const metaEl = $('roasAverageShippingMeta');
    if (!input || !resultEl || !metaEl) return;
    const result = calculateAverageShippingText(input.value);
    resultEl.textContent = result.display;
    metaEl.textContent = `有效行数 ${result.count} · 合计 ${result.total.toFixed(1)}`;
  }

  window.roasAverageShippingTool = {
    parseValues: parseAverageShippingValues,
    averageText: calculateAverageShippingText,
    updateView: updateRoasAverageShipping,
  };

  function setRoasFieldValues(product) {
    ROAS_FIELDS.forEach((field) => {
      const input = document.querySelector(`[data-roas-field="${field}"]`);
      if (!input) return;
      const value = product && product[field] !== null && product[field] !== undefined ? product[field] : '';
      input.value = value;
    });
  }

  function collectRoasPayload() {
    const payload = {};
    ROAS_FIELDS.forEach((field) => {
      const input = document.querySelector(`[data-roas-field="${field}"]`);
      if (!input) return;
      const raw = String(input.value || '').trim();
      payload[field] = raw || null;
    });
    return payload;
  }

  function renderRoasResult() {
    const payload = collectRoasPayload();
    const result = calculateRoasBreakEven(payload);
    if ($('roasEstimatedValue')) $('roasEstimatedValue').textContent = formatRoas(result.estimated_roas);
    if ($('roasActualValue')) $('roasActualValue').textContent = numberOrNull(payload.packet_cost_actual) === null ? '待回填' : formatRoas(result.actual_roas);
    if ($('roasEffectiveValue')) $('roasEffectiveValue').textContent = formatRoas(result.effective_roas);
    if ($('roasEffectiveBasis')) $('roasEffectiveBasis').textContent = result.effective_basis === 'actual' ? '实际保本 ROAS' : '预估保本 ROAS';
    if ($('roasEstimatedBox')) $('roasEstimatedBox').classList.toggle('active', result.effective_basis === 'estimated');
    if ($('roasActualBox')) $('roasActualBox').classList.toggle('active', result.effective_basis === 'actual');
  }

  function markRoasResultDirty() {
    if ($('roasEstimatedValue')) $('roasEstimatedValue').textContent = '待计算';
    if ($('roasActualValue')) $('roasActualValue').textContent = '待计算';
    if ($('roasEffectiveValue')) $('roasEffectiveValue').textContent = '待计算';
    if ($('roasEffectiveBasis')) $('roasEffectiveBasis').textContent = '待计算';
    if ($('roasEstimatedBox')) $('roasEstimatedBox').classList.remove('active');
    if ($('roasActualBox')) $('roasActualBox').classList.remove('active');
  }

  function openRoasModal(product) {
    if (!product) return;
    state.roasProduct = product;
    const mask = $('roasModalMask');
    if (!mask) return;
    $('roasProductId').textContent = product.id || '—';
    $('roasProductName').textContent = product.name || '—';
    $('roasProductEnglish').textContent = product.product_code || '—';
    const cover = $('roasProductCover');
    if (cover) {
      cover.innerHTML = product.cover_thumbnail_url
        ? `<img src="${escapeHtml(product.cover_thumbnail_url)}" alt="">`
        : `<div class="roas-cover-ph">${icon('package', 24)}</div>`;
    }
    setRoasFieldValues(product);
    if ($('roasSaveMsg')) $('roasSaveMsg').textContent = '';
    markRoasResultDirty();
    mask.hidden = false;
  }

  function closeRoasModal() {
    const mask = $('roasModalMask');
    if (mask) mask.hidden = true;
    state.roasProduct = null;
  }

  async function saveRoas() {
    const product = state.roasProduct;
    const form = $('roasForm');
    if (!product || !form) return;
    const btn = $('roasSaveBtn');
    const msg = $('roasSaveMsg');
    if (btn) btn.disabled = true;
    if (msg) msg.textContent = '保存中...';
    try {
      const payload = collectRoasPayload();
      await fetchJSON('/medias/api/products/' + product.id, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      Object.assign(product, payload);
      product.roas_calculation = calculateRoasBreakEven(payload);
      closeRoasModal();
      loadList();
    } catch (e) {
      if (msg) msg.textContent = e.message || '保存失败';
      alert(e.message || '保存失败');
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  function showAiEvaluationDetail(product) {
    if (window.EvalCountryTable && typeof window.EvalCountryTable.openModal === 'function') {
      ensureAiEvaluationRequestModalStyle();
      const shell = window.EvalCountryTable.openModal(product && product.ai_evaluation_detail, {
        withRequestTab: true,
      });
      if (shell && shell.requestPanel) {
        loadAiEvaluationDetailRequestPreview(shell.requestPanel, product && product.id);
      }
    }
  }

  function aiEvaluationFailureReason(reason) {
    const text = String(reason || '').trim();
    return text || '服务器没有返回';
  }

  function aiEvaluationErrorMessage(err) {
    if (!err) return '';
    if (err.name === 'AbortError') return '';
    const message = String(err.message || err || '').trim();
    if (!message || message.includes('Unexpected end of JSON input')) return '';
    return message;
  }

  function ensureAiEvaluationRequestModalStyle() {
    if (document.getElementById('aiEvaluationRequestModalStyle')) return;
    const style = document.createElement('style');
    style.id = 'aiEvaluationRequestModalStyle';
    style.textContent = `
      .ect-modal--ai-evaluating { max-width:min(1560px, calc(100vw - 48px)); min-height:min(820px, calc(100vh - 48px)); }
      .ect-modal--ai-evaluating .ect-modal-body { display:flex; flex-direction:column; min-height:0; padding:0; overflow:hidden; }
      .ect-ai-topbar { display:flex; align-items:center; justify-content:center; gap:24px; min-height:96px; padding:24px 20px; border-bottom:1px solid var(--oc-border, oklch(91% 0.012 230)); background:var(--oc-bg-subtle, oklch(97% 0.006 230)); }
      .ect-ai-status { display:flex; align-items:center; justify-content:center; gap:16px; min-width:0; }
      .ect-ai-status-dot { width:18px; height:18px; border-radius:50%; background:var(--oc-accent, oklch(56% 0.16 230)); box-shadow:0 0 0 7px var(--oc-accent-ring, oklch(56% 0.16 230 / 0.22)); }
      .ect-ai-status-title { font-size:28px; line-height:1.3; font-weight:700; color:var(--oc-fg, oklch(22% 0.020 235)); }
      .ect-ai-request-timer { display:inline-flex; align-items:center; height:48px; padding:0 18px; border-radius:999px; background:var(--oc-cyan-subtle, oklch(94% 0.04 215)); color:var(--oc-accent, oklch(56% 0.16 230)); font-size:24px; line-height:1.3; font-weight:700; font-variant-numeric:tabular-nums; }
      .ect-ai-tabs { display:flex; gap:8px; padding:12px 20px 0; background:var(--oc-bg, oklch(99% 0.004 230)); }
      .ect-ai-tab { height:32px; padding:0 14px; border:1px solid var(--oc-border-strong, oklch(84% 0.015 230)); border-radius:8px 8px 0 0; background:var(--oc-bg-subtle, oklch(97% 0.006 230)); color:var(--oc-fg-muted, oklch(48% 0.018 230)); font-size:13px; font-weight:600; cursor:pointer; }
      .ect-ai-tab.active { background:var(--oc-bg, oklch(99% 0.004 230)); color:var(--oc-accent, oklch(56% 0.16 230)); border-color:var(--oc-accent, oklch(56% 0.16 230)); }
      .ect-ai-panels { flex:1 1 auto; min-height:0; overflow:auto; padding:20px; }
      .ect-ai-panel[hidden] { display:none !important; }
      .ect-ai-grid { display:grid; grid-template-columns:minmax(320px, 420px) minmax(0, 1fr); gap:18px; align-items:start; }
      .ect-ai-card { border:1px solid var(--oc-border, oklch(91% 0.012 230)); border-radius:12px; background:var(--oc-bg, oklch(99% 0.004 230)); padding:16px; }
      .ect-ai-card h4 { margin:0 0 12px; font-size:14px; color:var(--oc-fg, oklch(22% 0.020 235)); }
      .ect-ai-media { display:grid; gap:12px; justify-items:start; }
      .ect-ai-cover { width:180px; height:180px; border:1px solid var(--oc-border, oklch(91% 0.012 230)); border-radius:10px; overflow:hidden; background:var(--oc-bg-muted, oklch(94% 0.010 230)); }
      .ect-ai-cover img, .ect-ai-video video { width:100%; height:100%; object-fit:contain; display:block; background:var(--oc-bg-muted, oklch(94% 0.010 230)); }
      .ect-ai-video-name { width:180px; min-height:58px; color:var(--oc-fg-muted, oklch(48% 0.018 230)); font-size:13px; line-height:1.45; overflow:hidden; overflow-wrap:anywhere; display:-webkit-box; -webkit-line-clamp:3; -webkit-box-orient:vertical; }
      .ect-ai-video { width:180px; height:320px; border:1px solid var(--oc-border, oklch(91% 0.012 230)); border-radius:10px; overflow:hidden; background:var(--oc-bg-muted, oklch(94% 0.010 230)); }
      .ect-ai-kv { display:grid; grid-template-columns:92px minmax(0, 1fr); gap:8px 12px; font-size:13px; line-height:1.55; }
      .ect-ai-kv dt { color:var(--oc-fg-subtle, oklch(62% 0.015 230)); }
      .ect-ai-kv dd { margin:0; min-width:0; overflow-wrap:anywhere; color:var(--oc-fg, oklch(22% 0.020 235)); }
      .ect-ai-code { margin:0; max-height:260px; overflow:auto; padding:12px; border-radius:10px; background:var(--oc-bg-subtle, oklch(97% 0.006 230)); border:1px solid var(--oc-border, oklch(91% 0.012 230)); font:12px/1.55 var(--font-mono, ui-monospace, Consolas, monospace); white-space:pre-wrap; word-break:break-word; }
      .ect-ai-actions { display:flex; gap:10px; align-items:center; justify-content:flex-end; margin-bottom:14px; }
      .ect-ai-btn { height:32px; padding:0 12px; border-radius:8px; border:1px solid var(--oc-border-strong, oklch(84% 0.015 230)); background:var(--oc-bg, oklch(99% 0.004 230)); color:var(--oc-fg, oklch(22% 0.020 235)); font-size:13px; font-weight:600; cursor:pointer; }
      .ect-ai-btn.primary { background:var(--oc-accent, oklch(56% 0.16 230)); border-color:var(--oc-accent, oklch(56% 0.16 230)); color:var(--oc-accent-fg, oklch(99% 0 0)); }
      .ect-ai-sections { margin-top:18px; }
      .ect-ai-empty { min-height:280px; display:flex; align-items:center; justify-content:center; color:var(--oc-fg-muted, oklch(48% 0.018 230)); text-align:center; line-height:1.7; }
      .ect-ai-detail-modal .ect-modal-body { padding:16px; }
      .ect-ai-detail-modal .ect-modal-json { max-height:62vh; }
      @media (max-width: 900px) { .ect-ai-grid { grid-template-columns:1fr; } .ect-ai-panels { min-height:420px; } }
    `;
    document.head.appendChild(style);
  }

  function aiEvaluationElapsedSeconds(modalState) {
    return Math.max(0, Math.floor((Date.now() - modalState.startedAt) / 1000));
  }

  function stopAiEvaluationTimers(modalState) {
    if (!modalState) return;
    if (modalState.timer) {
      window.clearInterval(modalState.timer);
      modalState.timer = null;
    }
    if (modalState.timeoutTimer) {
      window.clearTimeout(modalState.timeoutTimer);
      modalState.timeoutTimer = null;
    }
  }

  function openAiEvaluationRequestModal(product) {
    const titleText = product && product.name ? `AI评估 - ${product.name}` : 'AI评估';
    ensureAiEvaluationRequestModalStyle();
    const shell = window.EvalCountryTable.openModal('', { title: titleText });
    const modalState = {
      overlay: shell.overlay,
      modal: shell.modal,
      close: shell.close,
      body: shell.modal.querySelector('.ect-modal-body'),
      status: null,
      statusTitle: null,
      startedAt: Date.now(),
      timer: null,
      timeoutTimer: null,
      done: false,
      activeTab: 'request',
      preview: null,
      previewError: '',
      resultHtml: '',
      resultStatus: 'loading',
      fullPayloadUrl: '',
    };
    modalState.modal.classList.add('ect-modal--ai-evaluating');

    function updateElapsed() {
      if (modalState.done) return;
      const elapsed = aiEvaluationElapsedSeconds(modalState);
      if (modalState.status) modalState.status.textContent = `已请求 ${elapsed} 秒`;
    }
    function close() {
      stopAiEvaluationTimers(modalState);
      document.removeEventListener('keydown', onKey);
      shell.close();
    }
    function onKey(event) {
      if (event.key === 'Escape') close();
    }

    shell.overlay.querySelectorAll('.ect-modal-close, .ect-modal-button').forEach((btn) => {
      btn.addEventListener('click', close, { once: true });
    });
    shell.overlay.addEventListener('click', (event) => {
      if (event.target === shell.overlay) close();
    }, { capture: true, once: true });
    document.addEventListener('keydown', onKey);
    modalState.timer = window.setInterval(updateElapsed, 1000);
    modalState.timeoutTimer = window.setTimeout(() => {
      if (modalState.done) return;
      setAiEvaluationModalFailure(modalState, '服务器没有返回');
    }, AI_EVALUATION_TIMEOUT_MS);
    renderAiEvaluationShell(modalState);
    setAiEvaluationModalLoading(modalState);
    return modalState;
  }

  function renderAiEvaluationShell(modalState) {
    if (!modalState || !modalState.body) return;
    modalState.body.innerHTML = `
      <div class="ect-ai-topbar">
        <div class="ect-ai-status">
          <span class="ect-ai-status-dot"></span>
          <span class="ect-ai-status-title" data-ai-eval-status-title>正在请求中</span>
        </div>
        <span class="ect-ai-request-timer" data-ai-eval-status>已请求 ${aiEvaluationElapsedSeconds(modalState)} 秒</span>
      </div>
      <div class="ect-ai-tabs" role="tablist">
        <button type="button" class="ect-ai-tab active" data-ai-eval-tab="request">请求报文</button>
        <button type="button" class="ect-ai-tab" data-ai-eval-tab="result">结果</button>
      </div>
      <div class="ect-ai-panels">
        <section class="ect-ai-panel" data-ai-eval-panel="request"></section>
        <section class="ect-ai-panel" data-ai-eval-panel="result" hidden></section>
      </div>`;
    modalState.status = modalState.body.querySelector('[data-ai-eval-status]');
    modalState.statusTitle = modalState.body.querySelector('[data-ai-eval-status-title]');
    modalState.body.querySelectorAll('[data-ai-eval-tab]').forEach((btn) => {
      btn.addEventListener('click', () => switchAiEvaluationTab(modalState, btn.dataset.aiEvalTab));
    });
    renderAiEvaluationRequestPreview(modalState);
    renderAiEvaluationResultPanel(modalState);
  }

  function switchAiEvaluationTab(modalState, tab) {
    modalState.activeTab = tab === 'result' ? 'result' : 'request';
    modalState.body.querySelectorAll('[data-ai-eval-tab]').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.aiEvalTab === modalState.activeTab);
    });
    modalState.body.querySelectorAll('[data-ai-eval-panel]').forEach((panel) => {
      panel.hidden = panel.dataset.aiEvalPanel !== modalState.activeTab;
    });
  }

  async function loadAiEvaluationRequestPreview(modalState, pid) {
    try {
      const data = await fetchJSON(AI_EVAL_REQUEST_PREVIEW_ENDPOINT(pid));
      modalState.preview = data.payload || null;
      modalState.fullPayloadUrl = modalState.preview && modalState.preview.full_payload_url
        || `/medias/api/products/${pid}/evaluate/request-payload`;
      renderAiEvaluationRequestPreview(modalState);
    } catch (err) {
      modalState.previewError = err && err.message ? err.message : String(err || '加载请求报文失败');
      renderAiEvaluationRequestPreview(modalState);
    }
  }

  function renderAiEvaluationRequestPreview(modalState) {
    const panel = modalState && modalState.body && modalState.body.querySelector('[data-ai-eval-panel="request"]');
    if (!panel) return;
    renderAiEvaluationRequestPreviewToPanel(panel, {
      preview: modalState.preview,
      previewError: modalState.previewError,
      fullPayloadUrl: modalState.fullPayloadUrl,
    });
  }

  async function loadAiEvaluationDetailRequestPreview(panel, pid) {
    if (!panel) return;
    if (!pid) {
      renderAiEvaluationRequestPreviewToPanel(panel, {
        previewError: '缺少产品 ID，无法加载请求报文',
      });
      return;
    }
    renderAiEvaluationRequestPreviewToPanel(panel, { preview: null });
    try {
      const data = await fetchJSON(AI_EVAL_REQUEST_PREVIEW_ENDPOINT(pid));
      const preview = data.payload || null;
      const fullPayloadUrl = preview && preview.full_payload_url
        || `/medias/api/products/${pid}/evaluate/request-payload`;
      renderAiEvaluationRequestPreviewToPanel(panel, {
        preview: preview,
        fullPayloadUrl: fullPayloadUrl,
      });
    } catch (err) {
      renderAiEvaluationRequestPreviewToPanel(panel, {
        previewError: err && err.message ? err.message : String(err || '加载请求报文失败'),
      });
    }
  }

  function renderAiEvaluationRequestPreviewToPanel(panel, opts) {
    const options = opts || {};
    const preview = options.preview;
    if (options.previewError) {
      panel.innerHTML = `<div class="ect-ai-empty">请求报文加载失败：${escapeHtml(options.previewError)}</div>`;
      return;
    }
    if (!preview) {
      panel.innerHTML = `<div class="ect-ai-empty">正在加载请求报文、素材和提示词...</div>`;
      return;
    }
    const cover = (preview.media || []).find((item) => item.role === 'product_cover') || {};
    const video = (preview.media || []).find((item) => item.role === 'english_video') || {};
    const product = preview.product || {};
    panel.innerHTML = `
      <div class="ect-ai-actions">
        <button type="button" class="ect-ai-btn primary" data-ai-full-payload>请求报文</button>
      </div>
      <div class="ect-ai-grid">
        <div class="ect-ai-card">
          <h4>素材预览</h4>
          <div class="ect-ai-media">
            <div class="ect-ai-cover">${cover.preview_url ? `<img src="${escapeHtml(cover.preview_url)}" alt="商品主图">` : '暂无主图'}</div>
            <div class="ect-ai-video-name" title="${escapeHtml(video.filename || video.object_key || '')}">${escapeHtml(video.filename || video.object_key || '暂无视频文件名')}</div>
            <div class="ect-ai-video">${video.preview_url ? `<video controls preload="metadata" src="${escapeHtml(video.preview_url)}"></video>` : '暂无视频'}</div>
          </div>
        </div>
        <div class="ect-ai-card">
          <h4>请求关键元素</h4>
          <dl class="ect-ai-kv">
            <dt>产品</dt><dd>${escapeHtml(product.name || '-')} (#${escapeHtml(product.id || '-')})</dd>
            <dt>产品 ID</dt><dd>${escapeHtml(product.product_code || '-')}</dd>
            <dt>产品链接</dt><dd>${product.product_url ? `<a href="${escapeHtml(product.product_url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(product.product_url)}</a>` : '-'}</dd>
            <dt>主图</dt><dd>${escapeHtml(cover.object_key || '-')}</dd>
            <dt>视频</dt><dd>${escapeHtml(video.object_key || '-')}</dd>
            <dt>语种</dt><dd>${escapeHtml((preview.languages || []).map((lang) => `${lang.name}(${lang.code})`).join('、') || '-')}</dd>
            <dt>UseCase</dt><dd>${escapeHtml(preview.llm && preview.llm.use_case || '-')}</dd>
            <dt>Provider</dt><dd>${escapeHtml(preview.llm && preview.llm.provider || '-')}</dd>
            <dt>Model</dt><dd>${escapeHtml(preview.llm && preview.llm.model || '-')}</dd>
            <dt>Search</dt><dd>${preview.llm && preview.llm.google_search ? escapeHtml(JSON.stringify(preview.llm.tools || [])) : '-'}</dd>
            <dt>参数</dt><dd>temperature=${escapeHtml(preview.llm && preview.llm.temperature)}, max_output_tokens=${escapeHtml(preview.llm && preview.llm.max_output_tokens)}</dd>
          </dl>
        </div>
      </div>
      ${renderAiEvaluationPromptSections(preview)}`;
    const btn = panel.querySelector('[data-ai-full-payload]');
    if (btn) btn.addEventListener('click', () => openAiEvaluationPayloadDetail({
      fullPayloadUrl: options.fullPayloadUrl || preview.full_payload_url,
    }));
  }

  function renderAiEvaluationPromptSections(preview) {
    const prompts = preview && preview.prompts || {};
    return `
      <div class="ect-ai-grid ect-ai-sections">
        <div class="ect-ai-card">
          <h4>System Prompt</h4>
          <pre class="ect-ai-code">${escapeHtml(prompts.system || '')}</pre>
        </div>
        <div class="ect-ai-card">
          <h4>User Prompt</h4>
          <pre class="ect-ai-code">${escapeHtml(prompts.user || '')}</pre>
        </div>
        <div class="ect-ai-card">
          <h4>Response Schema</h4>
          <pre class="ect-ai-code">${escapeHtml(JSON.stringify(preview.response_schema || {}, null, 2))}</pre>
        </div>
        <div class="ect-ai-card">
          <h4>请求报文预览</h4>
          <pre class="ect-ai-code">${escapeHtml(JSON.stringify(preview.request || {}, null, 2))}</pre>
        </div>
      </div>`;
  }

  function renderAiEvaluationResultPanel(modalState) {
    const panel = modalState && modalState.body && modalState.body.querySelector('[data-ai-eval-panel="result"]');
    if (!panel) return;
    if (modalState.resultHtml) {
      panel.innerHTML = modalState.resultHtml;
      return;
    }
    panel.innerHTML = `<div class="ect-ai-empty">正在等待大模型返回结构化结果...</div>`;
  }

  function simplifyAiEvaluationPayload(payload) {
    return JSON.parse(JSON.stringify(payload || {}, (key, value) => {
      if ((key === 'base64' || key === 'data_base64') && typeof value === 'string' && value.length > 160) {
        return `${value.slice(0, 96)}...(${value.length} chars)`;
      }
      return value;
    }));
  }

  async function copyAiEvaluationPayload(payload) {
    const text = JSON.stringify(payload || {}, null, 2);
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
      return;
    }
    const ta = document.createElement('textarea');
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    ta.remove();
  }

  async function openAiEvaluationPayloadDetail(modalState) {
    if (!modalState.fullPayloadUrl) return;
    const shell = window.EvalCountryTable.openModal('', { title: '报文详情' });
    shell.modal.classList.add('ect-ai-detail-modal');
    const body = shell.modal.querySelector('.ect-modal-body');
    body.innerHTML = '<div class="ect-ai-empty">正在加载完整请求报文...</div>';
    try {
      const data = await fetchJSON(modalState.fullPayloadUrl);
      const payload = data.payload || data;
      body.innerHTML = `
        <div class="ect-ai-actions"><button type="button" class="ect-ai-btn primary" data-ai-copy-payload>一键复制</button></div>
        <pre class="ect-modal-json">${escapeHtml(JSON.stringify(simplifyAiEvaluationPayload(payload), null, 2))}</pre>`;
      const copyBtn = body.querySelector('[data-ai-copy-payload]');
      if (copyBtn) copyBtn.addEventListener('click', async () => {
        await copyAiEvaluationPayload(payload);
        copyBtn.textContent = '已复制';
      });
    } catch (err) {
      body.innerHTML = `<div class="ect-ai-empty">完整报文加载失败：${escapeHtml(err && err.message || err)}</div>`;
    }
  }

  function setAiEvaluationModalResult(modalState, data) {
    if (!modalState || !modalState.body) return;
    modalState.done = true;
    stopAiEvaluationTimers(modalState);
    if (modalState.statusTitle) modalState.statusTitle.textContent = '评估完成';
    if (modalState.status) modalState.status.textContent = `总耗时 ${aiEvaluationElapsedSeconds(modalState)} 秒`;
    const detail = data && (data.ai_evaluation_detail || data.detail || data.result || data);
    if (window.EvalCountryTable && typeof window.EvalCountryTable.render === 'function') {
      modalState.resultHtml = window.EvalCountryTable.render(detail);
    } else {
      modalState.resultHtml = `<pre class="audit-detail-pre">${escapeHtml(JSON.stringify(detail || {}, null, 2))}</pre>`;
    }
    renderAiEvaluationResultPanel(modalState);
    switchAiEvaluationTab(modalState, 'result');
  }

  function setAiEvaluationModalLoading(modalState) {
    if (!modalState || !modalState.body) return;
    if (modalState.statusTitle) modalState.statusTitle.textContent = '正在请求中';
    renderAiEvaluationResultPanel(modalState);
  }

  function setAiEvaluationModalFailure(modalState, reason) {
    if (!modalState || !modalState.body) return;
    modalState.done = true;
    stopAiEvaluationTimers(modalState);
    if (modalState.statusTitle) modalState.statusTitle.textContent = '评估失败';
    if (modalState.status) modalState.status.textContent = `总耗时 ${aiEvaluationElapsedSeconds(modalState)} 秒`;
    modalState.resultHtml = `<div class="ect-ai-empty"><strong>本次评估失败</strong><br>${escapeHtml(aiEvaluationFailureReason(reason))}</div>`;
    renderAiEvaluationResultPanel(modalState);
    switchAiEvaluationTab(modalState, 'result');
  }
  function listingStatus(product) {
    return product && product.listing_status === '下架' ? '下架' : '上架';
  }

  function isListed(product) {
    return listingStatus(product) === '上架';
  }

  function listingStatusPill(status) {
    const normalized = status === '下架' ? '下架' : '上架';
    const cls = normalized === '下架' ? 'off' : 'on';
    return `<span class="oc-listing-pill ${cls}">${escapeHtml(normalized)}</span>`;
  }

  function listingStatusSelect(status) {
    const normalized = status === '下架' ? '下架' : '上架';
    return `
      <select class="oc-listing-select" data-listing-edit aria-label="上架状态">
        <option value="上架" ${normalized === '上架' ? 'selected' : ''}>上架</option>
        <option value="下架" ${normalized === '下架' ? 'selected' : ''}>下架</option>
      </select>`;
  }

  function listingActionTitleForStatus(status) {
    return status === '下架' ? '产品已下架，不能执行翻译等生产操作' : '基于原始视频发起多语言翻译';
  }

  // ---------- 商品详情图（通用控制器） ----------
  // 在"添加产品"与"编辑产品"两个弹窗里都复用。
  function createDetailImagesController(opts) {
    const section = $(opts.section);
    const grid    = $(opts.grid);
    const input   = $(opts.input);
    const pickBtn = $(opts.pickBtn);
    const badge   = $(opts.badge);
    const progressBox = $(opts.progress);
    const gifGrid    = opts.gifGrid    ? $(opts.gifGrid)    : null;
    const gifBadge   = opts.gifBadge   ? $(opts.gifBadge)   : null;
    const gifPickBtn = opts.gifPickBtn ? $(opts.gifPickBtn) : null;
    const gifInput   = opts.gifInput   ? $(opts.gifInput)   : null;
    const getLang   = opts.getLang   || (() => 'en');
    const ensurePid = opts.ensurePid || (async () => null);
    const onItemsChange = opts.onItemsChange || (() => {});
    const staticLimit = 50;
    const gifLimit = 20;
    let items = [];

    function show() { if (section) section.hidden = false; }
    function hide() { if (section) section.hidden = true; }

    function isGifItem(it) {
      const key = String((it && it.object_key) || '').toLowerCase();
      return key.endsWith('.gif');
    }

    function renderItemHTML(it, idx) {
      return `
        <div class="oc-detail-image" data-id="${it.id}">
          <img src="${escapeHtml(it.thumbnail_url)}" alt="详情图 ${idx + 1}" loading="lazy">
          <span class="oc-detail-image-idx">${idx + 1}</span>
          <button class="oc-detail-image-del" type="button" title="删除这张" aria-label="删除">
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor"
                 stroke-width="1.8" stroke-linecap="round">
              <path d="M3 3l8 8M11 3l-8 8"></path>
            </svg>
          </button>
        </div>
      `;
    }

    function renderInto(targetGrid, list, emptyText) {
      if (!targetGrid) return;
      if (!list.length) {
        targetGrid.innerHTML = `<div class="oc-detail-images-empty">${escapeHtml(emptyText)}</div>`;
      } else {
        targetGrid.innerHTML = list.map(renderItemHTML).join('');
        targetGrid.querySelectorAll('.oc-detail-image-del').forEach(btn => {
          btn.addEventListener('click', onDelete);
        });
      }
    }

    function renderGrid() {
      if (!grid) return;
      if (gifGrid) {
        const staticList = items.filter(it => !isGifItem(it));
        const gifList = items.filter(isGifItem);
        renderInto(grid, staticList, '尚未上传静态详情图');
        renderInto(gifGrid, gifList, '当前语种暂无 GIF 动图');
        if (badge)    badge.textContent = String(staticList.length);
        if (gifBadge) gifBadge.textContent = String(gifList.length);
      } else {
        renderInto(grid, items, '尚未上传详情图');
        if (badge) badge.textContent = String(items.length);
      }
      onItemsChange(items.slice());
    }

    async function onDelete(e) {
      e.stopPropagation();
      const card = e.currentTarget.closest('.oc-detail-image');
      if (!card) return;
      const imgId = parseInt(card.dataset.id, 10);
      if (!imgId) return;
      if (!window.confirm('确定删除这张详情图？')) return;
      const pid = await ensurePid({ allowCreate: false });
      if (!pid) return;
      try {
        await fetchJSON(`/medias/api/products/${pid}/detail-images/${imgId}`, {
          method: 'DELETE',
        });
        items = items.filter(x => x.id !== imgId);
        renderGrid();
      } catch (err) {
        alert('删除失败：' + (err.message || err));
      }
    }

    function setProgress(text) {
      if (!progressBox) return;
      if (!text) { progressBox.hidden = true; progressBox.textContent = ''; return; }
      progressBox.hidden = false;
      progressBox.textContent = text;
    }

    function inferMimeFromName(name) {
      const n = (name || '').toLowerCase().trim();
      if (/\.(jpe?g)(?:\?|#|$)/.test(n)) return 'image/jpeg';
      if (/\.png(?:\?|#|$)/.test(n))      return 'image/png';
      if (/\.webp(?:\?|#|$)/.test(n))     return 'image/webp';
      if (/\.gif(?:\?|#|$)/.test(n))      return 'image/gif';
      return '';
    }
    function resolveMime(f) {
      if (!f) return '';
      // 浏览器有时给出带参数 ("image/png; charset=...") 或非标准 MIME ("image/x-png")；
      // 优先按白名单精确命中，失败则按文件名后缀兜底，再失败则接受任意 image/*。
      const rawMime = (f.type || '').toLowerCase().trim();
      const mime = rawMime.split(';')[0].trim();
      if (/^image\/(jpeg|png|webp|gif)$/.test(mime)) return mime;
      const extMime = inferMimeFromName(f.name);
      if (extMime) return extMime;
      if (mime.startsWith('image/')) {
        if (mime.includes('jpeg') || mime.includes('jpg')) return 'image/jpeg';
        if (mime.includes('png'))  return 'image/png';
        if (mime.includes('webp')) return 'image/webp';
        if (mime.includes('gif'))  return 'image/gif';
        return 'image/jpeg';
      }
      return '';
    }

  async function uploadFiles(rawFiles) {
      if (!window.MEDIAS_UPLOAD_READY) { alert('本地上传未就绪，无法上传'); return; }
      const all = [...(rawFiles || [])];
      let files = all.filter(f => !!resolveMime(f));
      if (!files.length) {
        const debug = all.length
          ? all.map((f, i) => `[${i}] name=${f && f.name || '(空)'} · type=${f && f.type || '(空)'}`).join('\n')
          : '(未选中任何文件)';
        alert('请选择 JPG / PNG / WebP / GIF 图片\n\n调试信息：\n' + debug);
        return;
      }
      const currentStatic = items.filter(it => !isGifItem(it)).length;
      const currentGif = items.filter(isGifItem).length;
      let staticSlots = Math.max(0, staticLimit - currentStatic);
      let gifSlots = Math.max(0, gifLimit - currentGif);
      const kept = [];
      let skippedStatic = 0;
      let skippedGif = 0;
      for (const f of files) {
        if (resolveMime(f) === 'image/gif') {
          if (gifSlots > 0) {
            kept.push(f);
            gifSlots -= 1;
          } else {
            skippedGif += 1;
          }
        } else if (staticSlots > 0) {
          kept.push(f);
          staticSlots -= 1;
        } else {
          skippedStatic += 1;
        }
      }
      if (!kept.length) {
        alert(`当前语种已达到数量上限：静态图最多 ${staticLimit} 张，GIF 最多 ${gifLimit} 张`);
        return;
      }
      if (kept.length < files.length) {
        const parts = [];
        if (skippedStatic) parts.push(`静态图跳过 ${skippedStatic} 张`);
        if (skippedGif) parts.push(`GIF 跳过 ${skippedGif} 张`);
        alert(`当前语种数量上限：静态图最多 ${staticLimit} 张，GIF 最多 ${gifLimit} 张；${parts.join('，')}`);
        files = kept;
      }
      const pid = await ensurePid({ allowCreate: true });
      if (!pid) return;
      const lang = getLang();

      setProgress(`准备上传 ${files.length} 张…`);
      try {
        const boot = await fetchJSON(
          `/medias/api/products/${pid}/detail-images/bootstrap`,
          {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              lang,
              files: files.map(f => ({
                filename: f.name,
                content_type: resolveMime(f),
                size: f.size,
              })),
            }),
          });
        if (!boot.uploads || boot.uploads.length !== files.length) {
          throw new Error('后端返回的上传位数量不匹配');
        }

        for (let i = 0; i < files.length; i++) {
          const f = files[i];
          const u = boot.uploads[i];
          setProgress(`上传中 ${i + 1} / ${files.length}：${f.name}`);
          const putRes = await fetch(u.upload_url, {
            method: 'PUT',
            headers: { 'Content-Type': resolveMime(f) || 'application/octet-stream' },
            body: f,
          });
          if (!putRes.ok) throw new Error(`上传失败 (${i + 1}/${files.length})`);
        }

        setProgress('登记中…');
        const done = await fetchJSON(
          `/medias/api/products/${pid}/detail-images/complete`,
          {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              lang,
              images: boot.uploads.map((u, i) => ({
                object_key: u.object_key,
                content_type: resolveMime(files[i]),
                file_size: files[i].size,
              })),
            }),
          });
        items = items.concat(done.items || []);
        renderGrid();
        setProgress('');
      } catch (err) {
        setProgress('上传失败：' + (err.message || err));
        setTimeout(() => setProgress(''), 3500);
      }
    }

    async function load(pid) {
      items = [];
      renderGrid();
      if (!pid) return;
      try {
        const lang = getLang();
        const data = await fetchJSON(
          `/medias/api/products/${pid}/detail-images?lang=${encodeURIComponent(lang)}`,
        );
        items = data.items || [];
        renderGrid();
      } catch (err) {
        console.error('[detail-images] load failed', err);
      }
    }

    function reset() {
      items = [];
      renderGrid();
      setProgress('');
    }

    if (pickBtn) pickBtn.addEventListener('click', () => input && input.click());
    if (input) input.addEventListener('change', (e) => {
      // Chromium 里 input.value='' 会 mutate 先前拿到的 FileList 引用 → 长度变 0；
      // 必须先 snapshot 成 Array 再清 value，否则 uploadFiles 永远拿到空列表，
      // 误触发"请选择 JPG / PNG / WebP / GIF 图片"弹窗。
      const files = [...(e.target.files || [])];
      e.target.value = '';
      uploadFiles(files);
    });

    if (gifPickBtn) gifPickBtn.addEventListener('click', () => gifInput && gifInput.click());
    if (gifInput) gifInput.addEventListener('change', (e) => {
      const files = [...(e.target.files || [])];
      e.target.value = '';
      uploadFiles(files);
    });

    return {
      load, reset, show, hide,
      items: () => items.slice(),
      staticItems: () => items.filter(it => !isGifItem(it)),
      gifItems:    () => items.filter(isGifItem),
    };
  }

  // ---------- List ----------
  async function loadList() {
    const requestSeq = ++state.listRequestSeq;
    const kw = $('kw').value.trim();
    const params = new URLSearchParams({ page: state.page });
    if (kw) params.set('keyword', kw);
    renderSkeleton();
    try {
      await ensureLanguages();
      const data = await fetchJSON('/medias/api/products?' + params);
      if (requestSeq !== state.listRequestSeq) return;
      renderGrid(data.items);
      renderPager(data.total, data.page, data.page_size);
      const pill = $('totalPill');
      if (pill) pill.textContent = `共 ${data.total} 个产品`;
    } catch (e) {
      if (requestSeq !== state.listRequestSeq) return;
      $('grid').innerHTML = `
        <div class="oc-state">
          <div class="icon">${icon('alert', 28)}</div>
          <p class="title">加载失败</p>
          <p class="desc">${escapeHtml(e.message || '请稍后重试')}</p>
          <button class="oc-btn ghost" onclick="location.reload()">刷新页面</button>
      </div>`;
    }
  }

  function runSearchNow() {
    if (liveSearchTimer) {
      window.clearTimeout(liveSearchTimer);
      liveSearchTimer = null;
    }
    state.page = 1;
    loadList();
  }

  function runLiveSearch() {
    liveSearchTimer = null;
    runSearchNow();
  }

  function scheduleLiveSearch() {
    if (liveSearchTimer) window.clearTimeout(liveSearchTimer);
    liveSearchTimer = window.setTimeout(runLiveSearch, 250);
  }

  function renderSkeleton() {
    $('grid').innerHTML = Array.from({ length: 8 }, () => '<div class="oc-skel"></div>').join('');
  }

  function renderGrid(items) {
    const grid = $('grid');
    if (!items || !items.length) {
      grid.innerHTML = `
        <div class="oc-state">
          <div class="icon">${icon('package', 28)}</div>
          <p class="title">还没有产品素材</p>
          <p class="desc">创建你的第一个产品素材库，统一管理文案与视频资源</p>
          <button class="oc-btn primary" id="emptyCreate">
            ${icon('plus', 14)}<span>添加产品素材</span>
          </button>
        </div>`;
      const ec = $('emptyCreate');
      if (ec) ec.addEventListener('click', () => $('createBtn').click());
      return;
    }
    grid.innerHTML = `
      <table class="oc-table" style="table-layout:fixed;">
        <colgroup>
        <col style="width:48px">
        <col style="width:88px">
        <col style="width:130px">
        <col style="width:120px">
        <col style="width:80px">
        <col style="width:68px">
        <col style="width:120px">
        <col style="width:64px">
        <col style="width:88px">
        <col style="width:56px">
        <col style="width:300px">
        <col style="width:92px">
        <col style="width:150px">
        <col style="width:200px">
      </colgroup>
      <thead>
        <tr>
          <th>ID</th>
          <th>主图</th>
          <th>产品名称</th>
          <th>产品 ID</th>
          <th>明空 ID</th>
          <th>AI评分</th>
          <th>AI评估结果</th>
          <th>上架</th>
          <th>负责人</th>
          <th>素材数</th>
          <th>语种覆盖</th>
          <th>修改时间</th>
          <th>备注说明</th>
          <th>操作</th>
        </tr>
        </thead>
        <tbody>
          ${items.map(rowHTML).join('')}
        </tbody>
      </table>`;
    grid.querySelectorAll('[data-edit]').forEach(b =>
      b.addEventListener('click', (e) => { e.stopPropagation(); openEdit(+b.dataset.edit); }));
    grid.querySelectorAll('[data-del]').forEach(b =>
      b.addEventListener('click', (e) => { e.stopPropagation(); deleteProduct(+b.dataset.del); }));
    grid.querySelectorAll('[data-ai-evaluate]').forEach(b =>
      b.addEventListener('click', (e) => {
        e.stopPropagation();
        const product = items.find(item => Number(item.id) === Number(b.dataset.aiEvaluate));
        triggerAiEvaluate(+b.dataset.aiEvaluate, b, product || null);
      }));
    grid.querySelectorAll('[data-ai-detail]').forEach(b =>
      b.addEventListener('click', (e) => {
        e.stopPropagation();
        const product = items.find(item => Number(item.id) === Number(b.dataset.aiDetail));
        showAiEvaluationDetail(product || null);
      }));
    grid.querySelectorAll('[data-roas]').forEach(b =>
      b.addEventListener('click', (e) => {
        e.stopPropagation();
        const product = items.find(item => Number(item.id) === Number(b.dataset.roas));
        openRoasModal(product || null);
      }));
    grid.querySelectorAll('.oc-product-id-copy').forEach(b =>
      b.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        copyProductCode(b);
      }));
    grid.querySelectorAll('tr[data-pid] .name a').forEach(a =>
      a.addEventListener('click', (e) => { e.preventDefault(); openEdit(+a.dataset.pid); }));
    grid.querySelectorAll('td.mk-id-cell').forEach(td =>
      td.addEventListener('click', (e) => { e.stopPropagation(); startMkIdInlineEdit(td); }));
    grid.querySelectorAll('td.listing-status-cell').forEach(td =>
      td.addEventListener('click', (e) => { e.stopPropagation(); startListingStatusInlineEdit(td); }));
    grid.querySelectorAll('td.owner-cell').forEach(td =>
      td.addEventListener('click', (e) => { e.stopPropagation(); startOwnerInlineEdit(td); }));
  }

  function rowHTML(p) {
    const count = p.items_count || 0;
    const rawCount = p.raw_sources_count || 0;
    const warnCls = !p.has_en_cover ? ' class="oc-row-warn"' : '';
    const cover = p.cover_thumbnail_url
      ? `<img src="${escapeHtml(p.cover_thumbnail_url)}" alt="" loading="lazy">`
      : `<div class="cover-ph">${icon('film', 16)}</div>`;
    const productCode = (p.product_code === null || p.product_code === undefined) ? '' : String(p.product_code).trim();
    const mkIdText = (p.mk_id === null || p.mk_id === undefined) ? '' : String(p.mk_id);
    const ownerName = (p.owner_name || '').trim();
    const ownerUid = (p.user_id === null || p.user_id === undefined) ? '' : String(p.user_id);
    const ownerCellCls = window.IS_ADMIN ? 'wrap owner-cell' : 'wrap';
    const ownerCellTitle = window.IS_ADMIN ? (ownerName || '点击指派负责人') : ownerName;
    const listed = isListed(p);
    const listingTitle = listingActionTitleForStatus(listingStatus(p));
    const mkIdCell = mkIdText
      ? `<span class="mk-id-text">${escapeHtml(mkIdText)}</span>`
      : `<span class="mk-id-text"><span class="muted">—</span></span>`;
    const productCodeCell = productCode
      ? `<div class="oc-product-id-main"><a href="https://newjoyloo.com/products/${encodeURIComponent(productCode)}" target="_blank" rel="noopener noreferrer">${escapeHtml(productCode)}</a></div>`
        + `<button type="button" class="oc-btn text sm oc-product-id-copy" data-product-code="${escapeHtml(productCode)}" data-copy-label="复制" title="复制产品 ID" aria-label="复制产品 ID">${icon('copy', 12)}<span>复制</span></button>`
      : '<span class="muted">—</span>';
    return `
      <tr${warnCls} data-pid="${p.id}">
        <td class="mono">${p.id}</td>
        <td><div class="oc-thumb-sm">${cover}</div></td>
        <td class="name wrap"><a href="#" data-pid="${p.id}" title="${escapeHtml(p.name)}">${escapeHtml(p.name)}</a></td>
        <td class="mono wrap oc-product-id-cell" title="${escapeHtml(productCode)}">${productCodeCell}</td>
        <td class="mono mk-id-cell" data-pid="${p.id}" data-mkid="${escapeHtml(mkIdText)}" title="点击编辑明空 ID">${mkIdCell}</td>
        <td class="mono ai-score">${p.ai_score !== null && p.ai_score !== undefined ? p.ai_score : '<span class="muted">—</span>'}</td>
        <td class="wrap ai-result" title="${escapeHtml(p.ai_evaluation_result || '')}">
          <div class="ai-result-text">${compactCellText(p.ai_evaluation_result)}</div>
          <button type="button" class="oc-btn sm ghost ai-detail-btn" data-ai-detail="${p.id}">评估详情</button>
        </td>
        <td class="listing-status-cell" data-pid="${p.id}" data-listing-status="${escapeHtml(listingStatus(p))}" title="点击编辑上架状态">${listingStatusPill(listingStatus(p))}</td>
        <td class="${ownerCellCls}" data-pid="${p.id}" data-owner-uid="${escapeHtml(ownerUid)}" data-owner-name="${escapeHtml(ownerName)}" title="${escapeHtml(ownerCellTitle)}">${ownerName ? escapeHtml(ownerName) : '<span class="muted">—</span>'}</td>
        <td><span class="oc-pill">${count}</span></td>
        <td>${renderLangBar(p.lang_coverage)}</td>
        <td class="muted">${fmtDate(p.updated_at)}</td>
        <td class="wrap material-remark" title="${escapeHtml(p.remark || '')}">${compactCellText(p.remark)}</td>
        <td class="actions">
          <div class="oc-row-actions">
            <button class="oc-btn sm ghost" data-edit="${p.id}">${icon('edit', 12)}<span>编辑</span></button>
            <button class="oc-btn sm ghost js-raw-sources" data-pid="${p.id}" data-name="${escapeHtml(p.name)}">原始视频 (${rawCount})</button>
            <button class="bt-row-btn js-translate" data-pid="${p.id}" data-name="${escapeHtml(p.name)}" title="${escapeHtml(listingTitle)}" ${listed ? '' : 'disabled aria-disabled="true"'}>🌐 翻译</button>
            <button class="oc-btn sm ghost" data-ai-evaluate="${p.id}" title="手动触发 AI 评估">${icon('zap', 12)}<span>${aiEvalBtnLabel(p)}</span></button>
            <button class="oc-btn sm ghost" data-roas="${p.id}"><span>ROAS</span></button>
          </div>
        </td>
      </tr>`;
  }

  async function startMkIdInlineEdit(td) {
    if (td.dataset.editing === '1') return;
    td.dataset.editing = '1';
    const pid = +td.dataset.pid;
    const original = td.dataset.mkid || '';
    const input = document.createElement('input');
    input.type = 'text';
    input.inputMode = 'numeric';
    input.maxLength = 8;
    input.value = original;
    input.className = 'mk-id-input';
    input.setAttribute('aria-label', '明空 ID');
    td.innerHTML = '';
    td.appendChild(input);
    input.focus();
    input.select();

    let settled = false;

    function restore(value) {
      td.dataset.mkid = value;
      td.dataset.editing = '';
      td.innerHTML = value
        ? `<span class="mk-id-text">${escapeHtml(value)}</span>`
        : `<span class="mk-id-text"><span class="muted">—</span></span>`;
    }

    async function commit() {
      if (settled) return;
      settled = true;
      const raw = input.value.trim();
      if (raw === original) { restore(original); return; }
      if (raw !== '' && !/^\d{1,8}$/.test(raw)) {
        input.classList.add('error');
        input.focus();
        settled = false;
        return;
      }
      input.disabled = true;
      try {
        await fetchJSON('/medias/api/products/' + pid, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ mk_id: raw === '' ? null : parseInt(raw, 10) }),
        });
        restore(raw);
      } catch (e) {
        const msg = (e.message || '').toString();
        if (msg.includes('mk_id_conflict') || msg.includes('明空 ID 已被其他产品占用')) {
          alert('明空 ID 已被其他产品占用');
        } else if (msg.includes('mk_id_invalid') || msg.includes('必须是 1-8 位数字')) {
          alert('明空 ID 必须是 1-8 位数字');
        } else {
          alert('保存失败：' + msg);
        }
        input.disabled = false;
        input.classList.add('error');
        input.focus();
        settled = false;
      }
    }

    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); input.blur(); }
      else if (e.key === 'Escape') { e.preventDefault(); settled = true; restore(original); }
    });
    input.addEventListener('blur', commit);
  }

  async function startListingStatusInlineEdit(td) {
    if (td.dataset.listingEdit === '1') return;
    td.dataset.listingEdit = '1';
    const pid = +td.dataset.pid;
    const original = listingStatus({ listing_status: td.dataset.listingStatus });
    td.innerHTML = listingStatusSelect(original);
    const select = td.querySelector('select');
    select.focus();

    let settled = false;

    function syncRowAction(status) {
      const row = td.closest('tr');
      const translateBtn = row ? row.querySelector('.js-translate') : null;
      if (!translateBtn) return;
      const listed = status === '上架';
      translateBtn.disabled = !listed;
      if (listed) {
        translateBtn.removeAttribute('aria-disabled');
      } else {
        translateBtn.setAttribute('aria-disabled', 'true');
      }
      translateBtn.title = listingActionTitleForStatus(status);
    }

    function restore(status) {
      td.dataset.listingStatus = status;
      td.dataset.listingEdit = '';
      td.innerHTML = listingStatusPill(status);
      syncRowAction(status);
    }

    async function commit() {
      if (settled) return;
      settled = true;
      const nextStatus = select.value === '下架' ? '下架' : '上架';
      if (nextStatus === original) {
        restore(original);
        return;
      }
      select.disabled = true;
      try {
        await fetchJSON('/medias/api/products/' + pid, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ listing_status: nextStatus }),
        });
        restore(nextStatus);
      } catch (e) {
        alert('保存上架状态失败：' + (e.message || e));
        restore(original);
      }
    }

    function cancel() {
      if (settled) return;
      settled = true;
      restore(original);
    }

    select.addEventListener('click', (e) => e.stopPropagation());
    select.addEventListener('change', commit);
    select.addEventListener('blur', commit);
    select.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        cancel();
      }
      if (e.key === 'Enter') {
        e.preventDefault();
        commit();
      }
    });
  }

  let _activeUsersCache = null;
  async function ensureActiveUsers() {
    if (_activeUsersCache) return _activeUsersCache;
    const data = await fetchJSON('/medias/api/users/active');
    _activeUsersCache = Array.isArray(data && data.users) ? data.users : [];
    return _activeUsersCache;
  }

  async function startOwnerInlineEdit(td) {
    if (td.dataset.ownerEdit === '1') return;
    td.dataset.ownerEdit = '1';
    const pid = +td.dataset.pid;
    const originalUid = (td.dataset.ownerUid || '').trim();
    const originalName = td.dataset.ownerName || '';
    const originalHTML = td.innerHTML;

    let users;
    try {
      users = await ensureActiveUsers();
    } catch (e) {
      alert('加载用户列表失败：' + (e.message || e));
      td.dataset.ownerEdit = '';
      return;
    }

    const select = document.createElement('select');
    select.className = 'owner-select';
    select.setAttribute('aria-label', '指派负责人');
    // 若原负责人当前不在 active 列表里（被禁用），先占位保留显示
    const hasOriginalInActive = users.some(u => String(u.id) === originalUid);
    if (originalUid && !hasOriginalInActive) {
      const opt = document.createElement('option');
      opt.value = originalUid;
      opt.textContent = originalName ? (originalName + '（已停用）') : '(当前负责人)';
      select.appendChild(opt);
    }
    if (!originalUid) {
      const opt = document.createElement('option');
      opt.value = '';
      opt.textContent = '—';
      opt.disabled = true;
      select.appendChild(opt);
    }
    users.forEach(u => {
      const opt = document.createElement('option');
      opt.value = String(u.id);
      opt.textContent = u.display_name || ('#' + u.id);
      select.appendChild(opt);
    });
    select.value = originalUid || '';

    td.innerHTML = '';
    td.appendChild(select);
    select.focus();

    let settled = false;

    function restore() {
      td.dataset.ownerEdit = '';
      td.innerHTML = originalHTML;
    }

    function applyNewOwner(uid, name) {
      td.dataset.ownerEdit = '';
      td.dataset.ownerUid = String(uid);
      td.dataset.ownerName = name || '';
      td.title = name || '点击指派负责人';
      td.innerHTML = name
        ? escapeHtml(name)
        : '<span class="muted">—</span>';
    }

    async function commit() {
      if (settled) return;
      settled = true;
      const nextUid = (select.value || '').trim();
      if (!nextUid || nextUid === originalUid) {
        restore();
        return;
      }
      select.disabled = true;
      try {
        const data = await fetchJSON(`/medias/api/products/${pid}/owner`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ user_id: +nextUid }),
        });
        applyNewOwner(data.user_id, data.owner_name || '');
        const toastName = (data.owner_name || '').trim();
        alert(toastName ? `已转交给 ${toastName}` : '负责人已更新');
      } catch (e) {
        alert('切换负责人失败：' + (e.message || e));
        restore();
      }
    }

    function cancel() {
      if (settled) return;
      settled = true;
      restore();
    }

    select.addEventListener('click', (e) => e.stopPropagation());
    select.addEventListener('change', commit);
    select.addEventListener('blur', commit);
    select.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        cancel();
      }
      if (e.key === 'Enter') {
        e.preventDefault();
        commit();
      }
    });
  }

  function closeAllMenus() {
    document.querySelectorAll('.oc-menu-pop.open').forEach(m => m.classList.remove('open'));
  }
  document.addEventListener('click', closeAllMenus);

  function renderPager(total, page, pageSize) {
    const pages = Math.max(1, Math.ceil(total / pageSize));
    const p = $('pager');
    if (pages <= 1) { p.innerHTML = ''; return; }
    let html = '';
    for (let i = 1; i <= pages; i++) {
      html += `<button class="${i === page ? 'active' : ''}" data-page="${i}">${i}</button>`;
    }
    p.innerHTML = html;
    p.querySelectorAll('[data-page]').forEach(b => b.addEventListener('click', () => {
      state.page = +b.dataset.page; loadList();
    }));
  }

  async function deleteProduct(pid) {
    if (!confirm('确认删除该产品及其所有素材？此操作不可恢复。')) return;
    await fetch('/medias/api/products/' + pid, { method: 'DELETE' });
    loadList();
  }

  function aiEvalBtnLabel(p) {
    const r = p.ai_evaluation_result || '';
    if (r === '评估失败') return 'AI评估(失败)';
    if (r) return 'AI重评';
    return 'AI评估';
  }

  async function triggerAiEvaluate(pid, btn, product) {
    const origHTML = btn.innerHTML;
    const modalState = openAiEvaluationRequestModal(product || { id: pid });
    loadAiEvaluationRequestPreview(modalState, pid);
    const controller = window.AbortController ? new AbortController() : null;
    btn.disabled = true;
    btn.innerHTML = icon('loader', 12) + '<span>请求中...</span>';
    const timeout = window.setTimeout(() => {
      if (controller) controller.abort();
    }, AI_EVALUATION_TIMEOUT_MS);
    try {
      const data = await fetchJSON('/medias/api/products/' + pid + '/evaluate', {
        method: 'POST',
        signal: controller ? controller.signal : undefined,
      });
      let fresh = null;
      try {
        fresh = await fetchJSON('/medias/api/products/' + pid);
      } catch (_) {
        fresh = null;
      }
      setAiEvaluationModalResult(modalState, (fresh && fresh.product) || data.result || data);
      btn.innerHTML = icon('check', 12) + '<span>已完成</span>';
      await loadList();
      setTimeout(() => { btn.innerHTML = origHTML; btn.disabled = false; }, 1200);
    } catch (err) {
      if (!modalState.done) {
        setAiEvaluationModalFailure(modalState, aiEvaluationErrorMessage(err));
      }
      btn.innerHTML = origHTML;
      btn.disabled = false;
    } finally {
      window.clearTimeout(timeout);
    }
  }

  // ---------- Modal ----------
  let mDetailImagesCtrl = null;

  function ensureMDetailImagesCtrl() {
    if (mDetailImagesCtrl) return mDetailImagesCtrl;
    mDetailImagesCtrl = createDetailImagesController({
      section: 'mDetailImagesSection', // 外层 section 无需 hidden 切换，保持可见即可
      grid:    'mDetailImagesGrid',
      input:   'mDetailImagesInput',
      pickBtn: 'mDetailImagesPickBtn',
      badge:   'mDetailImagesBadge',
      progress:'mDetailImagesProgress',
      getLang: () => 'en',
      ensurePid: async (arg) => {
        const cur = state.current && state.current.product;
        if (cur && cur.id) return cur.id;
        if (arg && arg.allowCreate === false) return null;
        return await ensureProductIdForUpload();
      },
    });
    return mDetailImagesCtrl;
  }

  function showModal() { $('editMask').hidden = false; }
  function hideModal() {
    $('editMask').hidden = true;
    state.current = null;
    if (mDetailImagesCtrl) mDetailImagesCtrl.reset();
  }

  function openCreate() {
    state.current = { product: null, copywritings: [], items: [] };
    state.pendingItemCover = null;
    $('modalTitle').textContent = '添加产品素材';
    $('mName').value = '';
    $('mCode').value = '';
    setCover(null);
    setItemCover(null);
    renderCopywritings([]);
    renderItems([]);
    $('uploadProgress').innerHTML = '';
    const ctrl = ensureMDetailImagesCtrl();
    ctrl.reset();
    showModal();
    setTimeout(() => $('mName').focus(), 80);
  }

  async function openEdit(pid) {
    return openEditDetail(pid);
  }

  // ---------- Cover ----------
  const SLUG_RE = /^[a-z0-9][a-z0-9-]{1,126}[a-z0-9]$/;
  const PRODUCT_CODE_SUFFIX = '-rjc';
  const PRODUCT_CODE_SUFFIX_ERROR = 'Product ID 必须以 -RJC 结尾';

  function validateProductCodeForSubmit(code) {
    if (!code) return '产品 ID 必填';
    if (!code.endsWith(PRODUCT_CODE_SUFFIX)) return PRODUCT_CODE_SUFFIX_ERROR;
    if (!SLUG_RE.test(code)) return '产品 ID 必填且需合法（小写字母/数字/连字符，3–128）';
    return '';
  }

  function setCover(url) {
    const dz = $('coverDropzone');
    const img = $('coverImg');
    const replace = $('coverReplace');
    if (url) {
      img.src = url; img.hidden = false; dz.hidden = true;
      if (replace) replace.hidden = false;
    } else {
      img.removeAttribute('src'); img.hidden = true; dz.hidden = false;
      if (replace) replace.hidden = true;
    }
  }

  // ---- Item cover (add modal, pending) ----
  // 注：添加弹窗已不再有 itemCover 区块，节点可能不存在；做 null 守卫。
  function setItemCover(url) {
    const dz = $('itemCoverDropzone');
    const img = $('itemCoverImg');
    if (!dz || !img) return;
    const replace = $('itemCoverReplace');
    const clear = $('itemCoverClear');
    if (url) {
      img.src = url; img.hidden = false; dz.hidden = true;
      if (replace) replace.hidden = false;
      if (clear) clear.hidden = false;
    } else {
      img.removeAttribute('src'); img.hidden = true; dz.hidden = false;
      if (replace) replace.hidden = true;
      if (clear) clear.hidden = true;
    }
  }

  async function importCoverFromUrl() {
    const url = $('coverUrl').value.trim();
    if (!url) { alert('请粘贴图片 URL'); return; }
    const pid = await ensureProductIdForUpload();
    if (!pid) return;
    try {
      const done = await fetchJSON(`/medias/api/products/${pid}/cover/from-url`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url }),
      });
      state.current.product.cover_object_key = done.object_key;
      setCover(done.cover_url + `?_=${Date.now()}`);
      $('coverUrl').value = '';
    } catch (e) {
      alert('从 URL 导入失败：' + (e.message || ''));
    }
  }

  async function importItemCoverFromUrl() {
    const url = $('itemCoverUrl').value.trim();
    if (!url) { alert('请粘贴图片 URL'); return; }
    const pid = await ensureProductIdForUpload();
    if (!pid) return;
    try {
      const done = await fetchJSON(`/medias/api/products/${pid}/item-cover/from-url`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url }),
      });
      state.pendingItemCover = done.object_key;
      setItemCover(url);  // 先展示原 URL 预览
      $('itemCoverUrl').value = '';
    } catch (e) {
      alert('从 URL 导入失败：' + (e.message || ''));
    }
  }

  async function uploadItemCover(file) {
    if (!window.MEDIAS_UPLOAD_READY) { alert('本地上传未就绪，无法上传'); return; }
    if (!file.type.startsWith('image/')) { alert('请上传图片文件'); return; }
    const pid = await ensureProductIdForUpload();
    if (!pid) return;
    try {
      const boot = await fetchJSON(`/medias/api/products/${pid}/item-cover/bootstrap`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename: file.name }),
      });
      const putRes = await fetch(boot.upload_url, { method: 'PUT', body: file });
      if (!putRes.ok) throw new Error('上传失败');
      state.pendingItemCover = boot.object_key;
      const blobUrl = URL.createObjectURL(file);
      setItemCover(blobUrl);
    } catch (e) {
      alert('视频封面上传失败：' + (e.message || ''));
    }
  }

  function clearItemCover() {
    state.pendingItemCover = null;
    setItemCover(null);
  }

  async function uploadCover(file) {
    if (!window.MEDIAS_UPLOAD_READY) { alert('本地上传未就绪，无法上传'); return; }
    if (!file.type.startsWith('image/')) { alert('请上传图片文件'); return; }
    const pid = await ensureProductIdForUpload();
    if (!pid) return;
    try {
      const boot = await fetchJSON(`/medias/api/products/${pid}/cover/bootstrap`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename: file.name }),
      });
      const putRes = await fetch(boot.upload_url, { method: 'PUT', body: file });
      if (!putRes.ok) throw new Error('上传失败');
      const done = await fetchJSON(`/medias/api/products/${pid}/cover/complete`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ object_key: boot.object_key }),
      });
      state.current.product.cover_object_key = boot.object_key;
      setCover(done.cover_url + `?_=${Date.now()}`);
    } catch (e) {
      alert('封面上传失败：' + (e.message || ''));
    }
  }

  // ---------- Items ----------
  function renderItems(items) {
    const g = $('itemsGrid');
    g.innerHTML = items.map(it => `
      <div class="oc-item" data-item="${it.id}">
        <div class="thumb">
          ${it.cover_url
            ? `<img src="${escapeHtml(it.cover_url)}" loading="lazy" alt="">`
            : `<div class="thumb-ph">${icon('film', 20)}</div>`}
          <button class="rm" type="button" aria-label="删除">${icon('close', 12)}</button>
        </div>
        <div class="name" title="${escapeHtml(it.display_name || it.filename)}">${escapeHtml(it.display_name || it.filename)}</div>
      </div>
    `).join('');
    g.querySelectorAll('[data-item]').forEach(card => {
      card.querySelector('.rm').addEventListener('click', () => removeItem(+card.dataset.item, card));
    });
    $('itemsBadge').textContent = items.length;
  }

  async function removeItem(itemId, card) {
    if (!confirm('确认删除该素材？')) return;
    await fetch('/medias/api/items/' + itemId, { method: 'DELETE' });
    card.remove();
    $('itemsBadge').textContent = document.querySelectorAll('.oc-item').length;
  }

  async function ensureProductIdForUploadBase() {
    if (state.current && state.current.product && state.current.product.id) return state.current.product.id;
    const name = $('mName').value.trim();
    const code = $('mCode').value.trim().toLowerCase();
    if (!name) { alert('请先填写产品名称'); $('mName').focus(); return null; }
    if (!SLUG_RE.test(code)) { alert('请先填写合法的产品 ID（小写字母/数字/连字符，3–128）'); $('mCode').focus(); return null; }
    try {
      const res = await fetchJSON('/medias/api/products', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, product_code: code }),
      });
      const full = await fetchJSON('/medias/api/products/' + res.id);
      state.current = full;
      $('modalTitle').textContent = '编辑产品素材';
      return res.id;
    } catch (e) {
      const msg = (e.message || '').toString();
      if (msg.includes('已被占用')) { alert('产品 ID 已被占用'); $('mCode').focus(); }
      else alert('创建失败：' + msg);
      return null;
    }
  }

  async function ensureProductIdForUpload() {
    if (state.current && state.current.product && state.current.product.id) {
      return state.current.product.id;
    }
    const name = $('mName').value.trim();
    const code = $('mCode').value.trim().toLowerCase();
    if (!name) return ensureProductIdForUploadBase();
    const codeError = validateProductCodeForSubmit(code);
    if (codeError) { alert(codeError); $('mCode').focus(); return null; }
    return ensureProductIdForUploadBase();
  }

  async function uploadVideo(file) {
    if (!window.MEDIAS_UPLOAD_READY) { alert('本地上传未就绪，无法上传'); return; }
    const pid = await ensureProductIdForUpload();
    if (!pid) return;
    await ensureLanguages();
    const productName = state.current && state.current.product && state.current.product.name;
    const lang = resolveMaterialFilenameLang(file.name, 'en');
    const box = $('uploadProgress');
    const row = document.createElement('div');
    row.className = 'oc-upload-row';
    row.innerHTML = `<span class="fname">${escapeHtml(file.name)}</span><span>上传中…</span>`;
    box.appendChild(row);
    try {
      const boot = await fetchJSON(`/medias/api/products/${pid}/items/bootstrap`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename: file.name, lang, skip_validation: true }),
      });
      const putRes = await fetch(boot.upload_url, { method: 'PUT', body: file });
      if (!putRes.ok) throw new Error('上传失败');
      await fetchJSON(`/medias/api/products/${pid}/items/complete`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          object_key: boot.object_key,
          filename: file.name,
          file_size: file.size,
          cover_object_key: state.pendingItemCover || null,
          lang,
          skip_validation: true,
        }),
      });
      state.pendingItemCover = null;
      setItemCover(null);
      row.className = 'oc-upload-row ok';
      row.innerHTML = `<span class="fname">${escapeHtml(file.name)}</span><span>完成</span>`;
    } catch (e) {
      row.className = 'oc-upload-row err';
      row.innerHTML = `<span class="fname">${escapeHtml(file.name)}</span><span>失败：${escapeHtml(e.message || '')}</span>`;
    }
    const prevCoverKey = state.current && state.current.product && state.current.product.cover_object_key;
    const full = await fetchJSON('/medias/api/products/' + pid);
    state.current = full;
    if (prevCoverKey && !state.current.product.cover_object_key) {
      state.current.product.cover_object_key = prevCoverKey;
    }
    setCover(full.product.cover_thumbnail_url || null);
    renderItems(full.items);
    loadList();
  }

  async function saveBase() {
    const name = $('mName').value.trim();
    const code = $('mCode').value.trim().toLowerCase();
    if (!name) { alert('产品名称必填'); $('mName').focus(); return; }
    if (!SLUG_RE.test(code)) { alert('产品 ID 必填且需合法（小写字母/数字/连字符，3–128）'); $('mCode').focus(); return; }
    const cw = collectCopywritings();
    const pid = state.current.product.id;
    try {
      await fetchJSON('/medias/api/products/' + pid, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name, product_code: code,
          cover_object_key: state.current.product.cover_object_key,
          copywritings: { en: cw },
        }),
      });
      hideModal();
      loadList();
    } catch (e) {
      const msg = (e.message || '').toString();
      if (msg.includes('已被占用')) { alert('产品 ID 已被占用'); $('mCode').focus(); }
      else alert('保存失败：' + msg);
    }
  }

  async function save() {
    const name = $('mName').value.trim();
    const code = $('mCode').value.trim().toLowerCase();
    if (!name) return saveBase();
    const codeError = validateProductCodeForSubmit(code);
    if (codeError) { alert(codeError); $('mCode').focus(); return; }
    return saveBase();
  }

  // ---------- Copywritings ----------
  // 添加弹窗：文案改为单 textarea。保留函数名/调用签名，内部改为读写 #cwBody；
  // 老数据若有多条则按空行拼接展示。
  function renderCopywritings(list) {
    const box = $('cwBody');
    if (!box) return;
    const parts = (list || [])
      .map(c => (c && c.body ? String(c.body).trim() : ''))
      .filter(Boolean);
    box.value = parts.join('\n\n');
  }

  function collectCopywritings() {
    const el = $('cwBody');
    const text = el ? (el.value || '').trim() : '';
    return text ? [{ body: text }] : [];
  }

  async function requestMkCopywriting(rawCode) {
    const params = new URLSearchParams({ product_code: rawCode });
    const data = await fetchJSON(`/medias/api/mk-copywriting?${params.toString()}`);
    const copywriting = (data.copywriting || '').trim();
    if (!copywriting) throw new Error('明空系统没有返回可用文案');
    return copywriting;
  }

  function alertMkCopywritingFetchError(error) {
    const msg = (error && error.message ? error.message : error || '').toString();
    if (msg.includes('mk_credentials_expired')) {
      alert('明空登录已失效，请重新同步 wedev 凭据');
    } else if (msg.includes('mk_credentials_missing')) {
      alert('明空凭据未配置，请先在设置页同步 wedev 凭据');
    } else if (msg.includes('mk_copywriting_not_found')) {
      alert('明空系统未找到与当前产品 ID 精准匹配的文案');
    } else if (msg.includes('mk_copywriting_empty')) {
      alert('明空系统找到了该产品，但没有可用文案');
    } else {
      alert('从明空系统获取文案失败：' + msg);
    }
  }

  async function fillCopywritingFromMkSystem() {
    const nameInput = $('mName');
    const codeInput = $('mCode');
    const textarea = $('cwBody');
    const btn = $('mkCopyFetchBtn');
    const label = btn ? btn.querySelector('span') : null;
    const originalLabel = label ? label.textContent : '';
    const name = nameInput ? nameInput.value.trim() : '';
    const rawCode = codeInput ? codeInput.value.trim() : '';
    const normalizedCode = rawCode.toLowerCase();

    if (!name) {
      alert('请先填写产品名称');
      if (nameInput) nameInput.focus();
      return;
    }
    if (!SLUG_RE.test(normalizedCode)) {
      alert('请先填写合法的产品 ID（小写字母/数字/连字符，3–128）');
      if (codeInput) codeInput.focus();
      return;
    }
    if (!textarea) return;

    if (btn) btn.disabled = true;
    if (label) label.textContent = '获取中...';
    try {
      const copywriting = await requestMkCopywriting(rawCode);
      if (textarea.value.trim() && !confirm('当前文案不为空，是否用明空文案覆盖？')) {
        return;
      }
      textarea.value = copywriting;
      textarea.focus();
    } catch (e) {
      alertMkCopywritingFetchError(e);
    } finally {
      if (btn) btn.disabled = false;
      if (label) label.textContent = originalLabel || '一键从明空系统获取';
    }
  }

  // ========== Edit Detail Modal ==========
  const edState = {
    current: null, activeLang: 'en', productData: null,
    // 新增素材提交大框 - 待上传的视频封面图本地 object_key
    pendingItemCover: null,
    // 新增素材提交大框 - 待提交的视频 File 对象
    pendingVideoFile: null,
    isSubmittingNewItem: false,
    // 小语种详情图翻译任务历史（按语种缓存）
    detailTranslateTasks: {},
    linkCheckPollTimer: null,
    linkCheckModalLang: '',
    linkCheckDetailTask: null,
    linkCheckDetailError: '',
  };

  function edShow() { $('edMask').hidden = false; }
  function edHide() {
    if (edState.isSubmittingNewItem) {
      alert('素材上传中，请等待完成后再关闭');
      return;
    }
    edCloseLinkCheckModal();
    edStopLinkCheckPoll();
    $('edMask').hidden = true;
    if ($('edFromUrlMask')) $('edFromUrlMask').hidden = true;
    if ($('edNewItemMask')) $('edNewItemMask').hidden = true;
    if ($('edDetailTranslateTaskMask')) $('edDetailTranslateTaskMask').hidden = true;
    edState.current = null;
    edState.activeLang = 'en';
    edState.productData = null;
    edState.detailTranslateTasks = {};
    edState.linkCheckDetailTask = null;
    edState.linkCheckDetailError = '';
    edResetNewItemForm();
  }

  let edDetailImagesCtrl = null;

  function ensureEdDetailImagesCtrl() {
    if (edDetailImagesCtrl) return edDetailImagesCtrl;
    edDetailImagesCtrl = createDetailImagesController({
      section:    'edDetailImagesSection',
      grid:       'edDetailImagesGrid',
      gifGrid:    'edDetailGifImagesGrid',
      input:      'edDetailImagesInput',
      pickBtn:    'edDetailImagesPickBtn',
      gifInput:   'edDetailGifImagesInput',
      gifPickBtn: 'edDetailGifImagesPickBtn',
      badge:      'edDetailImagesBadge',
      gifBadge:   'edDetailGifImagesBadge',
      progress:   'edDetailImagesProgress',
      getLang: () => edState.activeLang,
      ensurePid: async () => {
        const p = edState.productData && edState.productData.product;
        return p ? p.id : null;
      },
      onItemsChange: () => edSyncDetailImagesDownloadZipButton(),
    });
    return edDetailImagesCtrl;
  }

  function edSyncDetailImagesDownloadZipButton() {
    const p = edState.productData && edState.productData.product;
    const ctrl = edDetailImagesCtrl;
    const productBtn = $('edDownloadProductImagesBtn');
    if (productBtn) {
      productBtn.disabled = !(p && p.id);
    }
    const staticBtn = $('edDetailImagesDownloadZipBtn');
    if (staticBtn) {
      const list = (ctrl && ctrl.staticItems) ? ctrl.staticItems() : [];
      staticBtn.disabled = !(p && p.id && list.length);
    }
    const gifBtn = $('edDetailGifImagesDownloadZipBtn');
    if (gifBtn) {
      const list = (ctrl && ctrl.gifItems) ? ctrl.gifItems() : [];
      gifBtn.disabled = !(p && p.id && list.length);
    }
  }

  function edRenderAdSupportedLangs(selected) {
    const box = $('edAdSupportedLangsBox');
    if (!box) return;
    const selectedSet = new Set(
      (selected || '').split(',').map(s => s.trim().toLowerCase()).filter(Boolean)
    );
    const langs = (LANGUAGES || []).filter(l => l.code !== 'en');
    if (!langs.length) {
      box.innerHTML = '<span class="oc-hint">暂无可选语种</span>';
      return;
    }
    box.innerHTML = langs.map(l => {
      const checked = selectedSet.has(l.code) ? 'checked' : '';
      return `<label class="oc-lang-checkbox">`
           + `<input type="checkbox" name="ad_supported_langs" value="${escapeHtml(l.code)}" ${checked}/>`
           + `<span>${escapeHtml(langDisplayName(l.code))}</span>`
           + `</label>`;
    }).join('');
  }

  function edCanonicalCopyField(label) {
    const text = String(label || '').trim().toLowerCase();
    if (!text) return '';
    if (['标题', 'title', 'headline', 'subject'].includes(text)) return 'title';
    if (['文案', 'copy', 'body', 'text', 'content'].includes(text)) return 'body';
    if (['描述', 'description', 'desc', 'detail'].includes(text)) return 'description';
    return '';
  }

  function edAppendCopyFieldValue(target, key, rawValue) {
    const normalizedValue = String(rawValue || '')
      .replace(/\r\n?/g, '\n')
      .replace(/\n+/g, ' ')
      .replace(/[ \t\u00A0]+/g, ' ')
      .trim();
    const value = edStripLeadingCopyFieldLabel(normalizedValue, key);
    if (!value) return;
    target[key] = target[key] ? `${target[key]} ${value}`.trim() : value;
  }

  function edStripLeadingCopyFieldLabel(rawValue, expectedKey) {
    let value = String(rawValue || '').trim();
    if (!value) return '';
    const nestedFieldPattern = /^(\u6807\u9898|title|headline|subject|\u6587\u6848|copy|body|text|content|\u63cf\u8ff0|description|desc|detail)\s*(?:[:\uff1a]|[-\u2014]\s*|\s+)?(.*)$/i;
    for (let pass = 0; pass < 3; pass += 1) {
      const match = value.match(nestedFieldPattern);
      const nestedKey = match ? edCanonicalCopyField(match[1]) : '';
      if (!nestedKey || (expectedKey && nestedKey !== expectedKey)) break;
      const nextValue = String(match[2] || '').trim();
      if (!nextValue || nextValue === value) break;
      value = nextValue;
    }
    return value;
  }

  function edParseCopywritingBody(raw) {
    const text = String(raw || '')
      .replace(/\r\n?/g, '\n')
      .replace(/\u00A0/g, ' ')
      .trim();
    const fields = { title: '', body: '', description: '' };
    if (!text) return fields;

    const looseLines = [];
    let activeKey = '';
    let hasLabeledField = false;
    const fieldPattern = /^(标题|title|headline|subject|文案|copy|body|text|content|描述|description|desc|detail)\s*(?:[:：]|[-—]\s*|\s+)?(.*)$/i;

    text.split('\n').forEach((rawLine) => {
      const line = String(rawLine || '').trim();
      if (!line) return;
      const match = line.match(fieldPattern);
      const key = match ? edCanonicalCopyField(match[1]) : '';
      if (key) {
        hasLabeledField = true;
        activeKey = key;
        edAppendCopyFieldValue(fields, key, match[2] || '');
        return;
      }
      if (activeKey) {
        edAppendCopyFieldValue(fields, activeKey, line);
        return;
      }
      looseLines.push(line);
    });

    if (!hasLabeledField && looseLines.length) {
      if (looseLines.length === 1) {
        edAppendCopyFieldValue(fields, 'body', looseLines[0]);
      } else if (looseLines.length === 2) {
        edAppendCopyFieldValue(fields, 'title', looseLines[0]);
        edAppendCopyFieldValue(fields, 'body', looseLines[1]);
      } else {
        edAppendCopyFieldValue(fields, 'title', looseLines[0]);
        edAppendCopyFieldValue(fields, 'body', looseLines[1]);
        edAppendCopyFieldValue(fields, 'description', looseLines.slice(2).join(' '));
      }
      return fields;
    }

    if (hasLabeledField && looseLines.length) {
      looseLines.forEach((line) => {
        const fallbackKey = !fields.title ? 'title' : (!fields.body ? 'body' : 'description');
        edAppendCopyFieldValue(fields, fallbackKey, line);
      });
    }
    return fields;
  }

  function edHasMeaningfulCopywritingBody(raw) {
    const parsed = edParseCopywritingBody(raw);
    return !!(parsed.title || parsed.body || parsed.description);
  }

  function edNormalizeCopywritingBody(raw) {
    const text = String(raw || '').trim();
    if (!text) return '';
    const parsed = edParseCopywritingBody(text);
    return [
      `标题: ${parsed.title}`,
      `文案: ${parsed.body}`,
      `描述: ${parsed.description}`,
    ].join('\n');
  }

  function edValidateCopyTranslateSource(rawText) {
    const text = String(rawText || '').replace(/\r\n?/g, '\n').trim();
    if (!text) {
      return { ok: false, message: '\u82f1\u6587\u6587\u6848\u4e3a\u7a7a\uff0c\u65e0\u6cd5\u7ffb\u8bd1' };
    }
    return { ok: true, value: text };
  }

  function edNormalizeCopywritingsData(raw) {
    if (Array.isArray(raw)) {
      return raw.map((item) => ({
        ...item,
        body: edNormalizeCopywritingBody(item && item.body),
      }));
    }
    if (raw && typeof raw === 'object') {
      const normalized = {};
      Object.keys(raw).forEach((lang) => {
        const list = Array.isArray(raw[lang]) ? raw[lang] : [];
        normalized[lang] = list.map((item) => ({
          ...item,
          body: edNormalizeCopywritingBody(item && item.body),
        }));
      });
      return normalized;
    }
    return raw;
  }

  function edSetProductData(data) {
    if (data && typeof data === 'object') {
      data.copywritings = edNormalizeCopywritingsData(data.copywritings);
    }
    edState.current = data;
    edState.productData = data;
  }

  function edGetCopywritingsByLang(lang) {
    const code = String(lang || '').trim().toLowerCase();
    const raw = (edState.productData && edState.productData.copywritings) || [];
    if (Array.isArray(raw)) {
      return raw.filter((item) => (item.lang || '').trim().toLowerCase() === code);
    }
    if (raw && typeof raw === 'object') {
      return Array.isArray(raw[code]) ? raw[code] : [];
    }
    return [];
  }

  function edEnsureCopywritingsArray() {
    if (!edState.productData) return [];
    const raw = edState.productData.copywritings;
    if (Array.isArray(raw)) return raw;
    const arr = [];
    if (raw && typeof raw === 'object') {
      Object.keys(raw).forEach((lang) => {
        const list = Array.isArray(raw[lang]) ? raw[lang] : [];
        list.forEach((item) => arr.push({ ...item, lang }));
      });
    }
    edState.productData.copywritings = arr;
    return arr;
  }

  function edBuildCopywritingsPayload() {
    const rawList = edEnsureCopywritingsArray().filter((item) => (
      item.lang && edHasMeaningfulCopywritingBody(item.body)
    ));
    const cwDict = {};
    rawList.forEach((item) => {
      const body = edNormalizeCopywritingBody(item.body);
      if (!body) return;
      if (!cwDict[item.lang]) cwDict[item.lang] = [];
      cwDict[item.lang].push({ body });
    });
    return cwDict;
  }

  function edCollectProductPayloadBase(options = {}) {
    const {
      flushCopywritings = true,
      flushProductUrl = true,
    } = options;
    const name = $('edName').value.trim();
    const code = $('edCode').value.trim().toLowerCase();
    if (!name) {
      $('edName').focus();
      throw new Error('产品名称必填');
    }
    if (!SLUG_RE.test(code)) {
      $('edCode').focus();
      throw new Error('产品 ID 必填且需合法（小写字母/数字/连字符，3–128）');
    }

    if (flushCopywritings) edFlushCopywritings();
    if (flushProductUrl) edFlushProductUrl();

    const copywritings = edBuildCopywritingsPayload();
    if (!Object.keys(copywritings).length) {
      throw new Error('请填写文案');
    }

    const mkIdRaw = ($('edMkId').value || '').trim();
    if (mkIdRaw && !/^\d{1,8}$/.test(mkIdRaw)) {
      $('edMkId').focus();
      throw new Error('明空 ID 必须是 1-8 位数字');
    }

    const shopifyIdInput = $('edShopifyId');
    const shopifyIdRaw = shopifyIdInput ? (shopifyIdInput.value || '').trim() : '';
    if (shopifyIdRaw && !/^\d{1,32}$/.test(shopifyIdRaw)) {
      if (shopifyIdInput) shopifyIdInput.focus();
      throw new Error('Shopify ID 必须是纯数字');
    }

    const adSupportedLangs = [...document.querySelectorAll(
      '#edAdSupportedLangsBox input[name="ad_supported_langs"]:checked'
    )].map(i => i.value).join(',');

    return {
      pid: edState.productData && edState.productData.product && edState.productData.product.id,
      payload: {
        name,
        product_code: code,
        mk_id: mkIdRaw === '' ? null : parseInt(mkIdRaw, 10),
        shopifyid: shopifyIdRaw,
        copywritings,
        localized_links: edState.productData.product.localized_links || {},
        ad_supported_langs: adSupportedLangs,
      },
    };
  }

  function edCollectProductPayload(options = {}) {
    const name = $('edName').value.trim();
    const code = $('edCode').value.trim().toLowerCase();
    if (!name) return edCollectProductPayloadBase(options);
    const codeError = validateProductCodeForSubmit(code);
    if (codeError) {
      $('edCode').focus();
      throw new Error(codeError);
    }
    return edCollectProductPayloadBase(options);
  }

  function edGetEnglishSourceCopy() {
    const englishList = edGetCopywritingsByLang('en');
    return englishList.find((item) => edHasMeaningfulCopywritingBody(item && item.body)) || null;
  }

  function edRenderCopyTranslateButton() {
    const slot = $('edCwTranslateSlot');
    if (!slot) return;
    const lang = (edState.activeLang || '').trim().toLowerCase();
    if (lang === 'en') {
      let btn = slot.querySelector('#edMkCopyFetchBtn');
      if (!btn) {
        btn = document.createElement('button');
        btn.className = 'oc-btn ghost sm';
        btn.type = 'button';
        btn.id = 'edMkCopyFetchBtn';
        btn.innerHTML = `${icon('search', 14)}<span>一键从名控系统获取文案</span>`;
        btn.addEventListener('click', () => {
          edFillCopywritingFromMkSystem().catch((err) => {
            console.error('[copywriting] fetch from mk system failed:', err);
          });
        });
        slot.replaceChildren(btn);
      }
      return;
    }
    let btn = slot.querySelector('#edCwTranslateBtn');
    if (!btn) {
      btn = document.createElement('button');
      btn.className = 'oc-btn ghost sm';
      btn.type = 'button';
      btn.id = 'edCwTranslateBtn';
      btn.textContent = '一键翻译英文文案';
      btn.addEventListener('click', () => {
        edTranslateEnglishCopywriting().catch((err) => {
          console.error('[copywriting] translate from english failed:', err);
        });
      });
      slot.replaceChildren(btn);
    }
    const source = edGetEnglishSourceCopy();
    btn.disabled = !source;
    btn.title = source ? '读取英文文案并生成当前语种文案' : '当前没有可用的英文文案';
  }

  async function edFillCopywritingFromMkSystem() {
    const lang = (edState.activeLang || '').trim().toLowerCase();
    if (lang !== 'en') return;

    const nameInput = $('edName');
    const codeInput = $('edCode');
    const btn = $('edMkCopyFetchBtn');
    const label = btn ? btn.querySelector('span') : null;
    const originalLabel = label ? label.textContent : '';
    const name = nameInput ? nameInput.value.trim() : '';
    const rawCode = codeInput ? codeInput.value.trim() : '';
    const normalizedCode = rawCode.toLowerCase();

    if (!name) {
      alert('请先填写产品名称');
      if (nameInput) nameInput.focus();
      return;
    }
    if (!SLUG_RE.test(normalizedCode)) {
      alert('请先填写合法的产品 ID（小写字母/数字/连字符，3–128）');
      if (codeInput) codeInput.focus();
      return;
    }
    if (!edState.productData) return;

    if (btn) btn.disabled = true;
    if (label) label.textContent = '获取中...';
    try {
      edFlushCopywritings();
      const copywriting = edNormalizeCopywritingBody(await requestMkCopywriting(rawCode));
      const hasExisting = edGetCopywritingsByLang('en').some((item) => edHasMeaningfulCopywritingBody(item && item.body));
      if (hasExisting && !confirm('当前文案不为空，是否用明空文案覆盖？')) {
        return;
      }
      const nextCopywritings = edEnsureCopywritingsArray().filter(
        (item) => (item.lang || '').trim().toLowerCase() !== 'en'
      );
      nextCopywritings.push({ lang: 'en', body: copywriting });
      edState.productData.copywritings = nextCopywritings;
      edRenderLangTabs();
      edRenderCopyBlock('en');
      const textarea = $('edCwList') && $('edCwList').querySelector('[data-field="body"]');
      if (textarea) textarea.focus();
    } catch (e) {
      alertMkCopywritingFetchError(e);
    } finally {
      if (btn) btn.disabled = false;
      if (label) label.textContent = originalLabel || '一键从名控系统获取文案';
    }
  }

  async function edTranslateEnglishCopywriting() {
    const btn = $('edCwTranslateBtn');
    const targetLang = (edState.activeLang || '').trim().toLowerCase();
    if (!targetLang || targetLang === 'en') return;

    const source = edGetEnglishSourceCopy();
    if (!source) {
      alert('当前没有可用的英文文案');
      return;
    }

    const sourceValidation = edValidateCopyTranslateSource(source.body);
    if (!sourceValidation.ok) {
      alert(sourceValidation.message);
      return;
    }

    const originalLabel = btn ? btn.textContent.trim() : '';
    if (btn) {
      btn.disabled = true;
      btn.textContent = '翻译中...';
    }

    try {
      edFlushCopywritings();
      const response = await fetchJSON('/api/title-translate/translate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          language: targetLang,
          source_text: sourceValidation.value,
        }),
      });
      const translatedBody = edNormalizeCopywritingBody(response.result || '');

      const copies = edEnsureCopywritingsArray();
      copies.push({ lang: targetLang, body: translatedBody });

      const { pid, payload } = edCollectProductPayload({ flushCopywritings: false });
      await fetchJSON('/medias/api/products/' + pid, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const full = await fetchJSON('/medias/api/products/' + pid);
      edSetProductData(full);
      edRenderLangTabs();
      await edRenderActiveLangView();
      loadList();
    } catch (e) {
      alert('一键翻译英文文案失败：' + (e.message || e));
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.textContent = originalLabel || '一键翻译英文文案';
      }
      edRenderCopyTranslateButton();
    }
  }

  async function openEditDetail(pid) {
    try {
      await ensureLanguages();
      edStopLinkCheckPoll();
      edCloseLinkCheckModal();
      const data = await fetchJSON('/medias/api/products/' + pid);
      edSetProductData(data);
      edState.activeLang = 'en';
      edState.linkCheckDetailTask = null;
      edState.linkCheckDetailError = '';
      $('edName').value = data.product.name || '';
      $('edCode').value = data.product.product_code || '';
      $('edMkId').value = (data.product.mk_id === null || data.product.mk_id === undefined)
        ? '' : String(data.product.mk_id);
      const shopifyIdText = (data.product.shopifyid === null || data.product.shopifyid === undefined)
        ? '' : String(data.product.shopifyid);
      if ($('edShopifyId')) {
        $('edShopifyId').value = shopifyIdText;
      }
      edRenderAdSupportedLangs(data.product.ad_supported_langs || '');
      $('edUploadProgress').innerHTML = '';
      edResetNewItemForm();
      edShow();
      edRenderLangTabs();
      await edRenderActiveLangView();
    } catch (e) {
      alert('加载失败：' + (e.message || e));
    }
  }

  // --- 语种 tallies（用于 badge） ---
  function edLangTallies(lang) {
    const d = edState.productData;
    if (!d) return { items: 0, copy: 0, cover: false };
    const items = (d.items || []).filter(it => it.lang === lang).length;
    const copyList = d.copywritings;
    let copy = 0;
    if (Array.isArray(copyList)) {
      copy = copyList.filter(c => c.lang === lang).length;
    } else if (copyList && typeof copyList === 'object') {
      copy = (copyList[lang] || []).length;
    }
    const cover = !!(d.covers && d.covers[lang]);
    return { items, copy, cover };
  }

  function edRenderLangTabs() {
    const box = $('edLangTabs');
    if (!box) return;
    box.innerHTML = LANGUAGES.map(l => {
      const t = edLangTallies(l.code);
      // badge: 视频数 0 → 红色；>0 → 绿色；所有语种统一显示
      const badgeCls = t.items > 0 ? 'badge has' : 'badge';
      const badgeHtml = `<span class="${badgeCls}">${t.items}</span>`;
      const active = edState.activeLang === l.code ? ' active' : '';
      return `<button class="oc-lang-tab${active}" data-lang="${escapeHtml(l.code)}" title="${escapeHtml(langDisplayName(l.code))}">`
           + `${langDisplayName(l.code)}${badgeHtml}`
           + `</button>`;
    }).join('');
    box.querySelectorAll('[data-lang]').forEach(btn => {
      btn.addEventListener('click', () => edSwitchLang(btn.dataset.lang));
    });
  }

  function edSwitchLang(lang) {
    // 切换前保存当前语种文案到 productData（从 DOM 读取）
    edFlushCopywritings();
    edFlushProductUrl();
    edStopLinkCheckPoll();
    if (edState.linkCheckModalLang && edState.linkCheckModalLang !== lang) {
      edCloseLinkCheckModal();
    }
    edState.activeLang = lang;
    // 切语言时重置"新增素材"大框的待上传状态
    edResetNewItemForm();
    edRenderLangTabs();
    edRenderActiveLangView();
  }

  // --- 产品链接（按 activeLang）---
  function _defaultProductUrl(lang, code) {
    if (!code) return '';
    if (lang === 'en') return `https://newjoyloo.com/products/${code}`;
    return `https://newjoyloo.com/${lang}/products/${code}`;
  }

  function edRenderProductUrl(lang) {
    const input = $('edProductUrl');
    const hint = $('edProductUrlHint');
    if (!input) return;
    const code = ($('edCode').value || '').trim();
    const links = (edState.productData && edState.productData.product
                   && edState.productData.product.localized_links) || {};
    const override = links[lang];
    const def = _defaultProductUrl(lang, code);
    input.value = override || def || '';
    input.placeholder = def || '留空则用默认模板';
    if (hint) {
      const label = langDisplayName(lang);
      hint.textContent = override
        ? `（${label} · 已自定义）`
        : `（${label} · 使用默认：${def || '未设置产品 ID'}）`;
    }
  }

  function edFlushProductUrl() {
    const input = $('edProductUrl');
    if (!input || !edState.productData || !edState.productData.product) return;
    const lang = edState.activeLang;
    const code = ($('edCode').value || '').trim();
    const def = _defaultProductUrl(lang, code);
    const val = (input.value || '').trim();
    if (!edState.productData.product.localized_links) {
      edState.productData.product.localized_links = {};
    }
    const links = edState.productData.product.localized_links;
    // 如果用户输入的就是默认值或留空 → 不保存（避免冗余写入）
    if (!val || val === def) delete links[lang];
    else links[lang] = val;
  }

  function edDetailTranslateStatusLabel(status) {
    switch ((status || '').toLowerCase()) {
      case 'done': return '已完成';
      case 'running': return '进行中';
      case 'queued': return '排队中';
      case 'failed': return '已失败';
      default: return status || '待处理';
    }
  }

  function edDetailTranslateApplyLabel(status) {
    switch ((status || '').toLowerCase()) {
      case 'applied': return '已回填';
      case 'applied_partial': return '部分回填';
      case 'skipped_failed': return '未回填（有失败）';
      case 'apply_error': return '回填失败';
      case 'pending': return '待回填';
      default: return status || '待回填';
    }
  }

  async function edLoadDetailTranslateTasks(pid, lang) {
    if (!pid || !lang || lang === 'en') {
      edState.detailTranslateTasks[lang] = [];
      return [];
    }
    const data = await fetchJSON(`/medias/api/products/${pid}/detail-image-translate-tasks?lang=${encodeURIComponent(lang)}`);
    const tasks = Array.isArray(data.items) ? data.items : [];
    edState.detailTranslateTasks[lang] = tasks;
    return tasks;
  }

  function edRenderDetailTranslateHistory(tasks) {
    const wrap = $('edDetailTranslateHistoryWrap');
    const box = $('edDetailTranslateHistory');
    if (!wrap || !box) return;
    if (edState.activeLang === 'en') {
      wrap.hidden = true;
      box.innerHTML = '';
      return;
    }
    wrap.hidden = false;
    if (!tasks.length) {
      box.innerHTML = '<div class="oc-hint" style="padding:10px 12px;border:1px dashed var(--oc-border);border-radius:10px;">暂无翻译任务记录</div>';
      return;
    }
    box.innerHTML = tasks.map(task => {
      const progress = task.progress || {};
      const detailUrl = escapeHtml(task.detail_url || `/image-translate/${task.task_id}`);
      const taskId = escapeHtml(task.task_id || '');
      const status = escapeHtml(edDetailTranslateStatusLabel(task.status));
      const applyStatus = escapeHtml(edDetailTranslateApplyLabel(task.apply_status));
      const updatedAt = escapeHtml(fmtDate(task.updated_at || task.created_at || ''));
      const progressText = `${progress.done || 0}/${progress.total || 0}`;
      return `
        <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px;padding:12px;border:1px solid var(--oc-border);border-radius:10px;background:var(--oc-bg-subtle);margin-top:8px;">
          <div style="display:grid;gap:4px;min-width:0;">
            <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
              <strong style="color:var(--oc-fg);">任务 ${taskId || '-'}</strong>
              <span class="oc-hint">状态：${status}</span>
              <span class="oc-hint">回填：${applyStatus}</span>
              <span class="oc-hint">进度：${progressText}</span>
            </div>
            <div class="oc-hint">更新时间：${updatedAt || '-'}</div>
          </div>
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;justify-content:flex-end;">
            <a class="oc-btn ghost sm" href="${detailUrl}" target="_blank" rel="noopener">查看详情</a>
            <button type="button" class="oc-btn ghost sm" data-retranslate-lang="${escapeHtml(edState.activeLang)}">重新翻译</button>
          </div>
        </div>
      `;
    }).join('');
  }

  const LINK_CHECK_STATUS_LABELS = {
    queued: '排队中',
    locking_locale: '锁定目标语种页面',
    downloading: '下载图片中',
    analyzing: '分析图片中',
    review_ready: '待复核',
    done: '已完成',
    failed: '失败',
  };

  const LINK_CHECK_OVERALL_LABELS = {
    running: '检测中',
    done: '通过',
    unfinished: '需复核',
  };

  const LINK_CHECK_DECISION_LABELS = {
    pass: '通过',
    replace: '需替换',
    review: '待复核',
    no_text: '无文字',
    failed: '失败',
  };

  const LINK_CHECK_REFERENCE_LABELS = {
    matched: '已匹配参考图',
    weak_match: '弱匹配',
    not_matched: '未匹配',
    not_provided: '未提供参考图',
  };

  const LINK_CHECK_BINARY_LABELS = {
    pass: '快检通过',
    fail: '快检不通过',
    skipped: '未执行快检',
    error: '快检失败',
  };

  const LINK_CHECK_SAME_IMAGE_LABELS = {
    done: '已完成同图判断',
    skipped: '未执行同图判断',
    error: '同图判断失败',
  };

  function edLinkCheckTasks() {
    if (!edState.productData || !edState.productData.product) return {};
    if (!edState.productData.product.link_check_tasks || typeof edState.productData.product.link_check_tasks !== 'object') {
      edState.productData.product.link_check_tasks = {};
    }
    return edState.productData.product.link_check_tasks;
  }

  function edGetLinkCheckTask(lang) {
    if (!lang) return null;
    return edLinkCheckTasks()[lang] || null;
  }

  function edSetLinkCheckTask(lang, task) {
    if (!lang || !task || !edState.productData || !edState.productData.product) return null;
    const tasks = edLinkCheckTasks();
    tasks[lang] = { ...(tasks[lang] || {}), ...task };
    return tasks[lang];
  }

  function edReadVisibleProductUrl() {
    const input = $('edProductUrl');
    return input ? (input.value || '').trim() : '';
  }

  function edCurrentLinkUrl(lang) {
    const code = ($('edCode') && $('edCode').value || '').trim();
    const links = (edState.productData && edState.productData.product && edState.productData.product.localized_links) || {};
    if (lang === edState.activeLang) {
      const input = $('edProductUrl');
      const current = input ? (input.value || '').trim() : '';
      if (current) return current;
    }
    return links[lang] || _defaultProductUrl(lang, code) || '';
  }

  function copyText(text) {
    const value = String(text || '').trim();
    if (!value) return Promise.reject(new Error('empty'));
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(value);
    }
    return new Promise((resolve, reject) => {
      const ta = document.createElement('textarea');
      ta.value = value;
      ta.setAttribute('readonly', 'readonly');
      ta.style.position = 'fixed';
      ta.style.top = '-9999px';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.focus();
      ta.select();
      try {
        const ok = document.execCommand('copy');
        document.body.removeChild(ta);
        if (!ok) throw new Error('copy failed');
        resolve();
      } catch (err) {
        document.body.removeChild(ta);
        reject(err);
      }
    });
  }

  function flashCopiedButton(btn) {
    if (!btn) return;
    const original = btn.dataset.copyLabel || btn.textContent.trim() || '复制';
    const originalHtml = btn.dataset.copyHtml || btn.innerHTML;
    btn.dataset.copyLabel = original;
    btn.dataset.copyHtml = originalHtml;
    if (btn._copyTimer) window.clearTimeout(btn._copyTimer);
    btn.textContent = '已复制';
    btn.disabled = true;
    btn._copyTimer = window.setTimeout(() => {
      btn.innerHTML = btn.dataset.copyHtml || original;
      btn.disabled = false;
    }, 1200);
  }

  function copyProductCode(btn) {
    const code = btn && btn.dataset ? (btn.dataset.productCode || '').trim() : '';
    if (!code) return;
    copyText(code)
      .then(() => flashCopiedButton(btn))
      .catch(() => alert('复制失败，请手动复制'));
  }

  function edCopyProductId(btn) {
    const code = ($('edCode') && $('edCode').value || '').trim();
    if (!code) {
      alert('请先填写产品 ID');
      $('edCode') && $('edCode').focus();
      return;
    }
    copyText(code)
      .then(() => flashCopiedButton(btn))
      .catch(() => alert('复制失败，请手动复制'));
  }

  function edCopyLocalizedProductUrl(btn) {
    const url = edReadVisibleProductUrl();
    if (!url || !/^https?:\/\//i.test(url)) {
      alert('请先填写有效的商品链接');
      $('edProductUrl') && $('edProductUrl').focus();
      return;
    }
    copyText(url)
      .then(() => flashCopiedButton(btn))
      .catch(() => alert('复制失败，请手动复制'));
  }

  function edLinkCheckNeedsPolling(task) {
    if (!task || !task.status) return false;
    return !['done', 'review_ready', 'failed'].includes(task.status);
  }

  function edLinkCheckStatusKind(task) {
    if (!task) return 'info';
    if (task.status === 'failed') return 'danger';
    if (task.status === 'review_ready' || (task.summary || {}).overall_decision === 'unfinished') return 'warning';
    if (task.status === 'done') return 'success';
    return 'info';
  }

  function edLinkCheckStatusText(task) {
    if (!task) return '未检测';
    const summary = task.summary || {};
    if (task.status === 'done' && summary.overall_decision) {
      return LINK_CHECK_OVERALL_LABELS[summary.overall_decision] || LINK_CHECK_STATUS_LABELS[task.status] || task.status;
    }
    return LINK_CHECK_STATUS_LABELS[task.status] || LINK_CHECK_OVERALL_LABELS[summary.overall_decision] || task.status || '未检测';
  }

  function edLinkCheckDecisionText(decision, status) {
    if (status === 'failed') return LINK_CHECK_DECISION_LABELS.failed;
    return LINK_CHECK_DECISION_LABELS[decision] || '待复核';
  }

  function edLinkCheckDecisionKind(decision, status) {
    if (status === 'failed' || decision === 'replace') return 'danger';
    if (decision === 'pass' || decision === 'no_text') return 'success';
    return 'warning';
  }

  function edLinkCheckReferenceText(reference) {
    const status = (reference || {}).status || 'not_provided';
    if (status === 'matched' && reference.reference_filename) {
      return reference.reference_filename;
    }
    return LINK_CHECK_REFERENCE_LABELS[status] || status;
  }

  function edLinkCheckBinaryText(binary) {
    const status = (binary || {}).status || 'skipped';
    return LINK_CHECK_BINARY_LABELS[status] || status;
  }

  function edLinkCheckSameImageText(sameImage) {
    const status = (sameImage || {}).status || 'skipped';
    if (status === 'done' && sameImage.answer) return sameImage.answer;
    return LINK_CHECK_SAME_IMAGE_LABELS[status] || status;
  }

  function edLinkCheckBadge(label, kind) {
    return `<span class="oc-link-check-badge ${kind || 'info'}">${escapeHtml(label)}</span>`;
  }

  const SHOPIFY_IMAGE_REPLACE_LABELS = {
    none: '未替换',
    pending: '已排队',
    running: '替换中',
    auto_done: '自动替换完成',
    failed: '替换失败',
    confirmed: '人工确认完成',
  };

  const SHOPIFY_IMAGE_LINK_LABELS = {
    unknown: '链接未确认',
    needs_review: '待人工确认',
    normal: '链接正常',
    unavailable: '链接不可用',
  };

  function edShopifyImageStatusMap() {
    if (!edState.productData || !edState.productData.product) return {};
    if (!edState.productData.product.shopify_image_status || typeof edState.productData.product.shopify_image_status !== 'object') {
      edState.productData.product.shopify_image_status = {};
    }
    return edState.productData.product.shopify_image_status;
  }

  function edShopifyImageStatusForLang(lang) {
    const raw = edShopifyImageStatusMap()[lang] || {};
    return {
      replace_status: raw.replace_status || 'none',
      link_status: raw.link_status || 'unknown',
      last_error: raw.last_error || '',
      last_task_id: raw.last_task_id || '',
      updated_at: raw.updated_at || '',
      confirmed_at: raw.confirmed_at || '',
      result_summary: raw.result_summary || {},
    };
  }

  const SHOPIFY_IMAGE_ACTION_ENDPOINTS = {
    confirm: (pid, lang) => `/medias/api/products/${pid}/shopify-image/${encodeURIComponent(lang)}/confirm`,
    unavailable: (pid, lang) => `/medias/api/products/${pid}/shopify-image/${encodeURIComponent(lang)}/unavailable`,
    requeue: (pid, lang) => `/medias/api/products/${pid}/shopify-image/${encodeURIComponent(lang)}/requeue`,
  };

  function edShopifyImageBadgeKind(status) {
    if (!status) return 'info';
    if (status.link_status === 'unavailable' || status.replace_status === 'failed') return 'danger';
    if (status.replace_status === 'confirmed' && status.link_status === 'normal') return 'success';
    if (status.replace_status === 'auto_done' || status.link_status === 'needs_review') return 'warning';
    return 'info';
  }

  function edRenderShopifyImageStatus(lang) {
    const box = $('edShopifyImageStatus');
    if (!box) return;
    if (!lang || lang === 'en') {
      box.hidden = true;
      box.innerHTML = '';
      return;
    }

    const status = edShopifyImageStatusForLang(lang);
    const summary = status.result_summary || {};
    const parts = [
      edLinkCheckBadge(SHOPIFY_IMAGE_REPLACE_LABELS[status.replace_status] || status.replace_status, edShopifyImageBadgeKind(status)),
      edLinkCheckBadge(SHOPIFY_IMAGE_LINK_LABELS[status.link_status] || status.link_status, edShopifyImageBadgeKind(status)),
    ];
    if (status.last_task_id) {
      parts.push(`<span class="oc-link-check-meta">任务 #${escapeHtml(status.last_task_id)}</span>`);
    }
    if (typeof summary.carousel_ok === 'number') {
      parts.push(`<span class="oc-link-check-meta">轮播 ${escapeHtml(summary.carousel_ok)}/${escapeHtml(summary.carousel_requested || 0)}</span>`);
    }
    if (typeof summary.detail_replacement_count === 'number') {
      parts.push(`<span class="oc-link-check-meta">详情 ${escapeHtml(summary.detail_replacement_count)}</span>`);
    }
    if (status.updated_at) {
      parts.push(`<span class="oc-link-check-meta">更新 ${escapeHtml(fmtDate(status.updated_at))}</span>`);
    }
    if (status.last_error) {
      parts.push(edLinkCheckBadge(status.last_error, 'danger'));
    }

    const actions = [];
    if (status.replace_status !== 'confirmed' || status.link_status !== 'normal') {
      actions.push(`<button type="button" class="oc-btn primary sm" data-shopify-image-action="confirm" data-lang="${escapeHtml(lang)}">确认图片正常</button>`);
    }
    actions.push(`<button type="button" class="oc-btn ghost sm" data-shopify-image-action="requeue" data-lang="${escapeHtml(lang)}">重新排队换图</button>`);
    if (status.link_status !== 'unavailable') {
      actions.push(`<button type="button" class="oc-btn text sm" data-shopify-image-action="unavailable" data-lang="${escapeHtml(lang)}">标记链接不可用</button>`);
    }

    box.hidden = false;
    box.innerHTML = parts.join('') + `<span class="oc-link-check-actions">${actions.join('')}</span>`;
    box.querySelectorAll('[data-shopify-image-action]').forEach((btn) => {
      btn.addEventListener('click', () => {
        edApplyShopifyImageAction(btn.dataset.shopifyImageAction, btn.dataset.lang || lang).catch((err) => {
          alert('图片换图状态更新失败：' + (err.message || err));
        });
      });
    });
  }

  async function edApplyShopifyImageAction(action, lang) {
    const pid = edState.productData && edState.productData.product && edState.productData.product.id;
    if (!pid || !lang || lang === 'en') return;
    let body = null;
    if (action === 'unavailable') {
      const reason = prompt('请填写链接不可用原因', '链接不可用，等待负责人处理');
      if (reason === null) return;
      body = { reason };
    }
    if (action === 'requeue' && !confirm('确认重新排队执行该语种的轮播图和详情图替换？')) {
      return;
    }
    const endpoint = SHOPIFY_IMAGE_ACTION_ENDPOINTS[action];
    if (!endpoint) return;
    const resp = await fetchJSON(endpoint(pid, lang), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (resp.status) {
      edShopifyImageStatusMap()[lang] = resp.status;
    }
    const fresh = await fetchJSON('/medias/api/products/' + pid);
    edSetProductData(fresh);
    edRenderLangTabs();
    await edRenderActiveLangView();
    loadList();
  }

  function edLinkCheckPercent(task) {
    const progress = (task && task.progress) || {};
    const total = progress.total || 0;
    if (total > 0) {
      const finished = Math.max(progress.analyzed || 0, progress.downloaded || 0);
      return Math.max(8, Math.min(100, Math.round((finished / total) * 100)));
    }
    if (!task) return 0;
    if (task.status === 'queued') return 5;
    if (task.status === 'locking_locale') return 12;
    if (task.status === 'downloading') return 35;
    if (task.status === 'analyzing') return 72;
    if (task.status === 'review_ready' || task.status === 'done') return 100;
    return 0;
  }

  function edRenderLinkCheckSummary(task) {
    const box = $('edLinkCheckSummary');
    const viewBtn = $('edLinkCheckViewBtn');
    if (!box || !viewBtn) return;
    if (!task) {
      viewBtn.hidden = true;
      box.hidden = true;
      box.innerHTML = '';
      return;
    }

    box.hidden = false;
    const summary = task.summary || {};
    const currentUrl = edCurrentLinkUrl(edState.activeLang);
    const urlChanged = currentUrl && task.link_url && currentUrl !== task.link_url;
    const parts = [
      edLinkCheckBadge(edLinkCheckStatusText(task), edLinkCheckStatusKind(task)),
    ];
    if (summary.overall_decision) {
      parts.push(edLinkCheckBadge(
        LINK_CHECK_OVERALL_LABELS[summary.overall_decision] || summary.overall_decision,
        summary.overall_decision === 'done' ? 'success' : (summary.overall_decision === 'unfinished' ? 'warning' : 'info'),
      ));
    }
    if (typeof summary.pass_count === 'number') {
      parts.push(`<span class="oc-link-check-meta">通过 ${summary.pass_count}</span>`);
    }
    if (typeof summary.replace_count === 'number') {
      parts.push(`<span class="oc-link-check-meta">替换 ${summary.replace_count}</span>`);
    }
    if (typeof summary.review_count === 'number') {
      parts.push(`<span class="oc-link-check-meta">复核 ${summary.review_count}</span>`);
    }
    if (task.checked_at) {
      parts.push(`<span class="oc-link-check-meta">最近检测 ${escapeHtml(fmtDate(task.checked_at))}</span>`);
    }
    if (task.link_url) {
      parts.push(`<span class="oc-link-check-meta mono">${escapeHtml(task.link_url)}</span>`);
    }
    if (urlChanged) {
      parts.push(edLinkCheckBadge('链接已变更', 'warning'));
    }

    box.innerHTML = parts.join('');
    viewBtn.hidden = !task.task_id;
    viewBtn.textContent = edLinkCheckNeedsPolling(task) ? '查看进度' : '查看结果';
  }

  function edStopLinkCheckPoll() {
    if (edState.linkCheckPollTimer) {
      clearTimeout(edState.linkCheckPollTimer);
      edState.linkCheckPollTimer = null;
    }
  }

  function edRenderLinkCheckModal() {
    const summaryBox = $('edLinkCheckModalSummary');
    const refsBox = $('edLinkCheckRefs');
    const itemsBox = $('edLinkCheckItems');
    if (!summaryBox || !refsBox || !itemsBox) return;

    const lang = edState.linkCheckModalLang || edState.activeLang;
    const summaryTask = edGetLinkCheckTask(lang);
    const detailTask = edState.linkCheckDetailTask;
    const task = { ...(summaryTask || {}), ...(detailTask || {}) };

    if (!task || (!task.task_id && !task.id)) {
      summaryBox.innerHTML = '<div class="oc-detail-images-empty">当前语种还没有链接检测任务</div>';
      refsBox.innerHTML = '<div class="oc-detail-images-empty">暂无参考图</div>';
      itemsBox.innerHTML = '<div class="oc-detail-images-empty">还没有检测结果</div>';
      $('edLinkCheckRefsBadge').textContent = '0';
      $('edLinkCheckItemsBadge').textContent = '0';
      return;
    }

    const summary = task.summary || {};
    const progress = task.progress || {};
    const summaryCards = [
      ['当前状态', edLinkCheckStatusText(task), false],
      ['整体结论', LINK_CHECK_OVERALL_LABELS[summary.overall_decision] || '-', false],
      ['已分析图片', `${progress.analyzed ?? 0} / ${progress.total ?? 0}`, false],
      ['参考图匹配', String(summary.reference_matched_count ?? 0), false],
      ['通过', String(summary.pass_count ?? 0), false],
      ['需替换', String(summary.replace_count ?? 0), false],
      ['待复核', String(summary.review_count ?? 0), false],
      ['最终链接', task.resolved_url || task.link_url || '-', true],
    ];
    summaryBox.innerHTML = summaryCards.map(([label, value, mono]) => `
      <div class="oc-link-check-card">
        <span class="oc-link-check-card-title">${escapeHtml(label)}</span>
        <span class="oc-link-check-card-value${mono ? ' mono' : ''}">${escapeHtml(value)}</span>
      </div>
    `).join('');

    const references = Array.isArray(task.reference_images) ? task.reference_images : [];
    $('edLinkCheckRefsBadge').textContent = String(references.length);
    refsBox.innerHTML = references.length
      ? references.map(ref => `
          <div class="oc-link-check-ref">
            <img src="${escapeHtml(ref.preview_url || '')}" alt="${escapeHtml(ref.filename || '参考图')}" loading="lazy">
            <span title="${escapeHtml(ref.filename || '')}">${escapeHtml(ref.filename || '')}</span>
          </div>
        `).join('')
      : '<div class="oc-detail-images-empty">暂无参考图</div>';

    const items = Array.isArray(task.items) ? task.items : [];
    $('edLinkCheckItemsBadge').textContent = String(items.length);
    if (!items.length) {
      const placeholder = edLinkCheckNeedsPolling(summaryTask)
        ? `链接检测进行中，当前进度 ${edLinkCheckPercent(summaryTask)}%`
        : (edState.linkCheckDetailError || '还没有检测结果');
      itemsBox.innerHTML = `<div class="oc-detail-images-empty">${escapeHtml(placeholder)}</div>`;
      return;
    }

    itemsBox.innerHTML = items.map((item, idx) => {
      const analysis = item.analysis || {};
      const reference = item.reference_match || {};
      const binary = item.binary_quick_check || {};
      const sameImage = item.same_image_llm || {};
      const decision = analysis.decision || '';
      const reason = analysis.quality_reason || analysis.text_summary || item.error || binary.reason || sameImage.reason || '暂无说明';
      const itemLabel = item.kind === 'hero' ? '轮播图' : '详情图';
      const preview = item.site_preview_url
        ? `<img src="${escapeHtml(item.site_preview_url)}" alt="${escapeHtml(itemLabel)}" loading="lazy">`
        : `<div class="oc-detail-images-empty" style="height:100%;margin:0;">暂无预览</div>`;
      return `
        <article class="oc-link-check-item">
          <div class="oc-link-check-item-preview">${preview}</div>
          <div class="oc-link-check-item-body">
            <div class="oc-link-check-item-head">
              <div class="oc-link-check-item-title">${escapeHtml(itemLabel)} #${idx + 1}</div>
              <div class="oc-link-check-item-badges">
                ${edLinkCheckBadge(edLinkCheckDecisionText(decision, item.status), edLinkCheckDecisionKind(decision, item.status))}
                ${edLinkCheckBadge(edLinkCheckReferenceText(reference), reference.status === 'matched' ? 'success' : (reference.status === 'not_matched' ? 'warning' : 'info'))}
              </div>
            </div>
            <div class="oc-link-check-item-url">${escapeHtml(item.source_url || '-')}</div>
            <div class="oc-link-check-item-meta">
              <span><strong>识别语种：</strong>${escapeHtml(langDisplayName(analysis.detected_language || '-'))}</span>
              <span><strong>页面语种：</strong>${escapeHtml(langDisplayName(task.page_language || '-'))}</span>
              <span><strong>二值快检：</strong>${escapeHtml(edLinkCheckBinaryText(binary))}</span>
              <span><strong>同图判断：</strong>${escapeHtml(edLinkCheckSameImageText(sameImage))}</span>
            </div>
            <div class="oc-link-check-item-text">${escapeHtml(reason)}</div>
          </div>
        </article>
      `;
    }).join('');
  }

  function edRenderDetailTranslateState(lang, tasks, detailItems) {
    const section = $('edDetailImagesSection');
    const status = $('edDetailTranslateStatus');
    const translateBtn = $('edDetailImagesTranslateBtn');
    const fromUrlBtn = $('edDetailImagesFromUrlBtn');
    const headerTranslateBtn = $('edDetailImagesTranslateHeaderBtn');
    const headerClearBtn = $('edDetailImagesClearAllBtn');
    const title = section && section.querySelector('.oc-section-title > span');
    const subtitle = section && section.querySelector('.oc-section-title .optional');
    const langName = langDisplayName(lang);
    if (section) section.hidden = false;
    if (title) title.textContent = '商品详情图';
    if (subtitle) {
      subtitle.textContent = lang === 'en'
        ? '英文原始版，用于后续图片翻译'
        : `${langName} 版本，可自行上传，或从英语版一键翻译`;
    }
    if (translateBtn) translateBtn.hidden = lang === 'en';
    if (fromUrlBtn) fromUrlBtn.hidden = lang !== 'en';
    if (headerTranslateBtn) headerTranslateBtn.hidden = lang === 'en';
    if (headerClearBtn) {
      const hasItems = Array.isArray(detailItems) && detailItems.length > 0;
      headerClearBtn.hidden = lang === 'en';
      headerClearBtn.disabled = !hasItems;
    }
    if (!status) return;
    if (lang === 'en') {
      status.hidden = true;
      return;
    }

    const items = Array.isArray(detailItems) ? detailItems : [];
    const appliedImage = items.find(item => item && item.origin_type === 'image_translate');
    const appliedTaskId = appliedImage && appliedImage.image_translate_task_id ? String(appliedImage.image_translate_task_id) : '';
    const appliedTask = appliedTaskId
      ? (tasks.find(task => String(task.task_id || '') === appliedTaskId) || { task_id: appliedTaskId, detail_url: `/image-translate/${encodeURIComponent(appliedTaskId)}` })
      : null;
    const latest = tasks[0] || null;
    let html = '';
    if (appliedTask) {
      const appliedLabel = escapeHtml(edDetailTranslateApplyLabel(appliedTask.apply_status || 'applied'));
      const detailUrl = escapeHtml(appliedTask.detail_url || `/image-translate/${appliedTask.task_id}`);
      html = `当前 ${escapeHtml(langName)} 详情图已由英语版一键翻译回填（${appliedLabel}）。<a href="${detailUrl}" target="_blank" rel="noopener">查看关联任务</a>`;
    } else if (latest) {
      const detailUrl = escapeHtml(latest.detail_url || `/image-translate/${latest.task_id}`);
      html = `最近一次翻译任务：${escapeHtml(edDetailTranslateStatusLabel(latest.status))} / ${escapeHtml(edDetailTranslateApplyLabel(latest.apply_status))}。<a href="${detailUrl}" target="_blank" rel="noopener">查看任务详情</a>`;
    } else {
      html = `当前 ${escapeHtml(langName)} 还没有执行过从英语版一键翻译。`;
    }

    // 当最近一次任务已结束但尚未回填，且存在成功项时，显示"手动回填已成功项"按钮。
    // 仅以 latest 为目标，避免误把过期任务的图覆盖上来。
    const candidate = latest && !appliedTask ? latest : null;
    if (candidate) {
      const applyStatus = String(candidate.apply_status || '').toLowerCase();
      const status_ = String(candidate.status || '').toLowerCase();
      const progress = candidate.progress || {};
      const doneCount = Number(progress.done || 0);
      const totalCount = Number(progress.total || 0);
      const failedCount = Number(progress.failed || 0);
      const canApply =
        (status_ === 'done' || status_ === 'error')
        && applyStatus !== 'applied'
        && applyStatus !== 'applied_partial'
        && doneCount > 0;
      if (canApply) {
        const btnLabel = failedCount > 0
          ? `手动回填已成功项（${doneCount}/${totalCount}，忽略 ${failedCount} 张失败）`
          : `手动回填已成功项（${doneCount}/${totalCount}）`;
        html += ` <button type="button" class="oc-btn primary sm" data-apply-translate-task="${escapeHtml(candidate.task_id)}" data-apply-translate-lang="${escapeHtml(lang)}">${escapeHtml(btnLabel)}</button>`;
      }
    }

    status.hidden = false;
    status.innerHTML = html;
  }

  async function edRefreshDetailImagesPanel(lang) {
    const ctrl = ensureEdDetailImagesCtrl();
    const pid = edState.productData && edState.productData.product && edState.productData.product.id;
    ctrl.show();
    if (!pid) {
      ctrl.reset();
      edRenderDetailTranslateState(lang, [], []);
      edRenderDetailTranslateHistory([]);
      return;
    }
    await ctrl.load(pid);
    let tasks = [];
    let loadError = null;
    try {
      tasks = await edLoadDetailTranslateTasks(pid, lang);
    } catch (err) {
      loadError = err;
    }
    const detailItems = ctrl.items ? ctrl.items() : [];
    edRenderDetailTranslateState(lang, tasks, detailItems);
    edRenderDetailTranslateHistory(tasks);
    if (loadError) {
      const status = $('edDetailTranslateStatus');
      if (status && lang !== 'en') {
        status.hidden = false;
        status.textContent = '翻译任务记录加载失败：' + (loadError.message || loadError);
      }
    }
  }

  function edOpenDetailTranslateTaskModal(langOverride) {
    const mask = $('edDetailTranslateTaskMask');
    if (!mask) return;
    const config = $('edDetailTranslateTaskConfig');
    const result = $('edDetailTranslateTaskResult');
    if (config) config.hidden = false;
    if (result) result.hidden = true;
    const group = $('edDetailTranslateModeGroup');
    if (group) {
      group.querySelectorAll('.oc-chip').forEach(ch => {
        const active = ch.dataset.mode === 'parallel';
        ch.classList.toggle('on', active);
        ch.setAttribute('aria-checked', active ? 'true' : 'false');
      });
    }
    mask.dataset.lang = (langOverride || edState.activeLang || '').trim().toLowerCase();
    mask.hidden = false;
  }

  function edCloseDetailTranslateTaskModal() {
    const mask = $('edDetailTranslateTaskMask');
    if (mask) mask.hidden = true;
  }

  async function edStartDetailTranslate(langOverride) {
    const pid = edState.productData && edState.productData.product && edState.productData.product.id;
    const lang = (langOverride || edState.activeLang || '').trim().toLowerCase();
    if (!pid || !lang || lang === 'en') return;
    edOpenDetailTranslateTaskModal(lang);
  }

  async function edSubmitDetailTranslate() {
    const pid = edState.productData && edState.productData.product && edState.productData.product.id;
    const mask = $('edDetailTranslateTaskMask');
    const lang = mask ? (mask.dataset.lang || '').trim().toLowerCase() : '';
    if (!pid || !lang || lang === 'en') return;

    const langName = langDisplayName(lang);
    const group = $('edDetailTranslateModeGroup');
    const active = group ? group.querySelector('.oc-chip.on') : null;
    const mode = active ? active.dataset.mode : 'parallel';

    const config = $('edDetailTranslateTaskConfig');
    const result = $('edDetailTranslateTaskResult');
    const msg = $('edDetailTranslateTaskMsg');
    const meta = $('edDetailTranslateTaskMeta');
    const link = $('edDetailTranslateTaskLink');

    if (config) config.hidden = true;
    if (result) result.hidden = false;
    if (msg) msg.textContent = '正在创建翻译任务...';
    if (meta) meta.textContent = `${langName} · 商品详情图（${mode === 'parallel' ? '并行' : '串行'}）`;
    if (link) {
      link.hidden = true;
      link.removeAttribute('href');
      delete link.dataset.taskId;
    }

    try {
      const data = await fetchJSON(`/medias/api/products/${pid}/detail-images/translate-from-en`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ lang, concurrency_mode: mode }),
      });
      if (msg) msg.textContent = '翻译任务已创建，可以留在当前页查看历史记录，也可以打开详情页跟踪进度。';
      if (meta) meta.textContent = `任务 ID：${data.task_id} · ${langName} · ${mode === 'parallel' ? '并行' : '串行'}`;
      if (link) {
        link.href = data.detail_url || `/image-translate/${data.task_id}`;
        link.dataset.taskId = data.task_id || '';
        link.hidden = false;
      }
      await edRefreshDetailImagesPanel(lang);
    } catch (err) {
      if (msg) msg.textContent = '创建翻译任务失败';
      if (meta) meta.textContent = err.message || String(err);
      if (link) {
        link.hidden = true;
        link.removeAttribute('href');
      }
    }
  }

  function edCloseLinkCheckModal() {
    const mask = $('edLinkCheckMask');
    if (mask) mask.hidden = true;
    edState.linkCheckModalLang = '';
    edState.linkCheckDetailTask = null;
    edState.linkCheckDetailError = '';
  }

  function edLoadLinkCheckDetail(lang) {
    const pid = edState.productData && edState.productData.product && edState.productData.product.id;
    const task = edGetLinkCheckTask(lang);
    if (!pid || !task || !task.task_id) return Promise.resolve();
    return fetchJSON(`/medias/api/products/${pid}/link-check/${encodeURIComponent(lang)}/detail`)
      .then((detail) => {
        edState.linkCheckDetailTask = detail;
        edState.linkCheckDetailError = '';
        if (edState.linkCheckModalLang === lang) {
          edRenderLinkCheckModal();
        }
        return detail;
      })
      .catch((err) => {
        edState.linkCheckDetailTask = null;
        edState.linkCheckDetailError = err.message || String(err);
        if (edState.linkCheckModalLang === lang) {
          edRenderLinkCheckModal();
        }
      });
  }

  function edOpenLinkCheckModal() {
    const lang = edState.activeLang;
    const task = edGetLinkCheckTask(lang);
    if (!task || !task.task_id) return;
    edState.linkCheckModalLang = lang;
    edState.linkCheckDetailTask = null;
    edState.linkCheckDetailError = '';
    $('edLinkCheckMask').hidden = false;
    edRenderLinkCheckModal();
    if (edLinkCheckNeedsPolling(task)) {
      edPollLinkCheck(lang);
    } else {
      edLoadLinkCheckDetail(lang);
    }
  }

  function edPollLinkCheck(lang) {
    const pid = edState.productData && edState.productData.product && edState.productData.product.id;
    if (!pid || !lang) return Promise.resolve();
    edStopLinkCheckPoll();
    return fetchJSON(`/medias/api/products/${pid}/link-check/${encodeURIComponent(lang)}`)
      .then((data) => {
        if (data && data.task) {
          edSetLinkCheckTask(lang, data.task);
        }
        if (lang === edState.activeLang) {
          edRenderLinkCheckSummary(edGetLinkCheckTask(lang));
        }
        if (edState.linkCheckModalLang === lang) {
          edRenderLinkCheckModal();
        }
        const task = edGetLinkCheckTask(lang);
        if (task && edState.linkCheckModalLang === lang && !edLinkCheckNeedsPolling(task)) {
          return edLoadLinkCheckDetail(lang);
        }
        if (task && lang === edState.activeLang && edLinkCheckNeedsPolling(task) && !$('edMask').hidden) {
          edState.linkCheckPollTimer = setTimeout(() => edPollLinkCheck(lang), 2000);
        }
        return data;
      })
      .catch((err) => {
        if (lang === edState.activeLang) {
          edRenderLinkCheckSummary(edGetLinkCheckTask(lang));
        }
        if (edState.linkCheckModalLang === lang) {
          edState.linkCheckDetailError = err.message || String(err);
          edRenderLinkCheckModal();
        }
      });
  }

  function edStartLinkCheck() {
    return (async () => {
      const pid = edState.productData && edState.productData.product && edState.productData.product.id;
      if (!pid) return;
      edFlushProductUrl();
      const lang = edState.activeLang;
      const url = edCurrentLinkUrl(lang);
      if (!url || !/^https?:\/\//i.test(url)) {
        alert('请先填写有效的商品链接');
        $('edProductUrl') && $('edProductUrl').focus();
        return;
      }

      const actionBtn = $('edLinkCheckBtn');
      if (actionBtn) {
        actionBtn.disabled = true;
        actionBtn.innerHTML = `${icon('search', 14)}<span>检测中...</span>`;
      }

      try {
        const data = await fetchJSON(`/medias/api/products/${pid}/link-check`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ lang, link_url: url }),
        });
        edSetLinkCheckTask(lang, {
          task_id: data.task_id,
          status: data.status || 'queued',
          link_url: url,
          checked_at: new Date().toISOString(),
          summary: {
            overall_decision: 'running',
            pass_count: 0,
            replace_count: 0,
            review_count: 0,
          },
        });
        edRenderLinkCheckSummary(edGetLinkCheckTask(lang));
        edOpenLinkCheckModal();
      } catch (e) {
        alert('链接检测启动失败：' + (e.message || e));
      } finally {
        if (actionBtn) {
          actionBtn.disabled = false;
        }
        edRenderLinkCheckSummary(edGetLinkCheckTask(lang));
      }
    })();
  }

  async function edRenderActiveLangView() {
    const lang = edState.activeLang;
    // 更新语种标签提示
    const cwLabel = $('edCwLangLabel');
    const itemsLabel = $('edItemsLangLabel');
    const langName = langDisplayName(lang);
    if (cwLabel) cwLabel.textContent = `(${langName})`;
    if (itemsLabel) itemsLabel.textContent = `(${langName})`;

    edRenderCoverBlock(lang);
    edRenderItemsBlock(lang);
    edRenderCopyBlock(lang);
    edRenderCopyTranslateButton();
    edRenderProductUrl(lang);
    edRenderLinkCheckSummary(edGetLinkCheckTask(lang));
    edRenderShopifyImageStatus(lang);
    if (edGetLinkCheckTask(lang)) {
      edPollLinkCheck(lang);
    } else {
      edStopLinkCheckPoll();
    }

    await edRefreshDetailImagesPanel(lang);

    edSyncDetailImagesDownloadZipButton();
  }

  // --- 主图块（按语种渲染） ---
  function edRenderCoverBlock(lang) {
    const block = $('edCoverBlock');
    if (!block) return;
    const pid = edState.productData && edState.productData.product && edState.productData.product.id;
    const covers = (edState.productData && edState.productData.covers) || {};
    const hasKey = !!covers[lang];
    const coverUrl = hasKey ? `/medias/cover/${pid}?lang=${lang}&_=${Date.now()}` : null;
    const isEn = lang === 'en';
    const deleteBtn = isEn ? '' :
      `<button type="button" class="oc-btn text sm" id="edCoverDeleteBtn" style="color:var(--oc-danger-fg)">删除此语种主图</button>`;
    const fallbackHint = !isEn && !hasKey
      ? `<p class="oc-cover-fallback-hint">未上传时将展示 EN 主图</p>`
      : '';

    block.innerHTML = `
      <div class="oc-cover-row">
        <div id="edCoverBox" class="oc-cover-square-480">
          <div id="edCoverDropzone" class="cover-dz" tabindex="0" role="button" aria-label="上传产品主图">
            <div class="dz-icon"><svg width="18" height="18"><use href="#ic-upload"/></svg></div>
            <div class="dz-title">点击或拖拽上传</div>
            <div class="dz-hint">JPG / PNG / WebP</div>
          </div>
          <img id="edCoverImg" alt="主图" ${coverUrl ? `src="${escapeHtml(coverUrl)}"` : 'hidden'}>
        </div>
        <div class="oc-cover-actions">
          <button type="button" class="oc-btn ghost sm" id="edCoverReplace">更换主图</button>
          ${deleteBtn}
          <div class="oc-url-row" style="margin-top:var(--oc-sp-2)">
            <input type="url" id="edCoverUrl" class="oc-input sm" placeholder="粘贴图片 URL 导入…">
            <button type="button" class="oc-btn ghost sm" id="edCoverFromUrlBtn">从 URL 导入</button>
          </div>
          <input type="file" id="edCoverInput" accept="image/*" hidden>
          ${fallbackHint}
        </div>
      </div>`;

    // 同步显示状态
    if (coverUrl) {
      const dz = $('edCoverDropzone');
      if (dz) dz.hidden = true;
    }

    // 重新绑定事件
    const coverDropzone = $('edCoverDropzone');
    const coverInput = $('edCoverInput');
    const coverReplace = $('edCoverReplace');
    const coverFromUrl = $('edCoverFromUrlBtn');
    const coverDelete = $('edCoverDeleteBtn');

    if (coverDropzone) {
      coverDropzone.addEventListener('click', () => coverInput && coverInput.click());
      coverDropzone.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); coverInput && coverInput.click(); } });
      coverDropzone.addEventListener('dragover', (e) => { e.preventDefault(); coverDropzone.classList.add('drag'); });
      coverDropzone.addEventListener('dragleave', () => coverDropzone.classList.remove('drag'));
      coverDropzone.addEventListener('drop', (e) => {
        e.preventDefault(); coverDropzone.classList.remove('drag');
        const f = [...(e.dataTransfer.files || [])].find(x => x.type.startsWith('image/'));
        if (f) edUploadCover(f, lang);
      });
      coverDropzone.addEventListener('paste', (e) => {
        const item = [...(e.clipboardData?.items || [])].find(i => i.type.startsWith('image/'));
        if (item) { e.preventDefault(); edUploadCover(item.getAsFile(), lang); }
      });
    }
    if (coverReplace) coverReplace.addEventListener('click', () => coverInput && coverInput.click());
    if (coverInput) {
      coverInput.addEventListener('change', (e) => {
        const f = e.target.files[0]; e.target.value = '';
        if (f) edUploadCover(f, lang);
      });
    }
    if (coverFromUrl) coverFromUrl.addEventListener('click', () => edImportCoverFromUrl(lang));
    const urlInput = $('edCoverUrl');
    if (urlInput) urlInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); edImportCoverFromUrl(lang); } });
    if (coverDelete) coverDelete.addEventListener('click', () => edDeleteCover(lang));
  }

  function edSetCoverUI(url) {
    const dz = $('edCoverDropzone');
    const img = $('edCoverImg');
    if (!dz || !img) return;
    if (url) { img.src = url; img.hidden = false; dz.hidden = true; }
    else { img.removeAttribute('src'); img.hidden = true; dz.hidden = false; }
  }

  async function edUploadCover(file, lang) {
    lang = lang || edState.activeLang;
    if (!window.MEDIAS_UPLOAD_READY) { alert('本地上传未就绪，无法上传'); return; }
    if (!file.type.startsWith('image/')) { alert('请上传图片文件'); return; }
    const pid = edState.productData && edState.productData.product && edState.productData.product.id;
    if (!pid) return;
    try {
      const boot = await fetchJSON(`/medias/api/products/${pid}/cover/bootstrap`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename: file.name, lang }),
      });
      const putRes = await fetch(boot.upload_url, { method: 'PUT', body: file });
      if (!putRes.ok) throw new Error('上传失败');
      await fetchJSON(`/medias/api/products/${pid}/cover/complete`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ object_key: boot.object_key, lang }),
      });
      // 重拉数据刷新视图
      const fresh = await fetchJSON('/medias/api/products/' + pid);
      edSetProductData(fresh);
      edRenderLangTabs();
      edRenderActiveLangView();
    } catch (e) {
      alert('封面上传失败：' + (e.message || ''));
    }
  }

  async function edImportCoverFromUrl(lang) {
    lang = lang || edState.activeLang;
    const urlInput = $('edCoverUrl');
    const url = urlInput ? urlInput.value.trim() : '';
    if (!url) { alert('请粘贴图片 URL'); return; }
    const pid = edState.productData && edState.productData.product && edState.productData.product.id;
    if (!pid) return;
    try {
      await fetchJSON(`/medias/api/products/${pid}/cover/from-url`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url, lang }),
      });
      if (urlInput) urlInput.value = '';
      const fresh = await fetchJSON('/medias/api/products/' + pid);
      edSetProductData(fresh);
      edRenderLangTabs();
      edRenderActiveLangView();
    } catch (e) {
      alert('从 URL 导入失败：' + (e.message || ''));
    }
  }

  async function edDeleteCover(lang) {
    if (lang === 'en') { alert('EN 主图不可删除'); return; }
    if (!confirm(`确认删除 ${langDisplayName(lang)} 语种主图？`)) return;
    const pid = edState.productData && edState.productData.product && edState.productData.product.id;
    if (!pid) return;
    try {
      await fetchJSON(`/medias/api/products/${pid}/cover?lang=${lang}`, { method: 'DELETE' });
      const fresh = await fetchJSON('/medias/api/products/' + pid);
      edSetProductData(fresh);
      edRenderLangTabs();
      edRenderActiveLangView();
    } catch (e) {
      alert('删除失败：' + (e.message || ''));
    }
  }

  // --- 视频素材块（按 activeLang 过滤） ---
  function edRenderItemsBlock(lang) {
    const allItems = (edState.productData && edState.productData.items) || [];
    const filtered = allItems.filter(it => it.lang === lang);
    edRenderItems(filtered);
  }

  // --- 文案块（按 activeLang 过滤） ---
  function edRenderCopyBlock(lang) {
    const raw = (edState.productData && edState.productData.copywritings) || [];
    let list = [];
    if (Array.isArray(raw)) {
      list = raw.filter(c => c.lang === lang);
    } else if (raw && typeof raw === 'object') {
      list = (raw[lang] || []);
    }
    edRenderCopywritings(list);
  }

  // 切换语种前把当前 DOM 文案写回 productData
  function edFlushCopywritings() {
    const lang = edState.activeLang;
    const items = [...$('edCwList').children].map(card => ({
      lang,
      body: edNormalizeCopywritingBody(card.querySelector('[data-field="body"]').value) || null,
    }));
    // 确保 productData.copywritings 是 array 格式（按 lang 存储）
    if (!edState.productData) return;
    let arr = edEnsureCopywritingsArray();
    // 移除当前语种旧数据，写入新数据
    arr = arr.filter(c => c.lang !== lang);
    arr = arr.concat(items);
    edState.productData.copywritings = arr;
  }

  // ---------- 新增素材大框（封面+视频+提交） ----------

  function _fmtFileSize(n) {
    if (!n && n !== 0) return '';
    if (n < 1024) return n + ' B';
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
    if (n < 1024 * 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' MB';
    return (n / 1024 / 1024 / 1024).toFixed(2) + ' GB';
  }

  function edSetItemCover(url) {
    const box = $('edItemCoverBox');
    if (!box) return;
    const dz = $('edItemCoverDropzone');
    const img = $('edItemCoverImg');
    const replace = $('edItemCoverReplace');
    const clear = $('edItemCoverClear');
    if (url) {
      img.src = url; img.hidden = false; dz.hidden = true;
      if (replace) replace.hidden = false;
      if (clear) clear.hidden = false;
    } else {
      img.removeAttribute('src'); img.hidden = true; dz.hidden = false;
      if (replace) replace.hidden = true;
      if (clear) clear.hidden = true;
    }
  }

  function edSetPickedVideo(file) {
    edState.pendingVideoFile = file || null;
    const empty = $('edVideoPickEmpty');
    const filled = $('edVideoPickFilled');
    if (!empty || !filled) return;
    if (file) {
      empty.hidden = true;
      filled.hidden = false;
      $('edVideoPickName').textContent = file.name;
      $('edVideoPickSize').textContent = _fmtFileSize(file.size);
    } else {
      empty.hidden = false;
      filled.hidden = true;
      $('edVideoPickName').textContent = '';
      $('edVideoPickSize').textContent = '';
    }
  }

  function edResetNewItemForm() {
    edState.pendingItemCover = null;
    edState.pendingVideoFile = null;
    edSetItemCover(null);
    edSetPickedVideo(null);
    const url = $('edItemCoverUrl'); if (url) url.value = '';
  }

  function edClearNewItemProgress() {
    const box = $('edUploadProgress');
    if (box) box.innerHTML = '';
  }

  function edOpenNewItemModal() {
    const mask = $('edNewItemMask');
    if (!mask) return;
    edResetNewItemForm();
    edClearNewItemProgress();
    mask.hidden = false;
    const target = $('edItemCoverDropzone') || $('edItemCoverUrl') || $('edItemSubmitBtn');
    if (target) setTimeout(() => target.focus(), 0);
  }

  function edCloseNewItemModal() {
    if (edState.isSubmittingNewItem) {
      alert('素材上传中，请等待完成后再关闭');
      return;
    }
    const mask = $('edNewItemMask');
    if (!mask) return;
    mask.hidden = true;
    edResetNewItemForm();
    edClearNewItemProgress();
  }

  async function edUploadPendingItemCover(file) {
    if (!window.MEDIAS_UPLOAD_READY) { alert('本地上传未就绪，无法上传'); return; }
    if (!file.type.startsWith('image/')) { alert('请上传图片文件'); return; }
    const pid = edState.productData && edState.productData.product && edState.productData.product.id;
    if (!pid) { alert('产品数据未加载'); return; }
    try {
      const boot = await fetchJSON(`/medias/api/products/${pid}/item-cover/bootstrap`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename: file.name }),
      });
      const putRes = await fetch(boot.upload_url, { method: 'PUT', body: file });
      if (!putRes.ok) throw new Error('上传失败');
      edState.pendingItemCover = boot.object_key;
      edSetItemCover(URL.createObjectURL(file));
    } catch (e) {
      alert('视频封面上传失败：' + (e.message || ''));
    }
  }

  async function edImportItemCoverFromUrl() {
    const url = ($('edItemCoverUrl').value || '').trim();
    if (!url) { alert('请粘贴图片 URL'); return; }
    const pid = edState.productData && edState.productData.product && edState.productData.product.id;
    if (!pid) { alert('产品数据未加载'); return; }
    try {
      const done = await fetchJSON(`/medias/api/products/${pid}/item-cover/from-url`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url }),
      });
      edState.pendingItemCover = done.object_key;
      edSetItemCover(url);
      $('edItemCoverUrl').value = '';
    } catch (e) {
      alert('从 URL 导入失败：' + (e.message || ''));
    }
  }

  async function edSubmitNewItem() {
    if (!window.MEDIAS_UPLOAD_READY) { alert('本地上传未就绪，无法上传'); return; }
    if (!edState.pendingItemCover) {
      alert('请先上传视频封面图');
      $('edItemCoverDropzone') && $('edItemCoverDropzone').focus();
      return;
    }
    if (!edState.pendingVideoFile) {
      alert('请先选择视频源文件');
      $('edVideoPickBox') && $('edVideoPickBox').focus();
      return;
    }
    const file = edState.pendingVideoFile;
    const pid = edState.productData && edState.productData.product && edState.productData.product.id;
    if (!pid) { alert('产品数据未加载'); return; }
    await ensureLanguages();
    const lang = resolveMaterialFilenameLang(file.name, edState.activeLang);
    const productName = edState.productData && edState.productData.product && edState.productData.product.name;
    if (!assertMaterialFilenameOrAlert(file.name, productName, lang)) return;
    const box = $('edUploadProgress');
    const row = document.createElement('div');
    row.className = 'oc-upload-row';
    row.innerHTML = `<span class="fname">${escapeHtml(file.name)}</span><span>上传中…</span>`;
    box.appendChild(row);
    const submitBtn = $('edItemSubmitBtn');
    if (submitBtn) submitBtn.disabled = true;
    edState.isSubmittingNewItem = true;
    try {
      const boot = await fetchJSON(`/medias/api/products/${pid}/items/bootstrap`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename: file.name, lang }),
      });
      const putRes = await fetch(boot.upload_url, { method: 'PUT', body: file });
      if (!putRes.ok) throw new Error('上传失败');
      await fetchJSON(`/medias/api/products/${pid}/items/complete`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          object_key: boot.object_key,
          filename: file.name,
          file_size: file.size,
          cover_object_key: edState.pendingItemCover,
          lang,
        }),
      });
      row.className = 'oc-upload-row ok';
      row.innerHTML = `<span class="fname">${escapeHtml(file.name)}</span><span>完成</span>`;
      edResetNewItemForm();
    } catch (e) {
      row.className = 'oc-upload-row err';
      row.innerHTML = `<span class="fname">${escapeHtml(file.name)}</span><span>失败：${escapeHtml(e.message || '')}</span>`;
    } finally {
      edState.isSubmittingNewItem = false;
      if (submitBtn) submitBtn.disabled = false;
    }
    try {
      const full = await fetchJSON('/medias/api/products/' + pid);
      edSetProductData(full);
      edRenderLangTabs();
      edRenderActiveLangView();
      loadList();
    } catch {}
  }

  function edRenderCopywritings(list) {
    const box = $('edCwList');
    box.innerHTML = '';
    (list || []).forEach((c, i) => box.appendChild(edCwCard(c, i + 1)));
    $('edCwBadge').textContent = box.children.length;
  }

  function edCwCard(c, idx) {
    const d = document.createElement('div');
    d.className = 'oc-cw';
    const autoBadge = c && c.auto_translated ? (
      c.manually_edited_at
        ? `<span class="bt-row-btn" title="来自英文自动翻译,已人工修改" style="margin-left:6px;cursor:default">🔗 英文译本 · ✏️</span>`
        : `<span class="bt-row-btn" title="来自英文自动翻译" style="margin-left:6px;cursor:default">🔗 英文译本</span>`
    ) : '';
    d.innerHTML = `
      <button class="oc-icon-btn rm" type="button" aria-label="删除该条">${icon('close', 14)}</button>
      <div class="idx">#${idx}${autoBadge}</div>
      <div class="stack">
        <textarea class="oc-textarea" data-field="body"></textarea>
      </div>
    `;
    const textarea = d.querySelector('[data-field="body"]');
    textarea.rows = 3;
    textarea.wrap = 'off';
    textarea.placeholder = '标题: \n文案: \n描述: ';
    textarea.value = edNormalizeCopywritingBody((c && c.body) || '');
    textarea.addEventListener('blur', () => {
      textarea.value = edNormalizeCopywritingBody(textarea.value);
    });
    d.querySelector('.rm').addEventListener('click', () => {
      d.remove();
      [...$('edCwList').children].forEach((e, i) => {
        const el = e.querySelector('.idx'); if (el) el.textContent = '#' + (i + 1);
      });
      $('edCwBadge').textContent = $('edCwList').children.length;
    });
    return d;
  }

  function edCollectCopywritings() {
    return [...$('edCwList').children].map(card => ({
      body: edNormalizeCopywritingBody(card.querySelector('[data-field="body"]').value) || null,
    }));
  }

  function itemSourceLabel(it) {
    const source = it && it.source_raw;
    if (source && source.display_name) return source.display_name;
    const rawId = it && (it.source_raw_id || (it.auto_translated && it.source_ref_id));
    if (rawId) return `原始去字幕素材 #${rawId}`;
    return '';
  }

  function edRenderItems(items) {
    const g = $('edItemsGrid');
    g.innerHTML = (items || []).map(it => {
      const cover = it.cover_url;
      const rawName = it.display_name || it.filename || '';
      const name = escapeHtml(rawName);
      const sourceLabel = itemSourceLabel(it);
      const sourceHtml = sourceLabel
        ? `<div class="vsource" title="${escapeHtml(sourceLabel)}">来源：${escapeHtml(sourceLabel)}</div>`
        : '';
      const imgTag = cover
        ? `<img src="${escapeHtml(cover)}?_=${Date.now()}" loading="lazy" alt="">`
        : `<div class="thumb-ph">${icon('film', 20)}</div>`;
      return `
      <div class="oc-vitem" data-item="${it.id}">
        <div class="vname oc-vitem-name-editor">
          <input class="oc-input sm vname-input" type="text" value="${name}"
                 title="${name}" data-original="${name}" maxlength="255"
                 aria-label="视频素材文件名" readonly>
          <div class="vname-edit-actions">
            <button class="oc-btn text sm" type="button" data-act="name-edit">${icon('edit', 12)}<span>修改文件名</span></button>
            <button class="oc-btn primary sm" type="button" data-act="name-save" hidden>${icon('check', 12)}<span>保存</span></button>
            <button class="oc-btn ghost sm" type="button" data-act="name-cancel" hidden>${icon('close', 12)}<span>取消</span></button>
          </div>
        </div>
        ${sourceHtml}
        <div class="vtabs">
          <button type="button" class="vtab active" data-tab="img">图片</button>
          <button type="button" class="vtab" data-tab="video">视频</button>
        </div>
        <div class="vbody">
          <div class="vpane active" data-pane="img">${imgTag}</div>
          <div class="vpane" data-pane="video">
            <div class="vvideo-ph">点击"视频"标签后加载播放</div>
          </div>
        </div>
        <div class="vactions">
          <button class="oc-btn text sm" data-act="cover">${icon('edit', 12)}<span>换封面</span></button>
          <button class="oc-btn text sm danger-txt" data-act="del">${icon('trash', 12)}<span>删除</span></button>
        </div>
      </div>`;
    }).join('');
    g.querySelectorAll('[data-item]').forEach(card => {
      const id = +card.dataset.item;
      const tabs = card.querySelectorAll('.vtab');
      const panes = card.querySelectorAll('.vpane');
      tabs.forEach(t => t.addEventListener('click', () => {
        tabs.forEach(x => x.classList.toggle('active', x === t));
        panes.forEach(p => p.classList.toggle('active', p.dataset.pane === t.dataset.tab));
        if (t.dataset.tab === 'video') edEnsureVideoLoaded(card, id);
      }));
      card.querySelector('[data-act="del"]').addEventListener('click', () => edRemoveItem(id, card));
      card.querySelector('[data-act="cover"]').addEventListener('click', () => edPickItemCover(id));
      card.querySelector('[data-act="name-edit"]').addEventListener('click', () => edStartItemNameEdit(card));
      card.querySelector('[data-act="name-save"]').addEventListener('click', () => edSaveItemNameEdit(id, card));
      card.querySelector('[data-act="name-cancel"]').addEventListener('click', () => edCancelItemNameEdit(card));
      card.querySelector('.vname-input').addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
          event.preventDefault();
          edCancelItemNameEdit(card);
        } else if (event.key === 'Enter') {
          event.preventDefault();
          edSaveItemNameEdit(id, card);
        }
      });
    });
    $('edItemsBadge').textContent = (items || []).length;
  }

  function edSetItemNameSaving(card, saving) {
    const buttons = card.querySelectorAll('[data-act="name-edit"], [data-act="name-save"], [data-act="name-cancel"]');
    buttons.forEach(btn => { btn.disabled = !!saving; });
    const input = card.querySelector('.vname-input');
    if (input) input.disabled = !!saving;
  }

  function edSetItemNameEditMode(card, editing) {
    const input = card.querySelector('.vname-input');
    const editBtn = card.querySelector('[data-act="name-edit"]');
    const saveBtn = card.querySelector('[data-act="name-save"]');
    const cancelBtn = card.querySelector('[data-act="name-cancel"]');
    if (!input || !editBtn || !saveBtn || !cancelBtn) return;
    input.readOnly = !editing;
    editBtn.hidden = editing;
    saveBtn.hidden = !editing;
    cancelBtn.hidden = !editing;
    card.classList.toggle('is-name-editing', editing);
  }

  function edStartItemNameEdit(card) {
    const input = card.querySelector('.vname-input');
    if (!input) return;
    edSetItemNameEditMode(card, true);
    input.focus();
    input.setSelectionRange(0, input.value.length);
  }

  function edCancelItemNameEdit(card) {
    const input = card.querySelector('.vname-input');
    if (!input) return;
    input.value = input.dataset.original || '';
    edSetItemNameEditMode(card, false);
  }

  function edPatchItemNameInState(itemId, itemPayload, fallbackName) {
    const items = (edState.productData && edState.productData.items) || [];
    const target = items.find(it => Number(it.id) === Number(itemId));
    if (!target) return;
    target.display_name = (itemPayload && itemPayload.display_name) || fallbackName;
  }

  async function edSaveItemNameEdit(itemId, card) {
    const input = card.querySelector('.vname-input');
    if (!input) return;
    const nextName = input.value.trim();
    if (!nextName) {
      alert('文件名不能为空');
      input.focus();
      return;
    }
    const oldName = input.dataset.original || '';
    if (nextName === oldName) {
      edSetItemNameEditMode(card, false);
      return;
    }

    edSetItemNameSaving(card, true);
    try {
      const data = await fetchJSON(`/medias/api/items/${itemId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ display_name: nextName }),
      });
      const updated = data.item || {};
      const savedName = updated.display_name || nextName;
      input.value = savedName;
      input.dataset.original = savedName;
      input.title = savedName;
      edPatchItemNameInState(itemId, updated, savedName);
      edSetItemNameEditMode(card, false);
    } catch (e) {
      alert('修改文件名失败：' + (e.message || ''));
      input.focus();
    } finally {
      edSetItemNameSaving(card, false);
    }
  }

  async function edEnsureVideoLoaded(card, itemId) {
    const pane = card.querySelector('[data-pane="video"]');
    if (pane.dataset.loaded === '1') return;
    pane.innerHTML = `<div class="vvideo-ph">加载中…</div>`;
    try {
      const r = await fetchJSON(`/medias/api/items/${itemId}/play_url`);
      pane.innerHTML = `<video controls preload="metadata" src="${escapeHtml(r.url)}"></video>`;
      pane.dataset.loaded = '1';
    } catch (e) {
      pane.innerHTML = `<div class="vvideo-ph err">加载失败：${escapeHtml(e.message || '')}</div>`;
    }
  }

  function edPickItemCover(itemId) {
    const picker = document.createElement('input');
    picker.type = 'file';
    picker.accept = 'image/*';
    picker.onchange = (e) => {
      const f = e.target.files[0];
      if (f) edUploadItemCover(itemId, f);
    };
    picker.click();
  }

  async function edUploadItemCover(itemId, file) {
    if (!window.MEDIAS_UPLOAD_READY) { alert('本地上传未就绪，无法上传'); return; }
    const pid = edState.productData && edState.productData.product && edState.productData.product.id;
    if (!pid) return;
    try {
      const boot = await fetchJSON(`/medias/api/products/${pid}/item-cover/bootstrap`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename: file.name }),
      });
      const putRes = await fetch(boot.upload_url, { method: 'PUT', body: file });
      if (!putRes.ok) throw new Error('上传失败');
      await fetchJSON(`/medias/api/items/${itemId}/cover/set`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ object_key: boot.object_key }),
      });
      const full = await fetchJSON('/medias/api/products/' + pid);
      edSetProductData(full);
      edRenderLangTabs();
      edRenderActiveLangView();
    } catch (e) {
      alert('视频封面上传失败：' + (e.message || ''));
    }
  }

  async function edRemoveItem(itemId, card) {
    if (!confirm('确认删除该素材？')) return;
    await fetch('/medias/api/items/' + itemId, { method: 'DELETE' });
    card.remove();
    $('edItemsBadge').textContent = $('edItemsGrid').children.length;
    const pid = edState.productData && edState.productData.product && edState.productData.product.id;
    if (pid) {
      const full = await fetchJSON('/medias/api/products/' + pid);
      edSetProductData(full);
      edRenderLangTabs();
      edRenderActiveLangView();
    }
  }

  async function edSave() {
    let pid = null;
    let payload = null;
    try {
      ({ pid, payload } = edCollectProductPayload());
    } catch (e) {
      alert(e.message || String(e));
      return;
    }
    try {
      await fetchJSON('/medias/api/products/' + pid, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      edHide();
      loadList();
    } catch (e) {
      const msg = (e.message || '').toString();
      if (msg.includes('mk_id_conflict') || msg.includes('明空 ID 已被其他产品占用')) {
        alert('明空 ID 已被其他产品占用');
        $('edMkId').focus();
      } else if (msg.includes('mk_id_invalid')) {
        alert('明空 ID 必须是 1-8 位数字');
        $('edMkId').focus();
      } else if (msg.includes('已被占用')) {
        alert('产品 ID 已被占用');
        $('edCode').focus();
      } else {
        alert('保存失败：' + msg);
      }
    }
  }

  // ---------- Events ----------
  document.addEventListener('DOMContentLoaded', () => {
    const searchBtn = $('searchBtn');
    const kwInput = $('kw');
    searchBtn.addEventListener('click', runSearchNow);
    kwInput.addEventListener('input', scheduleLiveSearch);
    kwInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); runSearchNow(); } });

    const syncChip = (chipId, inputId) => {
      const chip = $(chipId), inp = $(inputId);
      if (!chip || !inp) return;
      const sync = () => chip.classList.toggle('on', inp.checked);
      inp.addEventListener('change', () => { sync(); state.page = 1; loadList(); });
      sync();
    };

    $('createBtn').addEventListener('click', openCreate);
    $('modalClose').addEventListener('click', hideModal);
    $('cancelBtn').addEventListener('click', hideModal);
    $('saveBtn').addEventListener('click', save);
    $('mkCopyFetchBtn').addEventListener('click', fillCopywritingFromMkSystem);
    $('editMask').addEventListener('click', (e) => { if (e.target.id === 'editMask') hideModal(); });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !$('editMask').hidden) hideModal(); });
    $('roasCloseBtn') && $('roasCloseBtn').addEventListener('click', closeRoasModal);
    $('roasCancelBtn') && $('roasCancelBtn').addEventListener('click', closeRoasModal);
    $('roasSaveBtn') && $('roasSaveBtn').addEventListener('click', saveRoas);
    $('roasCalculateBtn') && $('roasCalculateBtn').addEventListener('click', renderRoasResult);
    $('roasModalMask') && $('roasModalMask').addEventListener('click', (e) => {
      if (e.target.id === 'roasModalMask') closeRoasModal();
    });
    document.querySelectorAll('[data-roas-field]').forEach((input) => {
      input.addEventListener('input', markRoasResultDirty);
    });
    const roasAverageShippingInput = $('roasAverageShippingInput');
    if (roasAverageShippingInput) {
      roasAverageShippingInput.addEventListener('input', updateRoasAverageShipping);
      updateRoasAverageShipping();
    }
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && $('roasModalMask') && !$('roasModalMask').hidden) closeRoasModal();
    });

    $('cwAddBtn').addEventListener('click', () => {
      $('cwList').appendChild(cwCard({}, $('cwList').children.length + 1));
      updateCwBadge();
    });

    const cdz = $('coverDropzone');
    cdz.addEventListener('click', () => $('coverInput').click());
    cdz.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $('coverInput').click(); } });
    cdz.addEventListener('dragover', (e) => { e.preventDefault(); cdz.classList.add('drag'); });
    cdz.addEventListener('dragleave', () => cdz.classList.remove('drag'));
    cdz.addEventListener('drop', (e) => {
      e.preventDefault(); cdz.classList.remove('drag');
      const f = [...(e.dataTransfer.files || [])].find(x => x.type.startsWith('image/'));
      if (f) uploadCover(f);
    });
    $('coverReplace').addEventListener('click', () => $('coverInput').click());
    $('coverInput').addEventListener('change', (e) => {
      const f = e.target.files[0]; e.target.value = '';
      if (f) uploadCover(f);
    });

    $('coverFromUrlBtn').addEventListener('click', importCoverFromUrl);
    $('coverUrl').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); importCoverFromUrl(); } });

    // 粘贴图片到 产品主图 dropzone
    cdz.addEventListener('paste', (e) => {
      const item = [...(e.clipboardData?.items || [])].find(i => i.type.startsWith('image/'));
      if (item) { e.preventDefault(); uploadCover(item.getAsFile()); }
    });

    // 视频封面图（add modal, 等待 /items/complete 时带过去）
    // 注：添加弹窗新版已移除这块 UI，节点不存在时直接跳过绑定。
    const icdz = $('itemCoverDropzone');
    if (icdz) {
      icdz.addEventListener('click', () => $('itemCoverInput').click());
      icdz.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $('itemCoverInput').click(); } });
      icdz.addEventListener('dragover', (e) => { e.preventDefault(); icdz.classList.add('drag'); });
      icdz.addEventListener('dragleave', () => icdz.classList.remove('drag'));
      icdz.addEventListener('drop', (e) => {
        e.preventDefault(); icdz.classList.remove('drag');
        const f = [...(e.dataTransfer.files || [])].find(x => x.type.startsWith('image/'));
        if (f) uploadItemCover(f);
      });
      icdz.addEventListener('paste', (e) => {
        const item = [...(e.clipboardData?.items || [])].find(i => i.type.startsWith('image/'));
        if (item) { e.preventDefault(); uploadItemCover(item.getAsFile()); }
      });
    }
    $('itemCoverReplace') && $('itemCoverReplace').addEventListener('click', () => $('itemCoverInput').click());
    $('itemCoverClear') && $('itemCoverClear').addEventListener('click', clearItemCover);
    $('itemCoverInput') && $('itemCoverInput').addEventListener('change', (e) => {
      const f = e.target.files[0]; e.target.value = '';
      if (f) uploadItemCover(f);
    });
    $('itemCoverFromUrlBtn') && $('itemCoverFromUrlBtn').addEventListener('click', importItemCoverFromUrl);
    $('itemCoverUrl') && $('itemCoverUrl').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); importItemCoverFromUrl(); } });

    // 添加弹窗的视频拖拽上传区：模板也已隐藏；仅在节点存在时绑定，避免影响事件链后续注册
    const dz = $('dropzone');
    if (dz) {
      dz.addEventListener('click', () => $('fileInput').click());
      dz.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $('fileInput').click(); } });
      dz.addEventListener('dragover', (e) => { e.preventDefault(); dz.classList.add('drag'); });
      dz.addEventListener('dragleave', () => dz.classList.remove('drag'));
      dz.addEventListener('drop', (e) => {
        e.preventDefault(); dz.classList.remove('drag');
        const file = [...(e.dataTransfer.files || [])]
          .find(f => f.type.startsWith('video/') || /\.(mp4|mov|webm|mkv)$/i.test(f.name));
        if (file) uploadVideo(file);
      });
    }
    $('fileInput') && $('fileInput').addEventListener('change', (e) => {
      const file = e.target.files[0]; e.target.value = '';
      if (file) uploadVideo(file);
    });

    // ---- Edit detail modal wiring ----
    $('edClose').addEventListener('click', edHide);
    $('edCancelBtn').addEventListener('click', edHide);
    $('edSaveBtn').addEventListener('click', edSave);
    $('edNewItemOpenBtn') && $('edNewItemOpenBtn').addEventListener('click', edOpenNewItemModal);
    $('edNewItemClose') && $('edNewItemClose').addEventListener('click', edCloseNewItemModal);
    $('edMask').addEventListener('click', (e) => { if (e.target.id === 'edMask') edHide(); });
    $('edNewItemMask') && $('edNewItemMask').addEventListener('click', (e) => {
      if (e.target.id === 'edNewItemMask') edCloseNewItemModal();
    });
    document.addEventListener('keydown', (e) => {
      if (e.key !== 'Escape') return;
      if ($('edNewItemMask') && !$('edNewItemMask').hidden) {
        edCloseNewItemModal();
        return;
      }
      if (!$('edLinkCheckMask').hidden) {
        edCloseLinkCheckModal();
        return;
      }
      if (!$('edMask').hidden) edHide();
    });

    // 产品链接：输入变化时 flush 到内存；产品 ID 改了需刷新 placeholder/hint
    const edCodeInput = $('edCode');
    const edUrlInput = $('edProductUrl');
    if (edCodeInput) {
      edCodeInput.addEventListener('input', () => {
        edRenderProductUrl(edState.activeLang);
        edRenderLinkCheckSummary(edGetLinkCheckTask(edState.activeLang));
      });
    }
    if (edUrlInput) {
      edUrlInput.addEventListener('blur', () => {
        edFlushProductUrl();
        edRenderProductUrl(edState.activeLang);  // 刷新 hint（是否已自定义）
        edRenderLinkCheckSummary(edGetLinkCheckTask(edState.activeLang));
      });
    }
    $('edCopyProductIdBtn') && $('edCopyProductIdBtn').addEventListener('click', (e) => edCopyProductId(e.currentTarget));
    $('edCopyProductUrlBtn') && $('edCopyProductUrlBtn').addEventListener('click', (e) => edCopyLocalizedProductUrl(e.currentTarget));
    $('edLinkCheckViewBtn') && $('edLinkCheckViewBtn').addEventListener('click', edOpenLinkCheckModal);
    $('edLinkCheckClose') && $('edLinkCheckClose').addEventListener('click', edCloseLinkCheckModal);
    $('edLinkCheckDoneBtn') && $('edLinkCheckDoneBtn').addEventListener('click', edCloseLinkCheckModal);
    $('edLinkCheckRefreshBtn') && $('edLinkCheckRefreshBtn').addEventListener('click', () => {
      const lang = edState.linkCheckModalLang || edState.activeLang;
      const task = edGetLinkCheckTask(lang);
      if (!task) return;
      if (edLinkCheckNeedsPolling(task)) {
        edPollLinkCheck(lang);
      } else {
        edLoadLinkCheckDetail(lang);
      }
    });
    $('edLinkCheckMask') && $('edLinkCheckMask').addEventListener('click', (e) => {
      if (e.target.id === 'edLinkCheckMask') edCloseLinkCheckModal();
    });

    // 商品详情图：从商品链接一键下载（后台任务 + 进度弹窗）
    const edFromUrlBtn = $('edDetailImagesFromUrlBtn');
    const edDownloadZipBtn = $('edDetailImagesDownloadZipBtn');
    if (edFromUrlBtn) {
      let pollHandle = null;

      function renderFromUrlProgress(task) {
        const msg = $('edFromUrlMsg');
        const bar = $('edFromUrlBar');
        const sub = $('edFromUrlSub');
        const imgGrid = $('edFromUrlImages');
        const doneBtn = $('edFromUrlDoneBtn');
        if (!msg || !bar || !sub || !imgGrid || !doneBtn) return;

        msg.textContent = task.message || task.status;
        const total = task.total || 0;
        const progress = task.progress || 0;
        const percent = total ? Math.round((progress / total) * 100) : (task.status === 'fetching' ? 5 : 0);
        bar.style.width = percent + '%';
        if (task.current_url) {
          sub.textContent = `正在下载：${task.current_url}`;
        } else if (total) {
          sub.textContent = `${progress} / ${total}`;
        } else {
          sub.textContent = '';
        }

        const inserted = task.inserted || [];
        if (inserted.length) {
          imgGrid.innerHTML = inserted.map((it, i) => `
            <div style="border:1px solid var(--oc-border);border-radius:8px;overflow:hidden;">
              <img src="${escapeHtml(it.thumbnail_url)}" alt="图 ${i+1}" loading="lazy"
                   style="width:100%;height:120px;object-fit:cover;display:block;">
              <div style="padding:4px 6px;font-size:11px;color:var(--oc-fg-muted);text-align:center;">#${i + 1}</div>
            </div>
          `).join('');
        } else if (task.status === 'failed') {
          imgGrid.innerHTML = `<div class="oc-detail-images-empty" style="grid-column:1/-1;color:var(--danger-color,#dc2626);">${escapeHtml(task.error || '抓取失败')}</div>`;
        } else if (task.status === 'done' && !inserted.length) {
          imgGrid.innerHTML = `<div class="oc-detail-images-empty" style="grid-column:1/-1;">未下载到任何图片</div>`;
        }

        if (task.status === 'done' || task.status === 'failed') {
          doneBtn.disabled = false;
          doneBtn.textContent = task.status === 'failed' ? '关闭' : '完成，关闭并刷新';
        }
      }

      async function pollFromUrlTask(pid, taskId) {
        try {
          const resp = await fetch(`/medias/api/products/${pid}/detail-images/from-url/status/${taskId}`);
          if (!resp.ok) throw new Error(await resp.text());
          const task = await resp.json();
          renderFromUrlProgress(task);
          if (task.status === 'done' || task.status === 'failed') {
            pollHandle = null;
            return;
          }
        } catch (e) {
          console.error('[from-url] poll failed:', e);
        }
        pollHandle = setTimeout(() => pollFromUrlTask(pid, taskId), 1000);
      }

      function openFromUrlModal() {
        $('edFromUrlMask').hidden = false;
        $('edFromUrlMsg').textContent = '正在启动任务...';
        $('edFromUrlBar').style.width = '0%';
        $('edFromUrlSub').textContent = '';
        $('edFromUrlImages').innerHTML = '<div class="oc-detail-images-empty" style="grid-column:1/-1;">等待开始...</div>';
        $('edFromUrlDoneBtn').disabled = true;
        $('edFromUrlDoneBtn').textContent = '关闭（下载完成后可关）';
      }

      function closeFromUrlModal() {
        if (pollHandle) { clearTimeout(pollHandle); pollHandle = null; }
        $('edFromUrlMask').hidden = true;
        edRefreshDetailImagesPanel(edState.activeLang).catch((err) => {
          console.error('[detail-images] refresh after from-url failed:', err);
        });
      }

      $('edFromUrlClose').addEventListener('click', closeFromUrlModal);
      $('edFromUrlDoneBtn').addEventListener('click', closeFromUrlModal);
      $('edFromUrlMask').addEventListener('click', (e) => {
        if (e.target.id === 'edFromUrlMask') closeFromUrlModal();
      });

      function awaitFromUrlConfirm(existingCount, langCode) {
        return new Promise((resolve) => {
          const mask = $('edFromUrlConfirmMask');
          const body = $('edFromUrlConfirmBody');
          const okBtn = $('edFromUrlConfirmOkBtn');
          const cancelBtn = $('edFromUrlConfirmCancelBtn');
          const closeBtn = $('edFromUrlConfirmClose');
          if (!mask || !body || !okBtn || !cancelBtn) { resolve(true); return; }
          body.textContent = `即将清空当前【${langDisplayName(langCode)}】语种下 ${existingCount} 张详情图，并重新从商品链接抓取。该操作不可撤销。`;
          mask.hidden = false;
          const cleanup = (val) => {
            mask.hidden = true;
            okBtn.removeEventListener('click', onOk);
            cancelBtn.removeEventListener('click', onCancel);
            if (closeBtn) closeBtn.removeEventListener('click', onCancel);
            mask.removeEventListener('click', onMaskClick);
            resolve(val);
          };
          const onOk = () => cleanup(true);
          const onCancel = () => cleanup(false);
          const onMaskClick = (e) => { if (e.target.id === 'edFromUrlConfirmMask') cleanup(false); };
          okBtn.addEventListener('click', onOk);
          cancelBtn.addEventListener('click', onCancel);
          if (closeBtn) closeBtn.addEventListener('click', onCancel);
          mask.addEventListener('click', onMaskClick);
        });
      }

      edFromUrlBtn.addEventListener('click', async () => {
        const pid = edState.productData && edState.productData.product && edState.productData.product.id;
        if (!pid) return;
        edFlushProductUrl();
        const lang = edState.activeLang;
        const links = (edState.productData.product.localized_links) || {};
        const override = links[lang];
        const code = ($('edCode').value || '').trim();
        const def = _defaultProductUrl(lang, code);
        const url = override || def;
        if (!url) { alert('请先填写产品 ID 或产品链接'); return; }

        const ctrl = edDetailImagesCtrl;
        const existingCount = ctrl && ctrl.items ? ctrl.items().length : 0;
        let clearExisting = false;
        if (existingCount > 0) {
          const ok = await awaitFromUrlConfirm(existingCount, lang);
          if (!ok) return;
          clearExisting = true;
        }

        openFromUrlModal();
        try {
          const resp = await fetch(`/medias/api/products/${pid}/detail-images/from-url`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url, lang, clear_existing: clearExisting }),
          });
          const data = await resp.json();
          if (!resp.ok) {
            renderFromUrlProgress({
              status: 'failed',
              error: data.error || ('HTTP ' + resp.status),
              message: data.error || ('启动失败：HTTP ' + resp.status),
            });
            return;
          }
          pollFromUrlTask(pid, data.task_id);
        } catch (e) {
          renderFromUrlProgress({
            status: 'failed',
            error: e.message || String(e),
            message: '网络错误：' + (e.message || e),
          });
        }
      });
    }

    if (edDownloadZipBtn) {
      edDownloadZipBtn.addEventListener('click', () => {
        const pid = edState.productData && edState.productData.product && edState.productData.product.id;
        const lang = (edState.activeLang || 'en').trim().toLowerCase();
        if (!pid || edDownloadZipBtn.disabled) return;
        window.location.href = `/medias/api/products/${pid}/detail-images/download-zip?lang=${encodeURIComponent(lang)}&kind=image`;
      });
    }

    const edDownloadProductImagesBtn = $('edDownloadProductImagesBtn');
    if (edDownloadProductImagesBtn) {
      edDownloadProductImagesBtn.addEventListener('click', () => {
        const pid = edState.productData && edState.productData.product && edState.productData.product.id;
        if (!pid || edDownloadProductImagesBtn.disabled) return;
        window.location.href = `/medias/api/products/${pid}/detail-images/download-localized-zip`;
      });
    }

    const edGifDownloadZipBtn = $('edDetailGifImagesDownloadZipBtn');
    if (edGifDownloadZipBtn) {
      edGifDownloadZipBtn.addEventListener('click', () => {
        const pid = edState.productData && edState.productData.product && edState.productData.product.id;
        const lang = (edState.activeLang || 'en').trim().toLowerCase();
        if (!pid || edGifDownloadZipBtn.disabled) return;
        window.location.href = `/medias/api/products/${pid}/detail-images/download-zip?lang=${encodeURIComponent(lang)}&kind=gif`;
      });
    }

    // edCwAddBtn：按当前 activeLang 添加文案条目
    $('edDetailImagesTranslateBtn') && $('edDetailImagesTranslateBtn').addEventListener('click', () => {
      edStartDetailTranslate().catch((err) => {
        console.error('[detail-images] start translate failed:', err);
      });
    });
    $('edDetailImagesTranslateHeaderBtn') && $('edDetailImagesTranslateHeaderBtn').addEventListener('click', () => {
      edStartDetailTranslate().catch((err) => {
        console.error('[detail-images] start translate (header) failed:', err);
      });
    });
    $('edDetailImagesClearAllBtn') && $('edDetailImagesClearAllBtn').addEventListener('click', async () => {
      const pid = edState.productData && edState.productData.product && edState.productData.product.id;
      const lang = (edState.activeLang || '').trim().toLowerCase();
      if (!pid || !lang || lang === 'en') return;
      const ctrl = edDetailImagesCtrl;
      const count = ctrl && ctrl.items ? ctrl.items().length : 0;
      if (!count) return;
      const ok = window.confirm(
        `确定清空当前【${langDisplayName(lang)}】语种下全部 ${count} 张详情图？该操作不可撤销。`
      );
      if (!ok) return;
      const btn = $('edDetailImagesClearAllBtn');
      const orig = btn ? btn.textContent : '';
      if (btn) { btn.disabled = true; btn.textContent = '清空中...'; }
      try {
        await fetchJSON(`/medias/api/products/${pid}/detail-images/clear`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ lang }),
        });
        await edRefreshDetailImagesPanel(lang);
      } catch (err) {
        alert('清空失败：' + (err.message || err));
      } finally {
        if (btn) { btn.disabled = false; btn.textContent = orig || '一键清空'; }
      }
    });
    $('edDetailTranslateStartBtn') && $('edDetailTranslateStartBtn').addEventListener('click', () => {
      edSubmitDetailTranslate().catch((err) => {
        console.error('[detail-images] submit translate failed:', err);
      });
    });
    $('edDetailTranslateCancelBtn') && $('edDetailTranslateCancelBtn').addEventListener('click', edCloseDetailTranslateTaskModal);
    $('edDetailTranslateTaskClose') && $('edDetailTranslateTaskClose').addEventListener('click', edCloseDetailTranslateTaskModal);
    $('edDetailTranslateTaskMask') && $('edDetailTranslateTaskMask').addEventListener('click', (e) => {
      if (e.target.id === 'edDetailTranslateTaskMask') edCloseDetailTranslateTaskModal();
    });
    $('edDetailTranslateModeGroup') && $('edDetailTranslateModeGroup').addEventListener('click', (ev) => {
      const chip = ev.target.closest('.oc-chip');
      if (!chip) return;
      $('edDetailTranslateModeGroup').querySelectorAll('.oc-chip').forEach((c) => {
        const active = c === chip;
        c.classList.toggle('on', active);
        c.setAttribute('aria-checked', active ? 'true' : 'false');
      });
    });
    $('edDetailTranslateHistory') && $('edDetailTranslateHistory').addEventListener('click', (e) => {
      const btn = e.target && e.target.closest('[data-retranslate-lang]');
      if (!btn) return;
      edStartDetailTranslate(btn.getAttribute('data-retranslate-lang') || edState.activeLang).catch((err) => {
        console.error('[detail-images] retranslate failed:', err);
      });
    });
    $('edDetailTranslateStatus') && $('edDetailTranslateStatus').addEventListener('click', async (e) => {
      const btn = e.target && e.target.closest('[data-apply-translate-task]');
      if (!btn) return;
      const taskId = btn.getAttribute('data-apply-translate-task') || '';
      const lang = (btn.getAttribute('data-apply-translate-lang') || edState.activeLang || '').trim().toLowerCase();
      const pid = edState.productData && edState.productData.product && edState.productData.product.id;
      if (!pid || !taskId || !lang) return;
      const orig = btn.textContent;
      btn.disabled = true;
      btn.textContent = '回填中...';
      try {
        const data = await fetchJSON(
          `/medias/api/products/${pid}/detail-images/${encodeURIComponent(lang)}/apply-translate-task/${encodeURIComponent(taskId)}`,
          { method: 'POST' },
        );
        const msg = data.skipped_failed > 0
          ? `已回填 ${data.applied} 张（忽略 ${data.skipped_failed} 张失败）`
          : `已回填 ${data.applied} 张`;
        alert(msg);
        await edRefreshDetailImagesPanel(lang);
      } catch (err) {
        alert('手动回填失败：' + (err && err.message ? err.message : err));
        btn.disabled = false;
        btn.textContent = orig;
      }
    });

    $('edCwAddBtn').addEventListener('click', () => {
      $('edCwList').appendChild(edCwCard({ lang: edState.activeLang }, $('edCwList').children.length + 1));
      $('edCwBadge').textContent = $('edCwList').children.length;
    });
    // 编辑弹窗封面事件由 edRenderCoverBlock() 动态绑定，此处不再静态绑定

    // ===== 新增素材大框：视频封面图 =====
    const edIcDz = $('edItemCoverDropzone');
    if (edIcDz) {
      edIcDz.addEventListener('click', () => $('edItemCoverInput').click());
      edIcDz.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $('edItemCoverInput').click(); }
      });
      edIcDz.addEventListener('dragover', (e) => { e.preventDefault(); edIcDz.classList.add('drag'); });
      edIcDz.addEventListener('dragleave', () => edIcDz.classList.remove('drag'));
      edIcDz.addEventListener('drop', (e) => {
        e.preventDefault(); edIcDz.classList.remove('drag');
        const f = [...(e.dataTransfer.files || [])].find(x => x.type.startsWith('image/'));
        if (f) edUploadPendingItemCover(f);
      });
    }
    $('edItemCoverReplace') && $('edItemCoverReplace').addEventListener('click', () => $('edItemCoverInput').click());
    $('edItemCoverClear') && $('edItemCoverClear').addEventListener('click', () => {
      edState.pendingItemCover = null;
      edSetItemCover(null);
    });
    $('edItemCoverInput') && $('edItemCoverInput').addEventListener('change', (e) => {
      const f = e.target.files[0]; e.target.value = '';
      if (f) edUploadPendingItemCover(f);
    });
    $('edItemCoverFromUrlBtn') && $('edItemCoverFromUrlBtn').addEventListener('click', edImportItemCoverFromUrl);
    $('edItemCoverUrl') && $('edItemCoverUrl').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); edImportItemCoverFromUrl(); }
    });

    // ===== 新增素材大框：视频源（只选不传） =====
    const edVpBox = $('edVideoPickBox');
    if (edVpBox) {
      edVpBox.addEventListener('click', (e) => {
        // 点 "清空" 按钮时不触发 file picker
        if (e.target && e.target.closest('#edVideoPickClear')) return;
        $('edVideoInput').click();
      });
      edVpBox.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $('edVideoInput').click(); }
      });
      edVpBox.addEventListener('dragover', (e) => { e.preventDefault(); edVpBox.classList.add('drag'); });
      edVpBox.addEventListener('dragleave', () => edVpBox.classList.remove('drag'));
      edVpBox.addEventListener('drop', (e) => {
        e.preventDefault(); edVpBox.classList.remove('drag');
        const file = [...(e.dataTransfer.files || [])]
          .find(f => f.type.startsWith('video/') || /\.(mp4|mov|webm|mkv)$/i.test(f.name));
        if (file) edSetPickedVideo(file);
      });
    }
    $('edVideoInput') && $('edVideoInput').addEventListener('change', (e) => {
      const f = e.target.files[0]; e.target.value = '';
      if (f) edSetPickedVideo(f);
    });
    $('edVideoPickClear') && $('edVideoPickClear').addEventListener('click', (e) => {
      e.stopPropagation();
      edSetPickedVideo(null);
    });

    // ===== 新增素材大框：提交按钮 =====
    $('edItemSubmitBtn') && $('edItemSubmitBtn').addEventListener('click', edSubmitNewItem);

    loadList();
  });
})();

(function () {
  const $ = (id) => document.getElementById(id);
  const modalMask = $('rsModalMask');
  const modalClose = $('rsModalClose');
  const summary = $('rsSummary');
  const list = $('rsList');
  const uploadMask = $('rsUploadMask');
  const uploadForm = $('rsUploadForm');
  const uploadSubmit = $('rsUploadSubmit');
  const uploadVideoInput = $('rsVideoInput');
  const uploadCoverInput = $('rsCoverInput');
  const uploadNameInput = $('rsDisplayName');
  const uploadCoverBox = $('rsUploadCoverBox');
  const uploadCoverPreview = $('rsUploadCoverPreview');
  const uploadVideoBox = $('rsUploadVideoBox');
  const uploadVideoEmpty = $('rsUploadVideoEmpty');
  const uploadVideoFilled = $('rsUploadVideoFilled');
  const uploadVideoName = $('rsUploadVideoName');
  const uploadVideoSize = $('rsUploadVideoSize');
  const nameHelpMask = $('rsNameHelpMask');
  const nameHelpMessage = $('rsNameHelpMessage');
  const nameHelpList = $('rsNameHelpList');
  const translateMask = $('rsTranslateMask');
  const translateTitleMeta = $('rstTitleMeta');
  const translateRsList = $('rstRsList');
  const translateLangs = $('rstLangs');
  const translatePreview = $('rstPreview');
  const translateSubmit = $('rstSubmit');
  const uiState = {
    currentPid: null,
    currentName: '',
    translatePid: null,
    translateName: '',
  };
  let rawSourceCoverObjectUrl = '';

  if (!modalMask || !modalClose || !list || !uploadMask || !uploadForm || !uploadSubmit || !uploadVideoInput || !uploadCoverInput || !uploadNameInput || !uploadCoverBox || !uploadCoverPreview || !uploadVideoBox || !uploadVideoEmpty || !uploadVideoFilled || !uploadVideoName || !uploadVideoSize || !nameHelpMask || !nameHelpMessage || !nameHelpList || !translateMask || !translateRsList || !translateLangs || !translatePreview || !translateSubmit) {
    return;
  }

  uploadNameInput.maxLength = 128;
  uploadNameInput.placeholder = '默认读取所选视频文件名，可按需修改';

  function escapeHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, (ch) => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;',
    }[ch]));
  }

  function fmtRawDuration(seconds) {
    const value = Number(seconds || 0);
    if (!value) return '时长 —';
    const mins = value / 60;
    if (mins >= 60) return `时长 ${(mins / 60).toFixed(1)}h`;
    return `时长 ${mins.toFixed(1)}m`;
  }

  function fmtRawSize(bytes) {
    const value = Number(bytes || 0);
    if (!value) return '大小 -';
    return `大小 ${(value / (1024 * 1024)).toFixed(1)} MB`;
  }

  function fmtUploadSize(bytes) {
    const value = Number(bytes || 0);
    if (!value && value !== 0) return '';
    if (value < 1024) return `${value} B`;
    if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
    if (value < 1024 * 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`;
    return `${(value / 1024 / 1024 / 1024).toFixed(2)} GB`;
  }

  function rawSourceLangDisplayName(lang) {
    const code = String((lang && lang.code) || '').trim();
    const nameZh = String((lang && lang.name_zh) || '').trim();
    if (nameZh && code) return `${nameZh} (${code})`;
    return nameZh || code || '';
  }

  function isRawSourceVideoFile(file) {
    if (!file) return false;
    const type = String(file.type || '').toLowerCase();
    if (type === 'video/mp4' || type === 'video/quicktime') return true;
    return /\.(mp4|mov)$/i.test(file.name || '');
  }

  function isRawSourceCoverFile(file) {
    if (!file) return false;
    const type = String(file.type || '').toLowerCase();
    if (['image/jpeg', 'image/png', 'image/webp', 'image/gif'].includes(type)) return true;
    return /\.(jpe?g|png|webp|gif)$/i.test(file.name || '');
  }

  async function requestJSON(url, options) {
    const resp = await fetch(url, options);
    if (resp.ok) return resp.json();
    const err = await resp.json().catch(() => ({}));
    const error = new Error(err.message || err.error || `${resp.status}`);
    Object.assign(error, err || {});
    error.status = resp.status;
    throw error;
  }

  function syncRawSourceCount(pid, count) {
    document.querySelectorAll(`.js-raw-sources[data-pid="${pid}"]`).forEach((btn) => {
      btn.textContent = `原始视频 (${count})`;
    });
  }

  function normalizeRawSourceTitle(value) {
    return String(value ?? '').replace(/\s+/g, ' ').trim();
  }

  function getRawSourceTitleExample(productName) {
    const d = new Date();
    const pad = (n) => String(n).padStart(2, '0');
    const datePart = `${d.getFullYear()}.${pad(d.getMonth() + 1)}.${pad(d.getDate())}`;
    const pn = normalizeRawSourceTitle(productName) || '产品名';
    return `${datePart}-${pn}-原始视频.mp4`;
  }

  function validateRawSourceDisplayName(title, productName) {
    const value = normalizeRawSourceTitle(title);
    const pn = normalizeRawSourceTitle(productName);
    const errors = [];
    if (!pn) return ['当前产品尚未加载，请重试'];
    if (!value) return ['名称不能为空，格式为 YYYY.MM.DD-产品名-xxxxxx.mp4'];

    if (!value.toLowerCase().endsWith('.mp4')) {
      errors.push('名称必须以 .mp4 结尾');
    }

    if (value.length < 11 || value[10] !== '-') {
      errors.push('名称必须以 "YYYY.MM.DD-" 开头');
      return errors;
    }

    const dateStr = value.slice(0, 10);
    const m = /^(\d{4})\.(\d{2})\.(\d{2})$/.exec(dateStr);
    if (!m) {
      errors.push(`日期段 "${dateStr}" 格式必须是 YYYY.MM.DD`);
    } else {
      const y = Number(m[1]);
      const mo = Number(m[2]);
      const day = Number(m[3]);
      const parsed = new Date(y, mo - 1, day);
      if (parsed.getFullYear() !== y || parsed.getMonth() !== mo - 1 || parsed.getDate() !== day) {
        errors.push(`日期 "${dateStr}" 不是合法日期`);
      }
    }

    const expectedPrefix = `${dateStr}-${pn}-`;
    if (!value.startsWith(expectedPrefix)) {
      errors.push(`日期之后必须紧跟 "${pn}-"`);
      return errors;
    }

    let tail = value.slice(expectedPrefix.length);
    if (tail.toLowerCase().endsWith('.mp4')) {
      tail = tail.slice(0, -4);
    }
    if (!tail.trim()) {
      errors.push('产品名之后的描述不能为空');
    }
    return errors;
  }

  function alertRawSourceTitleErrors(errors) {
    const example = getRawSourceTitleExample(uiState.currentName);
    alert([
      '名称不符合原始去字幕视频素材命名规范：',
      ...errors.map((err) => `- ${err}`),
      '',
      '格式：YYYY.MM.DD-产品名-xxxxxx.mp4',
      `示例：${example}`,
    ].join('\n'));
  }

  function getRawSourceDefaultTitle(rid) {
    return `原始视频 #${rid}`;
  }

  function setSummary(items) {
    const name = uiState.currentName || (uiState.currentPid ? `产品 #${uiState.currentPid}` : '当前产品');
    summary.textContent = `${name} · 共 ${items.length} 条素材`;
  }

  function renderRawSourceCard(it) {
    const rawDisplayName = it.display_name || '';
    const defaultTitle = getRawSourceDefaultTitle(it.id);
    const titleText = rawDisplayName || defaultTitle;
    const title = escapeHtml(titleText);
    const coverPane = it.cover_url
      ? `<img src="${escapeHtml(it.cover_url)}" alt="${title}" loading="lazy">`
      : `<div class="thumb-ph"><svg width="20" height="20" aria-hidden="true"><use href="#ic-film"/></svg></div>`;
    return `
      <article
        class="oc-rs-card oc-vitem"
        data-rs-id="${it.id}"
        data-video-url="${escapeHtml(it.video_url || '')}"
        data-display-name="${escapeHtml(rawDisplayName)}"
        data-default-title="${escapeHtml(defaultTitle)}"
      >
        <div class="vname">
          <button type="button" class="oc-rs-title-display js-rs-title-display" title="${title}">${title}</button>
          <textarea
            class="oc-rs-title-input js-rs-title-input"
            rows="2"
            maxlength="128"
            aria-label="编辑原始素材名称"
            hidden
          >${title}</textarea>
        </div>
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

  function getRawSourceCardTitle(card) {
    return card?.dataset.displayName || card?.dataset.defaultTitle || getRawSourceDefaultTitle(card?.dataset.rsId || '');
  }

  function syncRawSourceCardTitle(card, displayName) {
    if (!card) return;
    const nextDisplayName = displayName || '';
    const nextTitle = nextDisplayName || card.dataset.defaultTitle || getRawSourceDefaultTitle(card.dataset.rsId || '');
    const display = card.querySelector('.js-rs-title-display');
    const input = card.querySelector('.js-rs-title-input');
    const coverImage = card.querySelector('[data-pane="cover"] img');
    card.dataset.displayName = nextDisplayName;
    if (display) {
      display.textContent = nextTitle;
      display.title = nextTitle;
    }
    if (input) {
      input.value = nextTitle;
    }
    if (coverImage) {
      coverImage.alt = nextTitle;
    }
  }

  function setRawSourceTitleEditing(card, editing) {
    const display = card?.querySelector('.js-rs-title-display');
    const input = card?.querySelector('.js-rs-title-input');
    if (!display || !input) return;
    display.hidden = !!editing;
    input.hidden = !editing;
    if (editing) {
      input.focus();
      input.setSelectionRange(0, input.value.length);
    }
  }

  function startRawSourceTitleEdit(trigger) {
    const card = trigger?.closest('[data-rs-id]');
    if (!card || card.dataset.saving === '1') return;
    const input = card.querySelector('.js-rs-title-input');
    if (!input) return;
    input.value = getRawSourceCardTitle(card);
    setRawSourceTitleEditing(card, true);
  }

  function cancelRawSourceTitleEdit(card) {
    const input = card?.querySelector('.js-rs-title-input');
    if (!input) return;
    input.value = getRawSourceCardTitle(card);
    setRawSourceTitleEditing(card, false);
  }

  async function saveRawSourceTitle(card) {
    if (!card || card.dataset.saving === '1') return;
    const input = card.querySelector('.js-rs-title-input');
    if (!input) return;
    const rid = card.dataset.rsId;
    const currentDisplayName = card.dataset.displayName || '';
    const defaultTitle = card.dataset.defaultTitle || getRawSourceDefaultTitle(rid);
    const normalized = normalizeRawSourceTitle(input.value);
    const nextDisplayName = (!currentDisplayName && normalized === defaultTitle) ? '' : normalized;
    if (nextDisplayName === currentDisplayName) {
      cancelRawSourceTitleEdit(card);
      return;
    }

    card.dataset.saving = '1';
    input.disabled = true;
    try {
      const data = await requestJSON(`/medias/api/raw-sources/${rid}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ display_name: nextDisplayName }),
      });
      syncRawSourceCardTitle(card, data.item?.display_name || '');
      setRawSourceTitleEditing(card, false);
    } catch (err) {
      alert(`改名失败：${err.message || err}`);
      input.disabled = false;
      input.focus();
      input.setSelectionRange(0, input.value.length);
      return;
    } finally {
      card.dataset.saving = '';
      input.disabled = false;
    }
  }

  function ensureRawSourceVideoLoaded(card) {
    const pane = card.querySelector('[data-pane="video"]');
    if (!pane || pane.dataset.loaded === '1') return;
    const videoUrl = card.dataset.videoUrl || '';
    if (!videoUrl) {
      pane.innerHTML = '<div class="vvideo-ph err">视频地址缺失，请重新上传后重试</div>';
      pane.dataset.loaded = '';
      return;
    }
    pane.innerHTML = '<div class="vvideo-ph">加载视频中...</div>';
    const loading = pane.firstElementChild;
    const video = document.createElement('video');
    video.controls = true;
    video.preload = 'metadata';
    video.src = videoUrl;
    video.addEventListener('loadedmetadata', () => {
      if (loading) loading.remove();
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
      const titleDisplay = card.querySelector('.js-rs-title-display');
      const titleInput = card.querySelector('.js-rs-title-input');
      tabs.forEach((tab) => {
        tab.addEventListener('click', () => {
          tabs.forEach((node) => node.classList.toggle('active', node === tab));
          panes.forEach((pane) => pane.classList.toggle('active', pane.dataset.pane === tab.dataset.tab));
          if (tab.dataset.tab === 'video') ensureRawSourceVideoLoaded(card);
        });
      });
      if (titleDisplay) {
        titleDisplay.addEventListener('click', () => startRawSourceTitleEdit(titleDisplay));
      }
      if (titleInput) {
        titleInput.addEventListener('keydown', async (event) => {
          if (event.key === 'Escape') {
            event.preventDefault();
            cancelRawSourceTitleEdit(card);
            return;
          }
          if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault();
            await saveRawSourceTitle(card);
          }
        });
        titleInput.addEventListener('blur', async () => {
          await saveRawSourceTitle(card);
        });
      }
    });
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
      retryBtn.addEventListener('click', async () => {
        await loadRawSourceList(uiState.currentPid);
      });
    }
  }

  function assignFilesToInput(input, files) {
    if (!input || !files || !files.length) return;
    const transfer = new DataTransfer();
    files.forEach((file) => transfer.items.add(file));
    input.files = transfer.files;
    input.dispatchEvent(new Event('change', { bubbles: true }));
  }

  function setRawSourceUploadCover(file) {
    const coverDropzone = uploadCoverBox.querySelector('.cover-dz');
    if (rawSourceCoverObjectUrl) {
      URL.revokeObjectURL(rawSourceCoverObjectUrl);
      rawSourceCoverObjectUrl = '';
    }
    if (!file) {
      uploadCoverPreview.hidden = true;
      uploadCoverPreview.removeAttribute('src');
      if (coverDropzone) coverDropzone.hidden = false;
      return;
    }
    rawSourceCoverObjectUrl = URL.createObjectURL(file);
    uploadCoverPreview.src = rawSourceCoverObjectUrl;
    uploadCoverPreview.hidden = false;
    if (coverDropzone) coverDropzone.hidden = true;
  }

  function setRawSourceUploadVideo(file) {
    if (file) {
      uploadVideoEmpty.hidden = true;
      uploadVideoFilled.hidden = false;
      uploadVideoName.textContent = file.name;
      uploadVideoSize.textContent = fmtUploadSize(file.size);
      uploadNameInput.value = file.name;
      return;
    }
    uploadVideoEmpty.hidden = false;
    uploadVideoFilled.hidden = true;
    uploadVideoName.textContent = '';
    uploadVideoSize.textContent = '';
    uploadNameInput.value = '';
  }

  function bindRawSourceUploadDropzone(box, input, acceptFile) {
    if (!box || !input) return;
    box.addEventListener('click', (event) => {
      if (event.target.closest('label')) return;
      const activeControl = event.target.closest('button, input, a');
      if (activeControl) return;
      input.click();
    });
    box.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        input.click();
      }
    });
    box.addEventListener('dragover', (event) => {
      event.preventDefault();
      box.classList.add('drag');
    });
    box.addEventListener('dragleave', () => box.classList.remove('drag'));
    box.addEventListener('drop', (event) => {
      event.preventDefault();
      box.classList.remove('drag');
      const file = [...(event.dataTransfer.files || [])].find(acceptFile);
      if (file) assignFilesToInput(input, [file]);
    });
  }

  function openRawSourceModal(pid, name) {
    uiState.currentPid = String(pid);
    uiState.currentName = name || '';
    summary.textContent = '加载中';
    renderRawSourceState('素材列表加载中...');
    modalMask.hidden = false;
  }

  function closeRawSourceModal() {
    modalMask.hidden = true;
    uiState.currentPid = null;
    uiState.currentName = '';
    list.innerHTML = '';
    summary.textContent = '加载中';
  }

  async function loadRawSourceList(pid) {
    summary.textContent = '加载中';
    renderRawSourceState('素材列表加载中...');
    try {
      return await refreshRawSourceList(pid);
    } catch (err) {
      renderRawSourceState(`加载失败：${err.message || err}`, 'error');
      summary.textContent = '素材列表加载失败';
      return null;
    }
  }

  function openRawSourceUpload() {
    if (!window.MEDIAS_UPLOAD_READY) {
      alert('本地上传未就绪，无法上传原始素材');
      return;
    }
    uploadMask.hidden = false;
    uploadVideoBox.focus();
  }

  function closeRawSourceUpload() {
    uploadMask.hidden = true;
    uploadForm.reset();
    uploadSubmit.disabled = false;
    setRawSourceUploadCover(null);
    setRawSourceUploadVideo(null);
    closeRawSourceFilenameHelp();
  }

  function closeRawSourceFilenameHelp() {
    nameHelpMask.hidden = true;
    nameHelpMessage.textContent = '';
    nameHelpList.innerHTML = '';
  }

  function openRawSourceFilenameHelp(payload) {
    const uploadedFilename = String(payload?.uploaded_filename || '').trim();
    const englishFilenames = Array.isArray(payload?.english_filenames) ? payload.english_filenames : [];
    if (!englishFilenames.length) {
      nameHelpMessage.textContent = uploadedFilename
        ? `当前上传文件「${uploadedFilename}」无法提交，因为该产品还没有英语视频。请先补英语视频。`
        : '当前产品还没有英语视频。请先补英语视频后再上传原始视频。';
      nameHelpList.innerHTML = '';
      nameHelpMask.hidden = false;
      return;
    }
    nameHelpMessage.textContent = uploadedFilename
      ? `当前上传文件名「${uploadedFilename}」没有命中现有英语视频。请把本地视频重命名为下面任一英语文件名后重新提交。`
      : '请把本地视频重命名为下面任一英语文件名后重新提交。';
    nameHelpList.innerHTML = englishFilenames.map((filename) => `
      <li class="oc-rst-choice">
        <div class="oc-rst-choice-row">
          <div class="oc-rst-choice-meta">
            <strong title="${escapeHtml(filename)}">${escapeHtml(filename)}</strong>
          </div>
          <button
            type="button"
            class="oc-btn ghost sm js-rs-name-copy"
            data-filename="${escapeHtml(filename)}"
            data-copy-label="复制"
          >复制</button>
        </div>
      </li>
    `).join('');
    nameHelpMask.hidden = false;
  }

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

  async function submitRawSourceUpload(event) {
    event.preventDefault();
    if (!uiState.currentPid) return;
    const fd = new FormData(uploadForm);
    uploadSubmit.disabled = true;
    try {
      await requestJSON(`/medias/api/products/${uiState.currentPid}/raw-sources`, {
        method: 'POST',
        body: fd,
      });
      closeRawSourceUpload();
      await refreshRawSourceList(uiState.currentPid);
    } catch (err) {
      if (err?.error === 'raw_source_filename_mismatch' || err?.error === 'english_video_required') {
        openRawSourceFilenameHelp(err);
      } else {
        alert(`上传失败：${err.message || err}`);
      }
      uploadSubmit.disabled = false;
    }
  }

  async function deleteRawSource(del) {
    if (!uiState.currentPid) return;
    if (!confirm('删除后无法恢复，该素材不会再出现在翻译弹窗，但已翻译出来的多语种素材不受影响。确定？')) {
      return;
    }
    try {
      await requestJSON(`/medias/api/raw-sources/${del.dataset.rid}`, { method: 'DELETE' });
      await refreshRawSourceList(uiState.currentPid);
    } catch (err) {
      alert(`删除失败：${err.message || err}`);
    }
  }

  function renderTranslateRawSourceChoice(it) {
    const title = escapeHtml(it.display_name || `原始视频 #${it.id}`);
    const inputId = `rst-rs-${it.id}`;
    const videoUrl = escapeHtml(it.video_url || '');
    const poster = it.cover_url ? ` poster="${escapeHtml(it.cover_url)}"` : '';
    const preview = videoUrl
      ? `<span class="oc-rst-choice-preview"><video class="oc-rst-choice-video" src="${videoUrl}"${poster} controls playsinline preload="metadata" aria-label="${title}"></video></span>`
      : `<span class="oc-rst-choice-preview"><span class="ph"><svg width="20" height="20" aria-hidden="true"><use href="#ic-film"/></svg></span></span>`;
    return `
      <li class="oc-rst-choice">
        <div class="oc-rst-choice-row">
          <label class="oc-rst-choice-check" for="${inputId}">
            <input id="${inputId}" type="checkbox" value="${it.id}" aria-label="选择 ${title}" checked>
          </label>
          ${preview}
          <label class="oc-rst-choice-meta" for="${inputId}">
            <span class="oc-rst-choice-title" title="${title}">${title}</span>
            <span class="oc-rst-choice-subtitle">${fmtRawDuration(it.duration_seconds)} · ${fmtRawSize(it.file_size)}</span>
          </label>
        </div>
      </li>`;
  }

  function renderTranslateLanguageChoice(lang) {
    const name = escapeHtml(rawSourceLangDisplayName(lang));
    return `
      <label class="oc-rst-lang">
        <input type="checkbox" value="${escapeHtml(lang.code)}">
        <span>${name}</span>
      </label>`;
  }

  function updateTranslatePreview() {
    const rawCount = translateRsList.querySelectorAll('input[type="checkbox"]:checked').length;
    const langCount = translateLangs.querySelectorAll('input[type="checkbox"]:checked').length;
    if (!rawCount || !langCount) {
      translatePreview.textContent = '请选择至少 1 条原始视频和 1 个目标语言';
      translateSubmit.disabled = true;
      return;
    }
    translatePreview.textContent = `将生成 ${rawCount} × ${langCount} = ${rawCount * langCount} 条多语种素材`;
    translateSubmit.disabled = false;
  }

  function closeTranslateDialog() {
    translateMask.hidden = true;
    uiState.translatePid = null;
    uiState.translateName = '';
    translateRsList.innerHTML = '';
    translateLangs.innerHTML = '';
    translateTitleMeta.textContent = '';
    translatePreview.textContent = '请选择原始视频和目标语言';
    translateSubmit.disabled = true;
    translateSubmit.textContent = '提交翻译';
  }

  async function openTranslateDialog(pid, name) {
    uiState.translatePid = String(pid);
    uiState.translateName = name || '';
    translateMask.hidden = false;
    translateTitleMeta.textContent = uiState.translateName ? ` · ${uiState.translateName}` : '';
    translateRsList.innerHTML = '<li class="oc-rs-empty">加载原始视频中…</li>';
    translateLangs.innerHTML = '<div class="oc-rs-empty">加载语言中…</div>';
    translatePreview.textContent = '加载中…';
    translateSubmit.disabled = true;

    try {
      const [rawData, langData] = await Promise.all([
        requestJSON(`/medias/api/products/${pid}/raw-sources`),
        requestJSON('/medias/api/languages'),
      ]);
      const items = rawData.items || [];
      const languages = (langData.items || langData.languages || []).filter((lang) => lang.code !== 'en');

      translateRsList.innerHTML = items.length
        ? items.map(renderTranslateRawSourceChoice).join('')
        : '<li class="oc-rs-empty">还没有原始去字幕素材，请先上传素材。</li>';
      translateLangs.innerHTML = languages.length
        ? languages.map(renderTranslateLanguageChoice).join('')
        : '<div class="oc-rs-empty">暂无可选目标语言</div>';
      updateTranslatePreview();
    } catch (err) {
      translateRsList.innerHTML = `<li class="oc-rs-empty">加载失败：${escapeHtml(err.message || err)}</li>`;
      translateLangs.innerHTML = '<div class="oc-rs-empty">请稍后重试</div>';
      translatePreview.textContent = '翻译弹窗初始化失败';
      translateSubmit.disabled = true;
    }
  }

  async function submitTranslateTask() {
    const pid = uiState.translatePid;
    if (!pid) return;
    const raw_ids = Array.from(
      translateRsList.querySelectorAll('input[type="checkbox"]:checked'),
      (input) => Number(input.value),
    );
    const target_langs = Array.from(
      translateLangs.querySelectorAll('input[type="checkbox"]:checked'),
      (input) => input.value,
    );
    if (!raw_ids.length || !target_langs.length) {
      updateTranslatePreview();
      return;
    }

    translateSubmit.disabled = true;
    translateSubmit.textContent = '提交中…';
    try {
      const data = await requestJSON(`/medias/api/products/${pid}/translate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ raw_ids, target_langs }),
      });
      const taskId = data.task_id;
      closeTranslateDialog();
      window.open(`/tasks/${taskId}`, '_blank', 'noopener,noreferrer');
    } catch (err) {
      alert(`提交失败：${err.message || err}`);
      translateSubmit.textContent = '提交翻译';
      updateTranslatePreview();
    }
  }

  translateRsList.addEventListener('change', updateTranslatePreview);
  translateLangs.addEventListener('change', updateTranslatePreview);
  uploadCoverInput.addEventListener('change', (event) => {
    const file = event.target.files[0] || null;
    if (file && !isRawSourceCoverFile(file)) {
      alert('仅支持 JPG / PNG / WebP / GIF 图片');
      uploadCoverInput.value = '';
      setRawSourceUploadCover(null);
      return;
    }
    setRawSourceUploadCover(file);
  });
  uploadVideoInput.addEventListener('change', (event) => {
    const file = event.target.files[0] || null;
    if (file && !isRawSourceVideoFile(file)) {
      alert('仅支持 MP4 / MOV 视频');
      uploadVideoInput.value = '';
      setRawSourceUploadVideo(null);
      return;
    }
    setRawSourceUploadVideo(file);
  });
  bindRawSourceUploadDropzone(uploadCoverBox, uploadCoverInput, isRawSourceCoverFile);
  bindRawSourceUploadDropzone(
    uploadVideoBox,
    uploadVideoInput,
    isRawSourceVideoFile,
  );
  uploadForm.addEventListener('submit', submitRawSourceUpload);
  translateSubmit.addEventListener('click', submitTranslateTask);

  document.addEventListener('click', async (event) => {
    const openBtn = event.target.closest('.js-raw-sources');
    if (openBtn) {
      event.preventDefault();
      openRawSourceModal(openBtn.dataset.pid, openBtn.dataset.name || '');
      await loadRawSourceList(openBtn.dataset.pid);
      return;
    }

    const translateBtn = event.target.closest('.js-translate');
    if (translateBtn) {
      event.preventDefault();
      if (translateBtn.disabled || translateBtn.getAttribute('aria-disabled') === 'true') return;
      await openTranslateDialog(translateBtn.dataset.pid, translateBtn.dataset.name || '');
      return;
    }

    if (event.target === modalMask || event.target.closest('#rsModalClose')) {
      closeRawSourceModal();
      return;
    }

    if (event.target === uploadMask || event.target.closest('#rsUploadClose') || event.target.closest('#rsUploadCancel')) {
      closeRawSourceUpload();
      return;
    }

    if (event.target === nameHelpMask || event.target.closest('#rsNameHelpClose') || event.target.closest('#rsNameHelpCancel')) {
      closeRawSourceFilenameHelp();
      return;
    }

    if (event.target === translateMask || event.target.closest('#rstClose') || event.target.closest('#rstCancel')) {
      closeTranslateDialog();
      return;
    }

    const del = event.target.closest('.js-rs-del');
    if (del) {
      event.preventDefault();
      await deleteRawSource(del);
      return;
    }

    const copyBtn = event.target.closest('.js-rs-name-copy');
    if (copyBtn) {
      event.preventDefault();
      copyText(copyBtn.dataset.filename || '')
        .then(() => flashCopiedButton(copyBtn))
        .catch(() => alert('复制失败，请手动复制'));
      return;
    }

    if (event.target.closest('#rsUploadBtn')) {
      event.preventDefault();
      openRawSourceUpload();
    }
  });

  window.MediasRawSources = {
    escapeHtml,
    refreshRawSourceList,
    syncRawSourceCount,
    openTranslateDialog,
  };
})();
