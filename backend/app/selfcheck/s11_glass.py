"""阶段 11 自检：Liquid Glass 视觉层（双主题 + 玻璃材质 + 大圆角 + 200-400ms 动效）。

这一阶段只换皮肤、不动功能，所以断言全落在源码上。要防的是四类回归：

1. 写死颜色。往 components.css 里塞回一个 #f0f1f3，在浅色下毫无破绽，
   切到深色就是一块刺眼的白 —— 只在浅色里点一遍是永远测不出来的。
2. 深浅两套令牌不同步。深色令牌在 base.css 里写了两遍（手动深色走属性选择器，
   跟随系统走媒体查询），改一处忘另一处，"自动"和"深色"就会长得不一样。
3. 出现天气/天体元素。本次重构只借 macOS 天气 App 的材质语言，硬约束是
   不许出现任何气象或天体意象 —— 连图标、emoji 都不行（所以主题切换用的是文字）。
   现有界面里的 ✓ ✗ ⚠ 🔒 是对勾/警告/锁，与气象无关，不在禁止之列。
4. 换主题后图表不跟着变。图表画在 canvas 上，颜色是 setOption 时定死的，
   必须显式重刷；而且不能 dispose 重建 —— 外面存着实例引用。
"""

import re
from pathlib import Path

from .harness import check, eq, true

FRONTEND = Path("/app/frontend")
CSS = FRONTEND / "css"
JS = FRONTEND / "js"
PAGES = ["index.html", "backtest.html", "tasks.html", "settings.html"]


def _read(p: Path) -> str:
    true(p.exists(), f"文件存在: {p.name}")
    return p.read_text(encoding="utf-8")


def _plain(css: str) -> str:
    """去掉注释再扫，免得注释里提到的东西被当成代码。"""
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _block(css: str, selector: str) -> str:
    """截出某个选择器的声明块（按花括号配对，能跳过嵌套的 @media）。"""
    src = _plain(css)
    m = re.search(re.escape(selector) + r"\s*\{", src)
    true(m is not None, f"css 里有 {selector} 规则块")
    if not m:
        return ""
    depth, start = 0, m.end() - 1
    for j in range(start, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start + 1:j]
    return ""


def _decls(block: str) -> dict:
    out = {}
    for d in block.split(";"):
        if ":" in d:
            k, v = d.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def _tokens(block: str) -> dict:
    return {k: " ".join(v.split()) for k, v in _decls(block).items() if k.startswith("--")}


def _base() -> str:
    return _read(CSS / "base.css")


def _dark_media(css: str) -> str:
    m = re.search(r"@media \(prefers-color-scheme: dark\)\s*\{(.*?)\n\}", css, re.S)
    true(m is not None, "base.css 有 prefers-color-scheme: dark 的媒体查询")
    return m.group(1) if m else ""


# ── 令牌层 ────────────────────────────────────────────────

@check("11")
def t11_token_scale_meets_the_spec():
    """圆角 12-20px、动效 200-400ms，是这次重构明确写下来的数字指标。"""
    d = _tokens(_block(_base(), ":root"))
    for name in ("--radius", "--radius-sm", "--radius-lg"):
        true(name in d, f"要有 {name}")
        px = float(d.get(name, "0px").replace("px", ""))
        true(12 <= px <= 20, f"{name} 要落在 12-20px，实际 {d.get(name)}")
    for name in ("--t-fast", "--t-mid", "--t-slow"):
        true(name in d, f"要有 {name}")
        ms = float(d.get(name, "0ms").replace("ms", ""))
        true(200 <= ms <= 400, f"{name} 要落在 200-400ms，实际 {d.get(name)}")
    true("--glass-blur" in d and "blur(" in d["--glass-blur"], "要有毛玻璃的 blur 令牌")
    true("--glass-sheen" in d and "inset" in d["--glass-sheen"],
         "要有玻璃顶部高光令牌（inset 阴影）")


@check("11")
def t11_up_is_red_and_down_is_green_in_both_themes():
    """红涨绿跌是 A 股习惯，和西方相反。两套主题都得守住。
    这里判色相而不是钉死十六进制 —— 深色下本来就该换成更亮的色值。"""
    css = _base()
    blocks = (("浅色", _block(css, ":root")),
              ("手动深色", _block(css, ':root[data-theme="dark"]')),
              ("跟随系统深色", _block(_dark_media(css), ':root:not([data-theme="light"])')))
    for label, blk in blocks:
        d = _tokens(blk)
        for name, want in (("--up", "红"), ("--down", "绿")):
            true(name in d, f"{label}要有 {name}")
            h = d.get(name, "").lstrip("#")
            true(len(h) == 6, f"{label} {name} 要是六位十六进制，实际 {d.get(name)}")
            r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
            if want == "红":
                true(r > g and r > b, f"{label} {name} 要偏红（涨），实际 {d.get(name)}")
            else:
                true(g > r and g > b, f"{label} {name} 要偏绿（跌），实际 {d.get(name)}")


