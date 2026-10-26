"""NapCat (QQ / OneBot v11) platform adapter for Hermes Agent.

Implements the BasePlatformAdapter contract from ``gateway/platforms.base``:
``connect`` / ``disconnect`` / ``send`` plus optional helpers. Inbound QQ
messages are converted to ``MessageEvent`` objects and handed to the gateway
via ``self.handle_message``; outbound text goes through the OneBot send APIs
exposed by NapCat.

Chat id conventions (stable, string-typed, shared with the napcat toolset):

* private chats:  the plain QQ number, e.g. ``"123456789"``
* group chats:   ``"g"`` + group id, e.g. ``"g987654321"``

Configuration precedence: ``config.yaml`` ``platforms.napcat.extra`` first,
environment variables (``NAPCAT_*``) as fallback, matching other adapters.
"""

from __future__ import annotations

import datetime
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Optional

from gateway.platforms.base import BasePlatformAdapter, Platform, SendResult
from gateway.platforms.event import MessageEvent, MessageType
from gateway.config import PlatformConfig

try:  # loaded as a package (pip install)
    from .onebot import OneBotClient, OneBotError
except ImportError:  # loaded as a directory plugin
    from onebot import OneBotClient, OneBotError

logger = logging.getLogger("hermes.napcat.adapter")

GROUP_PREFIX = "g"


def _chat_id_for(message_type: str, raw_id: Any) -> str:
    chat_id = str(raw_id)
    if message_type == "group":
        return f"{GROUP_PREFIX}{chat_id}"
    return chat_id


def parse_chat_id(chat_id: str) -> tuple[str, int]:
    """Return (message_type, numeric_id) from an adapter-style chat id."""
    chat_id = str(chat_id).strip()
    if chat_id.startswith(GROUP_PREFIX):
        return "group", int(chat_id[len(GROUP_PREFIX):])
    return "private", int(chat_id)


