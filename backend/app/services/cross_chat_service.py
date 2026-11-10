"""授权后的跨会话读取、持久投递和运行内追加。"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid

from app.services.context.message_adapter import context_message_adapter
from app.services.cross_chat_store import cross_chat_store, timestamp
from app.services.durable_io import durable_io, finish_on_cancel
from app.services.lifecycle_service import ServiceDraining, lifecycle_service
from app.services.runtime_state import runtime_state_service
from app.services.session_service import session_service
from app.services.store import store_service
from app.services.tool_result_service import ToolExecutionResult
from app.services.transcript_service import transcript_service


logger = logging.getLogger(__name__)
APPROVE = "确认发送"
IDENTITY_FIELDS = ("owner_user_id", "platform_id", "external_user_id", "external_org_id")
MODE_LABELS = {"queue": "等待空闲后新开一轮", "start": "仅在空闲时立即开启一轮", "steer": "追加到指定运行的下一安全边界"}


class CrossChatDenied(LookupError):
    pass


class CrossChatService:
    def __init__(self) -> None:
        self._worker: asyncio.Task | None = None
        self._wake = asyncio.Event()

    @staticmethod
    def _eligible(conversation: dict | None) -> bool:
        if not conversation or conversation.get("visibility") != "normal" or conversation.get("deleted_at") or conversation.get("archived_at"):
            return False
        owner, platform, external, _ = (conversation.get(key) for key in IDENTITY_FIELDS)
        return (owner is not None and external is None) or (owner is None and platform is not None and bool(external))

    def _source(self, session) -> dict:
        source = store_service.get_conversation_by_session(session.session_id)
        if not self._eligible(source):
            raise CrossChatDenied("当前会话没有跨会话操作权限。")
        return source

    def _peer(self, source: dict, conversation_id: str) -> dict:
        target = store_service.get_conversation(conversation_id)
        if (not self._eligible(source) or not self._eligible(target)
                or target["conversation_id"] == source["conversation_id"]
                or any(source.get(key) != target.get(key) for key in IDENTITY_FIELDS)):
            raise CrossChatDenied("目标会话不存在或不可访问。")
        return target

    @staticmethod
    def _run_summary(target: dict) -> dict:
        run = store_service.get_active_agent_run_for_session(target["session_id"])
        return {"run_id": run["run_id"], "status": run["status"]} if run else {"status": "idle"}

    def _list(self, session, args: dict) -> dict:
        source = self._source(session)
        cursor = args.get("cursor")
        before = (cursor["updated_at"], cursor["conversation_id"]) if cursor else None
        limit = args.get("limit", 10)
        rows = cross_chat_store.list_peers(source, args.get("query", "").strip(), before, limit + 1)
        items = []
        for row in rows[:limit]:
            items.append({key: row[key] for key in ("conversation_id", "title", "updated_at")}
                         | {"active_run": self._run_summary(row)})
        return {
            "items": items,
            "next_cursor": ({key: rows[limit - 1][key] for key in ("updated_at", "conversation_id")}
                            if len(rows) > limit else None),
        }

    @staticmethod
    def _visible_text(item: dict) -> str | None:
        if item.get("role") == "user":
            return str(item.get("content") or "")
        if item.get("role") == "assistant":
            return "\n".join(str(block.get("content") or "") for block in item.get("blocks", [])
                             if block.get("kind") == "content") or None
        if item.get("role") == "elicitation_response":
            return "\n".join([str(item.get("title") or ""),
                             *(f"{answer['header']}: {answer['value']}" for answer in item.get("answers", []))])
        return None

    def _read(self, session, args: dict) -> dict:
        from app.services.audit_transcript_service import audit_transcript_service

        target = self._peer(self._source(session), args["conversation_id"])
        fragment = args.get("fragment")
        before = fragment["position"] + 1 if fragment else args.get("before")
        limit = 1 if fragment else args.get("limit", 6)
        try:
            page = audit_transcript_service.read_page(target, limit=limit, before=before)
        except LookupError:
            if fragment:
                raise CrossChatDenied("消息位置已变化，请重新读取会话。") from None
            page = {"transcript": [], "transcript_page": {"start": 0}}
        start = page["transcript_page"]["start"]
        items = []
        for position, item in enumerate(page["transcript"], start):
            if fragment and (position != fragment["position"] or item.get("id") != fragment["message_id"]):
                raise CrossChatDenied("消息位置已变化，请重新读取会话。")
            body = self._visible_text(item)
            if body is None:
                continue
            offset = fragment["offset"] if fragment else 0
            end = min(offset + (4000 if fragment else 1200), len(body))
            items.append({
                "position": position, "message_id": item["id"], "role": item["role"],
                "text": body[offset:end], "timestamp": item.get("timestamp"),
                "next_fragment": ({"position": position, "message_id": item["id"], "offset": end}
                                  if end < len(body) else None),
            })
        if fragment and not items:
            raise CrossChatDenied("目标消息不可读取，请重新读取会话。")
        return {"conversation_id": target["conversation_id"], "title": target["title"],
                "active_run": self._run_summary(target), "items": items,
                "next_before": start if not fragment and start > 0 else None}

    def _request_send(self, session, args: dict) -> ToolExecutionResult:
        source = self._source(session)
        target = self._peer(source, args["conversation_id"])
        active = store_service.get_active_agent_run_for_session(session.session_id)
        if not active or active["status"] != "running" or active["run_id"] != session.current_run_id():
            raise CrossChatDenied("跨会话发送只能从当前运行发起。")
        if runtime_state_service.get_elicitation(session).pending:
            raise CrossChatDenied("当前会话已有待回答的问题，请先处理。")
        message = args["message"].strip()
        if not message:
            raise CrossChatDenied("消息正文不能为空。")
        mode = args.get("mode", "queue")
        expected = args.get("expected_run_id")
        target_run = self._run_summary(target)
        if mode == "steer" and (not expected or target_run.get("run_id") != expected or target_run["status"] != "running"):
            raise CrossChatDenied("指定的目标运行已变化或未在执行。请刷新会话状态。")
        if mode != "steer" and expected:
            raise CrossChatDenied("expected_run_id 只用于定向追加。")
        if mode == "start" and target_run["status"] != "idle":
            raise CrossChatDenied("目标会话正在执行。可以请求排队发送。")
        delivery_id = f"xchat_{uuid.uuid4().hex}"
        cross_chat_store.create(delivery_id, source["conversation_id"], active["run_id"], target["conversation_id"],
                                expected, mode, message)
        try:
            target_title = " ".join(str(target["title"]).split())
            prompt = (f"目标：{target_title}（{target['conversation_id']}）\n"
                      f"方式：{MODE_LABELS[mode]}\n"
                      + (f"指定运行：{expected}\n" if expected else "") + f"\n发送正文\n{message}")
            question = runtime_state_service.request_user_input(session, {
                "id": delivery_id, "kind": "approval", "title": "确认跨会话发送", "blocking": True,
                "questions": [{"id": "delivery", "header": "核对目标及正文", "question": prompt,
                               "options": [{"label": APPROVE}, {"label": "不发送"}], "allow_other": False}],
            })
        except Exception:
            cross_chat_store.transition(delivery_id, "awaiting_approval", "failed", detail="无法创建确认问题。")
            raise
        state = runtime_state_service.get_elicitation(session)
        return ToolExecutionResult.success(
            "cross_chat.approval", "消息正在等待用户确认。",
            {"delivery_id": delivery_id, "status": "awaiting_approval"},
            runtime_events=[{"type": "ask_requested", "payload": {
                "request": question.model_dump(mode="json"), "snapshot": state.model_dump(mode="json"),
            }}],
            control={"type": "await_user_input", "request_id": question.id, "blocking": True},
        )

    def _owned_delivery(self, session, delivery_id: str) -> dict:
        source = self._source(session)
        delivery = cross_chat_store.get(delivery_id)
        if not delivery or delivery["source_conversation_id"] != source["conversation_id"]:
            raise CrossChatDenied("投递不存在或不可访问。")
        return delivery

    def _status(self, session, delivery_id: str) -> dict:
        delivery = self._owned_delivery(session, delivery_id)
        info = {key: delivery[key] for key in
                ("delivery_id", "target_conversation_id", "target_run_id", "mode", "status", "detail", "expires_at")}
        try:
            target = self._peer(self._source(session), delivery["target_conversation_id"])
        except CrossChatDenied:
            return info
        info["target_title"] = target["title"]
        if delivery["status"] == "delivered":
            run = store_service.get_agent_run(delivery["target_run_id"])
            if run and run["session_id"] == target["session_id"]:
                info["run_status"] = run["status"]
                if run["status"] == "completed" and run.get("result_json"):
                    value = json.loads(run["result_json"])
                    if isinstance(value, str):
                        info["reply_preview"] = value[:3000]
                if run["status"] == "waiting_input":
                    info["next_action"] = "目标会话正在等待用户回答，请在目标会话处理。"
        elif delivery["status"] == "queued":
            info["target_active_run"] = self._run_summary(target)
        return info

    async def tool(self, name: str, session, args: dict) -> ToolExecutionResult:
        try:
            if name == "chat_list":
                data = await durable_io(self._list, session, args)
            elif name == "chat_read":
                data = await durable_io(self._read, session, args)
            elif name == "chat_send":
                return self._request_send(session, args)
            else:
                delivery_id = args["delivery_id"]
                if name == "chat_cancel":
                    delivery = self._owned_delivery(session, delivery_id)
                    if delivery["status"] in {"awaiting_approval", "queued"}:
                        cross_chat_store.transition(delivery_id, delivery["status"], "cancelled", detail="投递已撤销。")
                deadline = asyncio.get_running_loop().time() + (args.get("wait_seconds", 10) if name == "chat_wait" else 0)
                while True:
                    data = await durable_io(self._status, session, delivery_id)
                    pending = data["status"] in {"awaiting_approval", "queued", "dispatching"}
                    running = data.get("run_status") in {"queued", "running"}
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0 or not (pending or running):
                        break
                    await asyncio.sleep(min(0.4, remaining))
            return ToolExecutionResult.success(f"cross_chat.{name}", "跨会话操作结果", data)
        except CrossChatDenied as exc:
            return ToolExecutionResult.failure("cross_chat.denied", "跨会话操作未执行",
                                               code="CROSS_CHAT_DENIED", message=str(exc))

    @staticmethod
    def _delivery_text(delivery: dict, source: dict) -> str:
        title = " ".join(str(source["title"]).split())
        return (f"[用户确认的跨会话消息 · 来源会话 {title}（{source['conversation_id']}）"
                f" · 投递 {delivery['delivery_id']}]\n"
                "此回执授权发送下面的消息。目标会话仍须遵守自己的工具和审批权限。\n\n" + delivery["message"])

    def wake(self) -> None:
        if self._worker is not None:
            self._wake.set()

    def record_authenticated_answer(self, session, answer) -> str | None:
        """仅由已鉴权的答复入口调用。后台任务不能从文件快照推断授权。"""
        delivery = cross_chat_store.get(answer.id)
        source = self._source(session)
        if not delivery or delivery["source_conversation_id"] != source["conversation_id"]:
            return None
        if delivery["status"] != "awaiting_approval":
            return delivery["status"]
        if delivery["expires_at"] <= timestamp():
            status, detail = "expired", "确认已超过投递有效期。"
        elif (answer.status == "resolved" and answer.resume_run_id == delivery["source_run_id"]
              and any(item.question_id == "delivery" and item.selected_options == [APPROVE] for item in answer.answers)):
            status, detail = "queued", None
        else:
            status, detail = "declined", "用户未确认发送。"
        if not cross_chat_store.transition(answer.id, "awaiting_approval", status, detail=detail):
            current = cross_chat_store.get(answer.id)
            return current["status"] if current else None
        self.wake()
        return status

    async def start(self) -> None:
        if self._worker is None:
            self._wake = asyncio.Event()
            self._worker = asyncio.create_task(self._run_worker(), name="cross-chat-delivery")

    async def stop(self) -> None:
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def _run_worker(self) -> None:
        while True:
            if not lifecycle_service.draining:
                try:
                    due = await durable_io(cross_chat_store.due)
                except Exception:
                    logger.exception("无法读取跨会话投递队列")
                    due = []
                for delivery in due:
                    try:
                        await self.process(delivery)
                    except Exception:
                        logger.exception("跨会话投递处理失败 %s", delivery["delivery_id"])
                        cross_chat_store.transition(delivery["delivery_id"], delivery["status"], delivery["status"], delay=3)
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=1.0)
            except TimeoutError:
                pass
            self._wake.clear()

    async def process(self, delivery: dict) -> None:
        from app.services.agent_run_service import AgentRunBusy, agent_run_service

        delivery_id, status, mode = (delivery[key] for key in ("delivery_id", "status", "mode"))
        if status not in {"awaiting_approval", "queued", "dispatching"}:
            return
        def transition(next_status, **kwargs):
            return cross_chat_store.transition(delivery_id, status, next_status, **kwargs)

        source = store_service.get_conversation(delivery["source_conversation_id"])
        try:
            target = self._peer(source, delivery["target_conversation_id"])
        except CrossChatDenied:
            transition("failed", detail="来源或目标会话已不可访问。")
            return
        if status in {"queued", "dispatching"} and mode != "steer":
            previous = store_service.find_agent_run_by_idempotency(target["conversation_id"], delivery["dispatch_key"])
            if previous is not None:
                transition("delivered", target_run_id=previous["run_id"])
                return
        if delivery["expires_at"] <= timestamp() and not (status == "dispatching" and mode == "steer"):
            transition("expired", detail="投递超时，未自动重新发送。")
            return
        if status == "awaiting_approval":
            source_run = store_service.get_agent_run(delivery["source_run_id"])
            if source_run is None or source_run["status"] in store_service.TERMINAL_AGENT_RUN_STATUSES:
                transition("cancelled", detail="来源运行已结束，未收到有效发送授权。")
            else:
                transition(status, delay=10)
            return
        active = self._run_summary(target)
        if mode == "steer":
            if active.get("run_id") == delivery["expected_run_id"] and active["status"] == "running":
                transition(status, delay=2)
            elif status == "dispatching" and await durable_io(self._persisted_steering, target, delivery_id):
                transition("delivered", target_run_id=delivery["expected_run_id"])
            else:
                transition("failed", detail="指定运行已结束或暂停，消息未追加。")
            return
        if active["status"] != "idle" or cross_chat_store.has_earlier_queued(delivery):
            if mode == "start":
                transition("failed", detail="目标会话已被占用，未启动新一轮。")
            else:
                transition("queued", delay=2)
            return
        if not transition("dispatching"):
            return

        async def dispatch():
            try:
                target_session = session_service.get_or_create(target["session_id"])
                run_id = await agent_run_service.start_chat_run(
                    target_session, self._delivery_text(delivery, source), emit_started=True,
                    client_message_id=delivery_id, idempotency_key=delivery["dispatch_key"],
                    user_message_kind="cross_chat",
                    before_admission=lambda: self._peer(store_service.get_conversation(source["conversation_id"]),
                                                        target["conversation_id"]),
                )
            except (AgentRunBusy, ServiceDraining):
                # 启动与用户普通发送竞争时，排队项保留，立即发送项返回冲突。
                queue_again = mode == "queue" or lifecycle_service.draining
                cross_chat_store.transition(delivery_id, "dispatching", "queued" if queue_again else "failed",
                                            detail=None if queue_again else "目标会话已被占用。", delay=2)
            except CrossChatDenied as exc:
                cross_chat_store.transition(delivery_id, "dispatching", "failed", detail=str(exc))
            except Exception:
                # run 已落库时用幂等键恢复回执，不重复启动。
                cross_chat_store.transition(delivery_id, "dispatching", "dispatching", delay=3)
                logger.exception("跨会话投递启动失败 %s", delivery_id)
            else:
                cross_chat_store.transition(delivery_id, "dispatching", "delivered", target_run_id=run_id)

        await finish_on_cancel(dispatch())

    @staticmethod
    def _persisted_steering(target: dict, delivery_id: str) -> bool:
        session = session_service.read_for_audit(target)
        return any(item.get("message_id") == f"msg_{delivery_id}" for item in session.messages)

    def has_steering(self, session, run_id: str) -> bool:
        return bool(session.conversation_id and cross_chat_store.steering(session.conversation_id, run_id))

    async def apply_steering(self, session, run_id: str, turn_index: int) -> list[dict]:
        if not session.conversation_id:
            return []
        committed = []
        for delivery in cross_chat_store.steering(session.conversation_id, run_id):
            delivery_id = delivery["delivery_id"]
            source = store_service.get_conversation(delivery["source_conversation_id"])
            try:
                self._peer(source, session.conversation_id)
            except CrossChatDenied as exc:
                cross_chat_store.transition(delivery_id, "queued", "failed", detail=str(exc))
                continue
            if not cross_chat_store.transition(delivery_id, "queued", "dispatching"):
                continue
            message = context_message_adapter.make_user_message(self._delivery_text(delivery, source), turn_index=turn_index)
            message["message_id"] = f"msg_{delivery_id}"
            session.messages.append(message)
            session.transcript = transcript_service.append_item(
                session.transcript, transcript_service.transcript_item_from_committed_message(message),
            )
            await session_service.persist_async(session)
            cross_chat_store.transition(delivery_id, "dispatching", "delivered", target_run_id=run_id)
            committed.append(message)
        return committed

    def inbox_cursor(self, conversation: dict) -> str | None:
        return cross_chat_store.inbox_cursor(conversation["conversation_id"]) if self._eligible(conversation) else None

    def receipts(self, session, before: str | None, limit: int) -> dict:
        source = self._source(session)
        cursor = tuple(before.rsplit("|", 1)) if before else None
        if cursor is not None and len(cursor) != 2:
            raise ValueError("投递分页游标无效。")
        rows = cross_chat_store.list_for_conversation(source["conversation_id"], cursor, limit + 1)
        items = []
        for row in rows[:limit]:
            sent = row["source_conversation_id"] == source["conversation_id"]
            peer_id = row["target_conversation_id"] if sent else row["source_conversation_id"]
            try:
                peer = self._peer(source, peer_id)
            except CrossChatDenied:
                # 源会话可确认自己发起的投递状态，但不能用历史收件凭据重新访问失权会话。
                if not sent:
                    continue
                peer = None
            run = store_service.get_agent_run(row["target_run_id"]) if row["target_run_id"] and peer else None
            items.append({key: row[key] for key in
                          ("delivery_id", "status", "mode", "detail", "message", "created_at", "expires_at")}
                         | {"direction": "sent" if sent else "received",
                            "peer_title": peer["title"] if peer else None,
                            "peer_session_id": peer["session_id"] if peer else None,
                            "run_status": run["status"] if run else None})
        anchor = rows[limit - 1] if len(rows) > limit else None
        return {"items": items, "next_cursor": f"{anchor['created_at']}|{anchor['delivery_id']}" if anchor else None}

    def cancel_queued(self, session, delivery_id: str) -> bool:
        self._owned_delivery(session, delivery_id)
        return cross_chat_store.transition(delivery_id, "queued", "cancelled", detail="用户已撤销投递。")


cross_chat_service = CrossChatService()
