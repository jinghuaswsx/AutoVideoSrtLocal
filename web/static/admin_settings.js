(function () {
  const tbody = document.getElementById("mediaLanguagesTableBody");
  const addBtn = document.getElementById("addMediaLanguageBtn");
  const flash = document.getElementById("mediaLanguagesFlash");

  if (!tbody || !addBtn || !flash) {
    return;
  }

  const state = {
    rows: normalizeRows(window.ADMIN_MEDIA_LANGUAGES_BOOTSTRAP || []),
  };

  function normalizeRows(rows) {
    return rows.map((row) => ({
      code: row.code || "",
      name_zh: row.name_zh || "",
      sort_order: Number(row.sort_order || 0),
      enabled: row.enabled === true || row.enabled === 1,
      items_count: Number(row.items_count || 0),
      copy_count: Number(row.copy_count || 0),
      cover_count: Number(row.cover_count || 0),
      in_use: Boolean(row.in_use),
      __draft: Boolean(row.__draft),
      __error: row.__error || "",
    }));
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function showFlash(message, tone) {
    flash.hidden = false;
    flash.textContent = message;
    if (tone === "error") {
      flash.style.background = "#fef2f2";
      flash.style.borderColor = "#fecaca";
      flash.style.color = "#b91c1c";
    } else {
      flash.style.background = "#f0fdf4";
      flash.style.borderColor = "#bbf7d0";
      flash.style.color = "#166534";
    }
  }

  function clearFlash() {
    flash.hidden = true;
    flash.textContent = "";
  }

  function renderCodeCell(row) {
    if (row.__draft) {
      return `<input type="text" data-field="code" value="${escapeHtml(row.code)}" placeholder="例如 pt" autocomplete="off">`;
    }
    const cls = row.code === "en" ? "media-language-code is-default" : "media-language-code";
    return `<span class="${cls}">${escapeHtml(row.code.toUpperCase())}</span>`;
  }

  function renderUsage(row) {
    return [
      `<div class="media-language-usage"><strong>素材</strong> ${row.items_count}</div>`,
      `<div class="media-language-usage"><strong>文案</strong> ${row.copy_count}</div>`,
      `<div class="media-language-usage"><strong>主图</strong> ${row.cover_count}</div>`,
    ].join("");
  }

  function renderRow(row, index) {
    const deleteDisabled = !row.__draft && (row.code === "en" || row.in_use);
    const deleteTitle = row.code === "en"
      ? "默认语种 EN 不能删除"
      : row.in_use
        ? "该语种已有关联数据，只能停用"
        : "";
    const enabledDisabled = !row.__draft && row.code === "en" ? "disabled" : "";
    const enabledLabel = row.code === "en" && !row.__draft ? "默认启用" : "启用";

    return `
      <tr data-index="${index}">
        <td>
          ${renderCodeCell(row)}
          ${row.code === "en" && !row.__draft ? '<div class="media-language-muted" style="margin-top:8px;">默认语种</div>' : ""}
        </td>
        <td class="media-language-name-cell">
          <input type="text" data-field="name_zh" value="${escapeHtml(row.name_zh)}" placeholder="例如 葡萄牙语" autocomplete="off">
          ${row.__error ? `<div class="media-language-row-error">${escapeHtml(row.__error)}</div>` : ""}
        </td>
        <td class="media-language-sort-cell">
          <input type="number" data-field="sort_order" value="${escapeHtml(row.sort_order)}" step="1">
        </td>
        <td>
          <label class="media-language-enabled">
            <input type="checkbox" data-field="enabled" ${row.enabled ? "checked" : ""} ${enabledDisabled}>
            <span>${enabledLabel}</span>
          </label>
        </td>
        <td>${renderUsage(row)}</td>
        <td>
          <div class="media-language-actions">
            <button type="button" class="btn btn-primary" data-action="save" data-index="${index}">保存</button>
            <button type="button" class="btn btn-ghost" data-action="delete" data-index="${index}" ${deleteDisabled ? "disabled" : ""} title="${escapeHtml(deleteTitle)}">删除</button>
          </div>
        </td>
      </tr>
    `;
  }

  function render() {
    if (!state.rows.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="media-language-muted" style="padding:16px;">暂无语种配置</td></tr>';
      return;
    }
    tbody.innerHTML = state.rows.map(renderRow).join("");
  }

  function getRowElement(index) {
    return tbody.querySelector(`tr[data-index="${index}"]`);
  }

  function readRow(index) {
    const current = state.rows[index];
    const rowEl = getRowElement(index);
    if (!rowEl) {
      return current;
    }
    const codeInput = rowEl.querySelector('[data-field="code"]');
    const nameInput = rowEl.querySelector('[data-field="name_zh"]');
    const sortInput = rowEl.querySelector('[data-field="sort_order"]');
    const enabledInput = rowEl.querySelector('[data-field="enabled"]');
    return {
      ...current,
      code: current.__draft ? (codeInput ? codeInput.value.trim().toLowerCase() : "") : current.code,
      name_zh: nameInput ? nameInput.value.trim() : current.name_zh,
      sort_order: sortInput ? Number(sortInput.value || 0) : current.sort_order,
      enabled: enabledInput ? enabledInput.checked : current.enabled,
      __error: "",
    };
  }

  async function fetchJSON(url, options) {
    const response = await fetch(url, options);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.error || "请求失败");
    }
    return payload;
  }

  async function reloadRows(message) {
    const payload = await fetchJSON("/admin/api/media-languages");
    state.rows = normalizeRows(payload.items || []);
    render();
    if (message) {
      showFlash(message, "success");
    }
  }

  async function saveRow(index) {
    const row = readRow(index);
    state.rows[index] = row;
    clearFlash();

    if (!row.code) {
      row.__error = "语言编码不能为空";
      render();
      return;
    }
    if (!row.name_zh) {
      row.__error = "语言名称不能为空";
      render();
      return;
    }

    const payload = {
      name_zh: row.name_zh,
      sort_order: row.sort_order,
      enabled: row.code === "en" ? true : row.enabled,
    };

    try {
      if (row.__draft) {
        await fetchJSON("/admin/api/media-languages", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ...payload, code: row.code }),
        });
        await reloadRows(`语种 ${row.code.toUpperCase()} 已创建`);
      } else {
        await fetchJSON(`/admin/api/media-languages/${encodeURIComponent(row.code)}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        await reloadRows(`语种 ${row.code.toUpperCase()} 已保存`);
      }
    } catch (error) {
      state.rows[index] = { ...row, __error: error.message || "保存失败" };
      render();
    }
  }

  async function deleteRow(index) {
    const row = state.rows[index];
    clearFlash();

    if (row.__draft) {
      state.rows.splice(index, 1);
      render();
      return;
    }
    if (row.code === "en" || row.in_use) {
      return;
    }
    if (!window.confirm(`确认删除语种 ${row.code.toUpperCase()} 吗？`)) {
      return;
    }

    try {
      await fetchJSON(`/admin/api/media-languages/${encodeURIComponent(row.code)}`, {
        method: "DELETE",
      });
      await reloadRows(`语种 ${row.code.toUpperCase()} 已删除`);
    } catch (error) {
      state.rows[index] = { ...row, __error: error.message || "删除失败" };
      render();
    }
  }

  addBtn.addEventListener("click", function () {
    clearFlash();
    const existingDraftIndex = state.rows.findIndex((row) => row.__draft);
    if (existingDraftIndex >= 0) {
      const rowEl = getRowElement(existingDraftIndex);
      const codeInput = rowEl && rowEl.querySelector('[data-field="code"]');
      if (codeInput) {
        codeInput.focus();
      }
      return;
    }
    state.rows.push({
      code: "",
      name_zh: "",
      sort_order: state.rows.length + 1,
      enabled: true,
      items_count: 0,
      copy_count: 0,
      cover_count: 0,
      in_use: false,
      __draft: true,
      __error: "",
    });
    render();
    const rowEl = getRowElement(state.rows.length - 1);
    const codeInput = rowEl && rowEl.querySelector('[data-field="code"]');
    if (codeInput) {
      codeInput.focus();
    }
  });

  tbody.addEventListener("click", function (event) {
    const button = event.target.closest("button[data-action]");
    if (!button) {
      return;
    }
    const index = Number(button.dataset.index);
    if (!Number.isInteger(index)) {
      return;
    }
    if (button.dataset.action === "save") {
      saveRow(index);
      return;
    }
    if (button.dataset.action === "delete") {
      deleteRow(index);
    }
  });

  render();
})();
