/* 横向对比：多次回测结果并排看。从任务页或首页触发。 */
var compare = (function () {
  var mask = null;

  function open(runIds) {
    if (!runIds || runIds.length < 2) {
      toast.warn("至少选 2 个任务才能对比");
      return;
    }
    close();
    mask = document.createElement("div");
    mask.className = "trace-mask";
    mask.addEventListener("click", function (e) {
      if (e.target === mask) close();
    });

    var inner = document.createElement("div");
    inner.className = "trace-panel";
    inner.style.maxWidth = "960px";
    inner.setAttribute("role", "dialog");
    inner.setAttribute("aria-label", "横向对比");

    var header = document.createElement("div");
    header.className = "trace-header";
    header.innerHTML = '<h3>横向对比</h3>' +
      '<button class="btn btn-sm btn-ghost trace-close" type="button" aria-label="关闭">&times;</button>';
    inner.appendChild(header);
    header.querySelector(".trace-close").addEventListener("click", close);

    var body = document.createElement("div");
    body.className = "trace-body";
    body.innerHTML = '<div class="muted">加载中...</div>';
    inner.appendChild(body);

    mask.appendChild(inner);
    document.body.appendChild(mask);
    document.addEventListener("keydown", _escHandler);

    api.post("/api/backtests/compare", { run_ids: runIds }).then(function (data) {
      renderCompare(body, data.items || []);
    }).catch(function () {
      body.innerHTML = '<div class="muted">加载失败</div>';
    });
  }

  function _escHandler(e) {
    if (e.key === "Escape") close();
  }

  function close() {
    document.removeEventListener("keydown", _escHandler);
    if (mask) { mask.remove(); mask = null; }
  }

  function renderCompare(body, items) {
    if (!items.length) {
      body.innerHTML = '<div class="muted">无数据</div>';
      return;
    }

    var html = '<div class="tbl-wrap"><table class="tbl" id="compare-table"><thead><tr>';
    html += '<th>任务</th><th>股票</th><th class="num">总收益率</th><th class="num">年化</th>' +
      '<th class="num">最大回撤</th><th class="num">夏普</th><th class="num">胜率</th>' +
      '<th class="num">手续费</th><th class="num">交易次数</th>';
    html += '</tr></thead><tbody>';

    items.forEach(function (item) {
      var bts = item.backtests || [];
      bts.forEach(function (bt, bi) {
        var m = bt.metrics || {};
        var label = bi === 0 ? (item.name || "任务 #" + item.id) : "";
        html += '<tr>';
        html += '<td>' + label + '</td>';
        html += '<td>' + (bt.name || bt.code) + '</td>';
        html += '<td class="num">' + fmt.pct(m.total_return) + '</td>';
        html += '<td class="num">' + fmt.pct(m.annual_return) + '</td>';
        html += '<td class="num">' + fmt.pct(-(Math.abs(m.max_drawdown || 0))) + '</td>';
        html += '<td class="num">' + (m.sharpe != null ? m.sharpe.toFixed(2) : "--") + '</td>';
        html += '<td class="num">' + fmt.pct(m.win_rate) + '</td>';
        html += '<td class="num">' + (m.total_fees != null ? fmt.money(m.total_fees) : "--") + '</td>';
        html += '<td class="num">' + (m.total_trades != null ? m.total_trades : "--") + '</td>';
        html += '</tr>';
      });
    });
    html += '</tbody></table></div>';

    html += '<div id="compare-chart" style="height:260px;margin-top:16px"></div>';
    html += '<p class="muted" style="margin-top:8px;font-size:12px">提示：对比仅供参考，历史收益不代表未来表现。</p>';

    body.innerHTML = html;
    bindSort();
    renderEquityComparison(items);
  }

  function bindSort() {
    var table = document.getElementById("compare-table");
    if (!table) return;
    var ths = table.querySelectorAll("th");
    ths.forEach(function (th, colIdx) {
      th.style.cursor = "pointer";
      th.addEventListener("click", function () {
        var tbody = table.querySelector("tbody");
        var rows = Array.from(tbody.querySelectorAll("tr"));
        var isNum = th.classList.contains("num");
        var asc = th.getAttribute("data-sort") !== "asc";
        ths.forEach(function (t) { t.removeAttribute("data-sort"); });
        th.setAttribute("data-sort", asc ? "asc" : "desc");
        rows.sort(function (a, b) {
          var av = a.children[colIdx].textContent.trim();
          var bv = b.children[colIdx].textContent.trim();
          if (isNum) {
            av = parseFloat(av.replace(/[+%元,]/g, "")) || 0;
            bv = parseFloat(bv.replace(/[+%元,]/g, "")) || 0;
            return asc ? av - bv : bv - av;
          }
          return asc ? av.localeCompare(bv) : bv.localeCompare(av);
        });
        rows.forEach(function (r) { tbody.appendChild(r); });
      });
    });
  }

  function renderEquityComparison(items) {
    var el = document.getElementById("compare-chart");
    if (!el) return;
    var series = [];
    items.forEach(function (item) {
      (item.backtests || []).forEach(function (bt) {
        if (!bt.metrics || !bt.metrics.initial_cash) return;
        var eqStr = bt.equity;
        var eq;
        try { eq = typeof eqStr === "string" ? JSON.parse(eqStr) : eqStr; } catch (e) { return; }
        if (!eq || !eq.length) return;
        var initial = bt.metrics.initial_cash;
        series.push({
          name: (item.name || "#" + item.id) + " " + (bt.name || bt.code),
          dates: eq.map(function (e) { return e.ts; }),
          data: eq.map(function (e) { return +((e.equity / initial - 1) * 100).toFixed(2); }),
        });
      });
    });
    if (series.length) {
      charts.equityChart(el, series);
    }
  }

  return { open: open, close: close };
})();
