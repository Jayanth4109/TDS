"""Exercise only synthetic sample cases through the deployed public HTTPS service."""
import json
import re
import runpy
import sys
import os
import time
import uuid
from pathlib import Path

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'assignment'))

base = Path('/tmp/ga3-deployment/tds-public-url.txt').read_text().strip()
assert re.fullmatch(r'https://[a-z0-9-]+\.trycloudflare\.com', base)
client = httpx.Client(base_url=base, timeout=50)
results = {}
for path in ['/healthz', '/.well-known/agent-card.json', '/media/nixon.glb', '/media/morph.mp4']:
    response = client.get(path, headers={'Origin': 'https://exam.sanand.workers.dev'})
    assert response.status_code == 200, (path, response.status_code)
    if path.endswith('agent-card.json'):
        assert response.json()['supportedInterfaces'][0]['url'] == base+'/a2a/'
    if path.startswith('/media/'):
        assert response.headers.get('access-control-allow-origin') == '*'
        assert response.content == Path('submissions/'+path.rsplit('/', 1)[1]).read_bytes()
    results[path] = 'HTTP 200; card binding or media hash/CORS verified'

suffix = uuid.uuid4().hex[:10]
invoice = runpy.run_path('tests/test_invoice.py')
headers = {**invoice['HEADERS'], 'Authorization': 'Bearer smoke-'+suffix}
started = time.monotonic()
response = client.post('/a2a/message:send', json=invoice['message']('public-'+suffix), headers=headers)
assert response.status_code == 200, ('invoice proposal', response.status_code, response.text[:150])
results['invoice_planning_seconds'] = round(time.monotonic()-started, 2)
task = response.json()['task']
continuation = invoice['continuation'](task)
continuation['message']['messageId'] = 'result-'+suffix
response = client.post('/a2a/message:send', json=continuation, headers=headers)
assert response.status_code == 200 and response.json()['task']['status']['state'] == 'TASK_STATE_COMPLETED'
assert client.get('/a2a/tasks/'+task['id'], headers={**headers, 'Authorization': 'Bearer outsider-'+suffix}).status_code == 404
assert client.get('/a2a/tasks', headers={**headers, 'Authorization': 'Bearer outsider-'+suffix}).json() == {'tasks': []}
results['invoice'] = 'Live proposal, receipt completion, isolated task read and list passed'

mailroom = runpy.run_path('tests/test_mailroom.py')
key = Ed25519PrivateKey.generate()
started = time.monotonic()
response = client.post('/v1/mailroom/actions', json=mailroom['request'](key, 'public-'+suffix))
assert response.status_code == 200, ('mailroom proposal', response.status_code, response.text[:150])
results['mailroom_planning_seconds'] = round(time.monotonic()-started, 2)
receipt = mailroom['commit'](response.json(), key)
response = client.post('/v1/mailroom/actions', json=receipt)
assert response.status_code == 200 and response.json()['status'] == 'completed'
results['mailroom'] = 'Live proposal, Ed25519-signed receipt verification and completion passed'

incident = runpy.run_path('tests/test_incident.py')
run_id = 'public-'+suffix
started = time.monotonic()
response = client.post('/v2/incidents', json=incident['request'](run_id))
assert response.status_code == 200, ('incident plan', response.status_code, response.text[:150])
results['incident_planning_seconds'] = round(time.monotonic()-started, 2)
state = response.json()
step = 1
while state['status'] == 'waiting' and step < 5:
    receipt = {'receiptId': f'public-{suffix}-{step}', 'outcomes': [incident['outcome'](d) for d in state['dispatches']]}
    response = client.post('/v2/incidents/'+run_id+'/receipts', json=receipt)
    assert response.status_code == 200
    state = response.json()
    step += 1
assert state['status'] == 'completed'
assert client.get('/v2/incidents/'+run_id).json() == state
results['incident'] = 'Live diagnosis, diagnostic/effect receipt lifecycle and stored state passed'

