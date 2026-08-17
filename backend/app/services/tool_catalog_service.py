"""Versioned host tool catalog with atomic, source-aware mutations."""

from __future__ import annotations

import copy
import hashlib
import json
import threading
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from app.services.session_types import AgentSession


MAX_HOST_TOOLS = 256
MAX_HOST_TOOL_CATALOG_BYTES = 2 * 1024 * 1024


class ToolCatalogConflictError(RuntimeError):
    """A caller attempted to mutate a stale tool catalog revision."""


class ToolCatalogValidationError(ValueError):
    """A catalog mutation would create an invalid or ambiguous catalog."""


@dataclass(frozen=True)
class HostToolCatalogSnapshot:
    revision: int
    fingerprint: str
    descriptors: tuple[dict[str, Any], ...]
    descriptors_by_name: Mapping[str, dict[str, Any]]
    sources_by_name: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True)
class ToolCatalogMutationResult:
    revision: int
    fingerprint: str
    changed: bool
    added: tuple[str, ...]
    updated: tuple[str, ...]
    removed: tuple[str, ...]
    tool_count: int


class ToolCatalogService:
    """Owns host tool state without assigning business meaning to tool groups."""

    def __init__(self) -> None:
        self._lock = threading.RLock()

    @staticmethod
    def _canonical_descriptor(descriptor: dict[str, Any]) -> dict[str, Any]:
        normalized = copy.deepcopy(descriptor)
        name = str(normalized.get("name") or "").strip()
        if not name:
            raise ToolCatalogValidationError("工具名称不能为空")
        normalized["name"] = name
        input_schema = normalized.get("input_schema") or {}
        try:
            Draft202012Validator.check_schema(input_schema)
        except SchemaError as exc:
            raise ToolCatalogValidationError(f"工具 {name} 的 input_schema 无效：{exc.message}") from exc
        return normalized

    @staticmethod
    def _fingerprint(collections: dict[str, list[dict[str, Any]]]) -> str:
        payload = {
            source_id: sorted(descriptors, key=lambda item: str(item.get("name") or ""))
            for source_id, descriptors in sorted(collections.items())
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _validate_size(descriptors: list[dict[str, Any]]) -> None:
        if len(descriptors) > MAX_HOST_TOOLS:
            raise ToolCatalogValidationError(f"宿主工具数量不能超过 {MAX_HOST_TOOLS}")
        encoded = json.dumps(descriptors, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_HOST_TOOL_CATALOG_BYTES:
            raise ToolCatalogValidationError("宿主工具目录不能超过 2 MiB")

    def _ensure_initialized(self, session: AgentSession) -> None:
        if not session.host_tool_collections and session.host_tools:
            collections: dict[str, list[dict[str, Any]]] = {}
            for descriptor in session.host_tools:
                name = str(descriptor.get("name") or "")
                source_id = str(session.host_tool_sources.get(name) or "host:legacy")
                collections.setdefault(source_id, []).append(copy.deepcopy(descriptor))
            session.host_tool_collections = collections
        descriptors, sources = self._compose(session.host_tool_collections)
        session.host_tools = descriptors
        session.host_tool_sources = {
            name: source_ids[0]
            for name, source_ids in sources.items()
            if source_ids
        }
        fingerprint = self._fingerprint(session.host_tool_collections)
        if not session.host_tools_fingerprint:
            session.host_tools_fingerprint = fingerprint
        if session.host_tools and session.host_tools_revision < 1:
            session.host_tools_revision = 1

    @staticmethod
    def _assert_revision(session: AgentSession, expected_revision: int | None) -> None:
        if expected_revision is not None and expected_revision != session.host_tools_revision:
            raise ToolCatalogConflictError(
                f"工具目录版本冲突：提交版本为 {expected_revision}，当前版本为 {session.host_tools_revision}"
            )

    def snapshot(self, session: AgentSession) -> HostToolCatalogSnapshot:
        with self._lock:
            self._ensure_initialized(session)
            descriptors = tuple(copy.deepcopy(session.host_tools))
            by_name = {str(item["name"]): item for item in descriptors}
            _, sources = self._compose(session.host_tool_collections)
            return HostToolCatalogSnapshot(
                revision=session.host_tools_revision,
                fingerprint=session.host_tools_fingerprint,
                descriptors=descriptors,
                descriptors_by_name=MappingProxyType(by_name),
                sources_by_name=MappingProxyType(sources),
            )

    @staticmethod
    def _compose(
        collections: dict[str, list[dict[str, Any]]],
    ) -> tuple[list[dict[str, Any]], dict[str, tuple[str, ...]]]:
        by_name: dict[str, dict[str, Any]] = {}
        sources: dict[str, list[str]] = {}
        for source_id, descriptors in sorted(collections.items()):
            for descriptor in descriptors:
                name = str(descriptor.get("name") or "")
                existing = by_name.get(name)
                if existing is not None and existing != descriptor:
                    owners = "、".join(sources.get(name, []))
                    raise ToolCatalogValidationError(
                        f"工具 {name} 在来源 {owners} 与 {source_id} 中定义不一致"
                    )
                by_name[name] = copy.deepcopy(descriptor)
                sources.setdefault(name, []).append(source_id)
        return (
            [by_name[name] for name in sorted(by_name)],
            {name: tuple(values) for name, values in sources.items()},
        )

    def replace(
        self,
        session: AgentSession,
        descriptors: list[dict[str, Any]],
        *,
        source_id: str,
        replace_all: bool,
        expected_revision: int | None = None,
    ) -> ToolCatalogMutationResult:
        source_id = source_id.strip()
        if not source_id:
            raise ToolCatalogValidationError("source_id 不能为空")
        normalized = [self._canonical_descriptor(item) for item in descriptors]
        names = [str(item["name"]) for item in normalized]
        if len(names) != len(set(names)):
            raise ToolCatalogValidationError("同一次更新中不能包含重名工具")

        with self._lock:
            self._ensure_initialized(session)
            self._assert_revision(session, expected_revision)
            current = {str(item["name"]): copy.deepcopy(item) for item in session.host_tools}
            collections = copy.deepcopy(session.host_tool_collections)
            if replace_all:
                collections = {source_id: normalized} if normalized else {}
            else:
                if normalized:
                    collections[source_id] = normalized
                else:
                    collections.pop(source_id, None)
            return self._commit(session, current, collections)

    def apply_operations(
        self,
        session: AgentSession,
        *,
        source_id: str,
        upserts: list[dict[str, Any]],
        removals: list[str],
        expected_revision: int | None = None,
    ) -> ToolCatalogMutationResult:
        source_id = source_id.strip()
        if not source_id:
            raise ToolCatalogValidationError("source_id 不能为空")
        normalized = [self._canonical_descriptor(item) for item in upserts]
        upsert_names = [str(item["name"]) for item in normalized]
        remove_names = [str(name).strip() for name in removals if str(name).strip()]
        if len(upsert_names) != len(set(upsert_names)) or len(remove_names) != len(set(remove_names)):
            raise ToolCatalogValidationError("同一次更新中不能重复操作同名工具")
        if set(upsert_names) & set(remove_names):
            raise ToolCatalogValidationError("同一次更新中不能同时新增和删除同名工具")

        with self._lock:
            self._ensure_initialized(session)
            self._assert_revision(session, expected_revision)
            current = {str(item["name"]): copy.deepcopy(item) for item in session.host_tools}
            collections = copy.deepcopy(session.host_tool_collections)
            source_tools = {
                str(item["name"]): item
                for item in collections.get(source_id, [])
            }
            for name in remove_names:
                source_tools.pop(name, None)
            for item in normalized:
                name = str(item["name"])
                source_tools[name] = item
            if source_tools:
                collections[source_id] = [source_tools[name] for name in sorted(source_tools)]
            else:
                collections.pop(source_id, None)
            return self._commit(session, current, collections)

    def _commit(
        self,
        session: AgentSession,
        current: dict[str, dict[str, Any]],
        collections: dict[str, list[dict[str, Any]]],
    ) -> ToolCatalogMutationResult:
        descriptors, sources = self._compose(collections)
        next_by_name = {str(item["name"]): item for item in descriptors}
        self._validate_size(descriptors)
        next_fingerprint = self._fingerprint(collections)
        added = tuple(sorted(set(next_by_name) - set(current)))
        removed = tuple(sorted(set(current) - set(next_by_name)))
        updated = tuple(
            sorted(name for name in set(current) & set(next_by_name) if current[name] != next_by_name[name])
        )
        changed = next_fingerprint != session.host_tools_fingerprint
        if changed:
            session.host_tools = descriptors
            session.host_tool_collections = copy.deepcopy(collections)
            session.host_tool_sources = {
                name: source_ids[0]
                for name, source_ids in sources.items()
                if source_ids
            }
            session.host_tools_revision += 1
            session.host_tools_fingerprint = next_fingerprint
            session.touch()
        return ToolCatalogMutationResult(
            revision=session.host_tools_revision,
            fingerprint=session.host_tools_fingerprint,
            changed=changed,
            added=added if changed else (),
            updated=updated if changed else (),
            removed=removed if changed else (),
            tool_count=len(session.host_tools),
        )


tool_catalog_service = ToolCatalogService()
