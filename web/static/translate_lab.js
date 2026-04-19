/* ==========================================================================
   视频翻译（测试）— 列表页 + 详情页 交互脚本
   - 全局命名空间 TranslateLab
   - 列表页：TranslateLab.initList()
   - 详情页：TranslateLab.initDetail()
   ========================================================================== */
(function () {
  "use strict";

  // ── 工具 ───────────────────────────────────────────────
  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  function escapeHtml(s) {
    if (s === null || s === undefined) return "";
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function formatTime(sec) {
    sec = Number(sec) || 0;
    var m = Math.floor(sec / 60);
    var s = Math.floor(sec % 60);
    return (m < 10 ? "0" : "") + m + ":" + (s < 10 ? "0" : "") + s;
  }

  function showToast(msg, kind) {
    var toast = $("#labToast");
    if (!toast) { console.log("[toast]", msg); return; }
    toast.textContent = msg;
    toast.className = "lab-toast show" + (kind ? " " + kind : "");
    setTimeout(function () { toast.classList.remove("show"); }, 2600);
  }

  function requestJson(url, opts) {
    opts = opts || {};
    opts.headers = opts.headers || {};
    if (opts.body && typeof opts.body === "object" && !(opts.body instanceof FormData)) {
      opts.headers["Content-Type"] = "Content-Type" in opts.headers ? opts.headers["Content-Type"] : "application/json";
      opts.body = JSON.stringify(opts.body);
    }
    return fetch(url, opts).then(function (resp) {
      return resp.text().then(function (text) {
        var data = null;
        try { data = text ? JSON.parse(text) : null; } catch (err) { /* ignore */ }
        if (!resp.ok) {
          var msg = (data && data.error) || ("HTTP " + resp.status);
          var e = new Error(msg);
          e.status = resp.status;
          e.data = data;
          throw e;
        }
        return data;
      });
    });
  }

  // ──────────────────────────────────────────────────────
  // 列表页
  // ──────────────────────────────────────────────────────
  function initList() {
    // 打开 / 关闭新建任务弹窗
    var createBtn = $("#openCreateBtn");
    var createOverlay = $("#createOverlay");
    var createForm = $("#createForm");
    var createCancelBtn = $("#createCancelBtn");
    var createSubmitBtn = $("#createSubmitBtn");

    function openCreate() { if (createOverlay) createOverlay.classList.add("open"); }
    function closeCreate() { if (createOverlay) createOverlay.classList.remove("open"); }

    if (createBtn) createBtn.addEventListener("click", openCreate);
    if (createCancelBtn) createCancelBtn.addEventListener("click", closeCreate);
    if (createOverlay) {
      createOverlay.addEventListener("click", function (e) {
        if (e.target === createOverlay) closeCreate();
      });
    }

    if (createForm) {
      createForm.addEventListener("submit", function (e) {
        e.preventDefault();
        var fileInput = $("#createVideoFile");
        if (!fileInput || !fileInput.files[0]) return;
        var file = fileInput.files[0];
        var sizeMB = (file.size / 1024 / 1024).toFixed(1);
        createSubmitBtn.disabled = true;
        createSubmitBtn.textContent = "上传中 (" + sizeMB + " MB)...";

        var fd = new FormData();
        fd.append("video", file);
        fd.append("source_language", $("#createSourceLang").value);
        fd.append("target_language", $("#createTargetLang").value);
        var modeEl = document.querySelector("input[name='voice_match_mode']:checked");
        fd.append("voice_match_mode", modeEl ? modeEl.value : "auto");

        requestJson("/api/translate-lab", { method: "POST", body: fd })
          .then(function (data) {
            window.location.href = "/translate-lab/" + data.task_id;
          })
          .catch(function (err) {
            showToast(err.message || "创建失败", "error");
            createSubmitBtn.disabled = false;
            createSubmitBtn.textContent = "上传并创建";
          });
      });
    }

    // 同步音色库
    var syncBtn = $("#syncVoiceLibraryBtn");
    var syncOverlay = $("#syncOverlay");
    var syncCancelBtn = $("#syncCancelBtn");
    var syncConfirmBtn = $("#syncConfirmBtn");

    if (syncBtn && syncOverlay) {
      syncBtn.addEventListener("click", function () { syncOverlay.classList.add("open"); });
    }
    if (syncCancelBtn) {
      syncCancelBtn.addEventListener("click", function () { syncOverlay.classList.remove("open"); });
    }
    if (syncOverlay) {
      syncOverlay.addEventListener("click", function (e) {
        if (e.target === syncOverlay) syncOverlay.classList.remove("open");
      });
    }
    if (syncConfirmBtn) {
      syncConfirmBtn.addEventListener("click", function () {
        syncConfirmBtn.disabled = true;
        syncConfirmBtn.textContent = "同步中...";
        requestJson("/api/translate-lab/voice-library/sync", {
          method: "POST",
          body: {},
        })
          .then(function (data) {
            syncOverlay.classList.remove("open");
            showToast("同步完成，共 " + (data && data.total || 0) + " 条音色", "success");
          })
          .catch(function (err) {
            showToast(err.message || "同步失败", "error");
          })
          .then(function () {
            syncConfirmBtn.disabled = false;
            syncConfirmBtn.textContent = "开始同步";
          });
      });
    }

    // 卡片菜单（删除）
    document.addEventListener("click", function (e) {
      var trigger = e.target.closest(".card-menu-btn");
      if (trigger) {
        e.preventDefault();
        e.stopPropagation();
        var id = trigger.getAttribute("data-menu-id");
        $$(".card-menu.open").forEach(function (m) {
          if (m.id !== id) m.classList.remove("open");
        });
        var menu = document.getElementById(id);
        if (menu) menu.classList.toggle("open");
        return;
      }
      var del = e.target.closest(".card-menu button[data-action='delete']");
      if (del) {
        e.preventDefault();
        e.stopPropagation();
        var taskId = del.getAttribute("data-task-id");
        if (!confirm("确定删除这个任务吗？此操作不可恢复。")) return;
        requestJson("/api/translate-lab/" + taskId, { method: "DELETE" })
          .then(function () { window.location.reload(); })
          .catch(function (err) { showToast(err.message || "删除失败", "error"); });
        return;
      }
      // 点击空白关闭所有菜单
      $$(".card-menu.open").forEach(function (m) { m.classList.remove("open"); });
    });
  }

  // ──────────────────────────────────────────────────────
  // 详情页
  // ──────────────────────────────────────────────────────
  var D = null; // 详情页运行态

  function initDetail() {
    var root = document.querySelector(".lab-detail");
    if (!root) return;

    D = {
      taskId: root.getAttribute("data-task-id"),
      status: root.getAttribute("data-initial-status") || "uploaded",
      sourceLanguage: root.getAttribute("data-source-language") || "zh",
      targetLanguage: root.getAttribute("data-target-language") || "en",
      voiceMatchMode: root.getAttribute("data-voice-match-mode") || "auto",
      shots: [],
      translationsByIdx: {},
      ttsByIdx: {},
      voiceConfirmed: null,
    };

    // 开始按钮：调 /start
    var startBtn = $("#labStartBtn");
    if (startBtn) {
      startBtn.addEventListener("click", onClickStart);
    }
    // 初始状态不是 uploaded 时，移除「开始处理」按钮（已启动过）
    if (D.status !== "uploaded" && startBtn) {
      startBtn.remove();
    }

    // 连接 Socket.IO
    if (window.io) {
      try {
        var socket = io();
        D.socket = socket;
        socket.on("connect", function () {
          socket.emit("join_translate_lab_task", { task_id: D.taskId });
        });
        bindLabEvents(socket);
      } catch (err) {
        console.error("[translate_lab] socket init failed:", err);
      }
    }

    // 首次刷新：从后端读状态，渲染已有数据
    refreshDetail();

    // 分镜展开/收起
    document.addEventListener("click", function (e) {
      var head = e.target.closest(".shot-head");
      if (head) {
        var item = head.closest(".shot-item");
        if (item) item.classList.toggle("open");
      }
    });
  }

  function onClickStart() {
    var btn = $("#labStartBtn");
    if (!btn || btn.disabled) return;
    btn.disabled = true;
    btn.textContent = "启动中...";

    var payload = {
      source_language: D.sourceLanguage,
      target_language: D.targetLanguage,
      voice_match_mode: D.voiceMatchMode,
    };
    requestJson("/api/translate-lab/" + D.taskId + "/start", {
      method: "POST",
      body: payload,
    })
      .then(function () {
        D.status = "running";
        setStatusTag("running");
        btn.remove();
      })
      .catch(function (err) {
        showToast(err.message || "启动失败", "error");
        btn.disabled = false;
        btn.textContent = "开始处理";
      });
  }

  function refreshDetail() {
    requestJson("/api/translate-lab/" + D.taskId)
      .then(function (task) {
        if (!task) return;
        applyTaskSnapshot(task);
      })
      .catch(function (err) {
        // 不打断 UI，仅提示错误
        if (err && err.status && err.status !== 404) {
          showToast(err.message || "任务加载失败", "error");
        }
      });
  }

  function applyTaskSnapshot(task) {
    D.status = task.status || D.status;
    setStatusTag(D.status);

    // 步骤状态与消息
    D.stepModelTags = task.step_model_tags || {};
    var steps = task.steps || {};
    Object.keys(steps).forEach(function (code) {
      setStepState(code, steps[code], (task.step_messages || {})[code]);
    });

    // 分镜
    if (Array.isArray(task.shots) && task.shots.length) {
      D.shots = task.shots;
      renderShots(task.shots);
    }

    // 翻译
    if (Array.isArray(task.translations)) {
      task.translations.forEach(function (tr) {
        if (tr && (tr.shot_index !== undefined)) {
          D.translationsByIdx[tr.shot_index] = tr;
        }
      });
      renderTranslateTts();
    }

    // TTS
    if (Array.isArray(task.tts_results)) {
      task.tts_results.forEach(function (r) {
        if (r && (r.shot_index !== undefined)) {
          D.ttsByIdx[r.shot_index] = r;
        }
      });
      renderTranslateTts();
    }

    // 音色候选
    if (Array.isArray(task.pending_voice_choice) && task.pending_voice_choice.length) {
      if (D.voiceMatchMode === "manual" && !task.chosen_voice) {
        renderVoiceCandidates(task.pending_voice_choice);
      }
    }
    if (task.chosen_voice) {
      renderVoiceConfirmed(task.chosen_voice);
    } else if (Array.isArray(task.voice_candidates) && task.voice_candidates.length && D.voiceMatchMode === "manual") {
      renderVoiceCandidates(task.voice_candidates);
    }

    // 字幕
    if (task.subtitle_path) {
      showSubtitleSection(task.subtitle_path);
    }

    // 最终视频
    if (task.compose_result && (task.compose_result.hard_video || task.compose_result.soft_video)) {
      showComposeSection("/api/translate-lab/" + D.taskId + "/final-video");
    } else if (task.final_video) {
      showComposeSection("/api/translate-lab/" + D.taskId + "/final-video");
    } else if (task.result && task.result.final_video_url) {
      showComposeSection(task.result.final_video_url);
    } else if (task.final_video_path) {
      showComposeSection(urlForFinalVideo(task));
    }

    // 错误
    if (task.status === "error" && task.error) {
      showError(task.error);
    }

    // 若任务尚未启动，保留开始按钮；已在 running/awaiting_voice/done 则确保按钮消失
    if (D.status !== "uploaded") {
      var btn = $("#labStartBtn");
      if (btn) btn.remove();
    }
  }

  function urlForFinalVideo(task) {
    // 兼容字段：后端可能只写 final_video_path，统一指向产物路由。
    if (D && D.taskId) {
      return "/api/translate-lab/" + D.taskId + "/final-video";
    }
    return "#";
  }

  // ── Socket.IO 事件 ───────────────────────────────────
  function bindLabEvents(socket) {
    // Generic step status updates from base PipelineRunner._set_step.
    // Covers extract/compose and any step without a dedicated event.
    socket.on("step_update", function (payload) {
      if (payload && payload.step) {
        if (payload.model_tag) {
          D.stepModelTags = D.stepModelTags || {};
          D.stepModelTags[payload.step] = payload.model_tag;
        }
        setStepState(payload.step, payload.status, payload.message);
      }
    });
    socket.on("lab_shot_decompose_result", function (payload) {
      if (!payload) return;
      if (Array.isArray(payload.shots)) {
        D.shots = payload.shots;
        renderShots(payload.shots);
        setStepState("shot_decompose", "done", "分镜完成，共 " + payload.shots.length + " 段");
      }
    });

    socket.on("lab_voice_match_candidates", function (payload) {
      if (!payload || !Array.isArray(payload.candidates)) return;
      // 只有人工模式才展示候选卡片
      if (D.voiceMatchMode === "manual") {
        renderVoiceCandidates(payload.candidates);
        setStatusTag("awaiting_voice");
      }
      setStepState("voice_match", "running", "共 " + payload.candidates.length + " 个候选");
    });

    socket.on("lab_voice_confirmed", function (payload) {
      if (!payload || !payload.voice) return;
      renderVoiceConfirmed(payload.voice);
      setStepState("voice_match", "done", "音色已确定");
    });

    socket.on("lab_translate_progress", function (payload) {
      if (!payload || payload.index === undefined) return;
      D.translationsByIdx[payload.index] = payload.result || {};
      renderTranslateTts();
      setStepState("translate", "running", "已翻译 " + Object.keys(D.translationsByIdx).length + " 段");
    });

    socket.on("lab_tts_progress", function (payload) {
      if (!payload || payload.index === undefined) return;
      D.ttsByIdx[payload.index] = payload.result || {};
      renderTranslateTts();
      setStepState("tts_verify", "running", "已生成 " + Object.keys(D.ttsByIdx).length + " 段配音");
    });

    socket.on("lab_subtitle_ready", function (payload) {
      if (!payload) return;
      if (payload.srt_path) {
        showSubtitleSection(payload.srt_path);
      }
      setStepState("subtitle", "done", "字幕已生成");
    });

    socket.on("lab_pipeline_done", function () {
      setStatusTag("done");
      setStepState("compose", "done", "合成完成");
      showToast("任务处理完成", "success");
      // 触发一次 refresh 拿最终产物 URL
      refreshDetail();
    });

    socket.on("lab_pipeline_error", function (payload) {
      var msg = (payload && payload.error) || "未知错误";
      showError(msg);
      setStatusTag("error");
      showToast("任务失败: " + msg, "error");
    });
  }

  // ── 步骤状态渲染 ─────────────────────────────────────
  function setStepState(code, status, message) {
    var item = document.querySelector('.lab-step-item[data-step="' + code + '"]');
    if (!item) return;
    // 更新 class
    item.className = "lab-step-item lab-step--" + (status || "pending");
    item.setAttribute("data-step", code);

    var statusEl = item.querySelector('[data-step-status="' + code + '"]');
    if (statusEl) statusEl.textContent = status || "pending";

    var msgEl = item.querySelector('[data-step-msg="' + code + '"]');
    if (msgEl && message !== undefined && message !== null) {
      msgEl.textContent = message;
      // 重新追加模型标签
      var tagText = (D.stepModelTags || {})[code] || "";
      if (tagText) {
        var span = document.createElement("span");
        span.className = "step-model-tag";
        span.textContent = tagText;
        msgEl.appendChild(span);
      }
    }
  }

  function setStatusTag(status) {
    var tag = $("#labStatusTag");
    if (!tag) return;
    tag.setAttribute("data-status", status);
    tag.textContent = status;
  }

  // ── 分镜渲染 ─────────────────────────────────────────
  function renderShots(shots) {
    var section = $("#labShotSection");
    var list = $("#labShotTimeline");
    var summary = $("#labShotSummary");
    if (!section || !list) return;
    section.hidden = false;

    var silentCount = 0;
    var html = shots.map(function (shot, i) {
      var idx = shot.index !== undefined ? shot.index : i;
      var start = shot.start_time !== undefined ? shot.start_time : shot.start || 0;
      var end = shot.end_time !== undefined ? shot.end_time : shot.end || 0;
      var duration = shot.duration || (end - start);
      var silent = !!shot.silent;
      if (silent) silentCount += 1;
      var title = silent
        ? "静音分镜"
        : (shot.source_text || shot.description || shot.visual_description || "");
      return ''
        + '<div class="shot-item">'
        + '  <div class="shot-head">'
        + '    <span class="shot-index">#' + escapeHtml(String(idx)) + '</span>'
        + '    <span class="shot-time">' + formatTime(start) + ' – ' + formatTime(end)
        + '      <span style="color:#9ca3af;font-size:11px;">(' + (Number(duration).toFixed(1)) + 's)</span>'
        + '    </span>'
        + '    <span class="shot-title">' + escapeHtml(title) + '</span>'
        + '    <button type="button" class="shot-toggle" aria-label="展开">›</button>'
        + '  </div>'
        + '  <div class="shot-body">'
        + (silent
            ? '    <div class="shot-field shot-field--silent"><span class="label">类型</span><span class="value">静音分镜（无需翻译与配音）</span></div>'
            : '')
        + (shot.source_text
            ? '    <div class="shot-field"><span class="label">原文</span><span class="value">' + escapeHtml(shot.source_text) + '</span></div>'
            : '')
        + (shot.visual_description || shot.description
            ? '    <div class="shot-field"><span class="label">画面描述</span><span class="value">' + escapeHtml(shot.visual_description || shot.description) + '</span></div>'
            : '')
        + '  </div>'
        + '</div>';
    }).join("");

    list.innerHTML = html;
    if (summary) {
      summary.textContent = "共 " + shots.length + " 段" + (silentCount ? "（静音 " + silentCount + "）" : "");
    }
  }

  // ── 音色候选 ─────────────────────────────────────────
  function renderVoiceCandidates(candidates) {
    var section = $("#labVoiceSection");
    var list = $("#labVoiceMatch");
    var subtitle = $("#labVoiceSubtitle");
    if (!section || !list) return;
    section.hidden = false;
    if (subtitle) subtitle.textContent = "请从下列 " + candidates.length + " 个候选中选择一个";

    list.innerHTML = candidates.map(function (v) {
      var gender = v.gender === "female" ? "女声" : (v.gender === "male" ? "男声" : (v.gender || ""));
      var lang = v.language || v.accent || "";
      var score = v.score !== undefined
        ? ('分数 ' + Number(v.score).toFixed(2))
        : "";
      var preview = v.preview_url || v.preview_audio || "";
      return ''
        + '<div class="voice-card" data-voice-id="' + escapeHtml(v.voice_id || "") + '">'
        + '  <div class="voice-card-head">'
        + '    <span class="voice-name">' + escapeHtml(v.name || v.voice_id || "—") + '</span>'
        + '    <button type="button" class="voice-play-btn" ' + (preview ? 'data-preview="' + escapeHtml(preview) + '"' : 'disabled') + ' aria-label="试听">▶</button>'
        + '  </div>'
        + '  <div class="voice-meta">'
        + (gender ? '<span>' + escapeHtml(gender) + '</span>' : '')
        + (lang ? '<span>' + escapeHtml(lang) + '</span>' : '')
        + (score ? '<span>' + escapeHtml(score) + '</span>' : '')
        + '  </div>'
        + '  <div class="voice-actions">'
        + '    <button type="button" class="btn btn-primary btn-sm voice-confirm-btn" data-voice-id="' + escapeHtml(v.voice_id || "") + '">选这个</button>'
        + '  </div>'
        + '</div>';
    }).join("");

    // 播放 / 选中 事件通过 document 级委托处理（见 bindVoiceActions），此处不重复绑定。
  }

  // 文档级事件委托：避免每次 renderVoiceCandidates 重复绑定导致内存泄漏。
  document.addEventListener("click", function (e) {
    var playBtn = e.target.closest(".voice-play-btn");
    if (playBtn && !playBtn.disabled) {
      var url = playBtn.getAttribute("data-preview");
      if (url) playVoicePreview(url, playBtn);
      return;
    }
    var confirmBtn = e.target.closest(".voice-confirm-btn");
    if (confirmBtn) {
      var voiceId = confirmBtn.getAttribute("data-voice-id");
      if (voiceId && D && D.taskId) {
        confirmBtn.disabled = true;
        confirmBtn.textContent = "确认中...";
        requestJson("/api/translate-lab/" + D.taskId + "/confirm-voice", {
          method: "POST",
          body: { voice_id: voiceId },
        })
          .then(function (data) {
            showToast("已选择该音色", "success");
            if (data && data.chosen) renderVoiceConfirmed(data.chosen);
          })
          .catch(function (err) {
            showToast(err.message || "确认失败", "error");
            confirmBtn.disabled = false;
            confirmBtn.textContent = "选这个";
          });
      }
    }
  });

  function playVoicePreview(url, btn) {
    var audio = $("#labVoicePreviewAudio");
    if (!audio) return;
    // 正在同一条：切暂停
    if (audio._currentUrl === url && !audio.paused) {
      audio.pause();
      return;
    }
    $$(".voice-play-btn.playing").forEach(function (b) {
      b.classList.remove("playing");
      b.textContent = "▶";
    });
    audio.src = url;
    audio._currentUrl = url;
    audio.play().catch(function () {});
    btn.classList.add("playing");
    btn.textContent = "■";
    audio.onended = function () {
      btn.classList.remove("playing");
      btn.textContent = "▶";
    };
  }

  function renderVoiceConfirmed(voice) {
    D.voiceConfirmed = voice;
    var section = $("#labVoiceSection");
    var list = $("#labVoiceMatch");
    var subtitle = $("#labVoiceSubtitle");
    if (!section || !list) return;
    section.hidden = false;
    if (subtitle) subtitle.textContent = "已确认音色";

    list.innerHTML = ''
      + '<div class="voice-card voice-card--chosen">'
      + '  <div class="voice-card-head">'
      + '    <span class="voice-name">' + escapeHtml(voice.name || voice.voice_id || "—") + '</span>'
      + (voice.preview_url
          ? '    <button type="button" class="voice-play-btn" data-preview="' + escapeHtml(voice.preview_url) + '" aria-label="试听">▶</button>'
          : '')
      + '  </div>'
      + '  <div class="voice-meta">'
      + '    <span>已选中</span>'
      + (voice.voice_id ? '<span>' + escapeHtml(voice.voice_id) + '</span>' : '')
      + '  </div>'
      + '</div>';
  }

  // ── 翻译 & TTS 渲染 ──────────────────────────────────
  function renderTranslateTts() {
    var section = $("#labTranslateSection");
    var list = $("#labTranslateList");
    var summary = $("#labTranslateSummary");
    if (!section || !list) return;
    var indices = Array.from(new Set(
      Object.keys(D.translationsByIdx).concat(Object.keys(D.ttsByIdx))
    )).map(function (k) { return Number(k); }).sort(function (a, b) { return a - b; });
    if (!indices.length) return;
    section.hidden = false;

    var totalOver = 0;
    var html = indices.map(function (idx) {
      var tr = D.translationsByIdx[idx] || {};
      var tts = D.ttsByIdx[idx] || {};
      var shot = (D.shots || []).find(function (s) { return s.index === idx; }) || {};
      var source = shot.source_text || tr.source_text || "";
      var translated = tts.final_text || tr.translated_text || "";
      var overLimit = !!tr.over_limit;
      if (overLimit) totalOver += 1;
      var finalDuration = tts.final_duration !== undefined ? Number(tts.final_duration).toFixed(2) : null;
      var shotDuration = shot.duration !== undefined ? Number(shot.duration).toFixed(2) : null;
      var durationOk = finalDuration !== null && shotDuration !== null
        && Number(finalDuration) <= Number(shotDuration) + 0.3;
      var audioPath = tts.audio_path ? fileToAudioUrl(D.taskId, idx) : "";
      return ''
        + '<div class="tt-item">'
        + '  <span class="tt-index">#' + idx + '</span>'
        + '  <div class="tt-body">'
        + (source ? '    <div class="tt-row tt-row--src"><span class="label">原文</span><span class="text">' + escapeHtml(source) + '</span></div>' : '')
        + (translated ? '    <div class="tt-row tt-row--trans"><span class="label">译文</span><span class="text">' + escapeHtml(translated) + '</span></div>' : '')
        + (audioPath ? '    <audio class="tt-audio" controls preload="none" src="' + escapeHtml(audioPath) + '"></audio>' : '')
        + '  </div>'
        + '  <div class="tt-duration">'
        + (shotDuration ? '<span>分镜 ' + shotDuration + 's</span>' : '')
        + (finalDuration
            ? '<span class="' + (durationOk ? 'ok' : 'over') + '">TTS ' + finalDuration + 's</span>'
            : '')
        + (overLimit ? '<span class="over">超出字符限制</span>' : '')
        + '  </div>'
        + '</div>';
    }).join("");

    list.innerHTML = html;

    if (summary) {
      summary.textContent = "共 " + indices.length + " 段"
        + (totalOver ? "，其中 " + totalOver + " 段超限" : "");
    }
  }

  function fileToAudioUrl(taskId, shotIndex) {
    if (!taskId || shotIndex === undefined || shotIndex === null) return "";
    return "/api/translate-lab/" + taskId + "/audio/" + shotIndex;
  }

  // ── 字幕 & 合成 ─────────────────────────────────────
  function showSubtitleSection(srtPath) {
    var section = $("#labSubtitleSection");
    var preview = $("#labSubtitlePreview");
    var download = $("#labSubtitleDownload");
    if (!section) return;
    section.hidden = false;
    var url = "/api/translate-lab/" + D.taskId + "/subtitle";
    if (download) {
      download.href = url;
    }
    if (preview) {
      preview.textContent = "加载字幕中...";
      fetch(url, { credentials: "same-origin" })
        .then(function (r) { return r.ok ? r.text() : Promise.reject(r.status); })
        .then(function (text) { preview.textContent = text || "(空字幕)"; })
        .catch(function (err) {
          preview.textContent = "字幕加载失败 (" + err + "，文件路径: " + srtPath + ")";
        });
    }
  }

  function showComposeSection(videoUrl) {
    var section = $("#labComposeSection");
    var video = $("#labComposeVideo");
    var download = $("#labComposeDownload");
    if (!section) return;
    section.hidden = false;
    if (video && videoUrl && videoUrl !== "#") {
      video.src = videoUrl;
    }
    if (download) {
      download.href = videoUrl || "#";
    }
  }

  // ── 错误 ────────────────────────────────────────────
  function showError(msg) {
    var box = $("#labError");
    var text = $("#labErrorText");
    if (!box || !text) return;
    text.textContent = msg;
    box.hidden = false;
  }

  // ── 暴露给模板 ───────────────────────────────────────
  window.TranslateLab = {
    initList: initList,
    initDetail: initDetail,
  };
})();
