(function() {
  'use strict';

  var instances = [];
  var activeInstance = null;
  var styleInstalled = false;

  function installStyles() {
    if (styleInstalled || document.getElementById('analytics-date-range-picker-style')) return;
    styleInstalled = true;
    var style = document.createElement('style');
    style.id = 'analytics-date-range-picker-style';
    style.textContent = [
      '.analytics-range-picker{position:relative;display:inline-block;min-width:220px;max-width:100%;}',
      '.analytics-range-picker.oad-date-range-field,.analytics-range-picker.op-date-range-field{padding:0;border:0;background:transparent;box-shadow:none;}',
      '.analytics-range-trigger{display:flex;align-items:center;justify-content:space-between;gap:8px;width:100%;height:32px;padding:0 10px;border:1px solid var(--border-strong,var(--oc-border-strong,#cbd5e1));border-radius:var(--radius,var(--oc-r,8px));background:var(--bg-card,var(--oc-bg,#fff));color:var(--fg,var(--oc-fg,#0f172a));font:inherit;font-size:13px;cursor:pointer;text-align:left;white-space:nowrap;}',
      '.analytics-range-trigger:hover{border-color:var(--accent,var(--oc-accent,#2563eb));}',
      '.analytics-range-trigger:focus-visible{outline:none;border-color:var(--accent,var(--oc-accent,#2563eb));box-shadow:0 0 0 2px var(--accent-ring,var(--oc-accent-ring,rgba(37,99,235,.2)));}',
      '.analytics-range-trigger-text{overflow:hidden;text-overflow:ellipsis;}',
      '.analytics-range-trigger-caret{color:var(--fg-subtle,var(--oc-fg-subtle,#64748b));font-size:10px;}',
      '.analytics-range-panel{position:absolute;top:calc(100% + 6px);right:0;z-index:2000;width:min(580px,calc(100vw - 32px));padding:16px;border:1px solid var(--border,var(--oc-border,#e2e8f0));border-radius:var(--radius-lg,var(--oc-r-lg,12px));background:var(--bg-card,var(--oc-bg,#fff));box-shadow:var(--shadow-lg,var(--oc-shadow-lg,0 16px 40px rgba(15,23,42,.14)));display:flex;flex-direction:column;gap:14px;}',
      '.analytics-range-panel[hidden]{display:none!important;}',
      '.analytics-range-panel-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;}',
      '.analytics-range-panel-title{font-weight:600;font-size:14px;color:var(--fg,var(--oc-fg,#0f172a));}',
      '.analytics-range-panel-note{margin-top:2px;font-size:12px;color:var(--fg-muted,var(--oc-fg-muted,#64748b));}',
      '.analytics-range-nav{display:flex;gap:8px;}',
      '.analytics-range-nav button,.analytics-range-actions button{height:28px;padding:0 10px;border:1px solid var(--border-strong,var(--oc-border-strong,#cbd5e1));border-radius:var(--radius,var(--oc-r,8px));background:var(--bg-card,var(--oc-bg,#fff));color:var(--fg-muted,var(--oc-fg-muted,#64748b));font:inherit;font-size:12px;cursor:pointer;}',
      '.analytics-range-nav button:hover,.analytics-range-actions button:hover{background:var(--bg-muted,var(--oc-bg-muted,#f1f5f9));color:var(--fg,var(--oc-fg,#0f172a));}',
      '.analytics-range-calendars{display:grid;grid-template-columns:1fr 1fr;gap:16px;}',
      '.analytics-calendar-title{text-align:center;margin-bottom:8px;font-size:13px;font-weight:600;color:var(--fg,var(--oc-fg,#0f172a));}',
      '.analytics-calendar-weekdays,.analytics-calendar-grid{display:grid;grid-template-columns:repeat(7,1fr);}',
      '.analytics-calendar-weekdays{margin-bottom:4px;text-align:center;font-size:11px;font-weight:500;color:var(--fg-subtle,var(--oc-fg-subtle,#94a3b8));}',
      '.analytics-calendar-grid{gap:2px;}',
      '.analytics-calendar-spacer,.analytics-calendar-day{aspect-ratio:1;}',
      '.analytics-calendar-day{display:flex;align-items:center;justify-content:center;border:0;border-radius:var(--radius,var(--oc-r,8px));background:transparent;color:var(--fg,var(--oc-fg,#0f172a));font:inherit;font-size:12px;cursor:pointer;}',
      '.analytics-calendar-day:hover{background:var(--bg-muted,var(--oc-bg-muted,#f1f5f9));}',
      '.analytics-calendar-day.is-today{font-weight:700;box-shadow:inset 0 0 0 1px var(--accent,var(--oc-accent,#2563eb));}',
      '.analytics-calendar-day.is-selected{background:var(--accent,var(--oc-accent,#2563eb))!important;color:var(--accent-fg,#fff)!important;}',
      '.analytics-calendar-day.is-in-range{background:var(--accent-subtle,var(--oc-accent-subtle,#dbeafe));color:var(--accent,var(--oc-accent,#2563eb));}',
      '.analytics-range-actions{display:flex;justify-content:flex-end;gap:8px;padding-top:12px;border-top:1px solid var(--border,var(--oc-border,#e2e8f0));}',
      '.analytics-range-apply{background:var(--accent,var(--oc-accent,#2563eb))!important;border-color:var(--accent,var(--oc-accent,#2563eb))!important;color:var(--accent-fg,#fff)!important;}',
      '.analytics-range-apply:disabled{opacity:.45;cursor:not-allowed;}',
      '@media(max-width:640px){.analytics-range-panel{right:50%;transform:translateX(50%);}.analytics-range-calendars{grid-template-columns:1fr;}}'
    ].join('');
    document.head.appendChild(style);
  }

  function parseIsoDate(value) {
    var match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value || '');
    if (!match) return null;
    return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  }

  function cloneDate(date) {
    return new Date(date.getFullYear(), date.getMonth(), date.getDate());
  }

  function todayDate() {
    if (window.orderAnalyticsMetaCalendar && typeof window.orderAnalyticsMetaCalendar.today === 'function') {
      return cloneDate(window.orderAnalyticsMetaCalendar.today());
    }
    return cloneDate(new Date());
  }

  function monthStart(date) {
    return new Date(date.getFullYear(), date.getMonth(), 1);
  }

  function addMonths(date, amount) {
    return new Date(date.getFullYear(), date.getMonth() + amount, 1);
  }

  function formatIsoDate(date) {
    return date.getFullYear() + '-' +
      String(date.getMonth() + 1).padStart(2, '0') + '-' +
      String(date.getDate()).padStart(2, '0');
  }

  function formatLabelDate(date) {
    return date.getFullYear() + '/' +
      String(date.getMonth() + 1).padStart(2, '0') + '/' +
      String(date.getDate()).padStart(2, '0');
  }

  function formatMonthTitle(date) {
    return date.getFullYear() + '年' + String(date.getMonth() + 1).padStart(2, '0') + '月';
  }

  function sameDate(left, right) {
    return !!left && !!right &&
      left.getFullYear() === right.getFullYear() &&
      left.getMonth() === right.getMonth() &&
      left.getDate() === right.getDate();
  }

  function timeValue(date) {
    return date ? date.getTime() : 0;
  }

  function findInput(root, side) {
    var direct = root.querySelector(side === 'start' ? '[data-range-start]' : '[data-range-end]');
    if (direct) return direct;
    var id = side === 'start' ? root.dataset.rangeStartId : root.dataset.rangeEndId;
    return id ? document.getElementById(id) : null;
  }

  function dispatchChange(input) {
    if (!input) return;
    input.dispatchEvent(new Event('change', { bubbles: true }));
  }

  function createCalendar(index) {
    var section = document.createElement('section');
    section.className = 'analytics-calendar';
    section.setAttribute('data-calendar-index', String(index));
    section.innerHTML =
      '<div class="analytics-calendar-title" data-calendar-title></div>' +
      '<div class="analytics-calendar-weekdays">' +
        '<span>一</span><span>二</span><span>三</span><span>四</span><span>五</span><span>六</span><span>日</span>' +
      '</div>' +
      '<div class="analytics-calendar-grid" data-calendar-grid></div>';
    return section;
  }

  function createPanel(label) {
    var panel = document.createElement('div');
    panel.className = 'analytics-range-panel';
    panel.hidden = true;
    panel.innerHTML =
      '<div class="analytics-range-panel-head">' +
        '<div>' +
          '<div class="analytics-range-panel-title">选择' + label + '</div>' +
          '<div class="analytics-range-panel-note" data-range-summary>点击第一个日期作为开始，第二个日期作为结束。</div>' +
        '</div>' +
        '<div class="analytics-range-nav">' +
          '<button type="button" data-range-nav="prev">上个月</button>' +
          '<button type="button" data-range-nav="next">下个月</button>' +
        '</div>' +
      '</div>' +
      '<div class="analytics-range-calendars" data-range-calendars></div>' +
      '<div class="analytics-range-actions">' +
        '<button type="button" data-range-cancel>取消</button>' +
        '<button type="button" class="analytics-range-apply" data-range-apply disabled>确认</button>' +
      '</div>';
    var calendars = panel.querySelector('[data-range-calendars]');
    calendars.appendChild(createCalendar(0));
    calendars.appendChild(createCalendar(1));
    return panel;
  }

  function init(options) {
    var root = options && options.root ? options.root : null;
    if (!root || root.__analyticsDateRangePicker) return root && root.__analyticsDateRangePicker;

    installStyles();

    var startInput = options.startInput || findInput(root, 'start');
    var endInput = options.endInput || findInput(root, 'end');
    var trigger = root.querySelector('[data-range-trigger]');
    var labelText = root.querySelector('[data-range-label-text]');
    var label = options.label || root.dataset.rangeLabel || '日期范围';
    if (!startInput || !endInput || !trigger || !labelText) return null;

    var panel = root.querySelector('[data-range-panel]') || createPanel(label);
    if (!panel.parentElement) root.appendChild(panel);
    var summary = panel.querySelector('[data-range-summary]');
    var applyButton = panel.querySelector('[data-range-apply]');
    var monthAnchor = monthStart(parseIsoDate(startInput.value) || todayDate());
    var draftStart = null;
    var draftEnd = null;
    var waitingForEnd = false;

    function currentStart() { return parseIsoDate(startInput.value); }
    function currentEnd() { return parseIsoDate(endInput.value); }
    function activeStart() { return waitingForEnd && draftStart ? draftStart : draftStart || currentStart(); }
    function activeEnd() { return waitingForEnd ? null : draftEnd || currentEnd(); }

    function sync() {
      var start = currentStart();
      var end = currentEnd();
      if (start && end) {
        labelText.textContent = label + '：' + formatLabelDate(start) + ' - ' + formatLabelDate(end);
      } else {
        labelText.textContent = label + '：请选择日期范围';
      }
      renderCalendars();
    }

    function updateSummary() {
      if (waitingForEnd && draftStart) {
        summary.textContent = '已选开始：' + formatLabelDate(draftStart) + '，请再选一个日期作为结束。';
      } else if (draftStart && draftEnd) {
        summary.textContent = '已选范围：' + formatLabelDate(draftStart) + ' 至 ' + formatLabelDate(draftEnd) + '，确认后生效。';
      } else {
        summary.textContent = '点击第一个日期作为开始，第二个日期作为结束。';
      }
      applyButton.disabled = !(draftStart && draftEnd);
    }

    function renderCalendars() {
      if (panel.hidden) return;
      var displayMonth = monthAnchor || monthStart(currentStart() || todayDate());
      var selectedStart = activeStart();
      var selectedEnd = activeEnd();
      var today = todayDate();
      panel.querySelectorAll('[data-calendar-index]').forEach(function(calendar, calendarIndex) {
        var monthDate = addMonths(displayMonth, Number(calendarIndex) || 0);
        var title = calendar.querySelector('[data-calendar-title]');
        var grid = calendar.querySelector('[data-calendar-grid]');
        if (title) title.textContent = formatMonthTitle(monthDate);
        if (!grid) return;
        grid.innerHTML = '';
        var firstWeekday = monthDate.getDay();
        var offset = firstWeekday === 0 ? 6 : firstWeekday - 1;
        var daysInMonth = new Date(monthDate.getFullYear(), monthDate.getMonth() + 1, 0).getDate();
        var rendered = 0;
        while (rendered < offset) {
          var spacer = document.createElement('span');
          spacer.className = 'analytics-calendar-spacer';
          grid.appendChild(spacer);
          rendered += 1;
        }
        for (var day = 1; day <= daysInMonth; day += 1) {
          var current = new Date(monthDate.getFullYear(), monthDate.getMonth(), day);
          var button = document.createElement('button');
          button.type = 'button';
          button.className = 'analytics-calendar-day';
          button.textContent = String(day);
          button.setAttribute('data-range-day', formatIsoDate(current));
          if (sameDate(current, today)) button.classList.add('is-today');
          if (selectedStart && selectedEnd && timeValue(current) > timeValue(selectedStart) && timeValue(current) < timeValue(selectedEnd)) {
            button.classList.add('is-in-range');
          }
          if ((selectedStart && sameDate(current, selectedStart)) || (selectedEnd && sameDate(current, selectedEnd))) {
            button.classList.add('is-selected');
          }
          grid.appendChild(button);
          rendered += 1;
        }
        while (rendered % 7 !== 0) {
          var tail = document.createElement('span');
          tail.className = 'analytics-calendar-spacer';
          grid.appendChild(tail);
          rendered += 1;
        }
      });
    }

    function openPanel() {
      if (activeInstance && activeInstance !== instance) activeInstance.close();
      activeInstance = instance;
      draftStart = currentStart();
      draftEnd = currentEnd();
      waitingForEnd = false;
      monthAnchor = monthStart(draftStart || todayDate());
      panel.hidden = false;
      trigger.setAttribute('aria-expanded', 'true');
      updateSummary();
      renderCalendars();
    }

    function closePanel() {
      panel.hidden = true;
      trigger.setAttribute('aria-expanded', 'false');
      draftStart = null;
      draftEnd = null;
      waitingForEnd = false;
      if (activeInstance === instance) activeInstance = null;
      sync();
    }

    function selectDay(value) {
      var clicked = parseIsoDate(value);
      if (!clicked) return;
      if (!waitingForEnd) {
        draftStart = clicked;
        draftEnd = null;
        waitingForEnd = true;
        monthAnchor = monthStart(clicked);
      } else {
        draftEnd = clicked;
        if (timeValue(draftEnd) < timeValue(draftStart)) {
          var swapped = draftStart;
          draftStart = draftEnd;
          draftEnd = swapped;
        }
        waitingForEnd = false;
      }
      updateSummary();
      renderCalendars();
    }

    function applyRange() {
      if (!draftStart || !draftEnd) return;
      startInput.value = formatIsoDate(draftStart);
      endInput.value = formatIsoDate(draftEnd);
      closePanel();
      dispatchChange(endInput);
      root.dispatchEvent(new CustomEvent('analytics-date-range:apply', {
        bubbles: true,
        detail: { start: startInput.value, end: endInput.value }
      }));
      syncAll();
    }

    trigger.addEventListener('click', function() {
      if (panel.hidden) openPanel();
      else closePanel();
    });
    panel.querySelector('[data-range-nav="prev"]').addEventListener('click', function() {
      monthAnchor = addMonths(monthAnchor || monthStart(todayDate()), -1);
      renderCalendars();
    });
    panel.querySelector('[data-range-nav="next"]').addEventListener('click', function() {
      monthAnchor = addMonths(monthAnchor || monthStart(todayDate()), 1);
      renderCalendars();
    });
    panel.querySelector('[data-range-cancel]').addEventListener('click', closePanel);
    applyButton.addEventListener('click', applyRange);
    panel.addEventListener('click', function(event) {
      var target = event.target;
      if (target && target.hasAttribute('data-range-day')) {
        selectDay(target.getAttribute('data-range-day'));
      }
    });

    var instance = {
      root: root,
      close: closePanel,
      sync: sync
    };
    root.__analyticsDateRangePicker = instance;
    instances.push(instance);
    sync();
    return instance;
  }

  function initAll() {
    document.querySelectorAll('[data-analytics-date-range]').forEach(function(root) {
      init({ root: root });
    });
    syncAll();
  }

  function syncAll() {
    instances.forEach(function(instance) {
      if (instance && typeof instance.sync === 'function') instance.sync();
    });
  }

  document.addEventListener('mousedown', function(event) {
    if (!activeInstance || activeInstance.root.contains(event.target)) return;
    activeInstance.close();
  });

  document.addEventListener('keydown', function(event) {
    if (event.key === 'Escape' && activeInstance) activeInstance.close();
  });

  window.AnalyticsDateRangePicker = {
    init: init,
    initAll: initAll,
    syncAll: syncAll
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAll);
  } else {
    initAll();
  }
})();
