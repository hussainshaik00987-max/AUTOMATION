#!/bin/bash
# ---------------------------------------------------------------------------
# One bounded, fully-recorded live campaign run.
#
#   ./run_campaign.sh <leads.csv> <max-answered> <tag> [--force]
#   ./run_campaign.sh wedding_run2.csv 35 live2
#
# Everything this run produces is named after <tag> and lands in one place, so a
# run can be read back weeks later without anyone remembering what was going on:
#
#   campaignlogs/<tag>.log     every log line, at debug level
#   campaignlogs/<tag>.jsonl   one JSON object per call — timings, AMD verdict,
#                              opener decision, audio counters, transcript
#   results_<tag>.csv          the outcome row per lead
#   recordings/                dual-channel audio, one file per answered call
#
# Read it back with:  ./voice-agent -report campaignlogs/<tag>.jsonl
# ---------------------------------------------------------------------------
set -u
cd /opt/voice-agent

FILE="${1:?usage: run_campaign.sh <leads.csv> <max-answered> <tag> [--force]}"
MAXA="${2:?usage: run_campaign.sh <leads.csv> <max-answered> <tag> [--force]}"
TAG="${3:?usage: run_campaign.sh <leads.csv> <max-answered> <tag> [--force]}"
FORCE="${4:-}"

LOG=/opt/voice-agent/campaignlogs/${TAG}.log
EVENTS=/opt/voice-agent/campaignlogs/${TAG}.jsonl
mkdir -p campaignlogs recordings

say() { echo "$*" | tee -a "$LOG"; }

# --- the leads must exist, and the tag must be fresh ----------------------
if [ ! -f "$FILE" ]; then
  echo "no such leads file: $FILE" >&2; exit 2
fi
if [ -s "$EVENTS" ] && [ "$FORCE" != "--force" ]; then
  echo "campaignlogs/${TAG}.jsonl already has records in it." >&2
  echo "Pick a new tag, or pass --force to append to this one." >&2
  exit 2
fi

# --- do not cold-call people at the wrong time of day ---------------------
#
# The first live campaign went out at 20:30 IST. Three of six connections were
# answering machines and nobody was in a mood to hear about websites — and none
# of that was the pitch's fault, which is exactly the problem: a badly-timed run
# produces numbers that cannot be read. The hour is not a detail of the
# experiment, it is one of its variables.
HOUR=$(TZ=Asia/Kolkata date +%-H)
DOW=$(TZ=Asia/Kolkata date +%u)
if [ "$FORCE" != "--force" ]; then
  if [ "$HOUR" -lt 10 ] || [ "$HOUR" -ge 19 ]; then
    echo "It is $(TZ=Asia/Kolkata date '+%H:%M %Z') — outside 10:00-19:00 IST." >&2
    echo "Indian small businesses answer between about 11:00 and 17:00; calling outside" >&2
    echo "that window measures the hour, not the pitch. Pass --force to run anyway." >&2
    exit 3
  fi
  if [ "$DOW" -eq 7 ]; then
    echo "It is Sunday in India. Pass --force if you mean it." >&2
    exit 3
  fi
fi

# --- the WhatsApp sidecar owns every promise this campaign makes ----------
WA=$(curl -s --max-time 5 http://127.0.0.1:8082/status || echo '{}')
if ! echo "$WA" | grep -q '"connected":true'; then
  echo "WhatsApp sidecar is not linked — refusing to dial." >&2
  echo "  status: $WA" >&2
  echo "  fix:    systemctl status whatsapp-sidecar   (see whatsapp/README.md to re-link)" >&2
  exit 4
fi

# Ask the parser, not wc. These CSVs carry Instagram bios and descriptions with
# embedded newlines inside quoted fields, so `wc -l` on wedding_run2.csv reports
# 69 rows for a file holding 40 leads — and the header of this log is exactly
# what someone reads a month later to work out what this run was.
LEADS=$(PROSPECTS_FILE="$FILE" ./voice-agent -dry-run 2>&1 | grep -c 'would dial')

{
  echo "══════════════════════════════════════════════════════════════════════"
  echo " CAMPAIGN $TAG"
  echo " leads      $FILE  ($LEADS rows)"
  echo " stop after $MAXA answered"
  echo " started    $(TZ=Asia/Kolkata date '+%a %d %b %Y %H:%M:%S %Z')  /  $(date -u '+%H:%M:%S UTC')"
  echo " whatsapp   $WA"
  echo " binary     $(ls -l --time-style=+%Y-%m-%d_%H:%M ./voice-agent | awk '{print $6, $5" bytes"}')"
  echo "══════════════════════════════════════════════════════════════════════"
} > "$LOG"

PROSPECTS_FILE="$FILE" RESULTS_FILE="results_${TAG}.csv" LOG_LEVEL=debug \
  ./voice-agent -run "$TAG" -max-answered "$MAXA" >> "$LOG" 2>&1
CODE=$?

{
  echo "══════════════════════════════════════════════════════════════════════"
  echo " EXIT $CODE at $(TZ=Asia/Kolkata date '+%a %d %b %Y %H:%M:%S %Z')"
  echo "══════════════════════════════════════════════════════════════════════"
} >> "$LOG"

# The report is already printed inside the log by the agent itself; print it
# again here so an operator watching the terminal sees it without tailing.
if [ -s "$EVENTS" ]; then
  ./voice-agent -report "$EVENTS" 2>&1 | tee -a "$LOG"
fi

echo
echo "  log        $LOG"
echo "  events     $EVENTS"
echo "  results    /opt/voice-agent/results_${TAG}.csv"
echo "  recordings /opt/voice-agent/recordings/"
echo "  re-read    ./voice-agent -report $EVENTS"
exit $CODE
