"""指标库 —— 白名单就是这套系统的能力边界。

三条硬规矩：
1. 前端下拉里的每一项都来自本文件（红线 6：不允许前端硬编码指标名）。
2. **暖机段一律留 NaN**。填 0 会让「收盘价 > MA(20)」在头 20 根上凭空成立；
   留 NaN 才能被 dsl 标成「数据不足」并跳过（验收 G4 / F17）。
3. 单位跟 datasource 的规范列一致：volume=手、amount=元、turnover/pct_chg 是**百分数**
   （0.8 就是 0.8%）。指标不重新解释单位。

公式口径按国内行情软件的习惯对齐，每处偏离都在注释里写明，避免「看着像但算得不一样」。
"""

from typing import Any, Callable

import numpy as np
import pandas as pd

from .errors import integrity_error

# 参数一律要求正整数：MA(-5)、RSI(abc) 都是用户填错，不是「帮他改成能跑的值」
PARAM_MIN, PARAM_MAX = 1, 500
PARAM_HINT = "天数要填正整数，比如 5、10、20"


def _p(cn: str, default: int, max_: int = 250) -> dict[str, Any]:
    return {"name": "n", "cn": cn, "default": default,
            "min": PARAM_MIN, "max": min(max_, PARAM_MAX), "integer": True}


def _pk() -> list[dict[str, Any]]:
    """布林带两个参数：天数 + 带宽倍数（倍数允许小数，所以不能套正整数规则）。"""
    return [{"name": "n", "cn": "天数", "default": 20, "min": PARAM_MIN, "max": PARAM_MAX,
             "integer": True},
            {"name": "k", "cn": "带宽倍数", "default": 2, "min": 0.5, "max": 5, "integer": False}]


def _ema(s: pd.Series, n: int) -> pd.Series:
    """指数移动平均。首日直接用当天数值起算（国内软件同此口径）。"""
    return s.ewm(span=n, adjust=False).mean()


def _wilder(s: pd.Series, n: int) -> pd.Series:
    """Wilder 递推平滑：用前 n 个**真实存在**的变动做种子，之后 alpha=1/n。

    RSI 的标准算法。两处容易写错：
    - 直接 ewm(alpha=1/n) 起算，前几十根会和行情软件差一截；
    - 按位置取"前 n 个"，可传进来的常常是 diff() 的结果，第 0 根本来就是 NaN，
      这样种子只用了 n-1 个变动，还比数据本身早一根出值。
    """
    arr = s.to_numpy(dtype="float64", copy=True)
    valid = np.flatnonzero(~np.isnan(arr))
    if valid.size < n:
        return pd.Series(np.nan, index=s.index, dtype="float64")
    seed_at = int(valid[n - 1])
    seed = float(arr[valid[:n]].mean())        # 先算种子：下面就要把前面覆盖成 NaN
    arr[:seed_at] = np.nan
    arr[seed_at] = seed
    return pd.Series(arr, index=s.index, dtype="float64").ewm(alpha=1 / n, adjust=False).mean()


def _recurve(s: pd.Series, alpha: float, seed: float | None = None) -> pd.Series:
    """KDJ 那种「上期值 ×(1-α) + 本期值 ×α」递推，缺失段保持缺失，用 seed 起算。

    ewm(adjust=False) 会以第一个真实值起算，而国内软件的 K/D 首日都从 50 开始，
    所以这里显式补一个 seed，算完再把它丢掉。
    """
    valid = s.dropna()
    out = pd.Series(np.nan, index=s.index, dtype="float64")
    if valid.empty:
        return out
    head = [] if seed is None else [float(seed)]
    y = pd.Series(head + list(valid.to_numpy()), dtype="float64").ewm(alpha=alpha, adjust=False).mean()
    out.loc[valid.index] = y.iloc[len(head):].to_numpy()
    return out


def _col(df: pd.DataFrame, name: str) -> pd.Series:
    return pd.to_numeric(df[name], errors="coerce")


def _flat(col: str) -> Callable:
    def get(df, *_args):
        return _col(df, col)
    return get


# ------------------------------------------------------------------ 派生线


def _ma(df, n, *_):
    return _col(df, "close").rolling(int(n)).mean()


def _ema_ind(df, n, *_):
    return _ema(_col(df, "close"), int(n))


def _vol_ma(df, n, *_):
    return _col(df, "volume").rolling(int(n)).mean()


