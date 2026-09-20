from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.sandbox.manager import sandbox_manager
from app.services.store import store_service


class WorkspaceService:
    """管理共享 Workspace 的生命周期与一次性布局迁移。"""

    def create_workspace(
        self,
        owner_session_id: str,
        *,
        owner_conversation_id: str | None = None,
        baseline_root: str = "",
    ) -> dict[str, Any]:
        workspace = store_service.create_workspace(
            owner_session_id=owner_session_id,
            owner_conversation_id=owner_conversation_id,
            baseline_root=baseline_root,
        )
        sandbox_manager.ensure_workspace(
            str(workspace["workspace_id"]),
            owner_session_id,
            Path(baseline_root) if baseline_root else None,
        )
        return workspace

    def attach_session(
        self,
        workspace_id: str,
        session_id: str,
        *,
        role: str,
        parent_session_id: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        return store_service.attach_workspace_session(
            workspace_id=workspace_id,
            session_id=session_id,
            conversation_id=conversation_id,
            role=role,
            parent_session_id=parent_session_id,
        )

    def resolve_workspace(self, workspace_id: str, owner_session_id: str) -> Any:
        workspace = store_service.get_workspace(workspace_id)
        if workspace is None:
            raise LookupError("Workspace 不存在")
        baseline_root = Path(str(workspace["baseline_root"])) if workspace.get("baseline_root") else None
        return sandbox_manager.ensure_workspace(workspace_id, owner_session_id, baseline_root)

    def migrate_legacy_layout(self) -> None:
        """把旧的 session-owned sandbox 一次性迁移为共享 Workspace。"""
        self._plan_legacy_sessions()
        for entry in store_service.list_workspace_migration_journal(only_pending=True):
            self._reconcile_migration_entry(entry)

    def _plan_legacy_sessions(self) -> None:
        sessions_root = settings.sessions_root.resolve()
        sessions_root.mkdir(parents=True, exist_ok=True)
        for session_dir in sessions_root.iterdir():
            if not session_dir.is_dir() or session_dir.name in {"workspaces", "session-state"}:
                continue
            if not session_dir.name.startswith("sess_"):
                continue
            session_id = session_dir.name
            source_root = session_dir / "sandbox"
            if not source_root.exists():
                continue

            workspace = store_service.get_workspace_by_session(session_id)
            if workspace is None:
                payload = self._read_legacy_metadata(source_root)
                workspace = self.create_workspace(
                    session_id,
                    baseline_root=str(payload.get("baseline_root") or ""),
                )
                store_service.attach_workspace_session(
                    workspace_id=str(workspace["workspace_id"]),
                    session_id=session_id,
                    conversation_id=payload.get("conversation_id"),
                    role="owner",
                    parent_session_id=None,
                )

            member = store_service.get_workspace_member(session_id) or {}
            role = str(member.get("role") or "owner")
            if role in {"subagent", "orphan_subagent"}:
                parent_session_id = str(member.get("parent_session_id") or "")
                parent_workspace = (
                    store_service.get_workspace_by_session(parent_session_id)
                    if parent_session_id
                    else None
                )
                if parent_workspace is not None:
                    workspace = parent_workspace
                    destination_root = (
                        settings.sessions_root
                        / "workspaces"
                        / str(workspace["workspace_id"])
                        / "sandbox"
                        / "work"
                        / "_migrated"
                        / "subagents"
                        / session_id
                    )
                    mode = "subagent_merge"
                else:
                    destination_root = self._workspace_sandbox_root(str(workspace["workspace_id"]))
                    mode = "workspace"
            else:
                destination_root = self._workspace_sandbox_root(str(workspace["workspace_id"]))
                mode = "workspace"

            store_service.upsert_workspace_migration_journal(
                session_id=session_id,
                workspace_id=str(workspace["workspace_id"]),
                source_root=str(source_root.resolve()),
                destination_root=str(destination_root.resolve()),
                mode=mode,
                status="pending",
            )

    def _reconcile_migration_entry(self, entry: dict[str, Any]) -> None:
        session_id = str(entry["session_id"])
        workspace_id = str(entry["workspace_id"])
        source_root = Path(str(entry["source_root"]))
        destination_root = Path(str(entry["destination_root"]))
        mode = str(entry["mode"])
        try:
            self._write_session_state(source_root, session_id, workspace_id)
            if source_root.exists():
                destination_root.parent.mkdir(parents=True, exist_ok=True)
                self._merge_directory(source_root, destination_root)
                if mode == "workspace":
                    (destination_root / "metadata" / "session.json").unlink(missing_ok=True)
                self._remove_legacy_session_directory(source_root.parent)
            store_service.upsert_workspace_migration_journal(
                session_id=session_id,
                workspace_id=workspace_id,
                source_root=str(source_root),
                destination_root=str(destination_root),
                mode=mode,
                status="completed",
            )
        except Exception as exc:
            store_service.upsert_workspace_migration_journal(
                session_id=session_id,
                workspace_id=workspace_id,
                source_root=str(source_root),
                destination_root=str(destination_root),
                mode=mode,
                status="failed",
                error_text=str(exc),
            )
            raise RuntimeError(f"Workspace 迁移失败: {session_id}: {exc}") from exc

    def _write_session_state(self, source_root: Path, session_id: str, workspace_id: str) -> None:
        state_path = settings.sessions_root / "session-state" / session_id / "session.json"
        payload = self._read_state(state_path)
        legacy_payload = self._read_legacy_metadata(source_root)
        for key, value in legacy_payload.items():
            # 重试时不能让已搬走后的空 legacy 元数据覆盖新状态。
            payload.setdefault(key, value)
        payload["session_id"] = session_id
        payload["workspace_id"] = workspace_id
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _read_state(state_path: Path) -> dict[str, Any]:
        if not state_path.exists():
            return {}
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _read_legacy_metadata(self, source_root: Path) -> dict[str, Any]:
        metadata_path = source_root / "metadata" / "session.json"
        if not metadata_path.exists():
            return {}
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _merge_directory(self, source: Path, destination: Path) -> None:
        destination.mkdir(parents=True, exist_ok=True)
        for item in source.iterdir():
            target = destination / item.name
            if not target.exists():
                shutil.move(str(item), str(target))
            elif item.is_dir() and target.is_dir():
                self._merge_directory(item, target)
            else:
                suffix = f".conflict-{uuid.uuid4().hex[:8]}"
                shutil.move(str(item), str(target.with_name(target.name + suffix)))
        source.rmdir()

    def _remove_legacy_session_directory(self, session_dir: Path) -> None:
        resolved = session_dir.resolve()
        sessions_root = settings.sessions_root.resolve()
        if resolved == sessions_root or sessions_root not in resolved.parents:
            raise RuntimeError("拒绝删除 sessions_root 之外的 legacy 目录")
        if not any(resolved.iterdir()):
            resolved.rmdir()

    @staticmethod
    def _workspace_sandbox_root(workspace_id: str) -> Path:
        return settings.sessions_root / "workspaces" / workspace_id / "sandbox"


workspace_service = WorkspaceService()
