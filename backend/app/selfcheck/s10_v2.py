"""阶段 10 自检：V2 —— 大白话 → 策略（DeepSeek）。

验收要点：
- 🔒 门控：没填 Key 时如实说"用不了"，并给出可执行动作，不静默失败
- 红线 1：AI 输出一律不被信任，必须再过 dsl.validate() 白名单
- 红线 2：证据回显里算不出来的值是 null，不拿 0 冒充
- 红线 6：提示词里的指标表/运算符表现场从白名单生成，本文件不硬编码
- 密钥安全：status 不回传 Key，GET /api/settings 掩码成 ***
- 错误分类：401 归 input（指向字段），超时归 datasource（可重试）
- 自检绝不连外网：真调用一律用 stub 换掉（多数换 ai._chat，
  要连 _chat 一起测的就换 httpx.post）
"""

import json
from pathlib import Path

import httpx

from .. import dsl as dsl_mod
from .. import explain
from .. import indicators as ind
from .. import store
from .. import synth
from ..api import ai
from ..config import SECRET_SETTINGS
from ..errors import KIND_DATASOURCE, KIND_INPUT, KIND_PROGRAM
from .harness import check, client, eq, fresh_db, near, stub, true

FRONTEND = Path("/app/frontend")
JS = FRONTEND / "js"
FAKE_KEY = "sk-selfcheck-not-a-real-key"


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _with_key():
    """把自检库清干净并填上一把假 Key。返回 TestClient。"""
    fresh_db()
    c = client()
    r = c.post("/api/settings", json={"deepseek_api_key": FAKE_KEY})
    eq(r.status_code, 200, "填 Key 的状态码")
    return c


def _say(content):
    """替掉真调用：不管问什么，都回同一段文字。"""
    return lambda messages, settings: content


def _fenced(obj):
    """AI 最爱的形态：前后有废话，中间裹一层 ```json 围栏。"""
    return ("好的，我理解你的意思了。\n```json\n"
            + json.dumps(obj, ensure_ascii=False, indent=2)
            + "\n```\n如果还想加条件，告诉我就行。")


def _ind(name, *args, mult=None):
    op = {"t": "ind", "name": name, "args": list(args)}
    if mult is not None:
        op["mult"] = mult
    return op


def _num(v):
    return {"t": "num", "value": v}


def _good_obj():
    return {
        "signals": {
            "buy": {"logic": "and", "items": [
                {"left": _ind("MA", 5), "cmp": "cross_above", "right": _ind("MA", 20)},
                {"left": _ind("VOLUME"), "cmp": ">", "right": _ind("VOL_MA", 5, mult=1.5)},
            ]},
            "sell": {"logic": "or", "items": [
                {"left": _ind("CLOSE"), "cmp": "cross_below", "right": _ind("MA", 10)},
            ]},
        },
        "sizing": {"mode": "all_in"},
        "risk": {"stop_loss_pct": 8.0},
    }


def _err(resp):
    return resp.json()["error"]


class _FakeResp:
    """只实现 _chat 用到的三个成员，够假就行。"""

    def __init__(self, payload, status=200, text=""):
        self._payload = payload
        self.status_code = status
        self.text = text or json.dumps(payload, ensure_ascii=False)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(str(self.status_code),
                                        request=httpx.Request("POST", "https://api.deepseek.com"),
                                        response=self)

    def json(self):
        return self._payload


def _fake_post(content):
    """造一个"AI 只回了 content"的假 httpx.post，好让真的 _chat 跑一遍。"""
    return lambda *a, **k: _FakeResp({"choices": [{"message": {"content": content}}]})


# ── 🔒 门控与密钥安全 ─────────────────────────────────────


@check("10")
def t10_status_unconfigured_by_default():
    """刚装好（没填 Key）时，status 必须如实说 configured=false。"""
    fresh_db()
    r = client().get("/api/ai/status")
    eq(r.status_code, 200, "status 状态码")
    data = r.json()
    eq(data["configured"], False, "默认应未配置")


