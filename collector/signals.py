"""Bounded notification allowlist. No captured names or routes leave parsing."""
import re

VOCABULARY = {
    'fleet_warp': ('Выполняется варп флота.', 'Fleet warp initiated.'),
    'module_range': ('Цель вне радиуса действия модуля.', 'Target is outside module range.'),
    'target_invulnerable': ('Цель неуязвима.', 'Target is invulnerable.'),
}
PATTERNS = {
    'fleet_warp': re.compile(r"Переход в варп-режим по приказу [A-Za-z0-9][A-Za-z0-9 '-]{0,63}"),
    'module_range': re.compile(r"[A-Za-z0-9][A-Za-z0-9 *'-]{0,95} отключается, теряя руду в пространстве, так как вы отдалились на [0-9]{1,9},[0-9]{2} м от цели, что превышает радиус действия в [0-9]{1,9},[0-9]{2} м\."),
}


def sanitize(category, text):
    if category not in ('notify', 'None') or not isinstance(text, str):
        return None
    for signal, forms in VOCABULARY.items():
        pattern = PATTERNS.get(signal)
        if text in forms or (pattern and pattern.fullmatch(text)):
            return forms[0]
    return None
