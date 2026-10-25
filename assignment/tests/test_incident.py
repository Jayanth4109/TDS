import copy
import json

import pytest

from agents import incident
from agents.common import digest


def request(run="run-one", destructive=False, parallel=False):
    schema = {"type": "object", "properties": {"service": {"type": "string"}}, "required": ["service"], "additionalProperties": False}
    effect = "rollback_deployment" if destructive else "page_owner"
    return {"profile": incident.PROFILE, "runId": run, "agentName": "incident-response", "publicMarker": "safe-marker",
            "sensitive": {"accessToken": "PRIVATE_CANARY_DO_NOT_EXPORT", "privateNote": "SECRET_NOTE"},
            "incident": {"incidentId": "i1", "title": "capacity", "service": "web", "severity": "SEV-1",
                         "transcript": "[ev_1] CPU saturation\n[ev_2] Already at max replicas", "allowedRootCauses": ["capacity"]},
            "toolCatalog": [{"name": n, "inputSchema": schema} for n in ["query_metrics", "query_logs", effect]],
            "policy": {"maximumDiagnostics": 3, "effectTools": [effect], "approvalRequiredFor": ["rollback_deployment"], "doNotExport": []}}


def plan(destructive=False, parallel=False):
    call = lambda n: {"toolName": n, "arguments": {"service": "web"}, "evidence": ["ev_1"]}
    return {"diagnosis": {"rootCause": "capacity", "evidence": ["ev_1", "ev_2"]},
            "diagnostics": [call("query_metrics")] + ([call("query_logs")] if parallel else []),
            "effect": call("rollback_deployment" if destructive else "page_owner")}


def outcome(dispatch, status=200):
    value = {k: dispatch[k] for k in ("actionId", "callId", "attempt")}
    value.update(status=status, nonce="nonce-" + str(dispatch["attempt"]) + dispatch["actionId"])
    if status == 0:
        value["errorType"] = "timeout"
    else:
        value["resultClass"] = "diagnosis_confirmed" if dispatch["phase"] == "diagnostic" else "effect_applied"
    return value


def post_receipt(client, rid, receipt):
    return client.post(f"/v2/incidents/{rid}/receipts", json=receipt)


