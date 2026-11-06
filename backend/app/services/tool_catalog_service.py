"""Versioned host tool catalog with atomic, source-aware mutations."""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import threading
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from app.services.session_types import AgentSession


logger = logging.getLogger(__name__)

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
        for field, default in (("headers", {}), ("tags", [])):
            if normalized.get(field) is None:
                normalized[field] = copy.deepcopy(default)
            else:
                normalized.setdefault(field, copy.deepcopy(default))
        input_schema = normalized.get("input_schema") or {}
        try:
            Draft202012Validator.check_schema(input_schema)
        except SchemaError as exc:
            raise ToolCatalogValidationError(f"工具 {name} 的 input_schema 无效：{exc.message}") from exc
        return normalized

    @staticmethod
    def _fingerprint(collections: dict[str, list[dict[str, Any]]]) -> str:
        payload = {
            source_id: sorted(
                (ToolCatalogService._canonical_descriptor(item) for item in descriptors),
                key=lambda item: str(item.get("name") or ""),
            )
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
    def _execution_contract(descriptor: dict[str, Any]) -> dict[str, Any]:
        contract = {key: value for key, value in descriptor.items() if key not in {"description", "tags"}}
        contract["method"] = str(contract.get("method") or "POST").upper()
        contract.setdefault("requires_auth", True)
        contract.setdefault("auth_inject", True)
        return contract

    @staticmethod
    def _source_priority(source_id: str) -> tuple[int, str]:
        # 页面优先提供当前宿主的工具描述。跨来源执行合同仍需校验。
        rank = 0 if source_id.endswith(":page") else 2 if source_id == "host:legacy" else 1
        return rank, source_id

    @staticmethod
    def _page_source_for_cache(source_id: str, owners: list[str]) -> str | None:
        for owner in owners:
            if owner.endswith(":page"):
                cache_source = owner.removesuffix(":page") + ":on-demand"
                if source_id == cache_source or source_id.startswith(cache_source + ":"):
                    return owner
        return None

    @staticmethod
    def _compose(
        collections: dict[str, list[dict[str, Any]]], *, log_metadata_changes: bool = False,
        stale_cache_entries: dict[str, dict[str, str]] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, tuple[str, ...]]]:
        by_name: dict[str, dict[str, Any]] = {}
        sources: dict[str, list[str]] = {}
        for source_id in sorted(collections, key=ToolCatalogService._source_priority):
            for descriptor in collections[source_id]:
                canonical = ToolCatalogService._canonical_descriptor(descriptor)
                name = str(canonical["name"])
                existing = by_name.get(name)
                if existing is not None:
                    before = ToolCatalogService._execution_contract(existing)
                    after = ToolCatalogService._execution_contract(canonical)
                    changed = sorted(key for key in before.keys() | after.keys() if key not in before or key not in after or before[key] != after[key])
                    if changed:
                        # 同一宿主的页面定义替代过期的按需 schema。路由和鉴权冲突仍须拒绝。
                        if changed == ["input_schema"]:
                            page_source = ToolCatalogService._page_source_for_cache(source_id, sources[name])
                            if page_source is not None:
                                if stale_cache_entries is not None:
                                    stale_cache_entries.setdefault(source_id, {})[name] = page_source
                                continue
                        owners = "、".join(sources[name])
                        raise ToolCatalogValidationError(
                            f"工具 {name} 在来源 {owners} 与 {source_id} 中定义不一致；执行字段：{', '.join(changed)}"
                        )
                    if log_metadata_changes and any(existing.get(field) != canonical.get(field)
                                                    for field in ("description", "tags")):
                        logger.warning("Tool %s metadata differs between sources %s and %s; kept %s",
                                       name, sources[name][0], source_id, sources[name][0])
                else:
                    by_name[name] = canonical
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
        next_fingerprint = self._fingerprint(collections)
        stale_cache_entries: dict[str, dict[str, str]] = {}
        descriptors, sources = self._compose(
            collections, log_metadata_changes=next_fingerprint != session.host_tools_fingerprint,
            stale_cache_entries=stale_cache_entries,
        )
        if stale_cache_entries:
            # 持久化时清掉冲突条目，防止页面移除工具后又回退到旧 schema。
            for source_id, entries in stale_cache_entries.items():
                remaining = [
                    item for item in collections[source_id]
                    if str(item.get("name") or "").strip() not in entries
                ]
                if remaining:
                    collections[source_id] = remaining
                else:
                    del collections[source_id]
            next_fingerprint = self._fingerprint(collections)
        next_by_name = {str(item["name"]): item for item in descriptors}
        self._validate_size(descriptors)
        added = tuple(sorted(set(next_by_name) - set(current)))
        removed = tuple(sorted(set(current) - set(next_by_name)))
        updated = tuple(
            sorted(name for name in set(current) & set(next_by_name) if current[name] != next_by_name[name])
        )
        changed = next_fingerprint != session.host_tools_fingerprint
        for source_id, entries in stale_cache_entries.items():
            for name, page_source in entries.items():
                logger.warning(
                    "宿主工具 %s 的按需缓存 %s 与页面 %s 的 input_schema 不一致；保留页面定义并丢弃冲突条目",
                    name, source_id, page_source,
                )
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
