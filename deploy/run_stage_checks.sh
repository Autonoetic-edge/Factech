#!/bin/sh
set -eu
for name in image_F1.jpg image_F2.jpg image_T1.jpg; do
  docker exec -i facetech-pad-stage-engine-1 sh -c "cat > /tmp/$name" < "/tmp/$name"
done
docker exec -i facetech-pad-stage-engine-1 sh -c 'cat > /tmp/benchmark_pad.py' < /tmp/facetech-benchmark-pad.py
docker exec -e PYTHONPATH=/app facetech-pad-stage-engine-1 python /tmp/benchmark_pad.py /tmp > /tmp/facetech-vps-benchmark.json
docker exec -i facetech-pad-stage-gateway-1 sh -c 'cat > /tmp/image_F1.jpg' < /tmp/facetech-public-fixture.jpg
docker exec -i facetech-pad-stage-gateway-1 python - < /tmp/facetech-smoke-pad.py > /tmp/facetech-stage-smoke.json
cat /tmp/facetech-vps-benchmark.json
cat /tmp/facetech-stage-smoke.json
docker stats --no-stream --format '{{.Name}} {{.MemUsage}} {{.CPUPerc}}' facetech-pad-stage-engine-1 facetech-pad-stage-gateway-1