def _macd(df) -> tuple[pd.Series, pd.Series, pd.Series]:
    close = _col(df, "close")
    dif = _ema(close, 12) - _ema(close, 26)
    dea = _ema(dif, 9)
    # 国内软件的 MACD 柱 = 2×(DIF-DEA)，跟盘面一致，不用国外口径的 (DIF-DEA)
    return dif, dea, (dif - dea) * 2


def _macd_part(i: int) -> Callable:
    def get(df, *_args):
        return _macd(df)[i]
    return get


def _rsi(df, n, *_):
    diff = _col(df, "close").diff()
    n = int(n)
    gain = _wilder(diff.clip(lower=0), n)
    loss = _wilder(-diff.clip(upper=0), n)
    # 100·涨均/(涨均+跌均) 与 100-100/(1+RS) 等价，但跌均=0 时自然得 100，不会除零
    with np.errstate(divide="ignore", invalid="ignore"):
        rsi = 100 * gain / (gain + loss)
    return rsi.where(gain.notna() & loss.notna())


def _kdj(df, n) -> tuple[pd.Series, pd.Series, pd.Series]:
    low, high, close = _col(df, "low"), _col(df, "high"), _col(df, "close")
    n = int(n)
    lo = low.rolling(n).min()
    hi = high.rolling(n).max()
    span = hi - lo
    with np.errstate(divide="ignore", invalid="ignore"):
        rsv = (close - lo) / span * 100
    # 一字板（最高=最低）那天 RSV 没有定义，国内软件记 50；留 NaN 会把正常交易日当数据不足
    rsv = rsv.where(span != 0, 50.0)
    k = _recurve(rsv, 1 / 3, seed=50.0)
    d = _recurve(k, 1 / 3, seed=50.0)
    return k, d, 3 * k - 2 * d


def _kdj_part(i: int) -> Callable:
    def get(df, n=None, *_args):
        return _kdj(df, n if n is not None else 9)[i]
    return get


def _boll(df, n, k=None):
    close = _col(df, "close")
    mid = close.rolling(int(n)).mean()
    # 国内布林带用总体标准差（ddof=0）；pandas 默认样本标准差，会让轨道略宽
    std = close.rolling(int(n)).std(ddof=0)
    return mid, std, float(k if k is not None else 2)


def _boll_part(which: str) -> Callable:
    def get(df, n=None, k=None):
        mid, std, k_ = _boll(df, n if n is not None else 20, k)
        if which == "mid":
            return mid
        return mid + k_ * std if which == "up" else mid - k_ * std
    return get


def _liangbi(df, n=None, *_):
    vol = _col(df, "volume")
    # 量比 = 当日成交量 / 前 n 日均量。必须 shift(1)：把当天算进分母是自己吃自己，
    # 放量判断会变得迟钝。
    base = vol.shift(1).rolling(int(n) if n else 5).mean()
    with np.errstate(divide="ignore", invalid="ignore"):
        out = vol / base
    return out.where(base.notna() & (base > 0))


def _hhv(df, n=None, col="high", newest=True):
    """前 n 根的最高价（**不含当天**）——「今天创 n 日新高」就是 HIGH > HHV(20)。

    必须 shift(1)：把当天算进去，今天自己永远和自己比不出新高。
    newest=False 时改成取最低，供 LLV 复用同一条 shift 规则。
    """
    s = _col(df, col)
    roll = s.shift(1).rolling(int(n) if n else 20)
    return roll.max() if newest else roll.min()


def _hhv_part(col: str, newest: bool = True) -> Callable:
    def get(df, n=None):
        return _hhv(df, n, col, newest)
    return get


def _term(what: str, example: str, why: str) -> dict[str, str]:
    return {"what": what, "example": example, "why": why}


def _wn(extra: int = 0) -> Callable:
    """暖机长度随参数变化：MA(20) 要 20 根，KDJ(9) 的 D 线要更久。"""
    return lambda args: int(args[0] if args else 20) + extra


