const backendInput = document.getElementById("backendBase");
const statusBadge = document.getElementById("statusBadge");
const messageEl = document.getElementById("message");
const saveBtn = document.getElementById("saveBtn");
const testBtn = document.getElementById("testBtn");
const openBtn = document.getElementById("openBtn");

function setStatus(label, type = "") {
  statusBadge.textContent = label;
  statusBadge.className = `badge ${type}`.trim();
}

function setMessage(text) {
  messageEl.textContent = text || "";
}

function sendMessage(message) {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage(message, (response) => {
      const lastError = chrome.runtime.lastError;
      if (lastError) {
        resolve({ ok: false, error: lastError.message });
        return;
      }
      resolve(response || { ok: false, error: "empty response" });
    });
  });
}

async function loadConfig() {
  const response = await sendMessage({ type: "dpi:getConfig" });
  if (response.ok) {
    backendInput.value = response.result.backendBase || "";
  } else {
    setMessage(response.error || "读取配置失败");
  }
}

async function saveConfig() {
  const response = await sendMessage({
    type: "dpi:setConfig",
    config: { backendBase: backendInput.value },
  });
  if (response.ok) {
    backendInput.value = response.result.backendBase || backendInput.value;
    setMessage("已保存");
  } else {
    setMessage(response.error || "保存失败");
  }
}

async function testBackend() {
  await saveConfig();
  setStatus("测试中");
  setMessage("");
  const response = await sendMessage({ type: "dpi:health" });
  if (response.ok && response.result && response.result.ok) {
    setStatus("可用", "ok");
    setMessage("后台接口可访问");
  } else {
    setStatus("失败", "error");
    setMessage((response && response.error) || "后台接口不可访问");
  }
}

async function openBackend() {
  await saveConfig();
  await sendMessage({ type: "dpi:openBackend" });
}

saveBtn.addEventListener("click", saveConfig);
testBtn.addEventListener("click", testBackend);
openBtn.addEventListener("click", openBackend);

loadConfig();
