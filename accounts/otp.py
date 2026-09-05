"""
Self-service email-OTP password recovery helpers (v2.2.0 Post-UAT Phase 6).

Kept separate from views.py so the crypto/rate-limit/email plumbing can be
unit-tested without going through the request/response cycle, and so
views.py stays focused on request handling.
"""
import logging
import secrets
import time

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.core.cache import cache
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone

from logs.models import AuditLog, Notification

logger = logging.getLogger(__name__)


def generate_otp_code() -> str:
    """Cryptographically secure N-digit numeric code (default 6 digits)."""
    length = getattr(settings, 'OTP_LENGTH', 6)
    lower = 10 ** (length - 1)
    upper = (10 ** length) - 1
    return str(secrets.randbelow(upper - lower + 1) + lower)


# ── Per-IP request throttling ────────────────────────────────────────────────
# Mirrors accounts/views.py's login-lockout pattern: a cache-backed counter,
# not a DB row, because this must throttle requests for identifiers that
# don't resolve to any account too (a DB row can't exist for those).

def _rate_limit_key(ip: str) -> str:
    return f'fans:otp_request:{ip}'


def is_rate_limited(ip: str) -> bool:
    limit = getattr(settings, 'OTP_REQUEST_RATE_LIMIT', 5)
    count = cache.get(_rate_limit_key(ip), 0)
    return count >= limit


def record_request(ip: str):
    window = getattr(settings, 'OTP_REQUEST_RATE_WINDOW_S', 900)
    key = _rate_limit_key(ip)
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=window)
        count = 1
    return count


# ── Resend cooldown ──────────────────────────────────────────────────────────

def _cooldown_key(user_id) -> str:
    return f'fans:otp_cooldown:{user_id}'


def seconds_until_resend_allowed(user) -> int:
    """0 if a new OTP may be issued now, else seconds remaining to wait."""
    until = cache.get(_cooldown_key(user.pk))
    if not until:
        return 0
    remaining = int(until - time.time())
    return max(remaining, 0)


def _start_cooldown(user):
    cooldown = getattr(settings, 'OTP_RESEND_COOLDOWN_S', 60)
    cache.set(_cooldown_key(user.pk), time.time() + cooldown, timeout=cooldown)


def try_start_cooldown(user) -> bool:
    """
    Atomically claim the resend-cooldown slot for `user`, in one step.

    v2.1.16 (Security Hardening Round #2, H-04): callers previously gated
    issue_otp() with `seconds_until_resend_allowed(user) == 0` — a
    check-then-act pattern. Two near-simultaneous requests (a double-click,
    or a scripted burst) could both read "cooldown not active" before either
    had written the cooldown key, so both would proceed to issue_otp(),
    invalidating each other's freshly-created OTP and sending two emails.

    cache.add() is atomic set-if-absent at the cache backend level: only the
    first caller to reach it for a given user gets True back; every
    concurrent competitor gets False and must not issue. Returns True if
    this call claimed the slot (caller may proceed to issue_otp()), False if
    another request already holds it.
    """
    cooldown = getattr(settings, 'OTP_RESEND_COOLDOWN_S', 60)
    return cache.add(_cooldown_key(user.pk), time.time() + cooldown, timeout=cooldown)


# ── Issuing + sending ────────────────────────────────────────────────────────

def issue_otp(user, request=None):
    """
    Invalidate any still-usable OTPs for this user, create a fresh one, and
    email it. Returns (otp, raw_code, email_sent: bool). raw_code is only
    for handing to the email sender — callers must never log or persist it.
    """
    import datetime
    from django.db import transaction
    from .models import PasswordResetOTP

    now = timezone.now()
    raw_code = generate_otp_code()
    expiry_minutes = getattr(settings, 'OTP_EXPIRY_MINUTES', 5)
    ip = None
    ua = ''
    if request is not None:
        from logs.models import get_client_ip
        ip = get_client_ip(request)
        ua = request.META.get('HTTP_USER_AGENT', '')[:500]

    # v2.1.16 (Security Hardening Round #2, H-04): invalidate-then-create was
    # two separate statements with no transaction around them — a request
    # failing/erroring between them could leave a user with no usable OTP row
    # at all (a starved, not exploitable, state, but still worth closing).
    # Atomic here also gives the two writes consistent all-or-nothing
    # behavior under concurrent callers, on top of the cooldown-based
    # exclusion already enforced by try_start_cooldown() at the call sites.
    with transaction.atomic():
        PasswordResetOTP.objects.filter(
            user=user, consumed_at__isnull=True, invalidated_at__isnull=True,
        ).update(invalidated_at=now)

        otp = PasswordResetOTP.objects.create(
            user=user,
            code_hash=make_password(raw_code),
            expires_at=now + datetime.timedelta(minutes=expiry_minutes),
            ip_address=ip,
            user_agent=ua,
        )

    AuditLog.log(
        action=AuditLog.ACTION_OTP_REQUESTED,
        user=user,
        target_type='PasswordResetOTP',
        target_id=otp.id,
        details={'username': user.username},
        request=request,
    )

    _start_cooldown(user)
    email_sent = send_otp_email(user, raw_code, expiry_minutes)

    AuditLog.log(
        action=AuditLog.ACTION_OTP_SENT,
        user=user,
        target_type='PasswordResetOTP',
        target_id=otp.id,
        # Never log the code itself — only whether delivery succeeded.
        details={'username': user.username, 'email_sent': email_sent},
        request=request,
    )
    return otp, raw_code, email_sent


def send_otp_email(user, raw_code: str, expiry_minutes: int) -> bool:
    """Best-effort send; failures are logged server-side but never surfaced
    to the caller in a way that would let an attacker distinguish 'account
    doesn't exist' from 'email failed to send' — both look identical to the
    end user (generic success message either way)."""
    if not getattr(settings, 'EMAIL_CONFIGURED', False):
        logger.warning('OTP email not sent for user=%s: SMTP not configured.', user.username)
        return False
    if not user.email:
        logger.warning('OTP email not sent for user=%s: no registered email on file.', user.username)
        return False
    try:
        ctx = {'user': user, 'raw_code': raw_code, 'expiry_minutes': expiry_minutes}
        text_body = render_to_string('accounts/emails/otp_code.txt', ctx)
        html_body = render_to_string('accounts/emails/otp_code.html', ctx)
        email = EmailMultiAlternatives(
            subject='FANS-C Password Reset Code',
            body=text_body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[user.email],
        )
        email.attach_alternative(html_body, 'text/html')
        email.send(fail_silently=False)
        return True
    except Exception:
        logger.exception('Failed to send password reset OTP email to user=%s', user.username)
        return False


def send_password_changed_confirmation(user, request=None):
    """Best-effort security notification after a successful OTP reset."""
    if getattr(settings, 'EMAIL_CONFIGURED', False) and user.email:
        try:
            ctx = {'user': user}
            text_body = render_to_string('accounts/emails/password_changed.txt', ctx)
            html_body = render_to_string('accounts/emails/password_changed.html', ctx)
            email = EmailMultiAlternatives(
                subject='FANS-C Password Changed',
                body=text_body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[user.email],
            )
            email.attach_alternative(html_body, 'text/html')
            email.send(fail_silently=True)
        except Exception:
            logger.exception('Failed to send password-changed confirmation to user=%s', user.username)

    # In-app notification mirrors the email so the event is visible even
    # when SMTP isn't configured for this site.
    from logs.notifications import notify_user
    notify_user(
        user,
        category=Notification.CATEGORY_SECURITY_ALERT,
        title='Your password was changed',
        message='Your password was just changed using the email verification code flow.',
    )