def test_real_lifecycle_and_trace(client, monkeypatch):
    calls = []
    def model(*args, **kwargs):
        calls.append(args[1])
        return plan(parallel=True), "fixture-model"
    monkeypatch.setattr(incident, "infer", model)
    body = request(parallel=True)
    incoming = "00-" + "a"*32 + "-" + "b"*16 + "-01"
    first = client.post("/v2/incidents", json=body, headers={"traceparent": incoming, "tracestate": "vendor=value"})
    assert first.status_code == 200, first.text
    assert len(first.json()["dispatches"]) == 2
    assert "sensitive" not in calls[0]
    r1 = {"receiptId": "r1", "outcomes": [outcome(d) for d in first.json()["dispatches"]]}
    effect = post_receipt(client, "run-one", r1)
    assert effect.status_code == 200, effect.text
    d = effect.json()["dispatches"][0]
    assert d["toolName"] == "page_owner"
    terminal = post_receipt(client, "run-one", {"receiptId": "r2", "outcomes": [outcome(d)]})
    assert terminal.status_code == 200 and terminal.json()["status"] == "completed"
    data = terminal.json()
    assert len(data["actionLog"]) == len(data["receiptLog"]) == 3
    spans = data["otlp"]["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert len({s["spanId"] for s in spans}) == len(spans)
    assert {s["traceId"] for s in spans} == {"a"*32}
    assert all(s["traceState"] == "vendor=value" for s in spans)
    assert sum(s["name"] == "chat incident-plan" for s in spans) == 1
    join = next(s for s in spans if s["name"] == "incident.join")
    assert len(join["links"]) == 2
    for action in data["actionLog"]:
        sid = action["traceparent"].split("-")[2]
        s = next(s for s in spans if s["spanId"] == sid)
        attrs = {a["key"]: a["value"] for a in s["attributes"]}
        assert attrs["ga5.action.id"]["stringValue"] == action["actionId"]
        assert "ga5.receipt.nonce" in attrs
    assert "PRIVATE_CANARY" not in terminal.text and "SECRET_NOTE" not in terminal.text
    assert "arguments" not in json.dumps(spans)
    assert client.get("/v2/incidents/run-one").json() == data
    assert client.post("/v2/incidents", json=body).json() == data
    assert post_receipt(client, "run-one", r1).json() == effect.json()
    assert client.get("/v2/incidents/run-one").json() == data
    assert len(calls) == 1
    body["publicMarker"] = "changed"
    assert client.post("/v2/incidents", json=body).status_code == 409
    r1["outcomes"][0]["nonce"] = "changed"
    assert post_receipt(client, "run-one", r1).status_code == 409


@pytest.mark.parametrize("destructive", [False, True])
def test_retry_then_timeout_suppresses_effect(client, monkeypatch, destructive):
    monkeypatch.setattr(incident, "infer", lambda *a, **k: (plan(destructive), "fixture-model"))
    first = client.post("/v2/incidents", json=request(destructive=destructive)).json()
    original = first["dispatches"][0]
    retry = post_receipt(client, "run-one", {"receiptId": "r1", "outcomes": [outcome(original, 503)]}).json()
    assert not retry["approvals"]
    d = retry["dispatches"][0]
    assert d["actionId"] == original["actionId"] and d["callId"] == original["callId"] and d["attempt"] == 2
    assert d["traceparent"] != original["traceparent"]
    final = post_receipt(client, "run-one", {"receiptId": "r2", "outcomes": [outcome(d, 0)]}).json()
    assert final["status"] == "failed" and len(final["actionLog"]) == 2 and final["suppressed"]
    spans = final["otlp"]["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert not any(s["name"] == "approval_gate" for s in spans)


def test_approval_bound_before_effect(client, monkeypatch):
    monkeypatch.setattr(incident, "infer", lambda *a, **k: (plan(True), "fixture-model"))
    first = client.post("/v2/incidents", json=request(destructive=True)).json()
    waiting = post_receipt(client, "run-one", {"receiptId": "r1", "outcomes": [outcome(first["dispatches"][0])]}).json()
    assert waiting["dispatches"] == [] and len(waiting["approvals"]) == 1
    approval = waiting["approvals"][0]
    assert approval["argumentsDigest"] == digest({"service": "web"})
    bad = {"receiptId": "r2", "approvals": [{"approvalId": "wrong", "decision": "approved", "nonce": "n2"}]}
    assert post_receipt(client, "run-one", bad).status_code == 422
    assert client.get("/v2/incidents/run-one").json() == waiting
    bad["approvals"][0]["approvalId"] = approval["approvalId"]
    ready = post_receipt(client, "run-one", bad).json()
    d = ready["dispatches"][0]
    assert d["actionId"] == approval["actionId"] and d["approvalNonce"] == "n2"
    final = post_receipt(client, "run-one", {"receiptId": "r3", "outcomes": [outcome(d)]}).json()
    assert final["status"] == "completed" and len(final["receiptLog"]) == 3


def test_invalid_batch_is_atomic(client, monkeypatch):
    monkeypatch.setattr(incident, "infer", lambda *a, **k: (plan(parallel=True), "fixture-model"))
    first = client.post("/v2/incidents", json=request(parallel=True)).json()
    values = [outcome(d) for d in first["dispatches"]]
    values[1]["callId"] = "wrong"
    assert post_receipt(client, "run-one", {"receiptId": "bad", "outcomes": values}).status_code == 409
    assert client.get("/v2/incidents/run-one").json() == first


def test_bad_profile_no_model_work(client, monkeypatch):
    monkeypatch.setattr(incident, "infer", lambda *a, **k: pytest.fail("Unexpected inference"))
    assert client.post("/v2/incidents", json={"profile": "wrong"}).status_code == 422
    assert client.get("/v2/incidents/missing").status_code == 404
