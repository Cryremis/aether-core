from __future__ import annotations

import pytest

from app.services.system_network_service import SystemNetworkService
from app.services.token_service import TokenService


def test_decode_output_falls_back_to_windows_code_page(monkeypatch):
    service = SystemNetworkService()
    monkeypatch.setattr("app.services.system_network_service.platform.system", lambda: "Windows")
    monkeypatch.setattr("app.services.system_network_service.locale.getpreferredencoding", lambda _do_setlocale=False: "cp936")

    payload = "以太网适配器".encode("gbk")

    assert service._decode_output(payload) == "以太网适配器"


def test_decode_output_prefers_utf8_when_available(monkeypatch):
    service = SystemNetworkService()
    monkeypatch.setattr("app.services.system_network_service.platform.system", lambda: "Linux")
    monkeypatch.setattr("app.services.system_network_service.locale.getpreferredencoding", lambda _do_setlocale=False: "utf-8")

    payload = "eth0: flags=4163".encode("utf-8")

    assert service._decode_output(payload) == "eth0: flags=4163"


def test_token_service_normalizes_algorithm(monkeypatch):
    service = TokenService()
    monkeypatch.setattr("app.services.token_service.settings.auth_algorithm", " hs256 ")

    assert service._resolved_algorithm() == "HS256"


def test_token_service_rejects_empty_algorithm(monkeypatch):
    service = TokenService()
    monkeypatch.setattr("app.services.token_service.settings.auth_algorithm", "   ")

    with pytest.raises(ValueError, match="AUTH_ALGORITHM"):
        service._resolved_algorithm()
