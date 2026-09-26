/* ECharts 封装。红涨绿跌（A 股习惯）。B/S 标记、缩放、定位。 */
var charts = (function () {
  var UP = "#d93026";
  var DOWN = "#0a8f5b";
  var FLAT = "#6b7280";

  function _baseOpts() {
    return {
      animation: false,
      color: [UP, DOWN, FLAT, "#2563eb", "#b45309"],
      grid: { left: 60, right: 20, top: 40, bottom: 40 },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "cross" },
        backgroundColor: "rgba(255,255,255,0.96)",
        borderColor: "#e3e6ea",
        textStyle: { color: "#1f2430", fontSize: 12 },
      },
    };
  }

  function candlestick(el, bars, opts) {
    opts = opts || {};
    if (!el || !bars || !bars.length) return null;
    var chart = echarts.init(el);
    var dates = [], ohlc = [], volumes = [];

    bars.forEach(function (b) {
      dates.push(b.ts || b.date);
      ohlc.push([b.open, b.close, b.low, b.high]);
      volumes.push(b.volume || 0);
    });

    var series = [{
      name: "K线",
      type: "candlestick",
      data: ohlc,
      itemStyle: {
        color: UP,        /* 涨（收>开） */
        color0: DOWN,     /* 跌 */
        borderColor: UP,
        borderColor0: DOWN,
      },
    }];

    if (opts.marks) {
      var buyPts = [], sellPts = [];
      opts.marks.forEach(function (m) {
        var idx = dates.indexOf(m.ts);
        if (idx < 0) return;
        if (m.side === "buy") {
          buyPts.push({ name: "B", value: m.price, xAxis: idx, yAxis: m.price });
        } else {
          sellPts.push({ name: "S", value: m.price, xAxis: idx, yAxis: m.price });
        }
      });
      if (buyPts.length) {
        series[0].markPoint = series[0].markPoint || { data: [] };
        series[0].markPoint.data = series[0].markPoint.data.concat(
          buyPts.map(function (p) {
            return { coord: [p.xAxis, p.yAxis], value: "B▲", symbol: "triangle",
              symbolSize: 14, itemStyle: { color: UP }, label: { show: false } };
          })
        );
      }
      if (sellPts.length) {
        series[0].markPoint = series[0].markPoint || { data: [] };
        series[0].markPoint.data = series[0].markPoint.data.concat(
          sellPts.map(function (p) {
            return { coord: [p.xAxis, p.yAxis], value: "S▼", symbol: "triangle",
              symbolSize: 14, symbolRotate: 180, itemStyle: { color: DOWN },
              label: { show: false } };
          })
        );
      }
    }

    if (opts.overlay) {
      opts.overlay.forEach(function (ov, oi) {
        series.push({
          name: ov.name || ("MA" + (oi + 1)),
          type: "line",
          data: ov.data,
          smooth: true,
          lineStyle: { width: 1.2 },
          symbol: "none",
        });
      });
    }

    var options = _baseOpts();
    options.xAxis = [{ type: "category", data: dates, boundaryGap: true, axisLine: { lineStyle: { color: "#e3e6ea" } } }];
    options.yAxis = [{ type: "value", scale: true, splitLine: { lineStyle: { color: "#f0f0f0" } } }];
    options.series = series;
    options.dataZoom = [
      { type: "inside", xAxisIndex: 0, start: 70, end: 100 },
      { type: "slider", xAxisIndex: 0, start: 70, end: 100, bottom: 8 },
    ];

    if (opts.sub === "volume") {
      options.grid = [
        { left: 60, right: 20, top: 40, height: "55%" },
        { left: 60, right: 20, top: "74%", height: "16%" },
      ];
      options.xAxis.push({ type: "category", data: dates, gridIndex: 1, boundaryGap: true, axisLabel: { show: false } });
      options.yAxis.push({ type: "value", gridIndex: 1, splitLine: { show: false }, axisLabel: { show: false } });
      series.push({
        name: "成交量",
        type: "bar",
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: volumes.map(function (v, i) {
          var isUp = ohlc[i][1] >= ohlc[i][0];
          return { value: v, itemStyle: { color: isUp ? UP : DOWN } };
        }),
      });
      options.dataZoom.forEach(function (dz) { dz.xAxisIndex = [0, 1]; });
    }

    options.tooltip.formatter = function (params) {
      if (!params || !params.length) return "";
      var idx = params[0].dataIndex;
      var b = bars[idx];
      var chg = b.pct_chg != null ? b.pct_chg : (b.open ? ((b.close - b.open) / b.open * 100) : 0);
      var cls = chg > 0 ? "up" : chg < 0 ? "down" : "";
      var color = chg > 0 ? UP : chg < 0 ? DOWN : FLAT;
      return '<div style="font-size:12px">' +
        '<b>' + dates[idx] + '</b><br/>' +
        '开 ' + b.open.toFixed(2) + ' 高 ' + b.high.toFixed(2) +
        ' 低 ' + b.low.toFixed(2) + ' 收 ' + b.close.toFixed(2) + '<br/>' +
        '<span style="color:' + color + '">涨跌幅 ' + (chg > 0 ? '+' : '') + chg.toFixed(2) + '%</span><br/>' +
        '成交量 ' + (b.volume || 0).toLocaleString() +
        '</div>';
    };

    chart.setOption(options);
    return chart;
  }

  function equityChart(el, series) {
    if (!el || !series || !series.length) return null;
    var chart = echarts.init(el);
    var options = _baseOpts();
    options.legend = { top: 4, textStyle: { fontSize: 12 } };
    options.xAxis = { type: "category", data: series[0].dates || [] };
    options.yAxis = { type: "value", scale: true, splitLine: { lineStyle: { color: "#f0f0f0" } } };
    options.series = series.map(function (s, i) {
      return {
        name: s.name || ("策略" + (i + 1)),
        type: "line",
        data: s.data,
        smooth: true,
        symbol: "none",
        lineStyle: { width: 1.5 },
        areaStyle: i === 0 ? { opacity: 0.05 } : undefined,
      };
    });
    options.dataZoom = [{ type: "inside" }, { type: "slider", height: 16, bottom: 8 }];
    chart.setOption(options);
    return chart;
  }

  function subChart(el, kind, data) {
    if (!el || !data || !data.length) return null;
    var chart = echarts.init(el);
    var options = _baseOpts();
    options.grid = { left: 50, right: 20, top: 20, bottom: 30 };
    options.xAxis = { type: "category", data: data.map(function (d) { return d.ts; }), axisLabel: { show: false } };
    options.yAxis = { type: "value", scale: true, splitLine: { lineStyle: { color: "#f0f0f0" } } };

    if (kind === "volume") {
      options.series = [{
        type: "bar",
        data: data.map(function (d) {
          return { value: d.value, itemStyle: { color: d.up ? UP : DOWN } };
        }),
      }];
    } else {
      var colors = kind === "macd" ? { pos: UP, neg: DOWN, line: "#2563eb" }
        : kind === "rsi" ? { line: "#b45309" }
        : kind === "kdj" ? { lines: ["#2563eb", "#b45309", "#6b7280"] }
        : { line: "#2563eb" };

      if (kind === "macd") {
        options.series = [
          { name: "DIF", type: "line", data: data.map(function (d) { return d.dif; }), symbol: "none", lineStyle: { width: 1.2, color: colors.line } },
          { name: "DEA", type: "line", data: data.map(function (d) { return d.dea; }), symbol: "none", lineStyle: { width: 1.2, color: colors.neg } },
          { name: "MACD", type: "bar", data: data.map(function (d) { return { value: d.hist, itemStyle: { color: d.hist >= 0 ? colors.pos : colors.neg } }; }) },
        ];
      } else if (kind === "kdj") {
        options.series = ["K", "D", "J"].map(function (n, i) {
          return { name: n, type: "line", data: data.map(function (d) { return d[n.toLowerCase()]; }),
            symbol: "none", lineStyle: { width: 1.2, color: colors.lines[i] } };
        });
      } else if (kind === "boll") {
        options.series = [
          { name: "上轨", type: "line", data: data.map(function (d) { return d.up; }), symbol: "none", lineStyle: { width: 1, color: UP } },
          { name: "中轨", type: "line", data: data.map(function (d) { return d.mid; }), symbol: "none", lineStyle: { width: 1, color: "#2563eb" } },
          { name: "下轨", type: "line", data: data.map(function (d) { return d.down; }), symbol: "none", lineStyle: { width: 1, color: DOWN } },
        ];
      } else {
        options.series = [{ type: "line", data: data.map(function (d) { return d.value; }),
          symbol: "none", lineStyle: { width: 1.5, color: colors.line || colors.pos || UP } }];
      }
    }
    chart.setOption(options);
    return chart;
  }

  function locate(chart, ts) {
    if (!chart || !ts) return;
    var opt = chart.getOption();
    var xAxis = opt.xAxis && opt.xAxis[0];
    if (!xAxis || !xAxis.data) return;
    var idx = xAxis.data.indexOf(ts);
    if (idx < 0) return;
    chart.dispatchAction({ type: "showTip", seriesIndex: 0, dataIndex: idx });
    chart.dispatchAction({ type: "dataZoom", startValue: xAxis.data[Math.max(0, idx - 10)], endValue: xAxis.data[Math.min(xAxis.data.length - 1, idx + 10)] });
  }

  return {
    candlestick: candlestick,
    equityChart: equityChart,
    subChart: subChart,
    locate: locate,
    UP: UP,
    DOWN: DOWN,
  };
})();
