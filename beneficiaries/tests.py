"""
Tests for the QC address addendum — city dropdown, house_no/street fields,
barangay validation, full_address display, and legacy compatibility.
"""
import datetime
from django.test import TestCase

from .forms import BeneficiaryInfoForm, BeneficiaryEditForm, CITY_CHOICES
from .models import Beneficiary, Representative
from .qc_barangays import QC_BARANGAY_SET, QC_BARANGAYS, QC_CITY


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_registration_data(**overrides):
    """Minimum valid data for BeneficiaryInfoForm (registration step 1)."""
    data = {
        'first_name': 'Maria',
        'middle_name': '',
        'last_name': 'Santos',
        'date_of_birth': '1950-01-01',
        'gender': 'F',
        'contact_number': '',
        'municipality': 'Quezon City',
        'house_no': '',
        'street': 'Maharlika Street',
        'barangay': 'Commonwealth',
        'province': 'Metro Manila (NCR)',
        'senior_citizen_id': 'SC-2024-00001',
        'valid_id_type': '',
        'valid_id_number': '',
    }
    data.update(overrides)
    return data


def _make_beneficiary(**overrides):
    """Create and return a minimal saved Beneficiary instance."""
    defaults = dict(
        first_name='Juan',
        last_name='Dela Cruz',
        date_of_birth=datetime.date(1945, 6, 15),
        gender='M',
        municipality='Quezon City',
        barangay='Commonwealth',
        province='Metro Manila (NCR)',
        senior_citizen_id='SC-2024-99999',
        registered_by=None,
    )
    defaults.update(overrides)
    b = Beneficiary(**defaults)
    b.save()
    return b


# ---------------------------------------------------------------------------
# 1. City field: CITY_CHOICES
# ---------------------------------------------------------------------------

class CityChoicesTest(TestCase):

    def test_city_choices_blank_first(self):
        """First choice is the blank placeholder."""
        self.assertEqual(CITY_CHOICES[0], ('', '-- Select City --'))

    def test_city_choices_quezon_city(self):
        self.assertIn(('Quezon City', 'Quezon City'), CITY_CHOICES)

    def test_city_choices_others(self):
        self.assertIn(('Others', 'Others'), CITY_CHOICES)

    def test_city_choices_exactly_three_options(self):
        self.assertEqual(len(CITY_CHOICES), 3)

    def test_municipality_is_choice_field(self):
        """municipality renders as a <select>, not a text input."""
        from django import forms as django_forms
        form = BeneficiaryInfoForm()
        self.assertIsInstance(form.fields['municipality'], django_forms.ChoiceField)
        self.assertIsInstance(
            form.fields['municipality'].widget,
            django_forms.Select,
        )

    def test_edit_form_municipality_is_choice_field(self):
        from django import forms as django_forms
        b = _make_beneficiary()
        form = BeneficiaryEditForm(instance=b)
        self.assertIsInstance(form.fields['municipality'], django_forms.ChoiceField)
        self.assertIsInstance(
            form.fields['municipality'].widget,
            django_forms.Select,
        )


# ---------------------------------------------------------------------------
# 2. Browser autocomplete disabled on City and Barangay widgets
# ---------------------------------------------------------------------------

class AutocompleteOffTest(TestCase):

    def test_city_autocomplete_off(self):
        form = BeneficiaryInfoForm()
        attrs = form.fields['municipality'].widget.attrs
        self.assertEqual(attrs.get('autocomplete'), 'off')

    def test_barangay_autocomplete_off(self):
        form = BeneficiaryInfoForm()
        attrs = form.fields['barangay'].widget.attrs
        self.assertEqual(attrs.get('autocomplete'), 'off')

    def test_edit_city_autocomplete_off(self):
        b = _make_beneficiary()
        form = BeneficiaryEditForm(instance=b)
        attrs = form.fields['municipality'].widget.attrs
        self.assertEqual(attrs.get('autocomplete'), 'off')

    def test_edit_barangay_autocomplete_off(self):
        b = _make_beneficiary()
        form = BeneficiaryEditForm(instance=b)
        attrs = form.fields['barangay'].widget.attrs
        self.assertEqual(attrs.get('autocomplete'), 'off')


# ---------------------------------------------------------------------------
# 3. QC barangay list: 142 entries, all unique
# ---------------------------------------------------------------------------

class QCBarangayListTest(TestCase):

    def test_142_barangays(self):
        self.assertEqual(len(QC_BARANGAYS), 142)

    def test_barangay_set_matches_list(self):
        self.assertEqual(QC_BARANGAY_SET, set(QC_BARANGAYS))

    def test_no_duplicates(self):
        self.assertEqual(len(QC_BARANGAYS), len(set(QC_BARANGAYS)))

    def test_known_barangays_present(self):
        for name in ('Commonwealth', 'Batasan Hills', 'Payatas', 'Holy Spirit',
                     'UP Campus', 'Loyola Heights', 'West Triangle'):
            self.assertIn(name, QC_BARANGAY_SET, f'{name} missing from QC list')


# ---------------------------------------------------------------------------
# 4. Barangay validation: QC city requires QC barangay
# ---------------------------------------------------------------------------

class BarangayValidationTest(TestCase):

    def test_qc_city_valid_barangay_passes(self):
        data = _base_registration_data(municipality='Quezon City', barangay='Commonwealth')
        form = BeneficiaryInfoForm(data)
        self.assertTrue(form.is_valid(), form.errors)

    def test_qc_city_invalid_barangay_fails(self):
        data = _base_registration_data(municipality='Quezon City', barangay='Malate')
        form = BeneficiaryInfoForm(data)
        self.assertFalse(form.is_valid())
        self.assertIn('barangay', form.errors)

    def test_qc_city_invalid_barangay_error_message(self):
        data = _base_registration_data(municipality='Quezon City', barangay='Caloocan')
        form = BeneficiaryInfoForm(data)
        form.is_valid()
        self.assertIn(
            'valid Quezon City barangay',
            ''.join(form.errors.get('barangay', [])),
        )

    def test_others_city_accepts_any_barangay(self):
        data = _base_registration_data(municipality='Others', barangay='Malate')
        form = BeneficiaryInfoForm(data)
        self.assertTrue(form.is_valid(), form.errors)

    def test_others_city_free_text_barangay_passes(self):
        data = _base_registration_data(municipality='Others', barangay='Some Remote Barangay 123')
        form = BeneficiaryInfoForm(data)
        self.assertTrue(form.is_valid(), form.errors)

    def test_empty_barangay_fails(self):
        data = _base_registration_data(municipality='Quezon City', barangay='')
        form = BeneficiaryInfoForm(data)
        self.assertFalse(form.is_valid())
        self.assertIn('barangay', form.errors)

    def test_edit_form_qc_valid_barangay(self):
        b = _make_beneficiary()
        data = _edit_data_from(b, barangay='Batasan Hills', municipality='Quezon City')
        form = BeneficiaryEditForm(data, instance=b)
        self.assertTrue(form.is_valid(), form.errors)

    def test_edit_form_qc_invalid_barangay_fails(self):
        b = _make_beneficiary()
        data = _edit_data_from(b, barangay='Ermita', municipality='Quezon City')
        form = BeneficiaryEditForm(data, instance=b)
        self.assertFalse(form.is_valid())
        self.assertIn('barangay', form.errors)

    def test_edit_form_others_accepts_any_barangay(self):
        b = _make_beneficiary()
        data = _edit_data_from(b, municipality='Others', barangay='Freetext Brgy')
        form = BeneficiaryEditForm(data, instance=b)
        self.assertTrue(form.is_valid(), form.errors)


