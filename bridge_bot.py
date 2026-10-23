"""Doorbell daemon: long-polls WeChat iLink, files incoming messages to inbox.jsonl.

Media items (image/file/video/voice) are downloaded from the WeChat CDN and
AES-128-ECB decrypted at receive time (protocol-spec 8.3/8.4), saved under
media/, and their local paths recorded in the inbox entry so the woken Muse
can actually look at them. The daemon never replies on its own.

v2 hardening: a heartbeat thread keeps status.json fresh (state derived from
credentials-file presence: file cleared by the SDK == session expired), the
log file rotates, and __main__ refuses to start headless QR login when no
credentials exist. Runs under systemd (Restart=on-failure); the doorbell
hook doubles as watchdog (stale heartbeat -> restart, expired -> wake agent).
"""
import asyncio
import base64
import json
import sys
import threading
import time
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).parent / "weixin-bot" / "python"))

import aiohttp
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

from weixin_bot import WeixinBot

HERE = Path(__file__).parent
INBOX = HERE / "inbox.jsonl"
MEDIA_DIR = HERE / "media"
FILES_IN_DIR = HERE / "files-in"  # inbound files land here as agent work files
MEDIA_DIR.mkdir(exist_ok=True)
STATUS = HERE / "status.json"
CREDS_FILE = Path.home() / ".weixin-bot" / "credentials.json"
CDN_BASE = "https://novac2c.cdn.weixin.qq.com/c2c"

bot = WeixinBot()
_counter = [int(time.time() * 1000)]


