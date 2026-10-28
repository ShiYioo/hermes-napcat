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
import re
import threading
from pathlib import Path
from typing import Any, Callable, Coroutine, Optional

try:  # loaded as a package (pip install)
    from .adapter import cq_to_segments, extract_text, file_param, parse_chat_id
    from .onebot import OneBotClient, OneBotError
except ImportError:  # loaded as a directory plugin
    from adapter import cq_to_segments, extract_text, file_param, parse_chat_id
    from onebot import OneBotClient, OneBotError

logger = logging.getLogger("hermes.napcat.tools")

TOOL_TIMEOUT = 120.0

# res_id extraction from a merged-forward wrapper: the `forward` segment id or
# the "resid" field inside NapCat's com.tencent.multimsg JSON card.
_CQ_FORWARD_ID_RE = re.compile(r"\[CQ:forward,[^\]]*?id=([^,\]]+)")
_MULTIMSG_RESID_RE = re.compile(r'"resid"\s*:\s*"([^"]+)"', re.IGNORECASE)

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
            # inline base64: NapCat runs in another container and cannot read
            # Hermes-side paths (bare paths fail with NapCat error 1200)
            "file": file_param(path),
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
        if not isinstance(groups, list):
            return {"raw": groups}  # unexpected shape; hand it through verbatim
        return [
            {"group_id": g.get("group_id"), "name": g.get("group_name")}
            for g in groups if isinstance(g, dict)
        ]

    return _sync_call(run)


def get_group_history(args: dict, **kwargs) -> str:
    group_id = args.get("group_id")
    if group_id is None:
        return _err("'group_id' is required")
    count = max(1, min(int(args.get("count", 20) or 20), 100))

    async def run() -> list:
        data = await _client.call(
            "get_group_msg_history",
            {"group_id": int(group_id), "count": count},
            timeout=60,
        )
        messages = data.get("messages") if isinstance(data, dict) else data
        if not isinstance(messages, list):
            return {"raw": data}
        self_id = _client.self_id
        out = []
        for m in messages[-count:]:
            if not isinstance(m, dict):
                continue
            sender = m.get("sender") or {}
            text, _, _ = extract_text(self_id, m.get("message"), m.get("raw_message"))
            out.append({
                "time": m.get("time"),
                "sender": sender.get("card") or sender.get("nickname") or m.get("user_id"),
                "text": text,
            })
        return out

    return _sync_call(run)


def get_forward_msg(args: dict, **kwargs) -> str:
    """Unpack a merged-forward message into its constituent floors.

    Accepts either the forward node's res_id or the outer wrapper message's
    id (e.g. a chat message shown as '[合并转发消息]') — an all-numeric id is
    treated as the wrapper and resolved to its res_id via get_msg first,
    because NapCat's get_forward_msg only accepts the node res_id.
    """
    message_id = str(args.get("message_id", "")).strip()
    if not message_id:
        return _err("'message_id' is required")

    async def run() -> list:
        client = _client
        assert client is not None  # _sync_call rejects before run() if disconnected
        # Two id shapes arrive here: the forward node's res_id (long, often
        # 19+ chars, from the forward segment or the send_forward result) or
        # the outer chat message's id (short numeric). Length decides: a
        # short id goes through get_msg to find the forward node — which
        # NapCat returns either as a `forward` segment or as a `json` card
        # (com.tencent.multimsg) carrying "resid" — and peels out the res_id.
        if len(message_id) < 16:
            outer = await client.call("get_msg", {"message_id": int(message_id)}, timeout=60)
            res_id = ""
            segments = outer.get("message") or []
            if isinstance(segments, str):
                # CQ-string shape: pull the forward/json id out by regex
                m = _CQ_FORWARD_ID_RE.search(segments)
                res_id = m.group(1) if m else ""
            else:
                for seg in segments:
                    if not isinstance(seg, dict):
                        continue
                    seg_data = seg.get("data") or {}
                    if seg.get("type") == "forward":
                        res_id = str(seg_data.get("id", ""))
                        break
                    if seg.get("type") == "json":
                        # NapCat renders merged forwards as a multimsg JSON card
                        card = str(seg_data.get("data", ""))
                        m = _MULTIMSG_RESID_RE.search(card)
                        if m:
                            res_id = m.group(1)
                            break
            if not res_id:
                return [
                    {
                        "error": (
                            f"message {message_id} contains no forward node "
                            "(it may not be a merged-forward message, or it expired)"
                        )
                    }
                ]
            target_id: str = res_id
        else:
            target_id = message_id

        data = await client.call("get_forward_msg", {"message_id": target_id}, timeout=60)
        messages = data.get("messages") if isinstance(data, dict) else data
        if not isinstance(messages, list):
            return [{"raw": data}]
        self_id = client.self_id
        out = []
        for m in messages:
            if not isinstance(m, dict):
                continue
            sender = m.get("sender") or {}
            text, _, _ = extract_text(self_id, m.get("message"), m.get("raw_message"))
            out.append({
                "time": m.get("time"),
                "sender": sender.get("card") or sender.get("nickname") or m.get("user_id"),
                "text": text,
            })
        return out

    return _sync_call(run)


