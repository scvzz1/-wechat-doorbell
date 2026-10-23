"""Reference brain #2: any local agent CLI / shell command.

Polls ../inbox.jsonl for new messages. For each one it runs BRAIN_COMMAND
with the message record as JSON on stdin; whatever the command prints to
stdout (trimmed) becomes the reply, sent through ../send_reply.py.
Empty stdout = stay silent for that message.

This is the adapter for agent CLIs (Claude Code, Codex, Gemini CLI, ...):
wrap your agent in a small shell script that reads a prompt on stdin and
prints the reply, e.g.:

  BRAIN_COMMAND='my-agent-bridge.sh'

where my-agent-bridge.sh extracts .text from the JSON and calls the agent.

Config (environment):
  BRAIN_COMMAND      shell command to run per message (required)
  WECHAT_BRIDGE_DIR  bridge root (default: parent of this file's dir)

Voice messages arrive pre-transcribed by the bridge (media transcript is
folded into the record's text passed on stdin).
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
BRIDGE = Path(os.environ.get("WECHAT_BRIDGE_DIR", HERE.parent))
INBOX = BRIDGE / "inbox.jsonl"
SENT = BRIDGE / "sent.jsonl"
STATE = HERE / ".brain_command_state"
POLL_SECS = 3
COMMAND = os.environ.get("BRAIN_COMMAND", "")


def sent_ids() -> set[str]:
    ids = set()
    if SENT.exists():
        for line in SENT.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    ids.add(json.loads(line)["id"])
                except Exception:
                    pass
    return ids


def send_reply(msg_id: str, text: str) -> bool:
    reply_file = BRIDGE / f"reply-{msg_id}.txt"
    reply_file.write_text(text, encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(BRIDGE / "send_reply.py"), msg_id, str(reply_file)],
        capture_output=True, text=True, timeout=60,
    )
    return "SENT ok" in (r.stdout or "")


def main() -> int:
    if not COMMAND:
        print("set BRAIN_COMMAND", file=sys.stderr)
        return 2
    seen = 0
    if STATE.exists():
        seen = int(STATE.read_text().strip() or 0)
    print(f"brain_command up: {COMMAND!r}, resuming at inbox line {seen}", flush=True)
    while True:
        try:
            lines = INBOX.read_text(encoding="utf-8").splitlines() if INBOX.exists() else []
            done = sent_ids()
            while seen < len(lines):
                line = lines[seen].strip()
                seen += 1
                STATE.write_text(str(seen))
                if not line:
                    continue
                rec = json.loads(line)
                if rec["id"] in done:
                    continue
                # fold voice transcripts into the text the command sees
                for m in rec.get("media") or []:
                    if m.get("kind") == "voice" and m.get("transcript"):
                        rec["text"] = (rec.get("text") or "") + "\n" + m["transcript"]
                try:
                    r = subprocess.run(
                        COMMAND, shell=True, input=json.dumps(rec, ensure_ascii=False),
                        capture_output=True, text=True, timeout=600,
                    )
                    reply = (r.stdout or "").strip()
                except Exception as e:
                    print(f"command error for {rec['id']}: {e}", flush=True)
                    continue
                if reply and send_reply(rec["id"], reply):
                    print(f"replied {rec['id']}: {reply[:60]!r}", flush=True)
        except Exception as e:
            print(f"loop error: {e}", flush=True)
        time.sleep(POLL_SECS)


if __name__ == "__main__":
    raise SystemExit(main())
