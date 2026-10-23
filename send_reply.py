"""Send one reply for an inbox message: send_reply.py <msg_id> <reply_file>

Looks up user_id + context_token from inbox.jsonl, sends via the iLink SDK,
and appends a receipt line to sent.jsonl. Reply text is read from reply_file
(UTF-8) to avoid shell-quoting issues.
"""
import asyncio
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE / "weixin-bot" / "python"))

from weixin_bot.api import build_text_message, send_message
from weixin_bot.auth import load_credentials


async def main() -> int:
    msg_id, reply_path = sys.argv[1], Path(sys.argv[2])
    text = reply_path.read_text(encoding="utf-8").strip()
    if not text:
        print("empty reply, abort", file=sys.stderr)
        return 2

    record = None
    for line in (HERE / "inbox.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r["id"] == msg_id:
            record = r
    if record is None:
        print(f"msg id {msg_id} not found in inbox", file=sys.stderr)
        return 3

    creds = await load_credentials()
    if creds is None:
        print("no saved credentials", file=sys.stderr)
        return 4

    # chunk like the client does (2000 chars per message)
    for i in range(0, len(text), 2000):
        chunk = text[i : i + 2000]
        await send_message(
            creds.base_url,
            creds.token,
            build_text_message(record["user_id"], record["context_token"], chunk),
        )

    receipt = {
        "id": msg_id,
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "reply_preview": text[:80],
    }
    with open(HERE / "sent.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(receipt, ensure_ascii=False) + "\n")
    print(f"SENT {msg_id} ok ({len(text)} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
