import logging
import time

from django.conf import settings
from django.core.cache import cache
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse_lazy, reverse
from django.utils import timezone
from django.contrib.auth import logout, update_session_auth_hash
from django.contrib.auth.views import LoginView
from django.contrib import messages
from django.views.decorators.http import require_http_methods
from django.contrib.auth.decorators import login_required

from .forms import (
    LoginForm, PasswordChangeForm, AdminPasswordResetForm, CreateAdminForm,
    BootstrapPresidentForm,
    UserCreateFullForm, UserEditFullForm, MyProfileForm,
    OfficerPositionForm, OfficerAssignmentForm,
    PasswordResetRequestForm,
)
from .models import CustomUser, OfficerPosition, OfficerAssignment, PasswordResetRequest
from logs.models import AuditLog, Notification
from logs.notifications import notify_admins

logger = logging.getLogger(__name__)


# ─── Login brute-force throttling ────────────────────────────────────────────
# Per-IP failure counter held in the Django cache so it works across restarts
# only if a persistent cache backend is configured. With the default LocMem
# cache this resets on restart, which is acceptable: an attacker who can crash
# the worker has bigger problems than a counter reset.

def _client_ip(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '0.0.0.0')


def _failure_keys(ip):
    return f'fans:login_fail:{ip}', f'fans:login_lock:{ip}'


def _is_locked(ip):
    _, lock_key = _failure_keys(ip)
    until = cache.get(lock_key)
    if not until:
        return False, 0
    remaining = int(until - time.time())
    if remaining <= 0:
        cache.delete(lock_key)
        return False, 0
    return True, remaining


def _record_failure(ip):
    fail_key, lock_key = _failure_keys(ip)
    window = getattr(settings, 'LOGIN_LOCKOUT_WINDOW_S', 600)
    max_fails = getattr(settings, 'LOGIN_MAX_FAILED_ATTEMPTS', 8)
    duration = getattr(settings, 'LOGIN_LOCKOUT_DURATION_S', 900)
    try:
        count = cache.incr(fail_key)
    except ValueError:
        cache.set(fail_key, 1, timeout=window)
        count = 1
    if count >= max_fails and not cache.get(lock_key):
        lock_until = time.time() + duration
        cache.set(lock_key, lock_until, timeout=duration)
        # Notify admins only at the moment of lockout (not on every failed
        # attempt) — a meaningful security event, not noise. Dedupe key
        # includes the lockout expiry so a fresh lockout after this one
        # expires can notify again.
        try:
            notify_admins(
                category=Notification.CATEGORY_SECURITY_ALERT,
                title='Repeated login failures — IP locked out',
                message=f'{ip} was locked out after {count} failed sign-in attempts.',
                url=reverse('logs:audit_logs') + '?action=login_failed',
                dedupe_key=f'security_alert_lockout:{ip}:{int(lock_until)}',
            )
        except Exception:
            logger.exception('Failed to create login-lockout security notification')


def _clear_failures(ip):
    fail_key, lock_key = _failure_keys(ip)
    cache.delete(fail_key)
    cache.delete(lock_key)


class UserLoginView(LoginView):
    template_name = 'accounts/login.html'
    authentication_form = LoginForm
    redirect_authenticated_user = True
    success_url = reverse_lazy('beneficiaries:dashboard')

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect('beneficiaries:dashboard')

        self.login_ip = _client_ip(request)
        locked, remaining = _is_locked(self.login_ip)
        if locked:
            try:
                AuditLog.log(
                    action=AuditLog.ACTION_LOGIN_FAILED,
                    details={
                        'username': request.POST.get('username', '') if request.method == 'POST' else '',
                        'reason': 'Locked out — too many failed attempts',
                        'lockout_remaining_seconds': remaining,
                    },
                    request=request,
                )
            except Exception:
                logger.exception('Failed to write login lockout audit event')

            messages.error(
                request,
                f'Too many failed sign-in attempts. Please try again in about {max(1, remaining // 60)} minute(s).'
            )
            return self.render_to_response(self.get_context_data())

        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        user = form.get_user()
        _clear_failures(self.login_ip)
        self.request.session['face_verified'] = False
        try:
            AuditLog.log(
                action=AuditLog.ACTION_LOGIN,
                user=user,
                details={'username': user.username},
                request=self.request,
            )
        except Exception:
            logger.exception('Failed to write login audit event')

        display_name = user.get_full_name() or user.username
        messages.success(
            self.request,
            f'Welcome back, {display_name} — {user.get_role_display()}',
            extra_tags='welcome-toast',
        )
        response = super().form_valid(form)
        if user.must_change_password:
            messages.warning(
                self.request,
                'Your temporary password must be changed before you can continue.'
            )
            return redirect('accounts:change_password')
        return response

    def form_invalid(self, form):
        username = self.request.POST.get('username', '')
        _record_failure(self.login_ip)
        try:
            AuditLog.log(
                action=AuditLog.ACTION_LOGIN_FAILED,
                details={'username': username, 'reason': 'Invalid credentials'},
                request=self.request,
            )
        except Exception:
            logger.exception('Failed to write failed login audit event')

        messages.error(self.request, 'Invalid username or password.')
        return super().form_invalid(form)


@login_required
def logout_view(request):
    AuditLog.log(
        action=AuditLog.ACTION_LOGOUT,
        user=request.user,
        details={'username': request.user.username},
        request=request
    )
    # Fully invalidate the session to prevent reuse
    request.session.flush()
    logout(request)
    messages.info(request, 'You have been logged out.')
    return redirect('accounts:login')


# ─── My Profile (self-service) ────────────────────────────────────────────────

