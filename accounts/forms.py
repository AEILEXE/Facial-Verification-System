import re
from django import forms
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm as _DjPasswordChangeForm
from django.contrib.auth.password_validation import validate_password
from .models import CustomUser

# Philippine mobile number: 09XXXXXXXXX (11 digits) or +639XXXXXXXXX (12 chars)
_PH_PHONE_RE = re.compile(r'^(\+639|09)\d{9}$')

# v2.1.12 — single canonical error message for the President uniqueness rule.
PRESIDENT_UNIQUE_ERROR = (
    'Only one active President account is allowed. '
    'Deactivate or change the current President first.'
)


def _validate_president_uniqueness(role, *, exclude_pk=None):
    """
    Raise ValidationError if `role == 'president'` and another active
    President already exists. Shared by every user form that exposes role.
    """
    if role == CustomUser.ROLE_PRESIDENT and CustomUser.active_president_exists(exclude_pk=exclude_pk):
        raise forms.ValidationError(PRESIDENT_UNIQUE_ERROR)


def _validate_ph_phone(value):
    if value and not _PH_PHONE_RE.match(value.strip()):
        raise forms.ValidationError(
            'Enter a valid Philippine mobile number (09XXXXXXXXX or +639XXXXXXXXX).'
        )


def _validate_unique_email(email, *, exclude_pk=None):
    """
    v2.2.0 Post-UAT Phase 8 — registered email is now a password-recovery
    factor (self-service OTP reset resolves an account by email), so two
    accounts sharing one address would make that lookup ambiguous. Enforced
    case-insensitively at the form layer (not a DB constraint, since a fresh
    deployment or an upgrade may already have legacy duplicates on file that
    must not block unrelated saves).
    """
    if not email:
        return
    qs = CustomUser.objects.filter(email__iexact=email)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    if qs.exists():
        raise forms.ValidationError(
            'This email address is already registered to another account. '
            'Each account must have a unique email — it is used for password recovery.'
        )


# Common HTML attribute set for password inputs across the app.
# - oncopy/oncut return false to deter shoulder-surfers grabbing the field.
# - paste is intentionally NOT blocked (password managers must keep working).
# - autocomplete hints help browser/PW manager pick the right credential type.
PW_INPUT_ATTRS_NEW = {
    'class': 'form-control',
    'autocomplete': 'new-password',
    'oncopy': 'return false;',
    'oncut': 'return false;',
    'oncontextmenu': 'return false;',
}
PW_INPUT_ATTRS_CURRENT = {
    'class': 'form-control',
    'autocomplete': 'current-password',
    'oncopy': 'return false;',
    'oncut': 'return false;',
    'oncontextmenu': 'return false;',
}

# Active system role choices for new-user creation and editing forms.
# System Role = software permissions (separate from Officer Position / org-chart title).
_ACTIVE_ROLE_CHOICES = [
    (CustomUser.ROLE_PRESIDENT, 'President'),
    (CustomUser.ROLE_ADMIN,     'Admin'),
    (CustomUser.ROLE_IT,        CustomUser.TECHNICAL_ADMIN_LABEL),
    (CustomUser.ROLE_STAFF,     'Staff'),
]


class LoginForm(AuthenticationForm):
    username = forms.CharField(
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Username',
            'autofocus': True,
            'autocomplete': 'username',
            'autocorrect': 'off',
            'autocapitalize': 'off',
            'spellcheck': 'false',
        })
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={
            **PW_INPUT_ATTRS_CURRENT,
            'placeholder': 'Password',
            'autocomplete': 'current-password',
            'autocorrect': 'off',
            'autocapitalize': 'off',
            'spellcheck': 'false',
        })
    )


