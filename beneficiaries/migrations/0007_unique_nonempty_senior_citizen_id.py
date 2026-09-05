from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Adds a partial unique index on Beneficiary.senior_citizen_id for non-empty values.

    Without this constraint, the application-level duplicate check in
    register_submit_face() has a TOCTOU race: two staff stations registering
    concurrently can both pass the .exists() check and create two beneficiary
    records with the same Senior Citizen ID number.

    A partial index (condition: senior_citizen_id > '') allows any number of
    beneficiaries to have an empty/blank senior_citizen_id (the field is
    optional) while still enforcing uniqueness for all non-empty values at the
    database level.

    PostgreSQL, SQLite 3.9+, and MariaDB 10.5+ all support partial/conditional
    unique indexes.  Django translates UniqueConstraint(condition=...) to the
    appropriate DDL for each backend.
    """

    dependencies = [
        ('beneficiaries', '0006_beneficiary_sync_fields'),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='beneficiary',
            constraint=models.UniqueConstraint(
                fields=['senior_citizen_id'],
                condition=models.Q(senior_citizen_id__gt=''),
                name='unique_nonempty_senior_citizen_id',
            ),
        ),
    ]
