#!/usr/bin/env bash
# Run checks.py every INTERVAL minutes, forever (Ctrl-C to stop).
INTERVAL=30  # minutes

cd "$(dirname "$0")" || exit 1
while true; do
  echo "== $(date '+%F %T') checks"
  ./checks.py || echo "== checks failed (exit $?)" >&2
  sleep $((INTERVAL * 60))
done
