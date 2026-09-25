#!/usr/bin/env bash

set -euo pipefail
cd "$(dirname "$0")/.."

HEALTH_ATTEMPTS="${HEALTH_ATTEMPTS:-30}"
HEALTH_DELAY="${HEALTH_DELAY:-4}"
ROLLBACK_TAG="facetech-engine:rollback"

if [[ -f .env ]]; then
  set -a

  source .env
  set +a
fi

if [[ -z "${ENGINE_API_KEY:-}" ]]; then
  echo "FATAL: ENGINE_API_KEY is not set in .env — the engine requires it" >&2
  echo "  generate one: python3 -c 'import secrets; print(secrets.token_urlsafe(32))'" >&2
  exit 1
fi

if [[ -n "${GHCR_TOKEN:-}" ]]; then
  echo "$GHCR_TOKEN" | docker login ghcr.io -u "${GHCR_USER:-github}" --password-stdin
fi

previous_image=""
prev_cid="$(docker compose ps -q engine 2>/dev/null || true)"
if [[ -n "$prev_cid" ]]; then
  previous_image="$(docker inspect -f '{{.Image}}' "$prev_cid" 2>/dev/null || true)"
fi
if [[ -n "$previous_image" ]]; then
  docker tag "$previous_image" "$ROLLBACK_TAG"
  echo "rollback target pinned: $ROLLBACK_TAG -> $previous_image"
else
  echo "no engine container running — first deploy, nothing to roll back to"
fi

engine_health_ok() {
  local attempts="$1" delay="$2" i status
  for ((i = 1; i <= attempts; i++)); do
    if docker compose exec -T engine python -c "
import sys, urllib.request
try:
    with urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5) as r:
        body = r.read().decode()[:200]
        if r.status != 200:
            sys.exit(f'status {r.status}')
        print(body)
except Exception as exc:
    sys.exit(str(exc))
" 2>/dev/null; then
      echo "  /health OK after ${i} attempt(s)"
      return 0
    fi

    status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}' \
      "$(docker compose ps -q engine 2>/dev/null)" 2>/dev/null || echo "no-container")"
    echo "  attempt ${i}/${attempts}: not serving yet (container: ${status})"
    sleep "$delay"
  done
  return 1
}

roll_back() {
  echo "--- health gate FAILED, rolling back ---" >&2
  docker compose logs --no-color --tail 100 engine >&2 || true
  if ! docker image inspect "$ROLLBACK_TAG" >/dev/null 2>&1; then
    echo "FATAL: the new engine is not healthy and there is no previous image" >&2
    echo "  to roll back to (first deploy?). The stack is left as it is for" >&2
    echo "  inspection: docker compose ps && docker compose logs engine" >&2
    exit 1
  fi
  echo "restoring $ROLLBACK_TAG" >&2

  if ENGINE_IMAGE="$ROLLBACK_TAG" docker compose up -d --no-deps engine &&
    engine_health_ok 15 "$HEALTH_DELAY"; then
    echo "FATAL: the new engine image failed its health gate — ROLLED BACK to" >&2
    echo "  the previous image, which is healthy and serving. Nothing else to" >&2
    echo "  do on the VPS; fix the image and merge again." >&2
    echo "  NOTE: .env still names the new ENGINE_IMAGE, so the next run of" >&2
    echo "  this script will try it again." >&2
  else
    echo "FATAL: the new engine image failed its health gate AND the rollback" >&2
    echo "  to $ROLLBACK_TAG did not come up healthy either. This one needs" >&2
    echo "  hands: docker compose ps && docker compose logs engine" >&2
  fi
  exit 1
}

docker compose pull
docker compose up -d --remove-orphans

echo "--- health gate (up to $((HEALTH_ATTEMPTS * HEALTH_DELAY))s) ---"
if ! engine_health_ok "$HEALTH_ATTEMPTS" "$HEALTH_DELAY"; then
  roll_back
fi

docker image prune -f
docker rmi "$ROLLBACK_TAG" >/dev/null 2>&1 || true

echo "--- status ---"
docker compose ps

if curl -fsS "https://${API_DOMAIN}/healthz" >/dev/null 2>&1; then
  echo "caddy OK"
else
  echo "WARN: caddy health check failed (expected before DNS/TLS is set up)"
fi
echo "deploy OK"
