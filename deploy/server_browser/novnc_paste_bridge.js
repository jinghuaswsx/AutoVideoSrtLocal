import UI from "./ui.js";

const REMOTE_PASTE_DELAY_MS = 160;
const XK_CONTROL_L = 0xffe3;
const XK_V = 0x0076;

function shouldHandlePaste(event) {
  if (!UI.rfb || !UI.connected) {
    return false;
  }

  const target = event.target;
  if (target && target.id === "noVNC_clipboard_text") {
    return false;
  }
  if (target && target.id === "noVNC_keyboardinput") {
    return true;
  }

  const tagName = target && target.tagName ? target.tagName.toLowerCase() : "";
  if (tagName === "input" || tagName === "textarea" || target?.isContentEditable) {
    return false;
  }

  return true;
}

function sendRemotePasteShortcut() {
  if (!UI.rfb || !UI.connected) {
    return;
  }

  UI.rfb.sendKey(XK_CONTROL_L, "ControlLeft", true);
  UI.rfb.sendKey(XK_V, "KeyV", true);
  UI.rfb.sendKey(XK_V, "KeyV", false);
  UI.rfb.sendKey(XK_CONTROL_L, "ControlLeft", false);
  UI.rfb.focus();
}

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
    UI.rfb.clipboardPasteFrom(text);
    setTimeout(sendRemotePasteShortcut, REMOTE_PASTE_DELAY_MS);
  },
  true,
);
