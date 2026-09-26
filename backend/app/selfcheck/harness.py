"""自检框架：把 PRD 第九章里"可用数字复核"的验收条目变成自动断言。

用法（容器内）：
    python -m app.selfcheck 0        只跑阶段 0
    python -m app.selfcheck all      跑全部已实现阶段

设计约定：
- 自检跑在 data/selfcheck/quant.db 这个独立库上，绝不污染正式数据；
- 需要真实行情的断言一律用合成K线（见 app/synth.py），不依赖外网；
- 任何 FAIL 都必须是真 bug，不允许为了让测试过而改断言。
"""

import logging
import os
import sys
import traceback
from pathlib import Path

# 自检里会故意触发异常（验证错误分类），堆栈会刷屏，只保留自检自己的输出
logging.disable(logging.ERROR)

# 必须在导入 app.config 之前设定，否则自检会写进正式数据库
VOLUME_DIR = os.environ.get("DATA_DIR", "/app/data")
_SCRATCH = Path(VOLUME_DIR) / "selfcheck"
_SCRATCH.mkdir(parents=True, exist_ok=True)
os.environ["DATA_DIR"] = str(_SCRATCH)
for _f in _SCRATCH.glob("quant.db*"):
    _f.unlink(missing_ok=True)

CHECKS: list[tuple[str, str, object]] = []


def check(stage: str):
    def deco(fn):
        CHECKS.append((stage, fn.__name__, fn))
        return fn

    return deco


class Fail(AssertionError):
    pass


def eq(actual, expected, label: str):
    if actual != expected:
        raise Fail(f"{label}\n      期望: {expected!r}\n      实际: {actual!r}")


def near(actual, expected, tol, label: str):
    if actual is None or abs(float(actual) - float(expected)) > tol:
        raise Fail(f"{label}\n      期望: {expected!r} ± {tol}\n      实际: {actual!r}")


def true(cond, label: str):
    if not cond:
        raise Fail(f"{label}\n      条件不成立: {cond!r}")


def raises_error(fn, kind: str, code_part: str, label: str):
    """断言某操作被 fail-closed 拒绝，并且错误分类与错误码正确。"""
    from ..errors import AppError

    try:
        fn()
    except AppError as exc:
        eq(exc.kind, kind, f"{label} · 错误分类")
        if code_part.lower() not in exc.code.lower():
            raise Fail(f"{label}\n      错误码应含 {code_part!r}，实际 {exc.code!r}")
        return
    except Exception as exc:  # noqa: BLE001
        raise Fail(f"{label}\n      抛出了非预期异常 {type(exc).__name__}: {exc}") from exc
    raise Fail(f"{label}\n      本该被拒绝，却顺利执行了")


def client():
    from fastapi.testclient import TestClient

    from ..main import app

    return TestClient(app, raise_server_exceptions=False)


def fresh_db():
    """清空自检库并重建，供各阶段独立使用。"""
    from ..db import connect, init_db

    with connect() as conn:
        names = [r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()]
        for n in names:
            conn.execute(f'DROP TABLE IF EXISTS "{n}"')
    init_db()


class stub:
    """临时替换模块属性，退出时还原。with stub(store, "fetch_daily", fn): ..."""

    def __init__(self, obj, name, value):
        self.obj, self.name = obj, name
        self.value, self.saved = value, getattr(obj, name)

    def __enter__(self):
        setattr(self.obj, self.name, self.value)
        return self.value

    def __exit__(self, *exc):
        setattr(self.obj, self.name, self.saved)
        return False


def run(stages: list[str]) -> int:
    failed: list[str] = []
    passed = 0
    selected = [c for c in CHECKS if "all" in stages or c[0] in stages]
    selected.sort(key=lambda c: (c[0], c[1]))
    current = None
    for stage, name, fn in selected:
        if stage != current:
            current = stage
            print(f"\n=== 阶段 {stage} ===")
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            failed.append(f"阶段{stage} · {name}")
            print(f"  ✗ {name}")
            if isinstance(exc, Fail):
                print(f"      {exc}")
            else:
                print("      " + traceback.format_exc().replace("\n", "\n      ").rstrip())
            sys.stdout.flush()
        else:
            passed += 1
            print(f"  ✓ {name}")
            sys.stdout.flush()

    print(f"\n结果：{passed} 通过 / {len(failed)} 失败")
    if failed:
        print("失败项：")
        for f in failed:
            print(f"  - {f}")
        return 1
    return 0


# 每个开发阶段对应的自检文件。
STAGE_MODULES = {
    "0": "s0_base",
    "1": "s1_datasource",
    "2": "s2_data",
    "3": "s3_dsl",
    "4": "s4_engine",
    "5": "s5_frontend",
    "6": "s6_homepage",
    "7": "s7_backtest",
    "8": "s8_tasks_settings",
    "9": "s9_smoke",
    "10": "s10_v2",
    "11": "s11_glass",
}

# 已交付的阶段。每完成一个开发阶段就把编号追加进来。
# "all" 只跑这里列出的阶段；请求一个未列出的阶段会直接报错，不会被悄悄跳过。
DELIVERED = ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11"]


def main(argv: list[str]) -> int:
    import importlib

    requested = [a for a in argv if not a.startswith("-")] or ["all"]
    if "all" in requested:
        stages = list(DELIVERED)
    else:
        stages = requested
        for s in stages:
            if s not in DELIVERED:
                print(f"\n[X] 阶段 {s} 尚未交付自检（已交付：{'、'.join(DELIVERED)}），不能算通过。")
                return 1
    for s in stages:
        importlib.import_module(f".{STAGE_MODULES[s]}", __package__)

    pending = [s for s in STAGE_MODULES if s not in DELIVERED]
    if pending:
        print(f"提示：阶段 {'、'.join(sorted(pending))} 尚未开发，本轮没有它们的断言。")
    return run(stages)
