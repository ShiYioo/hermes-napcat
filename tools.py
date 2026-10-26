"""Handlers for the napcat toolset.

Hermes invokes tool handlers as plain sync functions ``fn(args: dict, **kwargs)
-> str`` returning a JSON string (errors included). Our transport is async
(aiohttp WebSocket on the gateway event loop), so each sync handler bridges to
that loop with ``run_coroutine_threadsafe`` — run from a short-lived thread so
it is safe regardless of whether the dispatcher calls us from the loop thread
or from a worker thread.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from pathlib import Path
from typing import Any, Callable, Coroutine, Optional

try:  # loaded as a package (pip install)
    from .adapter import cq_to_segments, parse_chat_id
    from .onebot import OneBotClient, OneBotError
except ImportError:  # loaded as a directory plugin
    from adapter import cq_to_segments, parse_chat_id
    from onebot import OneBotClient, OneBotError

logger = logging.getLogger("hermes.napcat.tools")

TOOL_TIMEOUT = 120.0

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


def _sync_call(coro_factory: Callable[[], Coroutine]) -> str:
    """Run an async transport call from sync handler context.

    Schedules the coroutine on the gateway event loop from a temporary thread
    and blocks for the result. Using a thread keeps this safe even when the
    tool dispatcher invokes the handler from the loop thread itself.
    """
    client, err = _client_or_error()
    if err:
        return _err(err)
    loop = client.loop
    if loop is None:
        return _err("napcat event loop reference missing (adapter not started)")

    outcome: dict[str, Any] = {}

    def runner() -> None:
        future = asyncio.run_coroutine_threadsafe(coro_factory(), loop)
        try:
            outcome["value"] = future.result(TOOL_TIMEOUT)
        except Exception as exc:  # noqa: BLE001 - report as tool error JSON
            outcome["error"] = exc

    thread = threading.Thread(target=runner, name="napcat-tool-call", daemon=True)
    thread.start()
    thread.join(TOOL_TIMEOUT + 10)
    if "error" in outcome:
        return _err(str(outcome["error"]))
    if "value" not in outcome:
        return _err(f"tool call did not complete within {TOOL_TIMEOUT}s")
    return _ok(outcome["value"])


# --------------------------------------------------------------- handlers


def send_message(args: dict, **kwargs) -> str:
    target = str(args.get("target", "")).strip()
    message = str(args.get("message", ""))
    if not target or not message:
        return _err("both 'target' and 'message' are required")

    async def run() -> dict:
        message_type, numeric_id = parse_chat_id(target)
        segments = cq_to_segments(message)  # rename: don't shadow the closure var
        if message_type == "group":
            data = await _client.call(
                "send_group_msg", {"group_id": numeric_id, "message": segments}
            )
        else:
            data = await _client.call(
                "send_private_msg", {"user_id": numeric_id, "message": segments}
            )
        return {"message_id": (data or {}).get("message_id")}

    try:
        return _sync_call(run)
    except ValueError as exc:
        return _err(f"invalid target '{target}': {exc}")


def send_file(args: dict, **kwargs) -> str:
    target = str(args.get("target", "")).strip()
    file_path = str(args.get("file_path", "")).strip()
    file_name = args.get("file_name")
    if not target or not file_path:
        return _err("both 'target' and 'file_path' are required")
    path = Path(file_path).expanduser()
    if not path.is_file():
        return _err(f"file not found: {path}")

    async def run() -> dict:
        message_type, numeric_id = parse_chat_id(target)
        params: dict[str, Any] = {
            # plain absolute path — the most portable form for NapCat's file param
            "file": str(path.resolve()),
            "name": str(file_name or path.name),
        }
        if message_type == "group":
            params["group_id"] = numeric_id
            await _client.call("upload_group_file", params, timeout=TOOL_TIMEOUT)
        else:
            params["user_id"] = numeric_id
            await _client.call("upload_private_file", params, timeout=TOOL_TIMEOUT)
        return {"sent": str(path), "target": target}

    try:
        return _sync_call(run)
    except ValueError as exc:
        return _err(f"invalid target '{target}': {exc}")


def get_group_list(args: dict, **kwargs) -> str:
    async def run() -> list:
        groups = await _client.call("get_group_list", {"no_cache": False})
        return [
            {"group_id": g.get("group_id"), "name": g.get("group_name")} for g in groups or []
        ]

    return _sync_call(run)


def get_group_member_info(args: dict, **kwargs) -> str:
    group_id, user_id = args.get("group_id"), args.get("user_id")
    if group_id is None or user_id is None:
        return _err("'group_id' and 'user_id' are required")

    async def run() -> dict:
        return await _client.call(
            "get_group_member_info",
            {"group_id": int(group_id), "user_id": int(user_id), "no_cache": False},
        )

    return _sync_call(run)


def group_mute(args: dict, **kwargs) -> str:
    group_id, user_id = args.get("group_id"), args.get("user_id")
    duration = args.get("duration_seconds", 60)
    if group_id is None or user_id is None:
        return _err("'group_id', 'user_id' and 'duration_seconds' are required")

    async def run() -> dict:
        seconds = max(0, min(int(duration), 2592000))
        await _client.call(
            "set_group_ban",
            {"group_id": int(group_id), "user_id": int(user_id), "duration": seconds},
        )
        return {"muted": int(user_id), "group": int(group_id), "seconds": seconds}

    return _sync_call(run)


def group_kick(args: dict, **kwargs) -> str:
    group_id, user_id = args.get("group_id"), args.get("user_id")
    if group_id is None or user_id is None:
        return _err("'group_id' and 'user_id' are required")

    async def run() -> dict:
        await _client.call(
            "set_group_kick",
            {
                "group_id": int(group_id),
                "user_id": int(user_id),
                "reject_add_request": bool(args.get("reject_add_request", False)),
            },
        )
        return {"kicked": int(user_id), "group": int(group_id)}

    return _sync_call(run)


HANDLERS = {
    "napcat_send_message": send_message,
    "napcat_send_file": send_file,
    "napcat_get_group_list": get_group_list,
    "napcat_get_group_member_info": get_group_member_info,
    "napcat_group_mute": group_mute,
    "napcat_group_kick": group_kick,
}
