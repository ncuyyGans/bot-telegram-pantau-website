#!/usr/bin/env bash
# Watchdog WebPing Bot — dipanggil cron tiap 5 menit.
# - flock: serialisasi antar-jalan watchdog (cron bisa overlap).
# - bot.py sendiri memegang fcntl lock, jadi tak akan ada 2 instance.
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
LOCK="$DIR/watchdog.lock"
LOG="$DIR/watchdog-runs.log"

exec 9>"$LOCK"
if ! flock -n 9; then
  exit 0
fi

if pgrep -f "webping-bot/bot.py" >/dev/null 2>&1; then
  exit 0
fi

echo "$(date -u '+%Y-%m-%d %H:%M:%S') watchdog: bot mati, menyalakan ulang" >> "$LOG"
cd "$DIR" || exit 1
nohup "$DIR/.venv/bin/python" "$DIR/bot.py" >> bot.log 2>&1 &
echo $! > bot.pid
