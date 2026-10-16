import gzip
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pulse_fhir.serial_format import parse_line

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
START = datetime(2026, 10, 16, 7, 0, tzinfo=UTC)


def load(name: str):
    with gzip.open(FIXTURES / f"{name}.txt.gz", "rt") as f:
        return [s for s in map(parse_line, f) if s is not None]


@pytest.fixture(scope="session")
def recordings():
    return {n: load(n) for n in ("rest", "exercise", "dropout")}
