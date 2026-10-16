"""Command line: replay a recording or read the device live, and post to a FHIR server.

  pulse-fhir replay fixtures/rest.txt.gz --start 2026-10-16T09:00:00+02:00 \\
      --subject "Patient?identifier=https://example.org/mrn|12345" \\
      --device-serial PM-001 --fhir http://localhost:8080/fhir

  pulse-fhir serial /dev/ttyACM0 --subject Patient/123 --device-serial PM-001 --fhir ...

Without --fhir, bundles are written as JSON to --out (a dry run).
"""

from __future__ import annotations

import argparse
import gzip
import itertools
import json
import os
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from .delivery import FhirClient, Outbox
from .fhir import DEFAULT_DEVICE_SYSTEM, transaction_bundle
from .serial_format import Sample, parse_line
from .windows import windows


def _lines_from_file(path: str) -> Iterator[str]:
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt") as f:
        yield from f


def _lines_from_serial(port: str, baud: int) -> Iterator[str]:
    try:
        import serial  # pyserial, optional extra
    except ImportError:
        sys.exit("serial mode needs pyserial: pip install 'pulse-fhir-gateway[serial]'")
    with serial.Serial(port, baud, timeout=2) as s:
        s.reset_input_buffer()
        while True:
            line = s.readline()
            if line:
                yield line.decode("ascii", errors="replace")


def _samples(lines: Iterator[str], stats: dict) -> Iterator[Sample]:
    for line in lines:
        s = parse_line(line)
        if s is None:
            stats["dropped"] += 1
            continue
        yield s


def _batches(it, n):
    it = iter(it)
    while batch := list(itertools.islice(it, n)):
        yield batch


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="pulse-fhir", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="mode", required=True)
    rp = sub.add_parser("replay", help="replay a recorded serial log (.txt or .txt.gz)")
    rp.add_argument("file")
    rp.add_argument("--start", required=True, help="ISO 8601 time of the first sample, with offset")
    sp = sub.add_parser("serial", help="read the device live")
    sp.add_argument("port")
    sp.add_argument("--baud", type=int, default=115200)
    for q in (rp, sp):
        q.add_argument("--subject", required=True, help='e.g. "Patient/123" or "Patient?identifier=system|value"')
        q.add_argument("--device-serial", required=True)
        q.add_argument("--device-system", default=DEFAULT_DEVICE_SYSTEM)
        q.add_argument("--window", type=int, default=30, help="seconds per Observation (default 30)")
        q.add_argument("--min-coverage", type=float, default=0.8)
        q.add_argument("--batch", type=int, default=10, help="Observations per transaction")
        q.add_argument("--fhir", help="FHIR base URL; omit for a dry run")
        q.add_argument("--outbox", default="outbox.sqlite3")
        q.add_argument("--out", default="out", help="dry-run output folder")
    a = p.parse_args(argv)

    if a.mode == "replay":
        start = datetime.fromisoformat(a.start)
        lines = _lines_from_file(a.file)
    else:
        start = datetime.now(UTC).replace(microsecond=0)
        lines = _lines_from_serial(a.port, a.baud)
        if a.batch > 1:
            print("live mode: sending each window as it completes (--batch 1)", file=sys.stderr)
            a.batch = 1

    stats = {"dropped": 0}
    ws = windows(_samples(lines, stats), start, window_s=a.window, min_coverage=a.min_coverage)
    client = FhirClient(a.fhir, token=os.environ.get("FHIR_TOKEN")) if a.fhir else None
    outbox = Outbox(a.outbox) if client else None
    n_obs = n_absent = 0
    for i, batch in enumerate(_batches(ws, a.batch)):
        n_obs += len(batch)
        n_absent += sum(w.bpm is None for w in batch)
        bundle = transaction_bundle(
            batch, subject=a.subject, device_serial=a.device_serial, device_system=a.device_system, min_coverage=a.min_coverage
        )
        if client:
            outbox.put(bundle)
            r = outbox.flush(client)
            if r.stopped_on:
                print(f"server unavailable ({r.stopped_on}); {r.pending} bundle(s) queued", file=sys.stderr)
        else:
            Path(a.out).mkdir(parents=True, exist_ok=True)
            Path(a.out, f"bundle_{i:04d}.json").write_text(json.dumps(bundle, indent=2))

    summary = {"observations": n_obs, "absent": n_absent, "dropped_lines": stats["dropped"]}
    if outbox:
        summary |= {"queued": outbox.pending(), "dead_letter": outbox.dead()}
    print(json.dumps(summary))
    return 1 if outbox and outbox.dead() else 0


if __name__ == "__main__":
    raise SystemExit(main())
