"""阶段 7 自检：回测运行页 + 信号溯源 + 横向对比。

验收要点：
- backtest.html 结构完整（标签、指标卡、图表、明细表、免责声明）
- backtest.js / trace.js / compare.js 存在且含核心逻辑
- K 线数据接口已暴露（stocks.py 含 /kline 路由）
- 无 CDN 引用
- 溯源支持 Esc 关闭
- 对比表支持排序
"""

import os
from pathlib import Path

from .harness import check, true

FRONTEND = Path("/app/frontend")
JS = FRONTEND / "js"


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


@check("7")
def s7_backtest_html_exists():
    """backtest.html 存在且包含核心容器。"""
    html = FRONTEND / "backtest.html"
    true(html.exists(), "backtest.html 应存在")
    content = _read(html)
    for tag in ["stock-tabs", "metric-grid", "main-chart", "sub-chart",
                "equity-chart", "trades-table", "trades-body", "trades-pager"]:
        true(tag in content, f"backtest.html 应包含 #{tag} 容器")


@check("7")
def s7_backtest_html_disclaimer():
    """回测页底部有免责声明。"""
    content = _read(FRONTEND / "backtest.html")
    true("基于历史数据模拟" in content, "backtest.html 应有免责声明")
    true("不构成投资建议" in content, "backtest.html 免责声明应含「不构成投资建议」")


@check("7")
def s7_backtest_js_exists():
    """backtest.js 存在且包含核心逻辑。"""
    js = JS / "backtest.js"
    true(js.exists(), "backtest.js 应存在")
    content = _read(js)
    for kw in ["renderStockTabs", "renderMetrics", "renderMainChart",
               "renderSubChart", "renderEquity", "renderTrades", "selectStock"]:
        true(kw in content, f"backtest.js 应包含 {kw} 函数")


@check("7")
def s7_backtest_js_fetches_kline():
    """backtest.js 从 /kline 接口获取 K 线数据。"""
    content = _read(JS / "backtest.js")
    true("/kline" in content, "backtest.js 应调用 /kline 接口获取 K 线数据")


@check("7")
def s7_backtest_js_csv_export():
    """backtest.js 有 CSV 导出逻辑。"""
    content = _read(JS / "backtest.js")
    true("export-csv" in content or "export" in content.lower(),
         "backtest.js 应有 CSV 导出功能")
    true(".csv" in content, "backtest.js 导出文件名应含 .csv")


@check("7")
def s7_backtest_js_pagination():
    """backtest.js 交易明细有分页逻辑。"""
    content = _read(JS / "backtest.js")
    true("PAGE_SIZE" in content or "pageSize" in content,
         "backtest.js 应有分页常量")
    true("tradePage" in content, "backtest.js 应有当前页码变量")


@check("7")
def s7_trace_js_exists():
    """trace.js 存在且有 open/close。"""
    js = JS / "trace.js"
    true(js.exists(), "trace.js 应存在")
    content = _read(js)
    true("function open" in content or "open:" in content,
         "trace.js 应有 open 方法")
    true("function close" in content or "close:" in content,
         "trace.js 应有 close 方法")


@check("7")
def s7_trace_esc_close():
    """trace.js 支持 Esc 关闭。"""
    content = _read(JS / "trace.js")
    true("Escape" in content, "trace.js 应监听 Escape 键关闭")


@check("7")
def s7_trace_calls_api():
    """trace.js 调用 /api/backtests/trace 接口。"""
    content = _read(JS / "trace.js")
    true("/api/backtests/trace" in content or "/backtests/trace" in content,
         "trace.js 应调用溯源接口")


@check("7")
def s7_compare_js_exists():
    """compare.js 存在且有 open/close。"""
    js = JS / "compare.js"
    true(js.exists(), "compare.js 应存在")
    content = _read(js)
    true("function open" in content or "open:" in content,
         "compare.js 应有 open 方法")
    true("function close" in content or "close:" in content,
         "compare.js 应有 close 方法")


@check("7")
def s7_compare_sort():
    """compare.js 表格支持排序。"""
    content = _read(JS / "compare.js")
    true("sort" in content, "compare.js 应有排序逻辑")
    true("data-sort" in content, "compare.js 排序应标记 data-sort 属性")


@check("7")
def s7_compare_calls_api():
    """compare.js 调用 /api/backtests/compare 接口。"""
    content = _read(JS / "compare.js")
    true("/api/backtests/compare" in content or "/backtests/compare" in content,
         "compare.js 应调用对比接口")


@check("7")
def s7_kline_endpoint_exists():
    """stocks.py 暴露 /kline 路由。"""
    stocks_py = Path("/app/backend/app/api/stocks.py")
    if not stocks_py.exists():
        stocks_py = Path(os.environ.get("DATA_DIR", "/app/data")).parent / "backend" / "app" / "api" / "stocks.py"
    if not stocks_py.exists():
        stocks_py = Path("/app/app/api/stocks.py")
    content = _read(stocks_py)
    true("/kline" in content, "stocks.py 应有 /kline 路由")
    true("get_kline" in content, "stocks.py /kline 路由应调用 store.get_kline")


@check("7")
def s7_no_cdn_in_new_files():
    """新增文件不含 CDN 引用。"""
    for name in ["backtest.html", "js/backtest.js", "js/trace.js", "js/compare.js"]:
        content = _read(FRONTEND / name)
        true("cdn" not in content.lower() or "cdn.jsdelivr" not in content.lower(),
             f"{name} 不应含 CDN 引用")


@check("7")
def s7_backtest_html_no_cdn():
    """backtest.html 只引用本地 JS。"""
    content = _read(FRONTEND / "backtest.html")
    true("cdnjs" not in content and "jsdelivr" not in content and "unpkg" not in content,
         "backtest.html 不应引用外部 CDN")
    for script in ["/js/api.js", "/js/format.js", "/js/charts.js", "/js/backtest.js",
                   "/js/trace.js", "/js/compare.js"]:
        true(script in content, f"backtest.html 应引用 {script}")


@check("7")
def s7_backtest_html_ma_overlays():
    """backtest.html 有 MA 均线叠加复选框。"""
    content = _read(FRONTEND / "backtest.html")
    true("ma5-overlay" in content, "backtest.html 应有 MA5 复选框")
    true("ma10-overlay" in content, "backtest.html 应有 MA10 复选框")
    true("ma20-overlay" in content, "backtest.html 应有 MA20 复选框")


@check("7")
def s7_backtest_html_sub_tabs():
    """backtest.html 有副图标签（成交量/MACD/RSI/KDJ/BOLL）。"""
    content = _read(FRONTEND / "backtest.html")
    for sub in ["volume", "macd", "rsi", "kdj", "boll"]:
        true(f'data-sub="{sub}"' in content, f"backtest.html 应有 {sub} 副图标签")


@check("7")
def s7_backtest_html_quality_bar():
    """backtest.html 有数据质量条。"""
    content = _read(FRONTEND / "backtest.html")
    true("quality-bar" in content, "backtest.html 应有数据质量条")


@check("7")
def s7_backtest_html_breadcrumb():
    """backtest.html 有面包屑导航。"""
    content = _read(FRONTEND / "backtest.html")
    true("breadcrumb" in content, "backtest.html 应有面包屑导航")
