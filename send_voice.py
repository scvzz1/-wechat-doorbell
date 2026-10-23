"""Send one voice message for an inbox message:
send_voice.py <msg_id> <audio_path> <encode_type> <playtime_ms> <sample_rate>

Upload pipeline mirrors send_image.py but with media_type=4 (VOICE) and a
voice_item in sendmessage. Appends a receipt to sent.jsonl.
"""
import asyncio
import base64
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE / "weixin-bot" / "python"))

import aiohttp
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

from weixin_bot.api import _api_fetch, send_message
from weixin_bot.auth import load_credentials
from weixin_bot.types import MessageState, MessageType

CDN_BASE = "https://novac2c.cdn.weixin.qq.com/c2c"


def find_record(msg_id: str) -> dict:
    for line in (HERE / "inbox.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            if r["id"] == msg_id:
                return r
    raise SystemExit(f"msg id {msg_id} not found in inbox")


async def main() -> int:
    msg_id = sys.argv[1]
    audio_path = Path(sys.argv[2])
    encode_type = int(sys.argv[3])
    playtime_ms = int(sys.argv[4])
    sample_rate = int(sys.argv[5])

    record = find_record(msg_id)
    creds = await load_credentials()
    if creds is None:
        print("no saved credentials", file=sys.stderr)
        return 4

    raw = audio_path.read_bytes()
    rawsize = len(raw)
    rawmd5 = hashlib.md5(raw).hexdigest()
    key = os.urandom(16)
    aeskey_hex = key.hex()
    encrypted = AES.new(key, AES.MODE_ECB).encrypt(pad(raw, 16))
    filesize = len(encrypted)
    filekey = os.urandom(16).hex()

    resp = await _api_fetch(
        creds.base_url,
        "/ilink/bot/getuploadurl",
        {
            "filekey": filekey,
            "media_type": 4,
            "to_user_id": record["user_id"],
            "rawsize": rawsize,
            "rawfilemd5": rawmd5,
            "filesize": filesize,
            "no_need_thumb": True,
            "aeskey": aeskey_hex,
            "base_info": {"channel_version": "1.0.0"},
        },
        creds.token,
        15_000,
    )
    upload_param = resp.get("upload_param")
    if not upload_param:
        print(f"getuploadurl bad response: {resp}", file=sys.stderr)
        return 5

    url = f"{CDN_BASE}/upload?encrypted_query_param={quote(upload_param, safe='')}&filekey={filekey}"
    final_param = None
    async with aiohttp.ClientSession(trust_env=True) as session:
        for attempt in range(1, 4):
            async with session.post(
                url, data=encrypted, headers={"Content-Type": "application/octet-stream"}
            ) as r:
                body = await r.read()
                if r.status == 200:
                    final_param = r.headers.get("x-encrypted-param")
                    break
                err = r.headers.get("x-error-message") or body[:200]
                print(f"CDN attempt {attempt}: HTTP {r.status} err={err}", file=sys.stderr)
                if 400 <= r.status < 500:
                    return 6
            await asyncio.sleep(1.5)
    if not final_param:
        print("CDN upload failed after retries", file=sys.stderr)
        return 7

    msg = {
        "from_user_id": "",
        "to_user_id": record["user_id"],
        "client_id": f"voice-{int(time.time() * 1000)}",
        "message_type": MessageType.BOT,
        "message_state": MessageState.FINISH,
        "context_token": record["context_token"],
        "item_list": [
            {
                "type": 3,
                "voice_item": {
                    "media": {
                        "encrypt_query_param": final_param,
                        "aes_key": base64.b64encode(aeskey_hex.encode()).decode(),
                        "encrypt_type": 1,
                    },
                    "encode_type": encode_type,
                    "bits_per_sample": 16,
                    "sample_rate": sample_rate,
                    "playtime": playtime_ms,
                },
            }
        ],
    }
    resp2 = await send_message(creds.base_url, creds.token, msg)
    print(f"sendmessage response: {resp2}", file=sys.stderr)

    receipt = {
        "id": msg_id,
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "reply_preview": f"[voice sent: {audio_path.name}, encode_type={encode_type}, {playtime_ms}ms]",
    }
    with open(HERE / "sent.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(receipt, ensure_ascii=False) + "\n")
    print(f"VOICE SENT {msg_id} ok ({rawsize} bytes, encode_type={encode_type})")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
