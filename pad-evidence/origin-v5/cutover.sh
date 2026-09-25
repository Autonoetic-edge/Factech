#!/bin/sh
set -eu
release=/opt/facetech-releases/eval-pad-c574af5467d2
previous=/opt/facetech-releases/eval-pad-c9a87c4f2266
docker tag facetech-mock-gateway:eval-pad-c9a87c4f2266 facetech-mock-gateway:rollback-origin-v5
cd "$release"
docker compose --env-file .env.stage -f docker-compose.demo.yml -p facetech-origin-stage stop gateway
docker compose --env-file .env -f docker-compose.demo.yml -p facetech-demo up -d --no-build --no-deps gateway
python3 - <<'PY'
import json,pathlib,subprocess
root=pathlib.Path('/opt/facetech-releases/eval-pad-c574af5467d2')
before=json.loads((root/'deployment-before.json').read_text())
for name in ['facetech-demo-engine-1','facetech-demo-caddy-1']:
    obj=json.loads(subprocess.check_output(['docker','inspect',name]))[0]
    after={'id':obj['Id'],'started':obj['State']['StartedAt'],'image':obj['Image']}
    assert after==before[name],name
print('Engine and Caddy identities, images and start times unchanged')
PY
