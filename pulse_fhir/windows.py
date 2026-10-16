"""Reduce the 200 Hz stream to one heart-rate value per window.

A chart full of 200 readings per second is not a vital sign. Clinicians and
EHRs expect a value per interval, so each window becomes one Observation:

* time comes from counting samples (the firmware samples at exactly 200 Hz),
  not from when bytes arrive over USB, which is bursty;
* the value is the median of the firmware's BPM over the window's valid
  samples;
* a window is only reported as a number when the sensor produced a valid rate
  for at least `min_coverage` of it and the median is physiologically
  plausible. Otherwise it is reported as absent, with the reason.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta

from .serial_format import SAMPLE_RATE_HZ, Sample

PLAUSIBLE_BPM = (25.0, 250.0)


@dataclass(frozen=True, slots=True)
class HeartRateWindow:
    start: datetime
    end: datetime
    bpm: int | None  # None when absent
    coverage: float  # share of samples with a valid rate, 0..1
    absent_reason: str | None  # why bpm is None: "low-coverage" or "implausible"


def summarise(start: datetime, end: datetime, bpms: list[float], min_coverage: float) -> HeartRateWindow:
    valid = [b for b in bpms if b > 0]
    coverage = len(valid) / len(bpms) if bpms else 0.0
    if coverage < min_coverage:
        return HeartRateWindow(start, end, None, coverage, "low-coverage")
    median = statistics.median(valid)
    if not (PLAUSIBLE_BPM[0] <= median <= PLAUSIBLE_BPM[1]):
        return HeartRateWindow(start, end, None, coverage, "implausible")
    return HeartRateWindow(start, end, round(median), coverage, None)


def windows(
    samples: Iterable[Sample],
    start: datetime,
    window_s: int = 30,
    min_coverage: float = 0.8,
    fs: int = SAMPLE_RATE_HZ,
) -> Iterator[HeartRateWindow]:
    """Yield one window per `window_s` seconds of samples.

    `start` must be timezone-aware: it is the instant of the first sample. A
    trailing partial window is dropped rather than reported on less data.
    """
    if start.tzinfo is None:
        raise ValueError("start must be timezone-aware")
    per_window = window_s * fs
    buf: list[float] = []
    index = 0
    for s in samples:
        buf.append(s.bpm)
        if len(buf) == per_window:
            w_start = start + timedelta(seconds=index * window_s)
            yield summarise(w_start, w_start + timedelta(seconds=window_s), buf, min_coverage)
            buf = []
            index += 1