def write_status(state: str, detail: str = "") -> None:
    try:
        STATUS.write_text(
            json.dumps(
                {
                    "state": state,
                    "detail": detail,
                    "ts": int(time.time()),
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass


def _heartbeat() -> None:
    """Refresh status.json every 20s. The SDK deletes the credentials file
    when the session expires, so file presence is a truthful health signal."""
    while True:
        if CREDS_FILE.exists():
            write_status("running", "credentials present, long-poll active")
        else:
            write_status("expired", "credentials file gone; QR re-login required")
        time.sleep(20)


def log(msg: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    log_path = HERE / "bridge_daemon.log"
    try:
        if log_path.exists() and log_path.stat().st_size > 5_000_000:
            log_path.replace(HERE / "bridge_daemon.log.old")
    except Exception:
        pass
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def parse_aes_key(b64: str) -> bytes:
    decoded = base64.b64decode(b64)
    if len(decoded) == 16:
        return decoded
    if len(decoded) == 32:
        s = decoded.decode("ascii")
        if all(c in "0123456789abcdefABCDEF" for c in s):
            return bytes.fromhex(s)
    raise ValueError(f"unrecognized aes_key encoding ({len(decoded)} bytes decoded)")


def sniff_ext(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:4] == b"GIF8":
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".bin"


async def download_decrypt(encrypt_query_param: str, key: bytes | None) -> bytes:
    url = f"{CDN_BASE}/download?encrypted_query_param={quote(encrypt_query_param, safe='')}"
    async with aiohttp.ClientSession(trust_env=True) as session:
        async with session.get(url) as r:
            if r.status != 200:
                raise RuntimeError(f"CDN download HTTP {r.status}")
            data = await r.read()
    if key is None:
        return data
    plain = AES.new(key, AES.MODE_ECB).decrypt(data)
    try:
        plain = unpad(plain, 16)
    except ValueError:
        pass  # keep raw decrypt if padding is nonstandard
    return plain


async def transcribe_voice(path: Path) -> str | None:
    """Best-effort speech-to-text for an inbound voice file (local Whisper)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(HERE / "transcribe.py"),
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=240)
        text = json.loads(out.decode("utf-8").strip().splitlines()[-1]).get("text", "")
        return text or None
    except Exception as e:
        log(f"transcribe failed: {e}")
        return None


async def fetch_media(msg_id: str, raw: dict) -> list[dict]:
    """Download+decrypt every media item in the message; return saved-file info."""
    saved = []
    items = raw.get("item_list") or []
    for idx, item in enumerate(items):
        itype = item.get("type")
        kind = {2: "image", 3: "voice", 4: "file", 5: "video"}.get(itype)
        if kind is None:
            continue
        sub = item.get(f"{kind}_item") or {}
        media = sub.get("media") or {}
        param = media.get("encrypt_query_param")
        if not param:
            saved.append({"kind": kind, "ok": False, "error": "no encrypt_query_param"})
            continue
        key = None
        try:
            if kind == "image" and sub.get("aeskey"):
                key = bytes.fromhex(sub["aeskey"])
            elif media.get("aes_key"):
                key = parse_aes_key(media["aes_key"])
        except Exception as e:
            saved.append({"kind": kind, "ok": false, "error": f"key parse: {e}"})
            continue
        try:
            data = await download_decrypt(param, key)
            ext = sniff_ext(data) if kind == "image" else ".bin"
            entry_name = None
            if kind == "file":
                # Inbound files are work files for the agent: keep the
                # original filename and park them in files-in/.
                raw_name = (sub.get("file_name") or "").strip()
                safe = "".join(c for c in raw_name if c not in '\\/:*?"<>|').strip()
                FILES_IN_DIR.mkdir(exist_ok=True)
                if safe:
                    path = FILES_IN_DIR / safe
                    if path.exists():
                        path = FILES_IN_DIR / f"{msg_id}-{safe}"
                else:
                    path = FILES_IN_DIR / f"{msg_id}-{idx}{ext}"
                entry_name = path.name
            else:
                path = MEDIA_DIR / f"{msg_id}-{idx}{ext}"
            path.write_bytes(data)
            entry = {"kind": kind, "ok": True, "path": str(path), "bytes": len(data)}
            if entry_name:
                entry["name"] = entry_name
            if kind == "voice":
                transcript = await transcribe_voice(path)
                if transcript:
                    entry["transcript"] = transcript
                    log(f"VOICE transcript: {transcript[:60]!r}")
            saved.append(entry)
            log(f"MEDIA saved {kind} {path.name} ({len(data)} bytes)")
        except Exception as e:
            saved.append({"kind": kind, "ok": False, "error": str(e)})
            log(f"MEDIA failed {kind}: {e}")
    return saved


@bot.on_message
async def handle(msg):
    _counter[0] += 1
    msg_id = f"wx{_counter[0]}"
    ctx = getattr(msg, "_context_token", None) or (msg.raw or {}).get("context_token", "")
    media = []
    if msg.type in ("image", "file", "video", "voice"):
        media = await fetch_media(msg_id, msg.raw or {})
    record = {
        "id": msg_id,
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "user_id": msg.user_id,
        "type": msg.type,
        "text": msg.text,
        "context_token": ctx,
        "media": media,
    }
    with open(INBOX, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    log(f"INBOX {msg_id} type={msg.type} text={msg.text!r} media={len(media)}")
    try:
        await bot.send_typing(msg.user_id)
    except Exception as e:  # typing is best-effort
        log(f"typing failed: {e}")


if __name__ == "__main__":
    if not CREDS_FILE.exists():
        # Never start the SDK's interactive QR login inside a headless service.
        log("no saved credentials; exiting (QR re-login required)")
        write_status("expired", "no saved credentials; QR re-login required")
        sys.exit(0)
    threading.Thread(target=_heartbeat, daemon=True).start()
    creds = bot.login()
    log(f"doorbell daemon started (v2, systemd), bot user_id={creds.user_id}")
    write_status("running", f"started, bot user_id={creds.user_id}")
    try:
        bot.run()
    except Exception as e:  # unexpected crash: systemd Restart=on-failure takes over
        log(f"fatal: {type(e).__name__}: {e}")
        write_status("error", f"{type(e).__name__}: {e}"[:200])
        sys.exit(1)
