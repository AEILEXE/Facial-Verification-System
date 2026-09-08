import re
from django import forms
from .models import Beneficiary
from .validators import validate_senior_citizen_dob, representative_uses_beneficiary_identity
from .qc_barangays import (
    QC_CITY, QC_BARANGAY_SET, QC_BARANGAY_CHOICES,
    QC_BARANGAY_OPTGROUP_CHOICES,
)

_PH_PHONE_RE = re.compile(r'^(\+639|09)\d{9}$')
_ID_NUMBER_RE = re.compile(r'^[\w\s\-/]{1,50}$')


def _validate_ph_phone(value):
    if value and not _PH_PHONE_RE.match(value.strip()):
        raise forms.ValidationError(
            'Enter a valid Philippine mobile number (09XXXXXXXXX or +639XXXXXXXXX).'
        )


def _validate_id_number(value):
    if value and not _ID_NUMBER_RE.match(value.strip()):
        raise forms.ValidationError(
            'ID number may only contain letters, digits, spaces, hyphens, and slashes (max 50 characters).'
        )


# City choices — only two operational cities for this deployment.
# "Others" covers legacy records or beneficiaries at edge addresses.
CITY_CHOICES = [
    ('', '-- Select City --'),
    ('Quezon City', 'Quezon City'),
    ('Others', 'Others'),
]

PROVINCE_CHOICES = [
    ('', '-- Select Province --'),
    ('Metro Manila (NCR)', 'Metro Manila (NCR)'),
    ('Abra', 'Abra'),
    ('Agusan del Norte', 'Agusan del Norte'),
    ('Agusan del Sur', 'Agusan del Sur'),
    ('Aklan', 'Aklan'),
    ('Albay', 'Albay'),
    ('Antique', 'Antique'),
    ('Apayao', 'Apayao'),
    ('Aurora', 'Aurora'),
    ('Basilan', 'Basilan'),
    ('Bataan', 'Bataan'),
    ('Batanes', 'Batanes'),
    ('Batangas', 'Batangas'),
    ('Benguet', 'Benguet'),
    ('Biliran', 'Biliran'),
    ('Bohol', 'Bohol'),
    ('Bukidnon', 'Bukidnon'),
    ('Bulacan', 'Bulacan'),
    ('Cagayan', 'Cagayan'),
    ('Camarines Norte', 'Camarines Norte'),
    ('Camarines Sur', 'Camarines Sur'),
    ('Camiguin', 'Camiguin'),
    ('Capiz', 'Capiz'),
    ('Catanduanes', 'Catanduanes'),
    ('Cavite', 'Cavite'),
    ('Cebu', 'Cebu'),
    ('Compostela Valley', 'Compostela Valley'),
    ('Cotabato', 'Cotabato'),
    ('Davao del Norte', 'Davao del Norte'),
    ('Davao del Sur', 'Davao del Sur'),
    ('Davao Occidental', 'Davao Occidental'),
    ('Davao Oriental', 'Davao Oriental'),
    ('Dinagat Islands', 'Dinagat Islands'),
    ('Eastern Samar', 'Eastern Samar'),
    ('Guimaras', 'Guimaras'),
    ('Ifugao', 'Ifugao'),
    ('Ilocos Norte', 'Ilocos Norte'),
    ('Ilocos Sur', 'Ilocos Sur'),
    ('Iloilo', 'Iloilo'),
    ('Isabela', 'Isabela'),
    ('Kalinga', 'Kalinga'),
    ('La Union', 'La Union'),
    ('Laguna', 'Laguna'),
    ('Lanao del Norte', 'Lanao del Norte'),
    ('Lanao del Sur', 'Lanao del Sur'),
    ('Leyte', 'Leyte'),
    ('Maguindanao', 'Maguindanao'),
    ('Marinduque', 'Marinduque'),
    ('Masbate', 'Masbate'),
    ('Misamis Occidental', 'Misamis Occidental'),
    ('Misamis Oriental', 'Misamis Oriental'),
    ('Mountain Province', 'Mountain Province'),
    ('Negros Occidental', 'Negros Occidental'),
    ('Negros Oriental', 'Negros Oriental'),
    ('Northern Samar', 'Northern Samar'),
    ('Nueva Ecija', 'Nueva Ecija'),
    ('Nueva Vizcaya', 'Nueva Vizcaya'),
    ('Occidental Mindoro', 'Occidental Mindoro'),
    ('Oriental Mindoro', 'Oriental Mindoro'),
    ('Palawan', 'Palawan'),
    ('Pampanga', 'Pampanga'),
    ('Pangasinan', 'Pangasinan'),
    ('Quezon', 'Quezon'),
    ('Quirino', 'Quirino'),
    ('Rizal', 'Rizal'),
    ('Romblon', 'Romblon'),
    ('Samar', 'Samar'),
    ('Sarangani', 'Sarangani'),
    ('Siquijor', 'Siquijor'),
    ('Sorsogon', 'Sorsogon'),
    ('South Cotabato', 'South Cotabato'),
    ('Southern Leyte', 'Southern Leyte'),
    ('Sultan Kudarat', 'Sultan Kudarat'),
    ('Sulu', 'Sulu'),
    ('Surigao del Norte', 'Surigao del Norte'),
    ('Surigao del Sur', 'Surigao del Sur'),
    ('Tarlac', 'Tarlac'),
    ('Tawi-Tawi', 'Tawi-Tawi'),
    ('Zambales', 'Zambales'),
    ('Zamboanga del Norte', 'Zamboanga del Norte'),
    ('Zamboanga del Sur', 'Zamboanga del Sur'),
    ('Zamboanga Sibugay', 'Zamboanga Sibugay'),
]