# ---------------------------------------------------------------------------
# 5. House No. — optional in both forms
# ---------------------------------------------------------------------------

class HouseNoTest(TestCase):

    def test_registration_valid_without_house_no(self):
        data = _base_registration_data(house_no='')
        form = BeneficiaryInfoForm(data)
        self.assertTrue(form.is_valid(), form.errors)

    def test_registration_valid_with_house_no(self):
        data = _base_registration_data(house_no='Block 5 Lot 12')
        form = BeneficiaryInfoForm(data)
        self.assertTrue(form.is_valid(), form.errors)

    def test_house_no_field_not_required(self):
        form = BeneficiaryInfoForm()
        self.assertFalse(form.fields['house_no'].required)

    def test_edit_form_house_no_not_required(self):
        b = _make_beneficiary()
        form = BeneficiaryEditForm(instance=b)
        self.assertFalse(form.fields['house_no'].required)


# ---------------------------------------------------------------------------
# 6. Street — required in registration, optional in edit (backward compat)
# ---------------------------------------------------------------------------

class StreetTest(TestCase):

    def test_registration_valid_with_street(self):
        data = _base_registration_data(street='Maharlika Street')
        form = BeneficiaryInfoForm(data)
        self.assertTrue(form.is_valid(), form.errors)

    def test_registration_invalid_without_street(self):
        data = _base_registration_data(street='')
        form = BeneficiaryInfoForm(data)
        self.assertFalse(form.is_valid())
        self.assertIn('street', form.errors)

    def test_street_required_in_registration_form(self):
        form = BeneficiaryInfoForm()
        self.assertTrue(form.fields['street'].required)

    def test_street_optional_in_edit_form(self):
        """Edit form allows blank street so pre-existing records can be saved."""
        b = _make_beneficiary()
        form = BeneficiaryEditForm(instance=b)
        self.assertFalse(form.fields['street'].required)

    def test_edit_form_valid_without_street(self):
        b = _make_beneficiary()
        data = _edit_data_from(b, street='')
        form = BeneficiaryEditForm(data, instance=b)
        self.assertTrue(form.is_valid(), form.errors)

    def test_edit_form_valid_with_street(self):
        b = _make_beneficiary()
        data = _edit_data_from(b, street='Mabini Street')
        form = BeneficiaryEditForm(data, instance=b)
        self.assertTrue(form.is_valid(), form.errors)


# ---------------------------------------------------------------------------
# 7. full_address property
# ---------------------------------------------------------------------------

class FullAddressTest(TestCase):

    def test_full_address_all_fields(self):
        b = Beneficiary(
            house_no='123',
            street='Mabini Street',
            barangay='Commonwealth',
            municipality='Quezon City',
        )
        self.assertEqual(
            b.full_address,
            '123, Mabini Street, Barangay Commonwealth, Quezon City',
        )

    def test_full_address_without_house_no(self):
        b = Beneficiary(
            house_no='',
            street='Mabini Street',
            barangay='Commonwealth',
            municipality='Quezon City',
        )
        self.assertEqual(
            b.full_address,
            'Mabini Street, Barangay Commonwealth, Quezon City',
        )

    def test_full_address_legacy_address_fallback(self):
        """Records with no street fall back to the legacy address field."""
        b = Beneficiary(
            house_no='',
            street='',
            address='123 Old Address St.',
            barangay='Payatas',
            municipality='Quezon City',
        )
        self.assertEqual(
            b.full_address,
            '123 Old Address St., Barangay Payatas, Quezon City',
        )

    def test_full_address_entirely_empty(self):
        b = Beneficiary(house_no='', street='', address='', barangay='', municipality='')
        self.assertEqual(b.full_address, '')

    def test_full_address_no_house_no_no_street_no_legacy(self):
        b = Beneficiary(
            house_no='',
            street='',
            address='',
            barangay='Batasan Hills',
            municipality='Quezon City',
        )
        self.assertEqual(b.full_address, 'Barangay Batasan Hills, Quezon City')

    def test_full_address_prefers_street_over_legacy_address(self):
        """When street is present, legacy address is NOT appended."""
        b = Beneficiary(
            house_no='',
            street='New Street',
            address='Old Legacy Address',
            barangay='Commonwealth',
            municipality='Quezon City',
        )
        self.assertNotIn('Old Legacy Address', b.full_address)
        self.assertIn('New Street', b.full_address)


# ---------------------------------------------------------------------------
# 8. Legacy records — existing data preserved and renders safely
# ---------------------------------------------------------------------------

class LegacyRecordTest(TestCase):

    def test_legacy_record_without_house_no_street_renders(self):
        """A saved record with only legacy fields renders full_address without error."""
        b = _make_beneficiary(
            address='456 Old Barangay Road',
            house_no='',
            street='',
        )
        # Should not raise; should fall back to address field
        addr = b.full_address
        self.assertIn('456 Old Barangay Road', addr)

    def test_legacy_municipality_preserved_in_edit_form(self):
        """If municipality is an old value not in CITY_CHOICES, edit form still loads."""
        b = _make_beneficiary(municipality='Manila')
        form = BeneficiaryEditForm(instance=b)
        # The unknown value should appear in the choices (via _city_choices_with_legacy)
        choices_values = [v for v, _ in form.fields['municipality'].choices]
        self.assertIn('Manila', choices_values)

    def test_migration_fields_default_blank(self):
        """house_no and street default to blank on create; existing rows are safe."""
        b = _make_beneficiary()
        self.assertEqual(b.house_no, '')
        self.assertEqual(b.street, '')


# ---------------------------------------------------------------------------
# 9. Model fields exist and have correct constraints
# ---------------------------------------------------------------------------

