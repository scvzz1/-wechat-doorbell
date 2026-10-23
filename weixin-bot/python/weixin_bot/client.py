from __future__ import annotations

import asyncio
import inspect
import sys
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any, TypeVar, cast

from .api import ApiError, DEFAULT_BASE_URL, build_text_message, get_config, get_updates, send_message, send_typing as api_send_typing
from .auth import Credentials, clear_credentials, load_credentials, login as auth_login
from .types import IncomingMessage, MessageItem, MessageItemType, MessageKind, MessageType, WeixinMessage

MessageHandler = Callable[[IncomingMessage], Any]
HandlerT = TypeVar("HandlerT", bound=MessageHandler)


class WeixinBot:
    def __init__(
        self,
        base_url: str | None = None,
        token_path: str | None = None,
        on_error: Callable[[object], None] | None = None,
    ) -> None:
        self._base_url = base_url or DEFAULT_BASE_URL
        self._token_path = token_path
        self._on_error = on_error
        self._handlers: list[MessageHandler] = []
        self._context_tokens: dict[str, str] = {}
        self._credentials: Credentials | None = None
        self._cursor = ""
        self._stopped = False
        self._current_poll_task: asyncio.Task[Any] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def login(self, force: bool = False) -> Credentials:
        return asyncio.run(self._login(force=force))

    def on_message(self, handler: HandlerT) -> HandlerT:
        self._handlers.append(handler)
        return handler

    async def reply(self, message: IncomingMessage, text: str) -> None:
        self._context_tokens[message.user_id] = message._context_token
        await self._send_text(message.user_id, text, message._context_token)

        task = asyncio.create_task(self.stop_typing(message.user_id))
        task.add_done_callback(_consume_task_exception)

    async def send_typing(self, user_id: str) -> None:
        context_token = self._context_tokens.get(user_id)
        if context_token is None:
            raise RuntimeError(f"No cached context token for user {user_id}. Reply to an incoming message first.")

        credentials = await self._ensure_credentials()
        config = await get_config(self._base_url, credentials.token, user_id, context_token)
        ticket = config.get("typing_ticket")
        if not isinstance(ticket, str):
            self._log("sendTyping: no typing_ticket returned by getconfig")
            return

        await api_send_typing(self._base_url, credentials.token, user_id, ticket, 1)

    async def stop_typing(self, user_id: str) -> None:
        context_token = self._context_tokens.get(user_id)
        if context_token is None:
            return

        credentials = await self._ensure_credentials()
        config = await get_config(self._base_url, credentials.token, user_id, context_token)
        ticket = config.get("typing_ticket")
        if not isinstance(ticket, str):
            return

        await api_send_typing(self._base_url, credentials.token, user_id, ticket, 2)

    async def send(self, user_id: str, text: str) -> None:
        context_token = self._context_tokens.get(user_id)
        if context_token is None:
            raise RuntimeError(f"No cached context token for user {user_id}. Reply to an incoming message first.")

        await self._send_text(user_id, text, context_token)

    def run(self) -> None:
        self._stopped = False
        asyncio.run(self._run_loop())

    def stop(self) -> None:
        self._stopped = True
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(self._cancel_current_poll)

    async def _login(self, force: bool = False) -> Credentials:
        previous_token = self._credentials.token if self._credentials is not None else None
        credentials = await auth_login(base_url=self._base_url, token_path=self._token_path, force=force)

        self._credentials = credentials
        self._base_url = credentials.base_url

        if previous_token and previous_token != credentials.token:
            self._cursor = ""
            self._context_tokens.clear()

        self._log(f"Logged in as {credentials.user_id}")
        return credentials

    async def _run_loop(self) -> None:
        self._loop = asyncio.get_running_loop()

        try:
            await self._ensure_credentials()
            self._log("Long-poll loop started.")
            retry_delay_seconds = 1.0

            while not self._stopped:
                try:
                    credentials = await self._ensure_credentials()
                    self._current_poll_task = asyncio.create_task(get_updates(self._base_url, credentials.token, self._cursor))
                    updates = await self._current_poll_task
                    self._current_poll_task = None
                    self._cursor = updates.get("get_updates_buf") or self._cursor
                    retry_delay_seconds = 1.0

                    for raw in updates.get("msgs", []):
                        self._remember_context(raw)
                        incoming = self._to_incoming_message(raw)
                        if incoming is None:
                            continue

                        await self._dispatch_message(incoming)
                except asyncio.CancelledError as error:
                    self._current_poll_task = None
                    if self._stopped and _is_abort_error(error):
                        break
                    raise
                except Exception as error:
                    self._current_poll_task = None

                    if self._stopped and _is_abort_error(error):
                        break

                    if _is_session_expired(error):
                        self._log("Session expired. Waiting for a fresh QR login...")
                        self._credentials = None
                        self._cursor = ""
                        self._context_tokens.clear()

                        try:
                            await clear_credentials(self._token_path)
                            await self._login(force=True)
                            retry_delay_seconds = 1.0
                            continue
                        except Exception as login_error:
                            self._report_error(login_error)
                    else:
                        self._report_error(error)

                    await asyncio.sleep(retry_delay_seconds)
                    retry_delay_seconds = min(retry_delay_seconds * 2, 10.0)

            self._log("Long-poll loop stopped.")
        finally:
            self._current_poll_task = None
            self._loop = None

    async def _ensure_credentials(self) -> Credentials:
        if self._credentials is not None:
            return self._credentials

        stored = await load_credentials(self._token_path)
        if stored is not None:
            self._credentials = stored
            self._base_url = stored.base_url
            return stored

        return await self._login()

    async def _send_text(self, user_id: str, text: str, context_token: str) -> None:
        if not text:
            raise ValueError("Message text cannot be empty.")

        credentials = await self._ensure_credentials()
        for chunk in _chunk_text(text, 2_000):
            await send_message(self._base_url, credentials.token, build_text_message(user_id, context_token, chunk))

    async def _dispatch_message(self, message: IncomingMessage) -> None:
        if not self._handlers:
            return

        results = await asyncio.gather(
            *(self._call_handler(handler, message) for handler in self._handlers),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, Exception):
                self._report_error(result)

    async def _call_handler(self, handler: MessageHandler, message: IncomingMessage) -> None:
        result = handler(message)
        if inspect.isawaitable(result):
            await cast(Awaitable[Any], result)

    def _remember_context(self, message: WeixinMessage) -> None:
        user_id = message["from_user_id"] if message["message_type"] == MessageType.USER else message["to_user_id"]
        context_token = message.get("context_token")
        if user_id and context_token:
            self._context_tokens[user_id] = context_token

    def _to_incoming_message(self, message: WeixinMessage) -> IncomingMessage | None:
        if message["message_type"] != MessageType.USER:
            return None

        create_time_ms = message.get("create_time_ms", 0)
        timestamp = datetime.fromtimestamp(create_time_ms / 1000, tz=timezone.utc).astimezone()

        return IncomingMessage(
            user_id=message["from_user_id"],
            text=_extract_text(message["item_list"]),
            type=_detect_type(message["item_list"]),
            raw=message,
            _context_token=message["context_token"],
            timestamp=timestamp,
        )

    def _report_error(self, error: object) -> None:
        if isinstance(error, Exception):
            self._log(str(error))
        else:
            self._log(str(error))
        if self._on_error is not None:
            self._on_error(error)

    def _log(self, message: str) -> None:
        sys.stderr.write(f"[weixin-bot] {message}\n")

    def _cancel_current_poll(self) -> None:
        if self._current_poll_task is not None and not self._current_poll_task.done():
            self._current_poll_task.cancel()


