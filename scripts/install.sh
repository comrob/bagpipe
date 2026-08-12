#!/bin/bash
set -e

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
INSTALL_TARGET="${INSTALL_TARGET:-$REPO_ROOT}"

# `bagpipe update` re-runs this to refresh links; it pulls the image itself.
PULL_IMAGE=true
[[ "${1:-}" == "--no-pull" ]] && PULL_IMAGE=false

for required in bagpipe.sh docker-compose.yml lib/common.sh; do
    if [[ ! -f "$INSTALL_TARGET/$required" ]]; then
        echo "Error: $required not found in INSTALL_TARGET: $INSTALL_TARGET"
        exit 1
    fi
done

chmod +x "$INSTALL_TARGET/bagpipe.sh"

mkdir -p ~/.local/bin
ln -sf "$INSTALL_TARGET/bagpipe.sh" ~/.local/bin/bagpipe
# Compatibility: same file, invoked under the old name. bagpipe.sh detects
# this from $0 and implies the `convert` verb.
ln -sf "$INSTALL_TARGET/bagpipe.sh" ~/.local/bin/convert_bag
echo "Linked: ~/.local/bin/bagpipe (and convert_bag for compatibility)"

# Shell completion.
COMPLETION_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/bash-completion/completions"
mkdir -p "$COMPLETION_DIR"
ln -sf "$INSTALL_TARGET/completions/bagpipe.bash" "$COMPLETION_DIR/bagpipe"
ln -sf "$INSTALL_TARGET/completions/bagpipe.bash" "$COMPLETION_DIR/convert_bag"
echo "Linked: completion into $COMPLETION_DIR"

# Ensure ~/.local/bin is on PATH, exactly once.
PATH_MARKER="# Added by bagpipe install.sh"
if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    if ! grep -qF "$PATH_MARKER" ~/.bashrc 2>/dev/null; then
        {
            echo ""
            echo "$PATH_MARKER"
            echo 'export PATH="$HOME/.local/bin:$PATH"'
        } >> ~/.bashrc
        echo "Added ~/.local/bin to PATH in ~/.bashrc (restart your shell or: source ~/.bashrc)"
    fi
    export PATH="$HOME/.local/bin:$PATH"
fi

if $PULL_IMAGE; then
    echo "Pulling container image so the first run is fast..."
    BAGPIPE_IMAGE_TAG="${BAGPIPE_IMAGE_TAG:-latest}" \
        docker compose -f "$INSTALL_TARGET/docker-compose.yml" pull converter
fi

echo ""
echo "Done. Try:  bagpipe info"
