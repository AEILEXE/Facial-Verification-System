# FaceVerificationMiddleware was intentionally removed (2026-05-14).
#
# The gate required every authenticated staff member to scan their own face
# before each session in addition to username/password login. It was removed
# because:
#
#   1. UserFaceEmbedding (migration 0015) was just introduced — no staff face
#      data exists in existing deployments, so the gate would block all staff.
#   2. The file-upload UI (plain <input type="file">) was inconsistent with the
#      webcam-based flow used for beneficiary verification everywhere else.
#   3. Username/password authentication with session-based lockout is sufficient
#      for staff access control in a barangay LAN deployment.
#
# The UserFaceEmbedding model and is_face_verification_valid() helper in
# verification/views.py remain in place. A future re-implementation can use
# the webcam capture path (matching verify_submit) once staff face enrollment
# is populated. The verify-face/ URL route in verification/urls.py is also
# commented out pending that re-implementation.


class DynamicCookieSecurityMiddleware:
    """
    v2.2.0 Phase 3 fix — "refreshing the page logs the user out."

    Root cause: SESSION_COOKIE_SECURE / CSRF_COOKIE_SECURE are True by
    default in production (correctly — this app is normally served over
    HTTPS via Caddy). But this system ALSO supports a documented plain-HTTP
    LAN-IP fallback (Waitress on :8000, no Caddy in front) for sites/clients
    where HTTPS isn't reachable. A browser silently discards any cookie
    marked Secure if it was received over plain HTTP (RFC 6265bis) — so a
    session created while using the HTTP fallback never survives a refresh,
    even though login itself appeared to succeed.

    Fix: for a request that genuinely arrived over HTTPS (request.is_secure()
    — which already accounts for SECURE_PROXY_SSL_HEADER/Caddy's
    X-Forwarded-Proto), change nothing; the Secure flag stays and behaves
    exactly as before. Only for a request that arrived over plain HTTP does
    this middleware strip the Secure attribute from the session/csrf cookies
    Django just set on the response — so they actually get stored and sent
    back on the next (HTTP) request. This is scoped to per-request reality,
    not a global downgrade: the HTTPS path's security is completely
    unaffected, and the global SESSION_COOKIE_SECURE/CSRF_COOKIE_SECURE
    settings stay True (this fixes the one path they were previously too
    blunt an instrument to handle correctly).

    Placed first in MIDDLEWARE so its response-phase code runs LAST — after
    SessionMiddleware and CsrfViewMiddleware have already set their cookies.
    """
    _COOKIE_NAMES = ('sessionid', 'csrftoken')

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if not request.is_secure():
            for name in self._COOKIE_NAMES:
                if name in response.cookies:
                    response.cookies[name]['secure'] = False
        return response


class AccountStatusMiddleware:
    """
    v2.1.16 Security Hardening Round #2 (H-05) — session revocation on
    suspend/deactivate.

    Django's session/auth stack authenticates once at login; nothing
    re-checks is_active/account_status on later requests from an
    already-logged-in session by default. AuthenticationMiddleware DOES
    re-fetch the user row from the DB each request (so request.user reflects
    the current is_active value), but nothing previously consumed that
    freshness — @login_required only checks is_authenticated, not whether
    the account is still active/current. An administrator suspending or
    deactivating a user mid-session had no effect on that user's existing
    session: they could keep using every page they already had open until
    the session naturally expired on its own.

    Placed after AuthenticationMiddleware (needs request.user) and after
    MessageMiddleware (uses django.contrib.messages) in MIDDLEWARE. For an
    authenticated request whose account is no longer active/current, this
    logs the session out immediately, so @login_required and the login page
    take over from here exactly as they would for any other unauthenticated
    visitor — normal login flow and role permissions for still-active users
    are completely unaffected.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated and not user.can_login:
            from django.contrib import messages
            from django.contrib.auth import logout
            logout(request)
            messages.error(
                request,
                'Your account is no longer active. Contact an administrator if you believe this is an error.',
            )
        return self.get_response(request)
