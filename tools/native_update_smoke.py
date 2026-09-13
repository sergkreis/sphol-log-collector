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


def scenario(collector, dummy, replacement, rollback=False):
    with tempfile.TemporaryDirectory(prefix='sphol-update-smoke-') as d:
        root = Path(d)
        stage = root / 'update-synthetic'; stage.mkdir()
        target = root / 'dummy.exe'
        shutil.copyfile(dummy, target)
        payload = b'MZinvalid synthetic executable' if rollback else replacement.read_bytes()
        (stage / ASSET).write_bytes(payload)
        shutil.copyfile(collector, stage / 'update-helper.exe')
        sentinels = {name: b'synthetic state untouched: ' + name.encode() for name in
                     ('pending.sqlite3', 'pending-v2.sqlite3', 'credentials.dpapi', 'credentials-v2.dpapi')}
        for name, data in sentinels.items(): (root / name).write_bytes(data)
        old = target.read_bytes()
        sha = hashlib.sha256(old).hexdigest()
        new_sha = hashlib.sha256(payload).hexdigest()
        assert sha != new_sha, 'Replacement must have distinct bytes'
        original = subprocess.Popen([str(target), '--wait-gate'], cwd=root, env=clean_env())
        helper = None
        try:
            # A valid PID with the wrong image must never authorize replacement.
            wrong = subprocess.Popen([str(stage / 'update-helper.exe'), '--apply-update', str(target), str(os.getpid()), sha, new_sha], cwd=stage, env=clean_env())
            assert wrong.wait(timeout=30) != 0
            assert not (stage / 'ready').exists() and target.read_bytes() == old
            helper = subprocess.Popen([str(stage / 'update-helper.exe'), '--apply-update', str(target), str(original.pid), sha, new_sha], cwd=stage, env=clean_env())
            deadline = time.monotonic() + 30
            while not (stage/'ready').exists():
                if helper.poll() is not None or time.monotonic() > deadline:
                    raise AssertionError('Frozen helper did not bind exact dummy process')
                time.sleep(.05)
            assert original.poll() is None and target.read_bytes() == old
            (stage/'apply').write_bytes(b'apply')
            (root/'release-parent').write_bytes(b'exit')
            assert original.wait(timeout=30) == 0
            result = helper.wait(timeout=45)
            assert (result != 0) if rollback else (result == 0)
            deadline = time.monotonic() + 20
            while not (root/'dummy-starts.txt').exists() or len((root/'dummy-starts.txt').read_text().splitlines()) != 2:
                if time.monotonic() > deadline: raise AssertionError('Dummy did not restart')
                time.sleep(.05)
            assert target.read_bytes() == (old if rollback else payload)
            backup = root/'dummy.update-synthetic.rollback.exe'
            assert not backup.exists() if rollback else backup.read_bytes() == old
            for name, data in sentinels.items(): assert (root/name).read_bytes() == data
            # Restarted dummy exits itself; no process-name or production-PC kills.
            time.sleep(6)
        finally:
            (root/'release-parent').write_bytes(b'exit')
            for process in (original, helper):
                if process is not None and process.poll() is None:
                    process.kill()  # Exact isolated test child only, on test failure.
                    process.wait(timeout=10)
    print('OK: wrong-image rejected; frozen helper bound exact process; ' +
          ('launch-error rollback restored and restarted original' if rollback else
           'distinct executable replaced and restarted; original backup retained') +
          '; both queues and credential sentinels preserved')


def main():
    if os.name != 'nt':
        raise SystemExit('Native Windows required (not simulated)')
    collector, dummy, replacement = map(lambda p: Path(p).absolute(), sys.argv[1:])
    scenario(collector, dummy, replacement)
    scenario(collector, dummy, replacement, rollback=True)


if __name__ == '__main__': main()
