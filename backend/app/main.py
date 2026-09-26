"""FastAPI 入口：API 路由 + 前端静态托管 + 全局异常处理。"""

import logging
import sqlite3
import traceback
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .api.backtests import router as backtests_router
from .api.cache import router as cache_router
from .api.meta import router as meta_router
from .api.settings import router as settings_router
from .api.stocks import router as stocks_router
from .api.strategies import router as strategies_router
from .config import APP_VERSION, FRONTEND_DIR
from .db import init_db, now
from .errors import KIND_INPUT, KIND_PROGRAM, AppError, program_error

log = logging.getLogger("app")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    init_db()
    log.info("数据库就绪 %s", now())
    yield


app = FastAPI(
    title="A股策略验证系统",
    version=APP_VERSION,
    docs_url=None,
    redoc_url=None,
    lifespan=_lifespan,
)


def _db_status() -> str:
    try:
        from .db import connect

        with connect() as conn:
            conn.execute("SELECT 1")
        return "ready"
    except sqlite3.Error as exc:  # pragma: no cover - 仅在磁盘/权限异常时触发
        log.exception("数据库不可用")
        return f"error: {exc}"


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "version": APP_VERSION,
        "db": _db_status(),
        "time": now(),
    }


for router in (stocks_router, cache_router, settings_router, meta_router,
               strategies_router, backtests_router):
    app.include_router(router, prefix="/api")


@app.exception_handler(AppError)
def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
    if exc.kind == KIND_PROGRAM:
        log.error("业务异常 %s %s -> %s", request.method, request.url.path, exc)
    else:
        log.info("%s %s -> %s", request.method, request.url.path, exc)
    return JSONResponse(exc.payload(), status_code=exc.http)


@app.exception_handler(RequestValidationError)
def handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
    first = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(p) for p in first.get("loc", ()) if p not in ("body",))
    return JSONResponse(
        {
            "error": {
                "kind": KIND_INPUT,
                "code": "INVALID_REQUEST",
                "message": "提交的内容有不明白的地方，请检查后重试",
                "detail": str(first.get("msg", "")),
                "field": field or None,
                "retriable": False,
            }
        },
        status_code=400,
    )


@app.exception_handler(StarletteHTTPException)
def handle_http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """接口路径找不到时返回统一 JSON，而不是 HTML，避免前端解析崩掉。"""
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            {
                "error": {
                    "kind": KIND_INPUT,
                    "code": "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR",
                    "message": "没有这个功能" if exc.status_code == 404 else str(exc.detail),
                    "detail": "",
                    "retriable": False,
                }
            },
            status_code=exc.status_code,
        )
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


@app.exception_handler(Exception)
def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    log.error("未预期异常 %s %s\n%s", request.method, request.url.path, detail)
    payload = program_error(
        "INTERNAL_ERROR",
        "程序出了意料之外的状况，已记录日志。可以复制下面的详情反馈。",
        detail=detail,
    ).payload()
    return JSONResponse(payload, status_code=500)


if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="static")
