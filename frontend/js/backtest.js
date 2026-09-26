/* 回测结果页：标签切换、指标卡、K线+副图、收益曲线、明细表、CSV导出。 */
(function () {
  var runId = null;
  var runData = null;
  var currentCode = null;
  var currentBt = null;
  var mainChart = null;
  var subChartInst = null;
  var equityChartInst = null;
  var allBars = [];
  var PAGE_SIZE = 50;
  var tradePage = 0;
  var tradesAll = [];

  function init() {
    var m = location.search.match(/[?&]run=(\d+)/);
    if (!m) { toast.err("缺少 run 参数"); return; }
    runId = parseInt(m[1], 10);
    termtip.loadDynamic();
    loadRun();
  }

  function loadRun() {
    api.get("/api/backtests/runs/" + runId).then(function (data) {
      runData = data;
      var bcEl = document.getElementById("bc-strategy");
      if (bcEl) bcEl.textContent = data.name || ("策略 #" + data.strategy_id);
      if (!data.backtests || !data.backtests.length) {
        toast.warn("该任务暂无回测结果");
        return;
      }
      renderStockTabs();
      selectStock(data.backtests[0].code);
    }).catch(function () {});
  }

  function renderStockTabs() {
    var el = document.getElementById("stock-tabs");
    if (!el) return;
    el.innerHTML = "";
    runData.backtests.forEach(function (bt) {
      var btn = document.createElement("button");
      btn.className = "tab";
      btn.type = "button";
      btn.setAttribute("data-code", bt.code);
      var label = (bt.name || bt.code);
      var ret = bt.metrics && bt.metrics.total_return;
      if (ret != null) {
        var cls = ret > 0 ? "up" : ret < 0 ? "down" : "flat";
        var prefix = ret > 0 ? "+" : "";
        label += ' <span class="' + cls + '">' + prefix + (ret * 100).toFixed(2) + "%</span>";
      }
      if (bt.status === "error") {
        label += ' <span class="badge badge-err">异常</span>';
      }
      btn.innerHTML = label;
      btn.addEventListener("click", function () { selectStock(bt.code); });
      el.appendChild(btn);
    });
  }

  function selectStock(code) {
    currentCode = code;
    currentBt = null;
    for (var i = 0; i < runData.backtests.length; i++) {
      if (runData.backtests[i].code === code) { currentBt = runData.backtests[i]; break; }
    }
    if (!currentBt) return;

    var tabs = document.querySelectorAll("#stock-tabs .tab");
    tabs.forEach(function (t) { t.classList.toggle("active", t.getAttribute("data-code") === code); });

    if (currentBt.status === "error") {
      toast.err(currentBt.error || "回测异常");
      clearCharts();
      return;
    }

    renderQuality();
    renderMetrics();
    fetchBarsAndRender();
    renderEquity();
    renderTrades();
  }

  function clearCharts() {
    if (mainChart) { mainChart.dispose(); mainChart = null; }
    if (subChartInst) { subChartInst.dispose(); subChartInst = null; }
    if (equityChartInst) { equityChartInst.dispose(); equityChartInst = null; }
    var mg = document.getElementById("metric-grid");
    if (mg) mg.innerHTML = "";
    var qb = document.getElementById("quality-bar");
    if (qb) { qb.style.display = "none"; }
  }

  function renderQuality() {
    var el = document.getElementById("quality-bar");
    if (!el || !currentBt.quality) return;
    var q = currentBt.quality;
    var gapPct = q.gap_pct || 0;
    el.style.display = "block";
    el.className = "quality-bar " + (gapPct > 5 ? "err" : gapPct > 1 ? "warn" : "ok");
    el.innerHTML = "数据质量：有效 " + q.valid_bars + " 根 / 缺口 " + q.skipped_gap +
      " 根（" + gapPct.toFixed(1) + "%）" +
      (gapPct > 5 ? ' <span class="badge badge-err">缺口过大</span>' : "");
  }

  function renderMetrics() {
    var el = document.getElementById("metric-grid");
    if (!el || !currentBt.metrics) return;
    var m = currentBt.metrics;
    var items = [
      { term: "总收益率", val: fmt.pct(m.total_return) },
      { term: "年化收益率", val: fmt.pct(m.annual_return) },
      { term: "最大回撤", val: fmt.pct(-Math.abs(m.max_drawdown || 0)) },
      { term: "夏普比率", val: m.sharpe != null ? m.sharpe.toFixed(2) : "--" },
      { term: "波动率", val: fmt.pct(m.volatility) },
      { term: "胜率", val: fmt.pct(m.win_rate) },
      { term: "盈亏比", val: m.profit_loss_ratio != null ? m.profit_loss_ratio.toFixed(2) : "--" },
      { term: "总手续费", val: m.total_fees != null ? fmt.money(m.total_fees) + " 元" : "--" },
      { term: "基准收益", val: m.bench_return != null ? fmt.pct(m.bench_return) : "--" },
      { term: "上证指数", val: m.shanghai_return != null ? fmt.pct(m.shanghai_return) : "--" },
      { term: "买入持有", val: m.buy_hold_return != null ? fmt.pct(m.buy_hold_return) : "--" },
      { term: "总交易次数", val: m.total_trades != null ? m.total_trades + " 次" : "--" },
      { term: "期末资产", val: m.final_equity != null ? fmt.money(m.final_equity) + " 元" : "--" },
    ];
    el.innerHTML = "";
    items.forEach(function (it) {
      var card = document.createElement("div");
      card.className = "metric-card";
      card.innerHTML = '<div class="metric-label"><span data-term="' + it.term + '">' + it.term + '</span></div>' +
        '<div class="metric-value">' + it.val + '</div>';
      el.appendChild(card);
    });
    termtip.attach(el);
  }

  function fetchBarsAndRender() {
    var start = runData.start_date;
    var end = runData.end_date;
    var period = runData.period || "daily";
    api.get("/api/stocks/" + currentCode + "/kline?start=" + start + "&end=" + end + "&period=" + period)
      .then(function (data) {
        allBars = data.bars || [];
        renderMainChart();
        renderSubChart("volume");
      }).catch(function () {});
  }

  function renderMainChart() {
    var el = document.getElementById("main-chart");
    if (!el) return;
    if (mainChart) mainChart.dispose();

    var marks = [];
    if (currentBt && currentBt.trades) {
      currentBt.trades.forEach(function (t) {
        marks.push({ ts: t.ts, side: t.side, price: t.price });
      });
    }

    var overlays = [];
    var ma5 = buildMA(5);
    var ma10 = buildMA(10);
    var ma20 = buildMA(20);
    var chk5 = document.getElementById("ma5-overlay");
    var chk10 = document.getElementById("ma10-overlay");
    var chk20 = document.getElementById("ma20-overlay");
    if (chk5 && chk5.checked && ma5.length) overlays.push({ name: "MA5", data: ma5 });
    if (chk10 && chk10.checked && ma10.length) overlays.push({ name: "MA10", data: ma10 });
    if (chk20 && chk20.checked && ma20.length) overlays.push({ name: "MA20", data: ma20 });

    mainChart = charts.candlestick(el, allBars, { marks: marks, overlay: overlays, sub: "volume" });
    bindMaToggles();
    bindZoomReset();
  }

  function buildMA(period) {
    var result = [];
    for (var i = 0; i < allBars.length; i++) {
      if (i < period - 1) { result.push(null); continue; }
      var sum = 0;
      for (var j = i - period + 1; j <= i; j++) sum += allBars[j].close;
      result.push(+(sum / period).toFixed(2));
    }
    return result;
  }

  function bindMaToggles() {
    ["ma5-overlay", "ma10-overlay", "ma20-overlay"].forEach(function (id) {
      var el = document.getElementById(id);
      if (el && !el._bound) {
        el._bound = true;
        el.addEventListener("change", function () { renderMainChart(); });
      }
    });
  }

  function bindZoomReset() {
    var btn = document.getElementById("reset-zoom");
    if (btn && !btn._bound) {
      btn._bound = true;
      btn.addEventListener("click", function () {
        if (mainChart) {
          mainChart.dispatchAction({ type: "dataZoom", start: 70, end: 100 });
        }
      });
    }
  }

  function renderSubChart(kind) {
    var el = document.getElementById("sub-chart");
    if (!el) return;
    if (subChartInst) { subChartInst.dispose(); subChartInst = null; }
    if (!allBars.length) return;

    var data;
    if (kind === "volume") {
      data = allBars.map(function (b) {
        return { ts: b.ts, value: b.volume || 0, up: b.close >= b.open };
      });
    } else {
      data = computeIndicator(kind);
    }
    if (data && data.length) {
      subChartInst = charts.subChart(el, kind, data);
    }
  }

  function computeIndicator(kind) {
    var closes = allBars.map(function (b) { return b.close; });
    if (kind === "rsi") {
      return computeRSI(closes, 14);
    } else if (kind === "macd") {
      return computeMACD(closes);
    } else if (kind === "kdj") {
      return computeKDJ();
    } else if (kind === "boll") {
      return computeBOLL(closes, 20);
    }
    return [];
  }

  function computeRSI(closes, period) {
    var result = [];
    var gains = 0, losses = 0;
    for (var i = 1; i < closes.length; i++) {
      var diff = closes[i] - closes[i - 1];
      if (i <= period) {
        if (diff > 0) gains += diff; else losses -= diff;
        if (i === period) {
          gains /= period; losses /= period;
          var rs = losses === 0 ? 100 : gains / losses;
          result.push({ ts: allBars[i].ts, value: +(100 - 100 / (1 + rs)).toFixed(2) });
        }
      } else {
        var g = diff > 0 ? diff : 0;
        var l = diff < 0 ? -diff : 0;
        gains = (gains * (period - 1) + g) / period;
        losses = (losses * (period - 1) + l) / period;
        rs = losses === 0 ? 100 : gains / losses;
        result.push({ ts: allBars[i].ts, value: +(100 - 100 / (1 + rs)).toFixed(2) });
      }
    }
    return result;
  }

  function computeMACD(closes) {
    var ema12 = [closes[0]], ema26 = [closes[0]];
    for (var i = 1; i < closes.length; i++) {
      ema12.push(closes[i] * (2 / 13) + ema12[i - 1] * (1 - 2 / 13));
      ema26.push(closes[i] * (2 / 27) + ema26[i - 1] * (1 - 2 / 27));
    }
    var dif = [], dea = [];
    for (var j = 0; j < closes.length; j++) {
      dif.push(ema12[j] - ema26[j]);
    }
    dea.push(dif[0]);
    for (var k = 1; k < dif.length; k++) {
      dea.push(dif[k] * (2 / 10) + dea[k - 1] * (1 - 2 / 10));
    }
    var result = [];
    for (var m = 0; m < closes.length; m++) {
      result.push({ ts: allBars[m].ts, dif: +dif[m].toFixed(4), dea: +dea[m].toFixed(4), hist: +((dif[m] - dea[m]) * 2).toFixed(4) });
    }
    return result;
  }

  function computeKDJ() {
    var period = 9;
    var result = [];
    var prevK = 50, prevD = 50;
    for (var i = period - 1; i < allBars.length; i++) {
      var high = -Infinity, low = Infinity;
      for (var j = i - period + 1; j <= i; j++) {
        if (allBars[j].high > high) high = allBars[j].high;
        if (allBars[j].low < low) low = allBars[j].low;
      }
      var close = allBars[i].close;
      var rsv = high === low ? 50 : (close - low) / (high - low) * 100;
      var k = 2 / 3 * prevK + 1 / 3 * rsv;
      var d = 2 / 3 * prevD + 1 / 3 * k;
      var jv = 3 * k - 2 * d;
      prevK = k; prevD = d;
      result.push({ ts: allBars[i].ts, k: +k.toFixed(2), d: +d.toFixed(2), j: +jv.toFixed(2) });
    }
    return result;
  }

  function computeBOLL(closes, period) {
    var result = [];
    for (var i = period - 1; i < closes.length; i++) {
      var sum = 0;
      for (var j = i - period + 1; j <= i; j++) sum += closes[j];
      var mid = sum / period;
      var sq = 0;
      for (var k = i - period + 1; k <= i; k++) sq += (closes[k] - mid) * (closes[k] - mid);
      var std = Math.sqrt(sq / period);
      result.push({ ts: allBars[i].ts, up: +(mid + 2 * std).toFixed(2), mid: +mid.toFixed(2), down: +(mid - 2 * std).toFixed(2) });
    }
    return result;
  }

  function renderEquity() {
    var el = document.getElementById("equity-chart");
    if (!el || !currentBt || !currentBt.equity || !currentBt.equity.length) return;
    if (equityChartInst) equityChartInst.dispose();
    var eq = currentBt.equity;
    var m = currentBt.metrics || {};
    var initial = m.initial_cash || eq[0].equity;
    var series = [{
      name: currentBt.name || currentCode,
      dates: eq.map(function (e) { return e.ts; }),
      data: eq.map(function (e) { return +((e.equity / initial - 1) * 100).toFixed(2); }),
    }];
    if (m.bench_series && m.bench_series.length) {
      series.push({
        name: "基准(沪深300)",
        dates: m.bench_series.map(function (d) { return d.ts; }),
        data: m.bench_series.map(function (d) { return d.pct; }),
      });
    }
    if (m.shanghai_series && m.shanghai_series.length) {
      series.push({
        name: "上证指数",
        dates: m.shanghai_series.map(function (d) { return d.ts; }),
        data: m.shanghai_series.map(function (d) { return d.pct; }),
      });
    }
    if (m.buy_hold_return != null) {
      var dates = eq.map(function (e) { return e.ts; });
      var n = dates.length;
      var bhData = [];
      for (var i = 0; i < n; i++) {
        bhData.push(+(m.buy_hold_return * 100 * i / (n - 1)).toFixed(2));
      }
      series.push({ name: "买入持有", dates: dates, data: bhData });
    }
    equityChartInst = charts.equityChart(el, series);
  }

  function renderTrades() {
    tradesAll = (currentBt && currentBt.trades) ? currentBt.trades : [];
    tradePage = 0;
    renderTradePage();
    renderTradeFooter();
  }

  function renderTradePage() {
    var tbody = document.getElementById("trades-body");
    if (!tbody) return;
    tbody.innerHTML = "";
    var start = tradePage * PAGE_SIZE;
    var end = Math.min(start + PAGE_SIZE, tradesAll.length);
    for (var i = start; i < end; i++) {
      var t = tradesAll[i];
      var tr = document.createElement("tr");
      tr.setAttribute("data-idx", i);
      tr.style.cursor = "pointer";
      var sideText = t.side === "buy" ? '<span class="up">买入</span>' : '<span class="down">卖出</span>';
      var pnlText = "";
      if (t.side === "sell" && t.pnl != null) {
        var cls = t.pnl > 0 ? "up" : t.pnl < 0 ? "down" : "flat";
        pnlText = ' <span class="' + cls + '">' + (t.pnl > 0 ? "+" : "") + fmt.money(t.pnl) + '</span>';
      }
      tr.innerHTML = '<td>' + fmt.date(t.ts) + '</td>' +
        '<td>' + sideText + '</td>' +
        '<td class="num">' + fmt.price(t.price) + '</td>' +
        '<td class="num">' + fmt.shares(t.shares) + '</td>' +
        '<td class="num">' + fmt.money(t.amount) + '</td>' +
        '<td class="num">' + (t.fees ? fmt.money(t.fees.total) : "--") + '</td>' +
        '<td>' + (t.reason || "--") + pnlText + '</td>' +
        '<td><button class="btn btn-sm btn-ghost trace-btn" type="button" data-ts="' + t.ts + '">溯源</button></td>';
      tr.addEventListener("click", function (e) {
        var idx = parseInt(this.getAttribute("data-idx"), 10);
        openTrace(tradesAll[idx]);
      });
      tbody.appendChild(tr);
    }
  }

  function renderTradeFooter() {
    var foot = document.getElementById("trades-foot");
    if (!foot) return;
    foot.innerHTML = "";
    if (!tradesAll.length) return;
    var totalBuy = 0, totalSell = 0, totalFees = 0, totalPnl = 0;
    tradesAll.forEach(function (t) {
      if (t.fees) totalFees += t.fees.total || 0;
      if (t.side === "buy") totalBuy++;
      else { totalSell++; if (t.pnl != null) totalPnl += t.pnl; }
    });
    var tr = document.createElement("tr");
    tr.innerHTML = '<td colspan="4"><b>合计</b>（买 ' + totalBuy + ' 次 / 卖 ' + totalSell + ' 次）</td>' +
      '<td class="num">--</td>' +
      '<td class="num">' + fmt.money(totalFees) + '</td>' +
      '<td colspan="2">' + fmt.money(totalPnl) + ' 元</td>';
    foot.appendChild(tr);
  }

  function openTrace(trade) {
    if (typeof trace !== "undefined" && trace.open) {
      trace.open(runId, currentCode, trade.ts, trade);
    }
  }

  /* 分页 */
  function bindPager() {
    var pager = document.getElementById("trades-pager");
    if (!pager) return;
    pager.addEventListener("click", function (e) {
      var btn = e.target.closest("button");
      if (!btn) return;
      var p = parseInt(btn.getAttribute("data-page"), 10);
      if (isNaN(p)) return;
      tradePage = p;
      renderTradePage();
      bindPager();
    });
  }

  function updatePager() {
    var pager = document.getElementById("trades-pager");
    if (!pager) return;
    var totalPages = Math.ceil(tradesAll.length / PAGE_SIZE);
    if (totalPages <= 1) { pager.innerHTML = ""; return; }
    var html = "";
    for (var i = 0; i < totalPages; i++) {
      html += '<button class="btn btn-sm ' + (i === tradePage ? "btn-primary" : "btn-ghost") +
        '" data-page="' + i + '" type="button">' + (i + 1) + '</button> ';
    }
    pager.innerHTML = html;
  }

  var _origRenderTradePage = renderTradePage;
  renderTradePage = function () {
    _origRenderTradePage();
    updatePager();
  };

  /* CSV 导出 */
  function bindExport() {
    var btn = document.getElementById("export-csv");
    if (!btn) return;
    btn.addEventListener("click", function () {
      api.get("/api/backtests/export?run_id=" + runId + "&code=" + currentCode).then(function (data) {
        var bt = data.backtests && data.backtests[0];
        if (!bt || !bt.trades || !bt.trades.length) { toast.warn("无交易数据可导出"); return; }
        var lines = ["日期,方向,价格,数量,金额,手续费,原因"];
        bt.trades.forEach(function (t) {
          lines.push([
            t.ts, t.side === "buy" ? "买入" : "卖出",
            t.price, t.shares, t.amount,
            t.fees ? t.fees.total : 0,
            t.reason || "",
          ].join(","));
        });
        var bom = "\uFEFF";
        var blob = new Blob([bom + lines.join("\n")], { type: "text/csv;charset=utf-8" });
        var url = URL.createObjectURL(blob);
        var a = document.createElement("a");
        a.href = url;
        a.download = "backtest_" + runId + "_" + currentCode + ".csv";
        a.click();
        URL.revokeObjectURL(url);
        toast.ok("已导出 CSV");
      }).catch(function () {});
    });
  }

  /* 重跑 */
  function bindRerun() {
    var btn = document.getElementById("rerun-btn");
    if (!btn) return;
    btn.addEventListener("click", function () {
      location.href = "/?rerun=" + runId;
    });
  }

  /* 副图标签 */
  function bindSubTabs() {
    var tabs = document.querySelectorAll("#sub-tabs .tab");
    tabs.forEach(function (tab) {
      tab.addEventListener("click", function () {
        tabs.forEach(function (t) { t.classList.remove("active"); });
        tab.classList.add("active");
        renderSubChart(tab.getAttribute("data-sub"));
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    init();
    bindSubTabs();
    bindExport();
    bindRerun();
    bindPager();
  });
})();
