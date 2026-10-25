import base64
import copy

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agents import mailroom
from agents.common import canonical, digest


def request(key, eid="evaluation-one"):
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return {"profile": mailroom.PROFILE, "operation": "propose", "evaluationId": eid,
            "receiptVerifier": {"algorithm": "Ed25519", "publicKeyJwk": {"kty": "OKP", "crv": "Ed25519", "x": base64.urlsafe_b64encode(public).decode().rstrip("=")}},
            "corpus": {"coreId": "core", "auditId": "audit", "stableCount": 1, "freshCount": 0},
            "allowedActions": list(mailroom.TYPES), "dossiers": [{"dossierId": "d1", "partition": "stable_core", "receivedAt": "2026-10-25T00:00:00Z", "mailbox": "support", "objective": "Avoid duplicate work",
                "sources": [{"sourceId": "s1", "kind": "record", "provenance": "trusted_internal", "title": "State", "lines": [{"lineId": "l1", "text": "Reference R1 is already completed."}]}]}]}


def decision():
    return {"decisions": [{"dossierId": "d1", "action": "no_action", "target": None, "payload": {"reasonCode": "ALREADY_COMPLETED", "referenceId": "R1"}, "evidence": ["l1"]}]}


def commit(proposed, key, accepted=True):
    p = proposed["proposals"][0]
    receipt = {k: p[k] for k in ("dossierId", "callId", "action")}
    receipt.update(accepted=accepted, proposalDigest=mailroom.proposal_digest(p), receiptId="grader-nonce")
    signed = {"profile": mailroom.PROFILE, "evaluationId": proposed["evaluationId"], "inputDigest": proposed["inputDigest"], "receipt": receipt}
    receipt["receiptSignature"] = base64.b64encode(key.sign(canonical(signed).encode())).decode()
    return {"profile": mailroom.PROFILE, "operation": "commit", "evaluationId": proposed["evaluationId"], "inputDigest": proposed["inputDigest"], "receipts": [receipt]}


@pytest.mark.parametrize("accepted", [True, False])
def test_receipts_verified_and_cached(client, monkeypatch, accepted):
    calls = []
    def model(*a, **k):
        calls.append(a)
        return decision(), "fixture"
    monkeypatch.setattr(mailroom, "infer", model)
    key = Ed25519PrivateKey.generate()
    body = request(key)
    initial = client.post("/v1/mailroom/actions", json=body)
    assert initial.status_code == 200, initial.text
    proposed = initial.json()
    assert proposed["inputDigest"] == digest(body["dossiers"])
    assert client.post("/v1/mailroom/actions", json=body).json() == proposed
    second = client.post("/v1/mailroom/actions", json=request(Ed25519PrivateKey.generate(), "second")).json()
    assert second["proposals"] == proposed["proposals"] and len(calls) == 1
    receipt = commit(proposed, key, accepted)
    completed = client.post("/v1/mailroom/actions", json=receipt)
    assert completed.status_code == 200, completed.text
    assert completed.json()["outcomes"][0]["status"] == ("executed" if accepted else "rejected")
    assert client.post("/v1/mailroom/actions", json=receipt).json() == completed.json()
    assert client.post("/v1/mailroom/actions", json=body).json() == proposed
    body["dossiers"][0]["objective"] = "changed"
    assert client.post("/v1/mailroom/actions", json=body).status_code == 409


@pytest.mark.parametrize("change", ["signature", "key", "evaluation", "call", "accepted", "digest", "duplicate"])
def test_tampered_receipt_rejected_atomically(client, monkeypatch, change):
    monkeypatch.setattr(mailroom, "infer", lambda *a, **k: (decision(), "fixture"))
    key = Ed25519PrivateKey.generate()
    body = request(key)
    proposed = client.post("/v1/mailroom/actions", json=body).json()
    good = commit(proposed, key)
    bad = copy.deepcopy(good)
    if change == "signature": bad["receipts"][0]["receiptSignature"] = "eA=="
    elif change == "key": bad = commit(proposed, Ed25519PrivateKey.generate())
    elif change == "evaluation": bad["evaluationId"] = "other"
    elif change == "call": bad["receipts"][0]["callId"] = "wrong-call-id"
    elif change == "accepted": bad["receipts"][0]["accepted"] = False
    elif change == "digest": bad["receipts"][0]["proposalDigest"] = "0"*64
    elif change == "duplicate": bad["receipts"].append(bad["receipts"][0])
    assert client.post("/v1/mailroom/actions", json=bad).status_code in {409, 422}
    assert client.post("/v1/mailroom/actions", json=good).json()["outcomes"][0]["status"] == "executed"


def test_malformed_no_model(client, monkeypatch):
    monkeypatch.setattr(mailroom, "infer", lambda *a, **k: pytest.fail("Unexpected model call"))
    body = request(Ed25519PrivateKey.generate())
    body["dossiers"].append(body["dossiers"][0])
    assert client.post("/v1/mailroom/actions", json=body).status_code == 422
    assert client.post("/v1/mailroom/actions", json={"operation": "invalid", "profile": mailroom.PROFILE, "evaluationId": "x"}).status_code == 422
