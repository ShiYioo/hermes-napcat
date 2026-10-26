"""OneBot v11 WebSocket client for NapCat.

A minimal, dependency-light client built on aiohttp (already a Hermes
dependency) that:

* connects to a NapCat WebSocket server (forward WS),
* receives OneBot events and dispatches message events to a callback,
* exposes a request/response ``call()`` for OneBot API actions over the
  same WebSocket, correlated via the ``echo`` field,
* reconnects with capped exponential backoff.

Protocol reference: https://napneko.github.io/develop/api (OneBot v11).
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
from typing import Any, Awaitable, Callable, Optional

import aiohttp

logger = logging.getLogger("hermes.napcat.onebot")

_API_TIMEOUT = 60.0
_CONNECT_TIMEOUT = 15.0
_BACKOFF_MIN = 2.0
_BACKOFF_MAX = 120.0

# Event callback: async fn(event: dict) -> None. Called for every event with
# post_type == "message"; other event types (notices, meta) are logged only.
EventCallback = Callable[[dict], Awaitable[None]]


class OneBotError(RuntimeError):
    """Raised when NapCat returns a failed status for an API action."""

    def __init__(self, action: str, retcode: Any, message: str = ""):
        self.action = action
        self.retcode = retcode
        super().__init__(f"OneBot action '{action}' failed (retcode={retcode}): {message}")


class OneBotClient:
    """Forward-WebSocket client speaking OneBot v11 to NapCat."""

    def __init__(
        self,
        ws_url: str,
        token: str = "",
        on_message_event: Optional[EventCallback] = None,
    ):
        self.ws_url = ws_url.rstrip("/")
        self.token = token
        self.on_message_event = on_message_event
        self.self_id: Optional[str] = None  # bot QQ number, learned at runtime

        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._echo = itertools.count(1)
        self._pending: dict[str, asyncio.Future[dict]] = {}
        self._tasks: list[asyncio.Task] = []
        self._stopped = asyncio.Event()
        self._connected = asyncio.Event()
        self._backoff = _BACKOFF_MIN

    # ---------------------------------------------------------------- state

    @property
    def is_connected(self) -> bool:
        return self._ws is not None and not self._ws.closed

    async def wait_connected(self, timeout: float = 30.0) -> bool:
        """Wait for the first successful connection. Returns False on timeout."""
        try:
            await asyncio.wait_for(self._connected.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False

    # ------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        self._stopped.clear()
        self._tasks.append(asyncio.create_task(self._run(), name="napcat-ws-run"))

    async def stop(self) -> None:
        self._stopped.set()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        await self._close_ws()
        if self._session and not self._session.closed:
            await self._session.close()
        for future in self._pending.values():
            if not future.done():
                future.set_exception(ConnectionError("OneBot client stopped"))
        self._pending.clear()

    async def _close_ws(self) -> None:
        if self._ws and not self._ws.closed:
            await self._ws.close()
        self._ws = None

    # --------------------------------------------------------- main ws loop

    async def _run(self) -> None:
        while not self._stopped.is_set():
            try:
                await self._connect_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - reconnect on any error
                logger.warning("NapCat connection error: %s; reconnecting", exc)
            self._connected.clear()
            await self._fail_pending(ConnectionError("NapCat connection closed"))
            if self._stopped.is_set():
                return
            delay = self._backoff
            self._backoff = min(self._backoff * 2, _BACKOFF_MAX)
            logger.info("Reconnecting to NapCat in %.0fs", delay)
            try:
                await asyncio.wait_for(self._stopped.wait(), delay)
                return  # stop requested during backoff
            except asyncio.TimeoutError:
                pass

    async def _connect_once(self) -> None:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=None, connect=_CONNECT_TIMEOUT)
            )
        headers = {}
        if self.token:
            # NapCat accepts either scheme; Bearer is the OneBot v11 standard.
            headers["Authorization"] = f"Bearer {self.token}"
        logger.info("Connecting to NapCat at %s", self.ws_url)
        # 64 MiB cap: large enough for big forwarded-message events, but not
        # unlimited (see the review criticism of NousResearch/hermes-agent#17917).
        self._ws = await self._session.ws_connect(
            self.ws_url, headers=headers, max_msg_size=64 * 1024 * 1024
        )
        self._connected.set()
        self._backoff = _BACKOFF_MIN
        logger.info("NapCat WebSocket connected")

        async for msg in self._ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                await self._handle_raw(msg.data)
            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                break
        logger.info("NapCat WebSocket closed")

    # ------------------------------------------------------------ inbound

    async def _handle_raw(self, raw: str) -> None:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            logger.debug("Ignoring non-JSON frame: %.120s", raw)
            return
        if not isinstance(payload, dict):
            return

        echo = payload.get("echo")
        if echo is not None:  # API response
            future = self._pending.pop(str(echo), None)
            if future and not future.done():
                future.set_result(payload)
            return

        post_type = payload.get("post_type")
        if post_type == "meta_event":
            sub = payload.get("meta_event_type")
            if sub == "lifecycle":
                logger.info("NapCat lifecycle: %s", payload.get("sub_type"))
            return  # heartbeats are noisy; drop
        if post_type == "message":
            if self.self_id is None and payload.get("self_id"):
                self.self_id = str(payload["self_id"])
            if self.on_message_event:
                try:
                    await self.on_message_event(payload)
                except Exception:  # noqa: BLE001 - never kill the reader loop
                    logger.exception("message event handler failed")
        elif post_type in ("notice", "request", "message_sent"):
            logger.debug("Ignoring %s event", post_type)

    # ----------------------------------------------------------- outbound

    async def call(self, action: str, params: Optional[dict] = None,
                   timeout: float = _API_TIMEOUT) -> Any:
        """Invoke a OneBot API action; return its ``data`` payload.

        Raises OneBotError on failed status / non-zero retcode and
        ConnectionError if the socket is down.
        """
        if not self.is_connected:
            raise ConnectionError("NapCat WebSocket is not connected")
        echo = str(next(self._echo))
        future: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
        self._pending[echo] = future
        await self._ws.send_json({"action": action, "params": params or {}, "echo": echo})
        try:
            response = await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError as exc:
            self._pending.pop(echo, None)
            raise TimeoutError(f"OneBot action '{action}' timed out after {timeout}s") from exc
        status = response.get("status")
        retcode = response.get("retcode", -1)
        if status == "ok" or retcode == 0:
            return response.get("data")
        wording = response.get("wording") or response.get("message") or ""
        raise OneBotError(action, retcode, str(wording))

    async def _fail_pending(self, exc: Exception) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(exc)
        self._pending.clear()
