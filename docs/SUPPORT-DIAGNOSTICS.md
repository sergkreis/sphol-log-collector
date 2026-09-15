# 0.3.4 candidate: local support diagnostics

The collector always keeps a bounded technical history in
`%LOCALAPPDATA%\SPHOLLogCollector\diagnostics\history.json` across launches.
This does not enable capture, upload, clipboard access, or a background process.
The visible Russian **«Сохранить отчёт для поддержки»** button opens a Save As
dialog with a dated `SPHOL-support-*.json` filename. Send that one file to support
in Telegram yourself. Cancellation does nothing; failure leaves existing files
intact and shows a fixed Russian message. The report must be outside collector
state and use `.json`; queues, credentials and `.txt` Gamelogs cannot be selected.

## Exact privacy boundary

Only allowlisted event identifiers, outcomes, error categories, known exception
class identifiers, bounded integers and a numeric application version are stored.
Event identifiers name fixed code operations, not dynamically discovered stack
frames. The report contains UTC Unix timestamps, application version and numeric
Windows major/minor/build (no computer/user name or platform description).

Never recorded/exported: tokens, verifier/device secrets, pairing codes/URLs,
HTTP bodies/headers, event IDs, raw Gamelogs, character names, filesystem paths,
exception messages/args, arbitrary class names or tracebacks. Persisted entries
are revalidated on loading and again at export; adding an unknown field at a call
site or into the history file cannot add free-form text to a support report.
The report is not a ZIP and never enumerates or copies state/game files.

Technical coverage:
- app startup/closure, credential load/save and DPAPI error numbers;
- pairing request/poll success, pending, errors, redemption success, browser
  false/exception/timeout, recovery wait and failure (no challenge identifiers);
- HTTP statuses, upload attempt counts, timeout/error, retry number/delay,
  accepted/rejected ACK counts; last nonempty ACK retained through history pruning;
- Gamelogs existence/directory access and bounded `.txt` entry count (513 means
  at least 513), capture start/stop, read failures and unattributed file count.

Error categories are actionable: `missing` (check Gamelogs location),
`not_directory`, `permission` (folder/Windows access), `sharing_violation`
(another program locked a file), `file_limit` (archive old Gamelogs or restart
capture), `unsafe_file`/`changed_file` (unsafe entry or replacement during open),
`disk_full`, `encoding`, `timeout`, `tls`, `network`, `protocol`, `queue_full`,
`os_error`, `internal`. Numeric errno/winerror/status distinguishes remaining
failures without including private error text. Missing listener attribution is
not classified as failed upload. No change to queue admission, cursor advancement,
credential redemption ownership, consent, transport payloads, retries or ACK deletion.
Directory enumeration now uses scandir instead of glob so inaccessible directories
raise rather than silently appearing empty.

## Storage and failure behavior

A thread lock serializes history updates. Rolling history retains at most 800
records and 256 KiB serialized bytes, pruning oldest records while retaining the
last nonempty ACK. Successful per-file opens/idle polling are not recorded.
Writes use exclusive private temporary files, flush/fsync and atomic replace;
there is no unbounded backup series. A write failure retains old history and
best-effort bounded in-memory records. Export indicates
`persistent_history_available: false` if local persistence failed. The history
file is not a mandatory prerequisite for capture/upload. Logging failures must
never replace the original application exception.

Reads reject special files, symlinks/reparse points, changed inode on open,
oversized files and (POSIX) nonprivate/wrong-owner history. Writes reject special
files, symlinks/reparse targets/ancestors and hard-linked targets. Atomic replace
does not truncate an existing destination if writing fails. Diagnostics are not
a security boundary against another process with full access to the same account.

## Gates

`tests/test_diagnostics.py` exercises secret-bearing exceptions/paths, disk
revalidation, restarts/pruning/last ACK, unavailable storage, atomic export errors,
link/FIFO refusal, read classifications, pairing errors, DPAPI failure and actual
Uploader retry/ACK behavior with synthetic transport. `tools/native_gui_smoke.py`
invokes the real Tk export button, verifies the save-dialog contract, cancellation,
error handling, unchanged clipboard/queue and exported privacy. That exact test
is also included by the existing frozen `--synthetic-smoke-report` entry point
and Windows CI gate; no real user data or sockets are allowed in that smoke.

Linux Tk/source verification is not native Windows or frozen-EXE acceptance.
The candidate must pass the existing Windows build/DPAPI/frozen smoke and
cross-version gates after review. No release/CI/publication is performed by this
implementation task.
