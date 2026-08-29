#!/bin/bash
# Watch a live campaign run from another terminal.
#
#   ./watch_campaign.sh live2
#
# Shows the conversation and the decisions as they happen — every transcript
# line, which rule released each opener, what answering-machine detection said
# and how late, and the funnel so far. Everything else in the debug log is
# filtered out; it is all still on disk in campaignlogs/<tag>.log.
set -u
TAG="${1:?usage: watch_campaign.sh <tag>}"
LOG=/opt/voice-agent/campaignlogs/${TAG}.log
EVENTS=/opt/voice-agent/campaignlogs/${TAG}.jsonl

if [ ! -f "$LOG" ]; then
  echo "waiting for $LOG …"
  while [ ! -f "$LOG" ]; do sleep 2; done
fi

# The running funnel, refreshed whenever a call finishes.
(
  last=0
  while true; do
    if [ -s "$EVENTS" ]; then
      n=$(wc -l < "$EVENTS")
      if [ "$n" -ne "$last" ]; then
        last=$n
        printf '\n\033[1;36m'
        /opt/voice-agent/voice-agent -report "$EVENTS" 2>/dev/null | sed -n '/FUNNEL/,/link promised/p'
        printf '\033[0m\n'
      fi
    fi
    sleep 20
  done
) &
WATCHER=$!
trap 'kill $WATCHER 2>/dev/null' EXIT

tail -n 200 -f "$LOG" | grep --line-buffered -E \
  'dialing|call answered|opener released|holding the opener|amd verdict|· PROSPECT|· AGENT|tool call|demo link|declined|REFUSED|call complete|whatsapp health|stopping campaign|CAMPAIGN|EXIT'