def add_sticker(args: dict, **kwargs) -> str:
    """Add an image to the bot account's QQ favorite stickers (自定义表情).

    Accepts either a URL reachable by NapCat (QQ CDN share links included) or
    a plain message_id of a message carrying an image. Because NapCat only
    accepts add_custom_face with a local path, URL/message-id inputs are
    landed into NapCat's own cache first (send to self → get_msg →
    get_image), then registered.
    """
    url = str(args.get("url", "")).strip()
    message_id = str(args.get("message_id", "")).strip()
    name = str(args.get("name", "")).strip()
    if not url and not message_id:
        return _err("either 'url' or 'message_id' is required")

    async def run() -> dict:
        client = _client
        assert client is not None  # _sync_call rejects before run() if disconnected

        # 1. obtain an image segment: either send the URL to the master DM
        #    (NapCat downloads it into its own cache) or reuse an existing
        #    message's image.
        if url:
            self_id = str(client.self_id or "")
            if not self_id.isdigit():
                return {"error": "bot self_id unknown; cannot self-send to land the image in cache"}
            sent = await client.call(
                "send_private_msg",
                {"user_id": int(self_id), "message": [{"type": "image", "data": {"file": url}}]},
                timeout=90,
            )
            src_mid = (sent or {}).get("message_id")
        else:
            src_mid = int(message_id) if message_id.isdigit() else message_id
        rec = await client.call("get_msg", {"message_id": src_mid}, timeout=30)
        file_key = ""
        for seg in rec.get("message") or []:
            if isinstance(seg, dict) and seg.get("type") == "image":
                file_key = str((seg.get("data") or {}).get("file", ""))
                break
        if not file_key:
            return {"error": f"message {src_mid} contains no image segment"}

        # 2. resolve the NapCat-local absolute path
        img = await client.call("get_image", {"file": file_key}, timeout=30)
        local_path = str((img or {}).get("file", ""))
        if not local_path:
            return {"error": f"could not resolve local path for {file_key}"}

        # 3. register as favorite sticker
        params: dict[str, Any] = {"file": local_path, "is_origin": True}
        if name:
            params["file_name"] = name if name.endswith((".gif", ".png", ".jpg", ".jpeg", ".webp")) else f"{name}.gif"
        r = await client.call("add_custom_face", params, timeout=60)
        return {
            "added": True,
            "local_path": local_path,
            "file_name": params.get("file_name", ""),
            "is_exist": (r or {}).get("isExist"),
        }

    return _sync_call(run)


