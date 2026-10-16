# Design decisions

Each section is a choice I made, what I rejected, and why.

## Why windows, and why 30 seconds

The firmware emits a rate 200 times a second. Sending that as FHIR would mean 720,000
Observations per patient-hour, each a full JSON resource. No EHR flowsheet wants that, and no
clinician reads it. Vital signs in a record are values per interval.

30 s is short enough to show a real change (the exercise fixture's step from 62 to 112 BPM
appears in the right window) and long enough that a median is stable. It is a flag
(`--window`) because the right interval depends on the use: spot checks, ward monitoring and
remote monitoring want different ones.

Each Observation uses `effectivePeriod` (start and end), not `effectiveDateTime`. The value
describes an interval, and the record should say so.

## Why the median

The firmware already reports the median of its last five beat intervals. Over a window, the
gateway takes the median again. A mean would be pulled by the 123 BPM spike the firmware
emits for about a second and a half when the sensor reacquires the pulse. The median ignores it
(`test_median_ignores_the_spike_when_the_sensor_reacquires`).

## Why "absent" instead of a number

When the finger leaves the sensor the firmware reports 0. A window that is 58% zeros still
has a median of the remaining 42%, and that number would look perfectly normal in a chart.
It would also be a measurement of 12 seconds presented as 30.

So a window needs 80% valid samples to carry a value. Below that, FHIR's own mechanism is
used: `dataAbsentReason` with code `not-performed` and a text saying how much of the interval
was covered. Implausible medians (outside 25 to 250 BPM) get `error`. The R4 vital signs
profile requires exactly one of a value or an absent reason (invariant vs-2), and the tests
assert that.

## Why time comes from sample counts

USB serial delivers bytes in bursts. Timestamping each line on arrival would put jitter into
every window boundary. The firmware samples on a hardware timer at exactly 200 Hz, so sample
N happened at `start + N / 200` seconds. The gateway trusts the device's clock for spacing and
the host's clock only for the starting instant.

## Why conditional creates and deterministic identifiers

The hard part of sending data over a network is not sending it. It is knowing whether it
arrived. If a request times out, the server may or may not have stored it. Retrying blindly
creates duplicates. Not retrying loses data.

Every Observation carries an identifier derived from the device serial and the window start
(UUIDv5, so the same input always gives the same id). Each is sent as a conditional create:
`ifNoneExist: identifier=urn:ietf:rfc:3986|urn:uuid:...`. If the server already has it, it
returns the existing one instead of creating another. Retrying becomes safe, which is what
makes the outbox possible. The integration test proves it against HAPI FHIR by replaying the
same recording twice.

The Device is created the same way, conditionally on its serial number. Observations refer
to it by its in-bundle `urn:uuid`, and the server rewrites those references to the stored
Device whether it was just created or already existed.

## Why a narrative and a performer

The official HL7 validator passes the resources with two best-practice warnings: a resource
should carry a human-readable narrative (`dom-6`), and an Observation should say who
performed it. Both are now set. The narrative is one generated sentence ("Heart rate 71 /min,
07:00:00 to 07:00:30, device PM-001", or the absent reason), so a system that only shows
`text` still shows something true. The performer is the patient: the sensor is worn and the
reading is self-measured, and FHIR R4 does not allow a Device as a performer. The hardware
stays in `Observation.device`, where it belongs.

## Why a transaction bundle

All resources for a batch succeed or fail together. There is never an Observation pointing at
a Device that failed to save. One HTTP round trip per batch instead of one per resource.

## Why an outbox

The gateway runs next to a device. Networks there drop. The outbox is a SQLite table: a bundle
is written there first and deleted only after the server returns 2xx. On restart, anything
still in it is sent first, in order.

Failures split in two. A network error, 408, 429 or 5xx means "try later", so the flush stops
and keeps order. Any other 4xx means the bundle itself is wrong and will fail forever. It is
moved to a dead-letter table with the server's `OperationOutcome`, so a human can see why,
and the rest of the queue keeps moving.

## Why plain dicts instead of a FHIR model library

The resources are built as plain JSON-ready dicts. A model library would add a heavy runtime
dependency for three resource shapes. Validation happens where it is cheaper to trust: the
tests parse every resource with `fhir.resources`, and CI runs the official HL7 validator.
The runtime dependency is `httpx`, plus `pyserial` for live mode.

## Questions I would expect in an interview

**What happens if the gateway crashes mid-batch?** The bundle is in the outbox before it is
sent. On restart it is sent again. If the server had already stored it, the conditional
creates match and nothing is duplicated.

**Why not PUT with a client-assigned id instead of conditional create?** PUT to
`Observation/<id>` also gives idempotency, but it requires the server to allow client-assigned
ids, which many production servers disable. A conditional create on an identifier works
everywhere and keeps server ids under server control.

**How would this connect to a real hospital?** Behind an integration engine or straight to the
EHR's FHIR API, authenticated with SMART Backend Services, with the patient identified by the
hospital's MRN in a conditional reference. The device-to-patient association would come from
an admission or device-assignment workflow, not a command-line flag.

**What is missing for production?** SMART Backend Services auth, device-to-patient
association, clock drift correction for long sessions, and metrics on queue depth and
dead-letter count.
