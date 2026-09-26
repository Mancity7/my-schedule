"""阶段 0 自检：数据库结构、设置初始化、板块判定、健康检查、错误格式、静态页托管。

对应 PRD 验收 A / B / L1 / L10，功能 F1-F5。
"""

import json
import re
from pathlib import Path

from ..config import DEFAULT_SETTINGS, FRONTEND_DIR, board_of, limit_rate, market_prefix
from ..db import connect, dumps, get_fee_config, get_settings, loads, set_settings
from ..errors import (
    KIND_DATASOURCE,
    KIND_INPUT,
    KIND_INTEGRITY,
    KIND_PROGRAM,
    AppError,
    datasource_error,
    input_error,
    integrity_error,
    program_error,
)
from .harness import VOLUME_DIR, check, client, eq, fresh_db, near, true

EXPECTED_TABLES = {
    "stock_basic",
    "kline",
    "index_kline",
    "valuation",
    "strategy",
    "backtest_run",
    "backtest",
    "settings",
    "app_state",
    "sync_meta",
    "news",
}


@check("0")
def t01_schema_creates_all_tables():
    fresh_db()
    with connect() as conn:
        rows = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    missing = EXPECTED_TABLES - rows
    true(not missing, f"建表缺失：{missing}")


@check("0")
def t02_kline_has_source_column():
    """每行K线必须能追溯来源，否则混源无法检出（PRD F9/F13）。"""
    fresh_db()
    with connect() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(kline)")}
    for need in {"code", "period", "adjust", "ts", "open", "high", "low", "close", "source"}:
        true(need in cols, f"kline 缺列 {need}，实际 {sorted(cols)}")


@check("0")
def t03_default_settings_seeded():
    fresh_db()
    s = get_settings()
    for k, v in DEFAULT_SETTINGS.items():
        eq(s.get(k), v, f"设置项 {k} 未写入默认值")


@check("0")
def t04_settings_survive_reopen():
    """对应验收 B4：改佣金后重启容器仍是新值。"""
    fresh_db()
    set_settings({"commission_rate": "0.001"})
    eq(get_settings()["commission_rate"], "0.001", "重新连接后读回的佣金")
    eq(get_fee_config()["commission_rate"], 0.001, "撮合费用配置读回的佣金")


@check("0")
def t05_settings_reject_unknown_keys():
    fresh_db()
    set_settings({"not_a_real_setting": "x"})
    with connect() as conn:
        n = conn.execute("SELECT COUNT(*) c FROM settings WHERE k='not_a_real_setting'").fetchone()["c"]
    eq(n, 0, "未知设置项不应入库")


@check("0")
def t06_fee_config_defaults():
    fresh_db()
    set_settings({k: v for k, v in DEFAULT_SETTINGS.items()})
    fee = get_fee_config()
    near(fee["commission_rate"], 0.00025, 1e-12, "佣金率")
    near(fee["commission_min"], 5.0, 1e-9, "最低佣金")
    near(fee["stamp_duty_rate"], 0.0005, 1e-12, "印花税")
    near(fee["transfer_fee_rate"], 0.00001, 1e-12, "过户费")
    near(fee["slippage"], 0.001, 1e-12, "滑点")
    near(fee["risk_free_rate"], 0.02, 1e-12, "无风险利率")
    near(fee["annual_days"], 252, 1e-9, "年化交易日")


@check("0")
def t07_fee_config_survives_garbage():
    """设置被写坏时退回默认，而不是让回测用错费率。"""
    fresh_db()
    set_settings({"commission_rate": "不是数字"})
    near(get_fee_config()["commission_rate"], 0.00025, 1e-12, "非法值应回退默认佣金")


@check("0")
def t08_board_limit_rates():
    eq(limit_rate("600519", "贵州茅台"), 0.10, "主板涨跌停")
    eq(limit_rate("000001", "平安银行"), 0.10, "深主板涨跌停")
    eq(limit_rate("300750", "宁德时代"), 0.20, "创业板涨跌停")
    eq(limit_rate("301234", "样例"), 0.20, "创业板301涨跌停")
    eq(limit_rate("688981", "中芯国际"), 0.20, "科创板涨跌停")
    eq(limit_rate("920047", "诺思兰德"), 0.30, "北交所涨跌停")
    eq(limit_rate("600001", "ST某某"), 0.05, "ST股涨跌停")
    eq(board_of("688981", "中芯国际"), "star", "板块判定 688")
    eq(board_of("300750", "宁德时代"), "gem", "板块判定 300")


