(function () {
  var root = document.getElementById("pricingManager");
  var configEl = document.getElementById("pricingConfig");
  if (!root || !configEl) return;

  var config = JSON.parse(configEl.textContent || "{}");
  var unitsTypes = Array.isArray(config.units_types) ? config.units_types : ["tokens", "chars", "seconds", "images"];
  var tbody = document.getElementById("pricingTableBody");
  var table = document.getElementById("pricingTable");
  var loadingEl = document.getElementById("pricingLoading");
  var emptyEl = document.getElementById("pricingEmpty");
  var errorEl = document.getElementById("pricingError");
  var flashEl = document.getElementById("pricingFlash");
  var addBtn = document.getElementById("pricingAddBtn");
  var reloadBtn = document.getElementById("pricingReloadBtn");
  var state = {
    rows: [],
    loading: true,
    busy: false,
    adding: false,
    editingId: null,
    createDraft: defaultDraft(),
    editDraft: null,
    error: "",
    flash: "",
  };

  function defaultDraft() {
    return {
      provider: "",
      model: "",
      units_type: unitsTypes[0] || "tokens",
      unit_input_cny: "",
      unit_output_cny: "",
      unit_flat_cny: "",
      note: "",
    };
  }

  function csrfToken() {
    var el = document.querySelector("meta[name='csrf-token']");
    return el ? el.content : "";
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function formatPrice(value) {
    if (value == null || value === "") return "—";
    var text = String(value);
    if (!text.includes(".")) return text;
    return text.replace(/0+$/, "").replace(/\.$/, "");
  }

  function draftFromRow(row) {
    return {
      provider: row.provider || "",
      model: row.model || "",
      units_type: row.units_type || (unitsTypes[0] || "tokens"),
      unit_input_cny: row.unit_input_cny == null ? "" : String(row.unit_input_cny),
      unit_output_cny: row.unit_output_cny == null ? "" : String(row.unit_output_cny),
      unit_flat_cny: row.unit_flat_cny == null ? "" : String(row.unit_flat_cny),
      note: row.note || "",
    };
  }

  function buildUnitsOptions(current) {
    return unitsTypes
      .map(function (value) {
        return '<option value="' + escapeHtml(value) + '"' + (value === current ? " selected" : "") + ">" + escapeHtml(value) + "</option>";
      })
      .join("");
  }

  function renderEditor(draft, isCreate, rowId) {
    var saveAction = isCreate ? "save-create" : "save-edit";
    var cancelAction = isCreate ? "cancel-create" : "cancel-edit";
    var rowAttr = rowId == null ? "" : ' data-row-id="' + escapeHtml(rowId) + '"';
    return (
      '<tr class="pricing-form-row"' + rowAttr + ">" +
        '<td colspan="8">' +
          '<div class="pricing-form-grid">' +
            '<div><label>供应商</label><input type="text" data-field="provider" value="' + escapeHtml(draft.provider) + '"' + (isCreate ? "" : " readonly") + ' placeholder="openrouter / gemini_vertex"></div>' +
            '<div><label>模型</label><input type="text" data-field="model" value="' + escapeHtml(draft.model) + '"' + (isCreate ? "" : " readonly") + ' placeholder="支持 * 通配"></div>' +
            '<div><label>计量单位</label>' +
              (isCreate
                ? '<select data-field="units_type">' + buildUnitsOptions(draft.units_type) + "</select>"
                : '<input type="text" data-field="units_type" value="' + escapeHtml(draft.units_type) + '" readonly>') +
            "</div>" +
            '<div><label>输入 ¥/单位</label><input type="number" data-field="unit_input_cny" min="0" step="0.00000001" value="' + escapeHtml(draft.unit_input_cny) + '" placeholder="tokens 用"></div>' +
            '<div><label>输出 ¥/单位</label><input type="number" data-field="unit_output_cny" min="0" step="0.00000001" value="' + escapeHtml(draft.unit_output_cny) + '" placeholder="tokens 用"></div>' +
            '<div><label>统一 ¥/单位</label><input type="number" data-field="unit_flat_cny" min="0" step="0.00000001" value="' + escapeHtml(draft.unit_flat_cny) + '" placeholder="chars / seconds / images"></div>' +
            '<div><label>备注</label><textarea data-field="note" placeholder="例如：待复核：0.039 USD/image ×6.8">' + escapeHtml(draft.note) + "</textarea></div>" +
          "</div>" +
          '<div class="pricing-actions" style="margin-top:14px">' +
            '<button type="button" class="pricing-btn" data-action="' + saveAction + '"' + (state.busy ? " disabled" : "") + ">保存</button>" +
            '<button type="button" class="pricing-btn-ghost" data-action="' + cancelAction + '"' + (state.busy ? " disabled" : "") + ">取消</button>" +
          "</div>" +
        "</td>" +
      "</tr>"
    );
  }

  function renderRow(row) {
    return (
      '<tr data-row-id="' + escapeHtml(row.id) + '">' +
        '<td><div class="pricing-code">' + escapeHtml(row.provider || "—") + "</div></td>" +
        '<td><div class="pricing-code">' + escapeHtml(row.model || "—") + "</div></td>" +
        '<td><span class="pricing-unit-badge">' + escapeHtml(row.units_type || "—") + "</span></td>" +
        "<td>" + escapeHtml(formatPrice(row.unit_input_cny)) + "</td>" +
        "<td>" + escapeHtml(formatPrice(row.unit_output_cny)) + "</td>" +
        "<td>" + escapeHtml(formatPrice(row.unit_flat_cny)) + "</td>" +
        '<td><div class="pricing-note-text">' + escapeHtml(row.note || "—") + "</div>" +
          (row.updated_at ? '<div class="pricing-updated">更新于 ' + escapeHtml(row.updated_at) + "</div>" : "") +
        "</td>" +
        '<td><div class="pricing-actions">' +
          '<button type="button" class="pricing-btn-ghost" data-action="edit" data-id="' + escapeHtml(row.id) + '"' + (state.busy ? " disabled" : "") + ">编辑</button>" +
          '<button type="button" class="pricing-btn-danger" data-action="delete" data-id="' + escapeHtml(row.id) + '"' + (state.busy ? " disabled" : "") + ">删除</button>" +
        "</div></td>" +
      "</tr>"
    );
  }

  function render() {
    loadingEl.hidden = !state.loading;
    errorEl.hidden = !state.error;
    errorEl.textContent = state.error;
    flashEl.hidden = !state.flash;
    flashEl.textContent = state.flash;
    addBtn.disabled = state.busy;
    reloadBtn.disabled = state.busy;

    var rowsHtml = [];
    if (state.adding) {
      rowsHtml.push(renderEditor(state.createDraft, true, null));
    }
    state.rows.forEach(function (row) {
      if (state.editingId === row.id) {
        rowsHtml.push(renderEditor(state.editDraft || draftFromRow(row), false, row.id));
      } else {
        rowsHtml.push(renderRow(row));
      }
    });

    tbody.innerHTML = rowsHtml.join("");
    table.hidden = rowsHtml.length === 0;
    emptyEl.hidden = state.loading || rowsHtml.length !== 0;
  }

  function parseOptionalNumber(rawValue, label) {
    var text = String(rawValue || "").trim();
    if (!text) return null;
    var value = Number(text);
    if (!Number.isFinite(value) || value < 0) {
      throw new Error(label + "必须是非负数字");
    }
    return value;
  }

  function collectPayload(formRow) {
    var provider = (formRow.querySelector("[data-field='provider']").value || "").trim();
    var model = (formRow.querySelector("[data-field='model']").value || "").trim();
    var unitsType = (formRow.querySelector("[data-field='units_type']").value || "").trim();
    var note = (formRow.querySelector("[data-field='note']").value || "").trim();
    var unitInput = parseOptionalNumber(formRow.querySelector("[data-field='unit_input_cny']").value, "输入单价");
    var unitOutput = parseOptionalNumber(formRow.querySelector("[data-field='unit_output_cny']").value, "输出单价");
    var unitFlat = parseOptionalNumber(formRow.querySelector("[data-field='unit_flat_cny']").value, "统一单价");

    if (!provider) throw new Error("provider不能为空");
    if (!model) throw new Error("model不能为空");
    if (unitsTypes.indexOf(unitsType) === -1) {
      throw new Error("units_type 不合法");
    }
    if (unitInput == null && unitOutput == null && unitFlat == null) {
      throw new Error("至少填写一个单价字段");
    }

    return {
      provider: provider,
      model: model,
      units_type: unitsType,
      unit_input_cny: unitInput,
      unit_output_cny: unitOutput,
      unit_flat_cny: unitFlat,
      note: note,
    };
  }

  function requestJson(url, options) {
    var headers = Object.assign({}, (options && options.headers) || {});
    if (!headers["Content-Type"] && !(options && options.method === "DELETE")) {
      headers["Content-Type"] = "application/json";
    }
    if (csrfToken() && !headers["X-CSRFToken"] && !headers["X-CSRF-Token"]) {
      headers["X-CSRFToken"] = csrfToken();
    }
    return fetch(url, Object.assign({}, options, { headers: headers }))
      .then(function (response) {
        return response.text().then(function (text) {
          var data = {};
          if (text) {
            try {
              data = JSON.parse(text);
            } catch (err) {
              data = {};
            }
          }
          if (!response.ok) {
            throw new Error(data.error || ("请求失败（" + response.status + "）"));
          }
          return data;
        });
      });
  }

  function updateUrl(id) {
    return String(config.update_url_template || "").replace(/0$/, String(id));
  }

  function deleteUrl(id) {
    return String(config.delete_url_template || "").replace(/0$/, String(id));
  }

  function clearMessages() {
    state.error = "";
    state.flash = "";
  }

  async function loadRows(options) {
    state.loading = true;
    if (!options || !options.keepMessages) {
      clearMessages();
    }
    render();
    try {
      var payload = await requestJson(config.list_url, { method: "GET" });
      state.rows = Array.isArray(payload.items) ? payload.items : [];
      state.loading = false;
      if (options && options.message) {
        state.flash = options.message;
      }
      render();
      return true;
    } catch (err) {
      state.loading = false;
      state.error = err.message || "加载失败";
      render();
      return false;
    }
  }

  function startCreate() {
    clearMessages();
    state.adding = true;
    state.editingId = null;
    state.editDraft = null;
    state.createDraft = defaultDraft();
    render();
  }

  function startEdit(id) {
    clearMessages();
    var row = state.rows.find(function (item) { return item.id === id; });
    if (!row) return;
    state.adding = false;
    state.editingId = id;
    state.editDraft = draftFromRow(row);
    render();
  }

  async function handleMutation(requestFactory, successMessage) {
    state.busy = true;
    state.error = "";
    render();
    try {
      await requestFactory();
      state.busy = false;
      state.adding = false;
      state.editingId = null;
      state.createDraft = defaultDraft();
      state.editDraft = null;
      await loadRows({ keepMessages: true, message: successMessage });
    } catch (err) {
      state.busy = false;
      state.error = err.message || "保存失败";
      render();
    }
  }

  addBtn.addEventListener("click", function () {
    if (state.adding) {
      state.adding = false;
      render();
      return;
    }
    startCreate();
  });

  reloadBtn.addEventListener("click", function () {
    loadRows();
  });

  tbody.addEventListener("click", function (event) {
    var button = event.target.closest("button[data-action]");
    if (!button) return;

    var action = button.getAttribute("data-action");
    var rowEl = button.closest("[data-row-id]");
    var id = Number(button.getAttribute("data-id") || (rowEl ? rowEl.getAttribute("data-row-id") : ""));
    var formRow = button.closest(".pricing-form-row");

    if (action === "edit") {
      startEdit(id);
      return;
    }
    if (action === "delete") {
      if (!window.confirm("确认删除这条定价记录吗？")) return;
      handleMutation(function () {
        return requestJson(deleteUrl(id), { method: "DELETE" });
      }, "定价记录已删除");
      return;
    }
    if (action === "cancel-create") {
      clearMessages();
      state.adding = false;
      state.createDraft = defaultDraft();
      render();
      return;
    }
    if (action === "cancel-edit") {
      clearMessages();
      state.editingId = null;
      state.editDraft = null;
      render();
      return;
    }
    if (action === "save-create") {
      try {
        var createPayload = collectPayload(formRow);
        handleMutation(function () {
          return requestJson(config.create_url, {
            method: "POST",
            body: JSON.stringify(createPayload),
          });
        }, "定价记录已新增");
      } catch (err) {
        state.error = err.message || "保存失败";
        render();
      }
      return;
    }
    if (action === "save-edit") {
      try {
        var updatePayload = collectPayload(formRow);
        handleMutation(function () {
          return requestJson(updateUrl(id), {
            method: "PUT",
            body: JSON.stringify(updatePayload),
          });
        }, "定价记录已更新");
      } catch (err) {
        state.error = err.message || "保存失败";
        render();
      }
    }
  });

  loadRows();
})();
