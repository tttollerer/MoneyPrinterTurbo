#!/bin/bash
set -euo pipefail

# Finder does not inherit the interactive shell's Homebrew/uv PATH.
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"
cd "$(dirname "$0")"

for dependency in uv node npm ffmpeg ffprobe; do
  if ! command -v "$dependency" >/dev/null 2>&1; then
    echo "Fehlt: $dependency. Einrichtung siehe README-spotforge.md."
    exit 1
  fi
done
node -e 'if (Number(process.versions.node.split(".")[0]) < 22) { console.error("Node.js 22 oder neuer erforderlich."); process.exit(1); }'

if [[ "${1:-}" == "--check" ]]; then
  echo "Voraussetzungen vorhanden."
  exit 0
fi

export SPOTFORGE_DATA_DIR="${SPOTFORGE_DATA_DIR:-$HOME/Documents/Video Generator/spotforge-data}"
export SPOTFORGE_OUTPUT_DIR="${SPOTFORGE_OUTPUT_DIR:-$HOME/Documents/Video Generator/Ausgaben/SpotForge}"

uv sync --frozen --python 3.11
for package in renderer studio; do
  lock_hash="$(shasum -a 256 "$package/package-lock.json" | cut -d ' ' -f 1)"
  installed_hash="$(cat "$package/node_modules/.spotforge-lock.sha256" 2>/dev/null || true)"
  if [[ "$lock_hash" != "$installed_hash" ]]; then
    npm --prefix "$package" ci --no-audit --no-fund
    printf '%s\n' "$lock_hash" > "$package/node_modules/.spotforge-lock.sha256"
  fi
done
npm --prefix studio run build

echo "SpotForge: http://127.0.0.1:4831"
echo "Projekte: $SPOTFORGE_DATA_DIR"
echo "Videos:   $SPOTFORGE_OUTPUT_DIR"
echo "Dieses Terminal geöffnet lassen; Ctrl+C beendet den Server."
exec .venv/bin/python -m spotforge --port 4831