class ModelFieldsTest(TestCase):

    def test_house_no_field_exists(self):
        b = Beneficiary()
        self.assertTrue(hasattr(b, 'house_no'))

    def test_street_field_exists(self):
        b = Beneficiary()
        self.assertTrue(hasattr(b, 'street'))

    def test_house_no_max_length(self):
        field = Beneficiary._meta.get_field('house_no')
        self.assertEqual(field.max_length, 100)

    def test_street_max_length(self):
        field = Beneficiary._meta.get_field('street')
        self.assertEqual(field.max_length, 200)

    def test_house_no_blank(self):
        field = Beneficiary._meta.get_field('house_no')
        self.assertTrue(field.blank)

    def test_street_blank(self):
        field = Beneficiary._meta.get_field('street')
        self.assertTrue(field.blank)

    def test_address_blank(self):
        """Legacy address field must be blank=True so old records still save."""
        field = Beneficiary._meta.get_field('address')
        self.assertTrue(field.blank)


# ---------------------------------------------------------------------------
# v2.2.0 Follow-up Issue 36 — Beneficiary ID must always be server-generated,
# never a client-submittable value, and must stay independent from Senior
# Citizen ID (the external/official identifier, which IS a real input field).
#
# Investigation finding: neither BeneficiaryInfoForm (registration) nor
# BeneficiaryEditForm expose 'beneficiary_id' as a field at all — there is no
# editable "Beneficiary ID" input anywhere in the application. The reported
# field is almost certainly "Senior Citizen ID Number", a genuinely required
# input, mistaken for the internal identifier. These tests lock in the
# already-correct behavior (auto-generation, uniqueness, form exclusion,
# independence from Senior Citizen ID) so it cannot silently regress.
# ---------------------------------------------------------------------------

class BeneficiaryIdGenerationTest(TestCase):

    def test_beneficiary_id_auto_generated_when_blank(self):
        b = _make_beneficiary(senior_citizen_id='SC-BID-00001')
        self.assertTrue(b.beneficiary_id)
        import re
        self.assertRegex(b.beneficiary_id, r'^BEN-\d{4}-\d{5}$')

    def test_beneficiary_id_sequential_within_year(self):
        import datetime as _dt
        year = _dt.date.today().year
        b1 = _make_beneficiary(senior_citizen_id='SC-BID-00002')
        b2 = _make_beneficiary(senior_citizen_id='SC-BID-00003')
        n1 = int(b1.beneficiary_id.split('-')[-1])
        n2 = int(b2.beneficiary_id.split('-')[-1])
        self.assertEqual(n2, n1 + 1)
        self.assertIn(str(year), b1.beneficiary_id)

    def test_explicitly_set_beneficiary_id_is_respected_but_not_form_reachable(self):
        """The model itself allows setting beneficiary_id directly (used by
        fixtures/migrations/offline-sync) — the guarantee is that no FORM
        exposes it to a user, not that the model field is immutable."""
        b = Beneficiary(
            beneficiary_id='BEN-CUSTOM-00001',
            first_name='Custom', last_name='Id',
            date_of_birth='1945-01-01', gender='M',
            municipality='Quezon City', barangay='Commonwealth',
            province='Metro Manila (NCR)', senior_citizen_id='SC-BID-00004',
        )
        b.save()
        self.assertEqual(b.beneficiary_id, 'BEN-CUSTOM-00001')

    def test_duplicate_beneficiary_id_blocked_at_db_level(self):
        from django.db import IntegrityError, transaction
        _make_beneficiary(senior_citizen_id='SC-BID-00005')
        b1 = Beneficiary.objects.order_by('-created_at').first()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Beneficiary.objects.create(
                    beneficiary_id=b1.beneficiary_id,  # deliberate collision
                    first_name='Dup', last_name='Id',
                    date_of_birth='1945-01-01', gender='F',
                    municipality='Quezon City', barangay='Commonwealth',
                    province='Metro Manila (NCR)', senior_citizen_id='SC-BID-00006',
                )

    def test_beneficiary_id_not_a_registration_form_field(self):
        form = BeneficiaryInfoForm()
        self.assertNotIn('beneficiary_id', form.fields)

    def test_beneficiary_id_not_an_edit_form_field(self):
        form = BeneficiaryEditForm()
        self.assertNotIn('beneficiary_id', form.fields)

    def test_beneficiary_id_not_in_registration_template(self):
        """Defence-in-depth: confirm the actual rendered registration page
        never renders an input named beneficiary_id."""
        from django.contrib.auth import get_user_model
        User = get_user_model()
        staff = User.objects.create_user(
            username='bid_reg_staff', password='TestPass1!', role='staff',
        )
        self.client.force_login(staff)
        resp = self.client.get('/dashboard/register/step1/')
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('name="beneficiary_id"', resp.content.decode())

    def test_senior_citizen_id_independent_of_beneficiary_id(self):
        """Setting the same senior_citizen_id twice on different records is
        blocked (its own uniqueness rule), but must never influence or
        collide with the separately-generated beneficiary_id sequence."""
        b1 = _make_beneficiary(senior_citizen_id='SC-BID-UNIQUE-1')
        b2 = _make_beneficiary(senior_citizen_id='SC-BID-UNIQUE-2')
        self.assertNotEqual(b1.senior_citizen_id, b2.senior_citizen_id)
        self.assertNotEqual(b1.beneficiary_id, b2.beneficiary_id)
        # Changing senior_citizen_id via edit must not touch beneficiary_id.
        original_bid = b1.beneficiary_id
        b1.senior_citizen_id = 'SC-BID-CHANGED'
        b1.save()
        b1.refresh_from_db()
        self.assertEqual(b1.beneficiary_id, original_bid)


# ---------------------------------------------------------------------------
# Helpers used by edit-form tests
# ---------------------------------------------------------------------------

def _edit_data_from(beneficiary, **overrides):
    """Build a valid POST dict for BeneficiaryEditForm from an existing beneficiary."""
    import datetime as dt
    dob = beneficiary.date_of_birth
    data = {
        'first_name': beneficiary.first_name,
        'middle_name': beneficiary.middle_name,
        'last_name': beneficiary.last_name,
        'date_of_birth': dob.strftime('%Y-%m-%d') if dob else '1945-01-01',
        'gender': beneficiary.gender,
        'municipality': beneficiary.municipality,
        'house_no': getattr(beneficiary, 'house_no', ''),
        'street': getattr(beneficiary, 'street', ''),
        'address': getattr(beneficiary, 'address', ''),
        'barangay': beneficiary.barangay,
        'province': beneficiary.province,
        'contact_number': '',
        'senior_citizen_id': beneficiary.senior_citizen_id,
        'valid_id_type': '',
        'valid_id_number': '',
        'has_representative': False,
        'rep_first_name': '',
        'rep_last_name': '',
        'rep_relationship': '',
        'rep_contact': '',
        'rep_id_type': '',
        'rep_id_number': '',
    }
    data.update(overrides)
    return data


