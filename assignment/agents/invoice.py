"""Durable, principal-scoped A2A invoice Tasks."""
import uuid
import re

from .common import canonical, digest, evidence_ids, get, infer, infer_decisions, inference_window, serialized, put, require, string, transaction, unique
from .diagnostics import emit

PREFIX = "application/vnd.ga5.invoice-"
BATCH, PROPOSALS, RESULTS, RECEIPTS = [PREFIX + x + "+json" for x in ("claim-batch", "action-proposals", "action-results", "action-receipts")]
ACTIONS = {"settle_invoice", "request_approval", "hold_invoice", "reject_duplicate", "open_exception"}
PROMPT = """Read each synthetic invoice package and decide its business action from the decisive current paragraph.
Return {"decisions":[{"packageId":...,"action":...,"facts":{"vendorName":...,"invoiceNumber":...,"amountMinor":integer,"currency":...},"evidenceRefs":[...],"rationale":...}]}.
Exactly one decision per package. Action must be settle_invoice (valid, reconciled and within autonomous authority),
request_approval (commercially valid but exceeds delegated authority), hold_invoice (verification must complete before payment),
reject_duplicate (SAME commercial invoice already paid), or open_exception (material records conflict).
Do not treat payment of a different invoice as a duplicate. Respect negation, current records and approval limits.
Choose exactly the THREE decisive bracketed references in the paragraph that establishes the action; exclude cover-sheet,
historical examples, training examples, action-word decoys and archives. Preserve exact reference strings.
Use current invoice identity, amount in minor units, and currency. Rationale must be a concise 60–250 characters, name the selected
action with its EXACT snake_case identifier and explicitly cite at least two selected references.
Example rationale: "settle_invoice: [record_1] confirms reconciliation and [record_2] establishes autonomous authority."
Do not infer permission from a proposal or settle an uncertain invoice."""


def card(base):
    return {"name": "Evidence Invoice Agent", "description": "Receipt-gated invoice decisions with durable A2A Tasks", "version": "1.0.0",
            "capabilities": {}, "supportedInterfaces": [{"url": base, "protocolBinding": "HTTP+JSON", "protocolVersion": "1.0"}],
            "defaultInputModes": [BATCH], "defaultOutputModes": [PROPOSALS, RECEIPTS],
            "skills": [{"id": "invoice_action_agent", "name": "Invoice action agent", "description": "Decide invoice actions from current evidence", "tags": ["invoice", "evidence"]}]}


def artifact(media, data):
    return {"artifactId": str(uuid.uuid4()), "parts": [{"mediaType": media, "data": data}]}


def owned(db, principal, tid):
    task = get(db, "invoice-tasks", tid)
    require(task is not None and task["owner"] == principal, 404, "Not found")
    return task


def validate_decision(p, package):
    # This renders the model's selected action identifier; it does not alter its decision.
    if string(p.get("action")) and string(p.get("rationale")) and p["action"] not in p["rationale"]:
        p["rationale"] = p["action"] + ": " + p["rationale"]
    facts, refs, rationale = p.get("facts"), p.get("evidenceRefs"), p.get("rationale")
    refs = evidence_ids(refs, set(re.findall(r"\[([^\]\r\n]+)\]", canonical(package))))
    p["evidenceRefs"] = refs
    require(isinstance(p.get("action"), str) and p["action"] in ACTIONS and isinstance(facts, dict) and set(facts) == {"vendorName", "invoiceNumber", "amountMinor", "currency"}, 502, "Invalid model decision")
    require(all(string(facts[k]) for k in ("vendorName", "invoiceNumber", "currency")) and type(facts["amountMinor"]) is int and facts["amountMinor"] >= 0, 502)
    require(isinstance(refs, list) and len(refs) == 3 and all(string(r) for r in refs) and unique(refs), 502)
    require(all(ref in canonical(package) for ref in refs), 502, "Unknown model evidence")
    require(isinstance(rationale, str) and 60 <= len(rationale) <= 1500 and p["action"] in rationale and sum(ref in rationale for ref in refs) >= 2, 502, "Invalid rationale")


