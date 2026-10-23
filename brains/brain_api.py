"""Reference brain #1: plain LLM API (OpenAI-compatible).

Polls ../inbox.jsonl for new messages, answers them with any
OpenAI-compatible chat API, and sends replies through ../send_reply.py.

Config (environment):
  BRAIN_API_BASE   e.g. https://api.openai.com/v1  (no trailing slash)
  BRAIN_API_KEY    your API key
  BRAIN_MODEL      e.g. gpt-4o-mini / deepseek-chat / ...
  WECHAT_BRIDGE_DIR  bridge root (default: parent of this file's dir)

Persona: if brains/persona.md exists it becomes the system prompt;
otherwise a neutral default is used. Conversation history (last 20 turns
per user) is kept in brains/history.json so the model has context.
Voice messages arrive pre-transcribed by the bridge (media transcript).
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
BRIDGE = Path(os.environ.get("WECHAT_BRIDGE_DIR", HERE.parent))
INBOX = BRIDGE / "inbox.jsonl"
SENT = BRIDGE / "sent.jsonl"
STATE = HERE / ".brain_api_state"
HISTORY_FILE = HERE / "history.json"
POLL_SECS = 3

API_BASE = os.environ.get("BRAIN_API_BASE", "")
API_KEY = os.environ.get("BRAIN_API_KEY", "")
MODEL = os.environ.get("BRAIN_MODEL", "")

DEFAULT_PERSONA = (
    "You are a helpful assistant replying inside WeChat. "
    "Keep replies concise and natural, like chat messages."
)


def load_persona() -> str:
    p = HERE / "persona.md"
    if p.exists():
        return p.read_text(encoding="utf-8").strip()
    return DEFAULT_PERSONA


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


def load_history() -> dict:
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_history(h: dict) -> None:
    HISTORY_FILE.write_text(json.dumps(h, ensure_ascii=False), encoding="utf-8")


def effective_text(rec: dict) -> str:
    parts = []
    if rec.get("text"):
        parts.append(rec["text"])
    for m in rec.get("media") or []:
        if m.get("kind") == "voice" and m.get("transcript"):
            parts.append(f"[voice message, transcribed] {m['transcript']}")
        elif m.get("kind") == "image":
            parts.append("[the user sent an image]")
        elif m.get("kind") == "file":
            parts.append("[the user sent a file]")
    return "\n".join(p for p in parts if p).strip()


def call_llm(messages: list[dict]) -> str:
    body = json.dumps({"model": MODEL, "messages": messages}).encode("utf-8")
    req = urllib.request.Request(
        f"{API_BASE}/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"].strip()


def send_reply(msg_id: str, text: str) -> bool:
    reply_file = BRIDGE / f"reply-{msg_id}.txt"
    reply_file.write_text(text, encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(BRIDGE / "send_reply.py"), msg_id, str(reply_file)],
        capture_output=True, text=True, timeout=60,
    )
    return "SENT ok" in (r.stdout or "")


def main() -> int:
    if not (API_BASE and API_KEY and MODEL):
        print("set BRAIN_API_BASE, BRAIN_API_KEY, BRAIN_MODEL", file=sys.stderr)
        return 2
    persona = load_persona()
    seen = 0
    if STATE.exists():
        seen = int(STATE.read_text().strip() or 0)
    print(f"brain_api up: model={MODEL}, resuming at inbox line {seen}", flush=True)
    while True:
        try:
            lines = INBOX.read_text(encoding="utf-8").splitlines() if INBOX.exists() else []
            done = sent_ids()
            history = load_history()
            while seen < len(lines):
                line = lines[seen].strip()
                seen += 1
                STATE.write_text(str(seen))
                if not line:
                    continue
                rec = json.loads(line)
                if rec["id"] in done:
                    continue
                text = effective_text(rec)
                if not text:
                    continue
                user_id = rec.get("user_id", "default")
                turns = history.get(user_id, [])
                messages = [{"role": "system", "content": persona}]
                messages += turns[-20:]
                messages.append({"role": "user", "content": text})
                try:
                    reply = call_llm(messages)
                except Exception as e:
                    print(f"llm error for {rec['id']}: {e}", flush=True)
                    continue
                if reply and send_reply(rec["id"], reply):
                    turns.append({"role": "user", "content": text})
                    turns.append({"role": "assistant", "content": reply})
                    history[user_id] = turns[-40:]
                    save_history(history)
                    print(f"replied {rec['id']}: {reply[:60]!r}", flush=True)
        except Exception as e:
            print(f"loop error: {e}", flush=True)
        time.sleep(POLL_SECS)


if __name__ == "__main__":
    raise SystemExit(main())
