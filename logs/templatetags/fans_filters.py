from django import template
from django.utils.html import mark_safe, escape

register = template.Library()

# Human-readable labels for common audit log detail keys
_KEY_LABELS = {
    'beneficiary_id':   'Beneficiary ID',
    'beneficiary':      'Beneficiary',
    'decision':         'Decision',
    'score':            'Score',
    'similarity_score': 'Similarity Score',
    'threshold':        'Threshold',
    'reason':           'Reason',
    'action':           'Action',
    'action_label':     'Action Label',
    'notes':            'Notes',
    'id_type':          'ID Type',
    'id_verified':      'ID Verified',
    'claimant_type':    'Claimant Type',
    'liveness_passed':  'Liveness Passed',
    'liveness_score':   'Liveness Score',
    'event':            'Event',
    'quality_ok':       'Quality OK',
    'key':              'Config Key',
    'old_value':        'Old Value',
    'new_value':        'New Value',
    'attempt_id':       'Attempt ID',
    'review_notes':     'Review Notes',
    'template':         'Template',
    'templates_checked': 'Templates Checked',
    'original_score':   'Original Score',
    'stipend_event':    'Stipend Event',
    'via':              'Via',
    'highest_dedup_score': 'Dedup Score',
    'matched_beneficiary_id': 'Matched ID',
    'matched_name':     'Matched Name',
    'total_matches':    'Total Matches',
    'lookalike_id':     'Lookalike ID',
    'lookalike_name':   'Lookalike Name',
    'lookalike_score':  'Lookalike Score',
    'band':             'Detection Band',
    'attempted_name':   'Attempted Name',
}


@register.filter
def format_audit_details(details):
    """
    Renders audit log details as a small labeled key-value list.

    Handles every type that Django's JSONField can return: dict, list, str,
    int, float, bool, None.  Also survives completely unexpected input (e.g.
    a Python object stored by old code) by falling back to a plain text repr.
    Never raises an exception — any rendering failure returns empty string.
    """
    try:
        if details is None:
            return ''
        if details == {} or details == [] or details == '':
            return ''

        # Non-dict top-level value — render as compact monospaced text
        if not isinstance(details, dict):
            try:
                raw = str(details)
                return mark_safe(
                    f'<span class="text-muted font-mono">'
                    f'{escape(raw[:300] + ("…" if len(raw) > 300 else ""))}'
                    f'</span>'
                )
            except Exception:
                return ''

        parts = []
        for k, v in details.items():
            try:
                label = _KEY_LABELS.get(str(k), str(k).replace('_', ' ').title())
                if v is None:
                    display = mark_safe('&mdash;')
                elif isinstance(v, bool):
                    display = 'Yes' if v else 'No'
                elif isinstance(v, float):
                    display = f'{v:.4f}'
                elif isinstance(v, int):
                    display = escape(str(v))
                elif isinstance(v, (list, dict)):
                    raw = str(v)
                    display = escape(raw[:150] + ('…' if len(raw) > 150 else ''))
                else:
                    display = escape(str(v)[:200])
                parts.append(
                    mark_safe(
                        f'<span class="text-muted">{escape(label)}:</span> '
                        f'<strong>{display}</strong>'
                    )
                )
            except Exception:
                pass  # Skip any single entry that cannot be rendered

        return mark_safe(' &bull; '.join(parts))
    except Exception:
        return ''
