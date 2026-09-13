# Explicit cloud Gamelogs mode (schema 2)

This source adds an **independent**, default-off v2 panel. Existing local-only full
session recording remains local-only: its files are never used by either uploader.

1. Choose «Подтвердить v2 в браузере…», accept the Russian scope warning, and
   approve **gamelogs:write** on the website. A legacy combat approval cannot enable v2.
2. Choose «Включить отправку всех Gamelogs…» and explicitly accept cloud capture.
   The browser credential alone never starts capture or upload, including after restart.
3. New Gamelogs lines of all syntactically valid categories (including combat, notify,
   None, info and unknown categories) are captured with original case-sensitive category
   and plain raw text, including angle brackets. The server handles classification and
   battle association. Notifications can be private/sensitive. Chatlogs and diagnostics
   are excluded; existing history and local-captures are not uploaded.
4. Stop v2 or the global stop ends new capture/requests. An in-flight request may finish.
   Close warns about pending v2 records and retains them. Delete v2 queue affects only v2.

## Compatibility and persistence

- Legacy credentials.dpapi and pending.sqlite3 are not migrated, replaced or cleared by
  v2 approval. Existing combat IDs/payloads remain unchanged.
- v2 uses credentials-v2.dpapi (Windows CurrentUser DPAPI) and pending-v2.sqlite3
  (unencrypted, 10,000 records / 16 MiB payload, no eviction). Protect your Windows account.
- Both modes may operate independently; running both captures combat under both schemas.
  v2 raw combat stays private unless the server projects an approved signal; v1 retains
  existing combat analysis. No inference of missing combat data or activity is justified.
- Pairing uses existing /api/collector/v1/pairings with added scope=gamelogs:write;
  redemption must return exactly the requested scope. Upload uses the existing events
  route with envelope schema=2. The batch builder also supports unchanged schema 1
  records within a schema 2 envelope; the GUI deliberately keeps durable queues separate.
- Authoritative implemented ACK is {accepted_ids: [...], rejected: [...]}, unchanged
  from v1. The initial server v2 document said {accepted: [...]} in prose; actual server
  and real loopback integration confirm accepted_ids/rejected. Never weaken ACK validation.
- Unsupported/denied/unconfirmed v2 requests report that expanded mode is not confirmed
  or supported, preserve queues and leave legacy bindings intact. No deployed success
  is implied. Network/429/5xx upload failures retain records and schedule retries.
- Empty pre-login files are inert. Leading-space English/Russian listener headers are
  supported; unverified event-bearing sources wait separately without a global pause.

## Verification gate

Synthetic unit/callback tests: `python3 -m unittest discover -s tests -q`.
For a local server checkout, set `SPHOL_SERVER_PATH` to its absolute path and
`EVE_DASHBOARD_NO_COLLECTOR=1`, then run the same command. This enables loopback HTTP
pairing/scope rejection, real client capture, private raw persistence, capacity retry,
durable ACK/dedupe and legacy HTTP regression tests. Fixtures never use user logs.
Native Windows Tk/DPAPI and frozen executable interaction are still separate gates.
Do not build/publish a release before contract integration passes; this change is local
source only and does not claim a live backend, browser SSO or released executable.
