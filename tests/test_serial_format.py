import pytest

from pulse_fhir.serial_format import Sample, parse_line


# @verifies SRS-001
def test_parses_a_firmware_line():
    assert parse_line("452,500,723\n") == Sample(raw=452, beat_led=True, bpm=72.3)
    assert parse_line("452,0,0\r\n") == Sample(raw=452, beat_led=False, bpm=0.0)


@pytest.mark.parametrize(
    "line",
    [
        "",
        "452,500",
        "452,500,723,1",
        "abc,0,0",
        "45\x002,0,0",  # partial, extra field, garbage, corrupt byte
        "1024,0,700",
        "452,250,700",
        "452,0,-10",
        "452,0,4000",  # out of range for the firmware's format
    ],
)
# @verifies SRS-001
def test_rejects_what_the_firmware_never_sends(line):
    assert parse_line(line) is None
