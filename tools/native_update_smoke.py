"""Windows-only actual frozen helper/locked dummy replacement, isolated."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from collector.updater import ASSET, clean_env


def main():
    if os.name != 'nt':
        raise SystemExit('Native Windows required (not simulated)')
    collector, dummy = map(lambda p: Path(p).absolute(), sys.argv[1:])
    with tempfile.TemporaryDirectory(prefix='sphol-update-smoke-') as d:
        root = Path(d)
        stage = root / 'update-synthetic'; stage.mkdir()
        target = root / 'dummy.exe'
        shutil.copyfile(dummy, target)
        shutil.copyfile(dummy, stage / ASSET)
        shutil.copyfile(collector, stage / 'update-helper.exe')
        sentinel = root / 'pending.sqlite3'; sentinel.write_bytes(b'synthetic state untouched')
        sha = hashlib.sha256(target.read_bytes()).hexdigest()
        original = subprocess.Popen([str(target), '--wait-gate'], cwd=root, env=clean_env())
        helper = subprocess.Popen([str(stage / 'update-helper.exe'), '--apply-update', str(target), str(original.pid), sha, sha], cwd=stage, env=clean_env())
        deadline = time.monotonic() + 30
        while not (stage/'ready').exists():
            if helper.poll() is not None or time.monotonic() > deadline:
                raise AssertionError('Frozen helper did not bind exact dummy process')
            time.sleep(.05)
        (stage/'apply').write_bytes(b'apply')
        (root/'release-parent').write_bytes(b'exit')
        original.wait(timeout=30)
        assert helper.wait(timeout=45) == 0
        deadline = time.monotonic() + 20
        while not (root/'dummy-starts.txt').exists() or len((root/'dummy-starts.txt').read_text().splitlines()) != 2:
            if time.monotonic() > deadline: raise AssertionError('Updated dummy did not restart')
            time.sleep(.05)
        assert (root/'dummy.update-synthetic.rollback.exe').read_bytes() == target.read_bytes()
        assert sentinel.read_bytes() == b'synthetic state untouched'
        # Dummy exits itself; no process-name or production-PC kills.
        time.sleep(6)
    print('OK: frozen helper bound, waited, replaced, restarted; state preserved')

if __name__ == '__main__': main()
