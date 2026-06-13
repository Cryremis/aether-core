from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


AddressFamily = Literal["ipv4", "ipv6"]
AddressCategory = Literal["public", "private", "loopback", "link_local", "multicast", "unknown"]
NamespaceScope = Literal["host", "container", "unknown"]


class NetworkAddress(BaseModel):
    family: AddressFamily
    address: str
    prefix_length: int | None = None
    netmask: str | None = None
    broadcast: str | None = None
    scope: str | None = None
    label: str | None = None
    is_loopback: bool = False
    is_private: bool = False
    is_link_local: bool = False
    is_multicast: bool = False
    category: AddressCategory = "unknown"


class NetworkInterface(BaseModel):
    name: str
    display_name: str | None = None
    state: str = "UNKNOWN"
    is_up: bool = False
    mtu: int | None = None
    mac_address: str | None = None
    flags: list[str] = Field(default_factory=list)
    interface_type: str | None = None
    addresses: list[NetworkAddress] = Field(default_factory=list)


class NetworkSummary(BaseModel):
    interface_count: int = 0
    up_interface_count: int = 0
    ipv4_count: int = 0
    ipv6_count: int = 0
    public_address_count: int = 0


class SystemNetworkSnapshot(BaseModel):
    hostname: str
    fqdn: str | None = None
    platform: str
    source: str
    namespace_scope: NamespaceScope = "unknown"
    scope_note: str
    collected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    summary: NetworkSummary = Field(default_factory=NetworkSummary)
    interfaces: list[NetworkInterface] = Field(default_factory=list)
    raw_text: str | None = None