@check("10")
def t10_status_configured_after_key():
    """填了 Key 之后 configured 翻成 true，前端才肯解锁输入框。"""
    c = _with_key()
    eq(c.get("/api/ai/status").json()["configured"], True, "填 Key 后应已配置")


@check("10")
def t10_status_never_returns_the_key():
    """status 只回 configured/base_url/model，Key 本身一个字符都不能出去。"""
    c = _with_key()
    raw = c.get("/api/ai/status").text
    true(FAKE_KEY not in raw, "status 不得回传 API Key")
    eq(sorted(c.get("/api/ai/status").json()), ["base_url", "configured", "model"],
       "status 字段应固定为这三个")


@check("10")
def t10_api_key_is_masked_in_settings():
    """GET /api/settings 里 Key 掩码成 ***，只另给一个"填没填"的布尔（K9）。"""
    c = _with_key()
    data = c.get("/api/settings").json()
    eq(data["values"]["deepseek_api_key"], "***", "Key 应被掩码")
    eq(data["secret_set"]["deepseek_api_key"], True, "secret_set 要说明已填")
    true(FAKE_KEY not in json.dumps(data, ensure_ascii=False), "整个响应里都不能出现真 Key")
    eq(sorted(SECRET_SETTINGS), ["deepseek_api_key"], "密钥白名单目前只有 API Key")


@check("10")
def t10_parse_requires_key_with_actionable_hint():
    """没 Key 就点解析：400 + 指到 deepseek_api_key，话里要带"去设置页填"。"""
    fresh_db()
    r = client().post("/api/ai/parse", json={"text": "5日均线上穿20日均线买入"})
    eq(r.status_code, 400, "未配置时的状态码")
    err = _err(r)
    eq(err["kind"], KIND_INPUT, "未配置应归 input")
    eq(err["code"], "NO_API_KEY", "未配置错误码")
    eq(err.get("field"), "deepseek_api_key", "要指到具体字段，前端才能引导")
    true("设置" in err["message"], "提示里要告诉用户去哪儿填")


@check("10")
def t10_test_endpoint_requires_key():
    """设置页的 [测试] 在没 Key 时也要说清楚，不能假装测过了。"""
    fresh_db()
    err = _err(client().post("/api/ai/test"))
    eq(err["code"], "NO_API_KEY", "测试接口未配置错误码")
    eq(err.get("field"), "deepseek_api_key", "测试接口要指到字段")


# ── 输入校验 ──────────────────────────────────────────────


@check("10")
def t10_parse_rejects_empty_text():
    """空描述直接拦下，不浪费一次网络调用。"""
    c = _with_key()
    for body in ({}, {"text": ""}, {"text": "   \n "}, {"text": None}):
        err = _err(c.post("/api/ai/parse", json=body))
        eq(err["code"], "NO_TEXT", f"{body} 应报 NO_TEXT")
        eq(err["kind"], KIND_INPUT, f"{body} 分类")
        eq(err.get("field"), "text", f"{body} 要指到 text")


@check("10")
def t10_parse_rejects_overlong_text():
    """超过上限要说清上限是多少、用户写了多少，而不是含糊地报"太长"。"""
    c = _with_key()
    err = _err(c.post("/api/ai/parse", json={"text": "买" * (ai.MAX_TEXT + 1)}))
    eq(err["code"], "TEXT_TOO_LONG", "超长错误码")
    eq(err.get("field"), "text", "超长要指到 text")
    true(str(ai.MAX_TEXT) in err["message"], "提示里要写出上限数字")
    with stub(ai, "_chat", _say(_fenced(_good_obj()))):
        at_limit = c.post("/api/ai/parse", json={"text": "买" * ai.MAX_TEXT})
    eq(at_limit.status_code, 200, "正好卡在上限应放行")


# ── 红线 1：AI 输出一律过白名单 ───────────────────────────