if os.getenv('GA3_BATTERY') == '1':
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'assignment/tests'))
    from load_cases import invoices, mail, large_incident
    # The preceding smoke calls are separate from this cold verification sequence.
    # Let the observed per-minute provider window reset before measuring its capacity.
    print('Waiting for provider quota window before the complete sequence', flush=True)
    time.sleep(62)
    started = time.monotonic()
    invoice_count = 0
    for number in range(5):
        body, expected = invoices('battery-'+suffix+'-'+str(number))
        response = client.post('/a2a/message:send', json=body, headers=headers)
        assert response.status_code == 200, ('invoice battery', number, response.status_code, response.text[:150])
        task = response.json()['task']
        batch = task['artifacts'][0]['parts'][0]['data']
        assert all(expected[p['packageId']] == p['action'] for p in batch['proposals'])
        receipts = [{**{k:p[k] for k in ('packageId','actionId','action')}, 'outcome':'ACCEPTED', 'receiptNonce':uuid.uuid4().hex} for p in batch['proposals']]
        response = client.post('/a2a/message:send', headers=headers, json={'message':{'messageId':uuid.uuid4().hex,'taskId':task['id'],'contextId':task['contextId'],'role':'ROLE_USER','parts':[{'mediaType':'application/vnd.ga5.invoice-action-results+json','data':{'batchId':batch['batchId'],'results':receipts}}]}})
        assert response.status_code == 200 and response.json()['task']['status']['state']=='TASK_STATE_COMPLETED'
        invoice_count += len(receipts)
    results['invoice_battery'] = {'correctActions':invoice_count, 'completedBatches':5, 'seconds':round(time.monotonic()-started,2)}
    print('invoice battery passed:', results['invoice_battery'], flush=True)
    first_body, first_expected, _ = mail('battery-'+suffix+'-mail-first')
    started = time.monotonic()
    for number in range(2):
        body, expected, private = mail('battery-'+suffix+'-mail-'+str(number))
        if number:
            body['dossiers'][:58] = first_body['dossiers'][:58]
            expected.update(first_expected)
        else:
            first_body, first_expected = body, expected
        response = client.post('/v1/mailroom/actions', json=body)
        assert response.status_code == 200, ('mailroom battery',number,response.status_code,response.text[:150])
        proposed = response.json()
        assert len(proposed['proposals'])==64 and all(expected[p['dossierId']]==p['action'] for p in proposed['proposals'])
        from agents.mailroom import proposal_digest
        from agents.common import canonical
        import base64
        receipts=[]
        for proposal in proposed['proposals']:
            receipt={k:proposal[k] for k in ('dossierId','callId','action')}
            receipt.update(accepted=True,proposalDigest=proposal_digest(proposal),receiptId=uuid.uuid4().hex)
            signed={'profile':body['profile'],'evaluationId':body['evaluationId'],'inputDigest':proposed['inputDigest'],'receipt':receipt}
            receipt['receiptSignature']=base64.b64encode(private.sign(canonical(signed).encode())).decode()
            receipts.append(receipt)
        response=client.post('/v1/mailroom/actions',json={'profile':body['profile'],'operation':'commit','evaluationId':body['evaluationId'],'inputDigest':proposed['inputDigest'],'receipts':receipts})
        assert response.status_code==200 and response.json()['status']=='completed'
    results['mailroom_battery']={'evaluations':2,'correctActions':128,'uniqueDossiers':70,'seconds':round(time.monotonic()-started,2)}
    print('mailroom battery passed:',results['mailroom_battery'],flush=True)
    started=time.monotonic()
    for number in range(7):
        rid='battery-'+suffix+'-incident-'+str(number)
        response=client.post('/v2/incidents',json=large_incident(rid))
        assert response.status_code==200,('incident battery',number,response.status_code,response.text[:150])
        state=response.json()
        step=0
        while state['status']=='waiting' and step<4:
            response=client.post('/v2/incidents/'+rid+'/receipts',json={'receiptId':uuid.uuid4().hex,'outcomes':[incident['outcome'](d) for d in state['dispatches']]})
            assert response.status_code==200
            state=response.json();step+=1
        assert state['status']=='completed'
    results['incident_battery']={'completedRuns':7,'seconds':round(time.monotonic()-started,2)}
    print('incident battery passed:',results['incident_battery'],flush=True)
Path('/tmp/ga3-public-validation.json').write_text(json.dumps(results, indent=2)+'\n')
print(json.dumps(results, indent=2))
