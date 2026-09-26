"""接口层公共校验：所有用户输入进来的日期和代码都先过这里。"""

import re
from datetime import date, datetime

from ..errors import input_error

DATE_RE = re.compile(r"^\d{4}-?\d{2}-?\d{2}$")
CODE_RE = re.compile(r"^\d{6}$")


_FIELD_CN = {
    "start_date": "开始日期", "end_date": "结束日期", "date": "日期",
    "code": "股票代码", "codes": "股票代码", "name": "策略名称",
    "period": "周期", "dsl": "策略规则", "note": "备注",
}


def check_date(value: str, field: str = "date") -> str:
    s = str(value or "").strip()
    cn = _FIELD_CN.get(field, field)
    if not s:
        raise input_error("BAD_DATE", f"「{cn}」不能为空，请填写日期。", field=field)
    if not DATE_RE.match(s):
        raise input_error("BAD_DATE", f"「{cn}」格式不对（{s}），要写成 2026-01-05 这样。", field=field)
    try:
        d = datetime.strptime(s.replace("-", "")[:8], "%Y%m%d").date()
    except ValueError:
        raise input_error("BAD_DATE", f"「{cn}」的 {s} 不是一个真实存在的日期。", field=field) from None
    return d.strftime("%Y-%m-%d")


def check_range(start: str, end: str) -> tuple[str, str]:
    """回测/取数区间。结束日可以是未来（等同"到今天"），开始日不能晚于结束日。"""
    s, e = check_date(start, "start_date"), check_date(end, "end_date")
    if s > e:
        raise input_error("DATE_ORDER", "开始日期不能晚于结束日期。", field="start_date")
    if s < "1990-12-19":
        raise input_error("DATE_TOO_EARLY", "A股 1990-12-19 才开市，开始日期不能更早。",
                          field="start_date")
    if e > date.today().strftime("%Y-%m-%d"):
        e = date.today().strftime("%Y-%m-%d")
    return s, e


def check_code(code: str, field: str = "code") -> str:
    s = str(code or "").strip()
    if not CODE_RE.match(s):
        raise input_error("BAD_CODE", f"股票代码要填 6 位数字，收到的是「{s}」。", field=field)
    return s


def check_codes(codes, field: str = "codes", max_count: int = 50) -> list[str]:
    if isinstance(codes, str):
        codes = [c for c in re.split(r"[,\s;，、]+", codes) if c]
    out = [check_code(c, field) for c in codes]
    out = list(dict.fromkeys(out))
    if not out:
        raise input_error("NO_STOCK", "还没有选择任何股票。", field=field)
    if len(out) > max_count:
        raise input_error("TOO_MANY_STOCKS",
                          f"一次最多 {max_count} 只，你选了 {len(out)} 只。", field=field)
    return out


def check_period(period: str) -> str:
    """周期只有三种写法：日线 / 周线 / 月线（周月线由日线派生）。"""
    p = str(period or "daily").strip().lower()
    if p not in ("daily", "weekly", "monthly"):
        raise input_error("BAD_PERIOD", f"不认识的周期 {p}，可选：daily / weekly / monthly。",
                          field="period")
    return p


def check_strategy_name(name: str, max_len: int = 40) -> str:
    s = str(name or "").strip()
    if not s:
        raise input_error("NO_NAME", "还没有填策略名称。", field="name")
    if len(s) > max_len:
        raise input_error("NAME_TOO_LONG",
                          f"策略名最长 {max_len} 个字，现在有 {len(s)} 个。", field="name")
    return s


def check_note(note: str, max_len: int = 200) -> str:
    s = str(note or "").strip()
    if len(s) > max_len:
        raise input_error("NOTE_TOO_LONG",
                          f"备注最长 {max_len} 个字，现在有 {len(s)} 个。", field="note")
    return s