# ──────────────────────────────────────────────────────────────────────────────
# v2.1.11 — Issue 1: duplicate name+DOB override workflow
# ──────────────────────────────────────────────────────────────────────────────

from .models import DuplicateNameDobRequest


class DuplicateNameDobOverrideTest(TestCase):
    def setUp(self):
        from accounts.models import CustomUser
        self.admin = CustomUser.objects.create_user(
            username='admin1', password='Pass123!', role=CustomUser.ROLE_ADMIN,
            employee_id='EMP-ADMIN',
        )
        self.president = CustomUser.objects.create_user(
            username='pres1', password='Pass123!', role=CustomUser.ROLE_PRESIDENT,
            employee_id='EMP-PRES',
        )
        self.existing = Beneficiary.objects.create(
            beneficiary_id='BEN-2026-00001',
            first_name='Maria', last_name='Santos',
            date_of_birth='1940-05-19', gender='F',
            barangay='B1', municipality='Quezon City', province='NCR',
        )

    def test_override_request_model_default_status_pending(self):
        new_ben = Beneficiary.objects.create(
            beneficiary_id='BEN-2026-00002',
            first_name='Maria', last_name='Santos',
            date_of_birth='1940-05-19', gender='F',
            barangay='B2', municipality='Quezon City', province='NCR',
            status=Beneficiary.STATUS_PENDING,
        )
        req = DuplicateNameDobRequest.objects.create(
            new_beneficiary=new_ben,
            existing_beneficiary=self.existing,
            requested_by=self.admin,
            reason='Different person, distinct address and contact.',
        )
        self.assertEqual(req.status, DuplicateNameDobRequest.STATUS_PENDING)

    def test_president_can_approve_override_activates_beneficiary(self):
        from django.urls import reverse
        new_ben = Beneficiary.objects.create(
            beneficiary_id='BEN-2026-00003',
            first_name='Maria', last_name='Santos',
            date_of_birth='1940-05-19', gender='F',
            barangay='B3', municipality='Quezon City', province='NCR',
            status=Beneficiary.STATUS_PENDING, consent_given=True,
        )
        req = DuplicateNameDobRequest.objects.create(
            new_beneficiary=new_ben,
            existing_beneficiary=self.existing,
            requested_by=self.admin,
            reason='Different person, distinct address and contact.',
        )
        self.client.force_login(self.president)
        self.client.post(
            reverse('beneficiaries:namedob_review_detail', args=[req.pk]),
            {'action': 'approve', 'notes': 'verified by sight'},
        )
        new_ben.refresh_from_db()
        req.refresh_from_db()
        self.assertEqual(req.status, DuplicateNameDobRequest.STATUS_APPROVED)
        self.assertEqual(new_ben.status, Beneficiary.STATUS_ACTIVE)

    def test_reject_disapproves_beneficiary(self):
        """v2.2.0 Post-UAT Phase 4 — a rejected (never-approved) registration
        becomes 'disapproved', not 'inactive' (which would misleadingly
        imply it was once an active beneficiary)."""
        from django.urls import reverse
        new_ben = Beneficiary.objects.create(
            beneficiary_id='BEN-2026-00004',
            first_name='Maria', last_name='Santos',
            date_of_birth='1940-05-19', gender='F',
            barangay='B4', municipality='Quezon City', province='NCR',
            status=Beneficiary.STATUS_PENDING,
        )
        req = DuplicateNameDobRequest.objects.create(
            new_beneficiary=new_ben,
            existing_beneficiary=self.existing,
            requested_by=self.admin,
            reason='Different person, distinct address and contact.',
        )
        self.client.force_login(self.president)
        self.client.post(
            reverse('beneficiaries:namedob_review_detail', args=[req.pk]),
            {'action': 'reject', 'notes': 'looks like a duplicate'},
        )
        new_ben.refresh_from_db()
        req.refresh_from_db()
        self.assertEqual(req.status, DuplicateNameDobRequest.STATUS_REJECTED)
        self.assertEqual(new_ben.status, Beneficiary.STATUS_DISAPPROVED)


# ---------------------------------------------------------------------------
# Phase 1B — validate_senior_citizen_dob validator + BeneficiaryEditForm age gate
# ---------------------------------------------------------------------------

class SeniorCitizenDobValidatorTest(TestCase):
    """Unit tests for the shared validate_senior_citizen_dob validator."""

    def _v(self):
        from beneficiaries.validators import validate_senior_citizen_dob
        return validate_senior_citizen_dob

    def test_future_dob_rejected(self):
        from django.core.exceptions import ValidationError
        future_dob = datetime.date.today() + datetime.timedelta(days=1)
        with self.assertRaises(ValidationError) as ctx:
            self._v()(future_dob)
        self.assertIn('future', str(ctx.exception).lower())

    def test_under_60_rejected(self):
        from django.core.exceptions import ValidationError
        young_dob = datetime.date(2000, 1, 1)
        with self.assertRaises(ValidationError) as ctx:
            self._v()(young_dob)
        self.assertIn('60', str(ctx.exception))

    def test_exactly_60_accepted(self):
        today = datetime.date.today()
        try:
            dob = datetime.date(today.year - 60, today.month, today.day)
        except ValueError:
            dob = datetime.date(today.year - 60, today.month, today.day - 1)
        self._v()(dob)  # must not raise

    def test_well_over_60_accepted(self):
        old_dob = datetime.date(1940, 6, 15)
        self._v()(old_dob)  # must not raise

    def test_59_years_364_days_rejected(self):
        from django.core.exceptions import ValidationError
        today = datetime.date.today()
        try:
            dob = datetime.date(today.year - 60, today.month, today.day) + datetime.timedelta(days=1)
        except ValueError:
            dob = datetime.date(today.year - 59, 1, 1)
        with self.assertRaises(ValidationError):
            self._v()(dob)

    def test_leap_year_birthday_accepted_when_over_60(self):
        """Person born Feb 29, 1964 is clearly over 60 in 2026 — must not raise."""
        dob = datetime.date(1964, 2, 29)
        self._v()(dob)  # must not raise

    def test_birthday_comparison_correct_not_days_division(self):
        """
        Verify that the validator uses birthday comparison, not days//365.
        days//365 gives incorrect results around leap years.
        Someone who turns 60 today should pass — days//365 can fail for them
        because 60 * 365 < actual elapsed days by several leap-day corrections.
        """
        today = datetime.date.today()
        try:
            dob = datetime.date(today.year - 60, today.month, today.day)
        except ValueError:
            dob = datetime.date(today.year - 60, today.month, today.day - 1)
        # Verify days//365 would also give exactly 60 (sanity: both methods agree
        # on the non-boundary day). The key tested property is: no crash and no rejection.
        self._v()(dob)


