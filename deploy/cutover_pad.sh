#!/bin/sh
set -eu
release=/opt/facetech-releases/eval-pad-80a3dfa1dfe9
previous=/opt/facetech-releases/eval-d36fa4946ddf
python3 - "$previous" "$release" <<'PY'
import json,pathlib,subprocess,sys
old,new=map(pathlib.Path,sys.argv[1:])
def config(root):
    return json.loads(subprocess.check_output(['docker','compose','--env-file',str(root/'.env'),'-f',str(root/'docker-compose.demo.yml'),'config','--format','json']))['services']
a,b=config(old),config(new)
for key in ('ENGINE_API_KEY',):
    assert a['engine']['environment'][key]==b['engine']['environment'][key]
assert a['caddy']['environment']['DEMO_AUTH_HASH']==b['caddy']['environment']['DEMO_AUTH_HASH']
assert a['gateway']['volumes']==b['gateway']['volumes']
assert a['gateway']['environment']['FACETECH_CAPTURE_DB']==b['gateway']['environment']['FACETECH_CAPTURE_DB']
assert str(b['gateway']['environment']['FACETECH_EVALUATION_DAYS'])=='7'
print('Existing credentials, capture mount/database and seven-day retention preserved')
PY
docker tag facetech-engine:eval-d36fa4946ddf facetech-engine:rollback-pad-80a3dfa1dfe9
docker tag facetech-mock-gateway:eval-d36fa4946ddf facetech-mock-gateway:rollback-pad-80a3dfa1dfe9
cd "$release"
docker compose --env-file .env.stage -f docker-compose.demo.yml -p facetech-pad-stage stop gateway engine
# Preserve the existing caddy container and its Basic Auth/edge configuration.
docker compose --env-file .env -f docker-compose.demo.yml -p facetech-demo up -d --no-build engine gateway
docker compose --env-file .env -f docker-compose.demo.yml -p facetech-demo ps
