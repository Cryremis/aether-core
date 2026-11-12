"""模型服务异常与用户可见错误分离。"""
from __future__ import annotations

import asyncio
import re

import httpx


class ModelStreamError(RuntimeError):
    """模型流不完整或存在协议差异，可归档片段后重新生成。"""


class ModelOutputLimitError(ModelStreamError):
    """输出额度耗尽，收到的内容不能作为完整结果。"""

    def __init__(self, message: str, *, usage: dict | None = None):
        super().__init__(message)
        self.usage = usage if isinstance(usage, dict) else {}


def public_run_error() -> str:
    return "当前服务暂时无法完成请求，已保留已有进度，请稍后继续。"


def exception_leaves(exc: BaseException):
    if isinstance(exc, BaseExceptionGroup):
        for child in exc.exceptions:
            yield from exception_leaves(child)
    else:
        yield exc


def cancelled_model_error(exc: BaseException) -> bool:
    return any(isinstance(leaf, asyncio.CancelledError) for leaf in exception_leaves(exc))


def retryable_stream_error(exc: BaseException) -> bool:
    # 混合异常组必须全部可恢复；不能用其中一个连接错误掩盖程序错误或取消。
    return all(isinstance(leaf, (httpx.TransportError, ModelStreamError)) or (
        isinstance(leaf, RuntimeError) and re.fullmatch(
            r"File descriptor \d+ is used by transport(?:\s.*)?", str(leaf), re.DOTALL)
    ) for leaf in exception_leaves(exc))


def retryable_model_error(exc: BaseException) -> bool:
    if isinstance(exc, BaseExceptionGroup):
        return all(retryable_model_error(child) for child in exc.exceptions)
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {408, 425, 429, 500, 502, 503, 504, 529}
    return retryable_stream_error(exc)


def public_run_error_code(exc: Exception) -> str:
    """终态携带稳定分类，宿主自行呈现提示，不依赖解析异常正文。"""
    # 引擎会包装异常；沿显式 cause 读取类型，绝不解析或回传诊断正文。
    seen: set[int] = set()
    while not isinstance(exc, (httpx.HTTPStatusError, httpx.TransportError, ModelStreamError)):
        if id(exc) in seen or not isinstance(exc.__cause__, Exception):
            break
        seen.add(id(exc))
        exc = exc.__cause__
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in {401, 403}:
            return "MODEL_ACCESS_DENIED"
        if status == 429:
            return "MODEL_RATE_LIMITED"
        if status in {400, 404, 422}:
            return "MODEL_CONFIGURATION_ERROR"
        if status >= 500:
            return "MODEL_UNAVAILABLE"
    if isinstance(exc, httpx.TransportError):
        return "MODEL_UNAVAILABLE"
    if isinstance(exc, ModelStreamError):
        return "MODEL_RESPONSE_INVALID"
    return "RUN_FAILED"
