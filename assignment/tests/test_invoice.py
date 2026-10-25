import copy
from concurrent.futures import ThreadPoolExecutor

import pytest

from agents import invoice

HEADERS = {"Authorization": "Bearer owner", "A2A-Version": "1.0", "Content-Type": "application/a2a+json"}
OUTSIDER = {**HEADERS, "Authorization": "Bearer outsider"}


def message(mid="m1"):
    return {"message": {"messageId": mid, "role": "ROLE_USER", "parts": [{"mediaType": invoice.BATCH, "data": {
        "batchId": "b1", "policyRevision": "p1", "packages": [{"packageId": "p1", "documents": "Current decisive paragraph: [a] Vendor Vendor, invoice I1, currency INR, payable amountMinor 100. [b] Goods received and ledger reconciled; no prior payment, no bank-change hold, no disputed records. [c] Autonomous authority is 10000 minor units and this invoice is fully within it. Settle this commercially valid invoice without further approval."}]}}]}, "configuration": {"returnImmediately": False}}


def decision():
    return {"decisions": [{"packageId": "p1", "action": "settle_invoice", "facts": {"vendorName": "Vendor", "invoiceNumber": "I1", "amountMinor": 100, "currency": "INR"}, "evidenceRefs": ["a", "b", "c"],
                          "rationale": "settle_invoice because reference a identifies the invoice, b verifies it, and c establishes delegated authority."}]}


def continuation(task, accepted=True):
    proposal = task["artifacts"][0]["parts"][0]["data"]["proposals"][0]
    return {"message": {"messageId": "result", "taskId": task["id"], "contextId": task["contextId"], "role": "ROLE_USER",
        "parts": [{"mediaType": invoice.RESULTS, "data": {"batchId": "b1", "results": [{**{k: proposal[k] for k in ("packageId", "actionId", "action")}, "outcome": "ACCEPTED" if accepted else "REJECTED", "receiptNonce": "nonce-from-grader"}]}}]}}


@pytest.mark.parametrize("accepted", [True, False])
def test_task_lifecycle_isolation_replay(client, monkeypatch, accepted):
    calls = []
    def model(*a, **k):
        calls.append(a)
        return decision(), "fixture"
    monkeypatch.setattr(invoice, "infer", model)
    body = message()
    first = client.post("/a2a/message:send", json=body, headers=HEADERS)
    assert first.status_code == 200, first.text
    task = first.json()["task"]
    assert task["status"]["state"] == "TASK_STATE_INPUT_REQUIRED" and task["history"] == [body["message"]]
    tid = task["id"]
    assert client.get(f"/a2a/tasks/{tid}", headers=OUTSIDER).status_code == 404
    assert client.get("/a2a/tasks", headers=OUTSIDER).json() == {"tasks": []}
    result = continuation(task, accepted)
    assert client.post("/a2a/message:send", json=result, headers=OUTSIDER).status_code == 404
    changed = copy.deepcopy(result)
    changed["message"]["contextId"] = "wrong"
    assert client.post("/a2a/message:send", json=changed, headers=HEADERS).status_code == 409
    final = client.post("/a2a/message:send", json=result, headers=HEADERS)
    assert final.status_code == 200, final.text
    final_task = final.json()["task"]
    assert final_task["status"]["state"] == "TASK_STATE_COMPLETED"
    executions = final_task["artifacts"][1]["parts"][0]["data"]["executions"]
    assert len(executions) == int(accepted)
    if accepted:
        assert executions[0]["receiptNonce"] == "nonce-from-grader"
    assert client.post("/a2a/message:send", json=result, headers=HEADERS).json() == final.json()
    body["configuration"]["returnImmediately"] = True
    assert client.post("/a2a/message:send", json=body, headers=HEADERS).json() == first.json()
    assert client.post(f"/a2a/tasks/{tid}:cancel", headers=HEADERS).status_code == 409
    assert client.get(f"/a2a/tasks/{tid}", headers=HEADERS).json() == final_task
    assert len(calls) == 1
    body["message"]["parts"][0]["data"]["policyRevision"] = "changed"
    conflict = client.post("/a2a/message:send", json=body, headers=HEADERS)
    assert conflict.status_code == 409 and "IDEMPOTENCY_CONFLICT" in conflict.text


def test_concurrent_duplicates_and_cancel_race(client, monkeypatch):
    calls = []
    def model(*a, **k):
        calls.append(a)
        return decision(), "fixture"
    monkeypatch.setattr(invoice, "infer", model)
    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(lambda _: client.post("/a2a/message:send", json=message(), headers=HEADERS), range(2)))
    assert all(r.status_code == 200 for r in responses)
    assert responses[0].json() == responses[1].json() and len(calls) == 1
    task = responses[0].json()["task"]
    with ThreadPoolExecutor(2) as pool:
        f1 = pool.submit(client.post, "/a2a/message:send", json=continuation(task), headers=HEADERS)
        f2 = pool.submit(client.post, f"/a2a/tasks/{task['id']}:cancel", headers=HEADERS)
        codes = sorted([f1.result().status_code, f2.result().status_code])
    assert codes == [200, 409]
    final = client.get(f"/a2a/tasks/{task['id']}", headers=HEADERS).json()
    assert final["status"]["state"] in {"TASK_STATE_COMPLETED", "TASK_STATE_CANCELED"}
    assert (len(final["artifacts"]) == 2) == (final["status"]["state"] == "TASK_STATE_COMPLETED")


def test_auth_version_media_and_card(client, monkeypatch):
    monkeypatch.setattr(invoice, "infer", lambda *a, **k: pytest.fail("Unexpected model call"))
    assert client.get("/.well-known/agent-card.json").status_code == 200
    assert client.get("/a2a/tasks").status_code == 401
    assert client.get("/a2a/tasks", headers={"Authorization": "Bearer owner"}).status_code == 400
    assert client.post("/a2a/message:send", json=message(), headers={**HEADERS, "Content-Type": "application/json"}).status_code == 415
    assert client.post("/a2a/message:send", json={}, headers=HEADERS).status_code == 422