class NapCatPlatformAdapter(BasePlatformAdapter):
    """Relays QQ messages between NapCat and the Hermes gateway."""

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform("napcat"))
        extra = dict(config.extra or {}) if hasattr(config, "extra") else {}
        self.ws_url: str = (
            extra.get("ws_url")
            or os.getenv("NAPCAT_WS_URL")
            or "ws://127.0.0.1:3001"
        )
        self.token: str = extra.get("token") or os.getenv("NAPCAT_TOKEN", "")
        # Respond to every group message (not just @-mentions)?
        self.group_all_messages: bool = str(
            extra.get("group_all_messages")
            or os.getenv("NAPCAT_GROUP_ALL_MESSAGES", "")
        ).lower() in ("1", "true", "yes", "on")

        self.client = OneBotClient(self.ws_url, self.token, on_message_event=self._on_event)

    # ------------------------------------------------------------ lifecycle

    async def connect(self, is_reconnect: bool = False) -> bool:
        await self.client.start()
        ok = await self.client.wait_connected(timeout=30)
        if not ok:
            self._set_fatal_error(f"could not connect to NapCat at {self.ws_url}")
            return False
        try:
            login = await self.client.call("get_login_info")
            self.client.self_id = str(login.get("user_id", ""))
            logger.info("NapCat logged in as %s (%s)",
                        login.get("nickname"), self.client.self_id)
        except Exception:  # noqa: BLE001 - login info is best-effort
            logger.warning("get_login_info failed; continuing without it")
        self._mark_connected()
        return True

    async def disconnect(self) -> None:
        await self.client.stop()
        self._mark_disconnected()

    # -------------------------------------------------------------- inbound

    async def _on_event(self, event: dict) -> None:
        message_type = event.get("message_type")  # "private" | "group"
        user_id = str(event.get("user_id") or event.get("sender", {}).get("user_id") or "")
        sender = event.get("sender") or {}
        user_name = sender.get("card") or sender.get("nickname") or user_id
        raw_message = event.get("message") or []
        text, at_me, mentioned_ids = self._extract_text(raw_message, event.get("raw_message"))

        if message_type == "group":
            group_id = event.get("group_id")
            chat_id = _chat_id_for("group", group_id)
            chat_name = str(group_id)
            if not self.group_all_messages and not at_me:
                logger.debug("group %s message without @me; ignoring", group_id)
                return
        elif message_type == "private":
            chat_id = _chat_id_for("private", user_id)
            chat_name = user_name
        else:
            return

        if not text.strip():
            return

        message_event = MessageEvent(
            text=text.strip(),
            message_type=MessageType.TEXT,
            source=self.build_source(
                chat_id=chat_id,
                chat_name=str(chat_name),
                chat_type="group" if message_type == "group" else "dm",
                user_id=user_id,
                user_name=str(user_name),
            ),
            message_id=str(event.get("message_id") or uuid.uuid4().hex),
            timestamp=datetime.datetime.now(),
        )
        await self.handle_message(message_event)

    def _extract_text(self, message: Any, raw_message: Optional[str]) -> tuple[str, bool, set[str]]:
        """Flatten a OneBot message (array or CQ string) to plain text.

        Returns (text, at_me, mentioned_ids). Non-text segments degrade to a
        ``[type]`` placeholder so the agent at least knows media exists.
        """
        mentioned: set[str] = set()
        if isinstance(message, str):
            return (message or raw_message or ""), False, mentioned
        parts: list[str] = []
        for segment in message or []:
            seg_type = segment.get("type")
            data = segment.get("data") or {}
            if seg_type == "text":
                parts.append(data.get("text", ""))
            elif seg_type == "at":
                qq = str(data.get("qq", ""))
                if qq:
                    mentioned.add(qq)
                if data.get("qq") == "all":
                    parts.append("@全体成员 ")
            else:
                parts.append(f"[{seg_type}]")
        text = "".join(parts) or (raw_message or "")
        self_id = self.client.self_id
        at_me = bool(self_id and self_id in mentioned)
        return text, at_me, mentioned

    # ------------------------------------------------------------- outbound

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> SendResult:
        try:
            message_type, numeric_id = parse_chat_id(chat_id)
        except ValueError as exc:
            return SendResult(success=False, error=f"invalid chat_id '{chat_id}': {exc}")

        segments: list[dict] = []
        if reply_to:
            segments.append({"type": "reply", "data": {"id": str(reply_to)}})
        segments.append({"type": "text", "data": {"text": content}})

        try:
            if message_type == "group":
                data = await self.client.call(
                    "send_group_msg", {"group_id": numeric_id, "message": segments}
                )
            else:
                data = await self.client.call(
                    "send_private_msg", {"user_id": numeric_id, "message": segments}
                )
            return SendResult(success=True, message_id=str((data or {}).get("message_id", "")))
        except (OneBotError, ConnectionError, TimeoutError) as exc:
            logger.error("send to %s failed: %s", chat_id, exc)
            return SendResult(success=False, error=str(exc))

    async def send_typing(self, chat_id: str) -> None:
        # OneBot v11 has no typing indicator; no-op.
        return

    async def get_chat_info(self, chat_id: str) -> dict:
        message_type, numeric_id = parse_chat_id(chat_id)
        if message_type == "group":
            data = await self.client.call(
                "get_group_info", {"group_id": numeric_id, "no_cache": False}
            )
            return {
                "id": chat_id,
                "title": data.get("group_name", ""),
                "member_count": data.get("member_count"),
            }
        data = await self.client.call("get_stranger_info", {"user_id": numeric_id})
        return {"id": chat_id, "title": data.get("nickname", ""), "member_count": None}

    # -------------------------------------------------------- cron delivery

    async def standalone_send(
        self,
        chat_id: str,
        content: str,
        media_files: Optional[list[str]] = None,
        thread_id: Optional[str] = None,
        force_document: bool = False,
    ) -> None:
        """Cron/standalone sender: text plus optional local files.

        Files are delivered via ``upload_group_file`` / ``upload_private_file``
        using NapCat's ``file://`` URI scheme. Note that NapCat must be able to
        resolve the path: when NapCat runs in a separate container, mount the
        shared directory into it as well.
        """
        if content:
            result = await self.send(chat_id, content)
            if not result.success:
                logger.error("standalone_send text to %s failed: %s", chat_id, result.error)
        message_type, numeric_id = parse_chat_id(chat_id)
        for file_path in media_files or []:
            path = Path(file_path).expanduser()
            uri = path.resolve().as_uri()
            try:
                if message_type == "group":
                    await self.client.call(
                        "upload_group_file",
                        {"group_id": numeric_id, "file": uri, "name": path.name},
                        timeout=180,
                    )
                else:
                    await self.client.call(
                        "upload_private_file",
                        {"user_id": numeric_id, "file": uri, "name": path.name},
                        timeout=180,
                    )
            except (OneBotError, ConnectionError, TimeoutError) as exc:
                logger.error("standalone_send file %s to %s failed: %s", path, chat_id, exc)