@serialized("invoice-message", lambda principal, body: digest([principal, body["message"].get("messageId") if isinstance(body.get("message"), dict) else body]))
def send(principal, body):
    message = body.get("message")
    require(isinstance(message, dict) and string(message.get("messageId")) and message.get("role") == "ROLE_USER")
    parts = message.get("parts")
    require(isinstance(parts, list) and len(parts) == 1 and isinstance(parts[0], dict))
    part = parts[0]
    require(string(part.get("mediaType")) and part["mediaType"] in {BATCH, RESULTS} and isinstance(part.get("data"), dict))
    data = part["data"]
    require(string(data.get("batchId")))
    message_key = digest([principal, message["messageId"]])
    with transaction() as db:
        replay = get(db, "invoice-messages", message_key)
        if replay:
            require(replay["digest"] == digest(message), 409, "IDEMPOTENCY_CONFLICT")
            return {"task": replay["task"]}
        if part["mediaType"] == BATCH:
            require(not message.get("taskId"))
            packages = data.get("packages")
            require(string(data.get("policyRevision")) and isinstance(packages, list) and 0 < len(packages) <= 100)
            require(all(isinstance(p, dict) and string(p.get("packageId")) for p in packages))
            require(unique([p["packageId"] for p in packages]))
            # The policy revision belongs to the semantic cache, independent of batch/task namespaces.
            def cache_key(p):
                return digest({"policyRevision": data["policyRevision"], "package": p})
            missing = [p for p in packages if not get(db, "invoice-decisions", cache_key(p))]
            if missing:
                emit("invoice_input", {"policyRevision": data["policyRevision"], "packages": packages})
                with inference_window(db):
                    result = infer_decisions(PROMPT, missing, "packages", "packageId", timeout=38, batch_size=3, validate=validate_decision, extra={"policyRevision": data["policyRevision"]}, model_call=infer)
                decisions = result.get("decisions")
                require(isinstance(decisions, list) and len(decisions) == len(missing) and all(isinstance(p, dict) for p in decisions), 502, "Invalid model batch")
                require(unique([p.get("packageId") for p in decisions]), 502)
                by_id = {p.get("packageId"): p for p in decisions}
                for package in missing:
                    p = by_id.get(package["packageId"], {})
                    validate_decision(p, package)
                    put(db, "invoice-decisions", cache_key(package), {k: p[k] for k in ("action", "facts", "evidenceRefs", "rationale")})
            proposals = [{"packageId": p["packageId"], "actionId": str(uuid.uuid4()), **get(db, "invoice-decisions", cache_key(p))} for p in packages]
            task = {"id": str(uuid.uuid4()), "contextId": message.get("contextId") or str(uuid.uuid4()),
                    "status": {"state": "TASK_STATE_INPUT_REQUIRED"}, "history": [message],
                    "artifacts": [artifact(PROPOSALS, {"batchId": data["batchId"], "proposals": proposals})]}
            record = {"owner": principal, "task": task}
        else:
            require(string(message.get("taskId")) and string(message.get("contextId")))
            record = owned(db, principal, message["taskId"])
            task = record["task"]
            require(task["status"]["state"] == "TASK_STATE_INPUT_REQUIRED", 409, "Task terminal")
            require(message["contextId"] == task["contextId"], 409, "Context conflict")
            original = task["artifacts"][0]["parts"][0]["data"]
            require(data["batchId"] == original["batchId"], 409, "Batch conflict")
            results = data.get("results")
            require(isinstance(results, list) and len(results) == len(original["proposals"]))
            require(all(isinstance(r, dict) and string(r.get("packageId")) for r in results))
            require(unique([r["packageId"] for r in results]))
            proposals = {p["packageId"]: p for p in original["proposals"]}
            executions = []
            for r in results:
                p = proposals.get(r["packageId"])
                require(p is not None and r.get("actionId") == p["actionId"] and r.get("action") == p["action"], 409, "Proposal conflict")
                require(r.get("outcome") in {"ACCEPTED", "REJECTED"} and string(r.get("receiptNonce")))
                if r["outcome"] == "ACCEPTED":
                    executions.append({**{k: p[k] for k in ("packageId", "actionId", "action", "facts", "evidenceRefs")}, "receiptNonce": r["receiptNonce"]})
            task["history"].append(message)
            task["artifacts"].append(artifact(RECEIPTS, {"batchId": data["batchId"], "executions": executions}))
            task["status"] = {"state": "TASK_STATE_COMPLETED"}
        put(db, "invoice-tasks", task["id"], record)
        put(db, "invoice-messages", message_key, {"digest": digest(message), "task": task})
        return {"task": task}


def read(principal, tid):
    with transaction() as db:
        return owned(db, principal, tid)["task"]


def listing(principal):
    with transaction() as db:
        import json
        records = [json.loads(r[0]) for r in db.execute("SELECT value FROM records WHERE namespace='invoice-tasks'")]
        return {"tasks": [r["task"] for r in records if r["owner"] == principal]}


def cancel(principal, tid):
    with transaction() as db:
        record = owned(db, principal, tid)
        task = record["task"]
        require(task["status"]["state"] == "TASK_STATE_INPUT_REQUIRED", 409, "Task terminal")
        task["status"] = {"state": "TASK_STATE_CANCELED"}
        put(db, "invoice-tasks", tid, record)
        return task
