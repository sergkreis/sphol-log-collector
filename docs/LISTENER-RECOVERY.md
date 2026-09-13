# Listener repair and capture delivery

Listener extraction accepts indented UTF-8 English/Russian headers (including BOM and CRLF), rejects invalid/partial identities, conflicting identities and event-body lookalikes. Header scanning is bounded to 256 lines of at most 8192 bytes. Unknown identity pauses that file visibly without advancing its cursor; other characters continue independently. Existing files still start at EOF. Original logs are read-only.

Browser pairing authorizes delivery for the paired character. **Start capture** now enables capture and upload; launch alone enables neither. **Stop capture and upload** stops collection and new network requests, preserving the queue. An already in-flight request may finish. Only server ACK changes the last-confirmed indicator or deletes queued events. Full local recording remains separately opted-in, local-only, never included in uploads.

## Offline recovery

Close the collector before applying. Never run apply on a queue used by a running app. First run a dry-run on a private consistent queue copy:

```
python -m tools.recover_listeners --queue pending-copy.sqlite3 --logs /path/to/Gamelogs
```

Only explicit empty-string listeners are candidates. Matching compares all stored normalized record fields exactly against parsed source records. The legacy queue does not preserve raw bytes or reversible source positions, so byte-exact provenance cannot be reconstructed. More than one source occurrence (even the same character), a missing/invalid header, or no exact normalized record match is **not repaired**. No bound-character defaults, fuzzy matches or timestamps-only assignments are allowed. Output contains counts only.

After closing the app, apply with an explicit new backup filename:

```
python -m tools.recover_listeners --queue pending.sqlite3 --logs /path/to/Gamelogs --apply --collector-closed --backup pending-before-repair.sqlite3
```

The tool requires an exclusive SQLite transaction, creates and integrity-checks a complete logical pre-change backup, updates payload/byte-size only, preserves IDs and all attributed rows, and commits atomically. `--collector-closed` is an operator assertion, not process detection: an idle app may hold no SQLite lock. Recovery is never invoked automatically by the collector. Inspect counts, retain the backup, then launch the fixed collector and explicitly Start capture. This tool has no network transport and does not upload anything.
