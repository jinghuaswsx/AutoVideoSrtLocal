(function () {
  var config = window.DIALOGUE_TRANSLATE_DETAIL_CONFIG || {};
  var taskId = config.taskId;
  var apiBase = config.apiBase || "/api/dialogue-translate";
  var panel = document.getElementById("dialogueVoicePanel");
  var statusEl = document.getElementById("dialogueVoiceStatus");
  var gridEl = document.getElementById("dialogueVoiceGrid");
  var timelineEl = document.getElementById("dialogueSegmentTimeline");
  var feedbackEl = document.getElementById("dialogueVoiceFeedback");
  var confirmBtn = document.getElementById("dialogueVoiceConfirmBtn");

  var speakerConfirmPanel = document.getElementById("dialogueSpeakerConfirmPanel");
  var speakerAliasAInput = document.getElementById("speakerAliasA");
  var speakerAliasBInput = document.getElementById("speakerAliasB");
  var speakerConfirmList = document.getElementById("dialogueSpeakerConfirmList");
  var speakerConfirmBtn = document.getElementById("dialogueSpeakerConfirmBtn");
  var speakerFeedbackEl = document.getElementById("dialogueSpeakerFeedback");

  if (!panel || !taskId || !statusEl || !gridEl || !timelineEl || !feedbackEl || !confirmBtn || !speakerConfirmPanel || !speakerConfirmList || !speakerConfirmBtn) {
    return;
  }

  var speakers = ["A", "B"];
  var pollTimer = null;
  var selection = { A: "", B: "" };
  var libraryState = {
    A: { q: "", items: [], loaded: false, loading: false, error: "", total: 0, recommendedOnly: false },
    B: { q: "", items: [], loaded: false, loading: false, error: "", total: 0, recommendedOnly: false }
  };
  var lastTask = null;
  var isSubmitting = false;
  var lastRefreshRenderSignature = "";
  var subtitleDragging = false;
  var subtitleSize = 14;
  var subtitleRefs = {
    font: document.getElementById("dialogueSubtitleFont"),
    sizeGroup: document.getElementById("dialogueSubtitleSizeGroup"),
    position: document.getElementById("dialogueSubtitlePositionY"),
    positionHint: document.getElementById("dialogueSubtitlePositionHint"),
    previewFrame: document.getElementById("dialogueSubtitlePreviewFrame"),
    previewVideo: document.getElementById("dialogueSubtitlePreviewVideo"),
    previewBlock: document.getElementById("dialogueSubtitlePreviewBlock"),
    previewNote: document.getElementById("dialogueSubtitlePreviewNote"),
    lineA: document.getElementById("dialogueSubtitleLineA"),
    lineB: document.getElementById("dialogueSubtitleLineB")
  };
  var subtitleFontFamilies = {
    "Impact": 'Impact, Haettenschweiler, "Arial Narrow Bold", sans-serif',
    "Oswald Bold": '"Oswald", Impact, "Arial Narrow Bold", sans-serif',
    "Bebas Neue": '"Bebas Neue", Impact, "Arial Narrow Bold", sans-serif',
    "Montserrat ExtraBold": '"Montserrat", "Arial Black", sans-serif',
    "Poppins Bold": '"Poppins", "Arial Black", sans-serif',
    "Anton": '"Anton", Impact, sans-serif'
  };

  function csrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") || "" : "";
  }

  function requestHeaders() {
    var headers = {
      "Content-Type": "application/json",
      "X-Requested-With": "XMLHttpRequest"
    };
    var token = csrfToken();
    if (token) {
      headers["X-CSRFToken"] = token;
    }
    return headers;
  }

  function setFeedback(message, state) {
    feedbackEl.textContent = message || "";
    feedbackEl.dataset.state = state || "";
  }

  function voiceIdOf(value) {
    if (!value) return "";
    if (typeof value === "string") return value.trim();
    if (typeof value === "object") {
      return String(value.voice_id || value.elevenlabs_voice_id || value.id || "").trim();
    }
    return "";
  }

  function voiceNameOf(value, fallback) {
    if (value && typeof value === "object") {
      return String(value.name || value.voice_name || value.label || fallback || "").trim();
    }
    return fallback || "";
  }

  function selectedVoiceId(task, speaker, profile) {
    var selectedBySpeaker = task && task.selected_voice_by_speaker;
    var fromTask = selectedBySpeaker && selectedBySpeaker[speaker];
    var fromProfile = profile && profile.selected_voice;
    return voiceIdOf(fromTask) || voiceIdOf(fromProfile) || "";
  }

  function voiceMatchStatus(task) {
    var steps = task && task.steps ? task.steps : {};
    return String(steps.voice_match_ab || "pending");
  }

  function canEditVoices(task) {
    return voiceMatchStatus(task) === "waiting";
  }

  function isVoiceMatchDone(task) {
    return voiceMatchStatus(task) === "done";
  }

  function optionLabel(candidate) {
    var voiceId = voiceIdOf(candidate);
    var name = voiceNameOf(candidate, voiceId);
    var prefix = [];
    if (candidate && candidate.similarity != null) {
      prefix.push(formatPercent(candidate.similarity, 1) + " 相似");
    }
    var rank = normalizedRank(candidate && candidate.llm_rank);
    if (rank !== null) {
      prefix.push("AI #" + rank);
    }
    var label = name && name !== voiceId ? name + " (" + voiceId + ")" : (name || voiceId || "未命名音色");
    return prefix.length ? prefix.join(" · ") + " · " + label : label;
  }

  function artifactPathUrl(relpath) {
    return apiBase + "/" + encodeURIComponent(taskId) + "/artifact-path?path=" + encodeURIComponent(relpath);
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function safeMediaSrc(url) {
    var raw = String(url == null ? "" : url).trim();
    if (!raw) return "";
    try {
      var parsed = new URL(raw, window.location.origin);
      if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return "";
      if (/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(raw)) return parsed.href;
      return parsed.pathname + parsed.search + parsed.hash;
    } catch (_error) {
      return "";
    }
  }

  function coerceSubtitleSize(value) {
    var next = parseInt(value, 10);
    return Number.isFinite(next) ? next : 14;
  }

  function coerceSubtitlePositionY(value) {
    var next = parseFloat(value);
    if (!Number.isFinite(next)) return 0.68;
    return Math.max(0.12, Math.min(0.92, next));
  }

  function setSubtitlePreviewNote(message, mode) {
    if (!subtitleRefs.previewNote) return;
    subtitleRefs.previewNote.textContent = message || "";
    subtitleRefs.previewNote.dataset.mode = mode || "note";
  }

  function setSubtitleSampleLines(lines) {
    var values = Array.isArray(lines) && lines.length
      ? lines.slice(0, 2)
      : ["Tiktok and facebook shot videos!", "Tiktok and facebook shot videos!"];
    while (values.length < 2) {
      values.push("Tiktok and facebook shot videos!");
    }
    if (subtitleRefs.lineA) subtitleRefs.lineA.textContent = values[0];
    if (subtitleRefs.lineB) subtitleRefs.lineB.textContent = values[1];
  }

  function attachSubtitlePreviewVideo(src) {
    if (!subtitleRefs.previewVideo) return false;
    var videoSrc = safeMediaSrc(src);
    if (!videoSrc) return false;
    if (subtitleRefs.previewVideo.getAttribute("src") === videoSrc) return true;
    subtitleRefs.previewVideo.preload = "metadata";
    subtitleRefs.previewVideo.src = videoSrc;
    subtitleRefs.previewVideo.load();
    return true;
  }

  function setSubtitleFont(value) {
    if (!subtitleRefs.font) return;
    var next = value || "Impact";
    var option = Array.prototype.find.call(subtitleRefs.font.options || [], function (opt) {
      return opt.value === next;
    });
    subtitleRefs.font.value = option ? option.value : "Impact";
    syncSubtitlePreview();
  }

  function setSubtitleSize(value) {
    subtitleSize = coerceSubtitleSize(value);
    if (subtitleRefs.sizeGroup) {
      subtitleRefs.sizeGroup.querySelectorAll("button[data-size]").forEach(function (button) {
        button.classList.toggle("is-active", coerceSubtitleSize(button.dataset.size) === subtitleSize);
      });
    }
    syncSubtitlePreview();
  }

  function setSubtitlePositionY(value) {
    var next = coerceSubtitlePositionY(value);
    if (subtitleRefs.position) {
      subtitleRefs.position.value = String(next);
    }
    if (subtitleRefs.positionHint) {
      subtitleRefs.positionHint.textContent = Math.round(next * 100) + "%";
    }
    syncSubtitlePreview();
  }

  function currentSubtitlePositionY() {
    return coerceSubtitlePositionY(subtitleRefs.position ? subtitleRefs.position.value : 0.68);
  }

  function syncSubtitlePreview() {
    if (!subtitleRefs.previewBlock) return;
    var font = subtitleRefs.font ? (subtitleRefs.font.value || "Impact") : "Impact";
    subtitleRefs.previewBlock.style.fontFamily = subtitleFontFamilies[font] || subtitleFontFamilies.Impact;
    subtitleRefs.previewBlock.style.fontSize = coerceSubtitleSize(subtitleSize) + "px";
    subtitleRefs.previewBlock.style.top = (currentSubtitlePositionY() * 100) + "%";
  }

  function updateSubtitlePositionFromClientY(clientY) {
    if (!subtitleRefs.previewFrame) return;
    var rect = subtitleRefs.previewFrame.getBoundingClientRect();
    if (!rect.height) return;
    setSubtitlePositionY((clientY - rect.top) / rect.height);
  }

  function getSubtitlePayload() {
    return {
      subtitle_font: subtitleRefs.font ? (subtitleRefs.font.value || "Impact") : "Impact",
      subtitle_size: coerceSubtitleSize(subtitleSize),
      subtitle_position_y: currentSubtitlePositionY(),
      subtitle_position: "bottom"
    };
  }

  async function loadSubtitlePreviewPayload() {
    if (!subtitleRefs.previewFrame) return;
    setSubtitlePreviewNote("正在加载当前任务源视频预览...", "note");
    try {
      var response = await fetch(
        apiBase + "/" + encodeURIComponent(taskId) + "/subtitle-preview",
        { cache: "no-store", credentials: "same-origin" }
      );
      if (!response.ok) {
        throw new Error("HTTP " + response.status);
      }
      var payload = await response.json();
      setSubtitleFont(payload.subtitle_font || "Impact");
      setSubtitleSize(payload.subtitle_size == null ? 14 : payload.subtitle_size);
      setSubtitlePositionY(payload.subtitle_position_y == null ? 0.68 : payload.subtitle_position_y);
      setSubtitleSampleLines(payload.sample_lines);
      if (attachSubtitlePreviewVideo(payload.video_url || "")) {
        setSubtitlePreviewNote("已加载当前任务源视频，可直接检查字幕位置、字体和字号。", "success");
      } else {
        setSubtitlePreviewNote("源视频预览暂不可用，字幕位置仍会按同一坐标保存。", "note");
      }
    } catch (error) {
      setSubtitlePreviewNote(
        "源视频预览加载失败，字幕设置仍会保存到后续生成流程。",
        "error"
      );
    }
  }

  function formatPercent(value, digits) {
    var number = Number(value);
    if (!Number.isFinite(number)) return "";
    return (number * 100).toFixed(digits == null ? 1 : digits) + "%";
  }

  function formatRate(value) {
    var number = Number(value);
    return Number.isFinite(number) && number > 0 ? number.toFixed(2) : "";
  }

  function normalizedRank(value) {
    var number = Number(value);
    if (!Number.isFinite(number) || number <= 0) return null;
    return Math.trunc(number);
  }

  function formatDuration(value) {
    var number = Number(value);
    if (!Number.isFinite(number) || number < 0) return "";
    return number.toFixed(number >= 10 ? 1 : 3).replace(/\.0+$/, "") + "s";
  }

  function formatTime(value) {
    var number = Number(value);
    if (!Number.isFinite(number) || number < 0) {
      number = 0;
    }
    var minutes = Math.floor(number / 60);
    var seconds = number - minutes * 60;
    return String(minutes).padStart(2, "0") + ":" + seconds.toFixed(3).padStart(6, "0");
  }

  function segmentTranslation(segment) {
    return String(
      segment.translated_text ||
      segment.translated ||
      segment.target_text ||
      segment.tts_text ||
      ""
    ).trim();
  }

  function mergeVoiceOptions(candidates, libraryItems) {
    var merged = [];
    var seen = {};
    (candidates || []).concat(libraryItems || []).forEach(function (item) {
      var voiceId = voiceIdOf(item);
      if (!voiceId || seen[voiceId]) return;
      seen[voiceId] = true;
      merged.push(item);
    });
    return merged;
  }

  function reviewReason(task, speaker) {
    var segments = Array.isArray(task && task.dialogue_segments) ? task.dialogue_segments : [];
    var reasons = [];
    segments.forEach(function (segment) {
      if (segment && segment.speaker_id === speaker && segment.review_reason) {
        reasons.push(String(segment.review_reason));
      }
    });
    return reasons.length ? reasons.join(" / ") : "";
  }

  function repositionPanel() {
    var anchor =
      document.getElementById("step-voice_match_ab") ||
      document.getElementById("step-speaker_confirm") ||
      document.getElementById("step-speaker_detect") ||
      document.getElementById("step-asr_clean") ||
      document.getElementById("step-asr_normalize") ||
      document.getElementById("step-asr");
    if (!anchor || !anchor.parentNode) return;
    if (speakerConfirmPanel && anchor.nextSibling !== speakerConfirmPanel) {
      anchor.parentNode.insertBefore(speakerConfirmPanel, anchor.nextSibling);
    }
    if (panel && speakerConfirmPanel && speakerConfirmPanel.nextSibling !== panel) {
      anchor.parentNode.insertBefore(panel, speakerConfirmPanel.nextSibling);
    } else if (panel && !speakerConfirmPanel && anchor.nextSibling !== panel) {
      anchor.parentNode.insertBefore(panel, anchor.nextSibling);
    }
  }

  function updateStatus(task) {
    var currentReviewStep = String((task && task.current_review_step) || "");
    var steps = task && task.steps ? task.steps : {};
    var voiceStatus = voiceMatchStatus(task);
    var speakerStatus = String(steps.speaker_detect || "pending");
    var confirmStatus = String(steps.speaker_confirm || "pending");
    if (confirmStatus === "waiting" || currentReviewStep === "speaker_confirm") {
      statusEl.textContent = "第一步：等待确认说话人分配和别名设置。";
      return;
    }
    if (voiceStatus === "waiting") {
      statusEl.textContent = "第二步：A/B 候选音色已就绪，确认后将从 alignment 继续。";
      return;
    }
    if (currentReviewStep === "voice_match_ab") {
      statusEl.textContent = "第二步：等待确认 A/B 音色。";
      return;
    }
    if (voiceStatus === "done") {
      statusEl.textContent = "A/B 音色已确认，任务会继续推进后续步骤。";
      return;
    }
    if (speakerStatus === "failed" || voiceStatus === "failed") {
      statusEl.textContent = String((task && task.error) || "A/B 音色流程失败，请检查上方步骤。");
      return;
    }
    if (speakerStatus === "running") {
      statusEl.textContent = "正在识别对话说话人...";
      return;
    }
    if (confirmStatus === "running") {
      statusEl.textContent = "正在保存并处理说话人确认...";
      return;
    }
    if (voiceStatus === "running") {
      statusEl.textContent = "正在为 Speaker A / B 匹配候选音色...";
      return;
    }
    statusEl.textContent = "等待 speaker_detect / speaker_confirm / voice_match_ab 完成。";
  }

  function updateConfirmState() {
    var editable = canEditVoices(lastTask || {});
    confirmBtn.hidden = isVoiceMatchDone(lastTask || {});
    confirmBtn.disabled = isSubmitting || !editable || !selection.A || !selection.B;
  }

  function taskRenderSignature(task) {
    try {
      return JSON.stringify(task || {});
    } catch (_error) {
      return "";
    }
  }

  function hasActiveDialogueAudio() {
    return Array.prototype.some.call(panel.querySelectorAll("audio"), function (audio) {
      return audio.paused === false && audio.ended === false;
    });
  }

  function ensureSelectedOption(select, selectedId, profile) {
    if (!selectedId) return;
    var exists = Array.prototype.some.call(select.options, function (option) {
      return option.value === selectedId;
    });
    if (exists) return;
    var option = document.createElement("option");
    option.value = selectedId;
    option.textContent = voiceNameOf(profile && profile.selected_voice, selectedId);
    select.appendChild(option);
  }

  async function loadVoiceLibrary(speaker) {
    var state = libraryState[speaker];
    if (!state || state.loading) return;
    state.loading = true;
    state.error = "";
    render(lastTask || {});
    try {
      var params = new URLSearchParams({
        speaker: speaker,
        page: "1",
        page_size: "200"
      });
      if (state.q) {
        params.set("q", state.q);
      }
      var response = await fetch(
        apiBase + "/" + encodeURIComponent(taskId) + "/voice-library?" + params.toString(),
        { credentials: "same-origin" }
      );
      var payload = {};
      try {
        payload = await response.json();
      } catch (_error) {
        payload = {};
      }
      if (!response.ok) {
        throw new Error(payload.error || ("HTTP " + response.status));
      }
      state.items = Array.isArray(payload.items) ? payload.items : [];
      state.total = Number(payload.total || state.items.length || 0);
      state.loaded = true;
    } catch (error) {
      state.error = error && error.message ? error.message : "音色库加载失败";
    } finally {
      state.loading = false;
      render(lastTask || {});
    }
  }

  function rankingMap(profile) {
    var map = {};
    (Array.isArray(profile && profile.voice_ai_rankings) ? profile.voice_ai_rankings : [])
      .forEach(function (row) {
        var voiceId = voiceIdOf(row);
        if (voiceId) {
          map[voiceId] = row;
        }
      });
    return map;
  }

  function applyRanking(candidate, ranking) {
    var copy = Object.assign({}, candidate || {});
    if (!ranking || typeof ranking !== "object") {
      return copy;
    }
    [
      "llm_rank",
      "llm_reason_summary",
      "reason_summary",
      "source_words_per_second",
      "preview_words_per_second",
      "speed_match_score",
      "voice_speed_status"
    ].forEach(function (key) {
      if (copy[key] == null && ranking[key] != null) {
        copy[key] = ranking[key];
      }
    });
    return copy;
  }

  function collectVoiceRows(profile, libraryItems) {
    var rows = [];
    var seen = {};
    var rankings = rankingMap(profile || {});
    var candidates = Array.isArray(profile && profile.candidates) ? profile.candidates : [];
    candidates.forEach(function (candidate, index) {
      var voiceId = voiceIdOf(candidate);
      if (!voiceId || seen[voiceId]) return;
      var row = applyRanking(candidate, rankings[voiceId]);
      row.voice_id = voiceId;
      row._recommended = true;
      row._similarity_rank = index + 1;
      seen[voiceId] = true;
      rows.push(row);
    });

    var selected = profile && profile.selected_voice;
    var selectedId = voiceIdOf(selected);
    if (selectedId && !seen[selectedId]) {
      var selectedRow = applyRanking(selected, rankings[selectedId]);
      selectedRow.voice_id = selectedId;
      selectedRow._recommended = true;
      selectedRow._similarity_rank = rows.length + 1;
      seen[selectedId] = true;
      rows.push(selectedRow);
    }

    (libraryItems || []).forEach(function (item) {
      var voiceId = voiceIdOf(item);
      if (!voiceId || seen[voiceId]) return;
      var row = applyRanking(item, rankings[voiceId]);
      row.voice_id = voiceId;
      row._recommended = false;
      row._similarity_rank = null;
      seen[voiceId] = true;
      rows.push(row);
    });
    return rows;
  }

  function sortVoiceRows(rows) {
    return rows.slice().sort(function (a, b) {
      if (!!a._recommended !== !!b._recommended) {
        return a._recommended ? -1 : 1;
      }
      var aRank = normalizedRank(a.llm_rank);
      var bRank = normalizedRank(b.llm_rank);
      if (aRank !== null && bRank !== null && aRank !== bRank) {
        return aRank - bRank;
      }
      if (aRank !== null && bRank === null) return -1;
      if (aRank === null && bRank !== null) return 1;
      var aSimilarityRank = Number(a._similarity_rank || Number.MAX_SAFE_INTEGER);
      var bSimilarityRank = Number(b._similarity_rank || Number.MAX_SAFE_INTEGER);
      if (aSimilarityRank !== bSimilarityRank) {
        return aSimilarityRank - bSimilarityRank;
      }
      return voiceNameOf(a, a.voice_id).localeCompare(voiceNameOf(b, b.voice_id));
    });
  }

  function voiceMeta(candidate) {
    return [
      candidate && candidate.gender,
      candidate && candidate.accent,
      candidate && candidate.age,
      candidate && (candidate.description || candidate.descriptive)
    ].filter(Boolean).map(escapeHtml).join(" · ");
  }

  function voiceSpeedHtml(candidate) {
    if (!candidate) return "";
    var status = String(candidate.voice_speed_status || "");
    var sourceRate = formatRate(candidate.source_words_per_second);
    var previewRate = formatRate(candidate.preview_words_per_second);
    var speedScore = formatPercent(candidate.speed_match_score, 0);
    if (status === "source_rate_unavailable") {
      return '<div class="vs-row-speed vs-row-speed-missing">原视频语速不可比，按音色排序，等待 AI 推荐排名</div>';
    }
    if (!sourceRate && !previewRate && !speedScore) {
      return "";
    }
    if (!previewRate || status === "missing_preview_rate") {
      var sourceText = sourceRate ? "原视频 " + escapeHtml(sourceRate) + " 词/秒 · " : "";
      return '<div class="vs-row-speed vs-row-speed-missing">' + sourceText + '语速未维护，按音色排序</div>';
    }
    return [
      '<div class="vs-row-speed">',
      sourceRate ? '<span>原视频 ' + escapeHtml(sourceRate) + ' 词/秒</span>' : "",
      '<span>Preview ' + escapeHtml(previewRate) + ' 词/秒</span>',
      speedScore
        ? '<span class="vs-speed-match-pill"><span class="vs-speed-match-label">语速参考</span><span class="vs-speed-match-value">' + escapeHtml(speedScore) + '</span></span>'
        : "",
      '</div>'
    ].join("");
  }

  function voiceBadgesHtml(candidate) {
    var badges = [];
    if (candidate && candidate._recommended && candidate.similarity != null) {
      badges.push('<span class="vs-row-sim">' + escapeHtml(formatPercent(candidate.similarity, 1)) + ' 相似</span>');
    }
    if (candidate && candidate._recommended && Number.isFinite(Number(candidate._similarity_rank))) {
      badges.push('<span class="vs-row-rank">#' + escapeHtml(candidate._similarity_rank) + '</span>');
    }
    var rank = normalizedRank(candidate && candidate.llm_rank);
    if (rank !== null) {
      var reason = String((candidate && (candidate.llm_reason_summary || candidate.reason_summary)) || "").trim();
      badges.push(
        '<span class="vs-row-ai-rank" title="' + escapeHtml(reason || "大模型推荐排名") + '">AI #' +
        escapeHtml(rank) + (reason ? " · " + escapeHtml(reason) : "") + '</span>'
      );
    }
    return badges.join("");
  }

  function voiceRowHtml(candidate, speaker, selectedId, editable) {
    var voiceId = voiceIdOf(candidate);
    if (!voiceId) return "";
    var selected = selectedId === voiceId;
    var classes = ["vs-row"];
    if (candidate._recommended) classes.push("recommended");
    if (selected) classes.push("selected");
    var previewUrl = safeMediaSrc(candidate.preview_local_url || candidate.preview_url || "");
    var preview = previewUrl
      ? '<audio controls preload="none" src="' + escapeHtml(previewUrl) + '"></audio>'
      : '<span class="dialogue-voice-library-meta">暂无试听</span>';
    var buttonText = selected ? "已选" : (editable ? "选此音色" : "候选");
    return [
      '<div class="' + classes.join(" ") + '" data-speaker="' + escapeHtml(speaker) + '" data-voice-id="' + escapeHtml(voiceId) + '" data-voice-name="' + escapeHtml(voiceNameOf(candidate, voiceId)) + '" tabindex="0">',
      '<div class="vs-row-main">',
      '<div class="vs-row-name">' + voiceBadgesHtml(candidate) + escapeHtml(voiceNameOf(candidate, voiceId)) + '</div>',
      '<div class="vs-row-meta">' + voiceMeta(candidate) + '</div>',
      voiceSpeedHtml(candidate),
      '</div>',
      preview,
      '<button class="vs-row-select-btn" type="button" ' + (!editable ? "disabled" : "") + '>' + escapeHtml(buttonText) + '</button>',
      '</div>'
    ].join("");
  }

  function selectedVoiceLabel(profile, selectedId, rows) {
    if (!selectedId) return "";
    var selected = (rows || []).find(function (row) {
      return voiceIdOf(row) === selectedId;
    }) || (profile && profile.selected_voice);
    var label = voiceNameOf(selected, selectedId);
    return label && label !== selectedId ? label + " (" + selectedId + ")" : label;
  }

  function sampleWindowsText(profile) {
    var windows = Array.isArray(profile && profile.sample_windows) ? profile.sample_windows : [];
    if (!windows.length) return "";
    return sortSampleWindows(windows).slice(0, 3).map(function (window) {
      if (!Array.isArray(window) || window.length < 2) return "";
      return formatTime(window[0]) + " - " + formatTime(window[1]);
    }).filter(Boolean).join(" / ");
  }

  function sortSampleWindows(windows) {
    return (windows || []).slice().sort(function (a, b) {
      var aStart = Array.isArray(a) ? Number(a[0]) : Number.NaN;
      var bStart = Array.isArray(b) ? Number(b[0]) : Number.NaN;
      if (Number.isFinite(aStart) && Number.isFinite(bStart)) {
        return aStart - bStart;
      }
      if (Number.isFinite(aStart)) return -1;
      if (Number.isFinite(bStart)) return 1;
      return 0;
    });
  }

  function normalizedSpeakerId(value) {
    var speaker = String(value || "").trim().toUpperCase();
    return speaker === "A" || speaker === "B" ? speaker : "";
  }

  function filterSpeakerSegments(task, speaker) {
    var target = normalizedSpeakerId(speaker);
    var segments = Array.isArray(task && task.dialogue_segments) ? task.dialogue_segments : [];
    return segments.filter(function (segment) {
      return segment && typeof segment === "object" && normalizedSpeakerId(segment.speaker_id) === target;
    }).slice().sort(function (a, b) {
      var aStart = Number(a.start_time);
      var bStart = Number(b.start_time);
      if (Number.isFinite(aStart) && Number.isFinite(bStart) && aStart !== bStart) {
        return aStart - bStart;
      }
      return Number(a.index || 0) - Number(b.index || 0);
    });
  }

  function appendSegmentMeta(header, segment, options) {
    if (!options || options.showSpeaker !== false) {
      var speaker = document.createElement("span");
      speaker.className = "dialogue-segment-speaker";
      var aliases = lastTask && lastTask.speaker_aliases ? lastTask.speaker_aliases : {};
      var alias = aliases[segment.speaker_id];
      speaker.textContent = "Speaker " + (segment.speaker_id || "?") + (alias ? " (" + alias + ")" : "");
      header.appendChild(speaker);
    }

    var time = document.createElement("span");
    time.className = "dialogue-segment-meta";
    time.textContent = formatTime(segment.start_time) + " - " + formatTime(segment.end_time);
    header.appendChild(time);

    if (segment.speaker_confidence != null) {
      var confidence = document.createElement("span");
      confidence.className = "dialogue-segment-meta";
      confidence.textContent = "置信度 " + Math.round(Number(segment.speaker_confidence || 0) * 100) + "%";
      header.appendChild(confidence);
    }

    if (segment.review_reason) {
      var review = document.createElement("span");
      review.className = "dialogue-segment-meta";
      review.textContent = String(segment.review_reason);
      header.appendChild(review);
    }
  }

  function appendSegmentTextAndAudio(item, segment, position) {
    var text = document.createElement("p");
    text.className = "dialogue-segment-text";
    text.textContent = String(segment.text || segment.source_text || "第 " + (position + 1) + " 句");
    item.appendChild(text);

    var translated = segmentTranslation(segment);
    if (translated) {
      var translatedEl = document.createElement("p");
      translatedEl.className = "dialogue-segment-translation";
      translatedEl.textContent = translated;
      item.appendChild(translatedEl);
    }

    if (segment.source_audio_relpath) {
      var audio = document.createElement("audio");
      audio.controls = true;
      audio.preload = "metadata";
      audio.src = artifactPathUrl(segment.source_audio_relpath);
      item.appendChild(audio);
    } else {
      var missing = document.createElement("div");
      missing.className = "dialogue-segment-empty";
      missing.textContent = "该句原声尚未生成。";
      item.appendChild(missing);
    }
  }

  function renderDialogueSegmentItem(segment, position, options) {
    var item = document.createElement("li");
    item.className = options && options.itemClass ? options.itemClass : "dialogue-segment-item";

    var header = document.createElement("header");
    appendSegmentMeta(header, segment, options || {});
    item.appendChild(header);

    appendSegmentTextAndAudio(item, segment, position);
    return item;
  }

  function renderSpeakerSentenceReview(task, speaker) {
    var segments = filterSpeakerSegments(task, speaker);
    var section = document.createElement("section");
    section.className = "dialogue-speaker-sentence-review";

    var head = document.createElement("div");
    head.className = "dialogue-speaker-sentence-head";
    var title = document.createElement("h5");
    title.textContent = "该说话人逐句原声";
    var count = document.createElement("span");
    count.textContent = segments.length + " 句 ASR 对照";
    head.appendChild(title);
    head.appendChild(count);
    section.appendChild(head);

    if (!segments.length) {
      var empty = document.createElement("div");
      empty.className = "dialogue-segment-empty";
      empty.textContent = "speaker_detect 完成后显示该说话人的逐句 ASR 和原声。";
      section.appendChild(empty);
      return section;
    }

    var list = document.createElement("ol");
    list.className = "dialogue-speaker-sentence-list";
    segments.forEach(function (segment, position) {
      list.appendChild(renderDialogueSegmentItem(segment, position, {
        itemClass: "dialogue-speaker-sentence-item",
        showSpeaker: false
      }));
    });
    section.appendChild(list);
    return section;
  }

  function bindVoiceRows(card, speaker, editable) {
    var rows = card.querySelectorAll(".dialogue-voice-candidates .vs-row[data-voice-id]");
    rows.forEach(function (row) {
      function choose() {
        if (!editable) return;
        selection[speaker] = row.dataset.voiceId || "";
        setFeedback("", "");
        render(lastTask || {});
      }
      row.addEventListener("click", function (event) {
        if (event.target.tagName === "AUDIO" || event.target.closest("audio")) return;
        choose();
      });
      row.addEventListener("keydown", function (event) {
        if (event.key !== "Enter" && event.key !== " ") return;
        if (event.target.tagName === "AUDIO" || event.target.closest("audio")) return;
        event.preventDefault();
        choose();
      });
    });
  }

  function renderSpeakerCard(task, speaker) {
    var profiles = task && task.speaker_profiles ? task.speaker_profiles : {};
    var profile = profiles[speaker] || {};
    var summary = task && task.speaker_summary ? (task.speaker_summary[speaker] || {}) : {};
    var selectedId = selectedVoiceId(task, speaker, profile);
    var editable = canEditVoices(task);
    if (!selection[speaker]) {
      selection[speaker] = selectedId;
    }

    var card = document.createElement("article");
    card.className = "dialogue-speaker-card";
    if (selectedId) {
      card.classList.add("is-selected");
    }

    var header = document.createElement("header");
    var titleWrap = document.createElement("div");
    var title = document.createElement("h4");
    var aliases = task && task.speaker_aliases ? task.speaker_aliases : {};
    var alias = aliases[speaker];
    title.textContent = "Speaker " + speaker + (alias ? " (" + alias + ")" : "");
    var subtitle = document.createElement("small");
    var aiCount = (Array.isArray(profile.voice_ai_rankings) ? profile.voice_ai_rankings : [])
      .filter(function (row) { return normalizedRank(row && row.llm_rank) !== null; })
      .length;
    var candidateCount = Array.isArray(profile.candidates) ? profile.candidates.length : 0;
    var langLabel = String(task.target_lang || task.target_language || "").trim().toUpperCase();
    subtitle.textContent = [
      langLabel ? langLabel + " 音色库" : "目标音色",
      "向量推荐 " + candidateCount + " 个",
      "AI 排名 " + aiCount + " 个"
    ].join(" · ");
    var pill = document.createElement("span");
    pill.className = "dialogue-speaker-pill";
    pill.textContent = speaker;
    titleWrap.appendChild(title);
    titleWrap.appendChild(subtitle);
    header.appendChild(titleWrap);
    header.appendChild(pill);
    card.appendChild(header);

    var meta = document.createElement("div");
    meta.className = "dialogue-speaker-meta";
    if (summary.segment_count != null) {
      var segmentCount = document.createElement("span");
      segmentCount.textContent = "片段 " + summary.segment_count;
      meta.appendChild(segmentCount);
    }
    if (summary.duration != null) {
      var duration = document.createElement("span");
      duration.textContent = "时长 " + summary.duration + "s";
      meta.appendChild(duration);
    }
    if (!meta.children.length) {
      var emptyMeta = document.createElement("span");
      emptyMeta.textContent = "等待识别结果";
      meta.appendChild(emptyMeta);
    }
    card.appendChild(meta);

    var overview = document.createElement("div");
    overview.className = "dialogue-speaker-overview";
    var tracks = task && task.speaker_audio_tracks ? task.speaker_audio_tracks : {};
    var track = tracks && tracks[speaker] ? tracks[speaker] : {};
    if (track.relative_path) {
      var sample = document.createElement("div");
      sample.className = "dialogue-speaker-sample";
      var sampleLabel = document.createElement("small");
      sampleLabel.textContent = "全部原声试听";
      var trackAudio = document.createElement("audio");
      trackAudio.controls = true;
      trackAudio.preload = "metadata";
      trackAudio.src = artifactPathUrl(track.relative_path);
      sample.appendChild(sampleLabel);
      sample.appendChild(trackAudio);
      overview.appendChild(sample);
    }
    var windowsText = sampleWindowsText(profile);
    if (windowsText) {
      var windows = document.createElement("div");
      windows.className = "dialogue-speaker-windows";
      windows.textContent = "音色匹配采样范围：" + windowsText;
      overview.appendChild(windows);
    }
    if (overview.children.length) {
      card.appendChild(overview);
    }
    card.appendChild(renderSpeakerSentenceReview(task, speaker));

    var speakerLibrary = libraryState[speaker] || {};
    var rows = sortVoiceRows(collectVoiceRows(
      profile,
      speakerLibrary.recommendedOnly ? [] : (speakerLibrary.items || [])
    ));

    var toolbar = document.createElement("div");
    toolbar.className = "dialogue-voice-toolbar";
    var search = document.createElement("input");
    search.type = "search";
    search.placeholder = "搜索完整音色库";
    search.value = speakerLibrary.q || "";
    search.disabled = !editable;
    search.addEventListener("input", function () {
      libraryState[speaker].q = search.value.trim();
    });
    search.addEventListener("keydown", function (event) {
      if (event.key === "Enter") {
        event.preventDefault();
        loadVoiceLibrary(speaker);
      }
    });

    var select = document.createElement("select");
    select.dataset.speaker = speaker;
    var placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "请选择音色";
    select.appendChild(placeholder);
    select.disabled = !editable;
    rows.forEach(function (candidate) {
      var voiceId = voiceIdOf(candidate);
      if (!voiceId) return;
      var option = document.createElement("option");
      option.value = voiceId;
      option.textContent = optionLabel(candidate);
      select.appendChild(option);
    });
    ensureSelectedOption(select, selectedId, profile);
    select.value = selection[speaker] || selectedId || "";
    select.addEventListener("change", function () {
      selection[speaker] = select.value;
      setFeedback("", "");
      updateConfirmState();
    });

    var loadBtn = document.createElement("button");
    loadBtn.type = "button";
    loadBtn.textContent = speakerLibrary.loading ? "加载中..." : "查音色库";
    loadBtn.disabled = !editable || !!speakerLibrary.loading;
    loadBtn.addEventListener("click", function () {
      libraryState[speaker].q = search.value.trim();
      loadVoiceLibrary(speaker);
    });

    var recommendedOnly = document.createElement("label");
    recommendedOnly.className = "dialogue-recommended-only";
    var recommendedOnlyInput = document.createElement("input");
    recommendedOnlyInput.type = "checkbox";
    recommendedOnlyInput.checked = !!speakerLibrary.recommendedOnly;
    recommendedOnlyInput.addEventListener("change", function () {
      libraryState[speaker].recommendedOnly = recommendedOnlyInput.checked;
      render(lastTask || {});
    });
    recommendedOnly.appendChild(recommendedOnlyInput);
    recommendedOnly.appendChild(document.createTextNode("只看推荐"));

    toolbar.appendChild(search);
    toolbar.appendChild(select);
    toolbar.appendChild(loadBtn);
    toolbar.appendChild(recommendedOnly);
    card.appendChild(toolbar);

    var libraryMeta = document.createElement("div");
    libraryMeta.className = "dialogue-voice-library-meta";
    if (speakerLibrary.error) {
      libraryMeta.textContent = speakerLibrary.error;
    } else if (speakerLibrary.loaded) {
      libraryMeta.textContent = "已加载 " + (speakerLibrary.items || []).length + " / " + (speakerLibrary.total || (speakerLibrary.items || []).length) + " 个音色";
    } else {
      libraryMeta.textContent = "自动候选不合适时，可搜索完整音色库。";
    }
    card.appendChild(libraryMeta);

    var selectedLabel = selectedVoiceLabel(profile, selection[speaker] || selectedId, rows);
    if (selectedLabel) {
      var selectedNote = document.createElement("div");
      selectedNote.className = "dialogue-selected-voice";
      selectedNote.textContent = "当前选择：" + selectedLabel;
      card.appendChild(selectedNote);
    }

    var reasonText = reviewReason(task, speaker);
    if (reasonText) {
      var reason = document.createElement("div");
      reason.className = "dialogue-speaker-reason";
      reason.textContent = reasonText;
      card.appendChild(reason);
    }

    var list = document.createElement("div");
    list.className = "dialogue-voice-candidates";
    list.id = "dialogueVoiceCandidates" + speaker;
    var visibleRows = rows;
    if (speakerLibrary.recommendedOnly) {
      visibleRows = visibleRows.filter(function (row) { return row._recommended; });
    }
    if (!visibleRows.length) {
      list.innerHTML = '<div class="vs-loading">speaker_detect / voice_match_ab 完成后显示候选音色。</div>';
    } else {
      list.innerHTML = visibleRows.map(function (candidate) {
        return voiceRowHtml(candidate, speaker, selection[speaker] || selectedId, editable);
      }).join("");
    }
    card.appendChild(list);
    bindVoiceRows(card, speaker, editable);

    return card;
  }

  function renderSegmentTimeline(task) {
    var segments = Array.isArray(task && task.dialogue_segments) ? task.dialogue_segments : [];
    timelineEl.innerHTML = "";

    var head = document.createElement("div");
    head.className = "dialogue-segment-timeline-head";
    var titleWrap = document.createElement("div");
    var title = document.createElement("h4");
    title.textContent = "逐句说话人时间线";
    var subtitle = document.createElement("small");
    subtitle.textContent = "每句保留原声试听和 A/B 标记";
    titleWrap.appendChild(title);
    titleWrap.appendChild(subtitle);
    head.appendChild(titleWrap);
    timelineEl.appendChild(head);

    if (!segments.length) {
      var empty = document.createElement("div");
      empty.className = "dialogue-segment-empty";
      empty.textContent = "speaker_detect 完成后显示逐句原声。";
      timelineEl.appendChild(empty);
      return;
    }

    var list = document.createElement("ol");
    list.className = "dialogue-segment-list";
    segments.forEach(function (segment, position) {
      if (!segment || typeof segment !== "object") {
        return;
      }
      list.appendChild(renderDialogueSegmentItem(segment, position, { showSpeaker: true }));
    });
    timelineEl.appendChild(list);
  }

  function renderSpeakerConfirmItem(segment, position, task) {
    var item = document.createElement("li");
    item.className = "dialogue-segment-item";

    var header = document.createElement("header");
    
    var time = document.createElement("span");
    time.className = "dialogue-segment-meta";
    time.textContent = formatTime(segment.start_time) + " - " + formatTime(segment.end_time);
    header.appendChild(time);

    if (segment.speaker_confidence != null) {
      var confidence = document.createElement("span");
      confidence.className = "dialogue-segment-meta";
      confidence.textContent = "置信度 " + Math.round(Number(segment.speaker_confidence || 0) * 100) + "%";
      header.appendChild(confidence);
    }
    
    var select = document.createElement("select");
    select.className = "dialogue-speaker-selector";
    select.dataset.index = segment.index;
    
    var aliases = task.speaker_aliases || {};
    var aliasA = speakerAliasAInput.value.trim() || aliases.A || "";
    var aliasB = speakerAliasBInput.value.trim() || aliases.B || "";
    var suffixA = aliasA ? " (" + aliasA + ")" : "";
    var suffixB = aliasB ? " (" + aliasB + ")" : "";

    var optA = document.createElement("option");
    optA.value = "A";
    optA.textContent = "Speaker A" + suffixA;
    if (segment.speaker_id === "B") {
      optA.selected = false;
    } else {
      optA.selected = true;
    }
    select.appendChild(optA);

    var optB = document.createElement("option");
    optB.value = "B";
    optB.textContent = "Speaker B" + suffixB;
    if (segment.speaker_id === "B") {
      optB.selected = true;
    }
    select.appendChild(optB);

    select.addEventListener("change", function () {
      segment.speaker_id = select.value;
    });

    header.appendChild(select);
    item.appendChild(header);

    appendSegmentTextAndAudio(item, segment, position);
    return item;
  }

  function renderSpeakerConfirmPanel(task) {
    if (!task) return;
    
    if (document.activeElement !== speakerAliasAInput && !speakerAliasAInput.value && task.speaker_aliases && task.speaker_aliases.A) {
      speakerAliasAInput.value = task.speaker_aliases.A;
    }
    if (document.activeElement !== speakerAliasBInput && !speakerAliasBInput.value && task.speaker_aliases && task.speaker_aliases.B) {
      speakerAliasBInput.value = task.speaker_aliases.B;
    }

    var segments = Array.isArray(task.dialogue_segments) ? task.dialogue_segments : [];
    speakerConfirmList.innerHTML = "";

    if (!segments.length) {
      var empty = document.createElement("div");
      empty.className = "dialogue-segment-empty";
      empty.textContent = "未找到对话文本片段。";
      speakerConfirmList.appendChild(empty);
      return;
    }

    segments.forEach(function (segment, position) {
      if (!segment || typeof segment !== "object") return;
      speakerConfirmList.appendChild(renderSpeakerConfirmItem(segment, position, task));
    });
  }

  function render(task) {
    lastTask = task || {};
    repositionPanel();
    updateStatus(task || {});

    var steps = task && task.steps ? task.steps : {};
    var confirmStatus = String(steps.speaker_confirm || "pending");
    var currentReviewStep = String((task && task.current_review_step) || "");

    if (confirmStatus === "waiting" || currentReviewStep === "speaker_confirm") {
      speakerConfirmPanel.style.display = "grid";
      panel.style.display = "none";
      renderSpeakerConfirmPanel(task);
    } else if (currentReviewStep === "voice_match_ab" || steps.voice_match_ab === "waiting" || steps.voice_match_ab === "done") {
      speakerConfirmPanel.style.display = "none";
      panel.style.display = "grid";

      gridEl.innerHTML = "";
      speakers.forEach(function (speaker) {
        gridEl.appendChild(renderSpeakerCard(task || {}, speaker));
      });
      renderSegmentTimeline(task || {});
      updateConfirmState();
    } else {
      speakerConfirmPanel.style.display = "none";
      panel.style.display = "none";
    }
  }

  function renderFromRefresh(task) {
    var signature = taskRenderSignature(task);
    if (signature === lastRefreshRenderSignature) {
      return;
    }
    if (hasActiveDialogueAudio()) {
      return;
    }
    if (lastTask && lastTask.steps && lastTask.steps.speaker_confirm === "waiting" && task.steps && task.steps.speaker_confirm === "waiting") {
      if (lastTask.status === task.status && lastTask.current_review_step === task.current_review_step) {
        return;
      }
    }
    render(task);
    lastRefreshRenderSignature = signature;
  }

  async function fetchTaskState() {
    var response = await fetch(apiBase + "/" + encodeURIComponent(taskId), {
      credentials: "same-origin"
    });
    if (!response.ok) {
      throw new Error("HTTP " + response.status);
    }
    return response.json();
  }

  async function refresh() {
    try {
      var task = await fetchTaskState();
      renderFromRefresh(task);
    } catch (error) {
      statusEl.textContent = "加载 A/B 音色状态失败。";
      setFeedback(error && error.message ? error.message : "加载失败", "error");
    }
  }

  if (subtitleRefs.font) {
    subtitleRefs.font.addEventListener("change", function () {
      syncSubtitlePreview();
    });
  }

  if (subtitleRefs.sizeGroup) {
    subtitleRefs.sizeGroup.addEventListener("click", function (event) {
      var button = event.target.closest("button[data-size]");
      if (!button) return;
      setSubtitleSize(button.dataset.size);
    });
  }

  if (subtitleRefs.position) {
    subtitleRefs.position.addEventListener("input", function () {
      setSubtitlePositionY(subtitleRefs.position.value);
    });
  }

  if (subtitleRefs.previewBlock) {
    subtitleRefs.previewBlock.addEventListener("pointerdown", function (event) {
      subtitleDragging = true;
      try {
        subtitleRefs.previewBlock.setPointerCapture(event.pointerId);
      } catch (_error) {
        // ignore
      }
      event.preventDefault();
      updateSubtitlePositionFromClientY(event.clientY);
    });
    subtitleRefs.previewBlock.addEventListener("pointermove", function (event) {
      if (!subtitleDragging) return;
      updateSubtitlePositionFromClientY(event.clientY);
    });
    var endSubtitleDrag = function (event) {
      subtitleDragging = false;
      try {
        subtitleRefs.previewBlock.releasePointerCapture(event.pointerId);
      } catch (_error) {
        // ignore
      }
    };
    subtitleRefs.previewBlock.addEventListener("pointerup", endSubtitleDrag);
    subtitleRefs.previewBlock.addEventListener("pointercancel", endSubtitleDrag);
  }

  confirmBtn.addEventListener("click", async function () {
    if (isSubmitting || !canEditVoices(lastTask || {}) || !selection.A || !selection.B) {
      return;
    }
    isSubmitting = true;
    updateConfirmState();
    setFeedback("正在确认 A/B 音色...", "");
    confirmBtn.textContent = "确认中...";
    try {
      var response = await fetch(
        apiBase + "/" + encodeURIComponent(taskId) + "/confirm-voices",
        {
          method: "POST",
          headers: requestHeaders(),
          body: JSON.stringify(Object.assign({
            selected_voice_by_speaker: {
              A: selection.A,
              B: selection.B
            }
          }, getSubtitlePayload()))
        }
      );
      var payload = {};
      try {
        payload = await response.json();
      } catch (_error) {
        payload = {};
      }
      if (!response.ok) {
        throw new Error(payload.error || ("HTTP " + response.status));
      }
      setFeedback("A/B 音色已确认，任务继续处理中。", "success");
      await refresh();
    } catch (error) {
      setFeedback(error && error.message ? error.message : "确认失败", "error");
    } finally {
      isSubmitting = false;
      confirmBtn.textContent = "确认 A/B 音色并继续";
      updateConfirmState();
    }
  });

  speakerAliasAInput.addEventListener("input", function() {
    var val = speakerAliasAInput.value.trim();
    var suffix = val ? " (" + val + ")" : "";
    speakerConfirmList.querySelectorAll(".dialogue-speaker-selector").forEach(function(select) {
      if (select.options[0]) {
        select.options[0].textContent = "Speaker A" + suffix;
      }
    });
  });

  speakerAliasBInput.addEventListener("input", function() {
    var val = speakerAliasBInput.value.trim();
    var suffix = val ? " (" + val + ")" : "";
    speakerConfirmList.querySelectorAll(".dialogue-speaker-selector").forEach(function(select) {
      if (select.options[1]) {
        select.options[1].textContent = "Speaker B" + suffix;
      }
    });
  });

  speakerConfirmBtn.addEventListener("click", async function () {
    var steps = lastTask && lastTask.steps ? lastTask.steps : {};
    var confirmStatus = String(steps.speaker_confirm || "pending");
    if (isSubmitting || confirmStatus !== "waiting") {
      return;
    }

    isSubmitting = true;
    speakerConfirmBtn.disabled = true;
    speakerConfirmBtn.textContent = "正在处理...";
    speakerFeedbackEl.textContent = "正在保存说话人设置并生成音频样本...";
    speakerFeedbackEl.dataset.state = "";

    try {
      var segments = lastTask && lastTask.dialogue_segments ? lastTask.dialogue_segments : [];
      var segmentsPayload = segments.map(function(seg) {
        return {
          index: seg.index,
          speaker_id: seg.speaker_id
        };
      });

      var aliasA = speakerAliasAInput.value.trim();
      var aliasB = speakerAliasBInput.value.trim();

      var response = await fetch(
        apiBase + "/" + encodeURIComponent(taskId) + "/confirm-speakers",
        {
          method: "POST",
          headers: requestHeaders(),
          body: JSON.stringify({
            dialogue_segments: segmentsPayload,
            speaker_aliases: {
              A: aliasA,
              B: aliasB
            }
          })
        }
      );

      var payload = {};
      try {
        payload = await response.json();
      } catch (_error) {
        payload = {};
      }

      if (!response.ok) {
        throw new Error(payload.error || ("HTTP " + response.status));
      }

      speakerFeedbackEl.textContent = "说话人确认成功，即将进入音色匹配。";
      speakerFeedbackEl.dataset.state = "success";
      await refresh();
    } catch (error) {
      speakerFeedbackEl.textContent = error && error.message ? error.message : "确认失败";
      speakerFeedbackEl.dataset.state = "error";
    } finally {
      isSubmitting = false;
      speakerConfirmBtn.disabled = false;
      speakerConfirmBtn.textContent = "确认说话人与别名并继续";
    }
  });

  setSubtitleFont("Impact");
  setSubtitleSize(14);
  setSubtitlePositionY(0.68);
  loadSubtitlePreviewPayload();
  refresh();
  pollTimer = window.setInterval(refresh, 4000);
  window.addEventListener("beforeunload", function () {
    if (pollTimer) {
      window.clearInterval(pollTimer);
    }
  });
})();
