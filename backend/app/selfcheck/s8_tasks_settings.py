"""阶段 8 自检：我的任务页 + 设置页 + 导出。

验收要点：
- tasks.html / settings.html 存在且结构完整
- tasks.js / settings.js 存在且含核心逻辑
- 设置页按组渲染、即时保存、恢复默认
- 导出排除 API Key（SECRET_SETTINGS）
- 数据源测试按钮存在
- 无 CDN 引用
- 免责声明存在
"""

from pathlib import Path

from .harness import check, true

FRONTEND = Path("/app/frontend")
JS = FRONTEND / "js"


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


@check("8")
def s8_tasks_html_exists():
    """tasks.html 存在且包含核心容器。"""
    html = FRONTEND / "tasks.html"
    true(html.exists(), "tasks.html 应存在")
    content = _read(html)
    true("task-list" in content, "tasks.html 应有 #task-list 容器")
    true("compare-btn" in content, "tasks.html 应有横向对比按钮")


@check("8")
def s8_tasks_html_disclaimer():
    """任务页有免责声明。"""
    content = _read(FRONTEND / "tasks.html")
    true("不构成投资建议" in content, "tasks.html 应有免责声明")


@check("8")
def s8_tasks_js_exists():
    """tasks.js 存在且包含核心逻辑。"""
    js = JS / "tasks.js"
    true(js.exists(), "tasks.js 应存在")
    content = _read(js)
    true("loadRuns" in content, "tasks.js 应有 loadRuns 函数")
    true("renderList" in content, "tasks.js 应有 renderList 函数")


@check("8")
def s8_tasks_js_compare():
    """tasks.js 有横向对比功能。"""
    content = _read(JS / "tasks.js")
    true("compare" in content.lower(), "tasks.js 应有对比逻辑")
    true("selected" in content, "tasks.js 应有选中状态管理")


@check("8")
def s8_tasks_js_delete():
    """tasks.js 有删除功能。"""
    content = _read(JS / "tasks.js")
    true("task-del" in content or "删除" in content, "tasks.js 应有删除按钮/逻辑")
    true("dialog.confirm" in content or "confirm" in content, "tasks.js 删除前应确认")


@check("8")
def s8_settings_html_exists():
    """settings.html 存在且包含核心容器。"""
    html = FRONTEND / "settings.html"
    true(html.exists(), "settings.html 应存在")
    content = _read(html)
    true("settings-container" in content, "settings.html 应有 #settings-container")
    true("datasource-results" in content, "settings.html 应有数据源测试结果区")
    true("test-all" in content, "settings.html 应有测试全部按钮")


@check("8")
def s8_settings_html_disclaimer():
    """设置页有免责声明。"""
    content = _read(FRONTEND / "settings.html")
    true("不构成投资建议" in content, "settings.html 应有免责声明")


@check("8")
def s8_settings_html_data_management():
    """设置页有数据管理区（清空缓存 + 导出）。"""
    content = _read(FRONTEND / "settings.html")
    true("clear-cache" in content, "settings.html 应有清空缓存按钮")
    true("export-all" in content, "settings.html 应有导出按钮")


@check("8")
def s8_settings_js_exists():
    """settings.js 存在且包含核心逻辑。"""
    js = JS / "settings.js"
    true(js.exists(), "settings.js 应存在")
    content = _read(js)
    true("loadSettings" in content, "settings.js 应有 loadSettings 函数")
    true("renderSettings" in content, "settings.js 应有 renderSettings 函数")
    true("saveField" in content, "settings.js 应有即时保存逻辑")


@check("8")
def s8_settings_js_groups():
    """settings.js 按组渲染。"""
    content = _read(JS / "settings.js")
    true("GROUP_LABELS" in content or "groups" in content, "settings.js 应按组渲染")
    true("reset-group" in content or "恢复" in content, "settings.js 应有恢复默认功能")


@check("8")
def s8_settings_js_datasource_test():
    """settings.js 有数据源测试功能。"""
    content = _read(JS / "settings.js")
    true("/api/datasource/test" in content or "datasource/test" in content,
         "settings.js 应调用数据源测试接口")


@check("8")
def s8_settings_export_excludes_secret():
    """settings.js 导出时排除 API Key。"""
    content = _read(JS / "settings.js")
    true("secret_set" in content, "settings.js 导出应检查 secret_set")
    true("API Key" in content or "api_key" in content.lower() or "secret" in content.lower(),
         "settings.js 导出应排除密钥")


@check("8")
def s8_settings_js_clear_cache():
    """settings.js 有清空缓存功能。"""
    content = _read(JS / "settings.js")
    true("/api/cache/clear" in content or "cache/clear" in content,
         "settings.js 应调用清空缓存接口")


@check("8")
def s8_no_cdn_in_new_files():
    """新增文件不含 CDN 引用。"""
    for name in ["tasks.html", "settings.html", "js/tasks.js", "js/settings.js"]:
        content = _read(FRONTEND / name)
        true("cdnjs" not in content and "jsdelivr" not in content and "unpkg" not in content,
             f"{name} 不应引用外部 CDN")


@check("8")
def s8_nav_links_consistent():
    """三个页面的导航链接一致。"""
    for name in ["index.html", "tasks.html", "settings.html", "backtest.html"]:
        content = _read(FRONTEND / name)
        true('href="/"' in content, f"{name} 应有首页链接")
        true('href="/tasks.html"' in content, f"{name} 应有任务页链接")
        true('href="/settings.html"' in content, f"{name} 应有设置页链接")
