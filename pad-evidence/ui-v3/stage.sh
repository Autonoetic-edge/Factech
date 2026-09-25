#!/bin/sh
set -eu
release=/opt/facetech-releases/eval-pad-0d87e7b9a807
previous=/opt/facetech-releases/eval-pad-943dc39ab702
test ! -e "$release"
mkdir -p "$release"
tar -xzf /tmp/facetech-pad-ui-release.tar.gz -C "$release"
cp "$previous/engine/models/det_500m.onnx" "$release/engine/models/"
cp "$previous/engine/models/w600k_r50.onnx" "$release/engine/models/"
cp -p "$previous/.env" "$release/.env"
python3 - "$release" <<'PY'
import hashlib,json,pathlib,sys
root=pathlib.Path(sys.argv[1])
manifest=json.loads((root/'release-manifest.json').read_text())
for name,expected in manifest.items():
    assert hashlib.sha256((root/name).read_bytes()).hexdigest()==expected,name
print('Verified',len(manifest),'release file checksums')
def patch_env(text,changes):
    lines=text.splitlines()
    for key,value in changes.items():
        lines=[line for line in lines if not line.startswith(key+'=')]
        lines.append(key+'='+value)
    return '\n'.join(lines)+'\n'
env=root/'.env'
env.write_text(patch_env(env.read_text(),{'FACETECH_BUILD_ID':root.name,'ENGINE_IMAGE':'facetech-engine:'+root.name}))
env.chmod(0o600)
stage=root/'.env.stage'
stage.write_text(patch_env(env.read_text(),{'FACETECH_CAPTURE_DIR':'/var/lib/facetech/pad-stage-0d87e7b9a807','CADDY_HTTP_PORT':'8100'}))
stage.chmod(0o600)
PY
install -d -m 700 -o 10002 -g 10002 /var/lib/facetech/pad-stage-0d87e7b9a807
cd "$release"
docker compose --env-file .env.stage -f docker-compose.demo.yml -p facetech-pad-stage build engine gateway
docker compose --env-file .env.stage -f docker-compose.demo.yml -p facetech-pad-stage up -d engine gateway
docker compose --env-file .env.stage -f docker-compose.demo.yml -p facetech-pad-stage ps
