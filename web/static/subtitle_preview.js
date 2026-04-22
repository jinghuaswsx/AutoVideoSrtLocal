(function () {
  const SAMPLE_TEXT = "Tiktok and facebook shot videos!";

  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  function coerceNumber(value, fallback) {
    const num = Number(value);
    return Number.isFinite(num) ? num : fallback;
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
    };

    let dragging = false;

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
    };

    function applySampleLines(lines) {
      const nextLines = Array.isArray(lines) && lines.length ? lines.slice(0, 2) : [SAMPLE_TEXT, SAMPLE_TEXT];
      while (nextLines.length < 2) nextLines.push(SAMPLE_TEXT);
      state.sample_lines = nextLines;
      if (refs.lineA) refs.lineA.textContent = nextLines[0];
      if (refs.lineB) refs.lineB.textContent = nextLines[1];
    }

    function updateVideo() {
      if (!refs.video) return;
      refs.video.src = state.video_url || "";
      refs.video.load?.();
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
    };
  }

  window.createSubtitlePreviewController = createSubtitlePreviewController;
})();
