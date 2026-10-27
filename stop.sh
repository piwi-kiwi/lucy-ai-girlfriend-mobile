#!/usr/bin/env bash
cd "$(dirname "$0")" || exit 1
if [ -f server.pid ]; then kill "$(cat server.pid)" 2>/dev/null && echo "stopped"; rm -f server.pid; else echo "no pidfile"; fi