INDICATORS: dict[str, dict[str, Any]] = {
    # ---------------- 行情原始值 ----------------
    "OPEN": dict(code="OPEN", cn="开盘价", cat="行情数据", params=[], needs=["open"],
                 warmup=0, unit="元", fn=_flat("open"),
                 term=_term("当天早上的第一个成交价", "今天 9:30 第一笔成交在 12.30 元，开盘价就是 12.30",
                            "买卖其实发生在开盘，只看收盘价会错过这段差距")),
    "HIGH": dict(code="HIGH", cn="最高价", cat="行情数据", params=[], needs=["high"],
                 warmup=0, unit="元", fn=_flat("high"),
                 term=_term("当天成交过的最高价格", "当天最高摸到 12.80，最高价就是 12.80",
                            "突破前高、创新高这类打法要用它")),
    "LOW": dict(code="LOW", cn="最低价", cat="行情数据", params=[], needs=["low"],
                warmup=0, unit="元", fn=_flat("low"),
                term=_term("当天成交过的最低价格", "当天最低跌到 11.90，最低价就是 11.90",
                           "判断有没有跌破支撑、盘中有没有触及止损要用它")),
    "CLOSE": dict(code="CLOSE", cn="收盘价", cat="行情数据", params=[], needs=["close"],
                  warmup=0, unit="元", fn=_flat("close"),
                  term=_term("下午 3 点最后那笔成交的价格，画K线用的就是它",
                             "今天收在 12.50，收盘价就是 12.50", "最常用、也最算数的一个价")),
    "VOLUME": dict(code="VOLUME", cn="成交量", cat="行情数据", params=[], needs=["volume"],
                   warmup=0, unit="手", fn=_flat("volume"),
                   term=_term("当天成交了多少手（1 手 = 100 股）", "成交 20 万手 = 2000 万股",
                              "放量缩量都看它，是资金活跃度最直接的体现")),
    "AMOUNT": dict(code="AMOUNT", cn="成交额", cat="行情数据", params=[], needs=["amount"],
                   warmup=0, unit="元", fn=_flat("amount"),
                   term=_term("当天成交了多少钱", "成交额 3.2 亿元",
                              "不受股价高低影响，比成交量更适合拿不同股票互相比")),
    "TURNOVER": dict(code="TURNOVER", cn="换手率", cat="行情数据", params=[], needs=["turnover"],
                     warmup=0, unit="%", fn=_flat("turnover"),
                     term=_term("当天成交的股数占流通盘的比例，数值本身就是百分数",
                                "填 3 表示成交了流通盘的 3%",
                                "大盘股突然高换手往往说明有资金进来；小盘股高换手是常态")),
    "PCT_CHG": dict(code="PCT_CHG", cn="涨跌幅", cat="行情数据", params=[], needs=["pct_chg"],
                    warmup=0, unit="%", fn=_flat("pct_chg"),
                    term=_term("今天相对昨天收盘涨了几个百分点", "填 5.2 就是涨 5.2%",
                               "过滤涨停大跌很方便；个别上游没给这一列时会是空值")),
    # ---------------- 均线 ----------------
    "MA": dict(code="MA", cn="简单均线", cat="均线", params=[_p("天数", 20)], needs=["close"],
               warmup=_wn(), unit="元", fn=_ma,
               term=_term("最近 n 天收盘价的平均，把每天的波动抹平成一条线",
                          "MA(5) 就是最近 5 天平均价，常当短期趋势线",
                          "单看一天容易被一根K线骗到，均线能看出方向稳不稳")),
    "EMA": dict(code="EMA", cn="指数均线", cat="均线", params=[_p("天数", 20)], needs=["close"],
                warmup=_wn(), unit="元", fn=_ema_ind,
                term=_term("另一种平均：离今天越近的价格权重越大",
                           "同样 20 天，EMA 比 MA 更快跟上今天的拉升",
                           "MACD 就是用 EMA 算出来的，想和盘面对得上就用它")),
    # ---------------- 量能 ----------------
    "VOL_MA": dict(code="VOL_MA", cn="量能均线", cat="量能", params=[_p("天数", 5)],
                   needs=["volume"], warmup=_wn(), unit="手", fn=_vol_ma,
                   term=_term("最近 n 天成交量的平均，当作正常量的尺子",
                              "VOL_MA(5) 是前 5 天平均成交量，当天是它的 2 倍就叫放量一倍",
                              "价格涨但量没放出来，多半是虚涨")),
    "LIANGBI": dict(code="LIANGBI", cn="量比", cat="量能", params=[_p("天数", 5)],
                    needs=["volume"], warmup=_wn(1), unit="倍", fn=_liangbi,
                    term=_term("今天的成交量是最近 n 天平均量的多少倍",
                               "量比 1.8 就是比近期平均放量八成",
                               "比直接规定「成交量大于多少手」好用，它自动适应了大小盘")),
    # ---------------- MACD ----------------
    # 递推类指标理论上一直在收敛，暖机取「与盘面差异已经小于 0.5%」的根数
    "MACD_DIF": dict(code="MACD_DIF", cn="MACD快线(DIF)", cat="MACD", params=[],
                     needs=["close"], warmup=60, unit="元", fn=_macd_part(0),
                     term=_term("12 日 EMA 减 26 日 EMA，反映短期比长期强多少",
                                "DIF 由负转正，说明短期均线已经跑到长期上面去了",
                                "MACD 金叉死叉看的就是 DIF 和 DEA 谁在上面")),
    "MACD_DEA": dict(code="MACD_DEA", cn="MACD慢线(DEA)", cat="MACD", params=[],
                     needs=["close"], warmup=120, unit="元", fn=_macd_part(1),
                     term=_term("DIF 的 9 日 EMA，是 DIF 的平滑版",
                                "DIF 上穿 DEA 就叫金叉", "慢线用来过滤快线的假信号")),
    "MACD_HIST": dict(code="MACD_HIST", cn="MACD柱", cat="MACD", params=[],
                      needs=["close"], warmup=120, unit="元", fn=_macd_part(2),
                      term=_term("2×(DIF-DEA)，就是盘面上那根红绿柱",
                                 "柱子由长变短，说明这波推动在减弱",
                                 "和国内软件一致：柱值是差值的两倍，不是差值本身")),
    # ---------------- RSI ----------------
    "RSI": dict(code="RSI", cn="相对强弱(RSI)", cat="超买超卖", params=[_p("天数", 14, 100)],
                needs=["close"], warmup=_wn(1), unit="0~100", fn=_rsi,
                term=_term("最近 n 天里上涨的力气占总共波动的比例，落在 0~100",
                           "RSI(14)=70 以上通常算超买，30 以下算超卖",
                           "它衡量力气而不是涨跌，跌势里 RSI 低不代表一定会反弹")),
    # ---------------- KDJ ----------------
    "KDJ_K": dict(code="KDJ_K", cn="KDJ的K线", cat="超买超卖", params=[_p("天数", 9, 60)],
                  needs=["close", "high", "low"], warmup=_wn(4), unit="0~100", fn=_kdj_part(0),
                  term=_term("先在最近 n 天的最高最低之间给今天收盘价打个分（RSV），再平滑成 K",
                             "K 到 80 以上算高位", "KDJ 比 RSI 更敏感，适合看短线进出点")),
    "KDJ_D": dict(code="KDJ_D", cn="KDJ的D线", cat="超买超卖", params=[_p("天数", 9, 60)],
                  needs=["close", "high", "low"], warmup=_wn(8), unit="0~100", fn=_kdj_part(1),
                  term=_term("K 再平滑一次得到 D，比 K 慢半拍", "K 上穿 D 是常用买点",
                             "慢线用来确认快线不是瞎晃")),
    "KDJ_J": dict(code="KDJ_J", cn="KDJ的J线", cat="超买超卖", params=[_p("天数", 9, 60)],
                  needs=["close", "high", "low"], warmup=_wn(8), unit="-20~120", fn=_kdj_part(2),
                  term=_term("3K-2D，把 K 和 D 的差距放大", "J 经常冲到 100 以上或跌破 0",
                             "最敏感也最容易假信号，一般只当辅助")),
    # ---------------- 区间高低位 ----------------
    "HHV": dict(code="HHV", cn="前N日最高价", cat="区间高低", params=[_p("天数", 20)],
                needs=["high"], warmup=_wn(1), unit="元", fn=_hhv_part("high"),
                term=_term("最近 n 天（不算今天）的最高价，当作这段行情的天花板",
                           "今天最高价超过 HHV(20)，就是常说的创 20 日新高",
                           "突破新高才谈得上「打开空间」，这是打突破最核心的一条判据")),
    "LLV": dict(code="LLV", cn="前N日最低价", cat="区间高低", params=[_p("天数", 20)],
                needs=["low"], warmup=_wn(1), unit="元", fn=_hhv_part("low", False),
                term=_term("最近 n 天（不算今天）的最低价，当作这段行情的地板",
                           "收盘价跌破 LLV(60)，就是跌破半年支撑",
                           "支撑破了通常说明趋势变了，比单纯跌得多更值得警惕")),
    # ---------------- 布林带 ----------------
    "BOLL_MID": dict(code="BOLL_MID", cn="布林中轨", cat="布林带", params=_pk(),
                     needs=["close"], warmup=_wn(), unit="元", fn=_boll_part("mid"),
                     term=_term("n 日收盘价平均，就是布林带的中线",
                                "BOLL_MID(20) 约等于 MA(20)", "价格在中轨之上说明这段时间整体偏强")),
    "BOLL_UP": dict(code="BOLL_UP", cn="布林上轨", cat="布林带", params=_pk(),
                    needs=["close"], warmup=_wn(), unit="元", fn=_boll_part("up"),
                    term=_term("中轨 + k 倍标准差，画出一条压力线（用总体标准差，和国内软件一致）",
                               "收盘价顶到上轨之外，通常叫超买",
                               "上轨不是天花板，强势股会贴着上轨一直走")),
    "BOLL_LOW": dict(code="BOLL_LOW", cn="布林下轨", cat="布林带", params=_pk(),
                     needs=["close"], warmup=_wn(), unit="元", fn=_boll_part("low"),
                     term=_term("中轨 - k 倍标准差，一条支撑线",
                                "跌破下轨后马上收回来，是常见的反弹信号",
                                "跌得狠不一定便宜，也可能是趋势坏了")),
}

