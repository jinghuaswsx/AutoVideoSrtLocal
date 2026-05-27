(function () {
  const state = {
    currentTask: null,
    pollTimer: null,
    consecutivePollFailures: 0,
  };

  const MAX_POLL_FAILURES = 3;
  const TERMINAL_STATUSES = new Set(["done", "failed", "review_ready", "deleted"]);

  const LINK_CHECK_STATUS_LABELS = {
    queued: '排队中',
    locking_locale: '锁定目标语种页面',
    downloading: '下载图片中',
    analyzing: '分析图片中',
    review_ready: '待复核',
    done: '已完成',
    failed: '失败',
    deleted: '已删除',
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

  function $(id) {
    return document.getElementById(id);
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function safeMediaSrc(url) {
    const raw = String(url == null ? "" : url).trim();
    if (!raw) return "";
    try {
      const parsed = new URL(raw, window.location.origin);
      if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return "";
      if (/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(raw)) return parsed.href;
      return parsed.pathname + parsed.search + parsed.hash;
    } catch (_) {
      return "";
    }
  }

  function setStatus(text) {
    const node = $("linkCheckStatus");
    if (node) {
      node.textContent = text;
    }
  }

  function showError(message) {
    const node = $("linkCheckError");
    if (!node) {
      return;
    }
    if (!message) {
      node.hidden = true;
      node.textContent = "";
      return;
    }
    node.hidden = false;
    node.textContent = message;
  }

  async function fetchJSON(url, options) {
    const response = await fetch(url, options);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.error || "请求失败");
    }
    return payload;
  }

  function getBootstrappedTask() {
    if (window.__LINK_CHECK_TASK__ && typeof window.__LINK_CHECK_TASK__ === "object") {
      return window.__LINK_CHECK_TASK__;
    }

    const node = $("linkCheckInitialTask");
    if (!node || !node.textContent) {
      return null;
    }

    try {
      return JSON.parse(node.textContent);
    } catch (_error) {
      return null;
    }
  }

  function formatPercent(value) {
    if (typeof value !== "number" || Number.isNaN(value)) {
      return "-";
    }
    return `${(value * 100).toFixed(1)}%`;
  }

  function formatValue(value) {
    if (value == null || value === "") {
      return "-";
    }
    return value;
  }

  let LANGUAGES = [];
  async function ensureLanguages() {
    if (LANGUAGES.length) return LANGUAGES;
    try {
      const data = await fetchJSON('/medias/api/languages');
      LANGUAGES = data.items || [];
    } catch (e) {
      console.warn("Failed to load languages:", e);
      LANGUAGES = [];
    }
    return LANGUAGES;
  }

  function langDisplayName(code) {
    const raw = String(code || '').trim();
    const normalized = raw.toLowerCase();
    if (!normalized) return '';
    const l = (LANGUAGES || []).find(x => x && x.code === normalized);
    const upper = normalized.toUpperCase();
    if (l && l.name_zh) return `${l.name_zh} (${upper})`;
    return upper || raw;
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

  function edLinkCheckWorkflowHtml(item, task) {
    const analysis = item.analysis || {};
    const ref = item.reference_match || {};
    const binary = item.binary_quick_check || {};
    const sameImage = item.same_image_llm || {};
    const decision = analysis.decision || '';

    // Step 1: Reference Match
    let s1 = { color: 'var(--oc-text-mute)', status: '⏭️ 跳过匹配', detail: '未提供或无备选详情图，默认流向大模型OCR审计' };
    if (ref.status === 'matched') {
      const scorePct = typeof ref.score === 'number' ? Math.round(ref.score * 100) : 0;
      s1 = {
        color: 'var(--oc-success-fg)',
        status: '✅ 匹配成功',
        detail: `与黄金图相似度达 ${scorePct}%` + (ref.ssim ? ` (SSIM: ${ref.ssim})` : '')
      };
    } else if (ref.status === 'weak_match') {
      const scorePct = typeof ref.score === 'number' ? Math.round(ref.score * 100) : 0;
      s1 = {
        color: 'var(--oc-warning-fg)',
        status: '⚠️ 疑似度较低',
        detail: `与黄金图相似度 ${scorePct}% (低于 80% 阈值)`
      };
    } else if (ref.status === 'not_matched') {
      const scorePct = typeof ref.score === 'number' ? Math.round(ref.score * 100) : 0;
      s1 = {
        color: 'var(--oc-danger-fg)',
        status: '❌ 特征不匹配',
        detail: `最高相似度仅 ${scorePct}% (低于限度，判定未替换，后续步骤跳过)`
      };
    }

    // Step 2: Binary Fast Match
    let s2 = { color: 'var(--oc-text-mute)', status: '⏭️ 跳过快检', detail: binary.reason || '无可用匹配黄金图' };
    if (binary.status === 'pass') {
      const simPct = typeof binary.binary_similarity === 'number' ? Math.round(binary.binary_similarity * 100) : 0;
      const overlapPct = typeof binary.foreground_overlap === 'number' ? Math.round(binary.foreground_overlap * 100) : 0;
      s2 = {
        color: 'var(--oc-success-fg)',
        status: '✅ 快检通过',
        detail: `二值相似度 ${simPct}%，前景重合 ${overlapPct}%`
      };
    } else if (binary.status === 'fail') {
      const overlapPct = typeof binary.foreground_overlap === 'number' ? Math.round(binary.foreground_overlap * 100) : 0;
      s2 = {
        color: 'var(--oc-danger-fg)',
        status: '❌ 快检未通过',
        detail: `前景重合 ${overlapPct}% 低于阈值 (90%)`
      };
    } else if (binary.status === 'error') {
      s2 = {
        color: 'var(--oc-warning-fg)',
        status: '⚠️ 快检异常',
        detail: binary.reason || '快检执行失败'
      };
    }

    // Step 3: Same Image LLM
    let s3 = { color: 'var(--oc-text-mute)', status: '⏭️ 跳过判定', detail: sameImage.reason || '无需判定' };
    if (sameImage.status === 'done') {
      const isSame = sameImage.answer === '是';
      s3 = {
        color: isSame ? 'var(--oc-success-fg)' : 'var(--oc-danger-fg)',
        status: isSame ? '✅ 判定一致' : '❌ 判定不一致',
        detail: isSame ? '底图判定为同一张图' : '底图判定为不同图片'
      };
    } else if (sameImage.status === 'error') {
      s3 = {
        color: 'var(--oc-warning-fg)',
        status: '⚠️ 判定异常',
        detail: sameImage.reason || '大模型比对异常'
      };
    }

    // Step 4: Final Verdict
    let s4 = { color: 'var(--oc-text-mute)', status: '⚖️ 综合研判', detail: '等待判定结论' };
    const src = analysis.decision_source;
    if (src === 'green_pass') {
      s4 = {
        color: 'var(--oc-success-fg)',
        status: '✅ 绿色免检放行',
        detail: '网页图片与后台翻译的黄金参考图一致，免除大模型重复审计'
      };
    } else if (src === 'size_threshold_bypass') {
      s4 = {
        color: 'var(--oc-success-fg)',
        status: '✅ 尺寸免检放行',
        detail: '检测为网页边角挂饰或小图标，直接免检通过'
      };
    } else if (src === 'same_image_llm_check') {
      s4 = {
        color: 'var(--oc-danger-fg)',
        status: '❌ 判定需替换',
        detail: '对比判定：网页图片与翻译图特征不一致，尚未替换到位'
      };
    } else if (src === 'gemini_language_check') {
      const isPass = decision === 'pass';
      const isNoText = decision === 'no_text';
      let decLabel = '待人工复核';
      let decColor = 'var(--oc-warning-fg)';
      if (isPass) {
        decLabel = '✅ 大模型审核通过';
        decColor = 'var(--oc-success-fg)';
      } else if (isNoText) {
        decLabel = '✅ 无文字放行';
        decColor = 'var(--oc-success-fg)';
      } else if (decision === 'replace') {
        decLabel = '❌ 大模型判定需替换';
        decColor = 'var(--oc-danger-fg)';
      }

      const langText = analysis.detected_language ? `Detected: ${langDisplayName(analysis.detected_language)}` : '';
      const summaryText = analysis.text_summary ? ` | ${analysis.text_summary}` : '';
      s4 = {
        color: decColor,
        status: decLabel,
        detail: `Gemini OCR 审计。${langText}${summaryText}`
      };
    } else if (decision) {
      const isGood = decision === 'pass' || decision === 'no_text';
      s4 = {
        color: isGood ? 'var(--oc-success-fg)' : (decision === 'replace' ? 'var(--oc-danger-fg)' : 'var(--oc-warning-fg)'),
        status: `⚖️ 综合裁决: ${LINK_CHECK_DECISION_LABELS[decision] || decision}`,
        detail: analysis.quality_reason || '暂无详细说明'
      };
    }

    return `
      <div class="oc-audit-flow-box" style="margin-top: 12px; padding: 10px 12px; background: rgba(0,0,0,0.18); border-radius: 6px; font-size: 11px; line-height: 1.6; border: 1px solid var(--oc-border);">
        <div style="font-weight: bold; margin-bottom: 8px; color: var(--oc-text-normal); display: flex; align-items: center; gap: 6px;">
          <span>📋</span> 链路审计诊断明细 (Audit Diagnostic Flow)
        </div>
        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 10px;">
          <div style="border-left: 2px solid ${s1.color}; padding-left: 8px;">
            <div style="color: var(--oc-text-mute); font-weight: bold; margin-bottom: 2px;">Step 1: 视觉特征检索</div>
            <div>状态: <strong style="color: ${s1.color};">${escapeHtml(s1.status)}</strong></div>
            <div style="color: var(--oc-text-normal); font-size: 10px; margin-top: 2px;">${escapeHtml(s1.detail)}</div>
          </div>
          <div style="border-left: 2px solid ${s2.color}; padding-left: 8px;">
            <div style="color: var(--oc-text-mute); font-weight: bold; margin-bottom: 2px;">Step 2: 文字二值快检</div>
            <div>状态: <strong style="color: ${s2.color};">${escapeHtml(s2.status)}</strong></div>
            <div style="color: var(--oc-text-normal); font-size: 10px; margin-top: 2px;">${escapeHtml(s2.detail)}</div>
          </div>
          <div style="border-left: 2px solid ${s3.color}; padding-left: 8px;">
            <div style="color: var(--oc-text-mute); font-weight: bold; margin-bottom: 2px;">Step 3: 同图LLM辅助判定</div>
            <div>状态: <strong style="color: ${s3.color};">${escapeHtml(s3.status)}</strong></div>
            <div style="color: var(--oc-text-normal); font-size: 10px; margin-top: 2px;">${escapeHtml(s3.detail)}</div>
          </div>
          <div style="border-left: 2px solid ${s4.color}; padding-left: 8px;">
            <div style="color: var(--oc-text-mute); font-weight: bold; margin-bottom: 2px;">Step 4: 最终审计裁决</div>
            <div>决策: <strong style="color: ${s4.color};">${escapeHtml(s4.status)}</strong></div>
            <div style="color: var(--oc-text-normal); font-size: 10px; margin-top: 2px;">${escapeHtml(s4.detail)}</div>
          </div>
        </div>
      </div>
    `;
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

  function edRenderLinkCheckConsole(task) {
    const consoleBox = $('linkCheckConsole');
    if (!consoleBox) return;

    if (!task || (!task.task_id && !task.id)) {
      consoleBox.innerHTML = '';
      return;
    }

    const steps = [
      {
        key: 'lock_locale',
        name: '语言区域锁定 (Language / Locale Lock)',
        icon: '🔒',
        getDetails: (t) => {
          const ev = t.locale_evidence || {};
          const lines = [];
          if (t.target_language) lines.push(`目标语种：${t.target_language_name || langDisplayName(t.target_language)}`);
          if (ev.requested_url) lines.push(`请求 URL：${ev.requested_url}`);
          if (t.resolved_url) lines.push(`最终指向：${t.resolved_url}`);
          if (ev.lock_source) lines.push(`锁定方式：${ev.lock_source}`);
          if (ev.locked) {
            lines.push(`状态：成功锁定 [OK]`);
          } else if (ev.failure_reason) {
            lines.push(`锁定失败原因：${ev.failure_reason}`);
          }
          if (ev.attempts && ev.attempts.length) {
            lines.push(`尝试记录：`);
            ev.attempts.forEach((att, idx) => {
              lines.push(`  [${idx + 1}] URL: ${att.url || '-'} | HTTP: ${att.http_code || '-'} | Lang: ${att.lang || '-'}`);
            });
          }
          return lines.join('\n');
        }
      },
      {
        key: 'download',
        name: '图片资源下载提取 (Shopify Image Extraction)',
        icon: '📥',
        getDetails: (t) => {
          const lines = [];
          if (t.progress && typeof t.progress.total === 'number') {
            lines.push(`检测到 Shopify 图片总量：${t.progress.total} 张`);
            lines.push(`已成功拉取图片：${t.progress.downloaded ?? 0} / ${t.progress.total} 张`);
          }
          if (t.page_language) {
            lines.push(`网页 HTML 文本语言：${t.page_language} (${langDisplayName(t.page_language)})`);
          }
          return lines.join('\n');
        }
      },
      {
        key: 'analyze',
        name: '小语种图片与翻译审计 (Visual Contrast Audit)',
        icon: '🔍',
        getDetails: (t) => {
          const lines = [];
          const progress = t.progress || {};
          const summary = t.summary || {};
          if (typeof progress.total === 'number') {
            lines.push(`二值快速匹配 (Fast Match)：${progress.binary_checked ?? 0} 张已比对`);
            if (summary.binary_direct_pass_count) lines.push(`  - 快检直接通过 (Pass)：${summary.binary_direct_pass_count} 张`);
            if (summary.binary_direct_replace_count) lines.push(`  - 快检直接不通过 (Replace)：${summary.binary_direct_replace_count} 张`);
            lines.push(`大模型同图辅助分析 (Same Image LLM)：${progress.same_image_llm_done ?? 0} 张已判定`);
            if (summary.same_image_llm_yes_count) lines.push(`  - 同图判定一致：${summary.same_image_llm_yes_count} 张`);
            lines.push(`Gemini 多模态文本审计 (Gemini OCR)：${progress.analyzed ?? 0} / ${progress.total} 张完成`);
          }
          return lines.join('\n');
        }
      },
      {
        key: 'summarize',
        name: '最终质量结论裁决 (Final Quality Verdict)',
        icon: '⚖️',
        getDetails: (t) => {
          const s = t.summary || {};
          const lines = [];
          if (t.status === 'done' || t.status === 'review_ready') {
            lines.push(`审计结果汇总：`);
            lines.push(`  - 翻译合格直接通过：${s.pass_count ?? 0} 项`);
            lines.push(`  - 无文字背景图忽略：${s.no_text_count ?? 0} 项`);
            lines.push(`  - 存在英语/未翻译需替换：${s.replace_count ?? 0} 项`);
            lines.push(`  - 无法确定需人工复核：${s.review_count ?? 0} 项`);
            if (s.replaced_count !== undefined || s.total_count !== undefined) {
              lines.push(`  - 换图覆盖完成度：已换 ${s.replaced_count ?? 0} 张 / 共 ${s.total_count ?? 0} 张`);
              lines.push(`  - 未换或换图未到位：${s.not_replaced_count ?? 0} 张`);
            }
            if (s.reference_matched_count) lines.push(`  - 成功匹配本地参考图：${s.reference_matched_count} 张`);
          }
          return lines.join('\n');
        }
      }
    ];

    let html = `
      <div class="oc-console-wrapper" style="margin-bottom: 20px;">
        <div class="oc-console-header">
          <span>🖥️ 实时链接审计台 (Audit Console)</span>
          <span class="oc-console-task-id">Task: ${escapeHtml(task.task_id || task.id || '')}</span>
        </div>
        <div class="oc-console-steps">
    `;

    steps.forEach((step, idx) => {
      const stepState = (task.steps && task.steps[step.key]) || ''; 
      const stepMsg = (task.step_messages && task.step_messages[step.key]) || '';
      
      let badgeClass = 'queued';
      let badgeLabel = '排队中';
      let statusIcon = '●';

      if (stepState === 'running') {
        badgeClass = 'running';
        badgeLabel = '执行中';
        statusIcon = '<span class="oc-spinner-sm"></span>';
      } else if (stepState === 'done') {
        badgeClass = 'done';
        badgeLabel = '已完成';
        statusIcon = '✓';
      } else if (stepState === 'error') {
        badgeClass = 'error';
        badgeLabel = '失败';
        statusIcon = '✗';
      } else {
        if (task.status === 'failed') {
          badgeClass = 'queued';
          badgeLabel = '已停止';
        } else if (task.status === 'queued') {
          badgeClass = 'queued';
          badgeLabel = '排队中';
        } else {
          const prevStep = steps[idx - 1];
          if (prevStep && task.steps && task.steps[prevStep.key] === 'done') {
            badgeClass = 'running';
            badgeLabel = '等待中';
          }
        }
      }

      const details = step.getDetails(task);
      const detailsBlock = details
        ? `<pre class="oc-console-step-logs">${escapeHtml(details)}</pre>`
        : '';

      html += `
        <div class="oc-console-step-item">
          <div class="oc-console-step-row">
            <span class="oc-console-step-status ${badgeClass}">${statusIcon}</span>
            <span class="oc-console-step-name">${step.icon} ${escapeHtml(step.name)}</span>
            <span class="oc-console-step-badge ${badgeClass}">${escapeHtml(badgeLabel)}</span>
            ${stepMsg ? `<span class="oc-console-step-msg">${escapeHtml(stepMsg)}</span>` : ''}
          </div>
          ${detailsBlock}
        </div>
      `;
    });

    let verdictHtml = '';
    const isFinished = ['done', 'review_ready', 'failed'].includes(task.status);
    const overall = (task.summary && task.summary.overall_decision) || '';
    
    if (isFinished) {
      if (task.status === 'failed') {
        verdictHtml = `
          <div class="oc-console-verdict verdict-error">
            <strong>❌ 审计故障：</strong>检测任务在执行过程中出错，原因：${escapeHtml(task.error || '未知错误')}。请检查网络或稍后重试。
          </div>
        `;
      } else if (overall === 'done') {
        const total = task.progress.total ?? 0;
        const replaced = task.summary.replaced_count ?? 0;
        let subVerdict = '';
        if (total > 0) {
          subVerdict = `【换图结论：共拉取到 ${total} 张图片，已 100% 成功换图（${replaced} / ${total} 张已换到位）】`;
        }
        verdictHtml = `
          <div class="oc-console-verdict verdict-pass" style="display: flex; flex-direction: column; align-items: flex-start; gap: 4px;">
            <div><strong>🎉 审计合格：</strong>页面文案与商品图片已完全替换为目标小语种 (${escapeHtml(task.target_language_name || langDisplayName(task.target_language))})，无任何遗漏，完美通关！</div>
            ${subVerdict ? `<div style="font-size: 11px; opacity: 0.9; color: #d1fae5;">${escapeHtml(subVerdict)}</div>` : ''}
          </div>
        `;
      } else if (overall === 'unfinished') {
        const replaceCount = task.summary.replace_count ?? 0;
        const reviewCount = task.summary.review_count ?? 0;
        const total = task.progress.total ?? 0;
        const replaced = task.summary.replaced_count ?? 0;
        const notReplaced = task.summary.not_replaced_count ?? 0;
        let subVerdict = '';
        if (total > 0) {
          subVerdict = `【换图结论：共拉取到 ${total} 张图片，其中 ${replaced} 张已成功换图，${notReplaced} 张未换或换图未到位】`;
        }
        verdictHtml = `
          <div class="oc-console-verdict verdict-fail" style="display: flex; flex-direction: column; align-items: flex-start; gap: 4px;">
            <div><strong>⚠️ 审计未通过：</strong>网页存在未翻译的英语图片或文案（有 ${replaceCount} 张图片需要替换，${reviewCount} 项待人工复核）。请立即排查！</div>
            ${subVerdict ? `<div style="font-size: 11px; opacity: 0.9; color: #ffedd5;">${escapeHtml(subVerdict)}</div>` : ''}
          </div>
        `;
      } else {
        verdictHtml = `
          <div class="oc-console-verdict verdict-fail">
            <strong>⚠️ 审计未就绪：</strong>未获取到明确的审计结论。请人工检查或重新发起检测。
          </div>
        `;
      }
    } else {
      verdictHtml = `
        <div class="oc-console-verdict verdict-running">
          <span class="oc-spinner-sm"></span>
          <strong>🤖 正在进行小语种合规性审计...</strong> 预计需要 1-3 分钟，请稍候。
        </div>
      `;
    }

    html += `
      </div>
      ${verdictHtml}
    </div>
    `;

    consoleBox.innerHTML = html;
  }

  function renderTask(task) {
    state.currentTask = task;
    state.consecutivePollFailures = 0;
    showError(task.error || "");
    setStatus(`当前状态：${LINK_CHECK_STATUS_LABELS[task.status] || task.status || "-"}`);

    const summaryBox = $('linkCheckModalSummary');
    const refsBox = $('linkCheckRefs');
    const itemsBox = $('linkCheckItems');
    if (!summaryBox || !refsBox || !itemsBox) return;

    edRenderLinkCheckConsole(task);

    const summary = task.summary || {};
    const progress = task.progress || {};
    const summaryCards = [
      ['当前状态', edLinkCheckStatusText(task), false],
      ['整体结论', LINK_CHECK_OVERALL_LABELS[summary.overall_decision] || '-', false],
      ['已分析图片', `${progress.analyzed ?? 0} / ${progress.total ?? 0}`, false],
      ['参考图匹配', String(summary.reference_matched_count ?? 0), false],
      ['已替换', summary.replaced_count !== undefined ? `${summary.replaced_count} 张` : '-', false],
      ['未替换', summary.not_replaced_count !== undefined ? `${summary.not_replaced_count} 张` : '-', false],
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
    $('linkCheckRefsBadge').textContent = String(references.length);
    refsBox.innerHTML = references.length
      ? references.map(ref => {
          const previewUrl = safeMediaSrc(ref.preview_url || '');
          return `
          <div class="oc-link-check-ref">
            <img src="${escapeHtml(previewUrl)}" alt="${escapeHtml(ref.filename || '参考图')}" loading="lazy">
            <span title="${escapeHtml(ref.filename || '')}">${escapeHtml(ref.filename || '')}</span>
          </div>
        `;
        }).join('')
      : '<div class="oc-detail-images-empty">暂无参考图</div>';

    const items = Array.isArray(task.items) ? task.items : [];
    $('linkCheckItemsBadge').textContent = String(items.length);
    if (!items.length) {
      const placeholder = !TERMINAL_STATUSES.has(task.status)
        ? `链接检测进行中，当前进度 ${edLinkCheckPercent(task)}%`
        : '还没有检测结果';
      itemsBox.innerHTML = `<div class="oc-detail-images-empty">${escapeHtml(placeholder)}</div>`;
      return;
    }

    itemsBox.innerHTML = items.map((item, idx) => {
      const analysis = item.analysis || {};
      const reference = item.reference_match || {};
      const binary = item.binary_quick_check || {};
      const sameImage = item.same_image_llm || {};
      const decision = analysis.decision || '';
      const isReplaced = item.is_replaced; 

      let finalReplaced = isReplaced;
      if (finalReplaced === null || finalReplaced === undefined) {
        if (decision === 'pass') {
          finalReplaced = true;
        } else if (decision === 'replace') {
          finalReplaced = false;
        }
      }

      let replacedBadgeHtml = '';
      if (finalReplaced === true) {
        replacedBadgeHtml = `<span class="oc-link-check-badge success" style="font-size: 11px; padding: 2px 8px; font-weight: bold; border: 1px solid var(--oc-success-fg);">✅ 已替换</span>`;
      } else if (finalReplaced === false) {
        replacedBadgeHtml = `<span class="oc-link-check-badge danger" style="font-size: 11px; padding: 2px 8px; font-weight: bold; border: 1px solid var(--oc-danger-fg);">❌ 未替换</span>`;
      } else {
        if (reference.status === 'matched') {
          replacedBadgeHtml = `<span class="oc-link-check-badge info" style="font-size: 11px; padding: 2px 8px; font-weight: bold;">❔ 未对比</span>`;
        } else {
          replacedBadgeHtml = `<span class="oc-link-check-badge info" style="font-size: 11px; padding: 2px 8px; font-weight: bold;">❔ 未对比(无参考图)</span>`;
        }
      }

      const qualityScore = analysis.quality_score !== undefined ? Number(analysis.quality_score) : 0;
      let scoreBadgeColor = 'info';
      if (qualityScore >= 80) scoreBadgeColor = 'success';
      else if (qualityScore >= 60) scoreBadgeColor = 'warning';
      else if (qualityScore > 0) scoreBadgeColor = 'danger';

      const scoreBadgeHtml = `<span class="oc-link-check-badge ${scoreBadgeColor}" style="font-size: 11px; padding: 2px 8px; font-weight: bold;">⭐ 质量评分：${qualityScore}分</span>`;

      const reason = analysis.quality_reason || analysis.text_summary || item.error || binary.reason || sameImage.reason || '暂无说明';
      const itemLabel = item.kind === 'hero' ? '轮播图' : '详情图';
      const sitePreviewUrl = safeMediaSrc(item.site_preview_url);
      const refPreviewUrl = reference.reference_id 
        ? safeMediaSrc(`/api/link-check/tasks/${task.task_id || task.id}/images/reference/${reference.reference_id}`)
        : '';

      const original = item.original_match || {};
      const origPreviewUrl = original.original_id
        ? safeMediaSrc(`/api/link-check/tasks/${task.task_id || task.id}/images/original/${original.original_id}`)
        : '';

      const origImg = origPreviewUrl
        ? `<img src="${escapeHtml(origPreviewUrl)}" alt="英语原图" loading="lazy" style="width:100%; height:100%; object-fit:cover; display:block;">`
        : `<div class="oc-detail-images-empty" style="height:100%; margin:0; display:flex; align-items:center; justify-content:center; background:var(--oc-bg-subtle); font-size:11px; color:var(--oc-text-mute);">${original.status === 'not_matched' ? '未匹配到原图' : '无对照原图'}</div>`;

      const leftImg = sitePreviewUrl
        ? `<img src="${escapeHtml(sitePreviewUrl)}" alt="网页实际图" loading="lazy" style="width:100%; height:100%; object-fit:cover; display:block;">`
        : `<div class="oc-detail-images-empty" style="height:100%; margin:0; display:flex; align-items:center; justify-content:center; font-size:11px;">无实际图</div>`;

      const rightImg = refPreviewUrl
        ? `<img src="${escapeHtml(refPreviewUrl)}" alt="系统翻译图" loading="lazy" style="width:100%; height:100%; object-fit:cover; display:block;">`
        : `<div class="oc-detail-images-empty" style="height:100%; margin:0; display:flex; align-items:center; justify-content:center; background:var(--oc-bg-subtle); font-size:11px; color:var(--oc-text-mute);">${reference.status === 'not_matched' ? '未匹配到参考图' : '无对比参考图'}</div>`;

      let borderStyle = 'border-right:1px solid var(--oc-border); border-left:1px solid var(--oc-border);';
      if (finalReplaced === true) {
        borderStyle = 'border:2px solid var(--oc-success-fg); box-shadow:0 0 8px rgba(16, 185, 129, 0.2);';
      } else if (finalReplaced === false) {
        borderStyle = 'border:2px solid var(--oc-danger-fg); box-shadow:0 0 8px rgba(239, 68, 68, 0.2);';
      }

      const preview = `
        <div class="oc-link-check-item-comparison" style="display:flex; width:100%; height:100%;">
          <div class="oc-comparison-side" style="flex:1; position:relative; height:100%; border-right:1px solid var(--oc-border); background:var(--oc-bg-subtle);">
            <div style="position:absolute; bottom:4px; left:4px; background:rgba(0,0,0,0.6); color:#fff; padding:2px 6px; font-size:10px; border-radius:4px; z-index:2; pointer-events:none;">英语原图</div>
            ${origImg}
          </div>
          <div class="oc-comparison-side" style="flex:1; position:relative; height:100%; ${borderStyle} z-index:1;">
            <div style="position:absolute; bottom:4px; left:4px; background:rgba(0,0,0,0.6); color:#fff; padding:2px 6px; font-size:10px; border-radius:4px; z-index:2; pointer-events:none;">网页实际图</div>
            ${leftImg}
          </div>
          <div class="oc-comparison-side" style="flex:1; position:relative; height:100%; border-left:1px solid var(--oc-border); background:var(--oc-bg-subtle);">
            <div style="position:absolute; bottom:4px; left:4px; background:rgba(0,0,0,0.6); color:#fff; padding:2px 6px; font-size:10px; border-radius:4px; z-index:2; pointer-events:none;">系统翻译图</div>
            ${rightImg}
          </div>
        </div>
      `;
      return `
        <article class="oc-link-check-item">
          <div class="oc-link-check-item-preview">${preview}</div>
          <div class="oc-link-check-item-side-badges">
            ${replacedBadgeHtml}
            ${scoreBadgeHtml}
            ${edLinkCheckBadge(edLinkCheckDecisionText(decision, item.status), edLinkCheckDecisionKind(decision, item.status))}
            ${edLinkCheckBadge(edLinkCheckReferenceText(reference), reference.status === 'matched' ? 'success' : (reference.status === 'not_matched' ? 'warning' : 'info'))}
          </div>
          <div class="oc-link-check-item-body">
            <div class="oc-link-check-item-head">
              <div class="oc-link-check-item-title">${escapeHtml(itemLabel)} #${idx + 1}</div>
            </div>
            <div class="oc-link-check-item-url">${escapeHtml(item.source_url || '-')}</div>
            <div class="oc-link-check-item-meta">
              <span><strong>识别语种：</strong>${escapeHtml(langDisplayName(analysis.detected_language || '-'))}</span>
              <span><strong>页面语种：</strong>${escapeHtml(langDisplayName(task.page_language || '-'))}</span>
              <span><strong>二值快检：</strong>${escapeHtml(edLinkCheckBinaryText(binary))}</span>
              <span><strong>同图判断：</strong>${escapeHtml(edLinkCheckSameImageText(sameImage))}</span>
            </div>
            <div class="oc-link-check-item-text">${escapeHtml(reason)}</div>
            ${edLinkCheckWorkflowHtml(item, task)}
          </div>
        </article>
      `;
    }).join('');

  }

  function stopPolling() {
    if (state.pollTimer) {
      window.clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
  }

  async function pollTask(taskId) {
    const task = await fetchJSON(`/api/link-check/tasks/${taskId}`);
    renderTask(task);
    if (TERMINAL_STATUSES.has(task.status)) {
      stopPolling();
    }
  }

  function startPollingIfNeeded(task) {
    if (!task || !task.id || TERMINAL_STATUSES.has(task.status)) {
      stopPolling();
      return;
    }

    stopPolling();
    state.consecutivePollFailures = 0;
    state.pollTimer = window.setInterval(() => {
      pollTask(task.id).catch((error) => {
        state.consecutivePollFailures += 1;
        showError(error.message || "轮询失败");
        if (state.consecutivePollFailures >= MAX_POLL_FAILURES) {
          stopPolling();
          setStatus("任务状态获取失败，已停止自动轮询");
          return;
        }
        setStatus(`任务状态获取失败，正在重试（${state.consecutivePollFailures}/${MAX_POLL_FAILURES}）`);
      });
    }, 1500);
  }

  function getCsrfToken() {
    const el = document.querySelector("meta[name='csrf-token']");
    return el ? el.content || el.getAttribute("content") || "" : "";
  }

  document.addEventListener("DOMContentLoaded", async function () {
    const page = $("linkCheckDetailPage");
    if (!page) {
      return;
    }

    await ensureLanguages();

    const task = getBootstrappedTask();
    if (!task || !task.id) {
      showError("初始化任务数据缺失");
      setStatus("初始化失败");
      return;
    }

    renderTask(task);
    startPollingIfNeeded(task);

    // Bind refresh button
    const refreshBtn = $("standaloneRefreshBtn");
    if (refreshBtn) {
      refreshBtn.addEventListener("click", async function () {
        const origText = refreshBtn.textContent;
        refreshBtn.disabled = true;
        refreshBtn.textContent = "刷新中...";
        try {
          await pollTask(task.id);
        } catch (err) {
          showError("刷新失败: " + err.message);
        } finally {
          refreshBtn.disabled = false;
          refreshBtn.textContent = origText;
        }
      });
    }

    // Bind recheck button
    const recheckBtn = $("standaloneRecheckBtn");
    if (recheckBtn) {
      function updateRecheckButtonVisibility(status) {
        if (TERMINAL_STATUSES.has(status)) {
          recheckBtn.style.display = "inline-block";
        } else {
          recheckBtn.style.display = "none";
        }
      }

      updateRecheckButtonVisibility(task.status);

      const originalRenderTask = renderTask;
      renderTask = function(t) {
        originalRenderTask(t);
        updateRecheckButtonVisibility(t.status);
      };

      recheckBtn.addEventListener("click", async function () {
        const pid = recheckBtn.dataset.productId;
        const lang = recheckBtn.dataset.lang;
        const linkUrl = recheckBtn.dataset.linkUrl;
        if (!pid || !lang || !linkUrl) {
          alert("检测配置数据缺失，无法重新检测");
          return;
        }

        if (!confirm("确定要重新检测该链接吗？这将会覆盖当前检测结果并重新启动审计任务。")) {
          return;
        }

        recheckBtn.disabled = true;
        recheckBtn.textContent = "启动中...";

        try {
          const csrfToken = getCsrfToken();
          const headers = { "Content-Type": "application/json" };
          if (csrfToken) {
            headers["X-CSRFToken"] = csrfToken;
          }

          const data = await fetchJSON(`/medias/api/products/${pid}/link-check`, {
            method: "POST",
            headers: headers,
            body: JSON.stringify({
              lang: lang,
              link_url: linkUrl
            })
          });

          if (data && data.task_id) {
            window.location.href = `/link-check/${encodeURIComponent(data.task_id)}`;
          } else {
            throw new Error("启动任务失败");
          }
        } catch (err) {
          alert("启动重新检测任务失败: " + err.message);
          recheckBtn.disabled = false;
          recheckBtn.textContent = "重新检测";
        }
      });
    }
  });
})();
