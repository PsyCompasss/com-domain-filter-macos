#!/bin/zsh
set -e
SCRIPT_DIR="${0:A:h}"
cd "$SCRIPT_DIR"
export PLAYWRIGHT_BROWSERS_PATH="$SCRIPT_DIR/playwright-browsers"
exec "$SCRIPT_DIR/.venv/bin/python" "$SCRIPT_DIR/app.py"
