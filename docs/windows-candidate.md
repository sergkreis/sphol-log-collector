# Windows 0.3.1 candidate (not a published release)

Run `Windows portable validation` on the reviewed candidate commit. The workflow
has read-only repository permissions and uploads artifacts only; it never releases.
The update manifest derives its version from `collector/version.py`.

The frozen `--synthetic-smoke-report` gate runs both native GUI suites: single
Start/Stop, combined stream counters, observation failure not masked by combat ACK,
original-identity backlog isolation, default-off capture/upload, pending browser
consent/reopen, and the legacy code copy/expiry controls. All data is synthetic and
state lives in temporary directories. Browser opening and transport are mocked.
Windows-only screenshots cover pending consent, combined counters and stream error;
the separate smoke-evidence artifact retains these and text reports even on failure.

The real previous executable is pinned to:
https://github.com/sergkreis/sphol-log-collector/releases/download/v0.3.0/SPHOLLogCollector.exe
SHA256: 9760cd68b4974b1784a42b61174b6175264c2e49ae10525a56c63167696f1cd0
Downloaded bytes matched the GitHub release asset SHA256 during preflight.
The migration gate uses that executable's helper to replace/relaunch the candidate,
checks seeded combat/observation queue and both protected credential files byte for
byte, and decrypts both credentials before/after with native CurrentUser DPAPI.
It forces the isolated old parent to exit, not the interactive update confirmation.

Linux source tests are not Windows acceptance. Before release, require successful
Windows source/frozen/DPAPI/dummy-update/real-v0.3.0-update gates and inspect evidence.
Real browser SSO/scope upgrade and server ACK integration remain separate release
gates; neither a synthetic ACK nor a cached credential proves production delivery.
No private server source, real EVE logs or credentials belong in these artifacts.
