from __future__ import annotations

import ipaddress
import json
import locale
import platform
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Any

from app.schemas.system_network import NetworkAddress, NetworkInterface, NetworkSummary, SystemNetworkSnapshot


class SystemNetworkService:
    def get_snapshot(self) -> SystemNetworkSnapshot:
        hostname = socket.gethostname()
        fqdn = socket.getfqdn() or None
        system_name = platform.system().lower()
        namespace_scope = self._detect_namespace_scope(system_name)
        scope_note = self._scope_note(namespace_scope)

        if system_name == "linux":
            return self._collect_linux_snapshot(hostname, fqdn, namespace_scope, scope_note)
        if system_name == "windows":
            return self._collect_windows_snapshot(hostname, fqdn, namespace_scope, scope_note)
        return self._collect_socket_snapshot(
            hostname=hostname,
            fqdn=fqdn,
            platform_name=system_name or "unknown",
            namespace_scope=namespace_scope,
            scope_note=scope_note,
        )

    def _collect_linux_snapshot(
        self,
        hostname: str,
        fqdn: str | None,
        namespace_scope: str,
        scope_note: str,
    ) -> SystemNetworkSnapshot:
        interfaces: list[NetworkInterface] = []
        raw_text = ""
        source = "socket"

        if shutil.which("ip"):
            json_payload = self._run_command(["ip", "-j", "address", "show"])
            raw_text = self._run_command(["ip", "address", "show"]) or ""
            if json_payload:
                try:
                    interfaces = self._parse_linux_ip_json(json_payload)
                    source = "ip"
                except json.JSONDecodeError:
                    raw_text = raw_text or json_payload

        if not raw_text and shutil.which("ifconfig"):
            raw_text = self._run_command(["ifconfig", "-a"]) or ""
            if raw_text and source == "socket":
                source = "ifconfig"

        if not interfaces:
            interfaces = self._collect_socket_interfaces()

        return self._build_snapshot(
            hostname=hostname,
            fqdn=fqdn,
            platform_name="linux",
            source=source,
            namespace_scope=namespace_scope,
            scope_note=scope_note,
            interfaces=interfaces,
            raw_text=raw_text or None,
        )

    def _collect_windows_snapshot(
        self,
        hostname: str,
        fqdn: str | None,
        namespace_scope: str,
        scope_note: str,
    ) -> SystemNetworkSnapshot:
        raw_text = self._run_command(["ipconfig", "/all"]) or ""
        interfaces = self._collect_socket_interfaces()
        return self._build_snapshot(
            hostname=hostname,
            fqdn=fqdn,
            platform_name="windows",
            source="ipconfig" if raw_text else "socket",
            namespace_scope=namespace_scope,
            scope_note=scope_note,
            interfaces=interfaces,
            raw_text=raw_text or None,
        )

    def _collect_socket_snapshot(
        self,
        *,
        hostname: str,
        fqdn: str | None,
        platform_name: str,
        namespace_scope: str,
        scope_note: str,
    ) -> SystemNetworkSnapshot:
        return self._build_snapshot(
            hostname=hostname,
            fqdn=fqdn,
            platform_name=platform_name,
            source="socket",
            namespace_scope=namespace_scope,
            scope_note=scope_note,
            interfaces=self._collect_socket_interfaces(),
            raw_text=None,
        )

    def _build_snapshot(
        self,
        *,
        hostname: str,
        fqdn: str | None,
        platform_name: str,
        source: str,
        namespace_scope: str,
        scope_note: str,
        interfaces: list[NetworkInterface],
        raw_text: str | None,
    ) -> SystemNetworkSnapshot:
        summary = NetworkSummary(
            interface_count=len(interfaces),
            up_interface_count=sum(1 for item in interfaces if item.is_up),
            ipv4_count=sum(1 for item in interfaces for address in item.addresses if address.family == "ipv4"),
            ipv6_count=sum(1 for item in interfaces for address in item.addresses if address.family == "ipv6"),
            public_address_count=sum(1 for item in interfaces for address in item.addresses if address.category == "public"),
        )
        return SystemNetworkSnapshot(
            hostname=hostname,
            fqdn=fqdn,
            platform=platform_name,
            source=source,
            namespace_scope=namespace_scope,  # type: ignore[arg-type]
            scope_note=scope_note,
            summary=summary,
            interfaces=interfaces,
            raw_text=raw_text,
        )

    def _parse_linux_ip_json(self, payload: str) -> list[NetworkInterface]:
        parsed = json.loads(payload)
        interfaces: list[NetworkInterface] = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            flags = [str(flag) for flag in item.get("flags") or []]
            state = str(item.get("operstate") or ("UP" if "UP" in flags else "DOWN")).upper()
            mac_address = self._normalize_mac(str(item.get("address") or ""))
            addresses = [
                self._make_address(
                    family=str(addr.get("family") or ""),
                    address=str(addr.get("local") or ""),
                    prefix_length=addr.get("prefixlen"),
                    broadcast=addr.get("broadcast"),
                    scope=addr.get("scope"),
                    label=addr.get("label"),
                )
                for addr in item.get("addr_info") or []
                if isinstance(addr, dict) and addr.get("local")
            ]
            addresses = [address for address in addresses if address is not None]
            interfaces.append(
                NetworkInterface(
                    name=str(item.get("ifname") or "unknown"),
                    state=state,
                    is_up=("UP" in flags) or state == "UP",
                    mtu=self._safe_int(item.get("mtu")),
                    mac_address=mac_address,
                    flags=flags,
                    interface_type=self._classify_interface_type(
                        name=str(item.get("ifname") or ""),
                        link_type=str(item.get("link_type") or ""),
                        flags=flags,
                    ),
                    addresses=addresses,
                )
            )
        return interfaces

    def _collect_socket_interfaces(self) -> list[NetworkInterface]:
        records: dict[str, NetworkInterface] = {}
        try:
            host_entries = socket.getaddrinfo(socket.gethostname(), None, proto=socket.IPPROTO_TCP)
        except socket.gaierror:
            host_entries = []

        # 在没有 ip/ifconfig 的环境下退回到 socket 级别，至少把可解析到的地址展示出来。
        for family, _, _, _, sockaddr in host_entries:
            family_name = "ipv4" if family == socket.AF_INET else "ipv6" if family == socket.AF_INET6 else ""
            if not family_name:
                continue
            address_value = sockaddr[0]
            interface = records.setdefault(
                "host",
                NetworkInterface(
                    name="host",
                    display_name="hostname resolution",
                    state="UNKNOWN",
                    is_up=True,
                    interface_type="fallback",
                    addresses=[],
                ),
            )
            address = self._make_address(
                family="inet" if family_name == "ipv4" else "inet6",
                address=address_value,
                prefix_length=None,
                broadcast=None,
                scope=None,
                label=None,
            )
            if address is None:
                continue
            if all(existing.address != address.address for existing in interface.addresses):
                interface.addresses.append(address)
        return list(records.values())

    def _make_address(
        self,
        *,
        family: str,
        address: str,
        prefix_length: Any,
        broadcast: Any,
        scope: Any,
        label: Any,
    ) -> NetworkAddress | None:
        normalized_family = {"inet": "ipv4", "inet6": "ipv6"}.get(family.lower())
        if not normalized_family or not address:
            return None
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            return None

        category = "public"
        if parsed.is_loopback:
            category = "loopback"
        elif parsed.is_link_local:
            category = "link_local"
        elif parsed.is_multicast:
            category = "multicast"
        elif parsed.is_private:
            category = "private"

        return NetworkAddress(
            family=normalized_family,  # type: ignore[arg-type]
            address=address,
            prefix_length=self._safe_int(prefix_length),
            broadcast=str(broadcast) if broadcast else None,
            scope=str(scope) if scope else None,
            label=str(label) if label else None,
            is_loopback=parsed.is_loopback,
            is_private=parsed.is_private,
            is_link_local=parsed.is_link_local,
            is_multicast=parsed.is_multicast,
            category=category,  # type: ignore[arg-type]
        )

    def _classify_interface_type(self, *, name: str, link_type: str, flags: list[str]) -> str:
        lowered = name.lower()
        if "LOOPBACK" in flags or lowered == "lo":
            return "loopback"
        if lowered.startswith(("docker", "br-", "veth", "virbr", "cni")):
            return "virtual"
        if lowered.startswith(("wl", "wlan", "wifi")):
            return "wireless"
        if link_type:
            return link_type
        return "ethernet"

    def _detect_namespace_scope(self, system_name: str) -> str:
        if system_name != "linux":
            return "unknown"
        if Path("/.dockerenv").exists():
            return "container"
        cgroup_path = Path("/proc/1/cgroup")
        if cgroup_path.exists():
            try:
                content = cgroup_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                content = ""
            if any(marker in content for marker in ("docker", "kubepods", "containerd", "podman")):
                return "container"
        return "host"

    def _scope_note(self, namespace_scope: str) -> str:
        if namespace_scope == "host":
            return "当前结果来自 AetherCore 后端所在宿主机的网络命名空间。"
        if namespace_scope == "container":
            return "当前结果来自 AetherCore 后端进程所在容器的网络命名空间，不一定等于物理宿主机网卡。"
        return "当前结果来自 AetherCore 后端进程可见的网络命名空间。"

    def _normalize_mac(self, value: str) -> str | None:
        normalized = value.strip()
        if not normalized or normalized == "00:00:00:00:00:00":
            return None
        return normalized

    def _safe_int(self, value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _run_command(self, command: list[str]) -> str | None:
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                timeout=6,
                check=False,
            )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            return None
        if completed.returncode != 0:
            return None
        return self._decode_output(completed.stdout).strip()

    def _decode_output(self, payload: bytes) -> str:
        if not payload:
            return ""

        preferred = locale.getpreferredencoding(False) or "utf-8"
        candidates: list[str] = ["utf-8", preferred]
        if platform.system().lower() == "windows":
            candidates.extend(["mbcs", "oem", "cp936", "gbk"])

        seen: set[str] = set()
        for candidate in candidates:
            normalized = candidate.lower()
            if normalized in seen:
                continue
            seen.add(normalized)
            try:
                return payload.decode(candidate)
            except (LookupError, UnicodeDecodeError):
                continue
        return payload.decode(preferred, errors="replace")


system_network_service = SystemNetworkService()