VALID_ID_CHOICES = [
    ('', '-- Select ID Type --'),
    ('Senior Citizen ID', 'Senior Citizen ID'),
    ('PhilSys', 'PhilSys ID (National ID)'),
    ('Passport', 'Passport'),
    ('Drivers License', "Driver's License"),
    ('UMID', 'UMID'),
    ('Voters ID', "Voter's ID"),
    ('GSIS', 'GSIS eCard'),
    ('SSS', 'SSS ID'),
    ('Postal ID', 'Postal ID'),
    ('Other', 'Other Government ID'),
]

REP_ID_CHOICES = [
    ('', '-- Select ID Type --'),
    ('PhilSys', 'PhilSys ID'),
    ('Passport', 'Passport'),
    ('Drivers License', "Driver's License"),
    ('UMID', 'UMID'),
    ('Voters ID', "Voter's ID"),
    ('Senior Citizen ID', 'Senior Citizen ID'),
    ('Other', 'Other'),
]


def _city_choices_with_legacy(current_value: str) -> list:
    """Return CITY_CHOICES; if current_value is a legacy city not in the list, add it."""
    known = {v for v, _ in CITY_CHOICES}
    if current_value and current_value not in known:
        return CITY_CHOICES + [(current_value, current_value)]
    return CITY_CHOICES


