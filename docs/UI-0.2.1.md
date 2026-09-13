# 0.2.1 — local review candidate

Graphite Russian Tk utility, not a new protocol. Character identity is prominent;
capture, upload permission and actual server ACK remain distinct. One primary
start/stop control; settings, local-only recording and destructive actions are
collapsed. Unavailable v2 controls are not mounted; its independent consent,
credentials and queue lifecycle remain intact and default off.

Queue presentation scans existing payloads read-only: matching character,
missing/`unknown` identity, and other characters. Matching is not a claim of
server acceptance or connectivity. No historical IDs or payloads are migrated.

`collector.version.VERSION` is 0.2.1. The unchanged manifest generator emits
schema 1 with the same executable asset name and exact hash/size/version fields
accepted by 0.2.0. Generate the real manifest only from the final Windows EXE.
No executable or fabricated release manifest is included in this local change.

## Verification

- `python -m unittest discover -s tests -v`
- `python -m tools.native_gui_smoke` (real display / Xvfb required)
- `python -m tools.gui_preview /tmp/sphol-v0.2.1-preview` (Pillow, display)

The preview drives actual Tk controls using explicitly synthetic identity/queue
fixtures, blocks networking, and checks idle, active, failed upload, update
success/error and settings disclosure. Update transport is mocked: this does
not prove a Windows binary replacement. Existing frozen-update workflow remains
the Windows gate. Windows EXE build/native acceptance and review are pending;
no push, tag, release or user-PC change is authorized here.

Design: graphite #202226, foreground #f0f1f2, muted #b8bec7; restrained green
primary action, native ttk/clam controls, Segoe UI on Windows / DejaVu Sans for
Linux previews. No neon, decorative telemetry or implicit collection consent.