# ------------------------------------------------------------------ 对外接口


def names() -> list[str]:
    return list(INDICATORS)


def spec(name: str) -> dict[str, Any]:
    return INDICATORS[name]


def _args_for(name: str, args: list | None) -> list:
    """缺参数时用白名单默认值补齐（只用于估算暖机，不参与计算）。"""
    params = INDICATORS[name]["params"]
    given = list(args or [])
    return [given[i] if i < len(given) else p["default"] for i, p in enumerate(params)]


def warmup(name: str, args: list | None = None) -> int:
    """这个指标至少要几根K线才出第一个有效值。首页预检和「数据不足」都靠它。"""
    w = INDICATORS[name]["warmup"]
    value = w(_args_for(name, args)) if callable(w) else w
    return int(max(0, value))


def arg_error(name: str, args: list) -> str:
    """参数不合法时返回一句人话；合法返回空字符串。校验与提示共用这一处。"""
    params = INDICATORS[name]["params"]
    if len(args) != len(params):
        return f"「{INDICATORS[name]['cn']}」需要 {len(params)} 个参数，现在给了 {len(args)} 个"
    for p_, v in zip(params, args):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return f"「{INDICATORS[name]['cn']}」的{p_['cn']}要填数字，你填的是 {v!r}"
        if p_["integer"] and f != int(f):
            return f"「{INDICATORS[name]['cn']}」的{p_['cn']}要填整数，比如 {p_['default']}"
        if f < p_["min"] or f > p_["max"]:
            return (f"「{INDICATORS[name]['cn']}」的{p_['cn']}要在 "
                    f"{p_['min']:g}~{p_['max']:g} 之间，你填的是 {f:g}")
    return ""


