/* 数字 / 日期格式化。涨跌色永远和 +/- 一起出现，不只靠颜色（I13）。 */
var fmt = (function () {

  function money(n) {
    if (n == null || isNaN(n)) return "--";
    var neg = n < 0;
    var abs = Math.abs(n).toFixed(2);
    var parts = abs.split(".");
    parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    return (neg ? "-" : "") + parts.join(".");
  }

  function money_cn(n) {
    if (n == null || isNaN(n)) return "--";
    var abs = Math.abs(n);
    var sign = n < 0 ? "-" : "";
    if (abs >= 1e8) return sign + (abs / 1e8).toFixed(2) + "亿";
    if (abs >= 1e4) return sign + (abs / 1e4).toFixed(2) + "万";
    return money(n);
  }

  function pct(n) {
    if (n == null || isNaN(n)) return '<span class="flat">--</span>';
    var v = (n * 100).toFixed(2);
    var cls = n > 0 ? "up" : n < 0 ? "down" : "flat";
    var prefix = n > 0 ? "+" : "";
    return '<span class="' + cls + '">' + prefix + v + "%</span>";
  }

  function price(n) {
    if (n == null || isNaN(n)) return "--";
    return n.toFixed(n >= 100 ? 2 : 3);
  }

  function shares(n) {
    if (n == null || isNaN(n)) return "--";
    return n.toLocaleString("zh-CN") + " 股";
  }

  function date(ts) {
    if (!ts) return "--";
    var d = typeof ts === "string" ? ts.slice(0, 10) : new Date(ts).toISOString().slice(0, 10);
    return d;
  }

  function num_cn_class(n) {
    if (n == null || isNaN(n)) return '<span class="flat">--</span>';
    var cls = n > 0 ? "up" : n < 0 ? "down" : "flat";
    var prefix = n > 0 ? "+" : "";
    return '<span class="' + cls + '">' + prefix + n + "</span>";
  }

  return {
    money: money,
    money_cn: money_cn,
    pct: pct,
    price: price,
    shares: shares,
    date: date,
    num_cn_class: num_cn_class,
  };
})();
