"""OFFLINE repair of empty listeners. No transport, credentials or bound-character inference.

Dry-run: python -m tools.recover_listeners --queue COPY --logs Gamelogs
Apply ONLY with collector closed: add --apply --collector-closed --backup NEW_FILE
Matching uses the entire original normalized record (schema/time/type/text), not
substring/fuzzy/time-only matching. The old queue did not retain raw log bytes;
multiple source occurrences, even identical ones, therefore remain ambiguous.
"""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import sqlite3
from collector.core import Tailer, safe_open, parse_line, MAX_LINE


def record_key(event):
    return json.dumps({k: v for k, v in event.items() if k not in ('listener', 'id')}, sort_keys=True, ensure_ascii=False)


def recover(queue, logs, *, apply=False, collector_closed=False, backup=None):
    queue, logs = Path(queue).resolve(strict=True), Path(logs).resolve(strict=True)
    if apply and (not collector_closed or backup is None):
        raise ValueError('Close collector and specify a new backup path before applying')
    sources = defaultdict(list)
    files = list(logs.glob('*.txt'))
    if len(files) > 512:
        raise ValueError('Source file limit exceeded')
    for path in files:
        with safe_open(path) as f:
            listener = Tailer.listener(f)
            f.seek(0)
            while True:
                raw = f.readline(MAX_LINE + 1)
                if not raw:
                    break
                if len(raw) > MAX_LINE:
                    while raw and not raw.endswith(b'\n'):
                        raw = f.readline(MAX_LINE + 1)
                    continue
                if not raw.endswith(b'\n'):
                    continue
                event = parse_line(raw)
                if event:
                    # Unknown-header duplicates must also prevent attribution.
                    sources[record_key(event)].append(listener)
    db = sqlite3.connect(queue.as_uri() + ('?mode=rw' if apply else '?mode=ro'), uri=True, timeout=0)
    try:
        if apply:
            db.execute('PRAGMA locking_mode=EXCLUSIVE')
            db.execute('BEGIN EXCLUSIVE')
        rows = db.execute('SELECT id,payload FROM pending').fetchall()
        changes = []
        counts = dict(total=len(rows), empty=0, recoverable=0, ambiguous=0, unmatched=0, applied=0)
        for identity, payload in rows:
            event = json.loads(payload)
            if event.get('listener') != '':
                continue
            counts['empty'] += 1
            matches = sources.get(record_key(event), [])
            if len(matches) == 1 and matches[0]:
                event['listener'] = matches[0]
                fixed = json.dumps(event, ensure_ascii=False, separators=(',', ':'))
                changes.append((fixed, len(fixed.encode('utf-8')), identity, payload))
                counts['recoverable'] += 1
            elif matches:
                counts['ambiguous'] += 1
            else:
                counts['unmatched'] += 1
        if apply:
            # Create an exclusive, complete pre-change backup under the writer lock.
            backup = Path(backup)
            with backup.open('xb'):
                pass
            out = sqlite3.connect(backup)
            try:
                # iterdump operates on this exact locked pre-change snapshot.
                out.executescript('\n'.join(db.iterdump()))
                if out.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('Backup integrity failure')
            finally:
                out.close()
            db.executemany('UPDATE pending SET payload=?,size=? WHERE id=? AND payload=?', changes)
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Queue integrity failure')
            db.commit()
            counts['applied'] = len(changes)
        return counts
    finally:
        db.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--queue', required=True, type=Path)
    p.add_argument('--logs', required=True, type=Path)
    p.add_argument('--backup', type=Path)
    p.add_argument('--apply', action='store_true')
    p.add_argument('--collector-closed', action='store_true')
    args = p.parse_args()
    print(json.dumps(recover(**vars(args)), sort_keys=True))


if __name__ == '__main__':
    main()