@login_required
@require_http_methods(['GET', 'POST'])
def my_profile(request):
    """
    Self-service editing of one's OWN name/email/phone. Username, System
    Role, Account Status, and Officer Position are read-only here — this is
    the route user_edit_full's "Use the profile settings…" message pointed
    to before it existed (v2.1.19 UX pass: that route was previously a dead
    end). Password changes stay on accounts:change_password.
    """
    form = MyProfileForm(request.POST or None, instance=request.user)
    if request.method == 'POST' and form.is_valid():
        _old_email = CustomUser.objects.get(pk=request.user.pk).email
        user = form.save(commit=False)
        user.updated_by = request.user
        user.save()
        email_changed = user.email != _old_email
        AuditLog.log(
            action=AuditLog.ACTION_USER_UPDATE,
            user=request.user,
            target_type='CustomUser',
            target_id=user.id,
            details={
                'username': user.username,
                'event': 'self_profile_update',
                'email_changed': email_changed,
                'old_email': _old_email if email_changed else None,
                'new_email': user.email if email_changed else None,
            },
            request=request,
        )
        messages.success(request, 'Your profile has been updated.')
        return redirect('accounts:my_profile')

    return render(request, 'accounts/my_profile.html', {
        'form': form,
        'officer_assignment': request.user.get_current_officer_assignment(),
    })


# ─── Password Management ──────────────────────────────────────────────────────

@login_required
@require_http_methods(['GET', 'POST'])
def change_password(request):
    """Any logged-in user may change their own password."""
    form = PasswordChangeForm(user=request.user, data=request.POST or None)

    if request.method == 'POST' and form.is_valid():
        form.save()
        # Keep the session alive after password change so the user isn't logged out.
        update_session_auth_hash(request, form.user)
        if request.user.must_change_password:
            request.user.must_change_password = False
            request.user.save(update_fields=['must_change_password'])
        AuditLog.log(
            action=AuditLog.ACTION_PASSWORD_CHANGE,
            user=request.user,
            target_type='CustomUser',
            target_id=request.user.id,
            details={'username': request.user.username},
            request=request,
        )
        messages.success(request, 'Your password has been changed successfully.')
        return redirect('beneficiaries:dashboard')

    return render(request, 'accounts/change_password.html', {'form': form})


@login_required
@require_http_methods(['GET', 'POST'])
def admin_reset_password(request, user_id):
    """
    President, Admin, or IT resets another user's password.
    Staff may NOT use this view. A user cannot reset their own password here
    (use change_password instead).

    May be reached with a ?prr=<PasswordResetRequest id> query/POST param when
    an admin is actioning a self-service request from password_reset_request_list.
    When present, a successful reset also closes out that request in the same
    transaction (double-approval-safe via select_for_update) so two admins
    cannot both consume the same pending request.
    """
    if not request.user.is_admin:
        messages.error(request, 'Admin access required.')
        return redirect('beneficiaries:dashboard')

    target_user = get_object_or_404(CustomUser, pk=user_id)

    if target_user == request.user:
        messages.info(request, 'Use "Change Password" to update your own password.')
        return redirect('accounts:change_password')

    # Only President can reset another admin-level account (Admin / IT / President).
    if not request.user.is_president and target_user.is_admin:
        messages.error(
            request,
            'Only the President can reset passwords for admin-level accounts.'
        )
        return redirect('accounts:user_list')

    prr_id = request.GET.get('prr') or request.POST.get('prr')
    prr = None
    if prr_id:
        prr = PasswordResetRequest.objects.filter(
            pk=prr_id, status=PasswordResetRequest.STATUS_PENDING, user=target_user,
        ).first()
        if prr is None:
            messages.warning(
                request,
                'That self-service reset request is no longer pending (already resolved, or does not match this account).'
            )

    form = AdminPasswordResetForm(data=request.POST or None)

    if request.method == 'POST' and form.is_valid():
        from django.db import transaction as _transaction

        with _transaction.atomic():
            if prr_id:
                # Re-fetch and lock inside the transaction to guard against a
                # second admin approving the same request concurrently.
                prr = PasswordResetRequest.objects.select_for_update().filter(
                    pk=prr_id, status=PasswordResetRequest.STATUS_PENDING, user=target_user,
                ).first()

            target_user.set_password(form.cleaned_data['new_password1'])
            target_user.must_change_password = True
            target_user.save()

            details = {
                'reset_by': request.user.username,
                'target_user': target_user.username,
                'target_role': target_user.role,
                'reason': form.cleaned_data.get('reset_reason', ''),
            }
            if prr is not None:
                prr.status = PasswordResetRequest.STATUS_APPROVED
                prr.reviewed_by = request.user
                prr.reviewed_at = timezone.now()
                prr.review_notes = form.cleaned_data.get('reset_reason', '')
                prr.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'review_notes'])
                details['source'] = 'self_service_request'
                details['request_id'] = str(prr.id)

                from logs.notifications import resolve_notification
                resolve_notification(f'password_reset_request:{prr.id}')
                resolve_notification(f'approval_reminder_password_reset:{prr.id}')

            AuditLog.log(
                action=AuditLog.ACTION_PASSWORD_RESET,
                user=request.user,
                target_type='CustomUser',
                target_id=target_user.id,
                details=details,
                request=request,
            )
        messages.success(
            request,
            f'Password for {target_user.get_full_name() or target_user.username} has been reset.'
        )
        return redirect('accounts:user_list')

    return render(request, 'accounts/admin_reset_password.html', {
        'form': form,
        'target_user': target_user,
        'prr': prr,
    })


