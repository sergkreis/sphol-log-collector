# SPHOL combat-log collector — stage 1 preview

**Not connected. No upload implementation. No Windows binary released yet.**
This independent client is intended for public GitHub source review before release.
Publication is mandatory for the eventual release, but is deliberately held for
owner review. The website remains a separate private project.

Manually open before a sortie, click **Start capture**, then Stop/close afterwards.
No administrator, installation, service, startup entry, tray or silent background
execution is required. Closing terminates the program; pending data stays on disk.

## Run / test

Python 3.11+ with Tk (included in the usual python.org Windows installer):

```powershell
python run_collector.py
python -m unittest discover -s tests -v
```

The GUI resolves the Windows Documents known folder and reads only its
`EVE/logs/Gamelogs/*.txt` files, non-recursively, read-only. Tests use temporary,
synthetic directories. The local queue is `%LOCALAPPDATA%/SPHOLLogCollector/pending.sqlite3`.
Delete pending in the GUI to remove queued records (not a forensic secure erase).

Only `(combat)` is accepted, including damage and tackle attempts. `notify`,
`None` (including jumps), chat and unknown types are excluded. UTF-8 English and
Russian listener headers are supported. Markup is stripped; combat text is not
interpreted as verified damage/tackle success. Pilot labels come from untrusted logs.
Multiple files, rename rotation, observed truncation, partial UTF-8 lines and
restart-persistent pending IDs are covered by tests. Files already present start at
EOF; new files are timestamp-filtered against Start (UTC). Logs have second
precision: events in the fractional starting second can be omitted rather than
backfilling history. Events while stopped are never backfilled. Captured pending
records survive restart. In-place rewrite that regrows beyond the old offset between
polls is not reliably detected; native Windows rotation tests are a release gate.

Queue: at most 10,000 events / 16 MiB serialized payload. SQLite overhead is extra;
no silent eviction. On full, the cursor stays before the blocked event. The GUI
shows paused/full and polls for space. Disk errors stop capture visibly. Directory
limit: 512 `.txt` files, 1,024 identities per capture; 256 lines/file/poll, 8 KiB/line.
Archive old logs if the directory limit is reached. Oversized lines are discarded.

## Windows portable build (not yet executed)

On a clean Windows runner, after reviewing and pinning dependencies:
`python -m PyInstaller --onefile --windowed --name SPHOLLogCollector run_collector.py`

PyInstaller's one-file executable extracts its runtime to a temporary directory.
Portable means no installation, not zero filesystem writes. Runtime needs no third-party
Python packages. A future reviewed Actions workflow must pin action commit SHAs and
build dependencies, run tests on Windows, build from the exact public tag, produce
SHA-256 checksums and artifact attestations, and publish a GitHub Release only after
approval. No workflow that uploads or publishes is enabled in this draft.
Open source, checksums and provenance improve inspectability; none guarantees safety.

See [privacy](docs/PRIVACY.md), [proposed protocol](docs/PROTOCOL.md), and [plan](PLAN.md).
