"""Pinned published helper replaces real collector, isolated CI only.
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
from collector.credentials import CredentialStore
from collector.expanded import ExpandedQueue
from collector.updater import ASSET, clean_env, download

# Verified downloaded public EXE against SHA256SUMS and update manifest.
OLD_VERSION = '0.3.2'
OLD_SHA = '4f3297f1bbdc8b7ca0cd09875935ba1d5bb87385c25512428d6c9cdf768b6c8e'


from contextlib import contextmanager


@contextmanager
def isolated_children(root):
    """Fail closed; only disposable GitHub Windows runners may run this fixture."""
    assert os.environ.get('GITHUB_ACTIONS') == 'true', 'Disposable GitHub runner required'
    group = 'SPHOL-migration-' + root.name
    paths = [root / ASSET, root / 'update-pinned' / 'update-helper.exe']
    quoted = ','.join("'" + str(p).replace("'", "''") + "'" for p in paths)
    script = f"""
$ErrorActionPreference = 'Stop'
$logs = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'EVE/logs'
if (Test-Path $logs) {{ throw 'Refusing host with existing EVE logs' }}
if (@(Get-NetFirewallProfile | Where-Object {{-not $_.Enabled}}).Count) {{ throw 'Firewall profile disabled' }}
foreach ($program in @({quoted})) {{
  New-NetFirewallRule -DisplayName '{group}' -Group '{group}' -Direction Outbound -Action Block -Program $program -Profile Any -Enabled True | Out-Null
}}
$rules = @(Get-NetFirewallRule -Group '{group}')
if ($rules.Count -ne 2 -or @($rules | Where-Object {{$_.Enabled -ne 'True' -or $_.Action -ne 'Block'}}).Count) {{ throw 'Missing egress block' }}
"""
    try:
        subprocess.run(['powershell', '-NoProfile', '-Command', script], check=True)
        print('Isolation: firewall outbound block verified for old/new collector and helper; no host EVE logs; synthetic LOCALAPPDATA only', flush=True)
        yield
    finally:
        subprocess.run(['powershell', '-NoProfile', '-Command',
                        f"$ErrorActionPreference = 'Stop'; Get-NetFirewallRule | Where-Object {{$_.Group -eq '{group}'}} | Remove-NetFirewallRule"], check=True)


def child_env(root):
    # SHGetKnownFolderPath(flags=0), used by the normal EXE, verifies that
    # Documents exists. An empty synthetic USERPROFILE is not a valid profile:
    # startup can stop in a modal error dialog before the SPHOL window exists.
    (root / 'Documents').mkdir(exist_ok=True)
    env = clean_env()
    env.update(LOCALAPPDATA=str(root), APPDATA=str(root), USERPROFILE=str(root),
               HOME=str(root), TEMP=str(root), TMP=str(root))
    return env


def check_child_documents(env):
    # Resolve under the SAME environment as both frozen EXEs, not the runner's
    # parent environment. Fail closed if Windows resolves host game logs.
    code = """
from collector.gui import documents
path = documents()
assert path.is_dir(), 'Child Documents directory missing'
assert not (path / 'EVE' / 'logs').exists(), 'Refusing child-visible EVE logs'
print('Child SHGetKnownFolderPath: existing Documents; no EVE logs', flush=True)
"""
    subprocess.run([sys.executable, '-c', code], env=env, check=True)


def process_diagnostics(target):
    # Only exact fixture processes; never dump credentials, env or host titles.
    path = str(target).replace("'", "''")
    script = f"Get-Process | Where-Object {{$_.Path -eq '{path}'}} | Select-Object Id,MainWindowTitle,Responding | ConvertTo-Json -Compress"
    return subprocess.check_output(['powershell', '-NoProfile', '-Command', script], text=True).strip()


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
    old = download(f'https://github.com/sergkreis/sphol-log-collector/releases/download/v{OLD_VERSION}/' + ASSET, 128 * 1024 * 1024)
    assert hashlib.sha256(old).hexdigest() == OLD_SHA
    # Firewall program filters reject Windows 8.3 TEMP aliases (RUNNER~1).
    # A private directory under the checkout has a canonical long path.
    with tempfile.TemporaryDirectory(prefix='sphol-cross-version-', dir=Path.cwd()) as temp, isolated_children(Path(temp)):
        root = Path(temp)
        state = root / 'SPHOLLogCollector'
        state.mkdir()
        q = PendingQueue(state / 'pending.sqlite3')
        q.put('cross-version-sentinel', {'listener': 'unknown', 'synthetic': True})
        q.close()
        credentials = {'access_token': 'SYNTHETIC_' * 8, 'token_type': 'Bearer',
                       'scope': 'combat:write', 'installation_id': 'synthetic-installation',
                       'expires_at': '2099-01-01T00:00:00Z',
                       'characters': [{'id': 123, 'name': 'Synthetic Pilot'}]}
        for name, scope in (('credentials.dpapi', 'combat:write'), ('credentials-v2.dpapi', 'gamelogs:write')):
            CredentialStore(state / name).save({**credentials, 'scope': scope})
            assert CredentialStore(state / name).load() == {**credentials, 'scope': scope}
            assert credentials['access_token'].encode() not in (state / name).read_bytes()
        expanded = ExpandedQueue(state)
        expanded.put('expanded-sentinel', {'schema': 2, 'category': 'notify',
                     'text': 'Цель неуязвима.', 'listener': 'Synthetic Pilot',
                     'time': '2030-01-01T00:00:00+00:00', 'type': 'game-event'})
        expanded.close()
        seeded = {p.name: p.read_bytes() for p in state.iterdir() if p.is_file()}
        stage = root / 'update-pinned'; stage.mkdir()
        target = root / ASSET
        target.write_bytes(old)
        (stage / ASSET).write_bytes(replacement)
        helper_path = stage / 'update-helper.exe'
        helper_path.write_bytes(old)
        env = child_env(root)
        check_child_documents(env)
        original = subprocess.Popen([str(target)], cwd=root, env=env)
        helper = None
        restarted = []
        try:
            wait_for(lambda: windows(target))
            before = {p.name: p.read_bytes() for p in state.iterdir() if p.is_file()}
            for name, data in seeded.items():
                assert before[name] == data, 'Old release changed seeded state: ' + name
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
            assert {'pending.sqlite3', 'pending-v2.sqlite3', 'credentials.dpapi', 'credentials-v2.dpapi'} <= before.keys()
            for name, scope in (('credentials.dpapi', 'combat:write'), ('credentials-v2.dpapi', 'gamelogs:write')):
                assert CredentialStore(state / name).load() == {**credentials, 'scope': scope}
            print(f'OK: published frozen v{OLD_VERSION} pinned SHA256=' + OLD_SHA + '; actual old helper replaced and relaunched collector; new SHA256=' + new_sha + '; all queue/credential bytes preserved; native DPAPI roundtrip before/after; capture never started; fixture forces parent exit')
        except Exception:
            print('Migration failure: original exit=' + str(original.poll()) +
                  '; fixture processes=' + process_diagnostics(target), flush=True)
            raise
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