# ─── Self-Service Email-OTP Password Reset (v2.2.0 Post-UAT Phase 6) ─────────
# Three steps, each gated by a session marker so a step can't be skipped:
#   otp_forgot_password  -> sets session['otp_identifier']
#   otp_verify            -> sets session['otp_verified_id'] (consumes the marker above)
#   otp_reset_password    -> clears both once the password is actually changed
#
# Anti-enumeration is maintained throughout: whether or not the submitted
# username/email resolves to a real account, the response, redirect target,
# and generic wording are identical. Only server-side logs (AuditLog) ever
# record which case actually happened.

_OTP_SESSION_IDENTIFIER = 'otp_identifier'
_OTP_SESSION_VERIFIED_ID = 'otp_verified_id'

_OTP_GENERIC_SENT_MESSAGE = (
    'If an account matching the information provided exists, a verification '
    'code has been sent to its registered email address.'
)


def _resolve_otp_user(identifier: str):
    identifier = (identifier or '').strip()
    if not identifier:
        return None
    user = CustomUser.objects.filter(username=identifier).first()
    if user is None and '@' in identifier:
        user = CustomUser.objects.filter(email__iexact=identifier).first()
    if user is None or not user.is_active or user.account_status != CustomUser.STATUS_ACTIVE:
        return None
    return user


@require_http_methods(['GET', 'POST'])
def otp_forgot_password(request):
    from .forms import OTPRequestForm
    from . import otp as otp_lib

    form = OTPRequestForm(data=request.POST or None)

    if request.method == 'POST' and form.is_valid():
        ip = _client_ip(request)
        identifier = form.cleaned_data['identifier']

        if otp_lib.is_rate_limited(ip):
            AuditLog.log(
                action=AuditLog.ACTION_OTP_RATE_LIMITED,
                user=None,
                details={'ip': ip},
                request=request,
            )
            messages.error(
                request,
                'Too many password reset requests from this location. Please try again later.'
            )
            return render(request, 'accounts/otp_forgot_password.html', {'form': form})

        otp_lib.record_request(ip)
        matched_user = _resolve_otp_user(identifier)

        # v2.1.16 (Security Hardening Round #2, H-04): try_start_cooldown()
        # atomically claims the cooldown slot AND decides gating in one step
        # — closes the check-then-act race the old seconds_until_resend_
        # allowed()==0 pattern had (two concurrent requests could both read
        # "not on cooldown" before either wrote the cooldown key).
        if matched_user is not None and otp_lib.try_start_cooldown(matched_user):
            otp_lib.issue_otp(matched_user, request)
        elif matched_user is None:
            # No DB row, no email — but log the attempt so a burst of requests
            # for nonexistent accounts is still visible to admins.
            AuditLog.log(
                action=AuditLog.ACTION_OTP_REQUESTED,
                user=None,
                details={'identifier_entered': identifier, 'matched_account': False},
                request=request,
            )

        request.session[_OTP_SESSION_IDENTIFIER] = identifier
        messages.success(request, _OTP_GENERIC_SENT_MESSAGE)
        return redirect('accounts:otp_verify')

    return render(request, 'accounts/otp_forgot_password.html', {'form': form})


@require_http_methods(['GET', 'POST'])
def otp_verify(request):
    from .forms import OTPVerifyForm
    from . import otp as otp_lib
    from .models import PasswordResetOTP

    identifier = request.session.get(_OTP_SESSION_IDENTIFIER)
    if not identifier:
        return redirect('accounts:otp_forgot_password')

    form = OTPVerifyForm(data=request.POST or None)
    resend_cooldown = 0
    matched_user = _resolve_otp_user(identifier)
    if matched_user is not None:
        resend_cooldown = otp_lib.seconds_until_resend_allowed(matched_user)

    if request.method == 'POST':
        if request.POST.get('resend') == '1':
            # v2.1.16 (Security Hardening Round #2, H-04): gate on an atomic
            # claim (try_start_cooldown), not the earlier check-then-act
            # `resend_cooldown == 0` read — see otp_forgot_password above.
            if matched_user is not None and otp_lib.try_start_cooldown(matched_user):
                otp_lib.issue_otp(matched_user, request)
                resend_cooldown = otp_lib.seconds_until_resend_allowed(matched_user)
            messages.success(request, 'If the code has not arrived, a new one has been sent.')
            return render(request, 'accounts/otp_verify.html', {
                'form': OTPVerifyForm(), 'resend_cooldown': resend_cooldown,
            })

        if form.is_valid():
            code = form.cleaned_data['code']
            otp_row = None
            if matched_user is not None:
                otp_row = (
                    PasswordResetOTP.objects
                    .filter(user=matched_user)
                    .order_by('-created_at')
                    .first()
                )

            verified = False
            if otp_row is not None and otp_row.is_valid_for_verification:
                if otp_row.check_code(code):
                    otp_row.mark_verified()
                    verified = True
                    AuditLog.log(
                        action=AuditLog.ACTION_OTP_VERIFIED,
                        user=matched_user,
                        target_type='PasswordResetOTP',
                        target_id=otp_row.id,
                        request=request,
                    )
                else:
                    # v2.1.16 (Security Hardening Round #2, H-04): F()
                    # increments at the DB level instead of a Python
                    # read-modify-write, so two concurrent wrong-code
                    # submissions against the same OTP row can't lose an
                    # increment (which would otherwise let OTP_MAX_ATTEMPTS
                    # be undercounted).
                    from django.db.models import F
                    PasswordResetOTP.objects.filter(pk=otp_row.pk).update(attempts=F('attempts') + 1)
                    otp_row.refresh_from_db(fields=['attempts'])

            if not verified:
                AuditLog.log(
                    action=AuditLog.ACTION_OTP_FAILED,
                    user=matched_user,
                    details={'reason': 'invalid_or_expired_code'},
                    request=request,
                )
                messages.error(request, 'That code is invalid or has expired. Please try again.')
            else:
                request.session[_OTP_SESSION_VERIFIED_ID] = str(otp_row.id)
                del request.session[_OTP_SESSION_IDENTIFIER]
                return redirect('accounts:otp_reset_password')

    return render(request, 'accounts/otp_verify.html', {
        'form': form, 'resend_cooldown': resend_cooldown,
    })


