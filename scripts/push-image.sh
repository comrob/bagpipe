#!/bin/bash
set -e

ORG="${ORG:-comrob}"
IMAGE="${IMAGE:-bagpipe}"
TAG="${TAG:-latest}"
FULL="ghcr.io/$ORG/$IMAGE:$TAG"

echo "Pushing image: $FULL"
docker push "$FULL"