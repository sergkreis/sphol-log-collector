# Pairing recovery candidate: gates, not a release

No version, protocol or updater interface change. No publication, push,
workflow dispatch, deployment or production pairing is authorized by this work.

## Locally reproducible offline gates

With Python 3.11, Tk, Pillow and Xvfb installed, from the repository root:

```sh
xvfb-run -a /tmp/sphol-pairing-venv/bin/python -m unittest discover -s tests -v
xvfb-run -a /tmp/sphol-pairing-venv/bin/python run_collector.py --synthetic-smoke-report /tmp/pairing-smoke.txt
```

The second command exercises the **same entrypoint** packaged into the EXE,
not a frozen Windows binary. It includes real Start/Yes, Retry, Open Browser,
Copy Link/clipboard equality, browser false/exception/success, stale worker,
timeout and expiry controls. Sockets and credential loading are blocked or
injected. Existing unified stream, connection, capture and shutdown gates remain.
Footer, settings and recovery controls are checked at 780x700 and 760x660.
Secondary stream summaries receive remaining space during a tall recovery notice;
settings/diagnostics and primary actions retain priority. Offline dashboard and
expanded-settings preview assertions are unchanged.

Expected Linux exclusions are native DPAPI and server contract integration
(`SPHOL_SERVER_PATH` needs a separately approved server checkout). They are not
new skips. Windows/frozen acceptance is still required.

## Exact native build gates (PowerShell, Windows x64 Python 3.11.9)

Run only in an isolated candidate checkout and synthetic state, never a pilot's
live collector directory:

```powershell
python -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw 'Source tests failed' }
python -m unittest tools.native_gui_smoke tools.native_single_smoke tools.native_pairing_smoke -v
if ($LASTEXITCODE -ne 0) { throw 'Native Tk failed' }
python -m pip install --require-hashes --only-binary=":all:" -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw 'Build dependencies failed' }
python -m PyInstaller --clean --noconfirm --onefile --windowed --name SPHOLLogCollector run_collector.py
if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
$env:SPHOL_SMOKE_EVIDENCE = 'smoke-reports'
New-Item -ItemType Directory -Force smoke-reports | Out-Null
$report = Join-Path $pwd 'smoke-reports/frozen-smoke.txt'
$p = Start-Process dist/SPHOLLogCollector.exe -ArgumentList @('--synthetic-smoke-report', "`"$report`"") -PassThru
if (-not $p.WaitForExit(90000)) { $p.Kill(); throw 'Frozen smoke timeout' }
if ($p.ExitCode -ne 0 -or -not (Test-Path $report)) { throw 'Frozen smoke failed' }
Get-Content $report
if (-not (Select-String $report -Pattern '^OK$' -Quiet)) { throw 'Missing OK' }
if (-not (Select-String $report -Pattern '^test_visible_retry_consent_expiry_stale_and_copy .* \.\.\. ok$' -Quiet)) { throw 'Pairing gate absent' }
Get-FileHash dist/SPHOLLogCollector.exe -Algorithm SHA256
git rev-parse HEAD
```

The manual-only, read-only-permission `.github/workflows/windows-build.yml`
contains these source/native/frozen gates and retains candidate artifacts and
synthetic evidence, including on failure. It does not publish releases.
Pillow is not in the existing Windows build lock: install a reviewed pinned
Windows Pillow test dependency before treating the optional screenshot-based
source dashboard test as executed on Windows; native real-button/frozen tests
above do not depend on Pillow.

## Remaining release blockers / workflow audit

- No Windows executor or CI dispatch authorization in this task: Windows build,
  DPAPI, frozen EXE and interactive default-browser acceptance remain unexecuted.
- Existing cross-version job is pinned to **v0.3.1**, not the supplied handoff's
  latest v0.3.2. Its incorrect v0.3.0 result label is corrected, but it must be
  repinned using freshly verified release metadata/digest before release. Do not
  call that job evidence for migration from v0.3.2.
- That cross-version fixture launches normal old/new EXEs with synthetic stored
  credentials; unlike the explicit synthetic entrypoint it does not block socket
  access. A normal connection probe can reach production. Add external egress
  isolation for both EXEs before running it under an offline-only approval.
- Existing cross-version replacement forcibly exits its parent: byte retention
  and DPAPI roundtrip are not the normal user-confirmed updater experience.
- Parent must approve candidate version selection, source review, any push/CI
  dispatch, previous-release download and release/deployment separately. Keep
  current version unchanged until then. Artifacts are not an updater offer.
- Real SSO approval, credential redemption and actual upload ACK acceptance must
  be verified separately; synthetic browser mocks cannot identify the affected
  pilots' original network/browser root cause.
