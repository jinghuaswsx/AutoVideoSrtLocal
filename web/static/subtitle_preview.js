(function () {
  const SAMPLE_TEXT = "Tiktok and facebook shot videos!";

  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  function coerceNumber(value, fallback) {
    const num = Number(value);
    return Number.isFinite(num) ? num : fallback;
  }

  function isVideoFile(file) {
    if (!file) return false;
    if (String(file.type || "").startsWith("video/")) return true;
    return /\.(mp4|mov|m4v|webm|avi|mkv)$/i.test(file.name || "");
  }

  function pickVideoFile(fileList) {
    return Array.from(fileList || []).find(isVideoFile) || null;
  }

  function hasFileDrag(event) {
    return Array.from(event.dataTransfer?.types || []).includes("Files");
  }

  function createSubtitlePreviewController(root, initialPayload) {
    if (!root) {
      throw new Error("subtitle preview root is required");
    }

    const state = {
      video_url: "",
      subtitle_font: "Impact",
      subtitle_size: 14,
      subtitle_position_y: 0.68,
      sample_lines: [SAMPLE_TEXT, SAMPLE_TEXT],
      video_error: "",
      video_file_name: "",
    };

    let dragging = false;
    let dragDepth = 0;
    let localVideoObjectUrl = "";

    const refs = {
      video: root.querySelector('[data-role="video"]'),
      overlay: root.querySelector('[data-role="overlay"]'),
      block: root.querySelector('[data-role="subtitle-block"]'),
      lineA: root.querySelector('[data-role="line-a"]'),
      lineB: root.querySelector('[data-role="line-b"]'),
      font: root.querySelector('[data-role="font"]'),
      sizeGroup: root.querySelector('[data-role="size-group"]'),
      position: root.querySelector('[data-role="position"]'),
      positionLabel: root.querySelector('[data-role="position-label"]'),
      media: root.querySelector('[data-role="media"]'),
      file: root.querySelector('[data-role="file"]'),
      uploadCta: root.querySelector('[data-role="upload-cta"]'),
      uploadTitle: root.querySelector('[data-role="upload-title"]'),
      uploadError: root.querySelector('[data-role="upload-error"]'),
      changeVideo: root.querySelector('[data-role="change-video"]'),
    };

    function applySampleLines(lines) {
      const nextLines = Array.isArray(lines) && lines.length ? lines.slice(0, 2) : [SAMPLE_TEXT, SAMPLE_TEXT];
      while (nextLines.length < 2) nextLines.push(SAMPLE_TEXT);
      state.sample_lines = nextLines;
      if (refs.lineA) refs.lineA.textContent = nextLines[0];
      if (refs.lineB) refs.lineB.textContent = nextLines[1];
    }

    function setUploadError(message) {
      if (refs.uploadError) refs.uploadError.textContent = message || "";
    }

    function updateMediaState(hasVideo) {
      const canShowVideo = Boolean(hasVideo) && !state.video_error;
      if (refs.media) {
        refs.media.classList.toggle("has-video", canShowVideo);
        refs.media.classList.toggle("is-empty", !canShowVideo);
      }
      if (refs.uploadTitle) {
        refs.uploadTitle.textContent = state.video_error || "拖拽视频到这里";
      }
    }

    function clearLocalVideoObjectUrl() {
      if (!localVideoObjectUrl) return;
      URL.revokeObjectURL(localVideoObjectUrl);
      localVideoObjectUrl = "";
    }

    function updateVideo(options = {}) {
      if (!refs.video) return;
      const nextUrl = state.video_url || "";
      state.video_error = "";
      setUploadError("");
      if (!nextUrl) {
        refs.video.removeAttribute("src");
        refs.video.load?.();
        updateMediaState(false);
        return;
      }

      if (refs.video.getAttribute("src") !== nextUrl) {
        refs.video.src = nextUrl;
      }
      updateMediaState(true);
      refs.video.load?.();
      if (options.play) {
        refs.video.play?.().catch(() => {});
      }
    }

    function setLocalVideoFile(file) {
      if (!isVideoFile(file)) {
        state.video_error = "请选择视频文件";
        setUploadError(state.video_error);
        updateMediaState(false);
        return;
      }

      clearLocalVideoObjectUrl();
      localVideoObjectUrl = URL.createObjectURL(file);
      state.video_url = localVideoObjectUrl;
      state.video_file_name = file.name || "本地视频";
      updateVideo({ play: true });
    }

    function updateFont() {
      if (!refs.block) return;
      refs.block.style.fontFamily = `${state.subtitle_font}, "Impact", "Arial Black", sans-serif`;
    }

    function updateSize() {
      if (!refs.block) return;
      const size = clamp(state.subtitle_size, 8, 40);
      const lineHeight = Math.max(1.08, Math.min(1.32, 1.18 + (16 - size) * 0.01));
      refs.block.style.fontSize = `${size}px`;
      refs.block.style.lineHeight = String(lineHeight);
      if (refs.sizeGroup) {
        refs.sizeGroup.querySelectorAll("[data-size]").forEach((btn) => {
          btn.classList.toggle("is-active", Number(btn.dataset.size) === size);
        });
      }
    }

    function updatePosition() {
      const y = clamp(state.subtitle_position_y, 0.12, 0.92);
      state.subtitle_position_y = y;
      if (refs.overlay) {
        const block = refs.block;
        if (block) {
          block.style.top = `${y * 100}%`;
        }
      }
      if (refs.position) refs.position.value = String(y);
      if (refs.positionLabel) refs.positionLabel.textContent = `${(y * 100).toFixed(1)}%`;
      try {
        localStorage.setItem("subtitle_position_y", String(y));
      } catch {}
    }

    function applyState(nextPayload) {
      const payload = nextPayload || {};
      state.video_url = String(payload.video_url || state.video_url || "");
      state.subtitle_font = String(payload.subtitle_font || state.subtitle_font || "Impact");
      state.subtitle_size = coerceNumber(payload.subtitle_size, state.subtitle_size);
      state.subtitle_position_y = coerceNumber(payload.subtitle_position_y, state.subtitle_position_y);
      applySampleLines(payload.sample_lines);
      updateVideo();
      if (refs.font) refs.font.value = state.subtitle_font;
      updateFont();
      updateSize();
      updatePosition();
    }

    function setPositionFromClientY(clientY) {
      const media = refs.media || refs.overlay || root;
      const rect = media.getBoundingClientRect();
      const next = (clientY - rect.top) / rect.height;
      state.subtitle_position_y = clamp(next, 0.12, 0.92);
      updatePosition();
    }

    function beginDrag(event) {
      if (!refs.media) return;
      dragging = true;
      event.preventDefault();
      setPositionFromClientY(event.clientY);
    }

    function endDrag() {
      dragging = false;
    }

    if (refs.font) {
      refs.font.addEventListener("change", () => {
        state.subtitle_font = refs.font.value;
        updateFont();
      });
    }

    if (refs.sizeGroup) {
      refs.sizeGroup.addEventListener("click", (event) => {
        const button = event.target.closest("[data-size]");
        if (!button) return;
        state.subtitle_size = Number(button.dataset.size);
        updateSize();
      });
    }

    if (refs.position) {
      refs.position.addEventListener("input", () => {
        state.subtitle_position_y = coerceNumber(refs.position.value, state.subtitle_position_y);
        updatePosition();
      });
    }

    if (refs.overlay) {
      refs.overlay.addEventListener("pointerdown", beginDrag);
      window.addEventListener("pointermove", (event) => {
        if (!dragging) return;
        setPositionFromClientY(event.clientY);
      });
      window.addEventListener("pointerup", endDrag);
      window.addEventListener("pointercancel", endDrag);
    }

    if (refs.video) {
      refs.video.addEventListener("loadeddata", () => {
        state.video_error = "";
        setUploadError("");
        updateMediaState(Boolean(state.video_url));
      });
      refs.video.addEventListener("error", () => {
        if (!state.video_url) return;
        state.video_error = "视频加载失败，拖拽本地视频重新预览";
        setUploadError(state.video_error);
        updateMediaState(false);
      });
    }

    if (refs.file) {
      refs.file.addEventListener("change", () => {
        if (refs.file.files?.length) {
          setLocalVideoFile(pickVideoFile(refs.file.files));
        }
        refs.file.value = "";
      });
    }

    if (refs.uploadCta) {
      refs.uploadCta.addEventListener("click", () => refs.file?.click());
    }

    if (refs.changeVideo) {
      refs.changeVideo.addEventListener("click", () => refs.file?.click());
    }

    if (refs.media) {
      refs.media.addEventListener("dragenter", (event) => {
        if (!hasFileDrag(event)) return;
        event.preventDefault();
        dragDepth += 1;
        refs.media.classList.add("is-dragover");
      });
      refs.media.addEventListener("dragover", (event) => {
        if (!hasFileDrag(event)) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = "copy";
      });
      refs.media.addEventListener("dragleave", (event) => {
        if (!hasFileDrag(event)) return;
        dragDepth = Math.max(0, dragDepth - 1);
        if (dragDepth === 0) {
          refs.media.classList.remove("is-dragover");
        }
      });
      refs.media.addEventListener("drop", (event) => {
        if (!hasFileDrag(event)) return;
        event.preventDefault();
        dragDepth = 0;
        refs.media.classList.remove("is-dragover");
        setLocalVideoFile(pickVideoFile(event.dataTransfer.files));
      });
    }

    window.addEventListener("beforeunload", clearLocalVideoObjectUrl);

    applyState(initialPayload);

    return {
      setPayload(payload) {
        applyState(payload);
      },
      getValue() {
        return {
          subtitle_font: state.subtitle_font,
          subtitle_size: state.subtitle_size,
          subtitle_position_y: state.subtitle_position_y,
        };
      },
      destroy() {
        clearLocalVideoObjectUrl();
      },
    };
  }

  window.createSubtitlePreviewController = createSubtitlePreviewController;
})();
