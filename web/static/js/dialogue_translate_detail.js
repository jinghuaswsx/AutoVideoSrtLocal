(function () {
  var config = window.DIALOGUE_TRANSLATE_DETAIL_CONFIG || {};
  var taskId = config.taskId;
  var apiBase = config.apiBase || "/api/dialogue-translate";
  var panel = document.getElementById("dialogueVoicePanel");
  var statusEl = document.getElementById("dialogueVoiceStatus");
  var gridEl = document.getElementById("dialogueVoiceGrid");
  var feedbackEl = document.getElementById("dialogueVoiceFeedback");
  var confirmBtn = document.getElementById("dialogueVoiceConfirmBtn");
  if (!panel || !taskId || !statusEl || !gridEl || !feedbackEl || !confirmBtn) {
    return;
  }

  var pollTimer = null;
  var selection = { A: "", B: "" };
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

  function optionLabel(candidate) {
    var voiceId = voiceIdOf(candidate);
    var name = voiceNameOf(candidate, voiceId);
    return name && name !== voiceId ? name + " (" + voiceId + ")" : (name || voiceId || "未命名音色");
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
    var voiceStatus = String(steps.voice_match_ab || "pending");
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
    if (voiceStatus === "running") {
      statusEl.textContent = "正在为 Speaker A / B 匹配候选音色...";
      return;
    }
    statusEl.textContent = "等待 speaker_detect / voice_match_ab 完成。";
  }

  function updateConfirmState() {
    confirmBtn.disabled = isSubmitting || !selection.A || !selection.B;
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

  function renderSpeakerCard(task, speaker) {
    var profiles = task && task.speaker_profiles ? task.speaker_profiles : {};
    var profile = profiles[speaker] || {};
    var summary = task && task.speaker_summary ? (task.speaker_summary[speaker] || {}) : {};
    var selectedId = selectedVoiceId(task, speaker, profile);
    if (!selection[speaker]) {
      selection[speaker] = selectedId;
    }

    var card = document.createElement("article");
    card.className = "dialogue-speaker-card";

    var reasonText = reviewReason(task, speaker);
    var summaryHtml = "";
    if (summary.segment_count != null) {
      summaryHtml += '<span>片段 ' + summary.segment_count + "</span>";
    }
    if (summary.duration != null) {
      summaryHtml += '<span>时长 ' + summary.duration + "s</span>";
    }

    card.innerHTML =
      '<header>' +
      '  <div>' +
      '    <h4>Speaker ' + speaker + "</h4>" +
      '    <small>确认该说话人的目标音色</small>' +
      "  </div>" +
      '  <span class="dialogue-speaker-pill">' + speaker + "</span>" +
      "</header>" +
      '<div class="dialogue-speaker-meta">' + (summaryHtml || "<span>等待识别结果</span>") + "</div>" +
      '<label>候选音色<select data-speaker="' + speaker + '"><option value="">请选择音色</option></select></label>' +
      (reasonText ? '<div class="dialogue-speaker-reason">' + reasonText + "</div>" : "");

    var select = card.querySelector("select");
    var candidates = Array.isArray(profile.candidates) ? profile.candidates : [];
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

    return card;
  }

  function render(task) {
    updateStatus(task || {});
    gridEl.innerHTML = "";
    ["A", "B"].forEach(function (speaker) {
      gridEl.appendChild(renderSpeakerCard(task || {}, speaker));
    });
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
    if (isSubmitting || !selection.A || !selection.B) {
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
