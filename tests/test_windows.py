from datetime import datetime, timedelta

import pytest

from conftest import START
from pulse_fhir.serial_format import Sample
from pulse_fhir.windows import windows


# @verifies SRS-002
def test_rest_recording_gives_six_steady_values(recordings):
    ws = list(windows(recordings["rest"], START))
    assert len(ws) == 6  # 180 s / 30 s
    assert all(70 <= w.bpm <= 73 for w in ws)
    assert ws[1].start - ws[0].start == timedelta(seconds=30)
    assert ws[0].end == ws[1].start


# @verifies SRS-002
def test_exercise_step_shows_up_in_the_right_window(recordings):
    bpm = [w.bpm for w in windows(recordings["exercise"], START)]
    assert all(58 <= b <= 66 for b in bpm[:3])  # 62 BPM before 90 s
    assert all(108 <= b <= 116 for b in bpm[3:])  # 112 BPM after


# @verifies SRS-003
def test_sensor_off_is_reported_as_absent_not_as_a_number(recordings):
    ws = list(windows(recordings["dropout"], START))  # finger off 70-110 s
    absent = [i for i, w in enumerate(ws) if w.bpm is None]
    assert absent == [2, 3]
    assert all(ws[i].absent_reason == "low-coverage" for i in absent)
    assert ws[4].bpm is not None  # recovers once the finger is back


# @verifies SRS-004
def test_implausible_median_is_absent():
    samples = [Sample(500, False, 300.0)] * (30 * 200)
    (w,) = windows(samples, START)
    assert w.bpm is None and w.absent_reason == "implausible"


# @verifies SRS-007
def test_trailing_partial_window_is_dropped():
    samples = [Sample(500, False, 70.0)] * (45 * 200)
    assert len(list(windows(samples, START))) == 1


# @verifies SRS-006
def test_naive_start_time_is_refused():
    with pytest.raises(ValueError):
        next(windows([Sample(500, False, 70.0)], datetime(2026, 1, 1)))


# @verifies SRS-005
def test_median_ignores_the_spike_when_the_sensor_reacquires():
    # firmware output after the finger returns in the dropout fixture: 123, then 103, then settles at 72-74
    burst = [Sample(500, False, 123.0)] * 284 + [Sample(500, False, 103.0)] * 328
    steady = [Sample(500, False, 72.0)] * (30 * 200 - len(burst))
    (w,) = windows(burst + steady, START)
    assert w.bpm == 72