@check("10")
def t10_parse_happy_path_with_fence_and_chatter():
    """裹了围栏、前后有废话的回复也要能解析出来，并且过完 validate()。"""
    c = _with_key()
    with stub(ai, "_chat", _say(_fenced(_good_obj()))):
        r = c.post("/api/ai/parse", json={"text": "5日均线上穿20日均线买入，放量确认，跌破10日线卖"})
    eq(r.status_code, 200, "正常解析状态码")
    data = r.json()
    eq(data["dsl"], dsl_mod.validate(_good_obj()), "解析结果应等于白名单规范化后的 DSL")
    eq(data["text"], "5日均线上穿20日均线买入，放量确认，跌破10日线卖", "要把原话回传，前端存来源")
    true(len(data["explain"]["buy_lines"]) == 2, "人话解释应覆盖 2 条买入条件")
    true(len(data["explain"]["sell_lines"]) == 1, "人话解释应覆盖 1 条卖出条件")
    true("止损" in data["explain"]["summary"], "人话解释应提到止损")


@check("10")
def t10_parse_rejects_invented_indicator():
    """AI 编了个不存在的指标（比如"市场情绪"）→ 必须被白名单拦下，绝不入库。"""
    c = _with_key()
    obj = _good_obj()
    obj["signals"]["buy"]["items"].append(
        {"left": _ind("SENTIMENT", 14), "cmp": ">", "right": _num(0.5)})
    with stub(ai, "_chat", _say(json.dumps(obj, ensure_ascii=False))):
        r = c.post("/api/ai/parse", json={"text": "再加一条市场情绪大于0.5"})
    eq(r.status_code, 500, "被拦下时的状态码")
    err = _err(r)
    eq(err["kind"], KIND_PROGRAM, "白名单拒绝归 program（不是用户的错，也不该当成数据源问题）")
    eq(err["code"], "AI_DSL_REJECTED", "拒绝错误码")
    true("SENTIMENT" in err["detail"], "详情里要留下 AI 原文，方便排查")
    true("手动" in err["message"], "要给用户一条退路")


@check("10")
def t10_parse_rejects_cross_against_constant():
    """"收盘价上穿 30 块"在数学上没意义（D6），AI 真这么说也得拦。"""
    c = _with_key()
    obj = {"signals": {"buy": {"logic": "and", "items": [
        {"left": _ind("CLOSE"), "cmp": "cross_above", "right": _num(30)}]}},
        "sizing": {"mode": "all_in"}, "risk": {}}
    with stub(ai, "_chat", _say(json.dumps(obj, ensure_ascii=False))):
        err = _err(c.post("/api/ai/parse", json={"text": "收盘价上穿30块就买"}))
    eq(err["code"], "AI_DSL_REJECTED", "常数穿越应被拦")
    true("固定数字" in err["detail"], "详情应说明是固定数字的问题")


@check("10")
def t10_parse_rejects_extra_field():
    """AI 多塞一个自创字段（例如"仓位建议"）→ 白名单不认识就整份拒。"""
    c = _with_key()
    obj = _good_obj()
    obj["position_advice"] = "满仓干"
    with stub(ai, "_chat", _say(json.dumps(obj, ensure_ascii=False))):
        err = _err(c.post("/api/ai/parse", json={"text": "顺便告诉我仓位怎么配"}))
    eq(err["code"], "AI_DSL_REJECTED", "多字段应被拦")


@check("10")
def t10_parse_rejects_non_json():
    """AI 光讲道理不给 JSON → 说人话报错，不要抛 500 堆栈给用户看。"""
    c = _with_key()
    with stub(ai, "_chat", _say("这个想法挺有意思的，不过你需要先明确一下周期。")):
        r = c.post("/api/ai/parse", json={"text": "帮我随便搞个策略"})
    err = _err(r)
    eq(err["code"], "AI_PARSE_ERROR", "非 JSON 错误码")
    eq(err["kind"], KIND_PROGRAM, "非 JSON 归 program")
    true("换种说法" in err["message"], "要给出下一步动作")


