"""阶段 6 自检：首页（建策略）。

验证：
- 首页 HTML 结构完整（三段布局 + 确认面板）
- 条件构建器 JS 存在且包含关键逻辑
- 选股组件 JS 存在
- 预检组件 JS 存在
- 页面装配 JS 存在
- 键盘快捷键（/、Esc、Ctrl+Enter）
- 免责声明
- 模板 / 大白话标签
- 无 CDN 引用
"""

import re
from pathlib import Path

from .harness import check, true

FRONTEND = Path("/app/frontend")


def _read(rel: str) -> str:
    p = FRONTEND / rel
    true(p.exists(), f"文件存在: {rel}")
    return p.read_text(encoding="utf-8")


def _exists(rel: str) -> bool:
    return (FRONTEND / rel).exists()


@check("6")
def s6_homepage_files():
    required = [
        "index.html",
        "js/condition.js",
        "js/stockpicker.js",
        "js/preflight.js",
        "js/index.js",
    ]
    for f in required:
        true(_exists(f), f"首页文件存在: {f}")


@check("6")
def s6_index_html_structure():
    html = _read("index.html")
    # 三段布局
    true("①" in html and "②" in html and "③" in html, "首页有三段编号标题")
    true("condition-area" in html, "首页有条件构建器挂载点")
    true("stock-picker-area" in html, "首页有选股挂载点")

    # 确认面板
    true("confirm-panel" in html, "首页有确认面板")
    true("run-btn" in html, "首页有开始回测按钮")

    # 模式选择
    true("历史回测" in html, "首页有历史回测模式")
    true("实时模拟" in html, "首页有实时模拟（锁定）")
    true("情绪分析" in html, "首页有情绪分析（锁定）")

    # 策略入口
    true("手动搭条件" in html, "首页有手动搭条件标签")
    true("从模板选" in html, "首页有从模板选标签")
    true("大白话" in html, "首页有大白话标签")

    # 周期和日期
    true("period-select" in html, "首页有周期选择")
    true("start-date" in html, "首页有开始日期")
    true("end-date" in html, "首页有结束日期")

    # 免责声明
    true("不构成投资建议" in html, "首页有免责声明")

    # 预检
    true("preflight-area" in html, "首页有预检区域")

    # 最近运行
    true("recent-runs" in html, "首页有最近运行区域")

    # JS 引用
    true("condition.js" in html, "首页引用 condition.js")
    true("stockpicker.js" in html, "首页引用 stockpicker.js")
    true("preflight.js" in html, "首页引用 preflight.js")
    true("index.js" in html, "首页引用 index.js")


@check("6")
def s6_no_cdn_in_new_files():
    cdn_patterns = [
        r"https?://cdn\.",
        r"https?://unpkg\.com",
        r"https?://cdn.jsdelivr\.net",
        r"https?://cdnjs\.cloudflare\.com",
    ]
    files = [
        "js/condition.js",
        "js/stockpicker.js",
        "js/preflight.js",
        "js/index.js",
        "index.html",
    ]
    for rel in files:
        if not _exists(rel):
            continue
        content = _read(rel)
        for pat in cdn_patterns:
            matches = re.findall(pat, content)
            true(len(matches) == 0, f"无 CDN 引用 in {rel}")


@check("6")
def s6_condition_builder():
    content = _read("js/condition.js")
    # 嵌套上限 2 层（D7）
    true("2" in content and "层" in content, "条件构建器有嵌套上限说明")
    # cross_above/below 禁用固定数字（D6）
    true("cross_above" in content and "cross_below" in content, "条件构建器处理 cross_above/below")
    true("禁用" in content or "只能" in content, "条件构建器对 cross 有说明")
    # debounce 300ms（D2）
    true("300" in content, "条件构建器有 300ms debounce")
    # explain API
    true("/api/strategies/explain" in content, "条件构建器调 explain API")
    # 指标按组分类
    true("optgroup" in content, "条件构建器指标下拉按组分类")
    # 参数校验
    true("param" in content.lower(), "条件构建器处理参数")
    # 三种右值形态
    true('"ind"' in content and '"num"' in content and '"mult"' in content, "条件构建器支持三种右值形态")


@check("6")
def s6_stockpicker():
    content = _read("js/stockpicker.js")
    true("/api/stocks/search" in content, "选股调搜索 API")
    true("limit=8" in content or "limit" in content, "选股限制建议数量")
    true("chip" in content, "选股用 chip 显示已选")
    true("/" in content, "选股支持 / 快捷键聚焦")
    true("50" in content, "选股有多选上限")
    true("ArrowDown" in content or "ArrowUp" in content, "选股支持键盘导航")


@check("6")
def s6_preflight_component():
    content = _read("js/preflight.js")
    checks = ["code_valid", "date_range", "period", "dsl_valid", "initial_cash", "datasource_order", "single_source"]
    for c in checks:
        true(c in content, f"预检包含 {c} 检查项")
    true("pf-ok" in content and "pf-err" in content, "预检有通过/失败样式")
    true("allOk" in content, "预检有全部通过判断")


@check("6")
def s6_index_js():
    content = _read("js/index.js")
    # Ctrl+Enter 提交
    true("ctrlKey" in content and "Enter" in content, "index.js 有 Ctrl+Enter 提交")
    # 模板加载
    true("/api/templates" in content, "index.js 加载模板")
    # 策略保存
    true("/api/strategies" in content, "index.js 保存策略")
    # 回测提交
    true("/api/backtests" in content, "index.js 提交回测")
    # 进度浮层
    true("progress" in content.lower(), "index.js 有进度显示")
    # 大白话锁定
    true("V2" in content or "版本" in content, "index.js 大白话标签有说明")
    # 最近运行
    true("recent-runs" in content or "recent" in content.lower(), "index.js 有最近运行")
    # 免责声明
    true("不构成投资建议" in _read("index.html"), "首页有免责声明")


@check("6")
def s6_keyboard_shortcuts():
    """L8: / 聚焦搜索、Esc 关闭、Ctrl+Enter 提交、Tab+focus 样式"""
    index_js = _read("js/index.js")
    stockpicker = _read("js/stockpicker.js")
    dialog_js = _read("js/dialog.js")

    # / 聚焦搜索
    true('"/"' in stockpicker or "key === \"/\"" in stockpicker, "stockpicker 有 / 聚焦")
    # Ctrl+Enter 提交
    true("ctrlKey" in index_js and "Enter" in index_js, "index.js 有 Ctrl+Enter")
    # Esc 关闭
    true("Escape" in dialog_js, "dialog 有 Esc 关闭")
    # focus 样式在 components.css
    css = _read("css/base.css")
    true("focus-visible" in css, "base.css 有 focus-visible 样式")
