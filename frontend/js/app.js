/* 全局小脚本：顶栏数据源状态灯。页面专属逻辑各页自己管。 */
(function () {
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
  document.addEventListener("DOMContentLoaded", refreshLight);
})();
