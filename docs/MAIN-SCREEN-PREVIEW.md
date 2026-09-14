# Main-screen review candidate — LOCAL ONLY

Base: `1da4fe8` on existing `ui/graphite-v0.2.1`; source version remains **0.2.1**.
This is a Tk implementation for approval, not a release or native Windows acceptance.
No live app, game machine, website, server, production queue or pairing has been used.

## Chosen design

Approved direction: compact Russian graphite utility, restrained accent, readable Cyrillic,
permanent bound identity separate from capture and network state. One start/stop primary
action; three counters; explicit combat-only upload scope; collapsed settings/diagnostics;
version and manual update in footer; generic `https://sphol.com` link only.
The design-system search was consulted; oversized landing-page typography and decorative
cards were rejected as inappropriate for this foreground utility.

## Evidence and semantics

- **Собрано**: new committed queue insertions since this application window opened,
  across all characters. Continues across stop/start; resets on application restart.
  Historical pending rows, duplicate puts, and failed commits do not increase it.
  The counter is incremented after the SQLite transaction, not from `Tailer.poll()`'s
  return value: a poll can commit some events and then raise `QueueFull`.
- **Подтверждено**: IDs acknowledged during this application run, after existing
  `Uploader.validate_ack` validation and durable pending deletion. Can include records
  collected in an earlier run, so it is not a percentage of the first counter.
  The v1 protocol has per-ID `accepted_ids` and rejected records; it does NOT supply a
  global server total or an outing total. Neither is fabricated here.
- **В очереди**: actual total on-disk pending records, including old/unattributed/other
  characters. Counts are not claimed to be sendable; diagnostics show the breakdown.
- Last confirmation age uses local monotonic receipt time of a **nonempty validated
  event ACK**, not a heartbeat, empty queue poll, token redemption, or cached binding.
  It is not a claim that the server is currently reachable.
- Four anonymous historical fixture records remain byte-for-byte unchanged through
  capture, validated ACK, offline retry and recovery. They are a separate warning,
  not an ongoing capture failure and not reassigned to CoolDoog.
- Network retry retains the existing transport backoff/API behavior and pending data.
  Read failure is a distinct capture state. Update button state and callback both
  prohibit updating during capture, local recording, pairing or worker activity.

DPAPI, updater executable application, transport protocol, parser scope, event IDs,
queue schema and hidden v2 lifecycle are unchanged. Existing separately consented
local-all-Gamelogs recording remains in settings; it never reads Chatlogs or uploads.

## Reproduce actual UI + tests offline

Linux developer prerequisites: Tk, Xvfb, Cyrillic-capable DejaVu Sans, Pillow for
screenshots only. These are not new collector runtime dependencies.

```sh
xvfb-run -a -s '-screen 0 1280x1600x24' /usr/bin/python3 -m unittest discover -s tests -q
xvfb-run -a -s '-screen 0 1280x1600x24' /usr/bin/python3 -m tools.native_gui_smoke
xvfb-run -a -s '-screen 0 1280x1600x24' /usr/bin/python3 -m tools.gui_preview /tmp/sphol-main-preview
```

`gui_preview` renders the actual ConnectedApp and explicitly labels every screenshot
as internal synthetic preview with networking blocked. It uses a temporary Gamelogs
folder and SQLite queues, actual parser and queue commits, actual Uploader ACK
validation with an in-process synthetic HTTP fixture, real button callbacks and worker
results. Both socket connect paths are blocked; browser opening is intercepted.
Scheduled callbacks are manually driven there for deterministic fixtures; the separate
native GUI smoke also exercises the normal timed Tk loop and durable reopen.

Screenshots: `idle`, `waiting`, `active`, `offline`, `legacyqueue`, `capture-error`,
`update`, `update-error`, `settings` (PNG). Update fixture checks only callback/worker
presentation; it does not download, install, replace or launch an executable.

## Remaining approval gates

- Parent/user visual review of these actual Tk images before any publish/release.
- Native frozen Windows rendering, DPI/accessibility/keyboard and interactive desktop
  acceptance are not established by Linux Xvfb. Expanded diagnostics grows the window;
  short Windows screens need explicit review (the screenshot display is 1600 px high).
- No version bump, push, CI dispatch, GitHub release, production change or live upload
  is authorized by this local preview step.
