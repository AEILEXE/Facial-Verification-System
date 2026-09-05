import datetime

from django.core.exceptions import ValidationError


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
