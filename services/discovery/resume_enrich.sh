#!/usr/bin/env bash
# Resume Instagram enrichment after letting the rate window drain.
# Kept as a script (not an inline command) so `ps`/`pkill` patterns can't self-match the
# invoking shell - that mistake kills the caller instead of the target.
cd "/mnt/d/Code/Personal Projects/Cold Calling India/Scrap" || exit 1
DRAIN="${1:-600}"
sleep "$DRAIN"
exec ~/.venvs/coldcall/bin/python run.py enrich --limit 2500 >> logs/enrich2.log 2>&1
