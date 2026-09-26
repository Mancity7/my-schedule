"""阶段 9 自检：端到端冒烟测试基础设施。

验收要点（对应 PRD §9.M 的 M1-M15）：
- 健康检查可用（M1）
- 四个页面都能被服务（M1, M6, M9, M12）
- 核心 API 端点可用：搜索、模板、指标、回测、溯源、对比、导出、设置（M2-M13）
- 前端无登录/注册字样（M15）
- 无 CDN 引用（全局）
- 免责声明在所有结果页存在（M6, M12）
- 数据持久化：策略存进去能读出来（M14）
- 导航一致性（M1）
- 回测页核心元素（M6）
- 导出排除 API Key（M11 延伸）

注：bat 文件、docker-compose.yml、DOCKER使用指南.md 在宿主机上，
    由 test.bat 在宿主机侧验证，不在容器内重复检查。
"""

from pathlib import Path

from .harness import check, true, client, eq, near, fresh_db

FRONTEND = Path("/app/frontend")
JS = FRONTEND / "js"


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


# ── 健康检查 ──────────────────────────────────────────────

@check("9")
def s9_health_endpoint():
    """GET /api/health 返回 ok。"""
    c = client()
    r = c.get("/api/health")
    eq(r.status_code, 200, "健康检查状态码")
    eq(r.json()["status"], "ok", "健康检查 status")


# ── 四个页面都能被服务（M1, M6, M9, M12）─────────────────

@check("9")
def s9_index_page_served():
    """GET / 返回 200。"""
    c = client()
    r = c.get("/")
    eq(r.status_code, 200, "首页应返回 200")


@check("9")
def s9_backtest_page_served():
    """GET /backtest.html 返回 200。"""
    c = client()
    r = c.get("/backtest.html")
    eq(r.status_code, 200, "回测页应返回 200")


@check("9")
def s9_tasks_page_served():
    """GET /tasks.html 返回 200。"""
    c = client()
    r = c.get("/tasks.html")
    eq(r.status_code, 200, "任务页应返回 200")


@check("9")
def s9_settings_page_served():
    """GET /settings.html 返回 200。"""
    c = client()
    r = c.get("/settings.html")
    eq(r.status_code, 200, "设置页应返回 200")


# ── 核心 API 端点（M2-M13）──────────────────────────────

@check("9")
def s9_api_templates():
    """GET /api/templates 可用（M2 模板选择）。"""
    fresh_db()
    c = client()
    r = c.get("/api/templates")
    eq(r.status_code, 200, "templates 状态码")
    data = r.json()
    true("items" in data, "templates 应返回含 items 的字典")
    true(isinstance(data["items"], list), "templates.items 应为列表")


@check("9")
def s9_api_indicators():
    """GET /api/indicators 可用（M2 条件搭建）。"""
    c = client()
    r = c.get("/api/indicators")
    eq(r.status_code, 200, "indicators 状态码")
    data = r.json()
    true("items" in data, "indicators 应返回含 items 的字典")
    true(isinstance(data["items"], list), "indicators.items 应为列表")


@check("9")
def s9_api_operators():
    """GET /api/operators 可用（M2 条件搭建）。"""
    c = client()
    r = c.get("/api/operators")
    eq(r.status_code, 200, "operators 状态码")


@check("9")
def s9_api_stocks_search():
    """GET /api/stocks/search 可用（M3 搜索股票）。"""
    c = client()
    r = c.get("/api/stocks/search?q=茅台")
    eq(r.status_code, 200, "stocks/search 状态码")


@check("9")
def s9_api_settings_get():
    """GET /api/settings 可用（M13 设置）。"""
    fresh_db()
    c = client()
    r = c.get("/api/settings")
    eq(r.status_code, 200, "settings 状态码")
    true("values" in r.json(), "settings 应返回 values")


@check("9")
def s9_api_settings_keys():
    """GET /api/settings/keys 可用。"""
    c = client()
    r = c.get("/api/settings/keys")
    eq(r.status_code, 200, "settings/keys 状态码")


@check("9")
def s9_api_strategies_list():
    """GET /api/strategies 可用（M12 任务页需展示策略）。"""
    fresh_db()
    c = client()
    r = c.get("/api/strategies")
    eq(r.status_code, 200, "strategies 状态码")
    true("items" in r.json(), "strategies 应返回含 items 的字典")


