import json

import httpx
import pytest

from pulse_fhir.delivery import FhirClient, Outbox, Rejected, Unavailable

OK = {"resourceType": "Bundle", "type": "transaction-response", "entry": []}
OUTCOME = {"resourceType": "OperationOutcome", "issue": [{"severity": "error", "code": "invalid"}]}
no_sleep = {"sleep": lambda s: None}


def client(responses, seen=None):
    """A FhirClient whose server replies with `responses` in order."""
    it = iter(responses)

    def handler(request: httpx.Request):
        if seen is not None:
            seen.append(request)
        r = next(it)
        if isinstance(r, Exception):
            raise r
        return r

    return FhirClient("https://fhir.example/r4", token="t0ken", transport=httpx.MockTransport(handler))


def bundle(n):
    return {"resourceType": "Bundle", "type": "transaction", "entry": [], "id": str(n)}


# @verifies SRS-014
def test_posts_fhir_json_with_bearer_token():
    seen = []
    client([httpx.Response(200, json=OK)], seen).transaction(bundle(1), **no_sleep)
    (req,) = seen
    assert req.method == "POST" and str(req.url) == "https://fhir.example/r4"
    assert req.headers["content-type"] == "application/fhir+json"
    assert req.headers["authorization"] == "Bearer t0ken"


# @verifies SRS-011
def test_retries_transient_failures_then_succeeds():
    c = client([httpx.ConnectError("down"), httpx.Response(503), httpx.Response(200, json=OK)])
    assert c.transaction(bundle(1), **no_sleep) == OK


# @verifies SRS-011
def test_honours_retry_after():
    slept = []
    c = client([httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200, json=OK)])
    c.transaction(bundle(1), sleep=slept.append)
    assert slept == [7]


# @verifies SRS-011
def test_gives_up_after_the_attempt_budget():
    with pytest.raises(Unavailable):
        client([httpx.Response(503)] * 4).transaction(bundle(1), **no_sleep)


# @verifies SRS-013
def test_does_not_retry_a_bundle_the_server_rejected():
    with pytest.raises(Rejected) as e:
        client([httpx.Response(422, json=OUTCOME)]).transaction(bundle(1), **no_sleep)
    assert e.value.status == 422


# @verifies SRS-012
def test_outbox_keeps_everything_while_the_server_is_down(tmp_path):
    box = Outbox(str(tmp_path / "o.db"))
    for i in range(3):
        box.put(bundle(i))
    r = box.flush(client([httpx.ConnectError("down")] * 4), **no_sleep)
    assert (r.sent, r.pending) == (0, 3) and r.stopped_on


# @verifies SRS-012
def test_outbox_sends_in_order_once_the_server_is_back(tmp_path):
    box = Outbox(str(tmp_path / "o.db"))
    for i in range(3):
        box.put(bundle(i))
    seen = []
    r = box.flush(client([httpx.Response(200, json=OK)] * 3, seen), **no_sleep)
    assert (r.sent, r.pending) == (3, 0)
    assert [json.loads(q.content)["id"] for q in seen] == ["0", "1", "2"]


# @verifies SRS-013
def test_a_rejected_bundle_goes_to_dead_letter_and_does_not_block_the_queue(tmp_path):
    box = Outbox(str(tmp_path / "o.db"))
    for i in range(3):
        box.put(bundle(i))
    responses = [httpx.Response(200, json=OK), httpx.Response(400, json=OUTCOME), httpx.Response(200, json=OK)]
    r = box.flush(client(responses), **no_sleep)
    assert (r.sent, r.rejected, r.pending, box.dead()) == (2, 1, 0, 1)
    status, response = box.db.execute("SELECT status, response FROM dead_letter").fetchone()
    assert status == 400 and "OperationOutcome" in response


# @verifies SRS-012
def test_outbox_survives_a_restart(tmp_path):
    path = str(tmp_path / "o.db")
    box = Outbox(path)
    box.put(bundle(1))
    box.close()
    assert Outbox(path).pending() == 1
