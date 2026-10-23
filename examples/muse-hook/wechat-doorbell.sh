#!/usr/bin/env bash
# WeChat doorbell v2: watch the bridge inbox; wake Muse on new messages.
# Extra duties: watchdog for the systemd daemon (self-heal restarts), and a
# one-per-episode wake when the WeChat login expires (credentials file gone).
set -euo pipefail
source "$HATCH_HOOK_RUNTIME"

BRIDGE="$HOME/workspace/wechat-bridge"
INBOX="$BRIDGE/inbox.jsonl"
STATUS="$BRIDGE/status.json"
CREDS="$HOME/.weixin-bot/credentials.json"
STATE="$HOME/hooks/state/wechat-doorbell-seen"
EPISODE="$HOME/hooks/state/wechat-bridge-expired-episode"
LASTHEAL="$HOME/hooks/state/wechat-bridge-last-heal"
HEAL_LOG="$BRIDGE/heal.log"
UNIT_SRC="$BRIDGE/wechat-bridge.service"
UNIT_DST="/etc/systemd/system/wechat-bridge.service"
DRY="${HATCH_HOOK_DRY_RUN:-0}"
now=$(date +%s)

creds_present=0
[ -f "$CREDS" ] && creds_present=1

# bridge heartbeat (epoch ts from status.json; 0 when unreadable)
bts=0
if [ -f "$STATUS" ]; then
  bts=$(jq -r '.ts // 0' "$STATUS" 2>/dev/null || echo 0)
  case "$bts" in (*[!0-9]*|"") bts=0 ;; esac
fi

# ---------- 1) new inbox messages -> primary wake ----------
seen=0
if [ -f "$STATE" ]; then
  seen=$(cat "$STATE" || echo 0)
fi
count=0
if [ -f "$INBOX" ]; then
  count=$(wc -l < "$INBOX" | tr -d ' ')
fi

if [ "$count" -gt "$seen" ]; then
  payload=$(tail -n +"$((seen + 1))" "$INBOX" | jq -s -c --argjson creds "$creds_present" '{
    messages: [ .[] | {id: .id, type: .type, text: (.text | if length > 500 then .[:500] else . end)} ],
    bridge_creds_present: $creds
  }')
  if [ "$DRY" != "1" ]; then
    echo "$count" > "$STATE"
  fi
  wake "wechat_new_message" "$payload"
  exit 0
fi

# ---------- 2) login expiry: wake once per episode ----------
if [ "$creds_present" = "0" ]; then
  if [ ! -f "$EPISODE" ]; then
    if [ "$DRY" != "1" ]; then
      touch "$EPISODE"
    fi
    wake "wechat_bridge_expired" '{"reason":"credentials file missing; session expired or cleared"}'
    exit 0
  fi
  silent "bridge login expired (already reported this episode)" '{}'
  exit 0
fi
# credentials are back: close any open expiry episode
if [ -f "$EPISODE" ] && [ "$DRY" != "1" ]; then
  rm -f "$EPISODE"
fi

# ---------- 3) watchdog: heal a dead or hung daemon (silent) ----------
active=$(systemctl is-active wechat-bridge.service 2>/dev/null || echo "inactive")
stale=0
if [ "$bts" = "0" ] || [ $((now - bts)) -gt 180 ]; then
  stale=1
fi
if { [ "$active" != "active" ] || [ "$stale" = "1" ]; } && [ "$DRY" != "1" ]; then
  last=0
  if [ -f "$LASTHEAL" ]; then
    last=$(cat "$LASTHEAL" || echo 0)
  fi
  if [ $((now - last)) -gt 120 ]; then
    echo "$now" > "$LASTHEAL"
    {
      echo "[$(date '+%F %T')] heal attempt: active=$active heartbeat_stale=$stale"
      if [ ! -f "$UNIT_DST" ]; then
        cp "$UNIT_SRC" "$UNIT_DST" && systemctl daemon-reload && systemctl enable wechat-bridge.service
        echo "unit reinstalled from workspace copy"
      fi
      systemctl restart wechat-bridge.service || systemctl start wechat-bridge.service || true
    } >> "$HEAL_LOG" 2>&1 || true
  fi
fi

silent "no new wechat messages; bridge healthy" '{}'
