"""Read-only queue presentation; never infer or repair historical identities."""
import json


def queue_summary(queue, listeners):
    total = eligible = unknown = 0
    for (payload,) in queue.db.execute('SELECT payload FROM pending'):
        total += 1
        try:
            event = json.loads(payload)
            listener = event.get('listener') if isinstance(event, dict) else None
        except (ValueError, TypeError):
            listener = None
        if not isinstance(listener, str) or not listener or listener == 'unknown':
            unknown += 1
        elif listener in listeners:
            eligible += 1
    return f'Событий на компьютере: {total} · Для персонажа: {eligible}\nБез персонажа: {unknown} · Остальные: {total - eligible - unknown}'
