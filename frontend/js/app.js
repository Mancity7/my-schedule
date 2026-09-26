/* 全局小脚本：顶栏数据源状态灯 + 外观主题。页面专属逻辑各页自己管。 */
(function () {
  var KEY = "theme";

  function _stored() {
    try { return localStorage.getItem(KEY) || "auto"; } catch (e) { return "auto"; }
  }

  /* "自动"就是不写 data-theme，交给 CSS 的 prefers-color-scheme 分支判。
     这样系统切深浅色时不用 JS 参与，页面自己就跟着变了。 */
  function _apply(choice) {
    var root = document.documentElement;
    if (choice === "light" || choice === "dark") root.setAttribute("data-theme", choice);
    else root.removeAttribute("data-theme");
  }

  function _paintButtons(choice) {
    var box = document.getElementById("theme-switch");
    if (!box) return;
    Array.prototype.forEach.call(box.querySelectorAll("button"), function (b) {
      var on = b.getAttribute("data-theme-choice") === choice;
      b.classList.toggle("active", on);
      b.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  /* 图表颜色是从 CSS 令牌读的，读一次就缓存住了，换主题得叫它重读一遍 */
  function _repaintCharts() {
    if (window.charts && charts.refreshTheme) charts.refreshTheme();
  }

  function initTheme() {
    var choice = _stored();
    _apply(choice);
    _paintButtons(choice);

    var box = document.getElementById("theme-switch");
    if (box) {
      box.addEventListener("click", function (e) {
        var btn = e.target && e.target.closest
          ? e.target.closest("button[data-theme-choice]") : null;
        if (!btn) return;
        choice = btn.getAttribute("data-theme-choice");
        try { localStorage.setItem(KEY, choice); } catch (err) { /* 隐私模式存不进去，不影响本次生效 */ }
        _apply(choice);
        _paintButtons(choice);
        _repaintCharts();
      });
    }

    var mq = window.matchMedia("(prefers-color-scheme: dark)");
    var onSystemChange = function () { if (_stored() === "auto") _repaintCharts(); };
    if (mq.addEventListener) mq.addEventListener("change", onSystemChange);
    else if (mq.addListener) mq.addListener(onSystemChange);
  }

  function refreshLight() {
    var el = document.getElementById("datasource-light");
    if (!el) return;
    fetch("/api/health")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var ok = d && d.status === "ok" && d.db === "ready";
        el.innerHTML =
          '<i class="dot ' + (ok ? "ok" : "err") + '"></i>服务：' +
          (ok ? "正常" : "异常") + (d && d.version ? " · v" + d.version : "");
      })
      .catch(function () {
        el.innerHTML = '<i class="dot err"></i>服务：连不上';
      });
  }

  document.addEventListener("DOMContentLoaded", function () {
    refreshLight();
    initTheme();
  });
})();
