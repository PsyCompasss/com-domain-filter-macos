#!/bin/zsh
set -e
SCRIPT_DIR="${0:A:h}"
cd "$SCRIPT_DIR"
PYTHON_BIN="$(command -v python3)"
if [[ ! -x ".venv/bin/python" ]]; then
  "$PYTHON_BIN" -m venv .venv
fi
.venv/bin/python -m pip install -r requirements.txt pyinstaller==6.15.0
.venv/bin/pyinstaller --noconfirm --clean "COM域名筛选器.spec"
APP_PATH="$SCRIPT_DIR/dist/全网域名筛选器.app"
codesign --force --deep --sign - "$APP_PATH"
echo "构建完成：$SCRIPT_DIR/dist/全网域名筛选器.app"