@check("10")
def t10_parse_rejects_broken_json():
    """JSON 截断（AI 输出到一半被 max_tokens 掐了）也要归到同一个错误码。"""
    c = _with_key()
    with stub(ai, "_chat", _say('{"signals": {"buy": {"logic": "and", "items": [{"lef')):
        err = _err(c.post("/api/ai/parse", json={"text": "5日均线上穿20日均线"}))
    eq(err["code"], "AI_PARSE_ERROR", "截断 JSON 错误码")


@check("10")
def t10_parse_rejects_empty_ai_reply():
    """AI 回了空内容（限流/内容审查都可能）→ 明确说"返回了空内容"。

    这里替的是 httpx.post 而不是 _chat，为的是让真的 _chat 跑一遍：
    空回复的判定写在 _chat 里，把 _chat 整个换掉就等于没测。
    """
    c = _with_key()
    with stub(httpx, "post", _fake_post("")):
        err = _err(c.post("/api/ai/parse", json={"text": "5日均线上穿20日均线"}))
    eq(err["code"], "AI_EMPTY_RESPONSE", "空回复错误码")
    eq(err["kind"], KIND_PROGRAM, "空回复归 program")


@check("10")
def t10_chat_request_matches_deepseek_contract():
    """真发出去的那一次请求：地址、鉴权头、模型、消息体都要对得上 DeepSeek 的接口。"""
    c = _with_key()
    c.post("/api/settings", json={"deepseek_base_url": "https://api.deepseek.com/",
                                  "deepseek_model": "deepseek-chat"})
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None, **kw):
        seen.update(url=url, headers=headers, body=json, timeout=timeout)
        return _FakeResp({"choices": [{"message": {"content": _fenced(_good_obj())}}]})

    with stub(httpx, "post", fake_post):
        r = c.post("/api/ai/parse", json={"text": "5日均线上穿20日均线买入"})
    eq(r.status_code, 200, "正常请求状态码")
    eq(seen["url"], "https://api.deepseek.com/chat/completions", "地址应是 base_url 拼 /chat/completions")
    eq(seen["headers"]["Authorization"], f"Bearer {FAKE_KEY}", "要带 Bearer 鉴权头")
    eq(seen["body"]["model"], "deepseek-chat", "要用设置里的模型名")
    eq(seen["timeout"], ai.TIMEOUT, "超时要和后端约定一致")
    roles = [m["role"] for m in seen["body"]["messages"]]
    eq(roles, ["system", "user"], "应带系统提示词 + 用户原话")
    eq(seen["body"]["messages"][1]["content"], "5日均线上穿20日均线买入", "用户原话要原样送出")
    true(len(seen["body"]["messages"][0]["content"]) > 500, "系统提示词应是完整白名单，不是几句话")


@check("10")
def t10_chat_unwraps_openai_envelope():
    """_chat 要能从 choices[0].message.content 里把正文取出来（含前后空白）。"""
    c = _with_key()
    with stub(httpx, "post", _fake_post("  在线  ")):
        eq(c.post("/api/ai/test").json()["reply"], "在线", "应回显去掉空白的正文")


@check("10")
def t10_chat_upstream_5xx_is_classified():
    """上游 500 不在 401/402/429 那几档里，要如实把状态码和一小段原文报出来。"""
    c = _with_key()
    req = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    resp = httpx.Response(503, request=req, text="service unavailable")

    def boom(messages, settings):
        raise httpx.HTTPStatusError("503", request=req, response=resp)

    with stub(ai, "_chat", boom):
        data = c.post("/api/ai/test").json()
    eq(data["ok"], False, "503 应报不可用")
    true("503" in data["message"], "要带上原始状态码")


# ── 错误分类：401 归 input、超时归 datasource ─────────────


