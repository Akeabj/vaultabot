#!/usr/bin/env bash
set -e

# Railway assigns the public port via $PORT — default to 8000 for local use.
PORT="${PORT:-8000}"

# Start the Mini App API/web server in the background.
uvicorn miniapp_server:app --host 0.0.0.0 --port "$PORT" &
MINIAPP_PID=$!

# Start the Telegram bot (polling) in the background too.
python bot.py &
BOT_PID=$!

echo "Vaulta: miniapp_server running (pid $MINIAPP_PID), bot running (pid $BOT_PID)"

# If either process exits (crash or otherwise), stop the whole container so
# Railway restarts the service — and both processes come back up together,
# rather than one silently running without the other.
wait -n "$MINIAPP_PID" "$BOT_PID"
exit $?
