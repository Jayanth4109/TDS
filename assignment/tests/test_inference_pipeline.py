import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException

from agents import common, invoice, diagnostics


def test_inference_releases_database_writer(client, monkeypatch):
    import test_invoice
    started, release = threading.Event(), threading.Event()
    def model(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return test_invoice.decision(), "fixture"
    monkeypatch.setattr(invoice, "infer", model)
    with ThreadPoolExecutor(2) as pool:
        pending = pool.submit(client.post, "/a2a/message:send", json=test_invoice.message(), headers=test_invoice.HEADERS)
        try:
            assert started.wait(3)
            # This must complete while inference is blocked, not after a 60-second SQLite wait.
            read = pool.submit(client.get, "/a2a/tasks", headers=test_invoice.HEADERS)
            assert read.result(timeout=2).json() == {"tasks": []}
        finally:
            release.set()
        assert pending.result(timeout=3).status_code == 200


def test_partial_validated_cache_survives_other_batch_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_CACHE_DIR", str(tmp_path/'cache'))
    counts = {0: 0, 3: 0}
    first_validated = threading.Event()
    items = [{"id": i} for i in range(6)]
    def model(instruction, data, **kwargs):
        group = data['items']
        number = group[0]['id']
        counts[number] += 1
        if number == 3 and counts[number] == 1:
            assert first_validated.wait(3)
            raise HTTPException(503, "temporary provider failure")
        return {"decisions": [{"id": str(p['id']), "answer": "valid"} for p in group]}, "fixture"
    items = [{"id": str(i)} for i in range(6)]
    def model_fixed(instruction, data, **kwargs):
        numeric = [{"id": int(p['id'])} for p in data['items']]
        return model(instruction, {'items': numeric}, **kwargs)
    def validate(proposal, item):
        assert proposal['id'] == item['id'] and proposal['answer'] == 'valid'
        if item['id'] == '2':
            first_validated.set()
    with pytest.raises(HTTPException):
        common.infer_decisions('prompt', items, 'items', 'id', timeout=5, batch_size=3, validate=validate, model_call=model_fixed)
    result = common.infer_decisions('prompt', items, 'items', 'id', timeout=5, batch_size=3, validate=validate, model_call=model_fixed)
    assert len(result['decisions']) == 6
    assert counts == {0: 1, 3: 2}


@pytest.mark.parametrize('status,detail', [(402,'MODEL_PROVIDER_QUOTA_EXCEEDED'), (429,'MODEL_PROVIDER_QUOTA_EXCEEDED'), (401,'MODEL_PROVIDER_AUTH_REQUIRED'), (413,'MODEL_PROVIDER_INPUT_REJECTED')])
def test_provider_failures_are_classified(monkeypatch, status, detail):
    monkeypatch.setattr(httpx, 'post', lambda *a, **kw: httpx.Response(status, json={'error':'upstream failure'}, request=httpx.Request('POST','https://provider.example')))
    with pytest.raises(HTTPException) as failure:
        common.infer('prompt', {'safe':'synthetic'})
    assert failure.value.detail == detail


def test_diagnostics_are_encrypted(tmp_path, monkeypatch):
    import base64
    from cryptography.hazmat.primitives import serialization, hashes
    from cryptography.hazmat.primitives.asymmetric import rsa, padding
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = tmp_path/'public.pem'
    public.write_bytes(private.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo))
    monkeypatch.setenv('DIAGNOSTIC_PUBLIC_KEY_FILE',str(public))
    monkeypatch.setenv('DIAGNOSTIC_DIR',str(tmp_path/'encrypted'))
    diagnostics.emit('sample',{'private':'SYNTHETIC_PRIVATE_MARKER'})
    file = next((tmp_path/'encrypted').glob('*.json'))
    assert 'SYNTHETIC_PRIVATE_MARKER' not in file.read_text()
    envelope = json.loads(file.read_text())
    decode = lambda field: base64.b64decode(envelope[field])
    key = private.decrypt(decode('key'),padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),algorithm=hashes.SHA256(),label=None))
    recovered = json.loads(AESGCM(key).decrypt(decode('nonce'),decode('ciphertext'),b'tds-diagnostics-v1'))
    assert recovered['data']['private']=='SYNTHETIC_PRIVATE_MARKER'


def test_invoice_rationale_renders_selected_action():
    import test_invoice
    proposal = test_invoice.decision()['decisions'][0]
    proposal['rationale'] = 'The invoice is commercially valid and reconciled according to a and b, with delegated authority established by c.'
    invoice.validate_decision(proposal, test_invoice.message()['message']['parts'][0]['data']['packages'][0])
    assert proposal['rationale'].startswith('settle_invoice: ')


def test_mailroom_update_alias_only_if_value_is_unchanged():
    import test_mailroom
    from agents import mailroom
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    dossier = test_mailroom.request(Ed25519PrivateKey.generate())['dossiers'][0]
    proposal = {'dossierId':'d1', 'action':'update_internal_record', 'target':{'kind':'case_record','id':'CASE-1'}, 'payload':{'delivery_window':'tomorrow','sourceEventId':'EVENT-1','value':'tomorrow'}, 'evidence':['l1']}
    mailroom.validate_proposal(proposal, dossier)
    assert proposal['payload']=={'field':'delivery_window','sourceEventId':'EVENT-1','value':'tomorrow'}
    proposal['payload']={'delivery_window':'different','sourceEventId':'EVENT-1','value':'tomorrow'}
    with pytest.raises(HTTPException):
        mailroom.validate_proposal(proposal, dossier)


def test_display_brackets_resolve_only_to_known_ids():
    assert common.evidence_ids(['[ev_1]','ev_2','[unknown]'], {'ev_1','ev_2'}) == ['ev_1','ev_2','[unknown]']


def test_incident_bracketed_evidence_preserves_exact_ids(client, monkeypatch):
    import test_incident
    from agents import incident
    plan = test_incident.plan()
    plan['diagnosis']['evidence']=['[ev_1]','[ev_2]']
    plan['diagnostics'][0]['evidence']=['[ev_1]']
    plan['effect']['evidence']=['[ev_2]']
    monkeypatch.setattr(incident,'infer',lambda *a,**kw:(plan,'fixture'))
    response=client.post('/v2/incidents',json=test_incident.request())
    assert response.status_code==200
    assert response.json()['diagnosis']['evidence']==['ev_1','ev_2']
    assert response.json()['dispatches'][0]['evidence']==['ev_1']
