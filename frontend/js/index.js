/* 首页装配：三段布局 + 条件构建器 + 股票搜索 + 预检 + 右侧确认面板。 */
(function () {
  var templates = [];
  var strategies = [];
  var currentStrategy = null;
  var nlSourceText = "";        // 非空表示当前规则是「用大白话」确认来的

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
        tabs.forEach(function (t) { t.classList.remove("active"); });
        tab.classList.add("active");
        if (mode === "template") {
          nlSourceText = "";       // 用户自己换了入口，就别再挂"大白话"这个来源
          _showTemplatePanel();
        } else if (mode === "nl") {
          _showNlPanel();
        } else {
          nlSourceText = "";
          _showManualPanel();
        }
      });
    });
  }

  function _showManualPanel() {
    var tp = document.getElementById("template-panel");
    var mp = document.getElementById("manual-panel");
    var np = document.getElementById("nl-panel");
    if (tp) tp.style.display = "none";
    if (mp) mp.style.display = "";
    if (np) np.style.display = "none";
  }

  function _showTemplatePanel() {
    var tp = document.getElementById("template-panel");
    var mp = document.getElementById("manual-panel");
    var np = document.getElementById("nl-panel");
    if (mp) mp.style.display = "none";
    if (np) np.style.display = "none";
    if (tp) {
      tp.style.display = "";
      _renderTemplates();
    }
  }

  /* ── 用大白话写（V2 · DeepSeek）──
     PRD 场景 F：解析 → 规则卡片 + 人话解释 + 证据回显 → 用户点「确认规则无误」才载入。
     AI 的产出**不直接进条件构建器**，中间必须有人核对这一步。 */
  var nlConfigured = null;
  var nlParsed = null;          // 最近一次解析结果，确认时才用

  function _showNlPanel() {
    var tp = document.getElementById("template-panel");
    var mp = document.getElementById("manual-panel");
    var np = document.getElementById("nl-panel");
    if (mp) mp.style.display = "none";
    if (tp) tp.style.display = "none";
    if (!np) return;
    np.style.display = "";
    _renderNlPanel();            // 先画出来：🔒 的原则是不隐藏、不禁用到看不见
    api.get("/api/ai/status").then(function (s) {
      nlConfigured = !!(s && s.configured);
      _syncNlGate();
      if (!nlConfigured) _promptNeedKey();
    }).catch(function () {
      nlConfigured = false;
      _syncNlGate();
    });
  }

  function _promptNeedKey() {
    dialog.warn(
      "需要 DeepSeek API Key",
      "这个功能需要 DeepSeek API Key，去设置页填一下就能用（充值 5 元够用很久）。\n\n" +
      "没有 Key 也照样能用「手动搭条件」和「从模板选」，这两个不需要联网。",
      function () { location.href = "/settings.html"; },
      "去设置"
    );
  }

  function _syncNlGate() {
    var btn = document.getElementById("nl-parse-btn");
    var banner = document.getElementById("nl-gate-banner");
    if (banner) banner.style.display = nlConfigured ? "none" : "";
    if (btn) btn.disabled = !nlConfigured;
  }

  function _renderNlPanel() {
    var container = document.getElementById("nl-area");
    if (!container || container.children.length > 0) return;

    var banner = document.createElement("p");
    banner.id = "nl-gate-banner";
    banner.className = "muted";
    banner.style.cssText = "font-size:13px;padding:8px 10px;background:var(--warn-bg);" +
      "color:var(--warn);border-radius:6px;display:none";
    banner.textContent = "还没有配置 DeepSeek API Key，去「设置」页填一下就能用（充值 5 元够用很久）。" +
      "没填也不影响「手动搭条件」和「从模板选」，这两个不用联网。";
    container.appendChild(banner);

    var desc = document.createElement("p");
    desc.className = "muted";
    desc.style.cssText = "font-size:13px;margin:10px 0 8px;line-height:1.7";
    desc.textContent = "把你脑子里的买卖想法直接写下来，不用管格式。例如：「5日均线上穿20日均线，" +
      "而且当天成交量比5日均量大1.5倍的时候买入；跌破10日均线或者亏了8%就卖」";
    container.appendChild(desc);

    var textarea = document.createElement("textarea");
    textarea.className = "input";
    textarea.id = "nl-input";
    textarea.rows = 4;
    textarea.maxLength = 500;
    textarea.placeholder = "在这里写你的策略…";
    textarea.style.cssText = "width:100%;resize:vertical";
    container.appendChild(textarea);

    var btnRow = document.createElement("div");
    btnRow.style.cssText = "display:flex;gap:8px;align-items:center;margin-top:10px;flex-wrap:wrap";

    var parseBtn = document.createElement("button");
    parseBtn.type = "button";
    parseBtn.className = "btn btn-primary";
    parseBtn.id = "nl-parse-btn";
    parseBtn.textContent = "解析成规则";
    parseBtn.addEventListener("click", _parseNl);
    btnRow.appendChild(parseBtn);

    var hint = document.createElement("span");
    hint.className = "muted";
    hint.style.fontSize = "12px";
    hint.textContent = "解析结果要你亲自核对确认，AI 不会直接改你的策略";
    btnRow.appendChild(hint);

    container.appendChild(btnRow);

    var result = document.createElement("div");
    result.id = "nl-result";
    result.style.marginTop = "14px";
    container.appendChild(result);

    _syncNlGate();
  }

  function _parseNl() {
    var input = document.getElementById("nl-input");
    var btn = document.getElementById("nl-parse-btn");
    var result = document.getElementById("nl-result");
    if (!input || !btn || !result) return;

    var text = input.value.trim();
    if (!text) {
      toast.warn("先写一句你想怎么买卖，再点解析");
      input.focus();
      return;
    }

    btn.disabled = true;
    btn.textContent = "解析中…";
    result.innerHTML = '<p class="muted" style="font-size:13px">正在请 AI 解析，一般 5~20 秒…</p>';

    api.post("/api/ai/parse", { text: text }, 90000).then(function (data) {
      nlParsed = data;
      _renderNlResult(data);
    }).catch(function (err) {
      nlParsed = null;
      result.innerHTML = "";
      var box = document.createElement("div");
      box.style.cssText = "padding:10px 12px;border-radius:6px;background:var(--err-bg);" +
        "color:var(--err);font-size:13px";
      box.textContent = (err && err.message) || "解析失败，请重试";
      result.appendChild(box);
    }).then(function () {
      btn.disabled = !nlConfigured;
      btn.textContent = "解析成规则";
    });
  }

  /* 操作数 → 人话。指标中文名来自后端白名单，前端不硬编码（红线 6）。 */
  function _opLabel(op) {
    if (!op) return "?";
    if (op.t === "num") return String(op.value);
    var meta = conditionBuilder.meta();
    var cn = op.name;
    (meta.indicators || []).forEach(function (i) { if (i.code === op.name) cn = i.cn; });
    var args = (op.args || []).map(function (a) { return String(a); });
    var text = args.length ? cn + "(" + args.join(",") + ")" : cn;
    if (op.mult) text += " ×" + op.mult;
    return text;
  }

  function _cmpLabel(cmp) {
    var meta = conditionBuilder.meta();
    var found = null;
    (meta.operators || []).forEach(function (o) { if (o.code === cmp) found = o; });
    return found ? (found.symbol + " " + found.cn) : cmp;
  }

  /* 把一个条件组摊平成卡片行。最多 2 层，用缩进表示嵌套。 */
  function _appendCondRows(host, node, depth) {
    if (!node || !node.items) return;
    node.items.forEach(function (item) {
      if (item.t === "group") {
        var g = document.createElement("div");
        g.className = "muted";
        g.style.cssText = "font-size:12px;margin:6px 0 2px " + (depth * 16) + "px";
        g.textContent = (item.logic === "and" ? "以下全部满足：" : "以下任一满足：");
        host.appendChild(g);
        _appendCondRows(host, item, depth + 1);
        return;
      }
      var row = document.createElement("div");
      row.style.cssText = "display:flex;gap:8px;align-items:center;flex-wrap:wrap;" +
        "padding:6px 8px;margin:4px 0 4px " + (depth * 16) + "px;background:#f8fafc;border-radius:6px";
      [["左", _opLabel(item.left)], ["", _cmpLabel(item.cmp)], ["右", _opLabel(item.right)]]
        .forEach(function (pair) {
          var chip = document.createElement("span");
          chip.style.cssText = "font-size:13px;padding:2px 8px;border-radius:4px;" +
            (pair[0] ? "background:#fff;border:1px solid #e5e7eb" : "color:var(--muted)");
          chip.textContent = pair[1];
          row.appendChild(chip);
        });
      if (item.not) {
        var neg = document.createElement("span");
        neg.style.cssText = "font-size:12px;color:var(--err)";
        neg.textContent = "（取反：不满足时才算）";
        row.appendChild(neg);
      }
      host.appendChild(row);
    });
  }

  function _section(title) {
    var h = document.createElement("div");
    h.style.cssText = "font-weight:600;font-size:14px;margin:12px 0 4px";
    h.textContent = title;
    return h;
  }

  function _renderNlResult(data) {
    var result = document.getElementById("nl-result");
    if (!result) return;
    result.innerHTML = "";

    var card = document.createElement("div");
    card.className = "card";
    card.style.cssText = "padding:14px;background:#fff";

    var head = document.createElement("div");
    head.style.cssText = "font-weight:600;margin-bottom:4px";
    head.textContent = "AI 解析出的规则";
    card.appendChild(head);

    var src = document.createElement("p");
    src.className = "muted";
    src.style.cssText = "font-size:12px;margin-bottom:8px";
    src.textContent = "你的原话：" + data.text;
    card.appendChild(src);

    var ex = data.explain || {};
    var buy = data.dsl && data.dsl.signals && data.dsl.signals.buy;
    var sell = data.dsl && data.dsl.signals && data.dsl.signals.sell;

    if (buy) {
      card.appendChild(_section("买入条件（" + (buy.logic === "and" ? "全部满足" : "任一满足") + "）"));
      _appendCondRows(card, buy, 0);
    }
    if (sell && sell.items && sell.items.length) {
      card.appendChild(_section("卖出条件（" + (sell.logic === "and" ? "全部满足" : "任一满足") + "）"));
      _appendCondRows(card, sell, 0);
    }
    if (ex.risk_lines && ex.risk_lines.length) {
      card.appendChild(_section("风控"));
      ex.risk_lines.forEach(function (line) {
        var p = document.createElement("div");
        p.style.cssText = "font-size:13px;padding:3px 8px;margin:3px 0;background:#f8fafc;border-radius:6px";
        p.textContent = line;
        card.appendChild(p);
      });
    }

    card.appendChild(_section("人话解释"));
    var say = document.createElement("p");
    say.style.cssText = "font-size:13px;line-height:1.8;margin:0";
    say.textContent = ex.summary || "";
    card.appendChild(say);

    var evTitle = _section("证据回显");
    card.appendChild(evTitle);
    var ev = document.createElement("div");
    ev.id = "nl-evidence";
    ev.className = "muted";
    ev.style.fontSize = "13px";
    ev.textContent = "读取中…";
    card.appendChild(ev);
    _loadEvidence(data.dsl, ev);

    var actions = document.createElement("div");
    actions.style.cssText = "display:flex;gap:8px;margin-top:16px;flex-wrap:wrap";

    var okBtn = document.createElement("button");
    okBtn.type = "button";
    okBtn.className = "btn btn-primary";
    okBtn.textContent = "确认规则无误";
    okBtn.addEventListener("click", function () { _confirmNl(data); });
    actions.appendChild(okBtn);

    var againBtn = document.createElement("button");
    againBtn.type = "button";
    againBtn.className = "btn btn-secondary";
    againBtn.textContent = "不对，重新描述";
    againBtn.addEventListener("click", function () {
      result.innerHTML = "";
      nlParsed = null;
      var input = document.getElementById("nl-input");
      if (input) input.focus();
    });
    actions.appendChild(againBtn);

    card.appendChild(actions);
    result.appendChild(card);
  }

  /* 证据回显：拿规则引用到的每条线在真实行情上的最新值，让用户核对 AI 有没有理解错。
     要先在②选了股票才有数据；没选就如实说明，不编数字。 */
  function _loadEvidence(dsl, host) {
    var codes = stockPicker.getCodes();
    if (!codes || !codes.length) {
      host.textContent = "还没有选股票。到下面「② 选股票」选一只，这里就会显示 MA5 / MA20 这些线" +
        "在真实行情上的最新数值，方便你核对 AI 有没有理解错。";
      return;
    }
    var period = (document.getElementById("period-select") || {}).value || "daily";
    api.post("/api/ai/evidence", { dsl: dsl, code: codes[0], period: period }, 60000)
      .then(function (data) {
        host.textContent = "";
        var cap = document.createElement("div");
        cap.style.cssText = "font-size:12px;color:var(--muted);margin-bottom:4px";
        cap.textContent = data.code + " 截至 " + data.as_of + " 的真实数值：";
        host.appendChild(cap);
        (data.items || []).forEach(function (it) {
          var row = document.createElement("div");
          row.style.cssText = "display:flex;gap:8px;font-size:13px;padding:2px 0";
          var k = document.createElement("span");
          k.style.cssText = "min-width:180px";
          k.textContent = it.label;
          var v = document.createElement("span");
          v.style.fontWeight = "600";
          // 暖机段算不出来就如实说，不填 0 冒充（红线 2）
          v.textContent = it.value == null ? "数据不足，算不出来" : String(it.value);
          row.appendChild(k);
          row.appendChild(v);
          host.appendChild(row);
        });
        host.classList.remove("muted");
      }).catch(function (err) {
        host.textContent = "取不到证据：" + ((err && err.message) || "未知原因");
      });
  }

  /* 用户点了「确认规则无误」才把 DSL 交出去，并切回「手动搭条件」让他能继续微调。 */
  function _confirmNl(data) {
    conditionBuilder.setDsl(data.dsl);
    var nameEl = document.getElementById("strategy-name");
    if (nameEl && !nameEl.value.trim()) {
      nameEl.value = (data.text || "").slice(0, 20);
    }
    nlSourceText = data.text || "";
    var tabs = document.querySelectorAll(".strategy-tabs .tab");
    tabs.forEach(function (t) {
      t.classList.toggle("active", t.dataset.mode === "manual");
    });
    _showManualPanel();
    _renderConfirmPanel();
    toast.ok("规则已载入，可以在「手动搭条件」里继续微调");
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
      // 走「用大白话」确认过的策略记下来源和原话，日后能查这条规则当初是怎么来的
      source: nlSourceText ? "nl" : "manual",
      nl_text: nlSourceText || "",
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