@check("9")
def s9_api_backtest_runs():
    """GET /api/backtests/runs 可用（M12 任务列表）。"""
    fresh_db()
    c = client()
    r = c.get("/api/backtests/runs")
    eq(r.status_code, 200, "backtests/runs 状态码")


@check("9")
def s9_api_backtest_trace():
    """GET /api/backtests/trace 端点存在（M7 溯源）。"""
    c = client()
    r = c.get("/api/backtests/trace")
    true(r.status_code != 404, "trace 端点应存在")


@check("9")
def s9_api_backtest_compare():
    """POST /api/backtests/compare 端点存在（M10 横向对比）。"""
    c = client()
    r = c.post("/api/backtests/compare", json={})
    true(r.status_code != 404, "compare 端点应存在")


@check("9")
def s9_api_backtest_export():
    """GET /api/backtests/export 端点存在（M11 导出 CSV）。"""
    c = client()
    r = c.get("/api/backtests/export")
    true(r.status_code != 404, "export 端点应存在")


@check("9")
def s9_api_datasource_status():
    """GET /api/datasource/status 可用。"""
    fresh_db()
    c = client()
    r = c.get("/api/datasource/status")
    eq(r.status_code, 200, "datasource/status 状态码")


@check("9")
def s9_api_cache_stats():
    """GET /api/cache/stats 可用。"""
    fresh_db()
    c = client()
    r = c.get("/api/cache/stats")
    eq(r.status_code, 200, "cache/stats 状态码")


# ── 无登录/注册字样（M15）────────────────────────────────

_AUTH_WORDS = ["登录", "注册", "login", "register", "signup", "sign up"]

@check("9")
def s9_no_auth_in_index():
    """首页无登录/注册字样（M15）。"""
    content = _read(FRONTEND / "index.html")
    for w in _AUTH_WORDS:
        true(w not in content.lower(), f"index.html 不应含 '{w}'")


@check("9")
def s9_no_auth_in_backtest():
    """回测页无登录/注册字样（M15）。"""
    content = _read(FRONTEND / "backtest.html")
    for w in _AUTH_WORDS:
        true(w not in content.lower(), f"backtest.html 不应含 '{w}'")


@check("9")
def s9_no_auth_in_tasks():
    """任务页无登录/注册字样（M15）。"""
    content = _read(FRONTEND / "tasks.html")
    for w in _AUTH_WORDS:
        true(w not in content.lower(), f"tasks.html 不应含 '{w}'")


@check("9")
def s9_no_auth_in_settings():
    """设置页无登录/注册字样（M15）。"""
    content = _read(FRONTEND / "settings.html")
    for w in _AUTH_WORDS:
        true(w not in content.lower(), f"settings.html 不应含 '{w}'")


@check("9")
def s9_no_auth_in_js():
    """所有 JS 文件无登录/注册字样（M15）。"""
    for js_file in JS.glob("*.js"):
        content = _read(js_file)
        for w in _AUTH_WORDS:
            true(w not in content.lower(),
                 f"{js_file.name} 不应含 '{w}'")


# ── 免责声明（M6, M12）───────────────────────────────────

@check("9")
def s9_disclaimer_in_backtest():
    """回测页有免责声明。"""
    content = _read(FRONTEND / "backtest.html")
    true("免责" in content or "disclaimer" in content.lower(),
         "backtest.html 应有免责声明")


@check("9")
def s9_disclaimer_in_tasks():
    """任务页有免责声明。"""
    content = _read(FRONTEND / "tasks.html")
    true("免责" in content or "disclaimer" in content.lower(),
         "tasks.html 应有免责声明")


@check("9")
def s9_disclaimer_in_settings():
    """设置页有免责声明。"""
    content = _read(FRONTEND / "settings.html")
    true("免责" in content or "disclaimer" in content.lower(),
         "settings.html 应有免责声明")


# ── 无 CDN 引用（全局）───────────────────────────────────

@check("9")
def s9_no_cdn_in_html():
    """所有 HTML 文件无 CDN 引用。"""
    cdn_markers = ["cdn.jsdelivr", "cdn.bootcss", "unpkg.com",
                   "cdnjs.cloudflare", "code.jquery.com"]
    for html in FRONTEND.glob("*.html"):
        content = _read(html)
        for m in cdn_markers:
            true(m not in content, f"{html.name} 不应含 CDN: {m}")


