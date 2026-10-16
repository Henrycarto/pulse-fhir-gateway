"""Build FHIR R4 resources as plain JSON-ready dicts.

Observations follow the FHIR R4 heart rate profile
(http://hl7.org/fhir/StructureDefinition/heartrate): LOINC 8867-4, category
vital-signs, UCUM "/min", and either a value or a dataAbsentReason (vs-2).

Every resource carries a deterministic identifier and is sent with a
conditional create (`ifNoneExist`). Sending the same window twice, after a
timeout or a crash, never creates a duplicate on the server.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from .windows import HeartRateWindow

HEART_RATE_PROFILE = "http://hl7.org/fhir/StructureDefinition/heartrate"
LOINC = "http://loinc.org"
UCUM = "http://unitsofmeasure.org"
OBS_CATEGORY = "http://terminology.hl7.org/CodeSystem/observation-category"
DATA_ABSENT = "http://terminology.hl7.org/CodeSystem/data-absent-reason"
URI_SYSTEM = "urn:ietf:rfc:3986"  # identifier value is itself a URI

DEFAULT_DEVICE_SYSTEM = "https://henrycarto.com/fhir/pulse-monitor-serial"
_NAMESPACE = uuid.UUID("6d1c3c0e-8f0a-5b5e-9a43-3b7f2f8e6a11")

# why a window has no value -> FHIR data-absent-reason code
_ABSENT = {
    "low-coverage": ("not-performed", "Not Performed", "Sensor gave a valid rate for {pct}% of the interval (minimum {min}%)."),
    "implausible": ("error", "Error", "Median rate outside the plausible range; treated as a measurement error."),
}


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def observation_uri(device_serial: str, window: HeartRateWindow) -> str:
    """Stable id for one device and one interval, the same on every retry."""
    return f"urn:uuid:{uuid.uuid5(_NAMESPACE, f'{device_serial}|{iso(window.start)}')}"


def device(device_serial: str, device_system: str = DEFAULT_DEVICE_SYSTEM) -> dict:
    return {
        "resourceType": "Device",
        "identifier": [{"system": device_system, "value": device_serial}],
        "status": "active",
        "manufacturer": "pulse-monitor-avr (open source)",
        "deviceName": [{"name": "Pulse monitor, ATmega328P, PPG finger sensor", "type": "model-name"}],
        "note": [{"text": "Prototype built for teaching. Not a medical device."}],
    }


def heart_rate_observation(
    window: HeartRateWindow,
    *,
    subject: str,
    device_ref: str,
    device_serial: str,
    min_coverage: float,
) -> dict:
    obs: dict = {
        "resourceType": "Observation",
        "meta": {"profile": [HEART_RATE_PROFILE]},
        "identifier": [{"system": URI_SYSTEM, "value": observation_uri(device_serial, window)}],
        "status": "final",
        "category": [
            {
                "coding": [{"system": OBS_CATEGORY, "code": "vital-signs", "display": "Vital Signs"}],
                "text": "Vital Signs",
            }
        ],
        "code": {
            "coding": [{"system": LOINC, "code": "8867-4", "display": "Heart rate"}],
            "text": "Heart rate",
        },
        "subject": {"reference": subject},
        "effectivePeriod": {"start": iso(window.start), "end": iso(window.end)},
        "device": {"reference": device_ref},
        "method": {"text": "Photoplethysmography; median of the device's beat-interval rate over the period"},
    }
    if window.bpm is not None:
        obs["valueQuantity"] = {"value": window.bpm, "unit": "beats/minute", "system": UCUM, "code": "/min"}
    else:
        code, display, text = _ABSENT[window.absent_reason or "low-coverage"]
        obs["dataAbsentReason"] = {
            "coding": [{"system": DATA_ABSENT, "code": code, "display": display}],
            "text": text.format(pct=round(window.coverage * 100), min=round(min_coverage * 100)),
        }
    return obs


def transaction_bundle(
    windows: list[HeartRateWindow],
    *,
    subject: str,
    device_serial: str,
    device_system: str = DEFAULT_DEVICE_SYSTEM,
    min_coverage: float = 0.8,
) -> dict:
    """One transaction: the Device (created once) plus one Observation per window.

    Observations reference the Device by its in-bundle urn:uuid. If the Device
    already exists, the conditional create matches it and the server rewrites
    those references to the existing resource.
    """
    dev_full_url = f"urn:uuid:{uuid.uuid5(_NAMESPACE, f'device|{device_system}|{device_serial}')}"
    entries = [
        {
            "fullUrl": dev_full_url,
            "resource": device(device_serial, device_system),
            "request": {"method": "POST", "url": "Device", "ifNoneExist": f"identifier={device_system}|{device_serial}"},
        }
    ]
    for w in windows:
        obs = heart_rate_observation(
            w, subject=subject, device_ref=dev_full_url, device_serial=device_serial, min_coverage=min_coverage
        )
        uri = obs["identifier"][0]["value"]
        entries.append(
            {
                "fullUrl": uri,
                "resource": obs,
                "request": {"method": "POST", "url": "Observation", "ifNoneExist": f"identifier={URI_SYSTEM}|{uri}"},
            }
        )
    return {"resourceType": "Bundle", "type": "transaction", "entry": entries}
