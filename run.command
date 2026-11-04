#!/bin/zsh
# Double-click launcher for Echem Analyzer (macOS).
# The virtual environment lives outside Google Drive so it doesn't get synced.
cd "$(dirname "$0")" || exit 1
export UV_PROJECT_ENVIRONMENT="$HOME/.cache/echem-analyzer/venv"
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is not installed. Install it with:  brew install uv"
  read -k 1 "?Press any key to close..."
  exit 1
fi

uv run python -m echem_analyzer "$@"