@check("0")
def t09_market_prefix():
    eq(market_prefix("600519"), "sh", "沪市主板")
    eq(market_prefix("688981"), "sh", "科创板")
    eq(market_prefix("000001"), "sz", "深市主板")
    eq(market_prefix("300750"), "sz", "创业板")
    eq(market_prefix("920047"), "bj", "北交所 92 开头")
    eq(market_prefix("830799"), "bj", "北交所 8 开头")
    eq(market_prefix("430047"), "bj", "北交所 4 开头")


@check("0")
def t10_json_roundtrip_keeps_chinese():
    obj = {"name": "双均线金叉", "num": 3, "nil": None}
    text = dumps(obj)
    true("双均线金叉" in text, "JSON 不应把中文转义成 \\uXXXX")
    eq(loads(text), obj, "JSON 往返")
    eq(loads(None, {"a": 1}), {"a": 1}, "空文本应返回兜底值")
    eq(loads("{坏数据", {"a": 1}), {"a": 1}, "坏数据应返回兜底值而不是抛异常")


@check("0")
def t11_health_endpoint():
    with client() as c:
        r = c.get("/api/health")
    eq(r.status_code, 200, "健康检查状态码")
    body = r.json()
    eq(body["status"], "ok", "健康检查 status")
    eq(body["db"], "ready", "健康检查 db")
    true(re.fullmatch(r"\d+\.\d+\.\d+", body["version"]), f"版本号格式 {body['version']}")


@check("0")
def t12_error_payload_shape_for_all_kinds():
    """四类错误必须走同一个 JSON 形状，前端才能按 kind 上色（验收 L3-L5）。"""
    cases = [
        (datasource_error("FETCH_FAILED", "数据源连不上", detail="akshare 超时"), KIND_DATASOURCE, 502),
        (input_error("BAD_FIELD", "均线天数要填正整数", field="signals.buy.items.0"), KIND_INPUT, 400),
        (integrity_error("KLINE_GAP_TOO_LARGE", "数据缺口 8.3% 超过容忍上限"), KIND_INTEGRITY, 422),
        (program_error("INTERNAL_ERROR", "程序异常", detail="traceback..."), KIND_PROGRAM, 500),
    ]
    for exc, kind, http in cases:
        eq(exc.kind, kind, f"{kind} 分类")
        eq(exc.http, http, f"{kind} HTTP 状态码")
        p = exc.payload()["error"]
        for key in ("kind", "code", "message", "detail", "retriable"):
            true(key in p, f"{kind} payload 缺字段 {key}")


@check("0")
def t13_input_error_carries_field():
    exc = input_error("E", "m", field="start_date")
    eq(exc.payload()["error"]["field"], "start_date", "输入类错误必须带字段名，前端才能标红并跳焦点")


@check("0")
def t14_unknown_api_path_returns_json():
    with client() as c:
        r = c.get("/api/definitely-not-here")
    eq(r.status_code, 404, "未知接口状态码")
    eq(r.headers["content-type"].split(";")[0], "application/json", "未知接口必须返回 JSON 而不是 HTML")
    eq(r.json()["error"]["code"], "NOT_FOUND", "未知接口错误码")


@check("0")
def t15_unhandled_exception_handler():
    from types import SimpleNamespace

    from ..main import handle_unexpected

    try:
        raise ValueError("炸了")
    except ValueError as exc:
        resp = handle_unexpected(SimpleNamespace(method="GET", url=SimpleNamespace(path="/api/x")), exc)
    eq(resp.status_code, 500, "未预期异常状态码")
    body = json.loads(resp.body)
    eq(body["error"]["kind"], KIND_PROGRAM, "未预期异常分类")
    true("ValueError" in body["error"]["detail"], "详情里要带可复制的异常信息")
    true("炸了" in body["error"]["detail"], "详情里要带原始报错文本")


