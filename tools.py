"""Handlers for the napcat toolset.

Each handler is an async ``fn(args: dict, **kwargs) -> str`` returning a JSON
string (errors included), as required by the Hermes plugin tool contract.

The tools talk to NapCat through the OneBotClient owned by the platform
adapter; the adapter publishes it here on startup (``set_client``) and clears
it on shutdown.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

try:  # loaded as a package (pip install)
    from .adapter import parse_chat_id
    from .onebot import OneBotClient, OneBotError
except ImportError:  # loaded as a directory plugin
    from adapter import parse_chat_id
    from onebot import OneBotClient, OneBotError

logger = logging.getLogger("hermes.napcat.tools")

_client: Optional[OneBotClient] = None


def set_client(client: Optional[OneBotClient]) -> None:
    global _client
    _client = client


def _ok(data: Any = None, **extra: Any) -> str:
    payload = {"ok": True, "data": data}
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False, default=str)


def _err(message: str) -> str:
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False)


def _client_or_error() -> tuple[Optional[OneBotClient], Optional[str]]:
    if _client is None:
        return None, "napcat platform is not connected (is the NapCat gateway enabled?)"
    if not _client.is_connected:
        return None, "napcat WebSocket is currently disconnected"
    return _client, None


# --------------------------------------------------------------- handlers


async def send_message(args: dict, **kwargs) -> str:
    client, err = _client_or_error()
    if err:
        return _err(err)
    target = str(args.get("target", "")).strip()
    message = str(args.get("message", ""))
    if not target or not message:
        return _err("both 'target' and 'message' are required")
    try:
        message_type, numeric_id = parse_chat_id(target)
    except ValueError as exc:
        return _err(f"invalid target '{target}': {exc}")
    try:
        if message_type == "group":
            data = await client.call(
                "send_group_msg",
                {"group_id": numeric_id, "message": [{"type": "text", "data": {"text": message}}]},
            )
        else:
            data = await client.call(
                "send_private_msg",
                {"user_id": numeric_id, "message": [{"type": "text", "data": {"text": message}}]},
            )
        return _ok({"message_id": (data or {}).get("message_id")})
    except (OneBotError, ConnectionError, TimeoutError) as exc:
        return _err(str(exc))


async def send_file(args: dict, **kwargs) -> str:
    client, err = _client_or_error()
    if err:
        return _err(err)
    target = str(args.get("target", "")).strip()
    file_path = str(args.get("file_path", "")).strip()
    file_name = args.get("file_name")
    if not target or not file_path:
        return _err("both 'target' and 'file_path' are required")
    path = Path(file_path).expanduser()
    if not path.is_file():
        return _err(f"file not found: {path}")
    try:
        message_type, numeric_id = parse_chat_id(target)
    except ValueError as exc:
        return _err(f"invalid target '{target}': {exc}")
    params: dict[str, Any] = {
        "file": path.resolve().as_uri(),
        "name": str(file_name or path.name),
    }
    try:
        if message_type == "group":
            params["group_id"] = numeric_id
            await client.call("upload_group_file", params, timeout=300)
        else:
            params["user_id"] = numeric_id
            await client.call("upload_private_file", params, timeout=300)
        return _ok({"sent": str(path), "target": target})
    except (OneBotError, ConnectionError, TimeoutError) as exc:
        return _err(str(exc))


async def get_group_list(args: dict, **kwargs) -> str:
    client, err = _client_or_error()
    if err:
        return _err(err)
    try:
        groups = await client.call("get_group_list", {"no_cache": False})
        summary = [
            {"group_id": g.get("group_id"), "name": g.get("group_name")} for g in groups or []
        ]
        return _ok(summary)
    except (OneBotError, ConnectionError, TimeoutError) as exc:
        return _err(str(exc))


async def get_group_member_info(args: dict, **kwargs) -> str:
    client, err = _client_or_error()
    if err:
        return _err(err)
    group_id, user_id = args.get("group_id"), args.get("user_id")
    if group_id is None or user_id is None:
        return _err("'group_id' and 'user_id' are required")
    try:
        info = await client.call(
            "get_group_member_info",
            {"group_id": int(group_id), "user_id": int(user_id), "no_cache": False},
        )
        return _ok(info)
    except (OneBotError, ConnectionError, TimeoutError, ValueError) as exc:
        return _err(str(exc))


async def group_mute(args: dict, **kwargs) -> str:
    client, err = _client_or_error()
    if err:
        return _err(err)
    group_id, user_id = args.get("group_id"), args.get("user_id")
    duration = args.get("duration_seconds", 60)
    if group_id is None or user_id is None:
        return _err("'group_id', 'user_id' and 'duration_seconds' are required")
    try:
        duration = max(0, min(int(duration), 2592000))
        await client.call(
            "set_group_ban",
            {"group_id": int(group_id), "user_id": int(user_id), "duration": duration},
        )
        return _ok({"muted": int(user_id), "group": int(group_id), "seconds": duration})
    except (OneBotError, ConnectionError, TimeoutError, ValueError) as exc:
        return _err(str(exc))


async def group_kick(args: dict, **kwargs) -> str:
    client, err = _client_or_error()
    if err:
        return _err(err)
    group_id, user_id = args.get("group_id"), args.get("user_id")
    if group_id is None or user_id is None:
        return _err("'group_id' and 'user_id' are required")
    try:
        await client.call(
            "set_group_kick",
            {
                "group_id": int(group_id),
                "user_id": int(user_id),
                "reject_add_request": bool(args.get("reject_add_request", False)),
            },
        )
        return _ok({"kicked": int(user_id), "group": int(group_id)})
    except (OneBotError, ConnectionError, TimeoutError, ValueError) as exc:
        return _err(str(exc))


HANDLERS = {
    "napcat_send_message": send_message,
    "napcat_send_file": send_file,
    "napcat_get_group_list": get_group_list,
    "napcat_get_group_member_info": get_group_member_info,
    "napcat_group_mute": group_mute,
    "napcat_group_kick": group_kick,
}
