# Pairing recovery candidate gates

Candidate version: 0.3.3. Scoped candidate-branch publication and manual Windows CI are authorized; stable GitHub release and server deployment are not.

## Offline source gates

```sh
EVE_DASHBOARD_NO_COLLECTOR=1 SPHOL_SERVER_PATH=/tmp/sphol-dis-nickname-partial xvfb-run -a /tmp/sphol-pairing-venv/bin/python -m unittest discover -s tests -v
EVE_DASHBOARD_NO_COLLECTOR=1 xvfb-run -a /tmp/sphol-pairing-venv/bin/python run_collector.py --synthetic-smoke-report /tmp/pairing-smoke.txt
```

The source entrypoint is not a frozen binary. Its real-button fixtures exercise Start/consent, retry, browser reopen, copy/clipboard equality, browser failures, stale responses, expiry, late irreversible redemption, storage failure/retry and explicit resume. Queue and credential preservation remains mandatory. An in-flight token worker can block normal close indefinitely; this is not bounded lossless shutdown or crash recovery.

## Native Windows gates

The manual-only `.github/workflows/windows-build.yml` runs the full source suite, native Tk fixtures, hash-locked PyInstaller build, actual frozen synthetic entrypoint (requiring named pairing and late-token successes), isolated dummy updater and actual previous-release migration. Only read-only repository permissions are used; there is no release publication step.

Pillow is absent from the existing Windows dependency lock. Optional screenshot source fixtures may skip explicitly; no unpinned dependency was added. Core native real-button and frozen gates do not depend on Pillow. Linux screenshot/layout tests remain in the offline suite.

## Previous release and isolation

The latest published release was queried live and its actual v0.3.2 EXE downloaded. SHA256SUMS.txt, update-manifest.json and calculated EXE SHA agree:

`4f3297f1bbdc8b7ca0cd09875935ba1d5bb87385c25512428d6c9cdf768b6c8e`

The migration fixture derives its URL and report label from `OLD_VERSION = '0.3.2'` and checks this digest before execution. It requires a disposable GitHub Windows runner, refuses existing Documents/EVE/logs, uses temporary synthetic LOCALAPPDATA/APPDATA/profile/temp directories, and checks enabled firewall profiles plus explicit outbound-block rules for both executable paths before launching any old/helper/new child. The new executable replaces the same blocked target path. Setup/download traffic remains available to the runner, not these collector processes. Rules are removed in fixture cleanup after child termination.

Migration proves actual replacement/relaunch, unchanged seeded queue/credential bytes and native DPAPI decryptability before/after. It forces the old parent to exit, so it does not prove the normal user-confirmed updater dialog or identity shown by both ordinary EXEs. The separate frozen updater fixture exercises the confirmation path against isolated dummies.

## Handoff limitations

Actions artifacts are unsigned candidates, not an offer through the Update button. Real SSO, affected-machine browser/network behavior, production redemption and actual upload ACKs are separate acceptance. No real pilot logs, credentials, pairing challenge or server deployment belongs in this public candidate. Release selection/discovery and stable publication remain parent-owned.
