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

import adapter
import schemas
import tools
from adapter import NapCatPlatformAdapter

logger = logging.getLogger("hermes.napcat")

TOOLSET = "napcat"


def _adapter_factory(config):
    bot = NapCatPlatformAdapter(config)
    # Share the adapter's OneBotClient with the napcat toolset.
    tools.set_client(bot.client)
    return bot


def _check() -> bool:
    return bool(os.getenv("NAPCAT_WS_URL"))


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
        adapter_factory=_adapter_factory,
        check_fn=_check,
        cron_deliver_env_var="NAPCAT_HOME_CHANNEL",
        cron_standalone_sender_env_var="NAPCAT_STANDALONE_TOKEN",
        allowed_users_env="NAPCAT_ALLOWED_USERS",
        allow_all_env="NAPCAT_ALLOW_ALL_USERS",
        max_message_length=4500,
        help="NapCat QQ adapter. Configure via NAPCAT_WS_URL/NAPCAT_TOKEN. "
        "Chat ids: QQ number (private) or g<group_id> (group).",
        setup_fn=_setup,
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