@require_http_methods(['GET', 'POST'])
def otp_reset_password(request):
    from .forms import OTPSetPasswordForm
    from .models import PasswordResetOTP
    from . import otp as otp_lib

    otp_id = request.session.get(_OTP_SESSION_VERIFIED_ID)
    otp_row = None
    if otp_id:
        otp_row = PasswordResetOTP.objects.filter(pk=otp_id).select_related('user').first()

    if otp_row is None or not otp_row.is_valid_for_reset:
        request.session.pop(_OTP_SESSION_VERIFIED_ID, None)
        messages.error(request, 'This password reset session has expired. Please start again.')
        return redirect('accounts:otp_forgot_password')

    target_user = otp_row.user
    form = OTPSetPasswordForm(data=request.POST or None, user=target_user)

    if request.method == 'POST' and form.is_valid():
        target_user.set_password(form.cleaned_data['new_password1'])
        target_user.must_change_password = False
        target_user.save(update_fields=['password', 'must_change_password'])
        otp_row.mark_consumed()
        request.session.pop(_OTP_SESSION_VERIFIED_ID, None)

        AuditLog.log(
            action=AuditLog.ACTION_OTP_RESET_DONE,
            user=target_user,
            target_type='PasswordResetOTP',
            target_id=otp_row.id,
            request=request,
        )
        otp_lib.send_password_changed_confirmation(target_user, request)

        messages.success(request, 'Your password has been changed. You may now sign in.')
        return redirect('accounts:login')

    return render(request, 'accounts/otp_reset_password.html', {'form': form})


def password_reset_request_create(request):
    """
    Public, unauthenticated 'I'm locked out' request page. Always shows the
    same generic success message regardless of whether the entered username
    matches a real account (anti-enumeration) — an admin reviews and
    dismisses/actions the request from password_reset_request_list.
    """
    form = PasswordResetRequestForm(data=request.POST or None)

    if request.method == 'POST' and form.is_valid():
        username = form.cleaned_data['username'].strip()
        matched_user = CustomUser.objects.filter(username=username).first()

        existing_pending = PasswordResetRequest.objects.filter(
            username_entered=username, status=PasswordResetRequest.STATUS_PENDING,
        ).exists()

        if not existing_pending:
            prr = PasswordResetRequest.objects.create(
                username_entered=username,
                user=matched_user,
                contact_note=form.cleaned_data['contact_note'],
                ip_address=_client_ip(request),
                user_agent=request.META.get('HTTP_USER_AGENT', '')[:500],
            )
            AuditLog.log(
                action=AuditLog.ACTION_PASSWORD_RESET_REQUEST,
                user=None,
                target_type='PasswordResetRequest',
                target_id=prr.id,
                details={
                    'username_entered': username,
                    'matched_account': matched_user is not None,
                },
                request=request,
            )
            notify_admins(
                category=Notification.CATEGORY_PASSWORD_RESET,
                title='Password reset request',
                message=f'"{username}" requested a password reset.',
                url=reverse('accounts:password_reset_request_list'),
                dedupe_key=f'password_reset_request:{prr.id}',
            )

        messages.success(
            request,
            'Your request has been submitted for review. An administrator will contact you.'
        )
        return redirect('accounts:login')

    return render(request, 'accounts/password_reset_request.html', {'form': form})


@login_required
def password_reset_request_list(request):
    """Admin/President/IT queue of pending self-service password reset requests."""
    if not request.user.is_admin:
        messages.error(request, 'Admin access required.')
        return redirect('beneficiaries:dashboard')

    requests_qs = PasswordResetRequest.objects.filter(
        status=PasswordResetRequest.STATUS_PENDING,
    ).select_related('user').order_by('created_at')

    return render(request, 'accounts/password_reset_request_list.html', {
        'requests': requests_qs,
    })


@login_required
@require_http_methods(['POST'])
def password_reset_request_reject(request, request_id):
    """Admin dismisses a pending self-service reset request without resetting anything."""
    if not request.user.is_admin:
        messages.error(request, 'Admin access required.')
        return redirect('beneficiaries:dashboard')

    from django.db import transaction as _transaction

    with _transaction.atomic():
        prr = get_object_or_404(
            PasswordResetRequest.objects.select_for_update(),
            pk=request_id, status=PasswordResetRequest.STATUS_PENDING,
        )
        prr.status = PasswordResetRequest.STATUS_REJECTED
        prr.reviewed_by = request.user
        prr.reviewed_at = timezone.now()
        prr.review_notes = request.POST.get('review_notes', '')
        prr.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'review_notes'])

        from logs.notifications import resolve_notification
        resolve_notification(f'password_reset_request:{prr.id}')
        resolve_notification(f'approval_reminder_password_reset:{prr.id}')

        AuditLog.log(
            action=AuditLog.ACTION_PASSWORD_RESET_REQUEST_REJECTED,
            user=request.user,
            target_type='PasswordResetRequest',
            target_id=prr.id,
            details={'username_entered': prr.username_entered},
            request=request,
        )

    messages.success(request, 'Request dismissed.')
    return redirect('accounts:password_reset_request_list')


