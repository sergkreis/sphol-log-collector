# Collector API v1 — implementation specification

Exact wire fields and responses are defined in [IMPLEMENTATION-CONTRACT.md](IMPLEMENTATION-CONTRACT.md).
Client HTTPS transport is implemented; server deployment and end-to-end verification
remain separate release gates. All paths use `https://sphol.com`; their presence in this
specification does not imply that the server accepts them. Do not reuse website session cookies or EVE access tokens as collector
credentials. Existing website authentication uses browser SSO and member sessions;
collector authorization must be a separate explicitly scoped implementation.

## Pairing

1. Client explicitly starts pairing with POST `/api/collector/v1/pairings` over
   verified TLS, sending a random verifier's SHA-256 challenge (S256), client
   version and user-visible device label. No machine fingerprint. Server returns
   high-entropy device secret, short human code, fixed-origin verification URL,
   expiry (5 minutes) and minimum polling interval. Rate-limit code creation/guessing;
   never put device secrets or access tokens in URLs or logs.
2. Default browser opens a fixed allowlisted `https://sphol.com/collector/pair` page.
   User logs in via the site's normal EVE SSO (password only at the EVE provider),
   manually confirms the matching code/device, consent and permitted characters.
   Require authenticated session, CSRF token and Origin validation on approval;
   recheck membership server-side; expire challenges and make approval one-use.
3. Client polls POST `/api/collector/v1/pairings/token` with device secret and
   verifier, obeying interval/slow_down/expiry. Bind redemption to the S256 challenge.
   Return a random revocable opaque token, hashed at rest server-side, with only
   `combat:write`, installation ID, approved character IDs and explicit expiry.
   No read/admin/ESI scope. Single-use redemption; never derive token from short code.
4. Protect token with Windows DPAPI CurrentUser, not plaintext SQLite/config.
   Do not implement insecure fallback. Show account/characters/expiry/last confirmed
   upload, allow unpair locally and revoke on the website; no automatic re-pairing.
   Server rejects revoked, expired or non-member installations at upload time.
   Define short membership cache TTL and fail closed if authorization is unknown.

## Upload

POST `/api/collector/v1/events` with `Authorization: Bearer <scoped token>`, JSON
`{ "schema": 1, "events": [ { "id": "<64 lowercase hex>", "schema": 1,
"time": "<UTC ISO-8601>", "type": "combat", "listener": "<untrusted label>",
"text": "<plain combat text>" } ] }`.

Maximum 100 events and 256 KiB per request; client batch builder must enforce both.
Reject unknown fields, invalid UTF-8, timestamps outside approved bounds, oversized
text, non-combat types, and malformed IDs. Authenticate before ingestion. Treat
listener names as untrusted labels, not authorization; server must map only to
explicitly paired characters and refuse ambiguous/unapproved identities. Multi-alt
pairing/ownership UX remains a product decision; never silently upload other alts.
Game files can be edited: label data client-reported, never ESI-verified evidence.
Escape text on rendering; never accept markup or render supplied HTML.

Atomic server transaction with unique `(installation_id, event_id)` and canonical
payload digest. Identical retry returns existing success; same ID/different payload
is conflict, not overwrite. Response `{"accepted_ids": [...], "rejected": [...]}`
contains only submitted IDs and is returned only after durable commit. Client deletes
only exact acknowledged IDs after strict response validation; no ACK on mere 2xx,
timeout, invalid JSON, partial response or connection loss. IDs are generated once
when queueing and persisted, never regenerated for retry. Exact ACK transport tests
are required before wiring `PendingQueue.acknowledge` to networking.

Retry 429 respecting bounded Retry-After and 5xx/network failures with capped
exponential backoff+jitter; retain queue. Stop on 401/403/revocation and display
not-connected. Quarantine permanent schema failures visibly, no silent drop. TLS
certificate validation mandatory; prohibit cross-origin redirects and custom URL
entry. No request/response bodies, tokens or combat text in diagnostic logs.

## Server integration decisions still required

Separate token/pairing/revocation tables and migrations; rate limits; CSRF routes;
role/member policy; alt binding; request/body limits; retention and deletion; member
visibility; idempotency transactions; clock skew tolerance; durable ACK semantics;
DPAPI and HTTP client implementation; real Windows and end-to-end failure tests.
Website public/development bypass must NEVER authorize collector endpoints.
No website files, schemas or services have been changed by this client stage.
