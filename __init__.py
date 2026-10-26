"""NapCat (QQ / OneBot v11) plugin for Hermes Agent.

Registers:
* a ``napcat`` gateway platform adapter (chat with the agent over QQ), and
* a ``napcat`` toolset (proactive sends, file delivery, group info/moderation)

See README.md for configuration. Requires ``NAPCAT_WS_URL`` pointing at a
NapCat forward WebSocket.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

try:  # loaded as a package (pip install)
    from . import schemas, tools
    from .adapter import NapCatPlatformAdapter
except ImportError:  # loaded as a directory plugin
    import schemas
    import tools
    from adapter import NapCatPlatformAdapter

logger = logging.getLogger("hermes.napcat")

TOOLSET = "napcat"

_adapter: Optional[NapCatPlatformAdapter] = None


def _adapter_factory(config):
    global _adapter
    _adapter = NapCatPlatformAdapter(config)
    # Share the adapter's OneBotClient with the napcat toolset.
    tools.set_client(_adapter.client)
    return _adapter


def _check() -> bool:
    return bool(os.getenv("NAPCAT_WS_URL"))


async def _standalone_send(
    chat_id: str,
    content: str,
    media_files: Optional[list[str]] = None,
    thread_id: Optional[str] = None,
    force_document: bool = False,
) -> None:
    """Cron/standalone sender wired into register_platform."""
    if _adapter is None:
        logger.error("standalone send requested but napcat adapter is not running")
        return
    await _adapter.standalone_send(chat_id, content, media_files, thread_id, force_document)


async def _setup() -> bool:
    """Interactive setup for ``hermes gateway setup``."""
    try:
        from gateway.config import EnvEntry
    except ImportError:  # pragma: no cover - hermes internals moved
        logger.error("EnvEntry unavailable; set NAPCAT_WS_URL/NAPCAT_TOKEN in .env manually")
        return False
    print("\nNapCat (QQ / OneBot v11) setup")
    ws_url = input("NapCat forward WebSocket URL [ws://127.0.0.1:3001]: ").strip()
    token = input("Access token (empty if none): ").strip()
    try:
        EnvEntry("NAPCAT_WS_URL", True, "NapCat forward WebSocket URL").set(
            ws_url or "ws://127.0.0.1:3001"
        )
        if token:
            EnvEntry("NAPCAT_TOKEN", True, "NapCat access token").set(token)
        home = input("Home channel for cron notifications (chat id, empty to skip): ").strip()
        if home:
            EnvEntry("NAPCAT_HOME_CHANNEL", True, "Default napcat delivery channel").set(home)
    except Exception as exc:  # noqa: BLE001
        logger.error("failed to save config: %s", exc)
        return False
    print("NapCat configuration saved.")
    return True


def register(ctx) -> None:
    ctx.register_platform(
        name="napcat",
        label="NapCat (QQ)",
        dashboard_description=(
            "Relay messages between QQ (private/group) via NapCat and Hermes; "
            "includes proactive send/file tools."
        ),
        adapter_factory=_adapter_factory,
        check_fn=_check,
        required_env=["NAPCAT_WS_URL"],
        install_hint="No extra packages needed (uses aiohttp bundled with Hermes)",
        setup_fn=_setup,
        cron_deliver_env_var="NAPCAT_HOME_CHANNEL",
        standalone_sender_fn=_standalone_send,
        allowed_users_env="NAPCAT_ALLOWED_USERS",
        allow_all_env="NAPCAT_ALLOW_ALL_USERS",
        max_message_length=4500,
        emoji="🐧",
        pii_safe=False,
        platform_hint=(
            "You are chatting over QQ via NapCat (OneBot v11). Keep replies "
            "concise; QQ friends and groups have message-length limits. The "
            "napcat toolset is available for sending files and proactive "
            "messages."
        ),
    )
    for schema in schemas.ALL:
        ctx.register_tool(
            name=schema["name"],
            toolset=TOOLSET,
            schema=schema,
            handler=tools.HANDLERS[schema["name"]],
        )
    logger.info("napcat platform + %d tools registered", len(schemas.ALL))