@require_http_methods(['GET', 'POST'])
def create_admin(request):
    """
    Initial-setup page: create the first account — always a Technical
    Administrator (role=IT). Only accessible when no admin-tier users exist
    yet. Once one exists, redirect to login.

    v2.1.19 UX pass (section 8A): this bootstrap account is technical/
    research access for initial setup, diagnostics, and controlled
    biometric evaluation — it is NOT the barangay Administrator, and no
    longer offers a role choice. Creating the barangay's actual President
    happens next, via accounts:bootstrap_president.
    """
    from .models import CustomUser as _CU
    if _CU.objects.filter(role__in=[_CU.ROLE_PRESIDENT, _CU.ROLE_ADMIN, _CU.ROLE_IT]).exists():
        return redirect('accounts:login')

    form = CreateAdminForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        try:
            AuditLog.log(
                action=AuditLog.ACTION_USER_CREATE,
                user=user,
                details={'username': user.username, 'role': user.role, 'setup': 'initial_technical_admin'},
                request=request,
            )
        except Exception:
            logger.exception('Failed to write create_admin audit event')
        messages.success(request, f'Technical Administrator account created. You can now log in as {user.username}.')
        return redirect('accounts:login')

    return render(request, 'accounts/create_admin.html', {'form': form})


@login_required
@require_http_methods(['GET', 'POST'])
def bootstrap_president(request):
    """
    One-time initial-President setup, reachable only by the Technical
    Administrator and only while no President exists yet (v2.1.19 UX pass,
    section 8B/41). Once a President exists this route refuses to create
    another — ordinary President-to-President succession goes through
    normal User Management, not this bootstrap path.
    """
    if not request.user.is_admin_it:
        messages.error(request, 'Only the Technical Administrator may perform this setup step.')
        return redirect('beneficiaries:dashboard')

    if CustomUser.active_president_exists():
        messages.info(request, 'A President account already exists. This setup step is no longer available.')
        return redirect('beneficiaries:dashboard')

    form = BootstrapPresidentForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save(created_by=request.user)
        AuditLog.log(
            action=AuditLog.ACTION_USER_CREATE,
            user=request.user,
            target_type='CustomUser',
            target_id=user.id,
            details={
                'username': user.username, 'role': user.role,
                'setup': 'initial_president_bootstrap', 'created_by': request.user.username,
            },
            request=request,
        )
        messages.success(request, f'Initial President account created for {user.get_full_name()}.')
        return redirect('accounts:user_list')

    return render(request, 'accounts/bootstrap_president.html', {'form': form})


# ─── Enhanced User Management ─────────────────────────────────────────────────

@login_required
def user_list_full(request):
    """Enhanced user list with account_status filter and officer assignments."""
    if not request.user.is_admin:
        messages.error(request, 'Admin access required.')
        return redirect('beneficiaries:dashboard')

    status_filter = request.GET.get('status', '')
    users = CustomUser.objects.all().prefetch_related('officer_assignments__position').order_by('last_name', 'first_name')
    if status_filter:
        users = users.filter(account_status=status_filter)

    return render(request, 'accounts/user_list.html', {
        'users': users,
        'status_filter': status_filter,
        'STATUS_CHOICES': CustomUser.STATUS_CHOICES,
    })


@login_required
@require_http_methods(['GET', 'POST'])
def user_create_full(request):
    """Create a new system user with full officer management fields."""
    if not request.user.is_admin:
        messages.error(request, 'Admin access required.')
        return redirect('beneficiaries:dashboard')

    # Only the President may create a Technical Administrator account — this
    # is a raw-POST check (not just a hidden UI option), so a direct POST
    # from an Admin/IT session is rejected the same as from the form.
    if request.method == 'POST' and request.POST.get('role') == CustomUser.ROLE_IT and not request.user.is_president:
        messages.error(request, 'Only the President can create a Technical Administrator account.')
        return redirect('accounts:user_list')

    # v2.1.16 (Security Hardening #2 — CRITICAL): President creation must go
    # through the dedicated bootstrap_president flow (IT-gated, only reachable
    # while no active President exists). Without this check, an Admin/IT
    # actor could create a brand-new President account directly here —
    # combined with a President-target status change elsewhere, a full
    # privilege-escalation chain (suspend the sitting President, then create
    # a replacement). Raw-POST check, not just a hidden UI option.
    if request.method == 'POST' and request.POST.get('role') == CustomUser.ROLE_PRESIDENT and not request.user.is_president:
        messages.error(request, 'Only the President can create a President account.')
        return redirect('accounts:user_list')

    # v2.1.17 audit fix: Admin carries the same has_financial_authority as
    # President (payout release, Manual Review approval, stipend-event
    # approval, etc.) — the checks above already stop a Technical
    # Administrator (is_admin=True but not is_president) from creating an
    # IT or President account directly, but left creating a plain Admin
    # account open. That let IT create a new Admin account (whose password
    # IT itself sets) and log in as it, indirectly obtaining exactly the
    # financial authority IT must never have. Require President for Admin
    # creation too, same as IT/President.
    if request.method == 'POST' and request.POST.get('role') == CustomUser.ROLE_ADMIN and not request.user.is_president:
        messages.error(request, 'Only the President can create an Admin account.')
        return redirect('accounts:user_list')

    form = UserCreateFullForm(request.POST or None)
    if not request.user.is_president:
        form.fields['role'].choices = [
            c for c in form.fields['role'].choices
            if c[0] not in (CustomUser.ROLE_ADMIN, CustomUser.ROLE_IT, CustomUser.ROLE_PRESIDENT)
        ]
    if request.method == 'POST' and form.is_valid():
        # ModelForm.save(commit=False) skips the form's _save_officer_assignment
        # hook, so set audit fields then call full save() so the hook runs.
        user = form.save(commit=False)
        user.created_by = request.user
        user.updated_by = request.user
        # Force a real save so audit fields persist before the officer
        # assignment hook fires (which references the saved user.pk).
        user.save()
        # Now invoke the form's officer-assignment side-effect on the saved user.
        form._save_officer_assignment(user)
        AuditLog.log(
            action=AuditLog.ACTION_USER_CREATE,
            user=request.user,
            target_type='CustomUser',
            target_id=user.id,
            details={
                'username': user.username,
                'role': user.role,
                'account_status': user.account_status,
                'created_by': request.user.username,
                'officer_position': (
                    form.cleaned_data['officer_position'].name
                    if form.cleaned_data.get('officer_position') else None
                ),
            },
            request=request,
        )
        messages.success(request, f'User account created for {user.get_full_name() or user.username}.')
        return redirect('accounts:user_list')

    return render(request, 'accounts/user_form.html', {'form': form, 'action': 'Create'})