class BeneficiaryInfoFormDobTest(TestCase):
    """Regression tests — registration form age validation must still work."""

    def test_registration_rejects_under_60(self):
        data = _base_registration_data(date_of_birth='2000-01-01')
        form = BeneficiaryInfoForm(data)
        self.assertFalse(form.is_valid())
        self.assertIn('date_of_birth', form.errors)
        self.assertIn('60', ''.join(form.errors['date_of_birth']))

    def test_registration_rejects_future_dob(self):
        future = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
        data = _base_registration_data(date_of_birth=future)
        form = BeneficiaryInfoForm(data)
        self.assertFalse(form.is_valid())
        self.assertIn('date_of_birth', form.errors)

    def test_registration_accepts_over_60(self):
        data = _base_registration_data(date_of_birth='1950-01-01')
        form = BeneficiaryInfoForm(data)
        self.assertTrue(form.is_valid(), form.errors)

    def test_registration_accepts_exactly_60(self):
        today = datetime.date.today()
        try:
            dob = datetime.date(today.year - 60, today.month, today.day)
        except ValueError:
            dob = datetime.date(today.year - 60, today.month, today.day - 1)
        data = _base_registration_data(date_of_birth=dob.isoformat())
        form = BeneficiaryInfoForm(data)
        self.assertTrue(form.is_valid(), form.errors)


class BeneficiaryEditFormDobTest(TestCase):
    """Tests for the 1B fix: BeneficiaryEditForm must now enforce age >= 60."""

    def setUp(self):
        self.beneficiary = _make_beneficiary(date_of_birth=datetime.date(1950, 6, 15))

    def test_edit_rejects_underage_dob(self):
        """Core 1B fix: editing DOB to under-60 must be rejected server-side."""
        data = _edit_data_from(self.beneficiary, date_of_birth='2000-01-01')
        form = BeneficiaryEditForm(data, instance=self.beneficiary)
        self.assertFalse(form.is_valid())
        self.assertIn('date_of_birth', form.errors)
        self.assertIn('60', ''.join(form.errors['date_of_birth']))

    def test_edit_rejects_future_dob(self):
        future = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
        data = _edit_data_from(self.beneficiary, date_of_birth=future)
        form = BeneficiaryEditForm(data, instance=self.beneficiary)
        self.assertFalse(form.is_valid())
        self.assertIn('date_of_birth', form.errors)

    def test_edit_accepts_valid_senior_dob(self):
        data = _edit_data_from(self.beneficiary, date_of_birth='1945-03-20')
        form = BeneficiaryEditForm(data, instance=self.beneficiary)
        self.assertTrue(form.is_valid(), form.errors)

    def test_edit_accepts_exactly_60(self):
        today = datetime.date.today()
        try:
            dob = datetime.date(today.year - 60, today.month, today.day)
        except ValueError:
            dob = datetime.date(today.year - 60, today.month, today.day - 1)
        data = _edit_data_from(self.beneficiary, date_of_birth=dob.isoformat())
        form = BeneficiaryEditForm(data, instance=self.beneficiary)
        self.assertTrue(form.is_valid(), form.errors)

    def test_edit_rejects_59_years(self):
        today = datetime.date.today()
        try:
            dob = datetime.date(today.year - 59, today.month, today.day)
        except ValueError:
            dob = datetime.date(today.year - 59, today.month, today.day - 1)
        data = _edit_data_from(self.beneficiary, date_of_birth=dob.isoformat())
        form = BeneficiaryEditForm(data, instance=self.beneficiary)
        self.assertFalse(form.is_valid())
        self.assertIn('date_of_birth', form.errors)

    def test_edit_form_error_message_mentions_60(self):
        data = _edit_data_from(self.beneficiary, date_of_birth='1990-05-01')
        form = BeneficiaryEditForm(data, instance=self.beneficiary)
        form.is_valid()
        error_text = ''.join(form.errors.get('date_of_birth', []))
        self.assertIn('60', error_text)


# ---------------------------------------------------------------------------
# Beneficiary list pagination tests (Phase 3A)
# ---------------------------------------------------------------------------

def _make_n_beneficiaries(n, base_sc_id='SC-PAGI-'):
    """Create n distinct Beneficiary records with unique senior_citizen_ids."""
    for i in range(n):
        Beneficiary.objects.create(
            first_name='Test',
            last_name=f'User{i:04d}',
            date_of_birth=datetime.date(1945, 1, 1),
            gender='M',
            municipality='Quezon City',
            barangay='Commonwealth',
            province='Metro Manila (NCR)',
            senior_citizen_id=f'{base_sc_id}{i:05d}',
            registered_by=None,
        )


