"""AI 接口：大白话 → 受限 DSL（PRD §3.2 场景 F，V2）。

一条不可让步的原则（红线 1）：**DeepSeek 的输出一律不被信任**。它返回的 JSON 必须
再过一遍 `dsl.validate()`——字段、指标名、运算符、参数区间全部走白名单，越界就拒。
AI 只是"帮用户填表的手"，不是能改规则的人。

提示词里的指标表/运算符表由 `indicators.public_specs()` 与 `dsl.CMP` 现场生成，
不在本文件硬编码（红线 6）：白名单一改，AI 能说的话跟着变，不会出现"提示词说能用、
校验说不能用"的裂缝。
"""

import json
import logging

import httpx
from fastapi import APIRouter, Body

from .. import dsl as dsl_mod
from .. import explain
from .. import indicators as ind
from .. import store
from .. import templates as tpl_mod
from ..db import get_settings
from ..errors import KIND_PROGRAM, AppError, input_error, program_error
from .common import check_code, check_period

log = logging.getLogger("app")
router = APIRouter()

MAX_TEXT = 500
TIMEOUT = 60.0

_RULES = """你只能输出一个 JSON 对象，不要有任何解释文字、不要 markdown 代码块。

JSON 形状（字段名必须一字不差，多一个字段都会被拒）：
{
  "signals": {
    "buy":  {"logic": "and|or", "items": [条件, ...]},
    "sell": {"logic": "and|or", "items": [条件, ...]}
  },
  "sizing": {"mode": "all_in"},
  "risk": {风控字段}
}

条件 = {"left": 操作数, "cmp": 比较符, "right": 操作数}
操作数只有两种形态：
  指标：{"t": "ind", "name": 指标代码, "args": [参数...]}，可选 "mult": 倍数（如 1.5 表示 1.5 倍）
  数字：{"t": "num", "value": 数值}

条件组可以嵌套，但最多 2 层。用户没提卖出条件时，"sell" 直接给 null。
"""


def _indicator_lines() -> str:
    rows = []
    for s in ind.public_specs():
        params = s["params"]
        if not params:
            rows.append(f"- {s['code']}：{s['cn']}，args 必须写成 []")
            continue
        desc = "、".join(
            f"{p['cn']}（默认 {p['default']:g}，范围 {p['min']:g}~{p['max']:g}"
            + ("，要整数" if p["integer"] else "") + "）"
            for p in params
        )
        rows.append(f"- {s['code']}：{s['cn']}，args 必须正好 {len(params)} 个：{desc}")
    return "\n".join(rows)


def _cmp_lines() -> str:
    return "\n".join(
        f'- "{c}"：{s["cn"]}'
        + ("（左右都必须是指标，一边是固定数字会被拒）" if s["line_only"] else "")
        for c, s in dsl_mod.CMP.items()
    )


def _risk_lines() -> str:
    return "\n".join(
        f"- {k}：{v['cn']}，范围 {v['min']:g}~{v['max']:g} {v['unit']}"
        + ("，要整数" if v.get("integer") else "")
        + "，用户没提就填 null"
        for k, v in dsl_mod.RISK_FIELDS.items()
    )


def _examples() -> list[dict]:
    """few-shot 直接用内置模板：它们本来就过得了 validate()，不会教坏 AI。"""
    picks = [("ma_cross", "5日均线上穿20日均线就买，跌破就卖，亏8%止损"),
             ("rsi_rebound", "RSI低于30而且当天收阳线才买，RSI高于70卖，止损8%，最多拿20天")]
    out = []
    for tid, text in picks:
        t = tpl_mod.by_id(tid)
        if t:
            out.append({"text": text, "json": t["dsl"]})
    return out


