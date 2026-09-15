# Late token redemption candidate gate

The server commits `collector_pairings.redeemed=1` and a new installation before returning the token. Repeating the same redemption returns HTTP 410; the stored token hash cannot recover the plaintext response. The real TempStore/Handler regression demonstrates this ordering.

Client behavior:
- The 20-second presentation timeout stops collection and cancels automatic resume, but retains ownership of an in-flight token worker. Pairing expiry cannot invalidate that worker either.
- Retry, unpair and normal close cannot abandon that outstanding result. Tk remains responsive, with a visible waiting explanation. A late valid result is saved with capture/upload OFF; Start is explicit.
- A storage failure retains the returned credential in memory. Retry repeats only storage, not pairing. Normal close, unpair and updater restart remain blocked until storage succeeds.
- Existing generation rejection remains intact: an older attempt cannot overwrite the current credential.
- A terminal token-request error is treated as potentially committed, with an explicit support/devices fallback and no new pairing in that process. Normal close becomes available. Existing credentials and queues are preserved.

## Remaining protocol blocker (not changed here)

`HTTPSConnection(..., timeout=15)` limits blocking socket operations, not total wall-clock duration. DNS and a peer slowly delivering a response can outlive the UI deadline. We cannot truthfully guarantee both a strict bounded exit and lossless irreversible redemption using the current server protocol. Forced termination, a lost response after commit, or persistent local credential-storage failure can still prevent recovery; the in-process retry block is not a cross-restart orphan-prevention guarantee.

A lossless bounded recovery design requires a separately reviewed server protocol: retry authenticated by the same high-entropy device secret plus verifier must recover the same installation credential for a bounded recovery window, without allocating another installation. Persist the recovery proof safely before redemption, retain revocation/expiry/owner/scope checks, and rate-limit/replay-test it. Do not implement a hard client abort and label it safe. No server or production changes are included in this candidate.

## Executed gates

With `EVE_DASHBOARD_NO_COLLECTOR=1`, `SPHOL_SERVER_PATH` pointing to the isolated server checkout, and Xvfb/Pillow:
- Full client discovery, including real server fixtures: 105 tests, OK, one native Windows DPAPI skip.
- `run_collector.py --synthetic-smoke-report ...`: six native Tk tests OK, including the late-redemption visible-control test. This is the source entrypoint, not a newly built Windows EXE.
- `git diff --check`: clean.

Tests use synthetic identities, temporary stores and blocked external sockets in GUI fixtures; integration traffic is loopback only. No release, CI, push, key operation or production write was performed.
