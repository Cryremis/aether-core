from __future__ import annotations

from app.services.system_network_service import SystemNetworkService


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
