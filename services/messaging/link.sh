#!/bin/bash
# Link this sidecar to a WhatsApp account, interactively, then hand back to systemd.
#
#   ./link.sh                 -> live QR, refreshed on screen until you scan it
#   ./link.sh 919876543210    -> pairing code to type into WhatsApp instead
#
# Run it in a terminal you can SEE — the QR is drawn with block characters and
# needs a readable window. The service is stopped for the duration and restarted
# automatically the moment the link succeeds, so there is no half-linked state to
# clean up if you change your mind and Ctrl-C.
set -u
cd /opt/whatsapp-sidecar

NUM="${1:-}"
RESTART=0
if systemctl is-active --quiet whatsapp-sidecar; then RESTART=1; fi

cleanup() {
  echo
  echo "==> restarting the sidecar service..."
  systemctl start whatsapp-sidecar 2>/dev/null
  sleep 3
  echo -n "==> status: "; curl -s --max-time 5 http://127.0.0.1:8082/status; echo
}
trap cleanup EXIT

[ "$RESTART" = "1" ] && { echo "==> stopping whatsapp-sidecar for the link..."; systemctl stop whatsapp-sidecar; sleep 1; }

if [ -s auth/creds.json ]; then
  echo "!!  auth/ already holds a session. To link a DIFFERENT account, first run:"
  echo "!!      systemctl stop whatsapp-sidecar && rm -rf /opt/whatsapp-sidecar/auth/*"
  echo
fi

if [ -n "$NUM" ]; then
  echo "==> requesting a PAIRING CODE for +$NUM"
  echo "    WhatsApp ▸ Settings ▸ Linked devices ▸ Link a device"
  echo "                        ▸ Link with phone number instead"
  WA_LINK_ONCE=1 WA_PAIRING_NUMBER="$NUM" node index.js
else
  echo "==> showing a QR. WhatsApp ▸ Settings ▸ Linked devices ▸ Link a device"
  echo "    It refreshes every ~20s; just scan whichever one is on screen."
  WA_LINK_ONCE=1 node index.js
fi
