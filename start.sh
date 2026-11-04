#!/usr/bin/env bash
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

echo "Terminating any existing bot instances..."
pkill -f "python3.*main.py" 2>/dev/null || true
sleep 1

echo "Starting Antigravity Telegram & Terminal Bridge..."
exec python3 main.py
