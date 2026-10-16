"""End to end against a real FHIR server (HAPI FHIR in CI).

docker run -p 8080:8080 hapiproject/hapi:latest
FHIR_BASE_URL=http://localhost:8080/fhir pytest -m integration
"""

import os
import uuid

import httpx
import pytest

from conftest import FIXTURES
from pulse_fhir.cli import main
from pulse_fhir.fhir import DEFAULT_DEVICE_SYSTEM

pytestmark = pytest.mark.integration
BASE = os.environ.get("FHIR_BASE_URL", "http://localhost:8080/fhir")
MRN = "https://example.org/mrn"


def count(http, query):
    return http.get(f"{BASE}/{query}&_summary=count").raise_for_status().json()["total"]


def test_replaying_twice_stores_each_window_exactly_once(tmp_path):
    run = uuid.uuid4().hex[:8]
    serial, mrn = f"IT-{run}", f"MRN-{run}"
    with httpx.Client(headers={"Content-Type": "application/fhir+json"}, timeout=30) as http:
        patient = {"resourceType": "Patient", "identifier": [{"system": MRN, "value": mrn}], "name": [{"family": "Test"}]}
        http.post(f"{BASE}/Patient", json=patient).raise_for_status()

        args = [
            "replay",
            str(FIXTURES / "dropout.txt.gz"),
            "--start",
            "2026-10-16T09:00:00+02:00",
            "--subject",
            f"Patient?identifier={MRN}|{mrn}",
            "--device-serial",
            serial,
            "--fhir",
            BASE,
            "--outbox",
            str(tmp_path / "outbox.db"),
        ]
        assert main(args) == 0
        assert main(args) == 0  # second delivery of the same data

        pid = http.get(f"{BASE}/Patient?identifier={MRN}|{mrn}").json()["entry"][0]["resource"]["id"]
        assert count(http, f"Observation?subject=Patient/{pid}") == 6
        assert count(http, f"Observation?subject=Patient/{pid}&code=http://loinc.org|8867-4&value-quantity=gt0") == 4
        assert count(http, f"Device?identifier={DEFAULT_DEVICE_SYSTEM}|{serial}") == 1

        obs = http.get(f"{BASE}/Observation?subject=Patient/{pid}&_count=10").json()["entry"]
        device_refs = {e["resource"]["device"]["reference"] for e in obs}
        assert len(device_refs) == 1 and device_refs.pop().startswith("Device/")