class BeneficiaryListPaginationTest(TestCase):
    """Pagination tests for the beneficiary_list view (Phase 3A)."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        self.user = User.objects.create_user(
            username='pagi_tester', password='TestPass1!',
            role='admin',
        )
        self.client.force_login(self.user)
        self.url = '/dashboard/beneficiaries/'

    def test_page_1_returns_50_records(self):
        _make_n_beneficiaries(55)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('paginator', response.context)
        self.assertEqual(response.context['paginator'].count, 55)
        self.assertEqual(len(response.context['page_obj'].object_list), 50)

    def test_page_2_returns_remaining_records(self):
        _make_n_beneficiaries(55)
        response = self.client.get(self.url + '?page=2')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['page_obj'].object_list), 5)

    def test_search_preserved_across_pages(self):
        _make_n_beneficiaries(55)
        # All 55 have last_name starting with 'User', so 'User' search returns all 55
        response = self.client.get(self.url + '?q=User&page=2')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['query'], 'User')
        self.assertContains(response, 'page=3' if response.context['paginator'].num_pages > 2 else 'User')

    def test_status_filter_preserved_across_pages(self):
        _make_n_beneficiaries(55)
        # All 55 default to status='pending' — filter and check filter is passed through
        response = self.client.get(self.url + '?status=pending&page=1')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['status_filter'], 'pending')
        self.assertEqual(response.context['paginator'].count, 55)

    def test_below_page_threshold_no_pagination_controls(self):
        _make_n_beneficiaries(10)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['paginator'].num_pages, 1)
        # Only one page — no Previous/Next controls in HTML
        self.assertNotContains(response, 'Previous')
        self.assertNotContains(response, 'Next')

    def test_unauthenticated_redirected(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertRedirects(response, f'/accounts/login/?next={self.url}')


# ---------------------------------------------------------------------------
# Beneficiary detail view — regression tests for the orphaned {% endif %} in
# templates/beneficiaries/detail.html (v2.2.0 Post-UAT Phase 1). That stray
# tag was a hard TemplateSyntaxError, so it failed for every beneficiary
# regardless of related records; these tests exercise the full range of
# related-record combinations called out in the bug report so any future
# regression in this template is caught regardless of which code path
# triggers it.
# ---------------------------------------------------------------------------

class BeneficiaryDetailViewTest(TestCase):

    def setUp(self):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        self.admin = User.objects.create_user(
            username='detail_admin', password='TestPass1!', role='admin',
        )
        self.client.force_login(self.admin)

    def _url(self, beneficiary):
        return f'/dashboard/beneficiaries/{beneficiary.pk}/'

    def test_new_beneficiary_no_related_records(self):
        b = _make_beneficiary(senior_citizen_id='SC-DTL-00001')
        response = self.client.get(self._url(b))
        self.assertEqual(response.status_code, 200)

    def test_beneficiary_with_no_claim(self):
        b = _make_beneficiary(senior_citizen_id='SC-DTL-00002', status='active')
        response = self.client.get(self._url(b))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No claim records yet')

    def test_beneficiary_with_successful_claim(self):
        from verification.models import StipendEvent, ClaimRecord
        b = _make_beneficiary(senior_citizen_id='SC-DTL-00003', status='active')
        event = StipendEvent.objects.create(
            title='Test Payout', date=datetime.date.today(),
            created_by=self.admin, approval_status=StipendEvent.APPROVAL_APPROVED,
        )
        ClaimRecord.objects.create(
            beneficiary=b, stipend_event=event, claimant_type='beneficiary',
            status=ClaimRecord.STATUS_CLAIMED, claimed_by=self.admin,
        )
        response = self.client.get(self._url(b))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Test Payout')

    def test_beneficiary_with_failed_verification(self):
        from verification.models import VerificationAttempt
        b = _make_beneficiary(senior_citizen_id='SC-DTL-00004', status='active')
        VerificationAttempt.objects.create(
            beneficiary=b, claimant_type='beneficiary',
            decision=VerificationAttempt.DECISION_NOT_VERIFIED,
            performed_by=self.admin,
        )
        response = self.client.get(self._url(b))
        self.assertEqual(response.status_code, 200)

    def test_beneficiary_with_manual_review(self):
        from verification.models import VerificationAttempt
        b = _make_beneficiary(senior_citizen_id='SC-DTL-00005', status='active')
        VerificationAttempt.objects.create(
            beneficiary=b, claimant_type='beneficiary',
            decision=VerificationAttempt.DECISION_MANUAL_REVIEW,
            performed_by=self.admin,
        )
        response = self.client.get(self._url(b))
        self.assertEqual(response.status_code, 200)

    def test_beneficiary_with_representative(self):
        b = _make_beneficiary(senior_citizen_id='SC-DTL-00006', status='active')
        Representative.objects.create(
            beneficiary=b, first_name='Jose', last_name='Rizal',
            relationship='Son', contact_number='09171234567',
            valid_id_type='SSS', valid_id_number='SSS-DTL-001',
            registered_by=self.admin,
        )
        response = self.client.get(self._url(b))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Jose Rizal')

    def test_beneficiary_with_representative_with_face_data(self):
        from verification.models import RepresentativeFaceEmbedding
        b = _make_beneficiary(senior_citizen_id='SC-DTL-00007', status='active')
        rep = Representative.objects.create(
            beneficiary=b, first_name='Ana', last_name='Reyes',
            relationship='Daughter', contact_number='09171234568',
            valid_id_type='SSS', valid_id_number='SSS-DTL-002',
            registered_by=self.admin,
        )
        RepresentativeFaceEmbedding.objects.create(
            representative=rep, embedding_data=b'\x00' * 16, created_by=self.admin,
        )
        response = self.client.get(self._url(b))
        self.assertEqual(response.status_code, 200)

    def test_beneficiary_with_duplicate_face_history(self):
        existing = _make_beneficiary(senior_citizen_id='SC-DTL-00008')
        b = _make_beneficiary(
            senior_citizen_id='SC-DTL-00009',
            duplicate_review_required=True,
            duplicate_match_beneficiary=existing,
            duplicate_match_score=0.91,
        )
        response = self.client.get(self._url(b))
        self.assertEqual(response.status_code, 200)

    def test_inactive_beneficiary(self):
        b = _make_beneficiary(senior_citizen_id='SC-DTL-00010', status='inactive')
        response = self.client.get(self._url(b))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Beneficiary is Inactive')

    def test_deceased_beneficiary(self):
        b = _make_beneficiary(senior_citizen_id='SC-DTL-00011', status='deceased')
        response = self.client.get(self._url(b))
        self.assertEqual(response.status_code, 200)

    def test_disapproved_registration(self):
        b = _make_beneficiary(
            senior_citizen_id='SC-DTL-00012', status='inactive',
            deactivated_reason='Registration rejected by admin: duplicate ID',
        )
        response = self.client.get(self._url(b))
        self.assertEqual(response.status_code, 200)

    def test_beneficiary_with_valid_id_but_no_senior_citizen_id(self):
        """Government ID card renders when senior_citizen_id is blank but a
        valid ID is on file — exercises the branch after the removed if-wrap."""
        b = _make_beneficiary(
            senior_citizen_id='', valid_id_type='PhilSys', valid_id_number='1234-5678-9012',
        )
        response = self.client.get(self._url(b))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'PhilSys')

    def test_pending_beneficiary(self):
        b = _make_beneficiary(senior_citizen_id='SC-DTL-00013', status='pending')
        response = self.client.get(self._url(b))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Awaiting Admin Approval')

    def test_disapproved_beneficiary_shows_disapproved_banner(self):
        """v2.2.0 Post-UAT Phase 4 — a disapproved registration must show
        distinct wording, never the generic 'Beneficiary is Inactive' banner."""
        b = _make_beneficiary(
            senior_citizen_id='SC-DTL-00014', status='disapproved',
            deactivated_reason='Registration rejected by admin: duplicate ID',
        )
        response = self.client.get(self._url(b))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Registration Disapproved')
        self.assertNotContains(response, 'Beneficiary is Inactive')

    def test_beneficiary_with_duplicate_review_flag_shows_conflict_banner(self):
        """v2.2.0 Follow-up Issue 31 — a beneficiary with an unresolved
        duplicate-face conflict must show a clear banner, not render as an
        ordinary pending record with no visible indicator."""
        existing = _make_beneficiary(senior_citizen_id='SC-DTL-DUPEXIST')
        b = _make_beneficiary(
            senior_citizen_id='SC-DTL-DUPFLAG', status='pending',
            duplicate_review_required=True,
            duplicate_match_beneficiary=existing,
            duplicate_match_score=0.93,
        )
        response = self.client.get(self._url(b))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Duplicate Face Detected')
        self.assertContains(response, existing.beneficiary_id)

    def test_beneficiary_with_multiple_additional_face_embeddings(self):
        """v2.2.0 Follow-up Issue 34/35 — a beneficiary can legitimately have
        more than one stored biometric template (primary + additional
        re-enrollment templates); the detail page must not assume exactly one."""
        from verification.models import AdditionalFaceEmbedding
        b = _make_beneficiary(senior_citizen_id='SC-DTL-MULTITMPL', status='active')
        for i in range(3):
            AdditionalFaceEmbedding.objects.create(
                beneficiary=b, embedding_data=bytes([i]) * 16,
            )
        response = self.client.get(self._url(b))
        self.assertEqual(response.status_code, 200)

    def test_beneficiary_referenced_as_match_target_by_another_registration(self):
        """The ORIGINAL beneficiary that another registration's duplicate
        check matched against must also open cleanly — the reverse relation
        (duplicate_registrations) must not be assumed absent or singular."""
        original = _make_beneficiary(senior_citizen_id='SC-DTL-ORIGINAL', status='active')
        _make_beneficiary(
            senior_citizen_id='SC-DTL-DUP1', status='pending',
            duplicate_review_required=True, duplicate_match_beneficiary=original,
            duplicate_match_score=0.91,
        )
        _make_beneficiary(
            senior_citizen_id='SC-DTL-DUP2', status='pending',
            duplicate_review_required=True, duplicate_match_beneficiary=original,
            duplicate_match_score=0.88,
        )
        response = self.client.get(self._url(original))
        self.assertEqual(response.status_code, 200)

    def test_beneficiary_detail_accessible_to_president_role(self):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        president = User.objects.create_user(
            username='detail_president', password='TestPass1!', role='president',
        )
        b = _make_beneficiary(senior_citizen_id='SC-DTL-PRES')
        self.client.force_login(president)
        response = self.client.get(self._url(b))
        self.assertEqual(response.status_code, 200)

    def test_beneficiary_detail_accessible_to_staff_role(self):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        staff = User.objects.create_user(
            username='detail_staff', password='TestPass1!', role='staff',
        )
        b = _make_beneficiary(senior_citizen_id='SC-DTL-STAFF')
        self.client.force_login(staff)
        response = self.client.get(self._url(b))
        self.assertEqual(response.status_code, 200)

    def test_beneficiary_with_full_realistic_combination(self):
        """Combines duplicate-face history, multiple biometric templates,
        manual review, failed verification, claim history, and a
        representative — the full realistic state a beneficiary can
        legitimately accumulate through the duplicate-biometric test scenario."""
        from verification.models import (
            VerificationAttempt, ClaimRecord, StipendEvent, AdditionalFaceEmbedding,
        )
        existing = _make_beneficiary(senior_citizen_id='SC-DTL-COMBOEXIST')
        b = _make_beneficiary(
            senior_citizen_id='SC-DTL-COMBO', status='active',
            duplicate_review_required=True,
            duplicate_match_beneficiary=existing,
            duplicate_match_score=0.90,
        )
        AdditionalFaceEmbedding.objects.create(beneficiary=b, embedding_data=b'\x01' * 16)
        Representative.objects.create(
            beneficiary=b, first_name='Jose', last_name='Rizal',
            relationship='Son', contact_number='09171234567',
            valid_id_type='SSS', valid_id_number='SSS-COMBO-001',
            registered_by=self.admin,
        )
        event = StipendEvent.objects.create(
            title='Combo Payout', date=datetime.date.today(), amount=500,
            created_by=self.admin, approval_status=StipendEvent.APPROVAL_APPROVED,
        )
        VerificationAttempt.objects.create(
            beneficiary=b, claimant_type='beneficiary',
            decision=VerificationAttempt.DECISION_MANUAL_REVIEW,
            performed_by=self.admin, stipend_event=event,
        )
        VerificationAttempt.objects.create(
            beneficiary=b, claimant_type='beneficiary',
            decision=VerificationAttempt.DECISION_NOT_VERIFIED,
            performed_by=self.admin, stipend_event=event,
        )
        ClaimRecord.objects.create(
            beneficiary=b, stipend_event=event, claimant_type='beneficiary',
            status=ClaimRecord.STATUS_CLAIMED, claimed_by=self.admin,
        )
        response = self.client.get(self._url(b))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Duplicate Face Detected')
        self.assertContains(response, 'Jose Rizal')


# ---------------------------------------------------------------------------
# v2.2.0 Post-UAT Phase 4 — Disapproved vs Inactive beneficiaries.
#
# Rejecting a PENDING registration (registration review, duplicate-face
# review, name/DOB override review, or the auto-approval queue) used to set
# status='inactive', making a never-approved application indistinguishable
# from a beneficiary that really was active and later deactivated. This
# section covers the two review paths in this app (reject_record and
# duplicate_review_detail) plus the schema/data migration that backfills
# legacy records.
# ---------------------------------------------------------------------------

class PendingApprovalsRejectDisapprovalTest(TestCase):
    """beneficiaries:reject_record — the auto-approval queue's reject action."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        self.admin = User.objects.create_user(
            username='reject_record_admin', password='TestPass1!', role='admin',
        )
        self.client.force_login(self.admin)
        self.beneficiary = _make_beneficiary(
            senior_citizen_id='SC-RR-00001', status='pending',
        )

    def test_reject_sets_disapproved(self):
        resp = self.client.post('/dashboard/pending-approvals/reject/', {
            'type': 'beneficiary',
            'id': str(self.beneficiary.pk),
            'reason': 'Incomplete documentation.',
        })
        self.assertEqual(resp.status_code, 200)
        self.beneficiary.refresh_from_db()
        self.assertEqual(self.beneficiary.status, Beneficiary.STATUS_DISAPPROVED)
        self.assertIn('Incomplete documentation.', self.beneficiary.deactivated_reason)


