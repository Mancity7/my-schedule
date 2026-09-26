/* 股票搜索 + 多选。代码/名称/拼音命中。已选 chips 可移除。 */
var stockPicker = (function () {
  var rootEl = null;
  var inputEl = null;
  var suggestEl = null;
  var selectedEl = null;
  var selected = [];
  var focusIdx = -1;
  var onChangeCb = null;
  var MAX = 50;

  function render(el, codes, onChange) {
    rootEl = el;
    selected = codes || [];
    onChangeCb = onChange;
    el.innerHTML = "";

    var wrap = document.createElement("div");
    wrap.className = "stock-search-wrap";

    inputEl = document.createElement("input");
    inputEl.type = "text";
    inputEl.className = "input";
    inputEl.placeholder = "输入代码、名称或拼音首字母搜索（按 / 聚焦）";
    inputEl.maxLength = 20;
    inputEl.addEventListener("input", function () { _search(); });
    inputEl.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown") { e.preventDefault(); _moveFocus(1); }
      if (e.key === "ArrowUp") { e.preventDefault(); _moveFocus(-1); }
      if (e.key === "Enter") { e.preventDefault(); _selectFocused(); }
      if (e.key === "Escape") { _hideSuggest(); }
    });
    inputEl.addEventListener("focus", function () { if (inputEl.value.trim()) _search(); });
    wrap.appendChild(inputEl);

    suggestEl = document.createElement("div");
    suggestEl.className = "stock-suggest";
    suggestEl.style.display = "none";
    wrap.appendChild(suggestEl);

    el.appendChild(wrap);

    selectedEl = document.createElement("div");
    selectedEl.className = "stock-selected";
    el.appendChild(selectedEl);
    _renderChips();

    document.addEventListener("keydown", function (e) {
      if (e.key === "/" && document.activeElement !== inputEl && e.target.tagName !== "INPUT" && e.target.tagName !== "TEXTAREA") {
        e.preventDefault();
        inputEl.focus();
      }
    });

    document.addEventListener("click", function (e) {
      if (!wrap.contains(e.target)) _hideSuggest();
    });
  }

  function _search() {
    var kw = inputEl.value.trim();
    if (!kw) { _hideSuggest(); return; }
    api.get("/api/stocks/search?kw=" + encodeURIComponent(kw) + "&limit=8").then(function (data) {
      var items = data.items || [];
      if (!items.length) {
        suggestEl.innerHTML = '<div style="padding:12px;color:var(--muted);font-size:13px">未找到匹配股票</div>';
        suggestEl.style.display = "block";
        return;
      }
      suggestEl.innerHTML = "";
      focusIdx = -1;
      items.forEach(function (item, i) {
        var div = document.createElement("div");
        div.className = "stock-suggest-item";
        div.dataset.code = item.code;
        var name = document.createElement("span");
        name.textContent = item.name || item.code;
        var code = document.createElement("span");
        code.className = "code";
        code.textContent = item.code;
        var market = document.createElement("span");
        market.className = "muted";
        market.style.fontSize = "11px";
        market.textContent = (item.market || "").toUpperCase();
        div.appendChild(name);
        div.appendChild(code);
        div.appendChild(market);
        if (selected.indexOf(item.code) >= 0) {
          var tag = document.createElement("span");
          tag.className = "badge badge-flat";
          tag.textContent = "已选";
          div.appendChild(tag);
        }
        div.addEventListener("click", function () { _addStock(item.code, item.name); });
        suggestEl.appendChild(div);
      });
      suggestEl.style.display = "block";
    }).catch(function () { _hideSuggest(); });
  }

  function _moveFocus(dir) {
    var items = suggestEl.querySelectorAll(".stock-suggest-item");
    if (!items.length) return;
    items.forEach(function (it) { it.classList.remove("focused"); });
    focusIdx += dir;
    if (focusIdx < 0) focusIdx = items.length - 1;
    if (focusIdx >= items.length) focusIdx = 0;
    items[focusIdx].classList.add("focused");
    items[focusIdx].scrollIntoView({ block: "nearest" });
  }

  function _selectFocused() {
    var items = suggestEl.querySelectorAll(".stock-suggest-item");
    if (focusIdx >= 0 && focusIdx < items.length) {
      var code = items[focusIdx].dataset.code;
      var name = items[focusIdx].querySelector("span").textContent;
      _addStock(code, name);
    }
  }

  function _addStock(code, name) {
    if (selected.indexOf(code) >= 0) {
      toast.warn(code + " 已在列表中");
      return;
    }
    if (selected.length >= MAX) {
      toast.warn("最多选 " + MAX + " 只股票");
      return;
    }
    selected.push(code);
    inputEl.value = "";
    _hideSuggest();
    _renderChips();
    if (onChangeCb) onChangeCb(selected.slice());
  }

  function _removeStock(code) {
    selected = selected.filter(function (c) { return c !== code; });
    _renderChips();
    if (onChangeCb) onChangeCb(selected.slice());
  }

  function _renderChips() {
    if (!selectedEl) return;
    selectedEl.innerHTML = "";
    if (!selected.length) {
      selectedEl.innerHTML = '<span class="muted" style="font-size:13px">尚未选择股票</span>';
      return;
    }
    selected.forEach(function (code) {
      var chip = document.createElement("span");
      chip.className = "chip";
      chip.textContent = code;
      var x = document.createElement("i");
      x.className = "chip-x";
      x.textContent = "×";
      x.addEventListener("click", function () { _removeStock(code); });
      chip.appendChild(x);
      selectedEl.appendChild(chip);
    });
  }

  function _hideSuggest() {
    if (suggestEl) suggestEl.style.display = "none";
    focusIdx = -1;
  }

  function getCodes() { return selected.slice(); }
  function setCodes(codes) { selected = codes || []; _renderChips(); }

  return {
    render: render,
    getCodes: getCodes,
    setCodes: setCodes,
  };
})();
