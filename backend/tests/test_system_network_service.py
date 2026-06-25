from __future__ import annotations

import subprocess

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


def test_list_80_prefix_ipv4_addresses_returns_unique_matches():
    from app.schemas.system_network import NetworkAddress, NetworkInterface, NetworkSummary, SystemNetworkSnapshot

    service = SystemNetworkService()
    snapshot = SystemNetworkSnapshot(
        hostname="host-01",
        fqdn=None,
        platform="linux",
        source="ip",
        namespace_scope="host",
        scope_note="host",
        summary=NetworkSummary(interface_count=1, up_interface_count=1, ipv4_count=1, ipv6_count=0, public_address_count=1),
        interfaces=[
            NetworkInterface(
                name="eth0",
                is_up=True,
                addresses=[NetworkAddress(family="ipv4", address="80.12.34.56", category="public")],
            )
        ],
    )

    commands: list[list[str]] = []

    def fake_run(command, capture_output, timeout, check):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=b"ok", stderr=b"")

    monkeypatch.setattr("app.services.system_network_service.platform.system", lambda: "Linux")
    monkeypatch.setattr(service, "get_snapshot", lambda: snapshot)
    monkeypatch.setattr("app.services.system_network_service.subprocess.run", fake_run)

    result = service.apply_route_for_80_network()

    assert commands == [[
        "route",
        "add",
        "-net",
        "80.0.0.0",
        "netmask",
        "255.0.0.0",
        "gw",
        "80.253.32.1",
    ]]
    assert result["gateway_ip"] == "80.253.32.1"
    assert result["detected_80_prefix_ips"] == ["80.12.34.56"]
    assert result["stdout"] == "ok"
