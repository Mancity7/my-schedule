/* 预检面板。7 项检查表逐条渲染。红项阻塞运行，黄项仅警告。 */
var preflight = (function () {
  var rootEl = null;
  var items = [];
  var CHECKS = [
    { name: "code_valid", label: "股票代码有效" },
    { name: "date_range", label: "日期范围合理" },
    { name: "period", label: "周期设置正确" },
    { name: "dsl_valid", label: "策略条件合法" },
    { name: "initial_cash", label: "初始资金合理" },
    { name: "datasource_order", label: "数据源配置正常" },
    { name: "single_source", label: "数据源一致性" },
  ];

  function render(el) {
    rootEl = el;
    el.innerHTML = "";
    var ul = document.createElement("ul");
    ul.className = "preflight-list";
    items = [];
    CHECKS.forEach(function (c) {
      var li = document.createElement("li");
      li.className = "preflight-item pf-pending";
      li.dataset.check = c.name;
      var icon = document.createElement("span");
      icon.className = "pf-icon";
      icon.textContent = "·";
      var text = document.createElement("span");
      text.className = "pf-text";
      text.textContent = c.label;
      li.appendChild(icon);
      li.appendChild(text);
      ul.appendChild(li);
      items.push({ name: c.name, el: li, status: "pending" });
    });
    el.appendChild(ul);
  }

  function update(results) {
    (results || []).forEach(function (r) {
      var item = items.find(function (i) { return i.name === r.name; });
      if (!item) return;
      item.el.className = "preflight-item " + (r.ok ? "pf-ok" : "pf-err");
      item.el.querySelector(".pf-icon").textContent = r.ok ? "✓" : "✗";
      if (r.message) {
        var msg = item.el.querySelector(".pf-msg");
        if (!msg) {
          msg = document.createElement("span");
          msg.className = "pf-msg muted";
          msg.style.fontSize = "12px";
          msg.style.marginLeft = "4px";
          item.el.appendChild(msg);
        }
        msg.textContent = r.message;
      }
      item.status = r.ok ? "ok" : "err";
    });
  }

  function allOk() {
    return items.every(function (i) { return i.status === "ok"; });
  }

  function hasErrors() {
    return items.some(function (i) { return i.status === "err"; });
  }

  function reset() {
    items.forEach(function (i) {
      i.el.className = "preflight-item pf-pending";
      i.el.querySelector(".pf-icon").textContent = "·";
      var msg = i.el.querySelector(".pf-msg");
      if (msg) msg.remove();
      i.status = "pending";
    });
  }

  return {
    render: render,
    update: update,
    allOk: allOk,
    hasErrors: hasErrors,
    reset: reset,
  };
})();
