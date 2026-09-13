# Privacy and review boundary

## Optional full local Gamelogs capture

The Russian toggle **Сохранять все игровые события локально** requires a separate
confirmation and defaults OFF on every launch. It immediately records only new
complete UTF-8 lines of every type from Gamelogs, never Chatlogs. Existing files
start at EOF; a pre-existing partial line is skipped. New/rotated files are read
from their beginning. The original game logs already remain on disk: previous
ESS test messages are not claimed lost, and are not automatically backfilled.

Records can contain sensitive game notifications, names, locations and links.
They are never passed to PendingQueue or the uploader; combat-only cloud behavior
and existing credentials/queue are unchanged. Local recording needs no pairing.
Files are private to the current Windows account (ACL inheritance removed), but
not encrypted against programs running as that user or administrators.

Storage: `%LOCALAPPDATA%\SPHOLLogCollector\local-captures\<session-id>\` contains
`events.jsonl` (original UTF-8 line, source filename, byte offset) and `session.json`
(start/update UTC, count, bytes, status, no-upload/no-backfill flags). The UI shows
the folder and count. Maximum 64 MiB event data/session and 128 MiB total with
metadata reserve; no automatic deletion or circular overwrite. Size, file-count,
overlong-line, invalid UTF-8 and I/O failures stop local recording visibly. Combat
collection/upload remains independent. Stop/exit closes local files; incomplete
lines remain in original Gamelogs. Restart requires new consent and starts at EOF.
An interrupted session may retain status `recording`; count can be recovered from
its JSONL. This is polling, not a guarantee against files deleted between polls.
Move old sessions manually if full; do not publish raw captures in issues.


This client uses fixed-origin verified HTTPS only after explicit pairing/upload consent.
It has no telemetry, crash reporting or updater. It stores its own scoped credential
with Windows DPAPI CurrentUser, without plaintext fallback.
It never reads Chatlogs, game memory, browser cookies, other applications' credentials or EVE passwords;
never injects into a process; never changes game logs. The only game input is the
non-recursive Documents/EVE/logs/Gamelogs directory. Small header reads identify
listeners; existing historical event bodies are not queued. The bounded header
read can encounter old lines in memory but does not persist or transmit them.

Stored fields: schema version, UTC event timestamp, combat type, listener label,
markup-stripped combat text, opaque SHA-256 event ID. Combat text can identify
pilots, corporations, ships, weapons and activities. It is still sensitive, not
anonymized. No full file paths, session headers or entire raw logs are queued.

Pending data stays in a local SQLite file in the Windows user's LocalAppData.
It is **not encrypted**; other programs acting as that user may read it. Unix mode
0600 is applied where supported; native Windows ACL/reparse-point hardening and
single-instance locking need verification before release. Do not run from a shared
untrusted account. No automatic expiry is implemented: data remains until explicitly
deleted or durably acknowledged by the server with an exact validated event-ID response. Backups/OS disk
snapshots may retain deleted content. No queue or real log is part of the repository.

Public content consists only of client code, tests with invented names, and these
documents. Never submit actual logs, queued records, access tokens, screenshots
containing private combat information, or private website configuration in issues.

Upload requires explicit browser pairing and consent plus Enable uploads; collection alone is
not proof of character ownership. Server retention, member visibility, deletion and
revocation policy must be approved before enabling transmission. Source availability
is a review opportunity, not a claim that a binary or dependency is harmless.
