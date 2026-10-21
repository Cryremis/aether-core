from __future__ import annotations

import logging

import pytest
from fastapi import HTTPException

from app.api.routes.host import bind_host
from app.host.registry import host_registry
from app.schemas.host import HostBindRequest


def test_bind_rejection_is_logged(monkeypatch, caplog):
    request = HostBindRequest(platform_key="test-platform", host_name="test-host")

    def reject(*args, **kwargs):
        raise ValueError("目标会话不存在")

    monkeypatch.setattr(host_registry, "bind", reject)

    with caplog.at_level(logging.WARNING, logger="app.api.routes.host"):
        with pytest.raises(HTTPException) as exc_info:
            bind_host(request, {"platform_key": "test-platform"})

    assert exc_info.value.status_code == 400
    assert "[host/bind] rejected: 目标会话不存在" in caplog.text