def _status_error(code, text="upstream said no"):
    req = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    resp = httpx.Response(code, request=req, text=text)
    return httpx.HTTPStatusError(f"{code}", request=req, response=resp)


@check("10")
def t10_parse_401_is_input_error_on_the_key_field():
    """Key 填错/失效是用户的输入问题，要红框标到 Key 那一栏，不是"网络错误"。"""
    c = _with_key()

    def boom(messages, settings):
        raise _status_error(401)

    with stub(ai, "_chat", boom):
        r = c.post("/api/ai/parse", json={"text": "5日均线上穿20日均线买入"})
    eq(r.status_code, 400, "401 应映射成 400")
    err = _err(r)
    eq(err["kind"], KIND_INPUT, "401 归 input")
    eq(err.get("field"), "deepseek_api_key", "要指到 Key 字段")
    true("401" in err["message"], "提示里要带上原始状态码")


@check("10")
def t10_parse_402_mentions_balance():
    """余额不足要说"充值"，而不是笼统的"请求失败"。"""
    c = _with_key()

    def boom(messages, settings):
        raise _status_error(402)

    with stub(ai, "_chat", boom):
        err = _err(c.post("/api/ai/parse", json={"text": "5日均线上穿20日均线买入"}))
    true("余额" in err["message"], "402 提示应指向余额")


@check("10")
def t10_parse_timeout_is_retriable_datasource_error():
    """超时归 datasource 且 retriable=true：这是网络问题，重试就有用（PRD §5）。"""
    c = _with_key()

    def boom(messages, settings):
        raise httpx.ReadTimeout("no reply")

    with stub(ai, "_chat", boom):
        r = c.post("/api/ai/parse", json={"text": "5日均线上穿20日均线买入"})
    eq(r.status_code, 502, "超时应映射成 502")
    err = _err(r)
    eq(err["kind"], KIND_DATASOURCE, "超时归 datasource")
    eq(err["code"], "AI_TIMEOUT", "超时错误码")
    eq(err["retriable"], True, "超时应可重试")
    true(str(int(ai.TIMEOUT)) in err["message"], "要写出等了多少秒")


@check("10")
def t10_parse_connect_error_is_retriable():
    """连不上（DNS/断网/base_url 写错）同样归 datasource 可重试。"""
    c = _with_key()

    def boom(messages, settings):
        raise httpx.ConnectError("name or service not known")

    with stub(ai, "_chat", boom):
        err = _err(c.post("/api/ai/parse", json={"text": "5日均线上穿20日均线买入"}))
    eq(err["kind"], KIND_DATASOURCE, "连不上归 datasource")
    eq(err["code"], "AI_UNREACHABLE", "连不上错误码")
    eq(err["retriable"], True, "连不上应可重试")


@check("10")
def t10_test_endpoint_reports_401_without_raising():
    """设置页 [测试] 遇到 401 应返回 ok=false + 人话，而不是抛 500 弹窗。"""
    c = _with_key()

    def boom(messages, settings):
        raise _status_error(401)

    with stub(ai, "_chat", boom):
        r = c.post("/api/ai/test")
    eq(r.status_code, 200, "测试接口不应把上游错误变成 500")
    data = r.json()
    eq(data["ok"], False, "401 应报不可用")
    true("Key" in data["message"], "要说清是 Key 的问题")


@check("10")
def t10_test_endpoint_ok_path():
    """AI 正常回应时，[测试] 报 ok=true 并回显模型名与一小段回复。"""
    c = _with_key()
    with stub(ai, "_chat", _say("在线")):
        data = c.post("/api/ai/test").json()
    eq(data["ok"], True, "连通应为 true")
    eq(data["reply"], "在线", "应回显 AI 的回复")


# ── 红线 6：提示词从白名单现场生成 ────────────────────────


@check("10")
def t10_prompt_lists_every_whitelisted_indicator():
    """提示词里的指标表必须覆盖白名单里的每一个指标，一个都不能漏。"""
    prompt = ai.system_prompt()
    for code in ind.names():
        true(f"- {code}：" in prompt, f"提示词应列出指标 {code}")


