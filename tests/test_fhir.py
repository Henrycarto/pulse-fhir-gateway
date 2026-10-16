"""Resources must parse as FHIR and meet the R4 heart rate profile's rules.

fhir.resources checks structure and datatypes (its R4B models; Observation,
Device and Bundle did not change between R4 and R4B). The profile's fixed
values are asserted here. CI additionally runs the official HL7 validator
against examples/ (see .github/workflows/ci.yml).
"""

from fhir.resources.R4B.bundle import Bundle
from fhir.resources.R4B.observation import Observation

from conftest import START
from pulse_fhir.fhir import HEART_RATE_PROFILE, transaction_bundle
from pulse_fhir.windows import windows

SUBJECT = "Patient?identifier=https://example.org/mrn|12345"


def bundle_for(recording, serial="PM-001"):
    return transaction_bundle(list(windows(recording, START)), subject=SUBJECT, device_serial=serial)


def observations(bundle):
    return [e["resource"] for e in bundle["entry"] if e["resource"]["resourceType"] == "Observation"]


def test_bundle_and_every_resource_parse_as_fhir(recordings):
    b = bundle_for(recordings["dropout"])
    Bundle.model_validate(b)
    for o in observations(b):
        Observation.model_validate(o)


def test_heart_rate_profile_fixed_values(recordings):
    for o in observations(bundle_for(recordings["dropout"])):
        assert o["meta"]["profile"] == [HEART_RATE_PROFILE]
        assert {"system": "http://loinc.org", "code": "8867-4", "display": "Heart rate"} in o["code"]["coding"]
        assert o["category"][0]["coding"][0]["code"] == "vital-signs"
        assert o["subject"]["reference"] == SUBJECT
        assert "effectivePeriod" in o
        # vs-2: a value or a data absent reason, never both, never neither
        assert ("valueQuantity" in o) != ("dataAbsentReason" in o)
        if "valueQuantity" in o:
            q = o["valueQuantity"]
            assert (q["system"], q["code"]) == ("http://unitsofmeasure.org", "/min")
            assert isinstance(q["value"], int)


def test_sensor_off_window_carries_a_reason(recordings):
    absent = [o for o in observations(bundle_for(recordings["dropout"])) if "dataAbsentReason" in o]
    assert len(absent) == 2
    assert absent[0]["dataAbsentReason"]["coding"][0]["code"] == "not-performed"
    assert "42%" in absent[0]["dataAbsentReason"]["text"]


def test_identifiers_are_stable_across_rebuilds_and_unique_per_device(recordings):
    ids = lambda b: [o["identifier"][0]["value"] for o in observations(b)]  # noqa: E731
    first, again = ids(bundle_for(recordings["rest"])), ids(bundle_for(recordings["rest"]))
    other_device = ids(bundle_for(recordings["rest"], serial="PM-002"))
    assert first == again
    assert len(set(first)) == len(first)
    assert not set(first) & set(other_device)


def test_every_create_is_conditional_and_references_resolve_in_the_bundle(recordings):
    b = bundle_for(recordings["rest"])
    full_urls = [e["fullUrl"] for e in b["entry"]]
    assert len(set(full_urls)) == len(full_urls)
    device = b["entry"][0]
    assert device["request"]["ifNoneExist"] == "identifier=https://henrycarto.com/fhir/pulse-monitor-serial|PM-001"
    for e in b["entry"][1:]:
        assert e["request"]["ifNoneExist"].endswith(e["resource"]["identifier"][0]["value"])
        assert e["resource"]["device"]["reference"] == device["fullUrl"]
