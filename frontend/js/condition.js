/* 条件构建器。本页最重的组件。
   DSL 结构：{ signals: { buy: { t:"group", logic:"and", items:[...] }, sell: ... }, risk: {...}, sizing: {...} }
   嵌套上限 2 层（D7）。cross_above/below 禁用固定数字右值（D6）。 */
var conditionBuilder = (function () {
  var indicators = [];
  var operators = [];
  var logicOpts = [{ code: "and", cn: "全部满足" }, { code: "or", cn: "任一满足" }];
  var loaded = false;
  var debounceTimer = null;
  var onChangeCb = null;
  var rootEl = null;
  var currentDsl = null;

  function load() {
    if (loaded) return Promise.resolve();
    return Promise.all([
      api.get("/api/indicators"),
      api.get("/api/operators"),
    ]).then(function (results) {
      indicators = results[0].items || [];
      var ops = results[1];
      operators = ops.compare || [];
      loaded = true;
    });
  }

  function _indOptions() {
    var cats = {};
    indicators.forEach(function (ind) {
      var cat = ind.cat || "其他";
      if (!cats[cat]) cats[cat] = [];
      cats[cat].push(ind);
    });
    return cats;
  }

  function _renderIndSelect(selected, container) {
    container.innerHTML = "";
    var sel = document.createElement("select");
    sel.className = "input cond-ind";
    var cats = _indOptions();
    Object.keys(cats).forEach(function (cat) {
      var og = document.createElement("optgroup");
      og.label = cat;
      cats[cat].forEach(function (ind) {
        var opt = document.createElement("option");
        opt.value = ind.code;
        opt.textContent = ind.cn + " (" + ind.code + ")";
        if (selected && selected.name === ind.code) opt.selected = true;
        og.appendChild(opt);
      });
      sel.appendChild(og);
    });
    container.appendChild(sel);
    return sel;
  }

  function _renderParamInputs(indCode, args, container) {
    var old = container.querySelectorAll(".cond-param");
    old.forEach(function (el) { el.remove(); });
    var ind = indicators.find(function (i) { return i.code === indCode; });
    if (!ind || !ind.params || !ind.params.length) return;
    ind.params.forEach(function (p, pi) {
      var wrap = document.createElement("span");
      wrap.className = "cond-param";
      wrap.style.display = "inline-flex";
      wrap.style.alignItems = "center";
      wrap.style.gap = "4px";
      var lbl = document.createElement("label");
      lbl.style.fontSize = "12px";
      lbl.style.color = "var(--muted)";
      lbl.textContent = p.cn;
      var inp = document.createElement("input");
      inp.type = "number";
      inp.className = "input";
      inp.style.width = "70px";
      inp.style.padding = "4px 6px";
      inp.style.fontSize = "12px";
      if (p.min != null) inp.min = p.min;
      if (p.max != null) inp.max = p.max;
      if (p.integer) inp.step = 1;
      inp.value = (args && args[pi] != null) ? args[pi] : (p.default != null ? p.default : "");
      inp.dataset.paramIdx = pi;
      wrap.appendChild(lbl);
      wrap.appendChild(inp);
      container.appendChild(wrap);
    });
  }

  function _renderOperandSelect(type, selected, container, label) {
    var sel = document.createElement("select");
    sel.className = "input cond-operand";
    sel.style.width = "auto";
    sel.style.minWidth = "100px";
    [
      { t: "ind", cn: "另一个指标" },
      { t: "num", cn: "固定数字" },
      { t: "mult", cn: "指标×倍数" },
    ].forEach(function (o) {
      var opt = document.createElement("option");
      opt.value = o.t;
      opt.textContent = o.cn;
      if (type === o.t) opt.selected = true;
      sel.appendChild(opt);
    });
    container.appendChild(sel);
    return sel;
  }

  function _renderRightValue(operand, cmpCode, condRow, container) {
    var valWrap = container.querySelector(".cond-right-wrap");
    if (!valWrap) {
      valWrap = document.createElement("span");
      valWrap.className = "cond-right-wrap";
      valWrap.style.display = "inline-flex";
      valWrap.style.alignItems = "center";
      valWrap.style.gap = "4px";
      container.appendChild(valWrap);
    }
    valWrap.innerHTML = "";

    var isCross = cmpCode === "cross_above" || cmpCode === "cross_below";
    var type = "ind";
    if (operand) {
      if (operand.t === "num") type = "num";
      else if (operand.mult && operand.mult !== 1) type = "mult";
      else type = "ind";
    }

    if (isCross) {
      type = "ind";
      var note = document.createElement("span");
      note.className = "muted";
      note.style.fontSize = "11px";
      note.textContent = "（上穿/下穿只能和另一个指标比较）";
      valWrap.appendChild(note);
    }

    var opSel = valWrap.querySelector(".cond-operand");
    if (!opSel) {
      opSel = _renderOperandSelect(type, operand, valWrap, "");
      if (isCross) opSel.disabled = true;
    }

    if (type === "num" && !isCross) {
      var numInp = document.createElement("input");
      numInp.type = "number";
      numInp.className = "input cond-num-val";
      numInp.style.width = "80px";
      numInp.style.padding = "4px 6px";
      numInp.style.fontSize = "12px";
      numInp.step = "any";
      numInp.value = operand && operand.value != null ? operand.value : "";
      numInp.placeholder = "数值";
      valWrap.appendChild(numInp);
    } else if (type === "mult") {
      var multInd = document.createElement("span");
      multInd.className = "cond-mult-ind";
      _renderIndSelect(operand, multInd);
      valWrap.appendChild(multInd);
      var multInp = document.createElement("input");
      multInp.type = "number";
      multInp.className = "input cond-mult-val";
      multInp.style.width = "60px";
      multInp.style.padding = "4px 6px";
      multInp.style.fontSize = "12px";
      multInp.step = "0.1";
      multInp.value = operand && operand.mult != null ? operand.mult : 1;
      multInp.placeholder = "倍数";
      valWrap.appendChild(document.createTextNode(" × "));
      valWrap.appendChild(multInp);
    } else {
      var indSpan = document.createElement("span");
      indSpan.className = "cond-right-ind";
      _renderIndSelect(operand, indSpan);
      var paramSpan = document.createElement("span");
      paramSpan.className = "cond-right-params";
      indSpan.appendChild(paramSpan);
      valWrap.appendChild(indSpan);
      var rightIndSel = indSpan.querySelector(".cond-ind");
      if (rightIndSel) {
        _renderParamInputs(rightIndSel.value, operand ? operand.args : null, paramSpan);
        rightIndSel.addEventListener("change", function () {
          _renderParamInputs(rightIndSel.value, null, paramSpan);
          _fireChange();
        });
      }
    }

    if (!isCross) {
      opSel.addEventListener("change", function () {
        _renderRightValue(null, cmpCode, condRow, container);
        _fireChange();
      });
    }
  }

  function _renderCondRow(item, groupEl) {
    var row = document.createElement("div");
    row.className = "cond-row";
    row.dataset.type = "cond";

    var leftWrap = document.createElement("span");
    leftWrap.style.display = "inline-flex";
    leftWrap.style.alignItems = "center";
    leftWrap.style.gap = "4px";
    leftWrap.className = "cond-left-wrap";
    _renderIndSelect(item.left, leftWrap);
    var leftParams = document.createElement("span");
    leftParams.className = "cond-left-params";
    leftWrap.appendChild(leftParams);
    row.appendChild(leftWrap);

    var leftIndSel = leftWrap.querySelector(".cond-ind");
    var left = item.left || { t: "ind", name: "", args: [] };
    _renderParamInputs(leftIndSel.value, left.args, leftParams);
    leftIndSel.addEventListener("change", function () {
      _renderParamInputs(leftIndSel.value, null, leftParams);
      _fireChange();
    });

    var cmpSel = document.createElement("select");
    cmpSel.className = "input cond-cmp";
    cmpSel.style.width = "auto";
    cmpSel.style.minWidth = "80px";
    operators.forEach(function (op) {
      var opt = document.createElement("option");
      opt.value = op.code;
      opt.textContent = op.cn + " " + op.symbol;
      if (item.cmp === op.code) opt.selected = true;
      cmpSel.appendChild(opt);
    });
    row.appendChild(cmpSel);

    var rightWrap = document.createElement("span");
    rightWrap.className = "cond-right-wrap";
    rightWrap.style.display = "inline-flex";
    rightWrap.style.alignItems = "center";
    rightWrap.style.gap = "4px";
    row.appendChild(rightWrap);
    _renderRightValue(item.right, item.cmp, row, rightWrap);

    cmpSel.addEventListener("change", function () {
      _renderRightValue(item.right, cmpSel.value, row, rightWrap);
      _fireChange();
    });

    var notLbl = document.createElement("label");
    notLbl.style.fontSize = "12px";
    notLbl.style.display = "inline-flex";
    notLbl.style.alignItems = "center";
    notLbl.style.gap = "2px";
    notLbl.style.cursor = "pointer";
    var notCb = document.createElement("input");
    notCb.type = "checkbox";
    notCb.checked = !!item.not;
    notCb.className = "cond-not";
    notLbl.appendChild(notCb);
    notLbl.appendChild(document.createTextNode("取反"));
    row.appendChild(notLbl);

    var delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.className = "cond-del";
    delBtn.textContent = "×";
    delBtn.title = "删除条件";
    delBtn.addEventListener("click", function () {
      row.remove();
      _fireChange();
    });
    row.appendChild(delBtn);

    row.addEventListener("change", function () { _fireChange(); });
    row.addEventListener("input", function () { _fireChange(); });
    return row;
  }

  function _renderGroup(group, depth) {
    var wrap = document.createElement("div");
    wrap.className = "cond-group";
    wrap.dataset.depth = depth;

    var header = document.createElement("div");
    header.className = "cond-group-header";

    var logicBtn = document.createElement("button");
    logicBtn.type = "button";
    logicBtn.className = "cond-logic-toggle";
    logicBtn.textContent = group.logic === "and" ? "全部满足 (AND)" : "任一满足 (OR)";
    logicBtn.addEventListener("click", function () {
      group.logic = group.logic === "and" ? "or" : "and";
      logicBtn.textContent = group.logic === "and" ? "全部满足 (AND)" : "任一满足 (OR)";
      _fireChange();
    });
    header.appendChild(logicBtn);

    var hint = document.createElement("span");
    hint.className = "muted";
    hint.style.fontSize = "12px";
    hint.textContent = "以下条件";
    header.appendChild(hint);

    if (depth > 0) {
      var delGroup = document.createElement("button");
      delGroup.type = "button";
      delGroup.className = "cond-del";
      delGroup.textContent = "×";
      delGroup.title = "删除条件组";
      delGroup.style.marginLeft = "auto";
      delGroup.addEventListener("click", function () {
        wrap.remove();
        _fireChange();
      });
      header.appendChild(delGroup);
    }

    wrap.appendChild(header);

    var itemsEl = document.createElement("div");
    itemsEl.className = "cond-items";
    (group.items || []).forEach(function (item) {
      if (item.t === "group" && depth < 1) {
        itemsEl.appendChild(_renderGroup(item, depth + 1));
      } else {
        itemsEl.appendChild(_renderCondRow(item, wrap));
      }
    });
    wrap.appendChild(itemsEl);

    var actions = document.createElement("div");
    actions.className = "cond-actions";
    var addCondBtn = document.createElement("button");
    addCondBtn.type = "button";
    addCondBtn.className = "btn btn-sm btn-secondary";
    addCondBtn.textContent = "+ 加条件";
    addCondBtn.addEventListener("click", function () {
      var newItem = { t: "cond", left: { t: "ind", name: "CLOSE", args: [] }, cmp: ">", right: { t: "num", value: 0 }, not: false };
      itemsEl.appendChild(_renderCondRow(newItem, wrap));
      _fireChange();
    });
    actions.appendChild(addCondBtn);

    if (depth < 1) {
      var addGroupBtn = document.createElement("button");
      addGroupBtn.type = "button";
      addGroupBtn.className = "btn btn-sm btn-secondary";
      addGroupBtn.textContent = "+ 加条件组";
      addGroupBtn.addEventListener("click", function () {
        var newGroup = { t: "group", logic: "and", items: [] };
        itemsEl.appendChild(_renderGroup(newGroup, depth + 1));
        _fireChange();
      });
      actions.appendChild(addGroupBtn);
    } else {
      var disabled = document.createElement("span");
      disabled.className = "muted";
      disabled.style.fontSize = "12px";
      disabled.textContent = "（最多嵌套 2 层）";
      actions.appendChild(disabled);
    }

    wrap.appendChild(actions);
    return wrap;
  }

  function _collectGroup(groupEl) {
    var logicBtn = groupEl.querySelector(".cond-group-header .cond-logic-toggle");
    var logic = logicBtn && logicBtn.textContent.indexOf("全部") >= 0 ? "and" : "or";
    var items = [];
    var condRows = groupEl.querySelectorAll(":scope > .cond-items > .cond-row");
    condRows.forEach(function (row) {
      var leftSel = row.querySelector(".cond-left-wrap .cond-ind");
      var leftParams = row.querySelectorAll(".cond-left-params .cond-param input");
      var args = [];
      leftParams.forEach(function (inp) { args.push(parseFloat(inp.value) || 0); });

      var cmpSel = row.querySelector(".cond-cmp");
      var notCb = row.querySelector(".cond-not");

      var opSel = row.querySelector(".cond-operand");
      var opType = opSel ? opSel.value : "num";
      var right;
      if (opType === "num") {
        var numVal = row.querySelector(".cond-num-val");
        right = { t: "num", value: parseFloat(numVal ? numVal.value : 0) || 0 };
      } else if (opType === "mult") {
        var multIndSel = row.querySelector(".cond-mult-ind .cond-ind");
        var multVal = row.querySelector(".cond-mult-val");
        var mArgs = [];
        row.querySelectorAll(".cond-mult-ind .cond-param input").forEach(function (inp) {
          mArgs.push(parseFloat(inp.value) || 0);
        });
        right = {
          t: "ind",
          name: multIndSel ? multIndSel.value : "CLOSE",
          args: mArgs,
          mult: parseFloat(multVal ? multVal.value : 1) || 1,
        };
      } else {
        var rightIndSel = row.querySelector(".cond-right-ind .cond-ind");
        var rArgs = [];
        row.querySelectorAll(".cond-right-params .cond-param input").forEach(function (inp) {
          rArgs.push(parseFloat(inp.value) || 0);
        });
        right = { t: "ind", name: rightIndSel ? rightIndSel.value : "CLOSE", args: rArgs };
      }

      items.push({
        t: "cond",
        left: { t: "ind", name: leftSel ? leftSel.value : "CLOSE", args: args },
        cmp: cmpSel ? cmpSel.value : ">",
        right: right,
        not: notCb ? notCb.checked : false,
      });
    });

    var subGroups = groupEl.querySelectorAll(":scope > .cond-items > .cond-group");
    subGroups.forEach(function (sg) {
      items.push(_collectGroup(sg));
    });

    return { t: "group", logic: logic, items: items };
  }

  function _collectDsl() {
    if (!rootEl) return null;
    var buyGroupEl = rootEl.querySelector(".buy-section > .cond-group");
    var sellGroupEl = rootEl.querySelector(".sell-section > .cond-group");

    var buy = buyGroupEl ? _collectGroup(buyGroupEl) : { t: "group", logic: "and", items: [] };
    var sell = sellGroupEl ? _collectGroup(sellGroupEl) : null;
    if (sell && sell.items.length === 0) sell = null;

    var risk = {};
    rootEl.querySelectorAll(".risk-input").forEach(function (inp) {
      var v = inp.value.trim();
      if (v === "") risk[inp.dataset.risk] = null;
      else risk[inp.dataset.risk] = parseFloat(v) || null;
    });

    return {
      signals: { buy: buy, sell: sell },
      sizing: { mode: "all_in" },
      risk: risk,
    };
  }

  function _fireChange() {
    currentDsl = _collectDsl();
    if (onChangeCb) onChangeCb(currentDsl);
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(function () {
      if (currentDsl && currentDsl.signals.buy.items.length > 0) {
        api.post("/api/strategies/explain", { dsl: currentDsl }).then(function (data) {
          var preview = rootEl.querySelector(".explain-preview");
          if (preview && data.explain) {
            var lines = (data.explain.buy_lines || []).concat(data.explain.sell_lines || []);
            preview.textContent = lines.join("；") || data.explain.summary || "";
          }
        }).catch(function () { /* 静默 */ });
      }
    }, 300);
  }

  function render(el, dsl, onChange) {
    rootEl = el;
    onChangeCb = onChange;
    currentDsl = dsl || null;
    el.innerHTML = "";

    var buySection = document.createElement("div");
    buySection.className = "buy-section";
    var buyLabel = document.createElement("h3");
    buyLabel.textContent = "买入条件";
    buySection.appendChild(buyLabel);
    var buyDsl = dsl && dsl.signals && dsl.signals.buy ? dsl.signals.buy : { t: "group", logic: "and", items: [] };
    buySection.appendChild(_renderGroup(buyDsl, 0));
    el.appendChild(buySection);

    var sellSection = document.createElement("div");
    sellSection.className = "sell-section";
    sellSection.style.marginTop = "12px";
    var sellLabel = document.createElement("h3");
    sellLabel.textContent = "卖出条件";
    var sellHint = document.createElement("span");
    sellHint.className = "muted";
    sellHint.style.fontSize = "12px";
    sellHint.style.marginLeft = "8px";
    sellHint.textContent = "（可留空，使用风控卖出）";
    sellLabel.appendChild(sellHint);
    sellSection.appendChild(sellLabel);
    var sellDsl = dsl && dsl.signals && dsl.signals.sell ? dsl.signals.sell : { t: "group", logic: "or", items: [] };
    sellSection.appendChild(_renderGroup(sellDsl, 0));
    el.appendChild(sellSection);

    var riskSection = document.createElement("div");
    riskSection.className = "risk-section";
    riskSection.style.marginTop = "12px";
    var riskLabel = document.createElement("h3");
    riskLabel.textContent = "风控参数";
    riskSection.appendChild(riskLabel);
    var riskGrid = document.createElement("div");
    riskGrid.style.display = "grid";
    riskGrid.style.gridTemplateColumns = "repeat(auto-fill, minmax(180px, 1fr))";
    riskGrid.style.gap = "8px";
    var riskFields = [
      { key: "stop_loss_pct", cn: "止损线 (%)", placeholder: "如 8" },
      { key: "take_profit_pct", cn: "止盈线 (%)", placeholder: "如 20" },
      { key: "trailing_stop_pct", cn: "移动止损 (%)", placeholder: "如 5" },
      { key: "max_hold_days", cn: "最长持有(天)", placeholder: "如 30" },
    ];
    riskFields.forEach(function (rf) {
      var lbl = document.createElement("label");
      lbl.className = "field";
      lbl.style.marginBottom = "0";
      var fl = document.createElement("span");
      fl.className = "field-label";
      fl.textContent = rf.cn;
      lbl.appendChild(fl);
      var inp = document.createElement("input");
      inp.type = "number";
      inp.className = "input risk-input";
      inp.dataset.risk = rf.key;
      inp.placeholder = rf.placeholder;
      inp.step = "any";
      var riskData = dsl && dsl.risk ? dsl.risk : {};
      if (riskData[rf.key] != null) inp.value = riskData[rf.key];
      inp.addEventListener("input", function () { _fireChange(); });
      lbl.appendChild(inp);
      riskGrid.appendChild(lbl);
    });
    riskSection.appendChild(riskGrid);
    el.appendChild(riskSection);

    var preview = document.createElement("div");
    preview.className = "explain-preview card";
    preview.style.marginTop = "12px";
    preview.style.padding = "10px 14px";
    preview.style.fontSize = "13px";
    preview.style.color = "var(--muted)";
    preview.textContent = "填写条件后，这里会显示策略的人话描述";
    el.appendChild(preview);

    if (dsl) _fireChange();
  }

  function getDsl() {
    return currentDsl || _collectDsl();
  }

  function setDsl(dsl) {
    currentDsl = dsl;
    if (rootEl) render(rootEl, dsl, onChangeCb);
  }

  return {
    load: load,
    render: render,
    getDsl: getDsl,
    setDsl: setDsl,
  };
})();