@check("10")
def t10_prompt_lists_every_operator():
    """比较符表同样现场生成，且"只能比线"的约束要写进提示词。"""
    prompt = ai.system_prompt()
    for cmp_, spec in dsl_mod.CMP.items():
        true(f'"{cmp_}"' in prompt, f"提示词应列出比较符 {cmp_}")
    line_only = [c for c, s in dsl_mod.CMP.items() if s["line_only"]]
    true(bool(line_only), "白名单里应有只能比线的运算符")
    true("固定数字会被拒" in prompt, "提示词要提前告知 cross_* 不能用常数")


@check("10")
def t10_prompt_follows_the_whitelist_not_a_copy():
    """白名单一变，提示词必须跟着变 —— 这才叫"现场生成"，不是抄一份。"""
    fake = [{"code": "ZZZ_SELFCHECK", "cn": "自检假指标",
             "params": [{"cn": "天数", "default": 5, "min": 2, "max": 60, "integer": True}]}]
    with stub(ind, "public_specs", lambda: fake):
        prompt = ai.system_prompt()
    true("ZZZ_SELFCHECK" in prompt, "新增指标应立刻出现在提示词里")
    for code in ind.names():
        true(f"- {code}：" not in prompt, f"假白名单下不应再出现 {code}")


@check("10")
def t10_prompt_lists_risk_fields_with_ranges():
    """风控字段要带区间，AI 才知道 stop_loss_pct 该填 8 还是 0.08。"""
    prompt = ai.system_prompt()
    for k, spec in dsl_mod.RISK_FIELDS.items():
        true(k in prompt, f"提示词应列出风控字段 {k}")
        true(f"{spec['min']:g}~{spec['max']:g}" in prompt, f"{k} 应带取值区间")


@check("10")
def t10_few_shot_examples_are_all_valid():
    """few-shot 例子必须自己就过得了 validate()，否则等于教 AI 说错话。"""
    examples = ai._examples()
    true(len(examples) >= 2, "至少给两个例子")
    for ex in examples:
        true(bool(ex["text"].strip()), "例子要有用户原话")
        once = dsl_mod.validate(ex["json"])           # 不合法会当场抛
        eq(dsl_mod.validate(once), once, f"例子 {ex['text'][:12]} 校验应幂等")
        true(bool(explain.describe(once)["summary"]), f"例子 {ex['text'][:12]} 应能生成人话")
        true(all(c in ind.names() for c in _codes_of(once)),
             f"例子 {ex['text'][:12]} 只应用白名单里的指标")


def _codes_of(node):
    """把一份规范化 DSL 里用到的指标代码全捞出来。"""
    out = []

    def walk(n):
        if not n:
            return
        if n["t"] == "group":
            for ch in n["items"]:
                walk(ch)
            return
        for side in ("left", "right"):
            op = n[side]
            if op["t"] == "ind":
                out.append(op["name"])

    for side in ("buy", "sell"):
        walk(node["signals"].get(side))
    return out


# ── 红线 2：证据回显 ──────────────────────────────────────

# 10 根递增K线：MA(5)=17、MA(20) 算不出来（暖机不足）、RSI(14) 同样算不出来
_EV_ROWS = [synth.bar(10.0 + i) for i in range(10)]
_EV_DSL = {
    "signals": {
        "buy": {"logic": "and", "items": [
            {"left": _ind("MA", 5), "cmp": "cross_above", "right": _ind("MA", 20)},
            {"left": _ind("VOLUME"), "cmp": ">", "right": _ind("VOL_MA", 5, mult=1.5)},
        ]},
        "sell": {"logic": "or", "items": [
            {"left": _ind("RSI", 14), "cmp": ">", "right": _num(70)},
        ]},
    },
    "sizing": {"mode": "all_in"},
    "risk": {"stop_loss_pct": 8.0},
}


