import UI from "./ui.js";

const REMOTE_PASTE_DELAY_MS = 500;
const PASTE_SINK_ID = "noVNC_windows_clipboard_sink";
const XK_CONTROL_L = 0xffe3;
const XK_V = 0x0076;

let pasteSink = null;
let remotePasteTimer = null;

const bridgeState = {
  lastPasteAt: null,
  lastPasteLength: 0,
  lastShortcutAt: null,
};
window.noVNCPasteBridge = bridgeState;

function isConnected() {
  return !!UI.rfb && !!UI.connected;
}

function getPasteSink() {
  if (pasteSink && document.body.contains(pasteSink)) {
    return pasteSink;
  }

  pasteSink = document.createElement("textarea");
  pasteSink.id = PASTE_SINK_ID;
  pasteSink.setAttribute("aria-hidden", "true");
  pasteSink.setAttribute("autocomplete", "off");
  pasteSink.setAttribute("autocapitalize", "off");
  pasteSink.setAttribute("spellcheck", "false");
  pasteSink.style.cssText = [
    "position:fixed",
    "left:0",
    "top:0",
    "width:2px",
    "height:2px",
    "opacity:0",
    "z-index:-1",
    "resize:none",
    "pointer-events:none",
  ].join(";");
  document.body.appendChild(pasteSink);
  return pasteSink;
}

function focusPasteSink() {
  const sink = getPasteSink();
  sink.value = "";
  sink.focus({ preventScroll: true });
  sink.select();
  return sink;
}

function isPasteSink(target) {
  return target && target.id === PASTE_SINK_ID;
}

function isPasteShortcut(event) {
  const key = String(event.key || "").toLowerCase();
  return (
    (event.ctrlKey || event.metaKey) &&
    !event.altKey &&
    !event.shiftKey &&
    (key === "v" || event.code === "KeyV")
  );
}

function isFormTarget(target) {
  const tagName = target && target.tagName ? target.tagName.toLowerCase() : "";
  return tagName === "input" || tagName === "textarea" || target?.isContentEditable;
}

function shouldHandlePaste(event) {
  if (!isConnected()) {
    return false;
  }

  const target = event.target;
  if (target && target.id === "noVNC_clipboard_text") {
    return false;
  }
  if (isPasteSink(target) || (target && target.id === "noVNC_keyboardinput")) {
    return true;
  }

  if (isFormTarget(target)) {
    return false;
  }

  return true;
}

function releaseRemotePasteModifier() {
  if (!isConnected()) {
    return;
  }

  UI.rfb.sendKey(XK_CONTROL_L, "ControlLeft", false);
}

function sendRemotePasteShortcut() {
  if (!isConnected()) {
    return;
  }

  releaseRemotePasteModifier();
  UI.rfb.sendKey(XK_CONTROL_L, "ControlLeft", true);
  UI.rfb.sendKey(XK_V, "KeyV", true);
  UI.rfb.sendKey(XK_V, "KeyV", false);
  UI.rfb.sendKey(XK_CONTROL_L, "ControlLeft", false);
  UI.rfb.focus();
}

function scheduleRemotePasteShortcut() {
  clearTimeout(remotePasteTimer);
  remotePasteTimer = setTimeout(sendRemotePasteShortcut, REMOTE_PASTE_DELAY_MS);
}

window.addEventListener(
  "keydown",
  (event) => {
    if (!isPasteShortcut(event) || !isConnected()) {
      return;
    }

    const target = event.target;
    if (target && target.id === "noVNC_clipboard_text") {
      return;
    }
    if (isFormTarget(target) && !isPasteSink(target) && target.id !== "noVNC_keyboardinput") {
      return;
    }

    bridgeState.lastShortcutAt = Date.now();
    focusPasteSink();
    releaseRemotePasteModifier();
    event.stopImmediatePropagation();
  },
  true,
);

window.addEventListener(
  "paste",
  (event) => {
    if (!shouldHandlePaste(event)) {
      return;
    }

    const text = event.clipboardData?.getData("text/plain");
    if (!text) {
      return;
    }

    event.preventDefault();
    event.stopImmediatePropagation();
    UI.rfb.clipboardPasteFrom(text);
    bridgeState.lastPasteAt = Date.now();
    bridgeState.lastPasteLength = text.length;
    scheduleRemotePasteShortcut();
    if (isPasteSink(event.target)) {
      event.target.value = "";
    }
    UI.rfb.focus();
  },
  true,
);