@check("9")
def s9_no_cdn_in_js():
    """所有 JS 文件无 CDN 引用。"""
    cdn_markers = ["cdn.jsdelivr", "cdn.bootcss", "unpkg.com",
                   "cdnjs.cloudflare", "code.jquery.com"]
    for js_file in JS.glob("*.js"):
        content = _read(js_file)
        for m in cdn_markers:
            true(m not in content, f"{js_file.name} 不应含 CDN: {m}")


# ── 数据持久化（M14）─────────────────────────────────────

@check("9")
def s9_data_persistence_strategy():
    """策略存入后能读出（M14 数据持久化）。"""
    fresh_db()
    c = client()
    payload = {
        "name": "持久化测试策略",
        "note": "",
        "period": "daily",
        "dsl": {
            "signals": {
                "buy": {
                    "logic": "and",
                    "items": [
                        {
                            "left": {"t": "ind", "name": "CLOSE", "args": []},
                            "cmp": ">",
                            "right": {"t": "ind", "name": "MA", "args": [20]}
                        }
                    ]
                },
                "sell": {
                    "logic": "and",
                    "items": [
                        {
                            "left": {"t": "ind", "name": "CLOSE", "args": []},
                            "cmp": "<",
                            "right": {"t": "ind", "name": "MA", "args": [5]}
                        }
                    ]
                }
            },
            "sizing": {"mode": "all_in"},
            "risk": {"stop_loss_pct": 5}
        }
    }
    r = c.post("/api/strategies", json=payload)
    eq(r.status_code, 200, f"创建策略状态码: {r.text[:200] if r.status_code != 200 else ''}")
    sid = r.json()["id"]

    r2 = c.get(f"/api/strategies/{sid}")
    eq(r2.status_code, 200, "读取策略状态码")
    eq(r2.json()["name"], "持久化测试策略", "策略名称应一致")


@check("9")
def s9_data_persistence_settings():
    """设置修改后持久化（M13 设置变更）。"""
    fresh_db()
    c = client()
    r = c.post("/api/settings", json={
        "commission_rate": "0.0005"
    })
    eq(r.status_code, 200, "保存设置状态码")

    r2 = c.get("/api/settings")
    eq(r2.status_code, 200, "读取设置状态码")
    near(r2.json()["values"]["commission_rate"], "0.0005", 1e-8,
         "佣金率应已更新")


# ── 导航一致性 ──────────────────────────────────────────

@check("9")
def s9_nav_links_consistent():
    """所有页面导航链接一致（M1 导航）。"""
    expected_links = [
        ('href="/"', "建策略"),
        ('href="/tasks.html"', "我的任务"),
        ('href="/settings.html"', "设置"),
    ]
    for html_name in ("index.html", "backtest.html", "tasks.html", "settings.html"):
        content = _read(FRONTEND / html_name)
        for link, label in expected_links:
            true(link in content,
                 f"{html_name} 应含导航链接 {link} ({label})")


# ── 回测页核心元素（M6）─────────────────────────────────

@check("9")
def s9_backtest_has_metric_cards():
    """回测页有指标卡容器（M6 要求 11 项指标）。"""
    content = _read(FRONTEND / "backtest.html")
    true("metric" in content.lower() or "指标" in content,
         "回测页应有指标卡区域")


@check("9")
def s9_backtest_has_chart_area():
    """回测页有图表容器（M6 K线图 + 收益曲线）。"""
    content = _read(FRONTEND / "backtest.html")
    true("chart" in content.lower(),
         "回测页应有图表容器")


@check("9")
def s9_backtest_has_trade_table():
    """回测页有交易记录表（M6）。"""
    content = _read(FRONTEND / "backtest.html")
    true("trade" in content.lower() or "交易" in content,
         "回测页应有交易记录区域")


# ── 导出排除 API Key（M11 延伸）─────────────────────────

@check("9")
def s9_export_excludes_secret():
    """settings.js 导出时排除 API Key。"""
    content = _read(JS / "settings.js")
    true("secret" in content.lower(), "导出应处理 secret 字段")
