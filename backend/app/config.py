import os
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "quant.db"

FRONTEND_DIR = Path(os.environ.get("FRONTEND_DIR", "/app/frontend"))
SCHEMA_PATH = Path(__file__).parent / "schema.sql"

APP_VERSION = "0.1.0"

# 设置项分组，设置页按组显示、按组「恢复本组默认」。
SETTING_GROUPS = {
    "trade": ["commission_rate", "commission_min", "stamp_duty_rate",
              "transfer_fee_rate", "slippage", "initial_cash"],
    "metric": ["risk_free_rate", "annual_days", "benchmark"],
    "data": ["datasource_order", "gap_tolerance_pct", "cache_ttl_days"],
    "ai": ["deepseek_api_key", "deepseek_base_url", "deepseek_model"],
}

# 数值项的合法区间。超过区间 = 用户填错，不是"帮他改成能跑的值"。
SETTING_RANGES = {
    "commission_rate": (0.0, 0.01),        # 万0 ~ 百分1
    "commission_min": (0.0, 100.0),        # 元
    "stamp_duty_rate": (0.0, 0.01),
    "transfer_fee_rate": (0.0, 0.01),
    "slippage": (0.0, 0.1),
    "initial_cash": (1000.0, 1e10),
    "risk_free_rate": (-0.05, 0.2),
    "annual_days": (100, 400),
    "gap_tolerance_pct": (0.0, 50.0),      # 缺口容忍上限，超过就该重新下载
    "cache_ttl_days": (0.0, 30.0),
}

# 这些设置不能出现在导出文件里，也不能原样回给前端
SECRET_SETTINGS = {"deepseek_api_key"}

# 取数上游顺序里允许出现的名字（阶段 1 实测结论）
UPSTREAM_NAMES = ["sina", "tencent", "eastmoney", "baostock"]

# 设置项默认值。用户可在「设置」页修改，改动存进 SQLite，重启不丢。
DEFAULT_SETTINGS = {
    "commission_rate": "0.00025",      # 佣金 万2.5，双向
    "commission_min": "5",             # 单笔最低 5 元
    "stamp_duty_rate": "0.0005",       # 印花税 千0.5，仅卖出
    "transfer_fee_rate": "0.00001",    # 过户费 万0.1，双向
    "slippage": "0.001",               # 滑点 0.1%
    "initial_cash": "100000",          # 每只股票独立账户的初始资金
    "risk_free_rate": "0.02",          # 夏普比率的无风险利率
    "annual_days": "252",              # 年化交易日
    "benchmark": "000300",             # 基准指数：沪深300
    # 取数上游顺序。2026-09-25 实测：baostock 需 TCP 10030（本机不通）、
    # 东财接口本机频繁 RemoteDisconnected，故主用新浪，腾讯次之，东财兜底。
    "datasource_order": "sina,tencent,eastmoney",
    "gap_tolerance_pct": "5",          # 数据缺口容忍上限（%），超过则 fail-closed 拒绝回测
    # 行情缓存有效期（天）。前复权序列每发生一次除权就整体重新锚定，
    # 只补最新一天的"增量"会把老价格留在旧锚点上，凭空造出一个涨跌。
    # 所以缓存只在一天内可信，过期后整段重下（一次请求，代价与只补尾巴相同）。
    "cache_ttl_days": "1",
    "deepseek_api_key": "",
    "deepseek_base_url": "https://api.deepseek.com",
    "deepseek_model": "deepseek-chat",
}

# A股各板块涨跌停幅度
LIMIT_RATES = {
    "main": 0.10,       # 主板 60/00 开头
    "star": 0.20,       # 科创板 688
    "gem": 0.20,        # 创业板 300/301
    "bse": 0.30,        # 北交所 4/8/92 开头
    "st": 0.05,         # ST 股
}


def market_prefix(code: str) -> str:
    """返回交易所前缀 sh/sz/bj。"""
    code = code.strip()
    # 92 开头是北交所新股，必须排在 "9" 之前判断，否则会被误判为沪市
    if code.startswith(("4", "8", "92")):
        return "bj"
    if code.startswith(("60", "68", "9", "5")):
        return "sh"
    return "sz"


# 指数代码的前缀和个股规则**不一样**：沪深300 的代码是 000300，但它挂在沪市
# （sh000300）下；按个股规则拼成 sz000300 会取回空表。
INDEX_PREFIX = {"000": "sh", "880": "sh", "999": "sh", "399": "sz", "899": "bj"}


def index_symbol(code: str) -> str:
    """指数代码 → 新浪/腾讯用的带前缀代码。"""
    c = str(code).strip().lower()
    if c.startswith(("sh", "sz", "bj")):
        return c
    for pfx, mkt in INDEX_PREFIX.items():
        if c.startswith(pfx):
            return mkt + c
    return "sh" + c


def board_of(code: str, name: str = "") -> str:
    """判断股票所属板块，用于确定涨跌停幅度。"""
    if "ST" in name.upper():
        return "st"
    code = code.strip()
    if code.startswith("688"):
        return "star"
    if code.startswith(("300", "301", "302")):
        return "gem"
    if code.startswith(("4", "8", "92")):
        return "bse"
    return "main"


def limit_rate(code: str, name: str = "") -> float:
    return LIMIT_RATES[board_of(code, name)]


def round_money(x, nd: int = 2) -> float:
    """钱和价格都精确到分，逢五进一。

    不用内置 `round()`：它按二进制实际值做银行家舍入，`round(2.675, 2)` 给 2.67，
    和行情软件、券商交割单上的数字对不上。验收 F10 要求全账误差 < 0.01 元，
    所以撮合层处处都得走这同一个口径（`repr` 先转成十进制字符串再舍入）。
    """
    quant = Decimal(1).scaleb(-int(nd))
    return float(Decimal(repr(float(x))).quantize(quant, rounding=ROUND_HALF_UP))


def limit_price(prev_close, rate: float, direction: int = 1) -> float:
    """涨/跌停价 = 昨收 ×(1 ± 幅度)，再四舍五入到分（交易所就是这么定的）。"""
    return round_money(float(prev_close) * (1.0 + float(direction) * float(rate)))