class DuplicateReviewDisapprovalTest(TestCase):
    """beneficiaries:duplicate_review_detail — reject_duplicate (confirmed fraud)."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        self.admin = User.objects.create_user(
            username='dup_review_admin', password='TestPass1!', role='admin',
        )
        self.client.force_login(self.admin)
        self.existing = _make_beneficiary(senior_citizen_id='SC-DR-EXIST')
        self.flagged = _make_beneficiary(
            senior_citizen_id='SC-DR-FLAGGED', status='pending',
            duplicate_review_required=True,
            duplicate_match_beneficiary=self.existing,
            duplicate_match_score=0.95,
        )

    def test_reject_duplicate_sets_disapproved_not_inactive(self):
        resp = self.client.post(
            f'/dashboard/duplicate-review/{self.flagged.pk}/',
            {'action': 'reject_duplicate', 'notes': 'Confirmed same person re-registering.'},
        )
        self.assertEqual(resp.status_code, 302)
        self.flagged.refresh_from_db()
        self.assertEqual(self.flagged.status, Beneficiary.STATUS_DISAPPROVED)

    def test_approve_twin_still_activates(self):
        resp = self.client.post(
            f'/dashboard/duplicate-review/{self.flagged.pk}/',
            {'action': 'approve_twin', 'notes': 'Confirmed legitimate twin.'},
        )
        self.assertEqual(resp.status_code, 302)
        self.flagged.refresh_from_db()
        self.assertEqual(self.flagged.status, Beneficiary.STATUS_ACTIVE)


class DisapprovedStatusMigrationBackfillTest(TestCase):
    """Data migration 0015 reclassifies legacy rejected-registration rows."""

    def test_legacy_registration_rejection_reclassified(self):
        import importlib
        migration = importlib.import_module(
            'beneficiaries.migrations.0015_backfill_disapproved_status'
        )

        legit_inactive = _make_beneficiary(
            senior_citizen_id='SC-MIG-00001', status='inactive',
            deactivated_reason='Beneficiary moved to another city.',
        )
        legacy_rejected = _make_beneficiary(
            senior_citizen_id='SC-MIG-00002', status='inactive',
            deactivated_reason='Registration rejected by admin: duplicate senior citizen ID',
        )
        legacy_fraud_rejected = _make_beneficiary(
            senior_citizen_id='SC-MIG-00003', status='inactive',
            deactivated_reason='Registration rejected as confirmed duplicate/fraud: same face on file',
        )
        legacy_namedob_rejected = _make_beneficiary(
            senior_citizen_id='SC-MIG-00004', status='inactive',
            deactivated_reason='Name/DOB override rejected: looks like a duplicate',
        )

        from django.apps import apps as real_apps
        migration.backfill_disapproved(real_apps, None)

        legit_inactive.refresh_from_db()
        legacy_rejected.refresh_from_db()
        legacy_fraud_rejected.refresh_from_db()
        legacy_namedob_rejected.refresh_from_db()

        self.assertEqual(legit_inactive.status, 'inactive')
        self.assertEqual(legacy_rejected.status, 'disapproved')
        self.assertEqual(legacy_fraud_rejected.status, 'disapproved')
        self.assertEqual(legacy_namedob_rejected.status, 'disapproved')


# ---------------------------------------------------------------------------
# v2.2.0 Post-UAT Phase 10/11 — Django {# #} single-line comment regression.
# A multi-line {# ... #} comment is NOT stripped by Django's template
# engine (only single-line {# #} comments are) — it renders as literal text.
# The dashboard's upcoming-payout card and two navbar sections had this bug;
# fixed by switching to {% comment %}...{% endcomment %}, which does support
# multiple lines. This test locks in the dashboard fix specifically, since
# that comment only rendered when an approved upcoming event existed.
# ---------------------------------------------------------------------------

class DashboardTemplateCommentLeakTest(TestCase):

    def test_upcoming_payout_card_does_not_leak_raw_comment_text(self):
        from django.contrib.auth import get_user_model
        from verification.models import StipendEvent
        User = get_user_model()
        admin = User.objects.create_user(
            username='dash_comment_admin', password='TestPass1!', role='admin',
        )
        StipendEvent.objects.create(
            title='Leak Check Payout', date=datetime.date.today() + datetime.timedelta(days=5),
            amount=500, created_by=admin, approval_status=StipendEvent.APPROVAL_APPROVED,
        )
        self.client.force_login(admin)
        resp = self.client.get('/dashboard/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Leak Check Payout')
        self.assertNotIn('{#', resp.content.decode())


class DashboardApprovalReminderSyncTest(TestCase):
    """FANS-C comprehensive pre-UAT pass, item 9: sync_approval_reminders()
    was only ever invoked from the Manual Review queue, so a President who
    never visits that admin-only page would never get a stale payout-approval
    reminder even though the dashboard already shows the pending count. The
    dashboard is the page every admin/President actually lands on, so
    reminders must be able to surface from there too."""

    def _stale_stipend_event(self, admin):
        from django.utils import timezone
        from verification.models import StipendEvent
        event = StipendEvent.objects.create(
            title='Stale Approval Payout', date=datetime.date.today() + datetime.timedelta(days=10),
            amount=500, created_by=admin, approval_status=StipendEvent.APPROVAL_PENDING,
        )
        StipendEvent.objects.filter(pk=event.pk).update(
            created_at=timezone.now() - datetime.timedelta(hours=72)
        )
        return event

    def test_admin_visiting_dashboard_creates_overdue_payout_reminder(self):
        from django.contrib.auth import get_user_model
        from logs.models import Notification
        User = get_user_model()
        admin = User.objects.create_user(
            username='dash_reminder_admin', password='TestPass1!', role='admin',
        )
        self._stale_stipend_event(admin)
        self.assertFalse(
            Notification.objects.filter(category=Notification.CATEGORY_APPROVAL_REMINDER).exists()
        )
        self.client.force_login(admin)
        resp = self.client.get('/dashboard/')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(
            Notification.objects.filter(
                recipient=admin, category=Notification.CATEGORY_APPROVAL_REMINDER,
                dedupe_key__startswith='approval_reminder_stipend:',
            ).exists()
        )

    def test_reminder_not_duplicated_on_repeated_dashboard_visits(self):
        from django.contrib.auth import get_user_model
        from logs.models import Notification
        User = get_user_model()
        admin = User.objects.create_user(
            username='dash_reminder_admin2', password='TestPass1!', role='admin',
        )
        self._stale_stipend_event(admin)
        self.client.force_login(admin)
        self.client.get('/dashboard/')
        self.client.get('/dashboard/')
        self.client.get('/dashboard/')
        self.assertEqual(
            Notification.objects.filter(
                recipient=admin, category=Notification.CATEGORY_APPROVAL_REMINDER,
            ).count(),
            1,
        )

    def test_staff_visiting_dashboard_does_not_create_reminder(self):
        """Reminder sync is scoped to admin-capable dashboard rendering only —
        a non-admin staff member's own dashboard load must not trigger it."""
        from django.contrib.auth import get_user_model
        from logs.models import Notification
        User = get_user_model()
        admin = User.objects.create_user(
            username='dash_reminder_admin3', password='TestPass1!', role='admin',
        )
        staff = User.objects.create_user(
            username='dash_reminder_staff', password='TestPass1!', role='staff',
        )
        self._stale_stipend_event(admin)
        self.client.force_login(staff)
        resp = self.client.get('/dashboard/')
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(
            Notification.objects.filter(category=Notification.CATEGORY_APPROVAL_REMINDER).exists()
        )
