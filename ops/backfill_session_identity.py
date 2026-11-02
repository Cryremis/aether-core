"""会话身份离线回填，默认 dry-run。

--apply --offline 仅在停止全部后端、调度器和其他写入进程后使用。
加密快照需要与部署相同的 CAPABILITY_SECRET_ENCRYPTION_KEY 环境变量。
只补空身份字段，冲突和运行中会话跳过；不修改对话正文、审计或 conversations。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.services.session_identity import SessionIdentityConflict, identity_updates


def _fernet():
    from cryptography.fernet import Fernet
    key = os.environ.get("CAPABILITY_SECRET_ENCRYPTION_KEY")
    if not key:
        raise ValueError("加密快照需要 CAPABILITY_SECRET_ENCRYPTION_KEY")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest()))


def _decode(raw):
    if raw.startswith("encrypted:"):
        raw = _fernet().decrypt(raw[len("encrypted:"):].encode()).decode()
    return json.loads(raw)


def _encode(payload, encrypted):
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return "encrypted:" + _fernet().encrypt(raw.encode()).decode() if encrypted else raw


def _backup(path, raw, label):
    backup = path.with_name(f"{label}.{hashlib.sha256(raw).hexdigest()[:16]}.backup")
    try:
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if backup.read_bytes() != raw:
            raise ValueError("已有备份校验失败，请保留现场后人工核对")
        return
    with os.fdopen(fd, "wb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def _replace(path, payload):
    temp = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def audit_and_backfill(db: Path, sessions_root: Path, *, apply=False, offline=False):
    if apply and not offline:
        raise ValueError("写入前必须停止全部后端和调度进程，并显式指定 --offline")
    db, sessions_root = db.resolve(strict=True), sessions_root.resolve(strict=True)
    conn = sqlite3.connect(db.as_uri() + ("?mode=rw" if apply else "?mode=ro"), uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    report = {"mode": "apply" if apply else "dry-run", "sessions": 0, "eligible": 0, "repaired": 0,
              "conflicts": 0, "sources": {}, "items": []}
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        ids = {r[0] for r in conn.execute("SELECT session_id FROM conversations")}
        if "context_snapshots" in tables:
            ids.update(r[0] for r in conn.execute("SELECT session_id FROM context_snapshots"))
        ids.update(p.parent.name for p in (sessions_root / "session-state").glob("*/session.json"))
        for sid in sorted(ids):
            item = {"session_id": sid, "status": "unchanged"}
            report["sessions"] += 1
            try:
                path = (sessions_root / "session-state" / sid / "session.json").resolve()
                if path.parent.parent != sessions_root / "session-state":
                    raise ValueError("会话路径超出 session-state")
                if apply:
                    conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT * FROM conversations WHERE session_id=?", (sid,)).fetchone()
                conversation = dict(row) if row else None
                snapshot = conn.execute("SELECT * FROM context_snapshots WHERE session_id=?", (sid,)).fetchone() if "context_snapshots" in tables else None
                raw_file = path.read_bytes() if path.exists() else None
                payloads = {}
                if raw_file is not None:
                    payloads["json"] = json.loads(raw_file)
                if snapshot:
                    payloads["snapshot"] = _decode(snapshot["payload"])
                updates, conflicts = {}, {}
                for source, payload in payloads.items():
                    if not isinstance(payload, dict) or payload.get("session_id") not in (None, "", sid):
                        raise ValueError("元数据格式或 session_id 不一致")
                    if int(payload.get("message_schema_version", 1)) > 3:
                        raise ValueError("不支持更新版本的元数据")
                    counts = report["sources"].setdefault(source, {"total": 0, "platform_null": 0, "platform_mismatch": 0, "external_user_mismatch": 0})
                    counts["total"] += 1
                    counts["platform_null"] += payload.get("platform_id") in (None, "")
                    for field, counter in [("platform_id", "platform_mismatch"), ("external_user_id", "external_user_mismatch")]:
                        counts[counter] += bool(conversation and payload.get(field) not in (None, "") and payload.get(field) != conversation.get(field))
                    try:
                        updates[source] = identity_updates(sid, payload, conversation)
                    except SessionIdentityConflict as exc:
                        conflicts[source] = exc.fields
                if not conversation:
                    item["status"] = "no_conversation"
                elif conversation.get("deleted_at"):
                    item["status"] = "deleted"
                elif conflicts:
                    item.update(status="conflict", fields=conflicts)
                    report["conflicts"] += 1
                elif any(updates.values()):
                    item["fields"] = {source: list(fields) for source, fields in updates.items() if fields}
                    active = "agent_runs" in tables and conn.execute("SELECT 1 FROM agent_runs WHERE session_id=? AND status NOT IN ('completed','failed','cancelled','timed_out') LIMIT 1", (sid,)).fetchone()
                    if active:
                        item["status"] = "active_run_skipped"
                    else:
                        report["eligible"] += 1
                        item["status"] = "would_repair"
                        if apply:
                            # 账本先提交，JSON 后替换；中断后重跑能补齐未完成的投影。
                            if raw_file is not None:
                                _backup(path, raw_file, "session.pre-identity")
                            if snapshot and updates.get("snapshot"):
                                path.parent.mkdir(parents=True, exist_ok=True)
                                _backup(path, snapshot["payload"].encode(), "context.pre-identity")
                                payload = {**payloads["snapshot"], **updates["snapshot"]}
                                revision = snapshot["revision"] + 1
                                body = {key: value for key, value in payload.items() if key != "ledger_revision"}
                                digest = hashlib.sha256(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
                                payload["ledger_revision"] = revision
                                conn.execute("UPDATE context_snapshots SET revision=?,digest=?,payload=? WHERE session_id=? AND revision=?",
                                    (revision, digest, _encode(payload, snapshot["payload"].startswith("encrypted:")), sid, snapshot["revision"]))
                            conn.commit()
                            if updates.get("json"):
                                payload = {**payloads["json"], **updates["json"]}
                                if snapshot:
                                    payload["ledger_revision"] = conn.execute("SELECT revision FROM context_snapshots WHERE session_id=?", (sid,)).fetchone()[0]
                                _replace(path, payload)
                            report["repaired"] += 1
                            item["status"] = "repaired"
                if apply:
                    conn.commit()
            except Exception as exc:
                conn.rollback()
                # 不输出元数据原文，防止宿主 auth 凭据进入运维报告。
                item.update(status="error", error_type=type(exc).__name__)
            report["items"].append(item)
    finally:
        conn.close()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--sessions-root", type=Path, required=True, help="storage/sessions 目录")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="默认行为，只统计不写入")
    mode.add_argument("--apply", action="store_true", help="只补空值；必须停止全部写入进程")
    parser.add_argument("--offline", action="store_true", help="确认后端、调度器及其他写入方均已停止")
    args = parser.parse_args()
    try:
        report = audit_and_backfill(args.db, args.sessions_root, apply=args.apply, offline=args.offline)
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.error(str(exc))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 2 if any(item["status"] in {"error", "conflict", "active_run_skipped"} for item in report["items"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
