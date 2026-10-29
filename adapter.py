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
import re
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

# OneBot 11 CQ code: [CQ:type,key=value,...]  (spec escapes: &amp; &#91; &#93; &comma;)
_CQ_CODE_RE = re.compile(r"\[CQ:([a-zA-Z0-9_.-]+)((?:,[^,\]]*)*)\]")
_CQ_UNESCAPES = {"&amp;": "&", "&#91;": "[", "&#93;": "]", "&comma;": ","}


def _cq_unescape(value: str) -> str:
    for escaped, char in _CQ_UNESCAPES.items():
        value = value.replace(escaped, char)
    return value


def _parse_cq_params(raw: Optional[str]) -> dict:
    """Parse ',k=v,k=v' from a CQ code body into a dict with unescaped values."""
    if not raw:
        return {}
    params: dict[str, str] = {}
    for pair in raw.split(","):
        if not pair or "=" not in pair:
            continue
        key, _, value = pair.partition("=")
        params[key.strip()] = _cq_unescape(value)
    return params


# CQ types that map to real outbound segments; anything else stays as text.
_CQ_OUTBOUND_MAP = {
    "at": lambda d: {"type": "at", "data": {"qq": d.get("qq", "")}},
    "image": lambda d: {"type": "image", "data": {"file": d.get("file", "")}},
    "record": lambda d: {"type": "record", "data": {"file": d.get("file", "")}},
    "video": lambda d: {"type": "video", "data": {"file": d.get("file", "")}},
    "face": lambda d: {"type": "face", "data": {"id": d.get("id", "")}},
    "reply": lambda d: {"type": "reply", "data": {"id": d.get("id", "")}},
}


def cq_to_segments(content: str) -> list[dict]:
    """Convert outbound text with embedded CQ codes to structured segments.

    NapCat does not interpret ``[CQ:...]`` inside array-format text segments,
    so an agent that writes ``[CQ:at,qq=123] hi`` would otherwise show the raw
    code in QQ. Recognized codes become real segments; unknown types are kept
    as literal text so nothing is silently dropped.
    """
    segments: list[dict] = []
    pos = 0
    for match in _CQ_CODE_RE.finditer(content):
        if text := content[pos:match.start()]:
            segments.append({"type": "text", "data": {"text": text}})
        pos = match.end()
        seg_type = match.group(1).lower()
        params = _parse_cq_params(match.group(2))
        builder = _CQ_OUTBOUND_MAP.get(seg_type)
        if builder is not None:
            segment = builder(params)
            # A media segment only counts when its payload is a URI the QQ side can
            # resolve (http(s) URL, base64://, file://). Placeholder text like
            # "[CQ:image,file=链接或本地路径]" used to become an image segment with
            # that Chinese placeholder as the file value -> NapCat error 1200
            # "识别URL失败" and the whole reply (plus its plain-text fallback, which
            # carried the same literal) silently failed to deliver.
            payload = str(segment["data"].get("file") or "")
            resolvable = payload.startswith(("http://", "https://", "base64://", "file://", "ftp://"))
            # Bare local paths (e.g. meme-vault files) are not resolvable by
            # NapCat directly, but if the file exists on the Hermes side we
            # can inline it as base64:// just like send_file does.
            if not resolvable and payload.startswith("/"):
                p = Path(payload)
                if p.is_file() and p.stat().st_size <= 50 * 1024 * 1024:
                    segment["data"]["file"] = file_param(p)
                    resolvable = True
            if segment["data"] and (resolvable or "file" not in segment["data"]):
                segments.append(segment)
                continue
            # Not resolvable: fall through and keep the raw code as literal text.
        segments.append({"type": "text", "data": {"text": match.group(0)}})
    if text := content[pos:]:
        segments.append({"type": "text", "data": {"text": text}})
    return segments or [{"type": "text", "data": {"text": ""}}]


def file_param(path: Path) -> str:
    """Encode a local file as NapCat's ``base64://`` file parameter.

    Hermes and NapCat usually run in separate containers, so NapCat cannot
    resolve paths from the Hermes filesystem (bare paths fail with NapCat
    error 1200 '识别URL失败', and file:// URIs are likewise unreachable).
    Inlining the bytes via base64 works everywhere at the cost of ~33%
    transport overhead; keep files reasonable (<50 MB) or share a volume.
    """
    import base64
    return "base64://" + base64.b64encode(path.read_bytes()).decode("ascii")


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


def extract_text(self_id: Optional[str], message: Any, raw_message: Optional[str]) -> tuple[str, bool, set[str]]:
    """Flatten a OneBot message to plain text (module-level; shared with tools).

    Accepts both napcat shapes:
    * array  — list of ``{"type": ..., "data": {...}}`` segments
    * string — plain text with embedded CQ codes (``[CQ:at,qq=123]``),
      per the OneBot 11 spec (escape rules: &amp; &#91; &#93; &comma;)

    Returns (text, at_me, mentioned_ids). Non-text segments degrade to
    readable placeholders so the agent knows media exists.
    """
    if isinstance(message, str):
        return _parse_cq_str(self_id, message or raw_message or "")
    if isinstance(message, list):
        segments = message
    elif raw_message:  # unexpected shape, fall back to raw string parsing
        return _parse_cq_str(self_id, raw_message)
    else:
        return "", False, set()

    mentioned: set[str] = set()
    parts: list[str] = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        seg_type = segment.get("type")
        data = segment.get("data") or {}
        if seg_type == "text":
            parts.append(str(data.get("text", "")))
        else:
            placeholder, qq = _render_segment(seg_type, data)
            parts.append(placeholder)
            if qq:
                mentioned.add(qq)
    text = "".join(parts).strip()
    at_me = bool(self_id and (self_id in mentioned or "all" in mentioned))
    return text, at_me, mentioned