class UserCreateForm(forms.ModelForm):
    password1 = forms.CharField(
        label='Password',
        widget=forms.PasswordInput(attrs=PW_INPUT_ATTRS_NEW),
        help_text=('At least 10 characters with at least one uppercase letter, '
                   'one digit or symbol. Cannot be a common password or entirely numeric.'),
    )
    password2 = forms.CharField(
        label='Confirm Password',
        widget=forms.PasswordInput(attrs=PW_INPUT_ATTRS_NEW),
    )

    class Meta:
        model = CustomUser
        fields = ['username', 'first_name', 'last_name', 'email', 'role', 'employee_id', 'phone']
        widgets = {
            'username':    forms.TextInput(attrs={'class': 'form-control'}),
            'first_name':  forms.TextInput(attrs={'class': 'form-control'}),
            'last_name':   forms.TextInput(attrs={'class': 'form-control'}),
            'email':       forms.EmailInput(attrs={'class': 'form-control'}),
            'role':        forms.Select(attrs={'class': 'form-select'},
                                        choices=_ACTIVE_ROLE_CHOICES),
            'employee_id': forms.TextInput(attrs={'class': 'form-control'}),
            'phone':       forms.TextInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['role'].choices = _ACTIVE_ROLE_CHOICES
        self.fields['role'].label = 'System Role / Access Role'
        self.fields['role'].help_text = (
            'This controls software permissions, not the organization chart position.'
        )
        for f in ('first_name', 'last_name', 'email', 'employee_id'):
            self.fields[f].required = True

    def clean_phone(self):
        value = self.cleaned_data.get('phone', '')
        _validate_ph_phone(value)
        return value

    def clean_email(self):
        email = self.cleaned_data.get('email', '')
        _validate_unique_email(email, exclude_pk=getattr(self.instance, 'pk', None))
        return email

    def clean_role(self):
        role = self.cleaned_data.get('role')
        _validate_president_uniqueness(role, exclude_pk=getattr(self.instance, 'pk', None))
        return role

    def clean_password2(self):
        p1 = self.cleaned_data.get('password1')
        p2 = self.cleaned_data.get('password2')
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError('Passwords do not match.')
        return p2

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get('password1')
        if p1:
            # Run Django's full password validation suite (min length, common, numeric)
            user = self.instance
            validate_password(p1, user=user)
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data['password1'])
        if commit:
            user.save()
        return user


