import hashlib,json,urllib.request
base='http://127.0.0.1:8080'
def get(path):
    with urllib.request.urlopen(base+path) as r:
        assert r.headers.get('Cache-Control')=='no-store',path
        return r.read()
page=get('/').decode('utf8')
assert 'ui-v5' in page and 'captureNoSave' not in page and 'Process without saving' not in page
assert 'id="evaluationHome"' in page
assert 'id="retryRecording"' in page and 'id="consentState"' in page
for file in ['evaluation-form.js','page.js']:
    served=get('/'+file+'?v=ui-v5')
    local=open('/app/apps/integration-demo/'+file,'rb').read()
    assert served==local
status=json.loads(get('/capture-status?check=origin-v5'))
assert status['enabled'] is True and status['evaluation_only'] is True
assert status['consent_version']=='storage-consent-v1' and status['retention_days']==7
assert status['write_failures']==status['bank_failures']==status['purge_failures']==0
print(json.dumps({'ui':'ui-v5','consent_only':True,'retry_and_diagnostics':True,'served_files_match':True,'status':{k:status[k] for k in ['enabled','evaluation_only','consent_version','retention_days','total','write_failures','bank_failures','purge_failures']}}))