@check("11")
def t11_dark_tokens_are_in_sync():
    """深色令牌写了两遍，两处必须完全一致。
    不然"自动"和手动选"深色"会长得不一样，而这种分歧只在特定系统设置下才露脸。"""
    css = _base()
    manual = _tokens(_block(css, ':root[data-theme="dark"]'))
    auto = _tokens(_block(_dark_media(css), ':root:not([data-theme="light"])'))
    eq(sorted(set(manual) ^ set(auto)), [], "两处深色令牌的名字要一模一样")
    diff = {k: (manual[k], auto[k]) for k in manual if manual[k] != auto.get(k)}
    eq(diff, {}, "两处深色令牌的取值要一模一样")


@check("11")
def t11_dark_only_overrides_known_tokens():
    """深色块里冒出一个浅色没定义的名字，基本就是拼错了 —— 它不会报错，
    只会让某个组件在深色下悄悄用回浅色值。"""
    css = _base()
    light = _tokens(_block(css, ":root"))
    dark = _tokens(_block(css, ':root[data-theme="dark"]'))
    true(len(light) >= 30, f"浅色令牌不少于 30 个，实际 {len(light)}")
    eq(sorted(set(dark) - set(light)), [], "深色块里的令牌名都得在浅色块里定义过")


# ── 颜色只能来自令牌 ──────────────────────────────────────

_HEX = re.compile(r"#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\b")


@check("11")
def t11_no_hardcoded_colors_in_component_or_page_css():
    """#fff 放行：那是实心按钮上的白字和玻璃顶部高光，两套主题下都该是白的。
    其余一律得走令牌。"""
    for rel in ("components.css", "pages.css"):
        bad = [m.group(0) for m in _HEX.finditer(_plain(_read(CSS / rel)))
               if m.group(0).lower() not in ("#fff", "#ffffff")]
        eq(bad, [], f"{rel} 不该写死颜色（只允许 #fff）")


@check("11")
def t11_no_hardcoded_colors_in_html():
    """内联 style 里也不许写死颜色，否则深色下漏白底。"""
    for name in PAGES:
        bad = _HEX.findall(_read(FRONTEND / name))
        eq(bad, [], f"{name} 不该写死颜色")


@check("11")
def t11_no_hardcoded_colors_in_page_scripts():
    """charts.js 的兜底调色板是唯一的例外（CSS 没加载时才用），
    而且它自己另有一条测试盯着，必须和浅色令牌一致。"""
    for f in sorted(JS.glob("*.js")):
        if f.name in ("charts.js", "echarts.min.js"):
            continue
        bad = _HEX.findall(_read(f))
        eq(bad, [], f"{f.name} 不该写死颜色")


@check("11")
def t11_charts_fallback_matches_the_light_tokens():
    """charts.js 那份兜底调色板必须和 base.css 的浅色令牌一致，
    否则"CSS 没加载"这条兜底路径会画出另一套配色，红绿都可能反。"""
    light = _tokens(_block(_base(), ":root"))
    pairs = re.findall(r'"(--[\w-]+)"\s*:\s*"([^"]+)"', _read(JS / "charts.js"))
    colors = 0
    for name, val in pairs:
        true(name in light, f"charts.js 兜底里的 {name} 在 base.css 里不存在")
        base = light.get(name, "")
        if base.startswith("#") or base.startswith("rgb"):
            colors += 1
            eq(" ".join(val.split()).lower(), base.lower(), f"{name} 兜底值要和浅色令牌一致")
    true(colors >= 14, f"兜底的颜色令牌不少于 14 个，实际 {colors}")


# ── 材质分工：外壳玻璃，数据面实心 ────────────────────────