class UserUpdateForm(forms.ModelForm):
    class Meta:
        model = CustomUser
        fields = ['first_name', 'last_name', 'email', 'role', 'employee_id',
                  'phone', 'is_active']
        widgets = {
            'first_name':  forms.TextInput(attrs={'class': 'form-control'}),
            'last_name':   forms.TextInput(attrs={'class': 'form-control'}),
            'email':       forms.EmailInput(attrs={'class': 'form-control'}),
            'role':        forms.Select(attrs={'class': 'form-select'},
                                        choices=_ACTIVE_ROLE_CHOICES),
            'employee_id': forms.TextInput(attrs={'class': 'form-control'}),
            'phone':       forms.TextInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['role'].choices = _ACTIVE_ROLE_CHOICES
        self.fields['role'].label = 'System Role / Access Role'
        self.fields['role'].help_text = (
            'This controls software permissions, not the organization chart position.'
        )
        for f in ('first_name', 'last_name', 'email', 'employee_id'):
            self.fields[f].required = True

    def clean_phone(self):
        value = self.cleaned_data.get('phone', '')
        _validate_ph_phone(value)
        return value

    def clean_email(self):
        email = self.cleaned_data.get('email', '')
        _validate_unique_email(email, exclude_pk=getattr(self.instance, 'pk', None))
        return email

    def clean_role(self):
        role = self.cleaned_data.get('role')
        _validate_president_uniqueness(role, exclude_pk=getattr(self.instance, 'pk', None))
        return role


class PasswordChangeForm(_DjPasswordChangeForm):
    """Self-service password change for the currently logged-in user."""
    old_password = forms.CharField(
        label='Current Password',
        strip=False,
        widget=forms.PasswordInput(attrs=PW_INPUT_ATTRS_CURRENT),
    )
    new_password1 = forms.CharField(
        label='New Password',
        widget=forms.PasswordInput(attrs=PW_INPUT_ATTRS_NEW),
        help_text=('At least 10 characters with at least one uppercase letter, '
                   'one digit or symbol. Cannot be a common password or entirely numeric.'),
    )
    new_password2 = forms.CharField(
        label='Confirm New Password',
        strip=False,
        widget=forms.PasswordInput(attrs=PW_INPUT_ATTRS_NEW),
    )


class CreateAdminForm(forms.Form):
    """
    Standalone form for the initial-setup 'Create Technical Administrator
    Account' page. Uses separate first_name/last_name fields (v2.1.17 QA
    fix pass, Issue 3 — a single combined "Full Name" field cannot be split
    reliably for every real name, e.g. multi-word given names). Role is
    always ROLE_IT — the first-run wizard no longer offers a role choice
    (v2.1.19 UX pass, section 8A): the bootstrap account is technical/
    research access, not a barangay organizational position. Creating the
    initial President is a separate step (accounts:bootstrap_president)
    available only to this account, only while no President exists yet.
    No Bootstrap classes — the page uses its own scoped CSS.
    """
    first_name = forms.CharField(
        label='First name',
        max_length=150,
        widget=forms.TextInput(attrs={
            'placeholder': 'e.g. Juan',
            'class': '',
            'autocomplete': 'given-name',
        }),
    )
    last_name = forms.CharField(
        label='Last name',
        max_length=150,
        widget=forms.TextInput(attrs={
            'placeholder': 'e.g. Dela Cruz',
            'class': '',
            'autocomplete': 'family-name',
        }),
    )
    username = forms.CharField(
        label='Username',
        max_length=150,
        widget=forms.TextInput(attrs={
            'placeholder': 'e.g. admin_juan',
            'class': '',
            'autocomplete': 'username',
        }),
    )
    email = forms.EmailField(
        label='Email',
        required=False,
        widget=forms.EmailInput(attrs={
            'placeholder': 'optional',
            'class': '',
            'autocomplete': 'email',
        }),
    )
    password1 = forms.CharField(
        label='Password',
        widget=forms.PasswordInput(attrs={
            'placeholder': 'Min. 10 characters',
            'class': '',
            'autocomplete': 'new-password',
            'oncopy': 'return false;',
            'oncut': 'return false;',
        }),
    )
    password2 = forms.CharField(
        label='Confirm password',
        widget=forms.PasswordInput(attrs={
            'placeholder': 'Re-enter password',
            'class': '',
            'autocomplete': 'new-password',
            'oncopy': 'return false;',
            'oncut': 'return false;',
        }),
    )

    def clean_username(self):
        username = self.cleaned_data['username']
        if CustomUser.objects.filter(username=username).exists():
            raise forms.ValidationError('That username is already taken.')
        return username

    def clean_email(self):
        email = self.cleaned_data.get('email', '')
        if email:
            _validate_unique_email(email)
        return email

    def clean_password2(self):
        p1 = self.cleaned_data.get('password1')
        p2 = self.cleaned_data.get('password2')
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError('Passwords do not match.')
        return p2

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get('password1')
        if p1:
            validate_password(p1)
        return cleaned

    def save(self):
        data = self.cleaned_data
        user = CustomUser.objects.create_user(
            username=data['username'],
            password=data['password1'],
            first_name=data['first_name'].strip(),
            last_name=data['last_name'].strip(),
            email=data.get('email', ''),
            role=CustomUser.ROLE_IT,
        )
        return user


class BootstrapPresidentForm(forms.Form):
    """
    One-time 'create the initial President' step, reachable only by the
    Technical Administrator and only while zero President-role users exist
    (accounts:bootstrap_president). Section 8B of the v2.1.19 UX pass —
    keeps Technical Administrator from being the account that permanently
    creates ordinary barangay officers/users; this covers only the single
    initial President so the barangay organization can bootstrap itself.

    Uses separate first_name/last_name fields (v2.1.17 QA fix pass, Issue 3)
    rather than a single combined "Full Name" field.
    """
    first_name = forms.CharField(label='First name', max_length=150,
                                  widget=forms.TextInput(attrs={'class': 'form-control', 'autocomplete': 'given-name'}))
    last_name = forms.CharField(label='Last name', max_length=150,
                                 widget=forms.TextInput(attrs={'class': 'form-control', 'autocomplete': 'family-name'}))
    username = forms.CharField(label='Username', max_length=150,
                                widget=forms.TextInput(attrs={'class': 'form-control', 'autocomplete': 'username'}))
    email = forms.EmailField(label='Email', required=False,
                              widget=forms.EmailInput(attrs={'class': 'form-control', 'autocomplete': 'email'}))
    password1 = forms.CharField(label='Password', widget=forms.PasswordInput(attrs=PW_INPUT_ATTRS_NEW))
    password2 = forms.CharField(label='Confirm password', widget=forms.PasswordInput(attrs=PW_INPUT_ATTRS_NEW))

    def clean_username(self):
        username = self.cleaned_data['username']
        if CustomUser.objects.filter(username=username).exists():
            raise forms.ValidationError('That username is already taken.')
        return username

    def clean_email(self):
        email = self.cleaned_data.get('email', '')
        if email:
            _validate_unique_email(email)
        return email

    def clean_password2(self):
        p1 = self.cleaned_data.get('password1')
        p2 = self.cleaned_data.get('password2')
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError('Passwords do not match.')
        return p2

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get('password1')
        if p1:
            validate_password(p1)
        if CustomUser.active_president_exists():
            raise forms.ValidationError('A President account already exists.')
        return cleaned

    def save(self, created_by=None):
        data = self.cleaned_data
        user = CustomUser.objects.create_user(
            username=data['username'],
            password=data['password1'],
            first_name=data['first_name'].strip(),
            last_name=data['last_name'].strip(),
            email=data.get('email', ''),
            role=CustomUser.ROLE_PRESIDENT,
        )
        if created_by is not None:
            user.created_by = created_by
            user.save(update_fields=['created_by'])
        return user


class AdminPasswordResetForm(forms.Form):
    """Admin resets another user's password without knowing the old one."""
    new_password1 = forms.CharField(
        label='New Password',
        widget=forms.PasswordInput(attrs=PW_INPUT_ATTRS_NEW),
        help_text=('At least 10 characters with at least one uppercase letter, '
                   'one digit or symbol. Cannot be a common password or entirely numeric.'),
    )
    new_password2 = forms.CharField(
        label='Confirm New Password',
        widget=forms.PasswordInput(attrs=PW_INPUT_ATTRS_NEW),
    )
    reset_reason = forms.CharField(
        label='Reason for reset',
        widget=forms.Textarea(attrs={
            'class': 'form-control', 'rows': 2,
            'placeholder': 'e.g. user forgot password / phone replacement / locked out',
        }),
        help_text='Required. Will appear in the audit log.',
        min_length=5,
        max_length=500,
    )

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get('new_password1')
        p2 = cleaned.get('new_password2')
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError('Passwords do not match.')
        if p1:
            validate_password(p1)
        return cleaned


class PasswordResetRequestForm(forms.Form):
    """Public, unauthenticated 'I'm locked out' request. Creates a
    PasswordResetRequest for an admin to review — never sets a password
    itself. Deliberately has no password fields."""
    username = forms.CharField(
        label='Username',
        max_length=150,
        widget=forms.TextInput(attrs={'class': 'form-control', 'autocomplete': 'username'}),
    )
    contact_note = forms.CharField(
        label='How can we reach you, or why do you need a reset?',
        widget=forms.Textarea(attrs={
            'class': 'form-control', 'rows': 3,
            'placeholder': 'e.g. contact number, or why you believe you are locked out',
        }),
        min_length=5,
        max_length=500,
    )


# ─── Self-Service Email-OTP Password Reset Forms (v2.2.0 Phase 6) ────────────

class OTPRequestForm(forms.Form):
    """Step 1: enter username or registered email. Deliberately accepts
    either — the view resolves it without revealing which one matched."""
    identifier = forms.CharField(
        label='Username or Email',
        max_length=150,
        widget=forms.TextInput(attrs={
            'class': 'form-control', 'autocomplete': 'username',
            'placeholder': 'Your username or registered email address',
        }),
    )


class OTPVerifyForm(forms.Form):
    """Step 2: enter the 6-digit code just emailed."""
    code = forms.CharField(
        label='Verification Code',
        min_length=4, max_length=8,
        widget=forms.TextInput(attrs={
            'class': 'form-control form-control-lg text-center font-mono',
            'autocomplete': 'one-time-code', 'inputmode': 'numeric',
            'placeholder': '••••••', 'style': 'letter-spacing:0.4em;',
        }),
    )

    def clean_code(self):
        code = self.cleaned_data['code'].strip()
        if not code.isdigit():
            raise forms.ValidationError('Enter the numeric code exactly as emailed to you.')
        return code


class OTPSetPasswordForm(forms.Form):
    """Step 3: set a new password after a verified OTP. Same password
    policy as every other password-setting form in this app."""
    new_password1 = forms.CharField(
        label='New Password',
        widget=forms.PasswordInput(attrs=PW_INPUT_ATTRS_NEW),
        help_text=('At least 10 characters with at least one uppercase letter, '
                   'one digit or symbol. Cannot be a common password or entirely numeric.'),
    )
    new_password2 = forms.CharField(
        label='Confirm New Password',
        widget=forms.PasswordInput(attrs=PW_INPUT_ATTRS_NEW),
    )

    def __init__(self, *args, user=None, **kwargs):
        self._user = user
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get('new_password1')
        p2 = cleaned.get('new_password2')
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError('Passwords do not match.')
        if p1:
            validate_password(p1, user=self._user)
        return cleaned


# ─── Officer Position Form ────────────────────────────────────────────────────

class OfficerPositionForm(forms.ModelForm):
    class Meta:
        from .models import OfficerPosition
        model = OfficerPosition
        fields = ['name', 'description', 'level', 'order', 'is_unique', 'is_active']
        widgets = {
            'name':        forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
            'level':       forms.NumberInput(attrs={'class': 'form-control'}),
            'order':       forms.NumberInput(attrs={'class': 'form-control'}),
            'is_unique':   forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'is_active':   forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
        labels = {
            'level': 'Chart Level (Tier)',
            'order': 'Sort Order Within Level',
        }
        help_texts = {
            'level': 'Positions with the same level appear side-by-side as equal-rank siblings (e.g. two Vice Presidents both at level 2).',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .models import OfficerPosition as _OP
        self.Meta.model = _OP


# ─── Officer Assignment Form ──────────────────────────────────────────────────

class OfficerAssignmentForm(forms.ModelForm):
    class Meta:
        from .models import OfficerAssignment
        model = OfficerAssignment
        fields = ['user', 'position', 'start_date', 'end_date', 'is_current', 'remarks']
        widgets = {
            'user':       forms.Select(attrs={'class': 'form-select'}),
            'position':   forms.Select(attrs={'class': 'form-select'}),
            'start_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'end_date':   forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'is_current': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'remarks':    forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .models import OfficerAssignment as _OA, OfficerPosition, CustomUser as _CU
        self.Meta.model = _OA
        self.fields['position'].queryset = OfficerPosition.objects.filter(is_active=True).order_by('order', 'name')
        self.fields['user'].queryset = _CU.objects.filter(is_active=True).order_by('last_name', 'first_name')
        self.fields['end_date'].required = False

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get('start_date')
        end = cleaned.get('end_date')
        if start and end and end < start:
            raise forms.ValidationError('End date cannot be earlier than start date.')
        return cleaned


# ─── Enhanced User Create/Edit Forms ─────────────────────────────────────────

class UserCreateFullForm(forms.ModelForm):
    """Create a new user with all officer management fields."""
    password1 = forms.CharField(
        label='Password',
        widget=forms.PasswordInput(attrs=PW_INPUT_ATTRS_NEW),
        help_text='At least 10 characters with uppercase, digit, and special character.',
    )
    password2 = forms.CharField(
        label='Confirm Password',
        widget=forms.PasswordInput(attrs=PW_INPUT_ATTRS_NEW),
    )
    # v2.1.12 (Issue 3) — integrated officer assignment fields.
    # System Role controls software access; Officer Position controls the
    # organization chart. Both can be set in the same form.
    officer_position = forms.ModelChoiceField(
        label='Officer Position',
        required=False,
        queryset=None,  # set in __init__
        empty_label='— None (no org-chart position) —',
        widget=forms.Select(attrs={'class': 'form-select'}),
        help_text='Organization-chart position. Separate from System Role.',
    )
    officer_start_date = forms.DateField(
        label='Officer Position Start Date',
        required=False,
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
    )
    officer_is_current = forms.BooleanField(
        label='Is Current Officer',
        required=False, initial=True,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        help_text='Uncheck to record a past term (no org-chart display).',
    )

    class Meta:
        model = CustomUser
        fields = [
            'username', 'first_name', 'middle_name', 'last_name', 'suffix', 'email',
            'role', 'employee_id', 'phone', 'assigned_office',
            'account_status', 'must_change_password',
        ]
        widgets = {
            'username':           forms.TextInput(attrs={'class': 'form-control'}),
            'first_name':         forms.TextInput(attrs={'class': 'form-control'}),
            'middle_name':        forms.TextInput(attrs={'class': 'form-control'}),
            'last_name':          forms.TextInput(attrs={'class': 'form-control'}),
            'suffix':             forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Jr., Sr., III'}),
            'email':              forms.EmailInput(attrs={'class': 'form-control'}),
            'role':               forms.Select(attrs={'class': 'form-select'}, choices=_ACTIVE_ROLE_CHOICES),
            'employee_id':        forms.TextInput(attrs={'class': 'form-control'}),
            'phone':              forms.TextInput(attrs={'class': 'form-control'}),
            'assigned_office':    forms.TextInput(attrs={'class': 'form-control',
                                                         'placeholder': 'e.g. Barangay Santol Senior Citizen Association'}),
            'account_status':     forms.Select(attrs={'class': 'form-select'}),
            'must_change_password': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
        labels = {
            'assigned_office': 'Assigned Office / Unit',
            'role': 'System Role / Access Role',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .models import OfficerPosition
        self.fields['role'].choices = _ACTIVE_ROLE_CHOICES
        self.fields['role'].label = 'System Role / Access Role'
        self.fields['role'].help_text = (
            'This controls software permissions, not the organization chart position.'
        )
        self.fields['officer_position'].queryset = OfficerPosition.objects.filter(
            is_active=True
        ).order_by('order', 'name')
        for f in ('first_name', 'last_name', 'email'):
            self.fields[f].required = True
        self.fields['phone'].required = False
        self.fields['employee_id'].required = False

    def clean_phone(self):
        value = self.cleaned_data.get('phone', '')
        _validate_ph_phone(value)
        return value

    def clean_email(self):
        email = self.cleaned_data.get('email', '')
        # New user — no pk to exclude.
        _validate_unique_email(email)
        return email

    def clean_role(self):
        role = self.cleaned_data.get('role')
        # New user — no pk to exclude.
        _validate_president_uniqueness(role)
        return role

    def clean_password2(self):
        p1 = self.cleaned_data.get('password1')
        p2 = self.cleaned_data.get('password2')
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError('Passwords do not match.')
        return p2

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get('password1')
        if p1:
            validate_password(p1, user=self.instance)
        # v2.1.12 (Issue 3) — if a unique officer position is selected as
        # CURRENT and someone else already holds it as CURRENT, block.
        pos = cleaned.get('officer_position')
        is_current = cleaned.get('officer_is_current', True)
        if pos is not None and is_current and pos.is_unique:
            from .models import OfficerAssignment
            occupied = OfficerAssignment.objects.filter(
                position=pos, is_current=True,
            ).exists()
            if occupied:
                raise forms.ValidationError(
                    f'Officer Position "{pos.name}" is already held by another active '
                    f'officer. Close the existing assignment first, or uncheck '
                    f'"Is Current Officer" to record a past term.'
                )
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data['password1'])
        # Sync is_active with account_status
        user.is_active = user.account_status == CustomUser.STATUS_ACTIVE
        if commit:
            user.save()
            self._save_officer_assignment(user)
        return user

    def _save_officer_assignment(self, user):
        """v2.1.12 (Issue 3): create the linked OfficerAssignment if requested."""
        from .models import OfficerAssignment
        import datetime as _dt
        pos = self.cleaned_data.get('officer_position')
        if pos is None:
            return None
        start_date = self.cleaned_data.get('officer_start_date') or _dt.date.today()
        is_current = bool(self.cleaned_data.get('officer_is_current', True))
        return OfficerAssignment.objects.create(
            user=user,
            position=pos,
            start_date=start_date,
            is_current=is_current,
        )


class UserEditFullForm(forms.ModelForm):
    """Edit an existing user including account_status and must_change_password."""
    # v2.1.12 (Issue 3) — integrated officer assignment fields. Reads the
    # user's current OfficerAssignment so the existing position is pre-selected.
    officer_position = forms.ModelChoiceField(
        label='Officer Position',
        required=False,
        queryset=None,
        empty_label='— None (no org-chart position) —',
        widget=forms.Select(attrs={'class': 'form-select'}),
        help_text='Organization-chart position. Separate from System Role.',
    )
    officer_start_date = forms.DateField(
        label='Officer Position Start Date',
        required=False,
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
    )
    officer_is_current = forms.BooleanField(
        label='Is Current Officer',
        required=False, initial=True,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        help_text='Uncheck to record a past term (no org-chart display).',
    )

    class Meta:
        model = CustomUser
        fields = [
            'first_name', 'middle_name', 'last_name', 'suffix', 'email',
            'role', 'employee_id', 'phone', 'assigned_office',
            'account_status', 'must_change_password',
        ]
        widgets = {
            'first_name':         forms.TextInput(attrs={'class': 'form-control'}),
            'middle_name':        forms.TextInput(attrs={'class': 'form-control'}),
            'last_name':          forms.TextInput(attrs={'class': 'form-control'}),
            'suffix':             forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Jr., Sr., III'}),
            'email':              forms.EmailInput(attrs={'class': 'form-control'}),
            'role':               forms.Select(attrs={'class': 'form-select'}, choices=_ACTIVE_ROLE_CHOICES),
            'employee_id':        forms.TextInput(attrs={'class': 'form-control'}),
            'phone':              forms.TextInput(attrs={'class': 'form-control'}),
            'assigned_office':    forms.TextInput(attrs={'class': 'form-control',
                                                         'placeholder': 'e.g. Barangay Santol Senior Citizen Association'}),
            'account_status':     forms.Select(attrs={'class': 'form-select'}),
            'must_change_password': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
        labels = {
            'assigned_office': 'Assigned Office / Unit',
            'role': 'System Role / Access Role',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .models import OfficerPosition
        self.fields['role'].choices = _ACTIVE_ROLE_CHOICES
        self.fields['role'].label = 'System Role / Access Role'
        self.fields['role'].help_text = (
            'This controls software permissions, not the organization chart position.'
        )
        self.fields['officer_position'].queryset = OfficerPosition.objects.filter(
            is_active=True
        ).order_by('order', 'name')
        for f in ('first_name', 'last_name', 'email'):
            self.fields[f].required = True
        self.fields['phone'].required = False
        # Pre-populate officer fields from the existing current assignment, if any.
        if self.instance and self.instance.pk:
            current = self.instance.get_current_officer_assignment()
            if current:
                self.fields['officer_position'].initial = current.position_id
                self.fields['officer_start_date'].initial = current.start_date
                self.fields['officer_is_current'].initial = current.is_current

    def clean_phone(self):
        value = self.cleaned_data.get('phone', '')
        _validate_ph_phone(value)
        return value

    def clean_email(self):
        email = self.cleaned_data.get('email', '')
        _validate_unique_email(email, exclude_pk=getattr(self.instance, 'pk', None))
        return email

    def clean_role(self):
        role = self.cleaned_data.get('role')
        _validate_president_uniqueness(role, exclude_pk=getattr(self.instance, 'pk', None))
        return role

    def clean(self):
        cleaned = super().clean()
        pos = cleaned.get('officer_position')
        is_current = cleaned.get('officer_is_current', True)
        if pos is not None and is_current and pos.is_unique:
            from .models import OfficerAssignment
            occupied = OfficerAssignment.objects.filter(
                position=pos, is_current=True,
            ).exclude(user=self.instance).exists()
            if occupied:
                raise forms.ValidationError(
                    f'Officer Position "{pos.name}" is already held by another active '
                    f'officer. Close the existing assignment first, or uncheck '
                    f'"Is Current Officer" to record a past term.'
                )
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_active = user.account_status == CustomUser.STATUS_ACTIVE
        if commit:
            user.save()
            self._sync_officer_assignment(user)
        return user

    def _sync_officer_assignment(self, user):
        """v2.1.12 (Issue 3): create/update the user's officer assignment."""
        from .models import OfficerAssignment
        import datetime as _dt
        pos = self.cleaned_data.get('officer_position')
        is_current = bool(self.cleaned_data.get('officer_is_current', True))
        start_date = self.cleaned_data.get('officer_start_date') or _dt.date.today()
        current = user.get_current_officer_assignment()
        if pos is None:
            # User cleared the officer position — close existing current term if any.
            if current:
                current.close()
            return None
        if current and current.position_id == pos.pk:
            # Same position — just sync start_date / is_current.
            update_fields = []
            if current.start_date != start_date:
                current.start_date = start_date
                update_fields.append('start_date')
            if current.is_current != is_current:
                current.is_current = is_current
                update_fields.append('is_current')
                if not is_current and not current.end_date:
                    current.end_date = _dt.date.today()
                    update_fields.append('end_date')
            if update_fields:
                update_fields.append('updated_at')
                current.save(update_fields=update_fields)
            return current
        # New/changed position — close the previous active term (if any) and create.
        if current:
            current.close()
        return OfficerAssignment.objects.create(
            user=user,
            position=pos,
            start_date=start_date,
            is_current=is_current,
        )


class MyProfileForm(forms.ModelForm):
    """
    Self-service "My Profile" editing (v2.1.19 UX pass — accounts:my_profile).

    Deliberately excludes username, role, account_status, and officer
    position: those stay read-only on this page (shown separately in the
    view context) so a user can never activate/deactivate themselves or
    change their own System Role. Password is handled by the existing
    accounts:change_password flow, not mixed in here.
    """
    class Meta:
        model = CustomUser
        fields = ['first_name', 'middle_name', 'last_name', 'suffix', 'email', 'phone']
        widgets = {
            'first_name':  forms.TextInput(attrs={'class': 'form-control'}),
            'middle_name': forms.TextInput(attrs={'class': 'form-control'}),
            'last_name':   forms.TextInput(attrs={'class': 'form-control'}),
            'suffix':      forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Jr., Sr., III'}),
            'email':       forms.EmailInput(attrs={'class': 'form-control'}),
            'phone':       forms.TextInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['first_name'].required = True
        self.fields['last_name'].required = True
        self.fields['email'].required = True
        self.fields['phone'].required = False

    def clean_phone(self):
        value = self.cleaned_data.get('phone', '')
        _validate_ph_phone(value)
        return value

    def clean_email(self):
        email = self.cleaned_data.get('email', '')
        _validate_unique_email(email, exclude_pk=getattr(self.instance, 'pk', None))
        return email
