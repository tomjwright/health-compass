#!/usr/bin/env bash
# Starts the API image and fails unless /health answers within 60 seconds.
# Usage: scripts/image_smoke.sh IMAGE DATABASE_URL
set -euo pipefail
image="$1"
database_url="$2"

docker run -d --name api-smoke --network host -e DATABASE_URL="$database_url" "$image" >/dev/null
trap 'docker rm -f api-smoke >/dev/null' EXIT

for _ in $(seq 1 60); do
  # A container that exited (e.g. port 8000 already taken) must not pass on another server's answer.
  if [ "$(docker inspect --format '{{.State.Running}}' api-smoke)" != "true" ]; then
    echo "The container exited. Container logs:"
    docker logs api-smoke
    exit 1
  fi
  if curl -sf http://127.0.0.1:8000/health; then
    echo
    echo "The image started and /health answered."
    exit 0
  fi
  sleep 1
done
echo "/health did not answer within 60 seconds. Container logs:"
docker logs api-smoke
exit 1
