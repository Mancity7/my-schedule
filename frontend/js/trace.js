/* 信号溯源：点某笔交易，弹出浮层显示当天每个条件的满足情况。 */
var trace = (function () {
  var panel = null;

  function open(runId, code, ts, trade) {
    close();
    panel = document.createElement("div");
    panel.className = "trace-mask";
    panel.addEventListener("click", function (e) {
      if (e.target === panel) close();
    });

    var inner = document.createElement("div");
    inner.className = "trace-panel";
    inner.setAttribute("role", "dialog");
    inner.setAttribute("aria-label", "信号溯源");

    var header = document.createElement("div");
    header.className = "trace-header";
    header.innerHTML = '<h3>信号溯源 · ' + code + ' · ' + ts + '</h3>' +
      '<button class="btn btn-sm btn-ghost trace-close" type="button" aria-label="关闭">&times;</button>';
    inner.appendChild(header);

    var body = document.createElement("div");
    body.className = "trace-body";
    body.innerHTML = '<div class="muted">加载中...</div>';
    inner.appendChild(body);

    panel.appendChild(inner);
    document.body.appendChild(panel);

    header.querySelector(".trace-close").addEventListener("click", close);
    document.addEventListener("keydown", _escHandler);

    api.get("/api/backtests/trace?run_id=" + runId + "&code=" + encodeURIComponent(code) + "&ts=" + encodeURIComponent(ts))
      .then(function (data) {
        renderBody(body, data, trade);
      })
      .catch(function () {
        body.innerHTML = '<div class="muted">加载失败</div>';
      });
  }

  function _escHandler(e) {
    if (e.key === "Escape") close();
  }

  function close() {
    document.removeEventListener("keydown", _escHandler);
    if (panel) { panel.remove(); panel = null; }
  }

  function renderBody(body, data, trade) {
    var signal = data.signal;
    if (!signal) {
      body.innerHTML = '<div class="muted">该日期无信号记录（可能是非交易日或数据缺失）</div>';
      return;
    }

    var html = '<table class="tbl"><thead><tr><th>项目</th><th>值</th></tr></thead><tbody>';
    html += '<tr><td>日期</td><td>' + (signal.ts || "--") + '</td></tr>';
    html += '<tr><td>买入信号</td><td>' + (signal.buy ? '<span class="up">是</span>' : '否') + '</td></tr>';
    html += '<tr><td>卖出信号</td><td>' + (signal.sell ? '<span class="down">是</span>' : '否') + '</td></tr>';
    html += '<tr><td>资金不足</td><td>' + (signal.insufficient ? '是' : '否') + '</td></tr>';

    if (signal.skip_reason) {
      html += '<tr><td>跳过原因</td><td>' + signal.skip_reason + '</td></tr>';
    }
    if (signal.action) {
      html += '<tr><td>实际动作</td><td>' + signal.action + '</td></tr>';
    }
    html += '</tbody></table>';

    if (trade) {
      html += '<h4 style="margin-top:16px">交易详情</h4>';
      html += '<table class="tbl"><thead><tr><th>项目</th><th>值</th></tr></thead><tbody>';
      html += '<tr><td>方向</td><td>' + (trade.side === "buy" ? "买入" : "卖出") + '</td></tr>';
      html += '<tr><td>成交价</td><td>' + fmt.price(trade.price) + '</td></tr>';
      html += '<tr><td>数量</td><td>' + fmt.shares(trade.shares) + '</td></tr>';
      html += '<tr><td>金额</td><td>' + fmt.money(trade.amount) + ' 元</td></tr>';
      if (trade.fees) {
        html += '<tr><td>佣金</td><td>' + fmt.money(trade.fees.commission) + ' 元</td></tr>';
        html += '<tr><td>印花税</td><td>' + fmt.money(trade.fees.stamp) + ' 元</td></tr>';
        html += '<tr><td>过户费</td><td>' + fmt.money(trade.fees.transfer) + ' 元</td></tr>';
        html += '<tr><td>手续费合计</td><td>' + fmt.money(trade.fees.total) + ' 元</td></tr>';
      }
      if (trade.side === "sell" && trade.pnl != null) {
        var cls = trade.pnl > 0 ? "up" : trade.pnl < 0 ? "down" : "flat";
        html += '<tr><td>盈亏</td><td><span class="' + cls + '">' +
          (trade.pnl > 0 ? "+" : "") + fmt.money(trade.pnl) + ' 元</span></td></tr>';
      }
      if (trade.reason) {
        html += '<tr><td>触发原因</td><td>' + trade.reason + '</td></tr>';
      }
      html += '</tbody></table>';
    }

    body.innerHTML = html;
  }

  return { open: open, close: close };
})();
