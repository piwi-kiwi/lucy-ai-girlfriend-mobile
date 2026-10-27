#!/usr/bin/env bash
cd "$(dirname "$0")" || exit 1
PORT="${1:-8899}"
if [ -f server.pid ]; then kill -9 "$(cat server.pid)" 2>/dev/null; rm -f server.pid; fi
sleep 0.4
command -v termux-wake-lock >/dev/null 2>&1 && termux-wake-lock >/dev/null 2>&1
setsid nohup python3 serve.py "$PORT" > server.log 2>&1 < /dev/null &
sleep 1.5
if curl -s -m 4 "http://127.0.0.1:$PORT/ping" | grep -q pong; then
  echo "OK -> http://127.0.0.1:$PORT/"
else
  echo "FAILED"; tail -20 server.log
fi