def list_stickers(args: dict, **kwargs) -> str:
    """List the bot account's QQ favorite stickers (自定义表情)."""
    count = max(1, min(int(args.get("count", 48) or 48), 200))

    async def run() -> dict:
        client = _client
        assert client is not None
        urls = await client.call("fetch_custom_face", {"count": count}, timeout=30)
        try:
            details = await client.call("fetch_custom_face_detail", {"count": count}, timeout=30)
        except Exception:  # noqa: BLE001 - details are best-effort
            details = []
        out = []
        for d in details if isinstance(details, list) else []:
            if isinstance(d, dict):
                out.append({
                    "res_id": d.get("resId"),
                    "md5": d.get("md5"),
                    "path": d.get("emoPath"),
                    "url": d.get("url"),
                })
        return {"count": len(out) or len(urls) if isinstance(urls, list) else len(out),
                "stickers": out, "urls": urls if isinstance(urls, list) else []}

    return _sync_call(run)


def remove_sticker(args: dict, **kwargs) -> str:
    """Remove a sticker from the bot account's QQ favorite stickers."""
    res_id = str(args.get("res_id", "")).strip()
    if not res_id:
        return _err("'res_id' is required (from napcat_list_stickers)")

    async def run() -> dict:
        client = _client
        assert client is not None
        r = await client.call("del_custom_face", {"res_id": res_id}, timeout=30)
        return {"removed": res_id, "raw": r}

    return _sync_call(run)


def send_poke(args: dict, **kwargs) -> str:
    """Poke (戳一戳) a user in a private chat or a group."""
    user_id = args.get("user_id")
    group_id = args.get("group_id")
    if user_id is None:
        return _err("'user_id' is required")

    async def run() -> dict:
        if group_id is not None:
            await _client.call(
                "group_poke", {"group_id": int(group_id), "user_id": int(user_id)}, timeout=30
            )
            return {"poked": int(user_id), "group": int(group_id)}
        await _client.call("friend_poke", {"user_id": int(user_id)}, timeout=30)
        return {"poked": int(user_id), "private": True}

    return _sync_call(run)


def get_essence_msgs(args: dict, **kwargs) -> str:
    """List a group's essence messages (精华消息)."""
    group_id = args.get("group_id")
    if group_id is None:
        return _err("'group_id' is required")

    async def run() -> list:
        client = _client
        assert client is not None  # _sync_call rejects before run() if disconnected
        data = await client.call("get_essence_msg_list", {"group_id": int(group_id)}, timeout=30)
        msgs = data if isinstance(data, list) else (data or {}).get("msg_list", [])
        out = []
        for m in msgs:
            if not isinstance(m, dict):
                continue
            sender = m.get("sender_id")
            out.append({
                "msg_id": str(m.get("message_id", "")),
                "sender": str(sender or ""),
                "sender_nick": m.get("sender_nick", ""),
                "operator_nick": m.get("operator_nick", ""),
                "content": str(m.get("content", ""))[:300],
            })
        return out

    return _sync_call(run)


def group_sign(args: dict, **kwargs) -> str:
    """Sign in (打卡) to a group."""
    group_id = args.get("group_id")
    if group_id is None:
        return _err("'group_id' is required")

    async def run() -> dict:
        client = _client
        assert client is not None  # _sync_call rejects before run() if disconnected
        await client.call("send_group_sign", {"group_id": int(group_id)}, timeout=30)
        return {"signed": True, "group": int(group_id)}

    return _sync_call(run)


    return _sync_call(run)


def set_input_status(args: dict, **kwargs) -> str:
    """Show/clear the 'typing...' indicator toward a user.

    event_type: 1 = typing, 0 = clear (OneBot/NapCat convention).
    """
    user_id = args.get("user_id")
    event_type = int(args.get("typing", 1))
    if user_id is None:
        return _err("'user_id' is required")

    async def run() -> dict:
        client = _client
        assert client is not None  # _sync_call rejects before run() if disconnected
        await client.call(
            "set_input_status",
            {"user_id": int(user_id), "event_type": event_type},
            timeout=30,
        )
        return {"user": int(user_id), "typing": bool(event_type)}

    return _sync_call(run)


