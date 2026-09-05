"""
BPA-4 — display-only formatting filters for Biometric Performance Analytics.

These filters format numbers the calculation layer (verification.
biometric_analytics) already produced — they never compute a rate, count, or
threshold themselves (BPA-4 checkpoint §40: "No mathematics in template").
"""
from django import template

register = template.Library()


@register.filter
def rate_pct(rate):
    """A `{'numerator','denominator','rate'}` dict (verification.
    biometric_analytics._rate convention) -> '12.34%', or 'Not available'
    when `rate` is None (zero-denominator / no eligible data) so an
    unavailable rate is never confused with a measured 0.00% (BPA-4 §6/§41)."""
    if not isinstance(rate, dict):
        return 'Not available'
    value = rate.get('rate')
    if value is None:
        return 'Not available'
    return f'{value * 100:.2f}%'


@register.filter
def frac_pct(value):
    """A raw fraction (0.0-1.0) or None -> percentage string, or 'Not
    available'. Unlike `rate_pct`, this takes the bare float directly — for
    values like EER that are not wrapped in the {numerator,denominator,rate}
    convention."""
    if value is None:
        return 'Not available'
    try:
        return f'{float(value) * 100:.2f}%'
    except (TypeError, ValueError):
        return 'Not available'


@register.filter
def decimal_or_na(value, places=4):
    """A raw float (AUC, EER, a similarity score) -> fixed-decimal string, or
    'Not available' for None. `places` defaults to 4 significant decimals —
    enough to distinguish adjacent cosine-similarity scores without implying
    false precision."""
    if value is None:
        return 'Not available'
    try:
        return f'{float(value):.{int(places)}f}'
    except (TypeError, ValueError):
        return 'Not available'


@register.filter
def ms_to_seconds(value):
    """Milliseconds -> seconds string with millisecond precision retained
    (BPA-4 §24: prefer seconds for primary display, keep ms precision)."""
    if value is None:
        return 'Not available'
    try:
        return f'{float(value) / 1000.0:.3f}'
    except (TypeError, ValueError):
        return 'Not available'


@register.filter
def transition_label(key):
    """'verified_to_manual_review' -> 'Verified → Manual Review'. Pure string
    formatting of an already-computed transition-count dict key."""
    if not isinstance(key, str) or '_to_' not in key:
        return key
    left, right = key.split('_to_', 1)
    return f"{left.replace('_', ' ').title()} → {right.replace('_', ' ').title()}"


@register.filter
def get_item(mapping, key):
    """Dict lookup by a variable key — Django templates only support literal
    dotted lookups, so a variable-keyed lookup (e.g. transitions[matcher_decision])
    needs this small helper. No calculation performed."""
    if not isinstance(mapping, dict):
        return None
    return mapping.get(key)
