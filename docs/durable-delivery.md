# Durable delivery candidate

The endpoint, schema 1/2 payloads, authentication, consent, and event-ID format are unchanged. This is at-least-once delivery, not a claim of exactly-once networking or protection against disk/host loss.

## Capture boundary

Start still snapshots existing Gamelogs at EOF. Before each active poll, SQLite stores the observed byte ceiling; complete events and their advanced cursors then commit in one transaction. A failed transaction restores in-memory cursors. At capacity a successfully inserted prefix and its cursor commit; the remainder is retained in the source, not evicted from the queue.

After a fault/process exit or normal Stop/close/update, a new explicit Start may replay only the last persisted observed envelope, using its original run ID, source identity, generation and offset. Stop retains already-observed but unparsed bytes through their persisted checkpoint; it does not discard that envelope. After replay, capture establishes the new EOF baseline. Bytes written beyond that ceiling while stopped are skipped. A line completed outside that envelope is not backfilled. Existing queued records survive either path. No background service or automatic capture is introduced.

Recovery requires the source still to exist with the matching filesystem identity and prefix fingerprint. Deleted or replaced source bytes cannot be recovered from cursor metadata. Atomicity covers queued events and their cursor advancement in SQLite, not arbitrary filesystem rewrites between observations. Writes after the last observed ceiling are outside the recovery promise. This is not a full private Gamelogs archive. File identity/truncation and UTF-8 behavior require native Windows acceptance in addition to Linux tests.

## Delivery boundary

GUI dispatch coalesces for a randomized 5–7.5 seconds by default (configurable interval 5–10), or flushes at 100 records / 262144 UTF-8 JSON bytes. Limits are configurable within protocol bounds. The gate never sleeps or performs network I/O; the existing worker owns HTTP. A nonblocking installation lock prevents simultaneous stream requests in one process. Stop prevents new dispatch; a request already started may finish.

Only a validated complete per-ID response can retire rows. An explicit valid rejection retains that row and pauses sending; malformed, incomplete, duplicate or foreign ACKs retire nothing. Worker-memory receipt is not logged as a durable local ACK: the GUI first commits queue deletion and retry metadata, then updates acknowledgement diagnostics/counters. Disk-write failure retains rows and disables sending with a visible local-storage warning.

Transient failures use bounded exponential jitter. Retry-After accepts seconds or an HTTP date, bounded to one day with at most five seconds additional jitter. SQLite stores failure count and wall-clock deadline keyed by installation/scope; restart translates the remaining bounded delay back to the monotonic clock. Credentials and pending IDs are not rewritten. GUI retry state becomes durable when the worker result is handled on the SQLite-owning thread; a process kill between receipt and that local commit may retry sooner, but cannot discard unacknowledged rows. Wall-clock changes across restarts can alter remaining wait, bounded to 86405 seconds.

## Local gates

Run `python -m unittest discover -s tests -v`. Linux Tk/Pillow gates require a compatible interpreter under Xvfb. `tests/test_durable_delivery.py` uses real PendingQueue/Uploader, temporary SQLite and synthetic loopback HTTP for lost ACK/replay and HTTP 429/503; additional fixtures cover recovery, malformed ACKs, retry persistence and old queue schema. No production upload is needed.

## Before any release

1. Independently review the exact committed candidate and integrate against the exact server candidate. Run the existing client/server gates with `SPHOL_SERVER_PATH`, and server gates with `SPHOL_CLIENT_PATH` pointing to this candidate.
2. The cross-version fixture pins the current stable v0.3.4 EXE SHA-256 `5185c8a9247f1324018e99cb9cf509a992b24de1332f1d5199a9a1ee31d2080b` and seeds queues with its released implementation. Require the exact new build to pass actual old-helper replacement/relaunch, exact pending tuple preservation, additive schema/integrity and released-reader rollback compatibility, credential-byte preservation and native DPAPI roundtrip. Keep downloads/EXE testing in isolated native CI with outbound collector egress blocked. This synthetic fixture does not prove live pilot use or the interactive old-parent shutdown path.
3. After publication authority is confirmed, push only the reviewed candidate ref and dispatch `.github/workflows/windows-build.yml` for that ref. Match run head SHA exactly. Require native unittest/DPAPI, Tk controls, frozen EXE smoke and actual old-to-new queue/binding preservation; retain reports and checksums. Linux synthetic credential sentinels are not DPAPI acceptance.
4. Require the separate sustained 100-client/server capacity and failure/restore gates. No throughput, four-hour stability, native upgrade or production deployment claim follows from these local tests.
