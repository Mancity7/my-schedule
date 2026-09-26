/* 我的任务页：列出所有回测任务，支持查看详情、删除、横向对比。 */
(function () {
  var runs = [];
  var selected = {};

  function init() {
    loadRuns();
    bindRefresh();
    bindCompare();
  }

  function loadRuns() {
    var el = document.getElementById("task-list");
    el.innerHTML = '<div class="muted">加载中...</div>';
    api.get("/api/backtests/runs?limit=50").then(function (data) {
      runs = data.items || [];
      selected = {};
      renderList();
    }).catch(function () {
      el.innerHTML = '<div class="muted">加载失败</div>';
    });
  }

  function renderList() {
    var el = document.getElementById("task-list");
    if (!runs.length) {
      el.innerHTML = '<div class="muted">暂无任务。去首页创建一个吧。</div>';
      updateCompareBtn();
      return;
    }
    el.innerHTML = "";
    runs.forEach(function (run) {
      var item = document.createElement("div");
      item.className = "task-item";

      var statusBadge = "";
      if (run.status === "running") statusBadge = '<span class="badge badge-info">运行中</span>';
      else if (run.status === "done") statusBadge = '<span class="badge badge-ok">完成</span>';
      else if (run.status === "error") statusBadge = '<span class="badge badge-err">异常</span>';
      else if (run.status === "cancelled") statusBadge = '<span class="badge badge-warn">已取消</span>';

      var bts = run.backtests || [];
      var stockSummary = bts.map(function (bt) {
        var ret = bt.total_return;
        var retStr = ret != null ? ((ret > 0 ? "+" : "") + (ret * 100).toFixed(2) + "%") : "";
        var cls = ret > 0 ? "up" : ret < 0 ? "down" : "flat";
        return (bt.name || bt.code) + (retStr ? ' <span class="' + cls + '">' + retStr + '</span>' : "");
      }).join("、");

      var checked = selected[run.id] ? "checked" : "";

      item.innerHTML =
        '<div class="task-item-main">' +
          '<label style="display:flex;align-items:center;gap:8px;cursor:pointer">' +
            '<input type="checkbox" class="task-check" data-id="' + run.id + '" ' + checked +
              (run.status !== "done" ? " disabled" : "") + '>' +
            '<div>' +
              '<div style="font-weight:600">' + (run.name || "任务 #" + run.id) + ' ' + statusBadge + '</div>' +
              '<div class="muted" style="font-size:12px;margin-top:2px">' +
                (run.period === "daily" ? "日线" : run.period === "weekly" ? "周线" : "月线") +
                ' · ' + fmt.date(run.start_date) + ' ~ ' + fmt.date(run.end_date) +
                ' · ' + (run.codes || []).length + ' 只股票' +
              '</div>' +
              (stockSummary ? '<div style="font-size:12px;margin-top:4px">' + stockSummary + '</div>' : '') +
            '</div>' +
          '</label>' +
        '</div>' +
        '<div class="task-item-actions">' +
          '<button class="btn btn-sm btn-ghost task-view" type="button" data-id="' + run.id + '">查看</button>' +
          '<button class="btn btn-sm btn-ghost task-del" type="button" data-id="' + run.id + '">删除</button>' +
        '</div>';

      el.appendChild(item);
    });

    el.querySelectorAll(".task-check").forEach(function (cb) {
      cb.addEventListener("change", function () {
        var id = parseInt(this.getAttribute("data-id"), 10);
        if (this.checked) selected[id] = true; else delete selected[id];
        updateCompareBtn();
      });
    });

    el.querySelectorAll(".task-view").forEach(function (btn) {
      btn.addEventListener("click", function () {
        location.href = "/backtest.html?run=" + this.getAttribute("data-id");
      });
    });

    el.querySelectorAll(".task-del").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var id = parseInt(this.getAttribute("data-id"), 10);
        dialog.confirm({
          title: "删除任务",
          body: "确认删除这个任务？删除后不可恢复。",
          okText: "删除",
          danger: true,
        }).then(function (ok) {
          if (!ok) return;
          api.del("/api/backtests/" + id).then(function () {
            toast.ok("已删除");
            loadRuns();
          }).catch(function () {});
        });
      });
    });
  }

  function updateCompareBtn() {
    var btn = document.getElementById("compare-btn");
    var count = Object.keys(selected).length;
    btn.disabled = count < 2;
    btn.textContent = count >= 2 ? "横向对比选中（" + count + "）" : "横向对比选中";
  }

  function bindRefresh() {
    var btn = document.getElementById("refresh-btn");
    if (btn) btn.addEventListener("click", loadRuns);
  }

  function bindCompare() {
    var btn = document.getElementById("compare-btn");
    if (btn) btn.addEventListener("click", function () {
      var ids = Object.keys(selected).map(Number);
      if (ids.length >= 2) compare.open(ids);
    });
  }

  document.addEventListener("DOMContentLoaded", init);
})();
