"""统一错误类型。

四类错误的分派规则见 PRD §5 与 DEVELOPMENT_PLAN §3.2：
    datasource  数据源问题（橙，可重试 / 可换源）
    input       输入问题（红框，定位到具体字段）
    integrity   数据完整性问题（红，fail-closed 拒绝运行）
    program     程序异常（红，可折叠详情 + 复制错误信息）
"""

from typing import Any

KIND_DATASOURCE = "datasource"
KIND_INPUT = "input"
KIND_INTEGRITY = "integrity"
KIND_PROGRAM = "program"

_DEFAULT_HTTP = {
    KIND_DATASOURCE: 502,
    KIND_INPUT: 400,
    KIND_INTEGRITY: 422,
    KIND_PROGRAM: 500,
}


class AppError(Exception):
    """带分类的业务错误。前端据 kind 决定颜色与可执行动作。"""

    def __init__(
        self,
        kind: str,
        code: str,
        message: str,
        detail: str = "",
        field: str | None = None,
        retriable: bool = False,
        http: int | None = None,
        extra: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.kind = kind
        self.code = code
        self.message = message
        self.detail = detail
        self.field = field
        self.retriable = retriable
        self.http = http or _DEFAULT_HTTP.get(kind, 400)
        self.extra = extra or {}

    def payload(self) -> dict[str, Any]:
        err: dict[str, Any] = {
            "kind": self.kind,
            "code": self.code,
            "message": self.message,
            "detail": self.detail,
            "retriable": self.retriable,
        }
        if self.field:
            err["field"] = self.field
        err.update(self.extra)
        return {"error": err}

    def __str__(self) -> str:
        return f"[{self.kind}/{self.code}] {self.message}"


def datasource_error(code: str, message: str, detail: str = "", retriable: bool = True) -> AppError:
    return AppError(KIND_DATASOURCE, code, message, detail=detail, retriable=retriable)


def input_error(code: str, message: str, field: str | None = None, detail: str = "") -> AppError:
    return AppError(KIND_INPUT, code, message, detail=detail, field=field)


def integrity_error(code: str, message: str, detail: str = "", field: str | None = None,
                    extra: dict[str, Any] | None = None) -> AppError:
    return AppError(KIND_INTEGRITY, code, message, detail=detail, field=field, extra=extra)


def program_error(code: str, message: str, detail: str = "") -> AppError:
    return AppError(KIND_PROGRAM, code, message, detail=detail)