def compute(df: pd.DataFrame, name: str, args: list | None = None) -> pd.Series:
    """算一条指标线。返回值与 df 等长，暖机段是 NaN。"""
    if name not in INDICATORS:
        raise integrity_error("UNKNOWN_INDICATOR", f"指标 {name} 不存在", field=name)
    spec_ = INDICATORS[name]
    args = list(args or [])
    err = arg_error(name, args)
    if err:
        raise integrity_error("INDICATOR_ARG", err, field=name)
    for col in spec_["needs"]:
        if col not in df.columns:
            raise integrity_error(
                "INDICATOR_COLUMN_MISSING",
                f"算「{spec_['cn']}」要用到「{col}」，但这份K线没有这一列",
                detail="删掉这只股票的缓存重新下载即可（设置页 → 行情缓存 → 删除）。",
                field=name,
            )
    cast = [int(a) if p["integer"] else float(a) for p, a in zip(spec_["params"], args)]
    series = spec_["fn"](df, *cast)
    if not isinstance(series, pd.Series):        # 防御：指标实现误返回多列
        raise integrity_error("INDICATOR_SHAPE", f"{name} 算出来不是一条线", field=name)
    return pd.Series(series, index=df.index, dtype="float64").rename(name.lower())


def public_specs() -> list[dict[str, Any]]:
    """下发给前端的白名单（GET /api/indicators）。前端不许自己硬编码指标表。"""
    return [
        {
            "code": s["code"], "cn": s["cn"], "cat": s["cat"], "unit": s["unit"],
            "params": s["params"], "needs": s["needs"], "term": s["term"],
            "warmup": warmup(code), "param_hint": PARAM_HINT,
        }
        for code, s in INDICATORS.items()
    ]
