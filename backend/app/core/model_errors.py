"""模型服务异常与用户可见错误分离。"""
from __future__ import annotations

import httpx


class ModelStreamError(RuntimeError):
    """模型流不完整或存在协议差异，可归档片段后重新生成。"""


def public_run_error() -> str:
    return "当前服务暂时无法完成请求，已保留已有进度，请稍后继续。"


def retryable_model_error(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {408, 425, 429, 500, 502, 503, 504, 529}
    return isinstance(exc, (httpx.TransportError, ModelStreamError))