@check("11")
def t11_chrome_is_glass():
    """飘在内容上面的东西（卡片、顶栏、弹层、浮层、提示）才用毛玻璃。"""
    base, comp = _plain(_base()), _plain(_read(CSS / "components.css"))
    pages = _plain(_read(CSS / "pages.css"))
    for sel, src in ((".card", base), (".topnav", base), (".placeholder", base),
                     (".dialog", comp), (".termtip", comp), (".toast", comp),
                     (".stock-suggest", pages), (".trace-panel", pages)):
        d = _decls(_block(src, sel))
        true("backdrop-filter" in d, f"{sel} 要用 backdrop-filter 做毛玻璃")
        true("-webkit-backdrop-filter" in d, f"{sel} 要带 -webkit- 前缀，Safari 才认")
        bg = d.get("background", "")
        true(bg.startswith("var(--glass"), f"{sel} 的背景要取玻璃令牌，实际 {bg!r}")
        true("var(--glass-sheen)" in d.get("box-shadow", ""),
             f"{sel} 要有顶部高光，玻璃才显得有厚度")


@check("11")
def t11_data_surfaces_stay_solid():
    """表格和指标卡是数字密集区，必须坐在不透明的高对比底上。
    给它们上毛玻璃，后面的色块会透过来干扰读数。"""
    comp = _plain(_read(CSS / "components.css"))
    for sel in (".tbl-wrap", ".metric-card"):
        d = _decls(_block(comp, sel))
        eq(d.get("background"), "var(--panel-solid)", f"{sel} 要坐在实心数据面上")
        true("backdrop-filter" not in d, f"{sel} 不该上毛玻璃")
        true("var(--panel-solid)" in _decls(_block(comp, ".input")).get("background", ""),
             "输入框也要坐在实心面上，不然打字时背后的字会透上来")


@check("11")
def t11_overlays_dim_with_a_scrim():
    """遮罩要压暗并轻微模糊背景，焦点才落得到弹层上。"""
    comp = _plain(_read(CSS / "components.css"))
    pages = _plain(_read(CSS / "pages.css"))
    for sel, src in ((".dialog-mask", comp), (".mask", comp), (".trace-mask", pages)):
        d = _decls(_block(src, sel))
        eq(d.get("background"), "var(--scrim)", f"{sel} 要用统一的遮罩令牌")
        true("backdrop-filter" in d, f"{sel} 要带背景模糊")


# ── 动效 ─────────────────────────────────────────────────

@check("11")
def t11_motion_durations_come_from_tokens():
    """时长写死成 0.15s 那种旧值，界面就会显得毛毛躁躁。一律走 var(--t-*)。
    唯一的例外是"减少动态效果"那段 —— 它本来就是要一刀切到 0，先摘掉再扫。"""
    lit = re.compile(r"transition[^;:]*:[^;]*?([\d.]+m?s)")
    for rel in ("base.css", "components.css", "pages.css"):
        src = re.sub(r"@media \(prefers-reduced-motion: reduce\)\s*\{.*?\n\}",
                     "", _plain(_read(CSS / rel)), flags=re.S)
        bad = [m.group(1) for m in lit.finditer(src)]
        eq(bad, [], f"{rel} 的 transition 时长不许写死，要走 var(--t-*)")


@check("11")
def t11_buttons_give_press_feedback():
    """按下要有反馈：位移或缩放，同时把顶部高光收掉，模拟被按平。"""
    comp = _plain(_read(CSS / "components.css"))
    d = _decls(_block(comp, ".btn"))
    true("transform" in _decls(_block(comp, ".btn:active")), "按钮按下要有形变")
    true("--glass-sheen" in d.get("box-shadow", ""), "按钮常态要有顶部高光")
    true("var(--t-fast)" in d.get("transition", ""), "按钮过渡要走时长令牌")


@check("11")
def t11_reduced_motion_is_honored():
    """系统开了"减少动态效果"就得把动画关掉，这是无障碍底线。
    光写个媒体查询不够，里面得真把时长压到 0。"""
    css = _base()
    true("prefers-reduced-motion" in css, "base.css 要响应 prefers-reduced-motion")
    blk = _plain(_block(css, "@media (prefers-reduced-motion: reduce)"))
    for prop in ("transition-duration", "animation-duration"):
        m = re.search(re.escape(prop) + r"\s*:\s*([\d.]+)ms", blk)
        true(m is not None, f"减少动态效果时要把 {prop} 压掉")
        true(m is not None and float(m.group(1)) <= 1,
             f"{prop} 要压到几乎为 0，实际 {m.group(1) if m else '?'}ms")
    true("animation-iteration-count" in blk, "无限循环的动画也要停")


# ── 主题切换 ─────────────────────────────────────────────

@check("11")
def t11_every_page_has_the_theme_switch():
    for name in PAGES:
        html = _read(FRONTEND / name)
        true('id="theme-switch"' in html, f"{name} 有外观切换控件")
        for choice in ("auto", "light", "dark"):
            true(f'data-theme-choice="{choice}"' in html, f"{name} 的控件里有「{choice}」")
        m = re.search(r'data-theme-choice="auto"[^>]*', html)
        true(m is not None and "active" in m.group(0), f"{name} 默认落在「自动」")


