/* ECharts 封装。红涨绿跌（A 股习惯）。B/S 标记、缩放、定位。

   取色一律从 base.css 的令牌里读，不写死十六进制 —— 否则切到深色主题时
   图表还是一副白底红绿，和外面的玻璃外壳对不上。令牌是运行时读的，
   所以换主题后要调 charts.refreshTheme() 让已画好的图重刷一遍。
   下面 FALLBACK 那份只是在 CSS 没加载时的兜底，正常路径用不上。 */
var charts = (function () {
  /* 令牌名 → 兜底值。兜底那份和 base.css 的浅色令牌保持一致，红涨绿跌别改。
     读出来后按 "--panel-sunken" → panelSunken 的规则变成 PAL 的键名。 */
  var TOKENS = {
    "--up": "#d93026",
    "--down": "#0a8f5b",
    "--flat": "#6b7280",
    "--info": "#3b5bdb",
    "--warn": "#a15c07",
    "--text": "#1b1f28",
    "--muted": "#6a7180",
    "--line": "rgba(18, 24, 40, 0.10)",
    "--line-soft": "rgba(18, 24, 40, 0.06)",
    "--panel-solid": "#ffffff",
    "--panel-sunken": "#f4f5f8",
    "--glass-bg-strong": "rgba(255, 255, 255, 0.78)",
    "--glass-border": "rgba(255, 255, 255, 0.70)",
    "--info-bg": "rgba(59, 91, 219, 0.10)",
    "--sans": "sans-serif",
    "--mono": "monospace",
    "--glass-blur": "blur(16px)",
  };

  var PAL = null;     /* 当前主题的调色板，惰性读取，换主题时置空 */
  var _live = [];     /* 已画出来的图：{chart, build}，build() 重新算一份 option */

  function _v(name) {
    var s = getComputedStyle(document.documentElement).getPropertyValue(name);
    return (s || "").trim();
  }

  function _key(token) {
    return token.slice(2).replace(/-([a-z])/g, function (_, c) { return c.toUpperCase(); });
  }

  function _pal() {
    if (PAL) return PAL;
    var p = {};
    Object.keys(TOKENS).forEach(function (t) {
      p[_key(t)] = _v(t) || TOKENS[t];
    });
    PAL = p;
    return p;
  }

  /* 副图/均线用的色环。涨跌色留给 K 线和 B/S 标记专用，别拿去画均线，
     否则满屏红绿会让人误以为均线也在表示涨跌。 */
  function _ramp() {
    var p = _pal();
    return [p.info, p.warn, p.flat, p.up, p.down];
  }

  function _tooltip() {
    var p = _pal();
    return {
      trigger: "axis",
      axisPointer: {
        type: "cross",
        lineStyle: { color: p.muted, opacity: 0.45 },
        crossStyle: { color: p.muted, opacity: 0.45 },
        label: { backgroundColor: p.info, color: "#fff", fontSize: 11 },
      },
      backgroundColor: p.glassBgStrong,
      borderColor: p.glassBorder,
      borderWidth: 1,
      padding: [8, 12],
      textStyle: { color: p.text, fontSize: 12, fontFamily: p.sans },
      /* 画布外的 tooltip 是个 div，所以能真的上毛玻璃 */
      extraCssText: "-webkit-backdrop-filter:" + p.glassBlur + ";backdrop-filter:" + p.glassBlur +
        ";border-radius:12px;box-shadow:0 16px 40px -16px rgba(0,0,0,0.45);",
    };
  }

  function _baseOpts() {
    var p = _pal();
    return {
      animation: false,
      color: _ramp(),
      textStyle: { fontFamily: p.sans, color: p.text },
      grid: { left: 60, right: 20, top: 40, bottom: 40 },
      tooltip: _tooltip(),
    };
  }

  function _axisLabel() {
    var p = _pal();
    return { color: p.muted, fontSize: 11, fontFamily: p.mono };
  }

  function _catAxis(dates, extra) {
    var p = _pal();
    var o = {
      type: "category",
      data: dates,
      boundaryGap: true,
      axisLine: { lineStyle: { color: p.line } },
      axisTick: { lineStyle: { color: p.line } },
      axisLabel: _axisLabel(),
    };
    if (extra) Object.keys(extra).forEach(function (k) { o[k] = extra[k]; });
    return o;
  }

  function _valAxis(extra) {
    var p = _pal();
    var o = {
      type: "value",
      scale: true,
      axisLine: { show: false },
      axisLabel: _axisLabel(),
      splitLine: { lineStyle: { color: p.lineSoft } },
    };
    if (extra) Object.keys(extra).forEach(function (k) { o[k] = extra[k]; });
    return o;
  }

  function _zoom(xAxisIndex, extra) {
    var p = _pal();
    var slider = {
      type: "slider",
      start: 70,
      end: 100,
      bottom: 8,
      height: 18,
      borderColor: "transparent",
      backgroundColor: p.panelSunken,
      fillerColor: p.infoBg,
      handleStyle: { color: p.panelSolid, borderColor: p.muted, borderWidth: 1 },
      moveHandleStyle: { color: p.line },
      emphasis: { handleStyle: { color: p.panelSolid, borderColor: p.info } },
      dataBackground: { lineStyle: { color: p.line }, areaStyle: { color: p.lineSoft } },
      selectedDataBackground: { lineStyle: { color: p.info }, areaStyle: { color: p.infoBg } },
      textStyle: { color: p.muted, fontSize: 11, fontFamily: p.mono },
    };
    var inside = { type: "inside", start: 70, end: 100 };
    /* 单轴图不写 xAxisIndex，交给 ECharts 默认绑第 0 轴 */
    if (xAxisIndex != null) { slider.xAxisIndex = xAxisIndex; inside.xAxisIndex = xAxisIndex; }
    if (extra) Object.keys(extra).forEach(function (k) { slider[k] = extra[k]; });
    return [inside, slider];
  }

  function _draw(el, build) {
    var chart = echarts.init(el);
    chart.setOption(build());
    /* 顺手把已销毁的清掉。反复切股票、切副图会不停 dispose 旧图，
       不清的话这个数组会一直长，把死图和它的数据一起拽在内存里。 */
    _live = _live.filter(function (e) { return !e.chart.isDisposed(); });
    _live.push({ chart: chart, build: build });
    return chart;
  }

  /* 换主题后重刷所有还活着的图。不 dispose 重建 —— 外面存着实例引用
     （mainChart 之类），一 dispose 就成野指针了。缩放位置也保住，
     不然用户刚拖好的窗口会被弹回默认区间。 */
  function refreshTheme() {
    PAL = null;
    _live = _live.filter(function (e) { return !e.chart.isDisposed(); });
    _live.forEach(function (e) {
      var zoom = (e.chart.getOption().dataZoom || []).map(function (z) {
        return { start: z.start, end: z.end };
      });
      var next = e.build();
      if (next.dataZoom) {
        next.dataZoom.forEach(function (dz, i) {
          if (zoom[i]) { dz.start = zoom[i].start; dz.end = zoom[i].end; }
        });
      }
      e.chart.setOption(next, { notMerge: true });
    });
  }

  function candlestick(el, bars, opts) {
    opts = opts || {};
    if (!el || !bars || !bars.length) return null;

    var dates = [], ohlc = [], volumes = [];
    bars.forEach(function (b) {
      dates.push(b.ts || b.date);
      ohlc.push([b.open, b.close, b.low, b.high]);
      volumes.push(b.volume || 0);
    });

    function build() {
      var p = _pal();
      var ramp = _ramp();

      var series = [{
        name: "K线",
        type: "candlestick",
        data: ohlc,
        itemStyle: {
          color: p.up,        /* 涨（收>开） */
          color0: p.down,     /* 跌 */
          borderColor: p.up,
          borderColor0: p.down,
        },
      }];

      if (opts.marks) {
        var buyPts = [], sellPts = [];
        opts.marks.forEach(function (m) {
          var idx = dates.indexOf(m.ts);
          if (idx < 0) return;
          if (m.side === "buy") {
            buyPts.push({ xAxis: idx, yAxis: m.price });
          } else {
            sellPts.push({ xAxis: idx, yAxis: m.price });
          }
        });
        if (buyPts.length) {
          series[0].markPoint = { data: buyPts.map(function (q) {
            return { coord: [q.xAxis, q.yAxis], value: "B▲", symbol: "triangle",
              symbolSize: 14, itemStyle: { color: p.up }, label: { show: false } };
          }) };
        }
        if (sellPts.length) {
          series[0].markPoint = series[0].markPoint || { data: [] };
          series[0].markPoint.data = series[0].markPoint.data.concat(
            sellPts.map(function (q) {
              return { coord: [q.xAxis, q.yAxis], value: "S▼", symbol: "triangle",
                symbolSize: 14, symbolRotate: 180, itemStyle: { color: p.down },
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
            lineStyle: { width: 1.3, color: ramp[oi % ramp.length] },
            itemStyle: { color: ramp[oi % ramp.length] },
            symbol: "none",
          });
        });
      }

      var options = _baseOpts();
      options.xAxis = [_catAxis(dates)];
      options.yAxis = [_valAxis()];
      options.series = series;
      options.dataZoom = _zoom(0);
      options.legend = series.length > 1
        ? { top: 6, textStyle: { fontSize: 12, color: p.muted }, inactiveColor: p.line, itemWidth: 14 }
        : undefined;

      if (opts.sub === "volume") {
        options.grid = [
          { left: 60, right: 20, top: 40, height: "55%" },
          { left: 60, right: 20, top: "74%", height: "16%" },
        ];
        options.xAxis.push(_catAxis(dates, { gridIndex: 1, axisLabel: { show: false } }));
        options.yAxis.push(_valAxis({ gridIndex: 1, splitLine: { show: false }, axisLabel: { show: false } }));
        series.push({
          name: "成交量",
          type: "bar",
          xAxisIndex: 1,
          yAxisIndex: 1,
          data: volumes.map(function (v, i) {
            var isUp = ohlc[i][1] >= ohlc[i][0];
            return { value: v, itemStyle: { color: isUp ? p.up : p.down, opacity: 0.75 } };
          }),
        });
        options.dataZoom.forEach(function (dz) { dz.xAxisIndex = [0, 1]; });
      }

      options.tooltip.formatter = function (params) {
        if (!params || !params.length) return "";
        var idx = params[0].dataIndex;
        var b = bars[idx];
        var chg = b.pct_chg != null ? b.pct_chg : (b.open ? ((b.close - b.open) / b.open * 100) : 0);
        var color = chg > 0 ? p.up : chg < 0 ? p.down : p.flat;
        return '<div style="font-size:12px;line-height:1.7">' +
          '<b>' + dates[idx] + '</b><br/>' +
          '开 ' + b.open.toFixed(2) + ' 高 ' + b.high.toFixed(2) +
          ' 低 ' + b.low.toFixed(2) + ' 收 ' + b.close.toFixed(2) + '<br/>' +
          '<span style="color:' + color + '">涨跌幅 ' + (chg > 0 ? '+' : '') + chg.toFixed(2) + '%</span><br/>' +
          '成交量 ' + (b.volume || 0).toLocaleString() +
          '</div>';
      };

      return options;
    }

    return _draw(el, build);
  }

  function equityChart(el, series) {
    if (!el || !series || !series.length) return null;

    function build() {
      var p = _pal();
      var ramp = _ramp();
      var options = _baseOpts();
      options.legend = {
        top: 4,
        textStyle: { fontSize: 12, color: p.muted },
        inactiveColor: p.line,
        itemWidth: 14,
      };
      options.grid = { left: 60, right: 20, top: 40, bottom: 44 };
      options.xAxis = _catAxis(series[0].dates || [], { boundaryGap: false });
      options.yAxis = _valAxis();
      options.series = series.map(function (s, i) {
        var c = ramp[i % ramp.length];
        return {
          name: s.name || ("策略" + (i + 1)),
          type: "line",
          data: s.data,
          smooth: true,
          symbol: "none",
          lineStyle: { width: 1.6, color: c },
          itemStyle: { color: c },
          /* 头一条（自己的策略）铺一层极淡的面积，突出主角 */
          areaStyle: i === 0 ? { color: c, opacity: 0.07 } : undefined,
        };
      });
      options.dataZoom = _zoom(null, { height: 16 });
      return options;
    }

    return _draw(el, build);
  }

  function subChart(el, kind, data) {
    if (!el || !data || !data.length) return null;

    function build() {
      var p = _pal();
      var options = _baseOpts();
      options.grid = { left: 50, right: 20, top: 20, bottom: 30 };
      options.xAxis = _catAxis(data.map(function (d) { return d.ts; }), { axisLabel: { show: false } });
      options.yAxis = _valAxis();

      if (kind === "volume") {
        options.series = [{
          type: "bar",
          data: data.map(function (d) {
            return { value: d.value, itemStyle: { color: d.up ? p.up : p.down, opacity: 0.8 } };
          }),
        }];
      } else if (kind === "macd") {
        options.legend = { top: 0, right: 0, textStyle: { fontSize: 11, color: p.muted }, itemWidth: 12 };
        options.grid.top = 26;
        options.series = [
          { name: "DIF", type: "line", data: data.map(function (d) { return d.dif; }),
            symbol: "none", lineStyle: { width: 1.2, color: p.info } },
          { name: "DEA", type: "line", data: data.map(function (d) { return d.dea; }),
            symbol: "none", lineStyle: { width: 1.2, color: p.warn } },
          { name: "MACD", type: "bar", data: data.map(function (d) {
            return { value: d.hist, itemStyle: { color: d.hist >= 0 ? p.up : p.down, opacity: 0.8 } };
          }) },
        ];
      } else if (kind === "kdj") {
        var lines = [p.info, p.warn, p.flat];
        options.legend = { top: 0, right: 0, textStyle: { fontSize: 11, color: p.muted }, itemWidth: 12 };
        options.grid.top = 26;
        options.series = ["K", "D", "J"].map(function (n, i) {
          return { name: n, type: "line", data: data.map(function (d) { return d[n.toLowerCase()]; }),
            symbol: "none", lineStyle: { width: 1.2, color: lines[i] }, itemStyle: { color: lines[i] } };
        });
      } else if (kind === "boll") {
        options.legend = { top: 0, right: 0, textStyle: { fontSize: 11, color: p.muted }, itemWidth: 12 };
        options.grid.top = 26;
        options.series = [
          { name: "上轨", type: "line", data: data.map(function (d) { return d.up; }),
            symbol: "none", lineStyle: { width: 1, color: p.up } },
          { name: "中轨", type: "line", data: data.map(function (d) { return d.mid; }),
            symbol: "none", lineStyle: { width: 1, color: p.info } },
          { name: "下轨", type: "line", data: data.map(function (d) { return d.down; }),
            symbol: "none", lineStyle: { width: 1, color: p.down } },
        ];
      } else {
        options.series = [{ type: "line", data: data.map(function (d) { return d.value; }),
          symbol: "none", lineStyle: { width: 1.5, color: p.info }, itemStyle: { color: p.info } }];
      }
      return options;
    }

    return _draw(el, build);
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
    refreshTheme: refreshTheme,
    colors: _pal,
  };
})();
