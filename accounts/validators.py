"""
Custom Django password validators for FANS-C.

CharacterClassValidator
    Requires at least one letter (any case) AND one of: digit, whitespace,
    or symbol. This blocks easy choices like "barangay123" being just a
    word concatenated with digits while still allowing accessible passphrases.

UppercasePasswordValidator
    Requires at least one uppercase letter A–Z.
"""
import re
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _


class CharacterClassValidator:
    def validate(self, password, user=None):
        has_letter = bool(re.search(r'[A-Za-z]', password or ''))
        has_other = bool(re.search(r'[\d\W_]', password or ''))
        if not (has_letter and has_other):
            raise ValidationError(
                _('Password must contain at least one letter and at least one '
                  'digit or symbol.'),
                code='password_character_classes',
            )

    def get_help_text(self):
        return _('Password must contain letters and at least one digit or symbol.')


class UppercasePasswordValidator:
    def validate(self, password, user=None):
        if not re.search(r'[A-Z]', password or ''):
            raise ValidationError(
                _('Password must contain at least one uppercase letter.'),
                code='password_no_upper',
            )

    def get_help_text(self):
        return _('Your password must contain at least one uppercase letter (A–Z).')