@check("11")
def t11_theme_is_applied_before_first_paint():
    """那段内联脚本必须排在 <body> 之前。放到 body 里或者等 DOMContentLoaded，
    深色用户每次刷新都会先闪一下白屏 —— 这毛病只在真机上闪给你看，测不出来。"""
    for name in PAGES:
        html = _read(FRONTEND / name)
        body = html.find("<body")
        head = html[:body]
        true(body > 0, f"{name} 有 <body>")
        i = head.find("localStorage.getItem")
        true(i > 0, f"{name} 的首屏脚本要读本地存的外观偏好")
        true("data-theme" in head[i:], f"{name} 的首屏脚本要写 data-theme")


@check("11")
def t11_theme_switch_is_text_only():
    """控件里只能是"自动/浅色/深色"三个词，不许配图标（见本文件开头的硬约束）。"""
    for name in PAGES:
        html = _read(FRONTEND / name)
        box = re.search(r'<div class="theme-switch".*?</div>', html, re.S)
        true(box is not None, f"{name} 有 theme-switch 容器")
        labels = re.findall(r'data-theme-choice="\w+"[^>]*>([^<]*)<', box.group(0) if box else "")
        eq(labels, ["自动", "浅色", "深色"], f"{name} 的三段文字")


@check("11")
def t11_auto_mode_is_pure_css():
    """"自动"不该由 JS 去读系统配色再写 data-theme —— 那样系统切换时页面不会自己跟着变。
    正确做法是 auto 时干脆不写 data-theme，让 CSS 的媒体查询分支接管。"""
    js = _read(JS / "app.js")
    true('removeAttribute("data-theme")' in js, "选「自动」要把 data-theme 摘掉，交给 CSS 判")
    true("matchMedia" in js and "prefers-color-scheme" in js,
         "自动模式下要监听系统深浅色变化，好重刷图表")
    css = _base()
    true(':root:not([data-theme="light"])' in _dark_media(css),
         "媒体查询分支要避开用户手动选了浅色的情况")


# ── 图表跟着主题走 ────────────────────────────────────────

@check("11")
def t11_charts_read_colors_from_css_tokens():
    js = _read(JS / "charts.js")
    true("getComputedStyle" in js and "getPropertyValue" in js,
         "图表取色要走 CSS 令牌，不能写死")
    true("refreshTheme" in js, "要暴露 refreshTheme() 供换主题时重刷")


@check("11")
def t11_refresh_theme_actually_repaints():
    """重刷要真把颜色换掉：清缓存、剔除已销毁实例、notMerge 覆盖旧 series，
    并且保住用户拖好的缩放区间 —— 少任何一条都会出现"切了主题图没变"或"图炸了"。"""
    js = _read(JS / "charts.js")
    i = js.index("function refreshTheme")
    body = js[i:js.index("\n  }", i)]
    true("PAL = null" in body, "要清掉调色板缓存，否则重刷还是旧色")
    true("isDisposed" in body, "要剔除已经 dispose 的实例，不然会往死图上画")
    true("notMerge" in body, "要 notMerge 覆盖，否则旧 series 会残留")
    true("dataZoom" in body, "要保住用户的缩放区间")
    true("dispose()" not in body, "不能 dispose 重建 —— 外面存着实例引用，会变野指针")


@check("11")
def t11_theme_switch_repaints_charts():
    js = _read(JS / "app.js")
    true("charts.refreshTheme" in js, "换主题后要叫图表重刷")
    true("localStorage.setItem" in js, "外观偏好要存下来，下次进来还是这个主题")


@check("11")
def t11_candlestick_keeps_up_red_down_green():
    """ECharts 的 K 线是 color=阳线 / color0=阴线，写反了整张图红绿颠倒，
    而且乍看还挺像那么回事，属于最阴的一种 bug。"""
    js = _read(JS / "charts.js")
    m = re.search(r"itemStyle:\s*\{([^}]*)\}", js, re.S)
    true(m is not None, "K 线有 itemStyle")
    blk = m.group(1) if m else ""
    true(re.search(r"\bcolor:\s*p\.up\b", blk) is not None, "阳线（涨）要用 --up")
    true(re.search(r"\bcolor0:\s*p\.down\b", blk) is not None, "阴线（跌）要用 --down")
    true(re.search(r"\bborderColor:\s*p\.up\b", blk) is not None, "阳线描边也要用 --up")
    true(re.search(r"\bborderColor0:\s*p\.down\b", blk) is not None, "阴线描边也要用 --down")


