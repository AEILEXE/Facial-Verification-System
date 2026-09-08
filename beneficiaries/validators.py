import datetime
import re

from django.core.exceptions import ValidationError

_ID_WHITESPACE_RE = re.compile(r'\s+')


def normalize_id_number(value: str) -> str:
    """
    Normalize a government ID number for identity-document comparison only
    (never for storage or display): collapse internal whitespace, strip, and
    uppercase, so that harmless formatting differences (spacing, case) don't
    hide that two entries name the same physical document.
    """
    return _ID_WHITESPACE_RE.sub('', (value or '').strip()).upper()


def representative_uses_beneficiary_identity(
    beneficiary_valid_id_type: str,
    beneficiary_valid_id_number: str,
    beneficiary_senior_citizen_id: str,
    rep_id_type: str,
    rep_id_number: str,
) -> bool:
    """
    True when the representative's ID type+number names the same identity
    document as the beneficiary they represent — either the beneficiary's own
    Valid ID or their Senior Citizen ID. Used to block a representative from
    impersonating the very beneficiary they claim on behalf of; it does not
    enforce any cross-beneficiary/global ID uniqueness.
    """
    rep_number = normalize_id_number(rep_id_number)
    if not rep_number:
        return False

    if (
        rep_number == normalize_id_number(beneficiary_valid_id_number)
        and rep_number
        and (rep_id_type or '').strip().casefold()
        == (beneficiary_valid_id_type or '').strip().casefold()
    ):
        return True

    if (
        (rep_id_type or '').strip().casefold() == 'senior citizen id'
        and rep_number == normalize_id_number(beneficiary_senior_citizen_id)
        and rep_number
    ):
        return True

    return False


def validate_senior_citizen_dob(dob):
    """
    Validate that a date of birth qualifies for senior citizen status (age >= 60).

    Rejects:
    - Future dates of birth
    - Ages under 60 years

    Uses a precise birthday comparison instead of (today - dob).days // 365 to
    avoid the leap-year off-by-one error that the floor-division approach produces
    for people whose birthday falls on Feb 29.
    """
    today = datetime.date.today()

    if dob > today:
        raise ValidationError('Date of birth cannot be in the future.')

    # Subtract 1 if this year's birthday has not yet occurred.
    age = today.year - dob.year - (
        (today.month, today.day) < (dob.month, dob.day)
    )
    if age < 60:
        raise ValidationError('Beneficiary must be at least 60 years old.')
