"""Parse the firmware's ASCII serial line: "<raw>,<led*500>,<bpm*10>".

The format is defined in pulse-monitor-avr, firmware/pulse_monitor/serial_format.h.
One line per sample at exactly 200 Hz. bpm is 0 while the detector has no
reliable rate (start-up, sensor off).
"""

from __future__ import annotations

from dataclasses import dataclass

SAMPLE_RATE_HZ = 200


@dataclass(frozen=True, slots=True)
class Sample:
    raw: int  # 10-bit ADC value, 0..1023
    beat_led: bool  # LED on, i.e. within 100 ms of a detected beat
    bpm: float  # 0.0 means "no reliable rate"


def parse_line(line: str) -> Sample | None:
    """Return a Sample, or None for anything that is not a complete, valid line.

    Serial links deliver partial lines at connect time and the odd corrupted
    byte. Those are dropped, not guessed at.
    """
    parts = line.strip().split(",")
    if len(parts) != 3:
        return None
    try:
        raw, led, bpm10 = (int(p) for p in parts)
    except ValueError:
        return None
    if not (0 <= raw <= 1023) or led not in (0, 500) or not (0 <= bpm10 <= 3000):
        return None
    return Sample(raw=raw, beat_led=led == 500, bpm=bpm10 / 10.0)
