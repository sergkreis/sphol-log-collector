"""Generate release metadata only from final frozen executable."""
import json
import sys
from pathlib import Path
from collector.updater import ASSET, digest, version, MAX_EXE
from collector.version import VERSION

if __name__ == '__main__':
    version(VERSION)
    exe = Path(sys.argv[1]).absolute()
    if exe.name != ASSET or not 1 <= exe.stat().st_size <= MAX_EXE:
        raise SystemExit('Invalid release executable')
    data = dict(schema=1, version=VERSION, asset=ASSET, size=exe.stat().st_size, sha256=digest(exe))
    (exe.parent / 'update-manifest.json').write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