def _detect_type(items: list[MessageItem]) -> MessageKind:
    first = items[0] if items else None
    item_type = first["type"] if first is not None else None

    if item_type == MessageItemType.IMAGE:
        return "image"
    if item_type == MessageItemType.VOICE:
        return "voice"
    if item_type == MessageItemType.FILE:
        return "file"
    if item_type == MessageItemType.VIDEO:
        return "video"
    return "text"


def _extract_text(items: list[MessageItem]) -> str:
    parts: list[str] = []
    for item in items:
        item_type = item["type"]
        if item_type == MessageItemType.TEXT:
            text = item.get("text_item", {}).get("text", "")
        elif item_type == MessageItemType.IMAGE:
            text = item.get("image_item", {}).get("url", "[image]")
        elif item_type == MessageItemType.VOICE:
            text = item.get("voice_item", {}).get("text", "[voice]")
        elif item_type == MessageItemType.FILE:
            text = item.get("file_item", {}).get("file_name", "[file]")
        elif item_type == MessageItemType.VIDEO:
            text = "[video]"
        else:
            text = ""

        if text:
            parts.append(text)

    return "\n".join(parts)


def _chunk_text(text: str, limit: int) -> list[str]:
    chunks = [text[index:index + limit] for index in range(0, len(text), limit)]
    return chunks or [""]


def _consume_task_exception(task: asyncio.Task[Any]) -> None:
    try:
        task.exception()
    except asyncio.CancelledError:
        return
    except Exception:
        return


def _is_abort_error(error: object) -> bool:
    return isinstance(error, (asyncio.CancelledError, TimeoutError, asyncio.TimeoutError))


def _is_session_expired(error: object) -> bool:
    return isinstance(error, ApiError) and error.is_session_expired


__all__ = ["WeixinBot"]