class BeneficiaryInfoForm(forms.ModelForm):
    province = forms.ChoiceField(
        choices=PROVINCE_CHOICES,
        widget=forms.Select(attrs={
            'class': 'form-select',
            'id': 'id_province',
            'required': True,
            'autocomplete': 'off',
        }),
    )
    municipality = forms.ChoiceField(
        choices=CITY_CHOICES,
        label='City',
        widget=forms.Select(attrs={
            'class': 'form-select',
            'id': 'id_municipality',
            'required': True,
            'autocomplete': 'off',
        }),
    )
    barangay = forms.CharField(
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'id': 'id_barangay',
            'placeholder': 'Enter barangay...',
            'required': True,
            'autocomplete': 'off',
        }),
    )
    house_no = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'id': 'id_house_no',
            'placeholder': 'Enter House No.',
            'autocomplete': 'off',
        }),
    )
    street = forms.CharField(
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'id': 'id_street',
            'placeholder': 'Enter Street',
            'required': True,
            'autocomplete': 'off',
        }),
    )

    class Meta:
        model = Beneficiary
        fields = [
            'first_name', 'middle_name', 'last_name', 'date_of_birth',
            'gender', 'municipality', 'house_no', 'street', 'barangay', 'province',
            'contact_number', 'senior_citizen_id', 'valid_id_type', 'valid_id_number',
        ]
        widgets = {
            'first_name': forms.TextInput(attrs={
                'class': 'form-control', 'required': True,
                'autocomplete': 'off', 'autocorrect': 'off', 'autocapitalize': 'off',
            }),
            'middle_name': forms.TextInput(attrs={
                'class': 'form-control',
                'autocomplete': 'off', 'autocorrect': 'off', 'autocapitalize': 'off',
            }),
            'last_name': forms.TextInput(attrs={
                'class': 'form-control', 'required': True,
                'autocomplete': 'off', 'autocorrect': 'off', 'autocapitalize': 'off',
            }),
            'date_of_birth': forms.DateInput(attrs={
                'class': 'form-control', 'type': 'date', 'required': True,
                'autocomplete': 'off',
            }),
            'gender': forms.Select(attrs={'class': 'form-select', 'required': True}),
            'contact_number': forms.TextInput(attrs={
                'class': 'form-control',
                'type': 'tel',
                'inputmode': 'numeric',
                'pattern': r'^(\+639|09)\d{9}$',
                'maxlength': '13',
                'placeholder': '09XXXXXXXXX',
                'autocomplete': 'off',
            }),
            'senior_citizen_id': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g. SC-2024-00123',
                'autocomplete': 'off',
            }),
            'valid_id_type': forms.Select(attrs={'class': 'form-select'}, choices=VALID_ID_CHOICES),
            'valid_id_number': forms.TextInput(attrs={
                'class': 'form-control', 'placeholder': 'ID number',
                'autocomplete': 'off',
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['senior_citizen_id'].required = True
        self.fields['senior_citizen_id'].widget.attrs.update({'required': True})
        # For edit forms, handle legacy municipality values not in CITY_CHOICES
        if self.instance and self.instance.pk:
            self.fields['municipality'].choices = _city_choices_with_legacy(
                self.instance.municipality
            )

    def clean_contact_number(self):
        value = self.cleaned_data.get('contact_number', '')
        _validate_ph_phone(value)
        return value

    def clean_senior_citizen_id(self):
        value = self.cleaned_data.get('senior_citizen_id', '').strip()
        if not value:
            raise forms.ValidationError('Senior Citizen ID Number is required.')
        _validate_id_number(value)
        return value

    def clean_valid_id_number(self):
        value = self.cleaned_data.get('valid_id_number', '')
        _validate_id_number(value)
        return value

    def clean_date_of_birth(self):
        dob = self.cleaned_data['date_of_birth']
        validate_senior_citizen_dob(dob)
        return dob

    def clean_province(self):
        province = self.cleaned_data.get('province')
        if not province:
            raise forms.ValidationError('Please select a province.')
        return province

    def clean_municipality(self):
        value = self.cleaned_data.get('municipality', '').strip()
        if not value:
            raise forms.ValidationError('Please select a city.')
        return value

    def clean_barangay(self):
        barangay = self.cleaned_data.get('barangay', '').strip()
        city = self.cleaned_data.get('municipality', '')
        if not barangay:
            raise forms.ValidationError('Barangay is required.')
        if city == QC_CITY and barangay not in QC_BARANGAY_SET:
            raise forms.ValidationError(
                'Please select a valid Quezon City barangay from the list.'
            )
        return barangay

    def clean_street(self):
        value = self.cleaned_data.get('street', '').strip()
        if not value:
            raise forms.ValidationError('Street is required.')
        return value


class BeneficiaryEditForm(forms.ModelForm):
    """Edit form for existing beneficiaries — all personal fields except face."""
    province = forms.ChoiceField(
        choices=PROVINCE_CHOICES,
        widget=forms.Select(attrs={
            'class': 'form-select',
            'id': 'id_province',
            'autocomplete': 'off',
        }),
    )
    municipality = forms.ChoiceField(
        choices=CITY_CHOICES,
        label='City',
        widget=forms.Select(attrs={
            'class': 'form-select',
            'id': 'id_municipality',
            'autocomplete': 'off',
        }),
    )
    barangay = forms.CharField(
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'id': 'id_barangay',
            'autocomplete': 'off',
        }),
    )
    house_no = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'id': 'id_house_no',
            'placeholder': 'Enter House No.',
            'autocomplete': 'off',
        }),
    )
    street = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'id': 'id_street',
            'placeholder': 'Enter Street',
            'autocomplete': 'off',
        }),
    )

    class Meta:
        model = Beneficiary
        fields = [
            'first_name', 'middle_name', 'last_name', 'date_of_birth',
            'gender', 'municipality', 'house_no', 'street', 'address', 'barangay', 'province',
            'contact_number', 'senior_citizen_id', 'valid_id_type', 'valid_id_number',
            'has_representative', 'rep_first_name', 'rep_last_name',
            'rep_relationship', 'rep_contact', 'rep_id_type', 'rep_id_number',
            'profile_picture',
        ]
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'form-control'}),
            'middle_name': forms.TextInput(attrs={'class': 'form-control'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control'}),
            'date_of_birth': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'gender': forms.Select(attrs={'class': 'form-select'}),
            'address': forms.Textarea(attrs={'class': 'form-control', 'rows': 2,
                                             'placeholder': 'Legacy address (read-only for reference)'}),
            'contact_number': forms.TextInput(attrs={
                'class': 'form-control',
                'type': 'tel',
                'inputmode': 'numeric',
                'pattern': r'^(\+639|09)\d{9}$',
                'maxlength': '13',
                'placeholder': '09XXXXXXXXX',
            }),
            'senior_citizen_id': forms.TextInput(attrs={'class': 'form-control', 'autocomplete': 'off'}),
            'valid_id_type': forms.Select(attrs={'class': 'form-select'}, choices=VALID_ID_CHOICES),
            'valid_id_number': forms.TextInput(attrs={'class': 'form-control'}),
            'has_representative': forms.CheckboxInput(attrs={'class': 'form-check-input', 'id': 'hasRep'}),
            'rep_first_name': forms.TextInput(attrs={'class': 'form-control'}),
            'rep_last_name': forms.TextInput(attrs={'class': 'form-control'}),
            'rep_relationship': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Son, Daughter, Spouse'}),
            'rep_contact': forms.TextInput(attrs={
                'class': 'form-control',
                'type': 'tel',
                'inputmode': 'numeric',
                'pattern': r'^(\+639|09)\d{9}$',
                'maxlength': '13',
                'placeholder': '09XXXXXXXXX',
            }),
            'rep_id_type': forms.Select(attrs={'class': 'form-select'}, choices=REP_ID_CHOICES),
            'rep_id_number': forms.TextInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['senior_citizen_id'].required = True
        self.fields['senior_citizen_id'].widget.attrs.update({'required': True})
        # Handle legacy municipality values not in CITY_CHOICES
        if self.instance and self.instance.pk:
            self.fields['municipality'].choices = _city_choices_with_legacy(
                self.instance.municipality
            )
            # Visually reflect the server-side block in clean_date_of_birth:
            # once reviewed, DOB is only changeable via the dedicated
            # correction action, not this form.
            if self.instance.status != Beneficiary.STATUS_PENDING:
                self.fields['date_of_birth'].widget.attrs['readonly'] = True
                self.fields['date_of_birth'].widget.attrs['title'] = (
                    "Use the Correct Date of Birth action to change this."
                )

    def clean_contact_number(self):
        value = self.cleaned_data.get('contact_number', '')
        _validate_ph_phone(value)
        return value

    def clean_senior_citizen_id(self):
        value = self.cleaned_data.get('senior_citizen_id', '').strip()
        if not value:
            raise forms.ValidationError('Senior Citizen ID Number is required.')
        _validate_id_number(value)
        return value

    def clean_valid_id_number(self):
        value = self.cleaned_data.get('valid_id_number', '')
        _validate_id_number(value)
        return value

    def clean_date_of_birth(self):
        dob = self.cleaned_data['date_of_birth']
        validate_senior_citizen_dob(dob)
        # Date of birth drives Birthday Bonus eligibility. Once a beneficiary
        # has been reviewed (any status other than the pre-approval PENDING
        # state — including a later-deactivated record, so deactivate/edit/
        # reactivate cannot be used to route around this), it must not be
        # mutable through the ordinary edit form: self.instance still holds
        # the pre-edit value here (ModelForm hasn't run _post_clean() yet),
        # so this compares old vs. submitted value server-side, not just a
        # readonly HTML attribute. Use the dedicated, audited DOB correction
        # action instead.
        if (
            self.instance.pk
            and self.instance.status != Beneficiary.STATUS_PENDING
            and dob != self.instance.date_of_birth
        ):
            raise forms.ValidationError(
                'Date of birth cannot be changed through the ordinary edit form once a '
                'beneficiary has been reviewed. Use the Correct Date of Birth action instead.'
            )
        return dob

    def clean_rep_contact(self):
        value = self.cleaned_data.get('rep_contact', '')
        _validate_ph_phone(value)
        return value

    def clean_rep_id_number(self):
        value = self.cleaned_data.get('rep_id_number', '')
        _validate_id_number(value)
        return value

    def clean_province(self):
        province = self.cleaned_data.get('province')
        if not province:
            raise forms.ValidationError('Please select a province.')
        return province

    def clean_municipality(self):
        value = self.cleaned_data.get('municipality', '').strip()
        if not value:
            raise forms.ValidationError('Please select a city.')
        return value

    def clean_barangay(self):
        barangay = self.cleaned_data.get('barangay', '').strip()
        city = self.cleaned_data.get('municipality', '')
        if not barangay:
            raise forms.ValidationError('Barangay is required.')
        if city == QC_CITY and barangay not in QC_BARANGAY_SET:
            raise forms.ValidationError(
                'Please select a valid Quezon City barangay from the list.'
            )
        return barangay

    def clean(self):
        cleaned_data = super().clean()
        has_rep = cleaned_data.get('has_representative', False)
        if has_rep:
            required = {
                'rep_first_name': 'Representative first name is required when a representative is enabled.',
                'rep_last_name': 'Representative last name is required when a representative is enabled.',
                'rep_contact': 'Representative contact number is required when a representative is enabled.',
                'rep_id_type': 'Representative ID type must be selected when a representative is enabled.',
                'rep_id_number': 'Representative ID number is required when a representative is enabled.',
            }
            missing = False
            for field, msg in required.items():
                if not cleaned_data.get(field, '').strip():
                    self.add_error(field, msg)
                    missing = True
            if not missing and representative_uses_beneficiary_identity(
                cleaned_data.get('valid_id_type', ''),
                cleaned_data.get('valid_id_number', ''),
                cleaned_data.get('senior_citizen_id', ''),
                cleaned_data.get('rep_id_type', ''),
                cleaned_data.get('rep_id_number', ''),
            ):
                self.add_error(
                    'rep_id_number',
                    "The representative cannot use the beneficiary's own identity document.",
                )
        return cleaned_data


class RepresentativeForm(forms.ModelForm):
    class Meta:
        model = Beneficiary
        fields = [
            'has_representative', 'rep_first_name', 'rep_last_name',
            'rep_relationship', 'rep_contact', 'rep_id_type', 'rep_id_number',
        ]
        widgets = {
            'has_representative': forms.CheckboxInput(attrs={'class': 'form-check-input', 'id': 'hasRep'}),
            'rep_first_name': forms.TextInput(attrs={
                'class': 'form-control',
                'autocomplete': 'off', 'autocorrect': 'off', 'autocapitalize': 'off',
            }),
            'rep_last_name': forms.TextInput(attrs={
                'class': 'form-control',
                'autocomplete': 'off', 'autocorrect': 'off', 'autocapitalize': 'off',
            }),
            'rep_relationship': forms.TextInput(attrs={
                'class': 'form-control', 'placeholder': 'e.g. Son, Daughter, Spouse',
                'autocomplete': 'off',
            }),
            'rep_contact': forms.TextInput(attrs={
                'class': 'form-control',
                'type': 'tel',
                'inputmode': 'numeric',
                'pattern': r'^(\+639|09)\d{9}$',
                'maxlength': '13',
                'placeholder': '09XXXXXXXXX',
                'autocomplete': 'off',
            }),
            'rep_id_type': forms.Select(attrs={'class': 'form-select'}, choices=REP_ID_CHOICES),
            'rep_id_number': forms.TextInput(attrs={
                'class': 'form-control',
                'autocomplete': 'off',
            }),
        }

    def clean_rep_contact(self):
        value = self.cleaned_data.get('rep_contact', '')
        _validate_ph_phone(value)
        return value

    def clean_rep_id_number(self):
        value = self.cleaned_data.get('rep_id_number', '')
        _validate_id_number(value)
        return value

    def clean(self):
        cleaned_data = super().clean()
        has_rep = cleaned_data.get('has_representative', False)
        if has_rep:
            required = {
                'rep_first_name': 'Representative first name is required.',
                'rep_last_name': 'Representative last name is required.',
                'rep_contact': 'Representative contact number is required.',
                'rep_id_type': 'Representative ID type must be selected.',
                'rep_id_number': 'Representative ID number is required.',
            }
            missing = False
            for field, msg in required.items():
                if not cleaned_data.get(field, '').strip():
                    self.add_error(field, msg)
                    missing = True
            # self.instance carries the represented beneficiary's own ID
            # fields (populated by the view from the not-yet-saved step1
            # data) so a representative cannot be registered using the same
            # identity document as the beneficiary they represent.
            if not missing and representative_uses_beneficiary_identity(
                self.instance.valid_id_type,
                self.instance.valid_id_number,
                self.instance.senior_citizen_id,
                cleaned_data.get('rep_id_type', ''),
                cleaned_data.get('rep_id_number', ''),
            ):
                self.add_error(
                    'rep_id_number',
                    "The representative cannot use the beneficiary's own identity document.",
                )
        return cleaned_data


class ConsentForm(forms.Form):
    consent = forms.BooleanField(
        required=True,
        label='I consent to the collection and processing of my biometric data for the purpose of stipend distribution verification.',
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
    )
    consent_privacy = forms.BooleanField(
        required=True,
        label='I have read and understood the Privacy Notice above.',
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
    )
