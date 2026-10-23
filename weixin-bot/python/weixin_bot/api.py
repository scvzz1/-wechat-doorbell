from __future__ import annotations

import base64
import json
import os
from typing import Any, Literal, cast
from urllib.parse import quote, urljoin
from uuid import uuid4

from .types import (
    BaseInfo,
    GetConfigResponse,
    GetUpdatesRequest,
    GetUpdatesResponse,
    MessageItemType,
    MessageState,
    MessageType,
    QrCodeResponse,
    QrStatusResponse,
    SendMessageMessage,
    SendTypingRequest,
)

DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
CHANNEL_VERSION = "1.0.0"


class ApiError(Exception):
    def __init__(self, message: str, *, status: int, code: int | None = None, payload: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.payload = payload

    @property
    def is_session_expired(self) -> bool:
        return self.code == -14


def _require_aiohttp() -> Any:
    try:
        import aiohttp
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError("aiohttp is required. Install dependencies with `pip install aiohttp`.") from exc

    return aiohttp


def _normalize_base_url(base_url: str) -> str:
    return base_url.rstrip("/")


def _build_base_info() -> BaseInfo:
    return {"channel_version": CHANNEL_VERSION}


async def _parse_json_response(response: Any, label: str) -> dict[str, Any]:
    text = await response.text()
    payload = cast(dict[str, Any], json.loads(text) if text else {})

    if response.status < 200 or response.status >= 300:
        message = payload.get("errmsg") or f"{label} failed with HTTP {response.status}"
        raise ApiError(message, status=response.status, code=payload.get("errcode"), payload=payload)

    if isinstance(payload.get("ret"), int) and payload["ret"] != 0:
        raise ApiError(
            payload.get("errmsg") or f"{label} failed",
            status=response.status,
            code=cast(int | None, payload.get("errcode", payload["ret"])),
            payload=payload,
        )

    return payload


async def _api_fetch(
    base_url: str,
    endpoint: str,
    body: object,
    token: str,
    timeout_ms: int = 40_000,
) -> dict[str, Any]:
    aiohttp = _require_aiohttp()
    url = urljoin(f"{_normalize_base_url(base_url)}/", endpoint.lstrip("/"))

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout_ms / 1000), trust_env=True) as session:
        async with session.post(url, headers=build_headers(token), json=body) as response:
            return await _parse_json_response(response, endpoint)


async def _api_get(base_url: str, path: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
    aiohttp = _require_aiohttp()
    url = urljoin(f"{_normalize_base_url(base_url)}/", path.lstrip("/"))

    async with aiohttp.ClientSession(trust_env=True) as session:
        async with session.get(url, headers=headers or {}) as response:
            return await _parse_json_response(response, path)


def random_wechat_uin() -> str:
    value = int.from_bytes(os.urandom(4), "big")
    return base64.b64encode(str(value).encode("utf-8")).decode("ascii")


def build_headers(token: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "Authorization": f"Bearer {token}",
        "X-WECHAT-UIN": random_wechat_uin(),
    }


async def get_updates(base_url: str, token: str, buf: str) -> GetUpdatesResponse:
    body: GetUpdatesRequest = {
        "get_updates_buf": buf,
        "base_info": _build_base_info(),
    }
    payload = await _api_fetch(base_url, "/ilink/bot/getupdates", body, token, 40_000)
    return cast(GetUpdatesResponse, payload)


async def send_message(base_url: str, token: str, msg: SendMessageMessage) -> dict[str, Any]:
    payload = await _api_fetch(
        base_url,
        "/ilink/bot/sendmessage",
        {"msg": msg, "base_info": _build_base_info()},
        token,
        15_000,
    )
    return payload


async def get_config(base_url: str, token: str, user_id: str, context_token: str) -> GetConfigResponse:
    payload = await _api_fetch(
        base_url,
        "/ilink/bot/getconfig",
        {
            "ilink_user_id": user_id,
            "context_token": context_token,
            "base_info": _build_base_info(),
        },
        token,
        15_000,
    )
    return cast(GetConfigResponse, payload)


async def send_typing(
    base_url: str,
    token: str,
    user_id: str,
    ticket: str,
    status: Literal[1, 2],
) -> dict[str, Any]:
    body: SendTypingRequest = {
        "ilink_user_id": user_id,
        "typing_ticket": ticket,
        "status": status,
        "base_info": _build_base_info(),
    }
    payload = await _api_fetch(base_url, "/ilink/bot/sendtyping", body, token, 15_000)
    return payload


async def fetch_qr_code(base_url: str) -> QrCodeResponse:
    payload = await _api_get(base_url, "/ilink/bot/get_bot_qrcode?bot_type=3")
    return cast(QrCodeResponse, payload)


async def poll_qr_status(base_url: str, qrcode: str) -> QrStatusResponse:
    payload = await _api_get(
        base_url,
        f"/ilink/bot/get_qrcode_status?qrcode={quote(qrcode, safe='')}",
        {"iLink-App-ClientVersion": "1"},
    )
    return cast(QrStatusResponse, payload)


def build_text_message(user_id: str, context_token: str, text: str) -> SendMessageMessage:
    return {
        "from_user_id": "",
        "to_user_id": user_id,
        "client_id": str(uuid4()),
        "message_type": MessageType.BOT,
        "message_state": MessageState.FINISH,
        "context_token": context_token,
        "item_list": [
            {
                "type": MessageItemType.TEXT,
                "text_item": {"text": text},
            }
        ],
    }


__all__ = [
    "ApiError",
    "DEFAULT_BASE_URL",
    "build_headers",
    "build_text_message",
    "fetch_qr_code",
    "get_config",
    "get_updates",
    "poll_qr_status",
    "random_wechat_uin",
    "send_message",
    "send_typing",
]