@check("11")
def t11_chart_surfaces_sit_on_the_solid_panel():
    """图表容器要坐在实心数据面上。HTML 里的图表 div 由页面自己给容器，
    这里盯住 CSS 有没有把实心面令牌定义出来，以及副图/表格没被玻璃化。"""
    d = _tokens(_block(_base(), ":root"))
    true("--panel-solid" in d, "要有实心数据面令牌")
    comp = _plain(_read(CSS / "components.css"))
    tbl = _decls(_block(comp, ".tbl-wrap"))
    true("overflow" in tbl, "表格容器要能横向滚动")
    eq(_decls(_block(comp, ".tbl th")).get("background"), "var(--panel-sunken)",
       "表头要和表身分开，但同样走令牌")


# ── 禁止天气与天体元素 ────────────────────────────────────

_WEATHER_CN = ["天气", "气象", "太阳", "月亮", "星星", "白云", "云朵", "云层", "多云",
               "下雨", "雨天", "雪花", "下雪", "晴天", "阴天", "山川", "城市景观",
               "星空", "日出", "日落"]
_WEATHER_EN = re.compile(
    r"\b(?:sky|skies|cloud|clouds|cloudy|sun|sunny|sunshine|moon|lunar|star|stars|"
    r"rain|rainy|snow|snowy|weather|storm|thunder|rainbow|forecast)\b", re.I)
_WEATHER_GLYPHS = ["☀", "☁", "☂", "☃", "❄", "⛅", "⛄", "🌞", "🌝", "🌙", "🌛",
                   "⭐", "🌟", "🌠", "🌧", "🌨", "🌩", "🌈", "🏙", "⛰", "🌤", "🌥", "🌦"]


def _frontend_sources():
    for name in PAGES:
        yield name, _read(FRONTEND / name)
    for f in sorted((FRONTEND / "css").glob("*.css")):
        yield f"css/{f.name}", f.read_text(encoding="utf-8")
    for f in sorted(JS.glob("*.js")):
        if f.name == "echarts.min.js":
            continue
        yield f"js/{f.name}", f.read_text(encoding="utf-8")


@check("11")
def t11_no_weather_or_celestial_elements():
    """只借材质语言，不许出现任何气象或天体意象。
    注意 ✓ ✗ ⚠ 🔒 是对勾/叉/警告/锁，与气象无关，不在禁止之列。"""
    hits = []
    for name, src in _frontend_sources():
        for w in _WEATHER_CN:
            if w in src:
                hits.append(f"{name}: {w}")
        for m in _WEATHER_EN.finditer(src):
            hits.append(f"{name}: {m.group(0)}")
        for g in _WEATHER_GLYPHS:
            if g in src:
                hits.append(f"{name}: {g}")
    eq(hits, [], "前端不该出现天气/天体元素")


# ── 功能冻结：只换皮肤 ────────────────────────────────────

@check("11")
def t11_every_id_the_scripts_look_for_still_exists():
    """这次只换视觉，脚本要找的元素一个都不能少。
    逐个把 getElementById 的名字拿去对：要么在静态 HTML 里，要么是脚本自己建的。
    少一个就是一处静默失效 —— 不报错，只是那块功能悄悄不干活了。"""
    html_all = "\n".join(_read(FRONTEND / n) for n in PAGES)
    wanted, built = set(), set()
    for f in sorted(JS.glob("*.js")):
        if f.name == "echarts.min.js":
            continue
        src = _read(f)
        wanted |= set(re.findall(r'getElementById\("([^"]+)"\)', src))
        built |= set(re.findall(r'\.id\s*=\s*"([^"]+)"', src))
        built |= set(re.findall(r'id="([^"]+)"', src))
    true(len(wanted) >= 40, f"脚本引用的 id 不少于 40 个，实际 {len(wanted)}")
    missing = sorted(i for i in wanted
                     if f'id="{i}"' not in html_all and i not in built)
    eq(missing, [], "这些 id 在页面里找不到")


@check("11")
def t11_theme_preference_stays_out_of_the_database():
    """外观是每台浏览器自己的展示偏好，不该写进后端设置：
    一来它和业务数据无关，二来免得导出设置时把别人的主题一起带走。"""
    js = _read(JS / "app.js")
    true("localStorage" in js, "外观偏好存浏览器本地")
    true("/api/settings" not in js, "外观偏好不该走后端设置接口")
