#!/usr/bin/env bash
# install.sh — put `cs` on your PATH. No dependencies beyond Python 3.10+.
#
#   ./install.sh            # symlink bin/cs into ~/.local/bin
#   ./install.sh /usr/local/bin   # or a directory of your choice
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_DIR="${1:-$HOME/.local/bin}"

command -v python3 >/dev/null 2>&1 || { echo "error: python3 not found"; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || {
  echo "error: cs needs Python 3.10+, found $(python3 -V 2>&1) at $(command -v python3)"
  echo "       Homebrew brings its own: brew install ssmule/tap/copilot-sessions"
  exit 1
}

mkdir -p "$TARGET_DIR"
ln -sf "$REPO_DIR/bin/cs" "$TARGET_DIR/cs"
chmod +x "$REPO_DIR/bin/cs"

echo "✓ Installed: $TARGET_DIR/cs -> $REPO_DIR/bin/cs"
case ":$PATH:" in
  *":$TARGET_DIR:"*) echo "✓ $TARGET_DIR is on your PATH — run: cs" ;;
  *) echo "⚠ Add to your shell profile:  export PATH=\"$TARGET_DIR:\$PATH\"" ;;
esac

# Being on PATH is not the same as winning on PATH: an earlier entry (a Homebrew
# install, say) keeps answering `cs` and the symlink just made looks inert.
FIRST="$(command -v cs 2>/dev/null || true)"
if [ -n "$FIRST" ] && [ "$FIRST" != "$TARGET_DIR/cs" ]; then
  echo "⚠ but '$FIRST' comes first on your PATH, so typing 'cs' still runs that one."
  echo "  Run it directly as $TARGET_DIR/cs, put $TARGET_DIR earlier on PATH,"
  echo "  or remove the other copy. 'which -a cs' lists them all."
fi
