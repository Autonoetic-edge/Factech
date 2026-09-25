import json,urllib.request
base='http://127.0.0.1:8080'
def get(path):
 with urllib.request.urlopen(base+path) as r:
  assert r.headers.get('Cache-Control')=='no-store',path
  return r.read()
health=json.loads(get('/health'));assert health['buildId']=='eval-pad-0d87e7b9a807'
page=get('/');assert b'ui-v3' in page and b'<fieldset id="evaluation">' in page and b'id="makeTester"' in page and b'id="captureNoSave"' in page
script=get('/page.js');assert b'POSITIONING_LIMIT_MS = 8000' in script and b'/sdk/index.js?v=ui-v3' in script
form=get('/evaluation-form.js');assert b'Choose Save this attempt' in form and b'facetechResetConsent' in form
get('/sdk/index.js')
status=json.loads(get('/capture-status'));assert status['enabled'] and status['evaluation_only'] and status['retention_days']==7
req=urllib.request.Request(base+'/v1/challenge',headers={'X-Facetech-Operation':'enroll','X-User-Id':'ui-check'})
with urllib.request.urlopen(req) as r:
 c=json.load(r);assert c['action']=='HEAD_SEQUENCE' and c['params']['settle_ms']==3200
print(json.dumps({'build':health['buildId'],'visible_form':True,'explicit_recording_choice':True,'tester_generator':True,'bounded_positioning':True,'fresh_assets':True,'challenge':c['action'],'capture_count':status['total']}))