def system_prompt() -> str:
    """现场拼提示词。白名单是唯一事实来源，改指标不用改这里。"""
    parts = [
        "你是 A 股策略构建助手。用户用大白话描述买卖想法，你把它翻译成下面这套受限 DSL。",
        _RULES,
        "可用的指标（只能用这些，一个都不能自己编）：",
        _indicator_lines(),
        "",
        "可用的比较符：",
        _cmp_lines(),
        "",
        f"风控字段（sizing.mode 只有 \"all_in\" 一种）：",
        _risk_lines(),
        "",
        "例子：",
    ]
    for ex in _examples():
        parts.append(f"用户说：{ex['text']}")
        parts.append("你输出：" + json.dumps(ex["json"], ensure_ascii=False))
    parts.append("现在处理用户的描述。只输出 JSON。")
    return "\n".join(parts)


def ai_status() -> dict:
    """前端 🔒 判定用。绝不回传 Key 本身。"""
    s = get_settings()
    return {
        "configured": bool(s.get("deepseek_api_key")),
        "base_url": s.get("deepseek_base_url", ""),
        "model": s.get("deepseek_model", ""),
    }


def _chat(messages: list[dict], settings: dict) -> str:
    """真去调一次 DeepSeek。抽成模块级函数是为了自检能替换掉它，不连外网。"""
    base = (settings.get("deepseek_base_url") or "https://api.deepseek.com").rstrip("/")
    resp = httpx.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {settings['deepseek_api_key']}"},
        json={
            "model": settings.get("deepseek_model") or "deepseek-chat",
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": 2000,
        },
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    choices = data.get("choices") or []
    content = (choices[0].get("message") or {}).get("content") if choices else None
    if not content or not str(content).strip():
        raise program_error("AI_EMPTY_RESPONSE", "AI 返回了空内容，请重试或换种说法。")
    return str(content)


def _extract_json(content: str) -> dict:
    """AI 常爱包一层 ```json 围栏或前后加几句话，这里只取第一个完整 JSON 对象。"""
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise program_error("AI_PARSE_ERROR", "AI 没有返回能看懂的规则，请换种说法再试一次。",
                            detail=content[:500])
    try:
        obj = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise program_error("AI_PARSE_ERROR", "AI 返回的内容不是合法 JSON，请再试一次。",
                            detail=f"{exc}\n{content[:500]}") from exc
    if not isinstance(obj, dict):
        raise program_error("AI_PARSE_ERROR", "AI 返回的不是一个规则对象，请再试一次。")
    return obj


@router.get("/ai/status")
def status() -> dict:
    return ai_status()


@router.post("/ai/test")
def test_connection() -> dict:
    """设置页的 [测试]：真发一次最小请求，而不是只看 Key 填没填。"""
    s = get_settings()
    if not s.get("deepseek_api_key"):
        raise input_error("NO_API_KEY", "还没有填 DeepSeek API Key。", field="deepseek_api_key")
    try:
        content = _chat([{"role": "user", "content": "只回复两个字：在线"}], s)
    except httpx.HTTPStatusError as exc:
        return _classify_http(exc)
    except httpx.TimeoutException:
        return {"ok": False, "message": f"连不上，超过 {TIMEOUT:g} 秒没有回应。检查网络或 base_url。"}
    except httpx.HTTPError as exc:
        return {"ok": False, "message": f"请求发不出去：{exc}"}
    return {"ok": True, "message": "连通正常，AI 已回应。",
            "model": s.get("deepseek_model"), "reply": content.strip()[:50]}


def _classify_http(exc: httpx.HTTPStatusError) -> dict:
    code = exc.response.status_code
    if code in (401, 403):
        return {"ok": False, "message": f"Key 被拒绝（{code}）。多半是填错了或已失效。"}
    if code == 402:
        return {"ok": False, "message": "账户余额不足（402），充值后再试。"}
    if code == 429:
        return {"ok": False, "message": "请求太频繁或超出配额（429），稍后再试。"}
    return {"ok": False, "message": f"接口返回 {code}：{exc.response.text[:200]}"}