def get_private_history(args: dict, **kwargs) -> str:
    """Fetch recent private-chat history with a user."""
    user_id = args.get("user_id")
    if user_id is None:
        return _err("'user_id' is required")
    count = max(1, min(int(args.get("count", 20) or 20), 100))

    async def run() -> list:
        client = _client
        assert client is not None  # _sync_call rejects before run() if disconnected
        data = await client.call(
            "get_friend_msg_history",
            {"user_id": int(user_id), "count": count},
            timeout=60,
        )
        messages = data.get("messages") if isinstance(data, dict) else data
        if not isinstance(messages, list):
            return [{"raw": data}]
        self_id = client.self_id
        out = []
        for m in messages[-count:]:
            if not isinstance(m, dict):
                continue
            sender = m.get("sender") or {}
            text, _, _ = extract_text(self_id, m.get("message"), m.get("raw_message"))
            out.append({
                "time": m.get("time"),
                "sender": sender.get("card") or sender.get("nickname") or m.get("user_id"),
                "text": text,
            })
        return out

    return _sync_call(run)


def send_like(args: dict, **kwargs) -> str:
    """Give a user's profile a daily like (点赞)."""
    user_id = args.get("user_id")
    if user_id is None:
        return _err("'user_id' is required")
    times = max(1, min(int(args.get("times", 1) or 1), 10))

    async def run() -> dict:
        client = _client
        assert client is not None  # _sync_call rejects before run() if disconnected
        await client.call(
            "send_like", {"user_id": int(user_id), "times": times}, timeout=30
        )
        return {"liked": int(user_id), "times": times}

    return _sync_call(run)


def check_url_safety(args: dict, **kwargs) -> str:
    """Check whether a URL is flagged unsafe by QQ's safety service."""
    url = str(args.get("url", "")).strip()
    if not url:
        return _err("'url' is required")

    async def run() -> dict:
        client = _client
        assert client is not None  # _sync_call rejects before run() if disconnected
        r = await client.call("check_url_safely", {"url": url}, timeout=30)
        level = (r or {}).get("level") if isinstance(r, dict) else r
        levels = {1: "safe", 2: "unknown", 3: "dangerous", 4: "unknown"}
        return {"url": url, "level": level, "verdict": levels.get(level, str(level))}

    return _sync_call(run)


def list_group_files(args: dict, **kwargs) -> str:
    """List a group's shared files (root or a specific folder)."""
    group_id = args.get("group_id")
    if group_id is None:
        return _err("'group_id' is required")
    folder_id = str(args.get("folder_id", "")).strip()

    async def run() -> list:
        client = _client
        assert client is not None  # _sync_call rejects before run() if disconnected
        if folder_id:
            data = await client.call(
                "get_group_files_by_folder",
                {"group_id": int(group_id), "folder_id": folder_id},
                timeout=30,
            )
        else:
            data = await client.call(
                "get_group_root_files", {"group_id": int(group_id)}, timeout=30
            )
        out = []
        if isinstance(data, dict):
            for key in ("file_list", "files"):
                for f in data.get(key) or []:
                    if isinstance(f, dict):
                        out.append({
                            "id": f.get("file_id") or f.get("id"),
                            "name": f.get("file_name") or f.get("name"),
                            "size": f.get("size"),
                            "type": "folder" if f.get("is_dir") or f.get("folder_id") else "file",
                            "uploader": (f.get("uploader") or f.get("upload_user_name") or ""),
                        })
            if not out and data:
                return [{"raw_keys": list(data.keys())}]
        return out

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
    "napcat_get_group_history": get_group_history,
    "napcat_get_forward_msg": get_forward_msg,
    "napcat_get_group_member_info": get_group_member_info,
    "napcat_add_sticker": add_sticker,
    "napcat_list_stickers": list_stickers,
    "napcat_remove_sticker": remove_sticker,
    "napcat_group_mute": group_mute,
    "napcat_group_kick": group_kick,
    "napcat_send_poke": send_poke,
    "napcat_get_essence_msgs": get_essence_msgs,
    "napcat_group_sign": group_sign,
    "napcat_set_input_status": set_input_status,
    "napcat_get_private_history": get_private_history,
    "napcat_send_like": send_like,
    "napcat_check_url_safety": check_url_safety,
    "napcat_list_group_files": list_group_files,
}
