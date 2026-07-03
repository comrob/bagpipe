#!/bin/bash
set -e

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
INSTALL_TARGET="${INSTALL_TARGET:-$REPO_ROOT}"

if [[ ! -f "$INSTALL_TARGET/convert_bag.sh" ]]; then
    echo "Error: convert_bag.sh not found in INSTALL_TARGET: $INSTALL_TARGET"
    exit 1
fi

if [[ ! -f "$INSTALL_TARGET/docker-compose.yml" ]]; then
    echo "Error: docker-compose.yml not found in INSTALL_TARGET: $INSTALL_TARGET"
    exit 1
fi

mkdir -p ~/.local/bin
ln -sf "$INSTALL_TARGET/convert_bag.sh" ~/.local/bin/convert_bag

# Check if ~/.local/bin is in PATH
if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    echo "# Added by ros_bag_conversion's install.sh" >> ~/.bashrc
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
    # Export for the current session so it can be used immediately.
    export PATH="$HOME/.local/bin:$PATH"
    echo "Added ~/.local/bin to PATH in .bashrc"
fi

# Pre-pull docker image so the first run is faster.
docker compose -f "$INSTALL_TARGET/docker-compose.yml" pull converter