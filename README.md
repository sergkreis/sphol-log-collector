# SPHOL foreground combat-log collector

**HTTPS client implemented; no Windows binary released, native GUI/build and deployed end-to-end flow not yet verified.** Public client source is separate from the private website. This is an unsigned testing candidate, not a production release.

## Use on Windows
Python 3.11+ with Tk: `python run_collector.py`. No administrator, installation, startup entry, service, tray, memory access or injection. Open during your sortie and close afterwards.

1. Pair with SPHOL; manually confirm the displayed code, member identity and characters in your browser at `https://sphol.com/collector/pair`. No password is entered in the collector.
2. Click **Start capture** for new combat lines. Existing files start at EOF; stopped-time history is not backfilled.
3. **Start capture** also enables legacy combat uploads for the bound character. Only exact server-approved character names may leave the computer. Pairing alone is not proof of a current connection; only exact server acknowledgement is reported as confirmed upload.
4. **Stop capture** also stops new uploads. An in-flight request may finish. Close exits; pending events survive and retry IDs do not change. Local unpair deletes credentials/pending after confirmation; revoke the installation separately on the website.

No deployed server success is assumed. If endpoints are absent, authorization fails, or responses are malformed, pending remains and the UI reports failure. HTTP 401/403 pauses uploads. Retryable network/429/5xx errors back off. Permanent rejections stay pending and pause uploads; no silent deletion.

## Data boundary
Only read-only, non-recursive Windows Documents (including redirected Documents)/`EVE/logs/Gamelogs/*.txt`. Legacy v1 sends only `(combat)` events with markup stripped. The separately consented [v2 mode](docs/GAMELOGS-V2.md) sends all Gamelogs categories, including potentially private notifications and original markup, after new scoped browser approval. Existing full local recording remains local-only and is never converted to cloud consent. No Chatlogs, diagnostics, browser credentials, EVE tokens or game memory. UTF-8 English/Russian listener headers; all data remains untrusted/client-reported. Combat text can identify people/ships/activities.

Queue: `%LOCALAPPDATA%/SPHOLLogCollector/pending.sqlite3`, unencrypted, max 10,000 events / 16 MiB payload, no eviction. Windows CurrentUser DPAPI protects `credentials.dpapi`; no plaintext fallback. Delete pending is not forensic erasure. Unapproved-character events remain pending visibly and do not block approved-character selection. Only accepted IDs from a complete validated response are removed.

Known boundaries: timestamps have second precision; the starting fractional second may be omitted. In-place log rewrites that regrow beyond the prior offset between polls may be missed. File count/session/line bounds stop or discard oversized input visibly where applicable. Native Windows ACL/reparse/race and multi-instance behavior require release review. Run only under a trusted Windows user account.

## Verification and portable build
`python -m unittest discover -s tests -v` uses synthetic temporary logs and mocked HTTPS responses; no production requests. Native DPAPI round-trip/tamper test runs only on Windows. Callback tests are not native GUI tests.

`.github/workflows/windows-build.yml` is manual-only, Windows 2022 + Python 3.11.9 x64, full action SHA pins verified against upstream tags, and hash-locked build-only dependencies installed with `--require-hashes --only-binary=:all:`. It tests, builds a PyInstaller one-file/windowed EXE, writes SHA256/source-commit files, and uploads those three files as a 14-day Actions candidate artifact named with the source commit. **No GitHub Release, signature or attestation is produced.** Check the actual workflow run before assuming a build succeeded. The checksum detects byte changes; the commit text is a build reference, not cryptographic provenance or a safety guarantee. Manual native UI/rotation/exit and end-to-end failure checks remain release gates. PyInstaller extracts into a temporary directory; portable does not mean no disk writes. Runtime uses only the Python standard library.

See [privacy](docs/PRIVACY.md), [protocol](docs/PROTOCOL.md), and the normative [implementation contract](docs/IMPLEMENTATION-CONTRACT.md). Do not include real logs, credentials or private website files in public issues/source.
