#!/bin/bash
set -e

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"

ORG="${ORG:-comrob}"
IMAGE="${IMAGE:-bagpipe}"
TAG="${TAG:-latest}"
FULL="ghcr.io/$ORG/$IMAGE:$TAG"
BUILD_CONTEXT="${BUILD_CONTEXT:-$REPO_ROOT}"

# The container fallback: locked dependencies only (see Dockerfile).
echo "Building image: $FULL"
docker build -t "$FULL" "$BUILD_CONTEXT"