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

  var pollTimer = null;
  var selection = { A: "", B: "" };
  var libraryState = {
    A: { q: "", items: [], loaded: false, loading: false, error: "", total: 0 },
    B: { q: "", items: [], loaded: false, loading: false, error: "", total: 0 }
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
    return name && name !== voiceId ? name + " (" + voiceId + ")" : (name || voiceId || "未命名音色");
  }

  function artifactPathUrl(relpath) {
    return apiBase + "/" + encodeURIComponent(taskId) + "/artifact-path?path=" + encodeURIComponent(relpath);
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

    var header = document.createElement("header");
    var titleWrap = document.createElement("div");
    var title = document.createElement("h4");
    title.textContent = "Speaker " + speaker;
    var subtitle = document.createElement("small");
    subtitle.textContent = "该说话人的目标音色";
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

    var tracks = task && task.speaker_audio_tracks ? task.speaker_audio_tracks : {};
    var track = tracks && tracks[speaker] ? tracks[speaker] : {};
    if (track.relative_path) {
      var trackAudio = document.createElement("audio");
      trackAudio.controls = true;
      trackAudio.preload = "metadata";
      trackAudio.src = artifactPathUrl(track.relative_path);
      card.appendChild(trackAudio);
    }

    var label = document.createElement("label");
    label.appendChild(document.createTextNode("候选音色"));
    var select = document.createElement("select");
    select.dataset.speaker = speaker;
    var placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "请选择音色";
    select.appendChild(placeholder);
    select.disabled = !editable;
    label.appendChild(select);
    card.appendChild(label);

    var reasonText = reviewReason(task, speaker);
    if (reasonText) {
      var reason = document.createElement("div");
      reason.className = "dialogue-speaker-reason";
      reason.textContent = reasonText;
      card.appendChild(reason);
    }

    var speakerLibrary = libraryState[speaker] || {};
    var candidates = mergeVoiceOptions(
      Array.isArray(profile.candidates) ? profile.candidates : [],
      speakerLibrary.items || []
    );
    candidates.forEach(function (candidate) {
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

    var tools = document.createElement("div");
    tools.className = "dialogue-voice-library-tools";
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
    var loadBtn = document.createElement("button");
    loadBtn.type = "button";
    loadBtn.textContent = speakerLibrary.loading ? "加载中..." : "查音色库";
    loadBtn.disabled = !editable || !!speakerLibrary.loading;
    loadBtn.addEventListener("click", function () {
      libraryState[speaker].q = search.value.trim();
      loadVoiceLibrary(speaker);
    });
    tools.appendChild(search);
    tools.appendChild(loadBtn);
    card.appendChild(tools);

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
    updateStatus(task || {});
    gridEl.innerHTML = "";
    ["A", "B"].forEach(function (speaker) {
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
