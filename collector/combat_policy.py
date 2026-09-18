"""Strict existing-envelope classification; never upgrades unknown records."""

def is_combat(event):
    if not isinstance(event, dict):
        return False
    schema = event.get('schema')
    if type(schema) is not int:
        return False
    if schema == 1:
        return event.get('type') == 'combat' and 'category' not in event
    # Released expanded producers sanitize notify/None only, never combat.
    # Unsupported schema-2 rows stay byte/ID-identical and must not block v1.
    return False
