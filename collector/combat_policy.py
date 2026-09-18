"""Strict existing-envelope classification; never upgrades unknown records."""

def is_combat(event):
    if not isinstance(event, dict):
        return False
    schema = event.get('schema')
    if type(schema) is not int:
        return False
    if schema == 1:
        return event.get('type') == 'combat' and 'category' not in event
    return schema == 2 and event.get('type') == 'game-event' and event.get('category') == 'combat'
