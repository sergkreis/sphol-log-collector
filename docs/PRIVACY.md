# Privacy and review boundary

This preview has **no network code, telemetry, crash reporting, updater or login**.
It never reads Chatlogs, game memory, browser cookies, credentials or EVE passwords;
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
deleted or, in a future version, durably acknowledged by the server. Backups/OS disk
snapshots may retain deleted content. No queue or real log is part of the repository.

Public content consists only of client code, tests with invented names, and these
documents. Never submit actual logs, queued records, access tokens, screenshots
containing private combat information, or private website configuration in issues.

Future upload requires explicit browser pairing and consent; collection alone is
not proof of character ownership. Server retention, member visibility, deletion and
revocation policy must be approved before enabling transmission. Source availability
is a review opportunity, not a claim that a binary or dependency is harmless.