def _parse_cq_str(self_id: Optional[str], text: str) -> tuple[str, bool, set[str]]:
    """Parse a string-format message with embedded CQ codes."""
    mentioned: set[str] = set()
    parts: list[str] = []
    pos = 0
    for match in _CQ_CODE_RE.finditer(text):
        parts.append(_cq_unescape(text[pos:match.start()]))
        pos = match.end()
        seg_type = match.group(1).lower()
        data = _parse_cq_params(match.group(2))
        placeholder, qq = _render_segment(seg_type, data)
        parts.append(placeholder)
        if qq:
            mentioned.add(qq)
    parts.append(_cq_unescape(text[pos:]))
    result = "".join(parts).strip()
    at_me = bool(self_id and (self_id in mentioned or "all" in mentioned))
    return result, at_me, mentioned


def _render_segment(seg_type: Any, data: dict) -> tuple[str, Optional[str]]:
    """Map a non-text segment to a readable placeholder + mentioned qq."""
    seg_type = str(seg_type).lower()
    if seg_type == "at":
        qq = str(data.get("qq", ""))
        if qq == "all":
            return "@全体成员 ", "all"
        return (f"@{qq} " if qq else ""), (qq or None)
    if seg_type == "image":
        src_url = str(data.get("url") or data.get("file") or "")
        return f"[图片:{src_url}]" if src_url else "[图片]", None
    if seg_type == "record":
        return "[语音]", None
    if seg_type == "video":
        return "[视频]", None
    if seg_type == "file":
        return f"[文件:{data.get('name') or data.get('file', '')}]", None
    if seg_type == "face":
        return "[表情]", None
    if seg_type == "reply":
        return "", None  # reply anchor; hermes passes reply ids separately
    if seg_type == "forward":
        fwd_id = str(data.get("id") or "")
        return f"[合并转发消息:{fwd_id}]" if fwd_id else "[合并转发消息]", None
    if seg_type == "json":
        card = str(data.get("data") or "")
        if card:
            # Card payloads can be long (mini-app JSON blobs); keep a usable
            # head so the agent can read title/url, cap the rest.
            return f"[卡片消息:{card[:500]}]", None
        return "[卡片消息]", None
    return f"[{seg_type}]", None


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
        # Group whitelist: when non-empty, messages from other groups are
        # dropped before reaching the gateway (intersects with the gateway's
        # NAPCAT_ALLOWED_USERS user allowlist).
        groups_env = extra.get("allowed_groups") or os.getenv("NAPCAT_ALLOWED_GROUPS", "")
        self.allowed_groups: frozenset[str] = frozenset(
            g.strip() for g in groups_env.split(",") if g.strip()
        )

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
            # Per OneBot 11: sub_type "notice" is a system notice (not a user
            # message) and "anonymous" carries an unreliable placeholder
            # user_id — ignore both rather than feeding them to the agent.
            if event.get("sub_type") in ("notice", "anonymous"):
                return
            group_id = event.get("group_id")
            if self.allowed_groups and str(group_id) not in self.allowed_groups:
                logger.debug("group %s not in NAPCAT_ALLOWED_GROUPS; ignoring", group_id)
                return
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
        return extract_text(self.client.self_id, message, raw_message)

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
        segments.extend(cq_to_segments(content))

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

        Files are delivered inline as ``base64://`` payloads via
        ``upload_group_file`` / ``upload_private_file`` — the only form that
        works when Hermes and NapCat run in separate containers without a
        shared volume (bare paths fail with NapCat error 1200).
        """
        if content:
            result = await self.send(chat_id, content)
            if not result.success:
                logger.error("standalone_send text to %s failed: %s", chat_id, result.error)
        message_type, numeric_id = parse_chat_id(chat_id)
        for file_path in media_files or []:
            path = Path(file_path).expanduser().resolve()
            if not path.is_file():
                logger.error("standalone_send file %s not found; skipping", path)
                continue
            try:
                if message_type == "group":
                    await self.client.call(
                        "upload_group_file",
                        {"group_id": numeric_id, "file": file_param(path), "name": path.name},
                        timeout=300,
                    )
                else:
                    await self.client.call(
                        "upload_private_file",
                        {"user_id": numeric_id, "file": file_param(path), "name": path.name},
                        timeout=300,
                    )
            except (OneBotError, ConnectionError, TimeoutError) as exc:
                logger.error("standalone_send file %s to %s failed: %s", path, chat_id, exc)
