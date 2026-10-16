"""Write example resources to examples/ for the official HL7 FHIR validator.

python scripts/make_examples.py
java -jar validator_cli.jar examples/*.json -version 4.0.1
"""

import gzip
import json
from datetime import UTC, datetime
from pathlib import Path

from pulse_fhir.fhir import heart_rate_observation, transaction_bundle
from pulse_fhir.serial_format import parse_line
from pulse_fhir.windows import windows

ROOT = Path(__file__).resolve().parent.parent
with gzip.open(ROOT / "fixtures" / "dropout.txt.gz", "rt") as f:
    ws = list(windows((s for s in map(parse_line, f) if s), datetime(2026, 10, 16, 7, 0, tzinfo=UTC)))

out = ROOT / "examples"
out.mkdir(exist_ok=True)
common = {"subject": "Patient/example", "device_ref": "Device/example", "device_serial": "PM-001", "min_coverage": 0.8}
measured = next(w for w in ws if w.bpm is not None)
absent = next(w for w in ws if w.bpm is None)
files = {
    "observation-heart-rate.json": heart_rate_observation(measured, **common),
    "observation-heart-rate-absent.json": heart_rate_observation(absent, **common),
    "bundle-transaction.json": transaction_bundle(ws, subject="Patient/example", device_serial="PM-001"),
}
for name, res in files.items():
    (out / name).write_text(json.dumps(res, indent=2) + "\n")
    print("wrote", out / name)