@login_required
@require_http_methods(['GET', 'POST'])
def user_edit_full(request, pk):
    """Edit a user account — role, status, office, etc."""
    if not request.user.is_admin:
        messages.error(request, 'Admin access required.')
        return redirect('beneficiaries:dashboard')

    target_user = get_object_or_404(CustomUser, pk=pk)
    # Captured before the form (bound to this same instance) mutates it during
    # is_valid()/save() — needed for the email-change audit trail below
    # (v2.2.0 Post-UAT Phase 8: registered email is a password-recovery
    # factor, so changing it is a security-sensitive action worth its own
    # explicit before/after audit record, not just the generic field dump).
    _old_email = target_user.email

    # Users cannot change their own role/status
    if target_user == request.user:
        messages.info(request, 'Use My Profile (top-right menu) to update your own account.')
        return redirect('accounts:my_profile')

    # v2.1.16 (Security Hardening #2 — CRITICAL): Only the President may
    # modify a President-level account AT ALL — not just its role. The
    # earlier role-only guard below left account_status, email (a
    # password-recovery factor), and must_change_password unprotected: an
    # Admin/IT could suspend, deactivate, or take over a President account's
    # recovery email by submitting the edit form with role left unchanged.
    # Block the entire edit up front for any non-President actor targeting
    # a President.
    if target_user.is_president and not request.user.is_president:
        messages.error(request, 'Only the President can modify a President account.')
        return redirect('accounts:user_list')

    # Only President can change the system role of an admin-level account (Admin / IT / President).
    if not request.user.is_president and target_user.is_admin and not target_user.is_staff_member:
        new_role = request.POST.get('role', '') if request.method == 'POST' else ''
        if new_role and new_role != target_user.role:
            messages.error(request, 'Only the President can change admin-level account roles.')
            return redirect('accounts:user_list')

    # Only the President may promote ANY account (including Staff) into
    # Technical Administrator, Admin, or President — raw-POST checks,
    # independent of the tier check above so they also cover Staff ->
    # Technical Administrator / Staff -> Admin / Staff -> President (the tier
    # check above only fires when the TARGET is already admin-level, which a
    # Staff account is not). The Admin case closes a v2.1.17 audit gap: Admin
    # carries the same has_financial_authority as President, so promoting a
    # Staff account to Admin is just as much an escalation for a
    # non-President (especially Technical Administrator) actor as promoting
    # to IT/President was already recognized to be.
    if request.method == 'POST':
        _new_role = request.POST.get('role', '')
        if _new_role == CustomUser.ROLE_IT and target_user.role != CustomUser.ROLE_IT and not request.user.is_president:
            messages.error(request, 'Only the President can promote an account to Technical Administrator.')
            return redirect('accounts:user_list')
        if _new_role == CustomUser.ROLE_ADMIN and target_user.role != CustomUser.ROLE_ADMIN and not request.user.is_president:
            messages.error(request, 'Only the President can promote an account to Admin.')
            return redirect('accounts:user_list')
        if _new_role == CustomUser.ROLE_PRESIDENT and target_user.role != CustomUser.ROLE_PRESIDENT and not request.user.is_president:
            messages.error(request, 'Only the President can promote an account to President.')
            return redirect('accounts:user_list')

    form = UserEditFullForm(request.POST or None, instance=target_user)
    if not request.user.is_president:
        _hidden_roles = set()
        if target_user.role != CustomUser.ROLE_IT:
            _hidden_roles.add(CustomUser.ROLE_IT)
        if target_user.role != CustomUser.ROLE_ADMIN:
            _hidden_roles.add(CustomUser.ROLE_ADMIN)
        if target_user.role != CustomUser.ROLE_PRESIDENT:
            _hidden_roles.add(CustomUser.ROLE_PRESIDENT)
        form.fields['role'].choices = [c for c in form.fields['role'].choices if c[0] not in _hidden_roles]
    if request.method == 'POST' and form.is_valid():
        user = form.save(commit=False)
        user.updated_by = request.user
        user.save()
        # v2.1.12 (Issue 3) — sync officer assignment with the saved user.
        form._sync_officer_assignment(user)

        email_changed = user.email != _old_email
        AuditLog.log(
            action=AuditLog.ACTION_USER_UPDATE,
            user=request.user,
            target_type='CustomUser',
            target_id=target_user.id,
            details={
                'username': target_user.username,
                'updated_by': request.user.username,
                'account_status': user.account_status,
                'officer_position': (
                    form.cleaned_data['officer_position'].name
                    if form.cleaned_data.get('officer_position') else None
                ),
                'email_changed': email_changed,
                'old_email': _old_email if email_changed else None,
                'new_email': user.email if email_changed else None,
            },
            request=request,
        )
        if email_changed:
            # A separate, explicitly-named audit action so "who changed whose
            # recovery email, and when" can be queried/filtered on its own —
            # this is the field an account-takeover attempt would target.
            AuditLog.log(
                action=AuditLog.ACTION_UPDATE,
                user=request.user,
                target_type='CustomUser',
                target_id=target_user.id,
                details={
                    'event': 'registered_email_changed',
                    'username': target_user.username,
                    'changed_by': request.user.username,
                    'old_email': _old_email,
                    'new_email': user.email,
                },
                request=request,
            )
        messages.success(request, f'User {target_user.get_full_name() or target_user.username} updated.')
        return redirect('accounts:user_list')

    return render(request, 'accounts/user_form.html', {
        'form': form, 'action': 'Edit', 'target_user': target_user,
    })


