"""Check exact A2A response headers and the complete public invoice lifecycle."""
import json
import runpy
import sys
import time
import uuid
from pathlib import Path

import httpx

root=Path(__file__).resolve().parents[2]/'assignment'
sys.path.insert(0,str(root))
sys.path.insert(0,str(root/'tests'))
from load_cases import invoices

base=Path('/tmp/ga3-deployment/tds-public-url.txt').read_text().strip()
client=httpx.Client(base_url=base,timeout=45)
fixture=runpy.run_path(str(root/'tests/test_invoice.py'))
suffix=uuid.uuid4().hex[:12]
headers={**fixture['HEADERS'],'Authorization':'Bearer q2-verify-'+suffix}
verified=[]


def a2a(response, label):
    assert response.status_code==200,(label,response.status_code,response.text[:200])
    assert response.headers.get('content-type')=='application/a2a+json',(label,response.headers.get('content-type'))
    verified.append(label)
    return response.json()


card=client.get('/.well-known/agent-card.json')
assert card.status_code==200 and card.json()['supportedInterfaces'][0]['url']==base+'/a2a/'
task=a2a(client.post('/a2a/message:send',json=fixture['message']('initial-'+suffix),headers=headers),'message:send initial')['task']
a2a(client.get('/a2a/tasks/'+task['id'],headers=headers),'task read')
a2a(client.get('/a2a/tasks',headers=headers),'task listing')
outsider={**headers,'Authorization':'Bearer outsider-'+suffix}
assert client.get('/a2a/tasks/'+task['id'],headers=outsider).status_code==404
assert a2a(client.get('/a2a/tasks',headers=outsider),'outsider empty list')=={'tasks':[]}
result=fixture['continuation'](task)
result['message']['messageId']='result-'+suffix
terminal=a2a(client.post('/a2a/message:send',json=result,headers=headers),'message:send receipt completion')['task']
assert terminal['status']['state']=='TASK_STATE_COMPLETED'
assert a2a(client.post('/a2a/message:send',json=result,headers=headers),'receipt replay')['task']==terminal
cancellable=a2a(client.post('/a2a/message:send',json=fixture['message']('cancel-'+suffix),headers=headers),'cancellable task')['task']
canceled=a2a(client.post('/a2a/tasks/'+cancellable['id']+':cancel',headers=headers),'task cancel')
assert canceled['status']['state']=='TASK_STATE_CANCELED'

started=time.monotonic()
count=0
for n in range(6):
    body,expected=invoices('q2-battery-'+suffix+'-'+str(n))
    if n==5:
        body['message']['parts'][0]['data']['packages']=body['message']['parts'][0]['data']['packages'][:4]
    task=a2a(client.post('/a2a/message:send',json=body,headers=headers),'invoice batch '+str(n))['task']
    data=task['artifacts'][0]['parts'][0]['data']
    assert all(expected[p['packageId']]==p['action'] for p in data['proposals'])
    receipts=[{**{k:p[k] for k in ('packageId','actionId','action')},'outcome':'ACCEPTED','receiptNonce':uuid.uuid4().hex} for p in data['proposals']]
    message={'messageId':uuid.uuid4().hex,'taskId':task['id'],'contextId':task['contextId'],'role':'ROLE_USER','parts':[{'mediaType':'application/vnd.ga5.invoice-action-results+json','data':{'batchId':data['batchId'],'results':receipts}}]}
    finished=a2a(client.post('/a2a/message:send',json={'message':message},headers=headers),'batch receipt '+str(n))['task']
    assert finished['status']['state']=='TASK_STATE_COMPLETED'
    count+=len(receipts)
report={'mediaType':'application/a2a+json','verifiedSuccessfulResponses':len(verified),'routesAndLifecycles':verified,'correctSyntheticActions':count,'completedCoreBatches':5,'completedAuditBatches':1,'seconds':round(time.monotonic()-started,2),'publicBase':base}
Path('/tmp/ga3-invoice-validation.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