def _evidence(c, dsl=None, code="600519", period="daily", bars=None):
    df = synth.make_bars(_EV_ROWS if bars is None else bars)
    with stub(store, "get_kline", lambda *a, **k: df):
        return c.post("/api/ai/evidence",
                      json={"dsl": dsl if dsl is not None else _EV_DSL,
                            "code": code, "period": period})


@check("10")
def t10_evidence_returns_real_numbers():
    """证据里的每个数字都必须等于指标层在同一份K线上算出来的值。"""
    c = _with_key()
    r = _evidence(c)
    eq(r.status_code, 200, "证据接口状态码")
    data = r.json()
    eq(data["code"], "600519", "应回传股票代码")
    eq(data["period"], "daily", "应回传周期")
    eq(data["as_of"], str(synth.make_bars(_EV_ROWS).index[-1]), "应回传数据截止日")
    got = {i["key"]: i["value"] for i in data["items"]}
    near(got["MA(5)"], 17.0, 1e-9, "MA(5) 应为后 5 根收盘均值")
    # 无参指标的键是 "VOLUME()"：空括号也得留着，否则和带参的同名指标会撞车
    near(got["VOLUME()"], synth.DEFAULT_VOLUME, 1e-9, "成交量应原样回显")
    near(got["VOL_MA(5)*1.5"], synth.DEFAULT_VOLUME * 1.5, 1e-9, "倍数要算进证据值")
    near(got["num:70"], 70.0, 1e-9, "常数操作数也要列出来核对")


@check("10")
def t10_evidence_nan_is_null_not_zero():
    """暖机不足算不出来的线 → null，绝不填 0 冒充（红线 2 / F17）。"""
    c = _with_key()
    got = {i["key"]: i["value"] for i in _evidence(c).json()["items"]}
    eq(got["MA(20)"], None, "只有 10 根K线，MA(20) 应如实说算不出来")
    eq(got["RSI(14)"], None, "只有 10 根K线，RSI(14) 应如实说算不出来")
    true(all(v is None or isinstance(v, (int, float)) for v in got.values()),
         "证据值只能是数字或 null，不能是字符串")


@check("10")
def t10_evidence_labels_are_human_readable():
    """标签必须是人话（"5日均线"），不能把 MA(5) 这种代码直接糊给小白用户。"""
    c = _with_key()
    items = _evidence(c).json()["items"]
    true(len(items) >= 5, "证据应覆盖规则里引用的每条线")
    for it in items:
        label = it["label"]
        true(bool(label.strip()), f"{it['key']} 的标签不能为空")
        true(it["key"] not in label, f"{it['key']} 的标签不该是原始代码：{label}")
        if not it["key"].startswith("num:"):
            true(any("\u4e00" <= ch <= "\u9fff" for ch in label),
                 f"{it['key']} 的标签应含中文：{label}")
    eq(ai._label("num:30"), "固定数字 30", "常数标签")
    true("倍" in ai._label("VOL_MA(5)*1.5"), "倍数标签要说清是几倍")


@check("10")
def t10_evidence_validates_dsl_first():
    """证据接口自己也要过白名单：不能因为"只是看看数"就放行脏 DSL。"""
    c = _with_key()
    bad = {"signals": {"buy": {"logic": "and", "items": [
        {"left": _ind("SENTIMENT", 5), "cmp": ">", "right": _num(1)}]}},
        "sizing": {"mode": "all_in"}, "risk": {}}
    err = _err(_evidence(c, dsl=bad))
    eq(err["kind"], KIND_INPUT, "脏 DSL 归 input")
    true("DSL" in err["code"] or "INVALID" in err["code"], f"错误码应指向 DSL，实际 {err['code']}")