@login_required
@require_http_methods(['POST'])
def user_set_status(request, pk):
    """Activate, deactivate, or suspend a user account."""
    if not request.user.is_admin:
        messages.error(request, 'Admin access required.')
        return redirect('accounts:user_list')

    target_user = get_object_or_404(CustomUser, pk=pk)
    if target_user == request.user:
        messages.error(request, 'You cannot change your own account status.')
        return redirect('accounts:user_list')

    # v2.1.16 (Security Hardening #2 — CRITICAL): this view previously had no
    # role-hierarchy check at all beyond is_admin — an Admin or Technical
    # Administrator could activate, deactivate, or suspend the President
    # account outright. Only the President may change a President's status.
    if target_user.is_president and not request.user.is_president:
        messages.error(request, 'Only the President can change a President account status.')
        return redirect('accounts:user_list')

    new_status = request.POST.get('status', '')
    reason = (request.POST.get('reason', '') or '').strip()[:500]
    valid_statuses = [s[0] for s in CustomUser.STATUS_CHOICES]
    if new_status not in valid_statuses:
        messages.error(request, 'Invalid status value.')
        return redirect('accounts:user_list')

    # Deactivating or suspending is a significant action — require a reason.
    if new_status in (CustomUser.STATUS_INACTIVE, CustomUser.STATUS_SUSPENDED) and not reason:
        messages.error(
            request,
            'A reason is required when deactivating or suspending an account. Please try again.'
        )
        return redirect('accounts:user_list')

    old_status = target_user.account_status
    target_user.account_status = new_status
    target_user.is_active = new_status == CustomUser.STATUS_ACTIVE
    target_user.updated_by = request.user
    target_user.save(update_fields=['account_status', 'is_active', 'updated_by', 'updated_at'])

    action_label = {
        CustomUser.STATUS_ACTIVE: 'reactivated',
        CustomUser.STATUS_INACTIVE: 'deactivated',
        CustomUser.STATUS_SUSPENDED: 'suspended',
    }.get(new_status, 'updated')

    AuditLog.log(
        action=AuditLog.ACTION_USER_UPDATE,
        user=request.user,
        target_type='CustomUser',
        target_id=target_user.id,
        details={
            'username': target_user.username,
            'action': action_label,
            'old_status': old_status,
            'new_status': new_status,
            'changed_by': request.user.username,
            'reason': reason,
        },
        request=request,
    )
    messages.success(request, f'{target_user.get_full_name() or target_user.username} has been {action_label}.')
    return redirect('accounts:user_list')


@login_required
@require_http_methods(['GET', 'POST'])
def user_reset_password(request, pk):
    """Admin resets another user's password (delegates to accounts logic)."""
    return admin_reset_password(request, user_id=pk)


# ─── Officer Position Management ──────────────────────────────────────────────

@login_required
def officer_position_list(request):
    if not request.user.is_admin:
        messages.error(request, 'Admin access required.')
        return redirect('beneficiaries:dashboard')

    positions = OfficerPosition.objects.prefetch_related('assignments__user').order_by('order', 'name')
    return render(request, 'accounts/officer_position_list.html', {'positions': positions})


@login_required
@require_http_methods(['GET', 'POST'])
def officer_position_create(request):
    if not request.user.is_admin:
        messages.error(request, 'Admin access required.')
        return redirect('accounts:officer_position_list')

    form = OfficerPositionForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        pos = form.save(commit=False)
        pos.created_by = request.user
        pos.save()
        AuditLog.log(
            action=AuditLog.ACTION_USER_CREATE,
            user=request.user,
            target_type='OfficerPosition',
            target_id=pos.id,
            details={'name': pos.name, 'created_by': request.user.username},
            request=request,
        )
        messages.success(request, f'Position "{pos.name}" created.')
        return redirect('accounts:officer_position_list')

    return render(request, 'accounts/officer_position_form.html', {'form': form, 'action': 'Create'})


@login_required
@require_http_methods(['GET', 'POST'])
def officer_position_edit(request, pk):
    if not request.user.is_admin:
        messages.error(request, 'Admin access required.')
        return redirect('accounts:officer_position_list')

    pos = get_object_or_404(OfficerPosition, pk=pk)
    form = OfficerPositionForm(request.POST or None, instance=pos)
    if request.method == 'POST' and form.is_valid():
        form.save()
        AuditLog.log(
            action=AuditLog.ACTION_USER_UPDATE,
            user=request.user,
            target_type='OfficerPosition',
            target_id=pos.id,
            details={'name': pos.name, 'updated_by': request.user.username},
            request=request,
        )
        messages.success(request, f'Position "{pos.name}" updated.')
        return redirect('accounts:officer_position_list')

    return render(request, 'accounts/officer_position_form.html', {
        'form': form, 'action': 'Edit', 'position': pos,
    })


# ─── Officer Assignment Management ────────────────────────────────────────────

