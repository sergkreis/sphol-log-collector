# v0.3.0 candidate — publication gate

This candidate remains a **draft** until compatible server deployment is verified. Do not mark it latest or stable before that gate. Public stable remains v0.2.1.

- Compact Russian main UI, separate combat/observation acknowledgements and queues.
- Observations allowlist: sanitized fleet warp, module range, target invulnerability only. No chat, routes, arbitrary raw notifications, commander/module names or distances are transmitted by this stream.
- Separate explicit `gamelogs:write` browser approval remains required. Saved pairing is not proof of connection or server support.
- Capture and sending remain off on startup. Existing pairing and queued events are preserved; unsafe historical expanded records remain local and cannot upload.
- Windows CI gates the frozen EXE, native DPAPI, updater replacement, and pinned released v0.2.1 → candidate relaunch with both protected credential stores and both queues preserved.
- CI acceptance is not interactive acceptance on a pilot's desktop. No live pilot machine is touched by this release preparation.
- Unsigned portable EXE; checksums identify bytes, not an independent safety endorsement.
