"""Frozen smoke fixture: never opens collector state or network."""
import time
import sys
from pathlib import Path
with open(Path.cwd() / 'dummy-starts.txt', 'a', encoding='utf-8') as f:
    f.write('synthetic start\n')
if sys.argv[1:] == ['--wait-gate']:
    deadline = time.monotonic() + 45
    while not (Path.cwd() / 'release-parent').exists():
        if time.monotonic() > deadline:
            raise SystemExit(2)
        time.sleep(.05)
else:
    time.sleep(2)
