"""Pinned released 0.2.0 helper replaces real collector, isolated CI only.
No capture or pairing is started. Parent exit is forced in this fixture;
this tests real replacement/relaunch, not the interactive confirmation dialog.
"""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from collector.core import PendingQueue
from collector.updater import ASSET, clean_env, download

OLD_SHA = '1369fd3a62ae274d3418dfda87b138c1c80cc7a3c94dcf97700f7b90cf68d882'


def windows(target):
    # Exact private executable path, never a global process-name selection.
    path = str(target).replace("'", "''")
    script = f"Get-Process | Where-Object {{$_.Path -eq '{path}' -and $_.MainWindowTitle -like 'SPHOL*'}} | ForEach-Object {{$_.Id}}"
    return subprocess.check_output(['powershell', '-NoProfile', '-Command', script], text=True).split()


def wait_for(predicate, seconds=40):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.2)
    raise AssertionError('Native cross-version deadline exceeded')


def main():
    assert os.name == 'nt', 'Windows required'
    replacement = Path(sys.argv[1]).absolute().read_bytes()
    old = download('https://github.com/sergkreis/sphol-log-collector/releases/download/v0.2.0/' + ASSET, 128 * 1024 * 1024)
    assert hashlib.sha256(old).hexdigest() == OLD_SHA
    with tempfile.TemporaryDirectory(prefix='sphol-cross-version-') as temp:
        root = Path(temp)
        state = root / 'SPHOLLogCollector'
        state.mkdir()
        q = PendingQueue(state / 'pending.sqlite3')
        q.put('cross-version-sentinel', {'listener': 'unknown', 'synthetic': True})
        q.close()
        for name in ('credentials.dpapi', 'credentials-v2.dpapi'):
            (state / name).write_bytes(b'synthetic non-secret credential sentinel')
        stage = root / 'update-pinned'; stage.mkdir()
        target = root / ASSET
        target.write_bytes(old)
        (stage / ASSET).write_bytes(replacement)
        helper_path = stage / 'update-helper.exe'
        helper_path.write_bytes(old)
        env = clean_env()
        env.update(LOCALAPPDATA=str(root), TEMP=str(root), TMP=str(root))
        original = subprocess.Popen([str(target)], cwd=root, env=env)
        helper = None
        restarted = []
        try:
            wait_for(lambda: windows(target))
            before = {p.name: p.read_bytes() for p in state.iterdir() if p.is_file()}
            new_sha = hashlib.sha256(replacement).hexdigest()
            helper = subprocess.Popen([str(helper_path), '--apply-update', str(target), str(original.pid), OLD_SHA, new_sha], cwd=stage, env=env)
            wait_for(lambda: (stage / 'ready').exists())
            (stage / 'apply').write_bytes(b'apply')
            subprocess.run(['taskkill', '/PID', str(original.pid), '/T', '/F'], check=True)
            original.wait(timeout=20)
            assert helper.wait(timeout=45) == 0
            restarted = wait_for(lambda: windows(target))
            assert target.read_bytes() == replacement
            assert (root / 'SPHOLLogCollector.update-pinned.rollback.exe').read_bytes() == old
            for name, data in before.items():
                assert (state / name).read_bytes() == data, name
            assert {'pending.sqlite3', 'credentials.dpapi', 'credentials-v2.dpapi'} <= before.keys()
            print('OK: published frozen v0.2.0 pinned SHA256=' + OLD_SHA + '; actual old helper replaced and relaunched collector; new SHA256=' + new_sha + '; window PIDs=' + ','.join(restarted) + '; all queue/credential bytes preserved; capture never started; fixture forces parent exit')
        finally:
            for pid in windows(target):
                subprocess.run(['taskkill', '/PID', pid, '/T', '/F'], check=False)
            if original.poll() is None:
                subprocess.run(['taskkill', '/PID', str(original.pid), '/T', '/F'], check=False)
            if helper is not None and helper.poll() is None:
                helper.kill(); helper.wait(timeout=10)
            time.sleep(2)


if __name__ == '__main__':
    main()
