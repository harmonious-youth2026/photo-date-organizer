#!/usr/bin/env bash
# Photo Date Organizer - macOS / Linux launcher
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"

if command -v python3 >/dev/null 2>&1; then
  PY=python3
else
  PY=python
fi

if [ -n "$1" ]; then
  "$PY" "$DIR/organize_photos.py" "$1"
else
  echo "No folder given - organizing the current directory: $(pwd)"
  echo "Tip: ./run.sh /path/to/your/photos"
  "$PY" "$DIR/organize_photos.py" "$(pwd)"
fi
