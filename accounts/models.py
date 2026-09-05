from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models
from django.conf import settings
import uuid


class CustomUserManager(UserManager):
    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)
        extra_fields.setdefault('role', 'it')

        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')

        return super().create_superuser(username, email, password, **extra_fields)


class CustomUser(AbstractUser):
    # ── System Role constants ─────────────────────────────────────────────────
    # System Role controls software permissions (what menus and actions a user
    # can access). It is SEPARATE from Officer Position (org-chart title).
    ROLE_PRESIDENT = 'president'  # Operational head; approves claims, overrides decisions
    ROLE_ADMIN     = 'admin'      # Administrative; manages users and beneficiaries
    ROLE_IT        = 'it'         # Technical; full system access, connection diagnostics
    ROLE_STAFF     = 'staff'      # Frontline; runs verifications, no admin access

    # Legacy constant kept for migration compatibility — no longer in ROLE_CHOICES.
    ROLE_HEAD_BRGY = 'head_brgy'  # migrated → president
    ROLE_ADMIN_IT  = 'admin_it'   # migrated → admin

    # User-facing label for ROLE_IT. Barangay operations have no "IT Officer"
    # position — this role exists for researchers/developers/technical staff
    # doing diagnostics, security review, and controlled biometric evaluation.
    # Kept as a constant so every OTHER display site (forms.py's
    # _ACTIVE_ROLE_CHOICES, badges, get_role_display below) stays in sync.
    #
    # NOTE: the model field's own ROLE_CHOICES below deliberately keeps the
    # legacy 'IT' label, NOT this constant — makemigrations treats a
    # CharField's `choices=` kwarg as migration-relevant state even though
    # choices aren't DB-enforced, so using TECHNICAL_ADMIN_LABEL here would
    # generate a migration for a pure wording change. get_role_display()
    # below is the actual source of truth for the displayed label; every
    # template/badge already calls it rather than rendering choices raw.
    TECHNICAL_ADMIN_LABEL = 'Technical Administrator'

    ROLE_CHOICES = [
        (ROLE_PRESIDENT, 'President'),
        (ROLE_ADMIN,     'Admin'),
        (ROLE_IT,        'IT'),
        (ROLE_STAFF,     'Staff'),
    ]

    # Account status (maps to is_active but gives explicit suspend capability)
    STATUS_ACTIVE    = 'active'
    STATUS_INACTIVE  = 'inactive'
    STATUS_SUSPENDED = 'suspended'
    STATUS_CHOICES = [
        (STATUS_ACTIVE,    'Active'),
        (STATUS_INACTIVE,  'Inactive'),
        (STATUS_SUSPENDED, 'Suspended'),
    ]

    objects = CustomUserManager()

    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default=ROLE_STAFF,
                            help_text='Software permission level. Separate from Officer Position.')
    # v2.2.0 Post-UAT Phase 9 — first_name/last_name already come from
    # AbstractUser; these two are purely additive (no migration of existing
    # data needed, nothing is split or renamed) so first/last name history
    # is untouched for every existing account.
    middle_name = models.CharField(max_length=100, blank=True)
    suffix = models.CharField(
        max_length=20, blank=True,
        help_text='e.g. Jr., Sr., II, III',
    )
    employee_id = models.CharField(max_length=50, unique=True, null=True, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    # Profile picture kept nullable but no longer shown in UI — retained for backward compat only.
    profile_picture = models.ImageField(upload_to='users/profile_pics/', null=True, blank=True)
    account_status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default=STATUS_ACTIVE,
        help_text='Active/Inactive/Suspended. Inactive and Suspended users cannot log in.',
    )
    must_change_password = models.BooleanField(
        default=False,
        help_text='Force user to change their temporary password on next login.',
    )
    assigned_office = models.CharField(
        max_length=200, blank=True,
        help_text='Where the user belongs/works (Assigned Office / Unit). Not the officer title.',
    )

    # Audit trail for account management
    created_by = models.ForeignKey(
        'self', on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='created_users',
    )
    updated_by = models.ForeignKey(
        'self', on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='updated_users',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'fans_users'
        verbose_name = 'User'
        verbose_name_plural = 'Users'

    # v2.2.0 Post-UAT Phase 9 — overrides AbstractUser.get_full_name() (which
    # only joins first_name/last_name) so every EXISTING call site across
    # user profile, audit logs, organization chart, officer assignments,
    # notifications, and reports picks up middle name / suffix automatically,
    # without needing each template/view updated individually. Falls back to
    # '' (not username) when no name parts are set — same empty-string
    # contract the AbstractUser default has, since some callers rely on
    # `user.get_full_name() or user.username`.
    def get_full_name(self):
        parts = [self.first_name, self.middle_name, self.last_name, self.suffix]
        return ' '.join(p for p in parts if p).strip()

    # ── Role helpers ──────────────────────────────────────────────────────────

    @property
    def is_admin(self):
        """True for any non-Staff role (President, Admin, IT)."""
        return self.role in (self.ROLE_PRESIDENT, self.ROLE_ADMIN, self.ROLE_IT)

    @property
    def is_president(self):
        """True only for President — operational head with claim approval authority."""
        return self.role == self.ROLE_PRESIDENT

    @property
    def is_admin_it(self):
        """True only for IT — technical role with full system/diagnostic access."""
        return self.role == self.ROLE_IT

    @property
    def is_technical_admin(self):
        """Alias of is_admin_it — reads clearer at call sites added for the
        Technical Administrator separation (v2.1.19 UX/permissions pass)."""
        return self.role == self.ROLE_IT

    @property
    def has_financial_authority(self):
        """
        True for President/Admin only. Technical Administrator (role=IT) has
        broad READ access for diagnostics (see is_admin) but must NOT get
        automatic authority over payout release, Manual Review approval,
        financial override, special-claim approval, stipend-event
        create/edit/approve, or organizational officer assignment merely
        because it can read those pages. See FINAL PRE-EXE UX/REPORTING
        CLOSURE spec, section 7.
        """
        return self.role in (self.ROLE_PRESIDENT, self.ROLE_ADMIN)

    @property
    def is_head_barangay(self):
        """Backward-compat alias for is_president."""
        return self.role == self.ROLE_PRESIDENT

    @property
    def is_staff_member(self):
        return self.role == self.ROLE_STAFF

    @property
    def can_login(self):
        return self.is_active and self.account_status == self.STATUS_ACTIVE

    def get_current_officer_assignment(self):
        """Return the current active OfficerAssignment for this user, or None."""
        from django.utils import timezone as _tz
        today = _tz.now().date()
        return self.officer_assignments.filter(
            is_current=True,
        ).select_related('position').first()

    @classmethod
    def active_president_exists(cls, exclude_pk=None):
        """
        True if any active user currently holds System Role = President.
        `exclude_pk` lets the edit form ignore the user being edited.
        v2.1.12 — only one active System Role President is allowed.
        """
        qs = cls.objects.filter(
            role=cls.ROLE_PRESIDENT,
            is_active=True,
            account_status=cls.STATUS_ACTIVE,
        )
        if exclude_pk:
            qs = qs.exclude(pk=exclude_pk)
        return qs.exists()

    _LEGACY_DISPLAY = {
        'admin_it':  'Admin',
        'head_brgy': 'President',
    }

    def get_role_display(self):
        if self.role in self._LEGACY_DISPLAY:
            return self._LEGACY_DISPLAY[self.role]
        if self.role == self.ROLE_IT:
            return self.TECHNICAL_ADMIN_LABEL
        for value, label in self.ROLE_CHOICES:
            if value == self.role:
                return label
        return self.role

    @property
    def account_type_display(self):
        """
        Secondary badge text distinguishing a barangay operational account
        from a technical/research one. Technical Administrator is a SYSTEM
        ROLE, never an Officer Position — this is the label User Management
        shows next to it so nobody reads "Technical Administrator" as a
        barangay office.
        """
        if self.role == self.ROLE_IT:
            return 'Technical / Research Access'
        return 'Barangay Operations'

    def __str__(self):
        return f'{self.get_full_name() or self.username} ({self.get_role_display()})'


class OfficerPosition(models.Model):
    """
    Defines officer positions in the Senior Citizen Association / Barangay structure.
    Admin can add, rename, disable, or reorder positions.
    """
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    # v2.2.0 Post-UAT Phase 12 — previously `order` alone decided BOTH rank
    # and chart row, so every position (having a distinct order value) got
    # its own row and the chart rendered as a strictly linear vertical list
    # regardless of how many positions were genuinely equivalent in rank
    # (e.g. two Vice Presidents). `level` is the chart tier: positions
    # sharing a level render side-by-side as siblings; `order` now only
    # breaks ties for sort position within (and across) levels.
    level = models.PositiveSmallIntegerField(
        default=0,
        help_text='Hierarchy tier. Positions sharing the same level render side-by-side '
                   'as equivalent-rank siblings (e.g. two Vice Presidents). Lower = higher in the chart.',
    )
    order = models.PositiveSmallIntegerField(
        default=0,
        help_text='Sort order within (and across) levels — does not affect rank/tier by itself.',
    )
    is_unique = models.BooleanField(
        default=False,
        help_text='Only one active user can hold this position at a time. Board of Director is non-unique (multiple holders allowed).',
    )
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='created_positions',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # ── Default positions (populated by migration / management command) ───────
    DEFAULT_POSITIONS = [
        ('President', 1, True),
        ('Vice President Internal', 2, True),
        ('Vice President External', 3, True),
        ('Secretary', 4, True),
        ('Treasurer', 5, True),
        ('Auditor', 6, True),
        ('PRO 1', 7, False),
        ('PRO 2', 8, False),
        ('Chairman of the Board', 9, True),
        ('Vice Chairman of the Board', 10, True),
        ('Board Secretary', 11, True),
        ('Board Undersecretary', 12, True),
        ('Board of Director', 13, False),
        ('Adviser / Punong Barangay', 14, False),
    ]

    class Meta:
        db_table = 'fans_officer_positions'
        ordering = ['level', 'order', 'name']

    def __str__(self):
        return self.name

    @property
    def current_holder(self):
        """Return the current OfficerAssignment for this position, or None."""
        return self.assignments.filter(is_current=True).select_related('user').first()


class OfficerAssignment(models.Model):
    """
    Tracks which user holds which officer position, and for what term.
    Supports term history: when a new officer is assigned the previous one is closed.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='officer_assignments',
    )
    position = models.ForeignKey(
        OfficerPosition,
        on_delete=models.CASCADE,
        related_name='assignments',
    )
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    is_current = models.BooleanField(
        default=True,
        help_text='True while the user actively holds this position.',
    )
    remarks = models.TextField(blank=True)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='officer_assignments_made',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'fans_officer_assignments'
        ordering = ['-start_date']

    def __str__(self):
        status = 'current' if self.is_current else 'past'
        return f'{self.user} — {self.position} ({self.start_date}, {status})'

    def close(self, end_date=None):
        """Mark this assignment as ended."""
        from django.utils import timezone as _tz
        self.is_current = False
        self.end_date = end_date or _tz.now().date()
        self.save(update_fields=['is_current', 'end_date', 'updated_at'])


class PasswordResetRequest(models.Model):
    """
    Admin-mediated "I forgot my password and can't access my registered
    email" fallback queue (v2.2.0 Post-UAT Phase 7).

    This deployment is normally LAN-only/offline with no SMTP configured, and
    even on a site that DOES have email configured (see PasswordResetOTP /
    accounts/otp.py for the self-service email-OTP flow — Phase 6), a user
    who lost access to their registered email inbox still needs a way back
    in. A locked-out user submits this request; an admin/president reviews
    it and performs the reset through the EXISTING admin_reset_password
    view/form (accounts/views.py),
    which already sets must_change_password=True and writes an
    AuditLog.ACTION_PASSWORD_RESET entry. This model only tracks the
    request→review lifecycle, never the password itself.

    username_entered/user are kept separate: user is null when the entered
    username does not match any account, so an admin can still see and
    dismiss junk/mistaken requests without the public endpoint ever revealing
    whether a given username exists (anti-enumeration).
    """
    STATUS_PENDING  = 'pending'
    STATUS_APPROVED = 'approved'
    STATUS_REJECTED = 'rejected'
    STATUS_CHOICES = [
        (STATUS_PENDING,  'Pending Review'),
        (STATUS_APPROVED, 'Approved'),
        (STATUS_REJECTED, 'Rejected'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    username_entered = models.CharField(
        max_length=150,
        help_text='Username as typed by the requester (may not match any account).',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='password_reset_requests',
        help_text='Matched account, if username_entered corresponds to one. Null for unmatched/junk requests.',
    )
    contact_note = models.TextField(
        help_text='How to reach the requester, or why they believe they are locked out.',
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=500, blank=True)

    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='reviewed_password_reset_requests',
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True)

    class Meta:
        db_table = 'fans_password_reset_requests'
        ordering = ['-created_at']

    def __str__(self):
        return f'PasswordResetRequest [{self.status}] for {self.username_entered}'


class PasswordResetOTP(models.Model):
    """
    Self-service email-OTP password recovery (v2.2.0 Post-UAT Phase 6).

    One row per OTP issued. The code itself is never stored in plaintext —
    only its Django password-hasher digest (make_password/check_password),
    the same mechanism used for account passwords. A row exists only for a
    real, resolved user; the "does this account exist" ambiguity required
    for anti-enumeration is handled entirely in the view layer (accounts/otp.py)
    by never creating a row — and always showing the same generic response —
    when the submitted username/email does not match any account.

    Lifecycle: requested -> (emailed) -> verified -> consumed (password set).
    `is_valid_for_verification` / `is_valid_for_reset` gate each step so an
    expired, already-consumed, or attempts-exhausted code can never be reused.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='password_reset_otps',
    )
    code_hash = models.CharField(
        max_length=128,
        help_text='make_password() digest of the 6-digit code — never the plaintext code.',
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    verified_at = models.DateTimeField(
        null=True, blank=True,
        help_text='Set once the correct code has been entered; required before the reset step.',
    )
    consumed_at = models.DateTimeField(
        null=True, blank=True,
        help_text='Set once this OTP has actually been used to change the password.',
    )
    invalidated_at = models.DateTimeField(
        null=True, blank=True,
        help_text='Set when a newer OTP was issued for the same user, superseding this one.',
    )
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=500, blank=True)

    class Meta:
        db_table = 'fans_password_reset_otps'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'created_at']),
        ]

    def __str__(self):
        return f'PasswordResetOTP for {self.user.username} (created {self.created_at})'

    @property
    def is_expired(self) -> bool:
        from django.utils import timezone as _tz
        return _tz.now() >= self.expires_at

    @property
    def is_spent(self) -> bool:
        """True if this code can never be used again for any purpose."""
        return bool(self.consumed_at or self.invalidated_at)

    @property
    def is_valid_for_verification(self) -> bool:
        from django.conf import settings as _settings
        max_attempts = getattr(_settings, 'OTP_MAX_ATTEMPTS', 5)
        return (
            not self.is_spent
            and not self.is_expired
            and self.verified_at is None
            and self.attempts < max_attempts
        )

    @property
    def is_valid_for_reset(self) -> bool:
        from django.conf import settings as _settings
        window_min = getattr(_settings, 'OTP_VERIFIED_SESSION_MINUTES', 10)
        if self.is_spent or self.verified_at is None:
            return False
        import datetime as _dt
        from django.utils import timezone as _tz
        return _tz.now() < self.verified_at + _dt.timedelta(minutes=window_min)

    def check_code(self, raw_code: str) -> bool:
        from django.contrib.auth.hashers import check_password
        return check_password(raw_code, self.code_hash)

    def mark_verified(self):
        from django.utils import timezone as _tz
        self.verified_at = _tz.now()
        self.save(update_fields=['verified_at'])

    def mark_consumed(self):
        from django.utils import timezone as _tz
        self.consumed_at = _tz.now()
        self.save(update_fields=['consumed_at'])
