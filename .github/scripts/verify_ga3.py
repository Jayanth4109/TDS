"""Exercise only synthetic sample cases through the deployed public HTTPS service."""
import json
import re
import runpy
import sys
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
Path('/tmp/ga3-public-validation.json').write_text(json.dumps(results, indent=2)+'\n')
print(json.dumps(results, indent=2))
