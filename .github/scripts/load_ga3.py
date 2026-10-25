"""Run full-sized live model workloads without contacting the exam grader."""
import json
import os
import sys
import time
import uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'assignment'))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'assignment/tests'))
import httpx
from fastapi.testclient import TestClient
from agents.app import app
from load_cases import invoices, mail, large_incident

public_file = Path(os.getenv('GA3_PUBLIC_URL_FILE', '/tmp/ga3-deployment/tds-public-url.txt'))
client = httpx.Client(base_url=public_file.read_text().strip(), timeout=60) if public_file.exists() else TestClient(app)
namespace = 'load-'+uuid.uuid4().hex[:12]
report = {}
failures = []
for name, factory, route, headers in [
    ('invoice', invoices, '/a2a/message:send', {'Authorization': 'Bearer '+namespace, 'A2A-Version': '1.0', 'Content-Type': 'application/a2a+json'}),
    ('mailroom', mail, '/v1/mailroom/actions', {}),
    ('incident', large_incident, '/v2/incidents', {}),
]:
    generated = factory(namespace+'-'+name)
    body = generated[0] if isinstance(generated, tuple) else generated
    started = time.monotonic()
    response = client.post(route, json=body, headers=headers)
    elapsed = round(time.monotonic()-started, 2)
    row = {'status': response.status_code, 'elapsed': elapsed, 'inputBytes': len(json.dumps(body).encode())}
    if elapsed >= {'invoice':45,'mailroom':55,'incident':18}[name]:
        failures.append(name+' deadline')
    if response.status_code == 200:
        data = response.json()
        if name == 'invoice':
            decisions = data['task']['artifacts'][0]['parts'][0]['data']['proposals']
            row['correctActions'] = sum(generated[1][p['packageId']]==p['action'] for p in decisions)
            row['total'] = 12
        elif name == 'mailroom':
            row['correctActions'] = sum(generated[1][p['dossierId']]==p['action'] for p in data['proposals'])
            row['total'] = 64
        else:
            row['diagnosis'] = data['diagnosis']
        if name in {'invoice','mailroom'} and row['correctActions'] != row['total']:
            failures.append(name+' action accuracy')
    else:
        row['error'] = response.text[:200]
        failures.append(name+' response')
    report[name] = row
    print(name, json.dumps(row), flush=True)
Path('/tmp/ga3-load-validation.json').write_text(json.dumps(report, indent=2)+'\n')
assert not failures, failures