@check("0")
def t16_app_error_handler_roundtrip():
    from types import SimpleNamespace

    from ..main import handle_app_error

    resp = handle_app_error(
        SimpleNamespace(method="POST", url=SimpleNamespace(path="/api/backtests")),
        integrity_error("KLINE_GAP_TOO_LARGE", "缺口超限", detail="2024-03-01 ~ 2024-03-05"),
    )
    eq(resp.status_code, 422, "完整性错误经 handler 后的状态码")
    eq(json.loads(resp.body)["error"]["code"], "KLINE_GAP_TOO_LARGE", "handler 输出的错误码")


@check("0")
def t17_index_page_served():
    with client() as c:
        r = c.get("/")
    eq(r.status_code, 200, "首页状态码")
    html = r.text
    for needle in ("建策略", "我的任务", "设置", "不构成投资建议"):
        true(needle in html, f"首页缺少：{needle}")


@check("0")
def t18_no_auth_words_in_frontend():
    """验收 L1：全站不出现登录/注册入口。"""
    bad = re.compile(r"登录|注册|sign\s*in|log\s*in|signup|register", re.I)
    hits = []
    for p in list(FRONTEND_DIR.rglob("*.html")) + list(FRONTEND_DIR.rglob("*.js")):
        if "vendor" in p.parts:
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if bad.search(line):
                hits.append(f"{p.name}:{i}: {line.strip()[:60]}")
    true(not hits, f"前端出现登录/注册字样：\n      " + "\n      ".join(hits))


@check("0")
def t19_static_dir_is_readonly_mount():
    """前端由容器只读挂载，代码不应往 frontend/ 写东西。"""
    true(FRONTEND_DIR.is_dir(), f"找不到前端目录 {FRONTEND_DIR}")
    compose = Path("/docker-compose.yml")
    if not compose.exists():  # 容器里看不到宿主 compose 文件，由 test.bat 在宿主侧检查
        return
    text = compose.read_text(encoding="utf-8")
    true("./frontend:/app/frontend:ro" in text, "前端目录应以只读方式挂载")


@check("0")
def t20_db_file_lands_in_volume():
    """验收 B2/B3：数据库必须在挂载卷里，容器删了数据才不丢。"""
    from ..config import DB_PATH

    eq(VOLUME_DIR, "/app/data", "容器内数据目录应为挂载点 /app/data")
    true(DB_PATH.is_relative_to(Path(VOLUME_DIR)), f"数据库 {DB_PATH} 不在挂载卷 {VOLUME_DIR} 内")
    fresh_db()
    true(DB_PATH.exists(), "quant.db 未生成")


@check("0")
def t20b_selfcheck_uses_scratch_db():
    """自检绝不能碰正式库，否则会污染用户的历史结果。"""
    from ..config import DB_PATH

    true("selfcheck" in str(DB_PATH), f"自检应跑在独立库上，实际 {DB_PATH}")


@check("0")
def t21_app_error_is_exception_subclass():
    true(issubclass(AppError, Exception), "AppError 必须继承 Exception 才能被 handler 捕获")


@check("0")
def t22_handlers_are_wired_end_to_end():
    """确认异常处理器真的会被 Starlette 调用，而不是只能手工调用。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from .. import main as main_mod

    sub = FastAPI()
    sub.add_exception_handler(AppError, main_mod.handle_app_error)
    sub.add_exception_handler(Exception, main_mod.handle_unexpected)

    @sub.get("/boom")
    def _boom():
        raise integrity_error("KLINE_GAP_TOO_LARGE", "缺口超限")

    @sub.get("/crash")
    def _crash():
        raise ValueError("非预期崩溃")

    with TestClient(sub, raise_server_exceptions=False) as c:
        r = c.get("/boom")
        eq(r.status_code, 422, "AppError 经中间件后的状态码")
        eq(r.json()["error"]["kind"], KIND_INTEGRITY, "AppError 经中间件后的分类")

        r2 = c.get("/crash")
        eq(r2.status_code, 500, "未预期异常经中间件后的状态码")
        eq(r2.json()["error"]["kind"], KIND_PROGRAM, "未预期异常的分类")
