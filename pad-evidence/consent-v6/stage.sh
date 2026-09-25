#!/bin/sh
set -eu
release=/opt/facetech-releases/eval-pad-0e45ad88290f
previous=/opt/facetech-releases/eval-pad-c574af5467d2
test ! -e "$release"
mkdir "$release"
tar -xzf /tmp/facetech-consent-v6.tar.gz -C "$release"
cp "$previous/engine/models/det_500m.onnx" "$release/engine/models/"
cp "$previous/engine/models/w600k_r50.onnx" "$release/engine/models/"
cp -p "$previous/.env" "$release/.env"
python3 - "$previous" "$release" <<'PY'
import hashlib,json,pathlib,subprocess,sys
old,new=map(pathlib.Path,sys.argv[1:])
a=json.loads((old/'release-manifest.json').read_text());b=json.loads((new/'release-manifest.json').read_text())
assert a.keys()==b.keys()
changed=[k for k in b if a[k]!=b[k]]
assert set(changed)=={'apps/integration-demo/evaluation-form.js','apps/integration-demo/page.js','apps/integration-demo/index.html'},changed
for name,expected in b.items():
    assert hashlib.sha256((new/name).read_bytes()).hexdigest()==expected,name
print('Verified',len(b),'checksums; only three UI files differ')
def config(root):
    return json.loads(subprocess.check_output(['docker','compose','--env-file',str(root/'.env'),'-f',str(root/'docker-compose.demo.yml'),'config','--format','json']))['services']
previous=config(old)
def patch(text,changes):
    lines=text.splitlines()
    for key,value in changes.items():
        lines=[line for line in lines if not line.startswith(key+'=')]
        lines.append(key+'='+value)
    return '\n'.join(lines)+'\n'
env=new/'.env'
env.write_text(patch(env.read_text(),{'FACETECH_BUILD_ID':new.name,'ENGINE_IMAGE':previous['engine']['image']}));env.chmod(0o600)
current=config(new)
assert current['engine']['image']==previous['engine']['image']
assert current['gateway']['environment']==previous['gateway']['environment']
assert current['gateway']['volumes']==previous['gateway']['volumes']
assert current['caddy']['environment']==previous['caddy']['environment']
assert {k:v for k,v in current['caddy'].items() if k!='volumes'}=={k:v for k,v in previous['caddy'].items() if k!='volumes'}
assert str(current['gateway']['environment']['FACETECH_EVALUATION_DAYS'])=='7'
state={}
for name in ['facetech-demo-engine-1','facetech-demo-caddy-1','facetech-demo-gateway-1']:
    obj=json.loads(subprocess.check_output(['docker','inspect',name]))[0]
    state[name]={'id':obj['Id'],'started':obj['State']['StartedAt'],'image':obj['Image']}
(new/'deployment-before.json').write_text(json.dumps(state))
stage=new/'.env.stage'
stage.write_text(patch(env.read_text(),{'FACETECH_CAPTURE_DIR':'/var/lib/facetech/consent-v6-stage'}));stage.chmod(0o600)
print('Gateway configuration, captures, credentials and retention preserved; engine image retained')
PY
install -d -m 700 -o 10002 -g 10002 /var/lib/facetech/consent-v6-stage
cd "$release"
docker compose --env-file .env -f docker-compose.demo.yml -p facetech-demo build gateway
docker compose --env-file .env.stage -f docker-compose.demo.yml -p facetech-consent-stage up -d --no-deps gateway
