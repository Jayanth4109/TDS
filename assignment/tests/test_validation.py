import pytest

from agents import mailroom, incident, invoice


@pytest.mark.parametrize("bad", [None, {}, [], False, 1, "unknown"])
def test_mailroom_bad_operations_are_client_errors(client, monkeypatch, bad):
    monkeypatch.setattr(mailroom, "infer", lambda *a, **k: pytest.fail("Invalid request reached model"))
    r = client.post("/v1/mailroom/actions", json={"profile": mailroom.PROFILE, "operation": bad, "evaluationId": "e1"})
    assert r.status_code in {400, 422}


def test_sensitive_validation_errors_do_not_echo_input(client):
    marker = "SECRET_VALIDATION_MARKER"
    for path in ["/v1/mailroom/actions", "/v2/incidents"]:
        r = client.post(path, json=[{"private": marker}])
        assert r.status_code == 422
        assert marker not in r.text


def test_nonfinite_json_rejected(client):
    r = client.post("/v2/incidents", content='{"private":NaN}', headers={"Content-Type": "application/json"})
    assert r.status_code == 400


def test_body_limit(client):
    r = client.post("/v1/mailroom/actions", content=b"x"*(4*1024*1024+1))
    assert r.status_code == 413
