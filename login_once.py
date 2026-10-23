"""One-shot iLink login helper: fetch QR, render PNG, poll until confirmed/expired.

Writes qr_url.txt + qr.png + status to login_status.log in this directory.
Credentials are saved by the SDK to ~/.weixin-bot/credentials.json on success.
"""
import asyncio
import sys
import time
from pathlib import Path

import qrcode

sys.path.insert(0, str(Path(__file__).parent / "weixin-bot" / "python"))

from weixin_bot.api import DEFAULT_BASE_URL, fetch_qr_code, poll_qr_status
from weixin_bot.auth import Credentials, _save_credentials_sync

HERE = Path(__file__).parent
MAX_QR_ROTATIONS = 5


def log(msg: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(HERE / "login_status.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


async def main() -> int:
    for attempt in range(1, MAX_QR_ROTATIONS + 1):
        qr = await fetch_qr_code(DEFAULT_BASE_URL)
        url = qr["qrcode_img_content"]
        (HERE / "qr_url.txt").write_text(url, encoding="utf-8")
        img = qrcode.make(url)
        img.save(HERE / "qr.png")
        log(f"QR_ISSUED attempt={attempt} url={url}")

        last = None
        while True:
            st = await poll_qr_status(DEFAULT_BASE_URL, qr["qrcode"])
            status = st["status"]
            if status != last:
                log(f"STATUS {status}")
                last = status
            if status == "confirmed":
                creds = Credentials(
                    token=st.get("bot_token"),
                    base_url=st.get("baseurl") or DEFAULT_BASE_URL,
                    account_id=st.get("ilink_bot_id"),
                    user_id=st.get("ilink_user_id"),
                )
                if not all([creds.token, creds.account_id, creds.user_id]):
                    log("ERROR confirmed but credentials incomplete")
                    return 2
                _save_credentials_sync(creds, None)
                log(f"LOGIN_OK account_id={creds.account_id}")
                return 0
            if status == "expired":
                log("QR expired, rotating")
                break
            await asyncio.sleep(2)
    log("GAVE_UP too many expired QRs")
    return 3


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