@router.post("/ai/parse")
def parse(payload: dict = Body(...)) -> dict:
    """大白话 → 校验过的 DSL + 人话解释。"""
    text = str(payload.get("text") or "").strip()
    if not text:
        raise input_error("NO_TEXT", "先写一句你想怎么买卖，比如「5日均线上穿20日均线买入」。",
                          field="text")
    if len(text) > MAX_TEXT:
        raise input_error("TEXT_TOO_LONG", f"一次最多 {MAX_TEXT} 字，你写了 {len(text)} 字。"
                                          f"太长 AI 容易抓不住重点，拆成几次说更准。", field="text")

    s = get_settings()
    if not s.get("deepseek_api_key"):
        raise input_error("NO_API_KEY",
                          "这个功能需要 DeepSeek API Key，去设置页填一下就能用（充值 5 元够用很久）。",
                          field="deepseek_api_key")

    try:
        content = _chat([{"role": "system", "content": system_prompt()},
                         {"role": "user", "content": text}], s)
    except httpx.HTTPStatusError as exc:
        bad = _classify_http(exc)
        raise input_error("AI_UPSTREAM", bad["message"], field="deepseek_api_key") from exc
    except httpx.TimeoutException as exc:
        raise AppError("datasource", "AI_TIMEOUT", f"AI 超过 {TIMEOUT:g} 秒没回应。",
                       retriable=True) from exc
    except httpx.HTTPError as exc:
        raise AppError("datasource", "AI_UNREACHABLE", "连不上 AI 接口。",
                       detail=str(exc), retriable=True) from exc

    obj = _extract_json(content)

    # 红线 1：AI 说的不算，白名单说了算。
    try:
        clean = dsl_mod.validate(obj)
    except AppError as exc:
        log.warning("AI 产出未通过白名单：%s", exc)
        raise AppError(KIND_PROGRAM, "AI_DSL_REJECTED",
                       "AI 给的规则不符合本系统的白名单，已拦下。换种说法再试，或用「手动搭条件」。",
                       detail=f"{exc.message}\n字段：{exc.field or '-'}\nAI 原文：{content[:500]}") from exc

    return {"dsl": clean, "explain": explain.describe(clean), "text": text}


@router.post("/ai/evidence")
def evidence(dsl: dict = Body(..., embed=True), code: str = Body(..., embed=True),
             period: str = Body("daily", embed=True)) -> dict:
    """证据回显：把规则引用到的每条线，在真实行情上的最新值摆出来给用户核对。"""
    clean = dsl_mod.validate(dsl)
    code, period = check_code(code), check_period(period)

    df = store.get_kline(code, period, "", "")
    if df is None or df.empty:
        raise input_error("NO_DATA", f"{code} 还没有行情数据，取不到证据。", field="code")

    lines = dsl_mod.lines_of(clean, df)
    last_ts = str(df.index[-1])
    items = []
    for key, series in lines.items():
        value = series.iloc[-1]
        items.append({
            "key": key,
            "label": _label(key),
            # 暖机段没数据时如实说"算不出来"，不填 0 冒充（红线 2）
            "value": None if value != value else round(float(value), 3),
        })
    return {"code": code, "period": period, "as_of": last_ts, "items": items}


def _label(key: str) -> str:
    """把 "MA(5)" / "VOL_MA(5)*1.5" / "num:30" 说成人话。"""
    if key.startswith("num:"):
        return f"固定数字 {key[4:]}"
    mult = ""
    if "*" in key:
        key, m = key.rsplit("*", 1)
        mult = f"的{m}倍"
    name, _, argstr = key.partition("(")
    args = [a for a in argstr.rstrip(")").split(",") if a]
    try:
        text = explain.say_operand({"t": "ind", "name": name,
                                    "args": [float(a) for a in args]})
    except (KeyError, ValueError, TypeError):
        text = key
    return text + mult