@check("10")
def t10_evidence_without_data_says_so():
    """这只票一行行情都没有 → 明确说"取不到证据"，不返回一张空表让人以为都是 0。"""
    c = _with_key()
    with stub(store, "get_kline", lambda *a, **k: synth.make_bars([])):
        err = _err(c.post("/api/ai/evidence",
                          json={"dsl": _EV_DSL, "code": "600519", "period": "daily"}))
    eq(err["code"], "NO_DATA", "无数据错误码")
    eq(err.get("field"), "code", "要指到股票代码那一栏")


@check("10")
def t10_evidence_rejects_bad_code_and_period():
    """代码和周期照旧走白名单校验。"""
    c = _with_key()
    eq(_evidence(c, code="abcdef").status_code, 400, "非法代码应被拒")
    eq(_evidence(c, period="tick").status_code, 400, "非法周期应被拒")


# ── 前端结构（🔒 原则：不隐藏、不禁用到看不见；确认才载入）──


@check("10")
def t10_frontend_parse_does_not_auto_apply():
    """解析成功不许直接改规则，必须等用户点「确认规则无误」（场景 F）。"""
    src = _read(JS / "index.js")
    true("确认规则无误" in src, "应有确认按钮")
    true("_confirmNl(data)" in src, "确认按钮应接到 _confirmNl")
    # 逐函数看：解析回调与渲染函数里都不许出现 setDsl，只有确认函数里可以有
    for fn in ("_parseNl", "_renderNlResult", "_loadEvidence"):
        true("setDsl" not in _fn(src, fn), f"{fn} 里不许直接载入规则")
    true("conditionBuilder.setDsl" in _fn(src, "_confirmNl"),
         "载入规则只应发生在 _confirmNl 里")


def _fn(src, name):
    """截出 index.js 里某个顶层函数的函数体（都是 2 空格缩进）。"""
    start = src.index(f"function {name}(")
    end = src.find("\n  function ", start + 1)
    return src[start:] if end < 0 else src[start:end]


@check("10")
def t10_frontend_asks_for_key_instead_of_hiding():
    """没 Key 时前端要给"去设置"这个动作，而不是把入口悄悄藏掉（🔒 原则）。"""
    src = _read(JS / "index.js")
    true("/api/ai/status" in src, "应查询 AI 配置状态")
    true("去设置" in src, "应有引导去设置页的按钮")
    true("settings.html" in src, "引导要真能跳到设置页")
    true("手动搭条件" in src, "要说清不填 Key 也能用哪些入口")


@check("10")
def t10_frontend_uses_longer_timeout_for_ai():
    """AI 调用比 30 秒默认超时久，前端必须显式传更长的超时，否则先自己 abort。"""
    src = _read(JS / "api.js")
    true("timeoutMs" in src, "api.js 应支持单次请求自定义超时")
    idx = _read(JS / "index.js")
    for call in ('"/api/ai/parse"', '"/api/ai/evidence"'):
        line = [l for l in idx.splitlines() if call in l]
        true(bool(line), f"index.js 应调用 {call}")
        true(any("," in l.split(call)[1] for l in line),
             f"{call} 的调用应带上超时参数")


@check("10")
def t10_settings_page_has_ai_group_and_test_button():
    """设置页要有 AI 分组和「测试 AI 连通性」按钮（真发请求，不是只看填没填）。"""
    src = _read(JS / "settings.js")
    true("/api/ai/test" in src, "设置页应调用连通性测试接口")
    true("测试 AI 连通性" in src, "应有测试按钮文案")
    true("ai" in src, "应有 AI 分组")
    true("platform.deepseek.com" in src, "要告诉小白去哪儿申请 Key")


@check("10")
def t10_no_cdn_in_ai_frontend():
    """AI 相关前端文件不得引入外部 CDN。"""
    for name in ["index.html", "settings.html", "js/index.js", "js/settings.js", "js/api.js"]:
        src = _read(FRONTEND / name)
        for host in ("cdnjs", "jsdelivr", "unpkg"):
            true(host not in src, f"{name} 不应引用 {host}")
