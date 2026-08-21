#!/bin/zsh
set -e
SCRIPT_DIR="${0:A:h}"
cd "$SCRIPT_DIR"
PYTHON_BIN="$(command -v python3)"
if [[ ! -x ".venv/bin/python" ]]; then
  "$PYTHON_BIN" -m venv .venv
fi
.venv/bin/python -m pip install -r requirements.txt pyinstaller==6.15.0
export PLAYWRIGHT_BROWSERS_PATH="$SCRIPT_DIR/playwright-browsers"
.venv/bin/playwright install chromium
.venv/bin/pyinstaller --noconfirm --clean --windowed \
  --name "COM域名筛选器" \
  --collect-all playwright \
  app.py
APP_PATH="$SCRIPT_DIR/dist/COM域名筛选器.app"
mkdir -p "$APP_PATH/Contents/Resources"
cp -R "$SCRIPT_DIR/playwright-browsers" "$APP_PATH/Contents/Resources/playwright-browsers"
codesign --force --deep --sign - "$APP_PATH"
echo "构建完成：$SCRIPT_DIR/dist/COM域名筛选器.app"
