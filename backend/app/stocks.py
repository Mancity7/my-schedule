"""股票列表：全A代码/名称/市场 + 拼音首字母，支持 `gzmt` 搜到 600519。

实测（docs/数据源实测记录.md §2.1）：免费源只有代码和名称，没有行业，
所以 `industry` 列一律留空，界面上不显示"行业"，也不许按行业筛选。
"""

import re

from pypinyin import Style, lazy_pinyin

from .datasource import fetch_stock_list
from .db import connect, now
from .errors import input_error

_PINYIN_RE = re.compile(r"^[a-z]+$")


def pinyin_of(name: str) -> str:
    """贵州茅台 -> gzmt；TCL科技 -> tclkj；重庆银行 -> cqyh。

    必须整串送进 pypinyin，不能一个字一个字转：多音字要靠词组上下文才读得对
    （"重庆" 逐字转会变成 "zhong"，"银行" 会变成 "xing"），拼错就搜不到。
    字母保留、其余非字母字符（`*`、空格、数字）丢掉——它们不是用户会输入的东西。
    """
    syllables = lazy_pinyin(str(name), style=Style.FIRST_LETTER)
    return "".join(c for s in syllables for c in s if c.isascii() and c.isalpha()).lower()


def sync_stock_list(force: bool = False) -> dict:
    """拉全A股票列表写进 stock_basic。返回 {count, downloaded, at}。"""
    with connect() as conn:
        have = int(conn.execute("SELECT COUNT(*) FROM stock_basic").fetchone()[0] or 0)
    if have and not force:
        return {"count": have, "downloaded": False, "at": last_sync()}

    df = fetch_stock_list()
    today = now()
    rows = [
        (str(r.code), str(r.name), str(r.market), "", pinyin_of(str(r.name)), today)
        for r in df.itertuples(index=False)
    ]
    with connect() as conn:
        conn.executemany(
            "INSERT INTO stock_basic(code, name, market, industry, pinyin, updated_at) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(code) DO UPDATE SET "
            "name=excluded.name, market=excluded.market, pinyin=excluded.pinyin, "
            "updated_at=excluded.updated_at",
            rows,
        )
    return {"count": len(rows), "downloaded": True, "at": today}


def last_sync() -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT MAX(updated_at) t FROM stock_basic").fetchone()
    return row["t"]


def ensure_stock_list() -> int:
    """股票列表是搜索的前置条件；空库时自动同步一次，避免"开箱就报错"。"""
    with connect() as conn:
        n = int(conn.execute("SELECT COUNT(*) FROM stock_basic").fetchone()[0] or 0)
    if n:
        return n
    return int(sync_stock_list()["count"])


def search_stocks(kw: str, limit: int = 8) -> list[dict]:
    """三种匹配：代码前缀 / 名称子串 / 拼音首字母前缀。命中越靠前越像用户想要的。"""
    kw = str(kw or "").strip()
    if not kw:
        return []
    if len(kw) > 20:
        raise input_error("KW_TOO_LONG", "搜索内容太长了，请少写一点。", field="kw")
    ensure_stock_list()
    # 直接去掉通配符：SQLite 的 LIKE 转义要写 ESCAPE 子句，而用户输入里的 % 和 _
    # 本来也不可能是股票代码或名称的一部分。
    like = kw.translate(str.maketrans("", "", "%_"))
    is_code = kw.isdigit()
    is_pinyin = bool(_PINYIN_RE.match(kw.lower()))

    sql = """
        SELECT code, name, market, pinyin,
               CASE
                 WHEN code = :kw                          THEN 0
                 WHEN :is_code   AND code LIKE :pfx       THEN 1
                 WHEN :is_pinyin AND pinyin = :low        THEN 2
                 WHEN :is_pinyin AND pinyin LIKE :pfx     THEN 3
                 WHEN name LIKE :sub                      THEN 4
                 WHEN :is_pinyin AND pinyin LIKE :sub     THEN 5
                 ELSE 9
               END AS prio
        FROM stock_basic
        WHERE (:is_code AND code LIKE :pfx)
           OR (:is_pinyin AND pinyin LIKE :pfx)
           OR (:is_pinyin AND pinyin LIKE :sub)
           OR name LIKE :sub
        ORDER BY prio, code
        LIMIT :limit
    """
    with connect() as conn:
        rows = conn.execute(sql, {
            "kw": kw, "low": kw.lower(), "pfx": f"{like}%", "sub": f"%{like}%",
            "is_code": 1 if is_code else 0, "is_pinyin": 1 if is_pinyin else 0,
            "limit": max(1, min(int(limit or 8), 50)),
        }).fetchall()
    return [
        {"code": r["code"], "name": r["name"], "market": r["market"], "pinyin": r["pinyin"]}
        for r in rows if r["prio"] < 9
    ]


def get_stock(code: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM stock_basic WHERE code = ?",
                           (str(code).strip(),)).fetchone()
    return dict(row) if row else None


def stocks_of(codes) -> dict[str, dict]:
    """批量取名称，给结果列表和图表标题用。"""
    codes = [str(c).strip() for c in codes if c]
    if not codes:
        return {}
    marks = ",".join("?" * len(codes))
    with connect() as conn:
        rows = conn.execute(
            f"SELECT code, name, market FROM stock_basic WHERE code IN ({marks})", codes
        ).fetchall()
    return {r["code"]: dict(r) for r in rows}


def is_st(name: str) -> bool:
    return "ST" in str(name).upper()
