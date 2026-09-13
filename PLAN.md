# Historical stage 1 plan — local-only collector

Superseded for publication: the owner approved public client source and a manual
Windows candidate build. Website/private infrastructure and real logs remain excluded.
No GitHub Release until the native artifact is tested.

Scope: independent Python standard-library collector, SQLite bounded offline queue,
UTF-8 combat parser, foreground Tk Windows GUI, synthetic tests and privacy/protocol docs.
No website edits, network integration, public publishing, deployment, service or autostart.

1. Inspect existing website auth read-only and GitHub identity (done).
2. Implement `collector/core.py`, `collector/gui.py`, `run_collector.py`.
3. Exercise synthetic parser/tailer/queue tests using `python3 -m unittest discover -s tests -v`.
4. Review tracked content; test clean staged archive; create local commit only.

Acceptance: old content skipped, appended combat only, UTF-8 partial lines, multiple
files, rename/truncate, bounded durable queue with stable IDs and visible saturation;
GUI explicitly disconnected, start/stop and pending-close warning. Windows build/UI
execution must be separately reported if unavailable on Linux.

Risks: local queue is unencrypted combat information; game logs are unauthenticated.
Rollback: delete this new project and its user-created local queue only; no server changes.
Publication gate: parent reviews every file and proposed API before GitHub creation.
Release gate: native Windows execution/build, server integration, security review,
public GitHub source, Actions provenance/checksums. No executable release in this stage.
