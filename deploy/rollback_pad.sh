#!/bin/sh
# WARNING: restores the older heuristic-only build with a confirmed screen-photo
# acceptance. Stop evaluation and notify testers if rollback is necessary.
set -eu
cd /opt/facetech-releases/eval-d36fa4946ddf
docker compose --env-file .env -f docker-compose.demo.yml -p facetech-demo up -d --no-build engine gateway
# Keep captures at /var/lib/facetech/eval-d36fa4946ddf. Do not delete or export them.
# Restart clears only in-memory engine enrollments/nonces; fresh enrollments needed.
