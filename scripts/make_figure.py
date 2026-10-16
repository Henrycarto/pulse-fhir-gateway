"""docs/windows.png: the device's 200 Hz rate stream and what becomes FHIR. Needs matplotlib."""

import gzip
from datetime import UTC, datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from pulse_fhir.serial_format import parse_line  # noqa: E402
from pulse_fhir.windows import windows  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
with gzip.open(ROOT / "fixtures" / "dropout.txt.gz", "rt") as f:
    samples = [s for s in map(parse_line, f) if s]
t0 = datetime(2026, 10, 16, 7, 0, tzinfo=UTC)
ws = list(windows(samples, t0))

fig, ax = plt.subplots(figsize=(9, 3.2), dpi=150)
ax.plot(
    [i / 200 for i in range(len(samples))], [s.bpm for s in samples], lw=0.8, color="#9a9a9a", label="device BPM, 200 per second"
)
for w in ws:
    a, b = (w.start - t0).total_seconds(), (w.end - t0).total_seconds()
    if w.bpm is None:
        ax.axvspan(a, b, color="#c0392b", alpha=0.10, lw=0)
        ax.text((a + b) / 2, 20, "absent:\nsensor off", ha="center", va="bottom", fontsize=7, color="#c0392b")
    else:
        ax.hlines(w.bpm, a, b, color="#53057F", lw=2.5)
        ax.text((a + b) / 2, w.bpm + 6, f"{w.bpm}", ha="center", fontsize=8, color="#53057F")
ax.plot([], [], color="#53057F", lw=2.5, label="FHIR Observation, one per 30 s")
ax.axvspan(70, 110, ymin=0, ymax=0.03, color="#c0392b", alpha=0.6, label="finger off the sensor")
ax.set_xlim(0, 180)
ax.set_ylim(0, 135)
ax.set_xlabel("seconds")
ax.set_ylabel("beats per minute")
ax.spines[["top", "right"]].set_visible(False)
ax.legend(loc="lower left", fontsize=7, frameon=False, ncol=3)
fig.tight_layout()
fig.savefig(ROOT / "docs" / "windows.png")
print("wrote docs/windows.png")
