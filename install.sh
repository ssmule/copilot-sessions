#!/usr/bin/env bash
# install.sh — put `cs` on your PATH. No dependencies beyond Python 3.10+.
#
#   ./install.sh            # symlink bin/cs into ~/.local/bin
#   ./install.sh /usr/local/bin   # or a directory of your choice
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_DIR="${1:-$HOME/.local/bin}"

command -v python3 >/dev/null 2>&1 || { echo "error: python3 not found"; exit 1; }

mkdir -p "$TARGET_DIR"
ln -sf "$REPO_DIR/bin/cs" "$TARGET_DIR/cs"
chmod +x "$REPO_DIR/bin/cs"

echo "✓ Installed: $TARGET_DIR/cs -> $REPO_DIR/bin/cs"
case ":$PATH:" in
  *":$TARGET_DIR:"*) echo "✓ $TARGET_DIR is on your PATH — run: cs" ;;
  *) echo "⚠ Add to your shell profile:  export PATH=\"$TARGET_DIR:\$PATH\"" ;;
esac
