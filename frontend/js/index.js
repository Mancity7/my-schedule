/* 首页装配：三段布局 + 条件构建器 + 股票搜索 + 预检 + 右侧确认面板。 */
(function () {
  var templates = [];
  var strategies = [];
  var currentStrategy = null;

  document.addEventListener("DOMContentLoaded", function () {
    termtip.loadDynamic();
    termtip.attach(document);

    Promise.all([
      conditionBuilder.load(),
      api.get("/api/templates").then(function (d) { templates = d.items || []; }),
      api.get("/api/strategies").then(function (d) { strategies = d.items || []; }),
    ]).then(function () {
      _initTabs();
      _initTemplates();
      _initCondition();
      _initStockPicker();
      _initMode();
      _initPeriod();
      _initSubmit();
      _initRecentRuns();
      _renderConfirmPanel();
    });
  });

  /* ── 策略入口标签 ── */
  function _initTabs() {
    var tabs = document.querySelectorAll(".strategy-tabs .tab");
    tabs.forEach(function (tab) {
      tab.addEventListener("click", function () {
        var mode = tab.dataset.mode;
        if (mode === "nl") {
          toast.warn("「用大白话写」功能将在 V2 版本提供");
          return;
        }
        tabs.forEach(function (t) { t.classList.remove("active"); });
        tab.classList.add("active");
        if (mode === "template") {
          _showTemplatePanel();
        } else {
          _showManualPanel();
        }
      });
    });
  }

  function _showManualPanel() {
    var tp = document.getElementById("template-panel");
    var mp = document.getElementById("manual-panel");
    if (tp) tp.style.display = "none";
    if (mp) mp.style.display = "";
  }

  function _showTemplatePanel() {
    var tp = document.getElementById("template-panel");
    var mp = document.getElementById("manual-panel");
    if (mp) mp.style.display = "none";
    if (tp) {
      tp.style.display = "";
      _renderTemplates();
    }
  }

  function _initTemplates() {
    var container = document.getElementById("template-list");
    if (!container) return;
    _renderTemplates();
  }

  function _renderTemplates() {
    var container = document.getElementById("template-list");
    if (!container) return;
    container.innerHTML = "";
    if (!templates.length) {
      container.innerHTML = '<p class="muted">暂无模板</p>';
      return;
    }
    templates.forEach(function (tpl) {
      var card = document.createElement("div");
      card.className = "card";
      card.style.cursor = "pointer";
      card.style.marginBottom = "8px";
      card.style.padding = "12px";
      var name = document.createElement("strong");
      name.textContent = tpl.name;
      var note = document.createElement("p");
      note.className = "muted";
      note.style.fontSize = "13px";
      note.style.margin = "4px 0 0";
      note.textContent = tpl.note || "";
      var level = document.createElement("span");
      level.className = "badge badge-info";
      level.style.marginLeft = "8px";
      level.textContent = "难度 " + (tpl.level || 1);
      name.appendChild(level);
      card.appendChild(name);
      card.appendChild(note);
      card.addEventListener("click", function () {
        _applyTemplate(tpl);
      });
      container.appendChild(card);
    });
  }

  function _applyTemplate(tpl) {
    toast.ok("已加载模板：" + tpl.name);
    _showManualPanel();
    var tabs = document.querySelectorAll(".strategy-tabs .tab");
    tabs.forEach(function (t) {
      t.classList.toggle("active", t.dataset.mode === "manual");
    });
    conditionBuilder.setDsl(tpl.dsl);
    var nameInput = document.getElementById("strategy-name");
    if (nameInput) nameInput.value = tpl.name;
    _renderConfirmPanel();
  }

  /* ── 条件构建器 ── */
  function _initCondition() {
    var el = document.getElementById("condition-area");
    if (!el) return;
    conditionBuilder.render(el, null, function () {
      _renderConfirmPanel();
    });
  }

  /* ── 选股 ── */
  function _initStockPicker() {
    var el = document.getElementById("stock-picker-area");
    if (!el) return;
    stockPicker.render(el, [], function () {
      _renderConfirmPanel();
    });
  }

  /* ── 模式选择 ── */
  function _initMode() {
    var modes = document.querySelectorAll(".mode-tab");
    modes.forEach(function (tab) {
      tab.addEventListener("click", function () {
        var mode = tab.dataset.mode;
        if (mode !== "backtest") {
          toast.warn("「" + tab.textContent.trim() + "」功能将在 V" + (mode === "realtime" ? "2" : "3") + " 版本提供");
          return;
        }
        modes.forEach(function (t) { t.classList.remove("active"); });
        tab.classList.add("active");
      });
    });
  }

  /* ── 周期与区间 ── */
  function _initPeriod() {
    var periodSel = document.getElementById("period-select");
    var startInput = document.getElementById("start-date");
    var endInput = document.getElementById("end-date");
    if (periodSel) {
      periodSel.addEventListener("change", function () { _renderConfirmPanel(); });
    }
    if (startInput) {
      startInput.value = "2020-01-01";
      startInput.addEventListener("change", function () { _renderConfirmPanel(); });
    }
    if (endInput) {
      endInput.value = new Date().toISOString().slice(0, 10);
      endInput.addEventListener("change", function () { _renderConfirmPanel(); });
    }
  }

  /* ── 确认面板 ── */
  function _renderConfirmPanel() {
    var panel = document.getElementById("confirm-panel-body");
    if (!panel) return;

    var dsl = conditionBuilder.getDsl();
    var codes = stockPicker.getCodes();
    var period = (document.getElementById("period-select") || {}).value || "daily";
    var startDate = (document.getElementById("start-date") || {}).value || "";
    var endDate = (document.getElementById("end-date") || {}).value || "";
    var name = (document.getElementById("strategy-name") || {}).value || "";

    var periodCn = { daily: "日线", weekly: "周线", monthly: "月线" };

    panel.innerHTML = "";

    var sections = [
      { label: "策略名称", value: name || "（未命名）" },
      { label: "周期", value: periodCn[period] || period },
      { label: "区间", value: (startDate && endDate) ? startDate + " ~ " + endDate : "（未设置）" },
      { label: "股票", value: codes.length ? codes.length + " 只" : "（未选择）" },
      { label: "买入条件", value: dsl && dsl.signals && dsl.signals.buy && dsl.signals.buy.items ? dsl.signals.buy.items.length + " 条" : "0 条" },
      { label: "卖出条件", value: dsl && dsl.signals && dsl.signals.sell && dsl.signals.sell.items ? dsl.signals.sell.items.length + " 条" : "（使用风控）" },
    ];

    sections.forEach(function (s) {
      var div = document.createElement("div");
      div.className = "confirm-section";
      var lbl = document.createElement("div");
      lbl.className = "confirm-label";
      lbl.textContent = s.label;
      var val = document.createElement("div");
      val.className = "confirm-value";
      val.textContent = s.value;
      div.appendChild(lbl);
      div.appendChild(val);
      panel.appendChild(div);
    });

    var btn = document.getElementById("run-btn");
    if (btn) {
      var canRun = name && codes.length > 0 && dsl && dsl.signals && dsl.signals.buy && dsl.signals.buy.items.length > 0;
      btn.classList.toggle("btn-incomplete", !canRun);
    }
  }

  /* ── 提交回测 ── */
  function _initSubmit() {
    var btn = document.getElementById("run-btn");
    if (!btn) return;
    btn.addEventListener("click", function () { _submit(); });

    document.addEventListener("keydown", function (e) {
      if (e.ctrlKey && e.key === "Enter") {
        e.preventDefault();
        _submit();
      }
    });
  }

  /* ── 验证并返回缺失项 ── */
  function _validate(name, dsl, codes, startDate, endDate) {
    if (!name) {
      return { field: "策略名称", section: "section-strategy", hint: "请先给策略起个名字" };
    }
    if (!codes.length) {
      return { field: "股票", section: "section-stock", hint: "请至少选择一只股票" };
    }
    if (!dsl || !dsl.signals || !dsl.signals.buy || !dsl.signals.buy.items.length) {
      return { field: "买入条件", section: "section-strategy", hint: "请至少设置一条买入条件" };
    }
    if (!startDate) {
      return { field: "开始日期", section: "section-params", hint: "请填写开始日期" };
    }
    if (!endDate) {
      return { field: "结束日期", section: "section-params", hint: "请填写结束日期" };
    }
    return null;
  }

  /* ── 显示缺失提示并引导 ── */
  function _showMissingDialog(missing) {
    // 显示对话框
    dialog.warn(
      "无法运行",
      "你尚未添加「" + missing.field + "」，导致无法运行。\n\n" + missing.hint,
      function () {
        // 点击确定后滚动到对应区域
        _scrollToSection(missing.section);
      }
    );
  }

  /* ── 滚动到指定区域并高亮 ── */
  function _scrollToSection(sectionId) {
    var section = document.getElementById(sectionId);
    if (!section) return;

    // 滚动到该区域
    section.scrollIntoView({ behavior: "smooth", block: "center" });

    // 添加高亮效果
    section.classList.remove("section-highlight");
    // 强制重排以重新触发动画
    void section.offsetWidth;
    section.classList.add("section-highlight");

    // 1.5秒后移除高亮类
    setTimeout(function () {
      section.classList.remove("section-highlight");
    }, 1500);
  }

  function _submit() {
    var name = (document.getElementById("strategy-name") || {}).value || "";
    var dsl = conditionBuilder.getDsl();
    var codes = stockPicker.getCodes();
    var period = (document.getElementById("period-select") || {}).value || "daily";
    var startDate = (document.getElementById("start-date") || {}).value || "";
    var endDate = (document.getElementById("end-date") || {}).value || "";

    // 验证并返回缺失项
    var missing = _validate(name, dsl, codes, startDate, endDate);
    if (missing) {
      _showMissingDialog(missing);
      return;
    }

    api.post("/api/strategies", {
      name: name,
      dsl: dsl,
      period: period,
      source: "manual",
    }).then(function (strategy) {
      return api.post("/api/backtests", {
        strategy_id: strategy.id,
        dsl: dsl,
        codes: codes,
        start_date: startDate,
        end_date: endDate,
        period: period,
      });
    }).then(function (result) {
      if (result.run_id) {
        _showProgress(result.run_id, codes);
      }
    }).catch(function (err) {
      if (err && err.kind === "integrity" && err.detail) {
        try {
          var pfResults = JSON.parse(err.detail);
          preflight.update(pfResults);
        } catch (e) { /* ignore */ }
      }
    });
  }

  function _showProgress(runId, codes) {
    var mask = document.createElement("div");
    mask.className = "mask";
    mask.style.flexDirection = "column";
    mask.style.gap = "12px";

    var title = document.createElement("div");
    title.style.fontSize = "16px";
    title.style.fontWeight = "600";
    title.textContent = "回测运行中...";
    mask.appendChild(title);

    var list = document.createElement("div");
    list.style.fontSize = "13px";
    list.style.textAlign = "left";
    codes.forEach(function (code) {
      var div = document.createElement("div");
      div.id = "progress-" + code;
      div.style.padding = "4px 0";
      div.innerHTML = '<span class="progress-icon">⏳</span> ' + code;
      list.appendChild(div);
    });
    mask.appendChild(list);

    var cancelBtn = document.createElement("button");
    cancelBtn.type = "button";
    cancelBtn.className = "btn btn-secondary";
    cancelBtn.textContent = "取消";
    cancelBtn.addEventListener("click", function () {
      api.post("/api/backtests/" + runId + "/cancel").then(function () {
        toast.warn("已取消回测");
        mask.remove();
      });
    });
    mask.appendChild(cancelBtn);

    document.body.appendChild(mask);

    var pollTimer = setInterval(function () {
      api.get("/api/backtests/pending").then(function (data) {
        var pending = (data.pending || []).find(function (p) { return p.run_id === runId; });
        if (!pending) {
          clearInterval(pollTimer);
          mask.remove();
          toast.ok("回测完成！");
          window.location.href = "/backtest.html?run=" + runId;
          return;
        }
        codes.forEach(function (code) {
          var el = document.getElementById("progress-" + code);
          if (!el) return;
          if (code === pending.code) {
            el.querySelector(".progress-icon").textContent = "⏳";
          } else {
            var idx = codes.indexOf(code);
            var curIdx = codes.indexOf(pending.code);
            if (idx < curIdx) el.querySelector(".progress-icon").textContent = "✓";
          }
        });
      }).catch(function () {
        clearInterval(pollTimer);
        mask.remove();
      });
    }, 2000);
  }

  /* ── 最近运行 ── */
  function _initRecentRuns() {
    var container = document.getElementById("recent-runs");
    if (!container) return;
    api.get("/api/backtests/runs?limit=5").then(function (data) {
      var items = data.items || [];
      if (!items.length) {
        container.innerHTML = '<p class="muted" style="font-size:13px">暂无运行记录</p>';
        return;
      }
      container.innerHTML = "";
      items.forEach(function (run) {
        var div = document.createElement("div");
        div.className = "recent-run-item";
        var status = document.createElement("span");
        status.className = "badge " + (run.status === "done" ? "badge-ok" : "badge-warn");
        status.textContent = run.status === "done" ? "完成" : "运行中";
        var name = document.createElement("a");
        name.href = "/backtest.html?run=" + run.id;
        name.textContent = run.name || ("回测 #" + run.id);
        var time = document.createElement("span");
        time.className = "muted";
        time.style.fontSize = "12px";
        time.style.marginLeft = "auto";
        time.textContent = run.created_at || "";
        div.appendChild(status);
        div.appendChild(name);
        div.appendChild(time);
        container.appendChild(div);
      });
    }).catch(function () {
      container.innerHTML = '<p class="muted" style="font-size:13px">加载失败</p>';
    });
  }
})();