@login_required
def officer_assignment_list(request):
    if not request.user.is_admin:
        messages.error(request, 'Admin access required.')
        return redirect('beneficiaries:dashboard')

    # v2.1.19 UX pass (section 29) — 5-year usability: Current Only / All
    # History alone isn't enough to find a specific past term. Added
    # date-range, officer, position, and status filters; kept backward
    # compatible with the existing ?current= toggle (still applied first).
    current_only = request.GET.get('current', '1') == '1'
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    officer_id = request.GET.get('officer', '')
    position_id = request.GET.get('position', '')
    status_f = request.GET.get('assignment_status', '')
    assigned_by_id = request.GET.get('assigned_by', '')

    qs = OfficerAssignment.objects.select_related('user', 'position', 'assigned_by').order_by('-start_date', 'position__order')
    if current_only:
        qs = qs.filter(is_current=True)
    if date_from:
        qs = qs.filter(start_date__gte=date_from)
    if date_to:
        qs = qs.filter(start_date__lte=date_to)
    if officer_id:
        qs = qs.filter(user_id=officer_id)
    if position_id:
        qs = qs.filter(position_id=position_id)
    if status_f == 'current':
        qs = qs.filter(is_current=True)
    elif status_f == 'ended':
        qs = qs.filter(is_current=False)
    if assigned_by_id:
        qs = qs.filter(assigned_by_id=assigned_by_id)

    from django.core.paginator import Paginator
    paginator = Paginator(qs, 50)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    return render(request, 'accounts/officer_assignment_list.html', {
        'assignments': page_obj,
        'page_obj': page_obj,
        'paginator': paginator,
        'current_only': current_only,
        'date_from': date_from, 'date_to': date_to,
        'officer_id': officer_id, 'position_id': position_id, 'status_f': status_f,
        'assigned_by_id': assigned_by_id,
        'officers': CustomUser.objects.filter(officer_assignments__isnull=False).distinct().order_by('last_name', 'first_name'),
        'positions': OfficerPosition.objects.order_by('order', 'name'),
        'assigners': CustomUser.objects.filter(officer_assignments_made__isnull=False).distinct().order_by('last_name', 'first_name'),
    })


@login_required
@require_http_methods(['GET', 'POST'])
def officer_assignment_create(request):
    # Organizational officer assignment is a barangay operational decision,
    # not a technical one — Technical Administrator keeps read access to
    # officer_assignment_list/org_chart but not this mutation.
    if not request.user.has_financial_authority:
        messages.error(request, 'Only the President or Admin can assign officer positions.')
        return redirect('accounts:officer_assignment_list')

    form = OfficerAssignmentForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        pos = form.cleaned_data['position']
        user = form.cleaned_data['user']

        # Enforce uniqueness constraint for unique positions
        if pos.is_unique and form.cleaned_data.get('is_current', True):
            existing = OfficerAssignment.objects.filter(position=pos, is_current=True).exclude(user=user)
            if existing.exists():
                # Close previous holder
                for ea in existing:
                    ea.close()

        assignment = form.save(commit=False)
        assignment.assigned_by = request.user
        assignment.save()

        AuditLog.log(
            action=AuditLog.ACTION_USER_UPDATE,
            user=request.user,
            target_type='OfficerAssignment',
            target_id=assignment.id,
            details={
                'user': user.username,
                'position': pos.name,
                'start_date': str(assignment.start_date),
                'assigned_by': request.user.username,
            },
            request=request,
        )
        messages.success(request, f'{user.get_full_name()} assigned as {pos.name}.')
        return redirect('accounts:officer_assignment_list')

    return render(request, 'accounts/officer_assignment_form.html', {'form': form, 'action': 'Assign'})


@login_required
@require_http_methods(['POST'])
def officer_assignment_close(request, pk):
    """Close (end) an active officer assignment."""
    if not request.user.has_financial_authority:
        messages.error(request, 'Only the President or Admin can end an officer assignment.')
        return redirect('accounts:officer_assignment_list')

    assignment = get_object_or_404(OfficerAssignment, pk=pk)
    assignment.close()
    AuditLog.log(
        action=AuditLog.ACTION_USER_UPDATE,
        user=request.user,
        target_type='OfficerAssignment',
        target_id=assignment.id,
        details={
            'user': assignment.user.username,
            'position': assignment.position.name,
            'action': 'closed',
            'closed_by': request.user.username,
        },
        request=request,
    )
    messages.success(request, f'Assignment for {assignment.user.get_full_name()} ({assignment.position.name}) closed.')
    return redirect('accounts:officer_assignment_list')


# ─── Organization Chart ───────────────────────────────────────────────────────

@login_required
def org_chart(request):
    """
    Visual organization chart of current officer assignments, rendered as a
    tiered hierarchy: each distinct OfficerPosition.level is one tier row,
    and positions sharing a level render side-by-side as equivalent-rank
    siblings (e.g. two Vice Presidents) instead of each getting its own row
    (v2.2.0 Post-UAT Phase 12 — `order` alone used to conflate rank and sort
    position, which forced every position onto its own row regardless of
    real rank equivalence). Purely data-driven: works with whatever
    positions/levels an admin has configured, no hardcoded position names.
    """
    positions = OfficerPosition.objects.filter(
        is_active=True,
    ).prefetch_related(
        'assignments__user',
    ).order_by('level', 'order', 'name')

    tiers = {}
    for pos in positions:
        current_assignments = [a for a in pos.assignments.all() if a.is_current]
        tiers.setdefault(pos.level, []).append({
            'position': pos,
            'holders': current_assignments,
        })
    chart_tiers = [tiers[level] for level in sorted(tiers.keys())]
    chart_data = [entry for tier in chart_tiers for entry in tier]

    return render(request, 'accounts/org_chart.html', {
        'chart_tiers': chart_tiers,
        'chart_data': chart_data,
    })
