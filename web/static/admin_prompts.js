(function () {
  const modal = document.getElementById("editor-modal");
  const title = document.getElementById("editor-title");
  const selProvider = document.getElementById("sel-provider");
  const txtModel = document.getElementById("txt-model");
  const txtContent = document.getElementById("prompt-textarea");
  let currentSlot = null, currentLang = null;

  const csrf = () => {
    const el = document.querySelector("meta[name=csrf-token]");
    return el ? el.content : "";
  };

  async function openEditor(slot, lang) {
    currentSlot = slot;
    currentLang = lang || null;
    title.textContent = `编辑 ${slot} · ${lang ? lang.toUpperCase() : "共享"}`;
    const qs = new URLSearchParams({ slot });
    if (lang) qs.set("lang", lang);
    try {
      const resp = await fetch(`/admin/api/prompts/resolve?${qs}`);
      if (!resp.ok) {
        alert("加载失败：" + (await resp.text()));
        return;
      }
      const cfg = await resp.json();
      selProvider.value = cfg.provider || "openrouter";
      txtModel.value = cfg.model || "";
      txtContent.value = cfg.content || "";
      modal.classList.add("open");
    } catch (err) {
      console.error("[admin_prompts] openEditor failed:", err);
      alert("网络错误");
    }
  }

  document.querySelectorAll(".btn-edit").forEach(btn => {
    btn.addEventListener("click", () => openEditor(btn.dataset.slot, btn.dataset.lang));
  });

  document.getElementById("btn-save").addEventListener("click", async () => {
    try {
      const resp = await fetch("/admin/api/prompts", {
        method: "PUT",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf() },
        body: JSON.stringify({
          slot: currentSlot,
          lang: currentLang,
          provider: selProvider.value,
          model: txtModel.value,
          content: txtContent.value,
        }),
      });
      if (resp.ok) {
        alert("已保存");
        modal.classList.remove("open");
      } else {
        alert("保存失败：" + (await resp.text()));
      }
    } catch (err) {
      console.error("[admin_prompts] save failed:", err);
      alert("网络错误");
    }
  });

  document.getElementById("btn-restore").addEventListener("click", async () => {
    if (!confirm("确认恢复此项到出厂默认？当前自定义内容将被删除。")) return;
    const qs = new URLSearchParams({ slot: currentSlot });
    if (currentLang) qs.set("lang", currentLang);
    try {
      const resp = await fetch(`/admin/api/prompts?${qs}`, {
        method: "DELETE",
        headers: { "X-CSRF-Token": csrf() },
      });
      if (resp.ok) {
        alert("已恢复默认，下次使用时会重新 seed。");
        modal.classList.remove("open");
      } else {
        alert("恢复失败：" + (await resp.text()));
      }
    } catch (err) {
      console.error("[admin_prompts] restore failed:", err);
    }
  });

  document.getElementById("btn-cancel").addEventListener("click", () => {
    modal.classList.remove("open");
  });
})();
