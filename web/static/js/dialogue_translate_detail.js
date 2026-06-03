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
  if (!panel || !taskId || !statusEl || !gridEl || !timelineEl || !feedbackEl || !confirmBtn) {
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
      document.getElementById("step-speaker_detect") ||
      document.getElementById("step-asr_clean") ||
      document.getElementById("step-asr_normalize") ||
      document.getElementById("step-asr");
    if (!anchor || !anchor.parentNode) return;
    if (anchor.nextSibling !== panel) {
      anchor.parentNode.insertBefore(panel, anchor.nextSibling);
    }
  }

  function updateStatus(task) {
    var currentReviewStep = String((task && task.current_review_step) || "");
    var steps = task && task.steps ? task.steps : {};
    var voiceStatus = voiceMatchStatus(task);
    var speakerStatus = String(steps.speaker_detect || "pending");
    if (voiceStatus === "waiting") {
      statusEl.textContent = "A/B 候选音色已就绪，确认后将从 alignment 继续。";
      return;
    }
    if (currentReviewStep === "voice_match_ab") {
      statusEl.textContent = "等待确认 A/B 音色。";
      return;
    }
    if (voiceStatus === "done") {
      statusEl.textContent = "A/B 音色已自动匹配，任务会继续推进后续步骤。";
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
    if (voiceStatus === "running") {
      statusEl.textContent = "正在为 Speaker A / B 匹配候选音色...";
      return;
    }
    statusEl.textContent = "等待 speaker_detect / voice_match_ab 完成。";
  }

  function updateConfirmState() {
    var editable = canEditVoices(lastTask || {});
    confirmBtn.hidden = isVoiceMatchDone(lastTask || {});
    confirmBtn.disabled = isSubmitting || !editable || !selection.A || !selection.B;
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
    return windows.slice(0, 3).map(function (window) {
      if (!Array.isArray(window) || window.length < 2) return "";
      return formatTime(window[0]) + " - " + formatTime(window[1]);
    }).filter(Boolean).join(" / ");
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
    title.textContent = "Speaker " + speaker;
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
      sampleLabel.textContent = "原声采样";
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
      windows.textContent = "采样时间范围：" + windowsText;
      overview.appendChild(windows);
    }
    if (overview.children.length) {
      card.appendChild(overview);
    }

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
      var item = document.createElement("li");
      item.className = "dialogue-segment-item";

      var header = document.createElement("header");
      var speaker = document.createElement("span");
      speaker.className = "dialogue-segment-speaker";
      speaker.textContent = "Speaker " + (segment.speaker_id || "?");
      header.appendChild(speaker);

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
      item.appendChild(header);

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

      list.appendChild(item);
    });
    timelineEl.appendChild(list);
  }

  function render(task) {
    lastTask = task || {};
    repositionPanel();
    updateStatus(task || {});
    gridEl.innerHTML = "";
    speakers.forEach(function (speaker) {
      gridEl.appendChild(renderSpeakerCard(task || {}, speaker));
    });
    renderSegmentTimeline(task || {});
    updateConfirmState();
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
      render(task);
    } catch (error) {
      statusEl.textContent = "加载 A/B 音色状态失败。";
      setFeedback(error && error.message ? error.message : "加载失败", "error");
    }
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
          body: JSON.stringify({
            selected_voice_by_speaker: {
              A: selection.A,
              B: selection.B
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

  refresh();
  pollTimer = window.setInterval(refresh, 4000);
  window.addEventListener("beforeunload", function () {
    if (pollTimer) {
      window.clearInterval(pollTimer);
    }
  });
})();
