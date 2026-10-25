"""Verify distinct incident causes, approval, retry, timeout, replay, and traces publicly."""
import json
import sys
import time
import uuid
from pathlib import Path
import httpx

root=Path(__file__).resolve().parents[2]/'assignment'
sys.path.insert(0,str(root))
from agents.incident import PROFILE

base=Path('/tmp/ga3-deployment/tds-public-url.txt').read_text().strip()
client=httpx.Client(base_url=base,timeout=20)
catalog=[{'name':name,'inputSchema':{'type':'object','required':fields}} for name,fields in {
 'query_logs':['service','query','windowMinutes'],'query_metrics':['service','metric','windowMinutes'],
 'inspect_deployment':['service'],'read_runbook':['service','topic'],'dependency_status':['dependency'],
 'rollback_deployment':['service','release'],'scale_service':['service','targetReplicas'],
 'disable_feature':['service','flag'],'page_owner':['service','severity'],'open_incident':['service','severity']}.items()]
roots=['deployment_regression','database_connection_exhaustion','dependency_certificate_expired','feature_flag_recursion','traffic_capacity_exhaustion','secret_rotation_mismatch']
facts=[
 ('deployment_regression','rollback_deployment', ['The current deployment bad-v2 immediately introduced malformed responses; stable-v1 was healthy.','A holdback on stable-v1 remains healthy under identical load. Roll back service to stable-v1 only after approval.']),
 ('database_connection_exhaustion','scale_service',['The database connection pool is saturated, requests wait 25 seconds, and logs show pool acquisition timeouts.','Ordinary CPU and traffic exclude demand growth. An approved recovery target of exactly 3 replicas provides independent pools.']),
 ('dependency_certificate_expired','open_incident',['Every request to dependency upstream-test fails with an expired leaf certificate. Other dependencies remain healthy.','The mandatory recovery runbook assigns renewal to the upstream owner and requires opening a linked severity-one incident for dependency upstream-test.']),
 ('feature_flag_recursion','disable_feature',['Only requests in flag sample-flag recurse until their depth guard fires. No deployment has changed.','A shadow request with sample-flag disabled succeeds. Disable that exact flag after approval; use bounded logs to confirm recursion.']),
 ('traffic_capacity_exhaustion','scale_service',['Request queue depth and latency grow with traffic while utilization is saturated. Releases and database waits are unchanged.','An added replica drains the queue; the authorized target is exactly 5 replicas.']),
 ('secret_rotation_mismatch','page_owner',['The service still uses credential version 8 after the vault promoted version 9 and revoked version 8. Authentication fails exactly at revocation.','The recovery runbook must be read before paging or changing credentials; its repository is unavailable.']),
 ('traffic_capacity_exhaustion','page_owner',['Traffic is saturating the service and queue depth is growing; no deployments or dependency failures occurred.','The service already runs at its maximum authorized 4 replicas. Scaling beyond 4 is forbidden. Page the owner with severity SEV-1.']),
]
noise='\n'.join(f'[ev_noise{i}] Archived unrelated maintenance for another service. This is not evidence of the current incident.' for i in range(160))
report=[];started=time.monotonic()
for index,(cause,effect,observations) in enumerate(facts):
 rid='q4-verify-'+uuid.uuid4().hex
 service='sample-service-'+str(index)
 transcript=noise+'\n'+'\n'.join('[ev_'+str(index)+'Fact'+str(i)+'] Current '+service+': '+text for i,text in enumerate(observations))
 body={'profile':PROFILE,'runId':rid,'agentName':'incident-response','publicMarker':'public-'+rid,'sensitive':{'accessToken':'PRIVATE-TEST-TOKEN','privateNote':'PRIVATE-TEST-NOTE'},'incident':{'incidentId':'sample-'+str(index),'title':'Service failure','service':service,'severity':'SEV-1','transcript':transcript,'allowedRootCauses':roots},'toolCatalog':catalog,'policy':{'maximumDiagnostics':3,'effectTools':['rollback_deployment','scale_service','disable_feature','page_owner','open_incident'],'approvalRequiredFor':['rollback_deployment','disable_feature'],'doNotExport':['sensitive','transcript']}}
 call_started=time.monotonic();response=client.post('/v2/incidents',json=body)
 assert response.status_code==200,(index,response.status_code,response.text[:200])
 state=response.json();assert state['diagnosis']['rootCause']==cause,(index,state['diagnosis'])
 assert time.monotonic()-call_started<18
 assert client.post('/v2/incidents',json=body).json()==state
 changed={**body,'publicMarker':'conflict'}
 assert client.post('/v2/incidents',json=changed).status_code==409
 did_retry=False;approved=False;step=0;issued=[]
 if index==5:assert any(d['toolName']=='read_runbook' for d in state['dispatches'])
 while state['status']=='waiting' and step<8:
  step+=1
  if state['dispatches']:
   issued.extend(state['dispatches']);outcomes=[]
   for d in state['dispatches']:
    if d['phase']=='effect':
     assert d['toolName']==effect,(index,d)
     if effect in {'rollback_deployment','disable_feature'}:assert approved and d.get('approvalNonce')
    status=200
    if index==2 and d['phase']=='diagnostic' and d['attempt']==1 and not did_retry:status=503;did_retry=True
    if index==5 and d['toolName']=='read_runbook':status=0
    o={k:d[k] for k in ('actionId','callId','attempt')};o.update(status=status,nonce=uuid.uuid4().hex)
    if status:o['resultClass']='diagnosis_confirmed' if d['phase']=='diagnostic' else 'effect_applied'
    else:o['errorType']='timeout'
    outcomes.append(o)
   receipt={'receiptId':uuid.uuid4().hex,'outcomes':outcomes}
  else:
   assert len(state['approvals'])==1
   receipt={'receiptId':uuid.uuid4().hex,'approvals':[{'approvalId':state['approvals'][0]['approvalId'],'decision':'approved','nonce':uuid.uuid4().hex}]};approved=True
  response=client.post('/v2/incidents/'+rid+'/receipts',json=receipt)
  assert response.status_code==200,(index,response.status_code,response.text[:200])
  state=response.json()
  assert client.post('/v2/incidents/'+rid+'/receipts',json=receipt).json()==state
 assert state['status']==('failed' if index==5 else 'completed'),(index,state)
 if index==5:assert not any(d['phase']=='effect' for d in issued)
 if index==2:assert any(d['attempt']==2 for d in issued)
 assert client.get('/v2/incidents/'+rid).json()==state
 assert 'PRIVATE-TEST-TOKEN' not in json.dumps(state) and 'PRIVATE-TEST-NOTE' not in json.dumps(state)
 spans=state['otlp']['resourceSpans'][0]['scopeSpans'][0]['spans']
 assert sum(s['name']=='chat incident-plan' for s in spans)==1
 assert all(any(s['spanId']==d['traceparent'].split('-')[2] for s in spans) for d in issued)
 report.append({'rootCause':cause,'expectedEffect':effect,'status':state['status'],'dispatches':len(issued),'approvalTested':approved,'retryTested':index==2,'timeoutSuppressionTested':index==5})
 print('Passed incident',index,cause,flush=True)
result={'publicBase':base,'passedCases':len(report),'seconds':round(time.monotonic()-started,2),'cases':report}
Path('/tmp/ga3-incident-validation.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
