# Design: Assigning Measurements to Persons

**Date:** 2026-06-04
**Status:** Approved

## Problem

The pulse oximeter is a shared device. The BLE data carries no identity, so the
integration cannot know who is being measured. Measurements must be linked to a
Home Assistant person explicitly.

## Core principle

**A measurement only counts for a person when that person explicitly claims it.**
No automatic assignment, no default person. Unclaimed measurements enter no
one's history.

## Measurement detection

A *measurement* spans finger-on to finger-off:

- **Start:** first valid reading (SpO2 > 0) after connect
- **Collect:** all valid readings (SpO2, pulse rate, PI) kept in memory
- **End:** BLE disconnect (device powers off after finger removal), or 30 s
  without valid readings
- **Validity:** fewer than 5 valid readings → measurement discarded silently
  (noise, no notification)
- **Summary:** median SpO2 and median pulse rate as primary values (robust
  against unstable settling-in readings), plus min/max SpO2, min/max pulse,
  mean PI, duration, timestamp, reading count

Existing live device-level sensors remain unchanged.

## Assignment model

Measurement states: `unassigned` → `assigned to <person>` | `faulty`.

- Measurement ends → state is **unassigned**; no person sensors change
- Only the **latest** measurement is assignable/correctable; an unclaimed
  measurement is replaced when the next one arrives
- Claiming updates that person's sensors; reassigning reverts the previous
  owner's sensors to their prior values
- Marking **faulty** discards the measurement; it enters no one's history
- All state changes are possible via notification buttons, the dashboard
  select, or the service (full parity)

## Notification flow

On measurement end, every configured phone receives a personalized actionable
notification:

> "Meting 14:32 — SpO2 97%, pols 72. Voor jou?"
> `[Voor mij]` `[Niet voor mij]` `[Foutieve meting]`

Three buttons — exactly the Android maximum.

- Action IDs encode measurement ID + person, pipe-delimited because person
  entity IDs contain underscores (e.g. `PULSEOX|MINE|42|person.frank`), so
  the integration always knows who answered
- **Voor mij** → assign to that phone's person → `clear_notification` on all
  phones
- **Niet voor mij** → dismisses only on that phone; others can still claim
- **Foutieve meting** → discard measurement → clear on all phones
- **No response** → stays unassigned; assignable later via the dashboard
  select until the next measurement replaces it
- Fixed notification tag per device entry: a new measurement automatically
  replaces a stale open question on all phones; answers referencing an
  outdated measurement are ignored with a log line
- The integration listens for `mobile_app_notification_action` events itself —
  no blueprint or automation required

## Entities

**Per participating person** (chosen in options), grouped under a per-person
device ("Pulse Oximeter Frank"):

- `sensor.<person>_spo2`, `sensor.<person>_pulse_rate`,
  `sensor.<person>_perfusion_index` — values from the last claimed
  measurement; attributes: `measured_at`, `duration`, `spo2_min/max`,
  `pulse_min/max`, `readings`
- Values survive restarts (RestoreEntity); per-person history accumulates via
  the recorder

**General:**

- `select.pulse_oximeter_last_measurement` — assignment of the latest
  measurement; options: persons + "Niet toegewezen" + "Foutieve meting";
  changing it reassigns/unassigns/discards (dashboard fallback for everything
  the buttons do)

## Options flow

1. **Participants:** multi-select from existing HA persons
2. **Notifications:** per participant a notify target
   (`notify.mobile_app_*` dropdown), pre-filled via the person→device_tracker
   mapping HA already knows; "no notification" allowed per person; global
   on/off toggle

## Events & services (escape hatch)

For users who disable built-in notifications and build their own automations:

- Events: `pulse_oximeter_measurement_finished`,
  `pulse_oximeter_measurement_assigned`
- Service: `pulse_oximeter.assign_measurement` (person entity ID, `none` to
  unassign, or `faulty` to discard)

## Edge cases

- Person removed from HA → entities cleaned up on reload; option validated
- Notify service gone (phone replaced) → warning logged, rest keeps working
- Restart mid-measurement → that measurement is lost (acceptable); claimed
  sensor values restored
- Simultaneous answers → last write wins; measurement always ends up on
  exactly one name

## Testing

pytest with `pytest-homeassistant-custom-component`:

- Measurement aggregation (median/min/max, validity threshold)
- Assignment state machine (claim, reassign, unassign, faulty, stale answers)
- Notification payload construction (actions, tags, per-person targeting)
- `mobile_app_notification_action` handling
- Restore after restart

## User-facing language

User-visible strings use "meting" (measurement), not "sessie". NL and EN
translations ship with the integration.
