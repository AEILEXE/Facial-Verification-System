import mimetypes
import os
import sys
from pathlib import Path

# Ensure .wasm files are served with the correct MIME type so the browser
# can compile WebAssembly (required for MediaPipe FaceMesh head tracking).
mimetypes.add_type('application/wasm', '.wasm')

# ── PyInstaller path split ────────────────────────────────────────────────────
# In a PyInstaller onedir bundle:
#   sys.executable  = C:\FANSC\fans_c.exe            (writable install dir)
#   sys._MEIPASS    = C:\FANSC\_internal\             (read-only bundle files)
#
# Writable runtime data (.env, db.sqlite3, media/) must go next to fans_c.exe
# (BASE_DIR), NOT inside _internal/ which is overwritten on every reinstall.
#
# Read-only bundled assets (templates/, static/, staticfiles/) live in
# _BUNDLE_DIR (_internal/) where PyInstaller staged them.
#
# In development (not frozen) both dirs are the project root.
if getattr(sys, 'frozen', False):
    BASE_DIR    = Path(sys.executable).parent   # writable: .env, db, media, logs
    _BUNDLE_DIR = Path(sys._MEIPASS)            # read-only: templates, static
else:
    BASE_DIR    = Path(__file__).resolve().parent.parent
    _BUNDLE_DIR = BASE_DIR

# ── Environment bootstrap ──────────────────────────────────────────────────────
# Auto-load .env if present.  Missing dotenv is caught below in first-run checks.
try:
    from dotenv import load_dotenv
    _env_file = BASE_DIR / '.env'
    load_dotenv(dotenv_path=_env_file, override=True)
except ImportError:
    pass  # Warning is surfaced by check_system / first-run validation below


def _bool_env(key, default=False):
    val = os.getenv(key, '').strip().lower()
    if val in ('1', 'true', 'yes', 'on'):
        return True
    if val in ('0', 'false', 'no', 'off'):
        return False
    return default


SECRET_KEY = os.getenv('SECRET_KEY', 'django-insecure-change-this-immediately')
# Defaults are now production-safe. Local development must explicitly opt in by
# setting DEBUG=True in .env — never rely on the in-code default to be permissive.
DEBUG = _bool_env('DEBUG', default=False)
# ALLOWED_HOSTS — configure this for your deployment environment.
#
#   Development (default):
#     ALLOWED_HOSTS=localhost,127.0.0.1
#
#   Barangay LAN server — plain HTTP (basic LAN access, no camera on clients):
#     ALLOWED_HOSTS=192.168.1.77,localhost,127.0.0.1
#     Replace 192.168.1.77 with the server's LAN IP (run `ipconfig` to find it).
#
#   Barangay LAN server — secure HTTPS via Caddy (recommended, enables camera):
#     ALLOWED_HOSTS=fans-barangay.local,192.168.1.77,localhost,127.0.0.1
#     Caddy terminates HTTPS and forwards requests to Waitress/Django on
#     localhost.  The hostname fans-barangay.local must also be set in
#     CSRF_TRUSTED_ORIGINS so Django accepts form POST requests from HTTPS
#     clients.  See the Secure HTTPS LAN Deployment section in README.md.
#
#   Why HTTPS matters for camera access: browsers enforce a "secure context"
#   rule — getUserMedia (webcam) is only available on https:// or localhost.
#   A plain http://192.168.x.x URL blocks camera access on client devices.
#   HTTPS with a locally-trusted certificate resolves this without any
#   insecure browser flag workarounds.
#
#   Multiple values are comma-separated in .env; whitespace is stripped.
ALLOWED_HOSTS = [
    h.strip()
    for h in os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')
    if h.strip()
]

# ── Auto-detect LAN IP and add to ALLOWED_HOSTS ───────────────────────────────
# Automatically includes the server's LAN IP so that http://<IP>:8000
# (fallback access) works without requiring the operator to update .env
# on every network change.  Also used by the system connection view.
import socket as _socket
_detected_lan_ip = None
try:
    _sock = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
    _sock.settimeout(0.1)
    _sock.connect(('8.8.8.8', 80))
    _detected_lan_ip = _sock.getsockname()[0]
    _sock.close()
except Exception:
    pass
if _detected_lan_ip and _detected_lan_ip not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(_detected_lan_ip)

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'accounts',
    'beneficiaries',
    'verification',
    'logs',
]

MIDDLEWARE = [
    # First in the list so its response-phase code runs LAST — after
    # SessionMiddleware/CsrfViewMiddleware below have already set their
    # cookies. Fixes "refresh logs me out" on the HTTP LAN-IP fallback
    # without weakening cookie security on the normal HTTPS path — see
    # fans/middleware.py DynamicCookieSecurityMiddleware for the full
    # explanation (v2.2.0 Phase 3).
    'fans.middleware.DynamicCookieSecurityMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    # v2.1.16 Security Hardening Round #2 (H-05): logs out an
    # already-authenticated session the moment its account is suspended/
    # deactivated, instead of leaving it valid until it naturally expires.
    # Must come after AuthenticationMiddleware (needs request.user) and
    # MessageMiddleware (uses django.contrib.messages) — see
    # fans/middleware.py AccountStatusMiddleware.
    'fans.middleware.AccountStatusMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'fans.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [_BUNDLE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                # Injects server_lan_ip/url, server_local_url, server_domain_url
                # into every template so staff can see how to connect.
                'fans.context_processors.server_access_info',
                # Injects unread_notification_count/recent_notifications for
                # the navbar bell — see templates/base.html.
                'logs.context_processors.notifications',
            ],
        },
    },
]

WSGI_APPLICATION = 'fans.wsgi.application'

# ── Database ──────────────────────────────────────────────────────────────────
USE_SQLITE = _bool_env('USE_SQLITE', default=True)

if USE_SQLITE:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': os.getenv('DB_NAME', 'fans_db'),
            'USER': os.getenv('DB_USER', 'fans_user'),
            'PASSWORD': os.getenv('DB_PASSWORD', ''),
            'HOST': os.getenv('DB_HOST', 'localhost'),
            'PORT': os.getenv('DB_PORT', '5432'),
            # Keep database connections alive between requests so multiple
            # staff stations sharing one PostgreSQL server do not pay the
            # TCP handshake cost on every request.  60 s is a safe default;
            # set CONN_MAX_AGE=0 to revert to per-request connections.
            'CONN_MAX_AGE': int(os.getenv('CONN_MAX_AGE', '60')),
        }
    }

AUTH_USER_MODEL = 'accounts.CustomUser'

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
        # Bumped from 8 -> 10 so dictionary words alone are not enough.
        'OPTIONS': {'min_length': 10},
    },
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
    # Custom validator: require a mix of letter and digit/symbol classes.
    {'NAME': 'accounts.validators.CharacterClassValidator'},
    # Custom validator: require at least one uppercase letter A–Z.
    {'NAME': 'accounts.validators.UppercasePasswordValidator'},
]

# ── Login throttling ─────────────────────────────────────────────────────────
# After this many failed attempts from the same IP within LOGIN_LOCKOUT_WINDOW_S
# seconds, login_view rejects further attempts for LOGIN_LOCKOUT_DURATION_S.
# Keeping it conservative so a legitimately-typo'd password is forgiven quickly.
LOGIN_MAX_FAILED_ATTEMPTS = int(os.getenv('LOGIN_MAX_FAILED_ATTEMPTS', '8'))
LOGIN_LOCKOUT_WINDOW_S = int(os.getenv('LOGIN_LOCKOUT_WINDOW_S', '600'))
LOGIN_LOCKOUT_DURATION_S = int(os.getenv('LOGIN_LOCKOUT_DURATION_S', '900'))

# ── Fraud Detection Phase 1 thresholds ───────────────────────────────────────
# Rule-based FLAG-only signals (verification.fraud_signals) — never auto-block.
# Conservative placeholders; tune per deployment via .env without a redeploy.
FRAUD_REPEATED_FAILURE_THRESHOLD = int(os.getenv('FRAUD_REPEATED_FAILURE_THRESHOLD', '3'))
FRAUD_REPEATED_FAILURE_WINDOW_DAYS = int(os.getenv('FRAUD_REPEATED_FAILURE_WINDOW_DAYS', '30'))
FRAUD_STAFF_VOLUME_THRESHOLD = int(os.getenv('FRAUD_STAFF_VOLUME_THRESHOLD', '50'))
FRAUD_STAFF_VOLUME_WINDOW_HOURS = int(os.getenv('FRAUD_STAFF_VOLUME_WINDOW_HOURS', '24'))
FRAUD_MASS_EDIT_THRESHOLD = int(os.getenv('FRAUD_MASS_EDIT_THRESHOLD', '20'))
FRAUD_MASS_EDIT_WINDOW_HOURS = int(os.getenv('FRAUD_MASS_EDIT_WINDOW_HOURS', '1'))
# v2.2.0 Phase 7 — two additional signal types.
FRAUD_LOGIN_FAILURE_THRESHOLD = int(os.getenv('FRAUD_LOGIN_FAILURE_THRESHOLD', '10'))
FRAUD_LOGIN_FAILURE_WINDOW_HOURS = int(os.getenv('FRAUD_LOGIN_FAILURE_WINDOW_HOURS', '24'))
FRAUD_PAYOUT_ANOMALY_THRESHOLD = int(os.getenv('FRAUD_PAYOUT_ANOMALY_THRESHOLD', '5'))
FRAUD_PAYOUT_ANOMALY_WINDOW_HOURS = int(os.getenv('FRAUD_PAYOUT_ANOMALY_WINDOW_HOURS', '24'))

# ── Email (v2.2.0 Post-UAT Phase 6 — self-service OTP password recovery) ────
# This deployment is normally LAN-only/offline; EMAIL_HOST is blank by default
# so nothing tries to reach the network unless a site explicitly configures
# SMTP in .env. When blank, the OTP request view shows a clear "not
# configured" message and points staff to the admin-mediated fallback
# (Phase 7) instead of silently failing.
EMAIL_BACKEND = os.getenv('EMAIL_BACKEND', 'django.core.mail.backends.smtp.EmailBackend')
EMAIL_HOST = os.getenv('EMAIL_HOST', '')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', '587'))
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '')
EMAIL_USE_TLS = _bool_env('EMAIL_USE_TLS', default=True)
EMAIL_USE_SSL = _bool_env('EMAIL_USE_SSL', default=False)
EMAIL_TIMEOUT = int(os.getenv('EMAIL_TIMEOUT', '10'))
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'no-reply@fans-c.local')
# True only when a site has actually filled in SMTP host/credentials —
# checked by accounts/otp.py before attempting delivery.
EMAIL_CONFIGURED = bool(EMAIL_HOST and EMAIL_HOST_USER)

# ── Self-service password reset OTP (v2.2.0 Post-UAT Phase 6) ───────────────
OTP_LENGTH = int(os.getenv('OTP_LENGTH', '6'))
OTP_EXPIRY_MINUTES = int(os.getenv('OTP_EXPIRY_MINUTES', '5'))
OTP_MAX_ATTEMPTS = int(os.getenv('OTP_MAX_ATTEMPTS', '5'))
OTP_RESEND_COOLDOWN_S = int(os.getenv('OTP_RESEND_COOLDOWN_S', '60'))
# Per-IP request throttle, independent of whether the identifier resolves to
# a real account — this is what actually rate-limits the endpoint, since a
# non-existent identifier has no DB row of its own to key a cooldown off of.
OTP_REQUEST_RATE_LIMIT = int(os.getenv('OTP_REQUEST_RATE_LIMIT', '5'))
OTP_REQUEST_RATE_WINDOW_S = int(os.getenv('OTP_REQUEST_RATE_WINDOW_S', '900'))
# How long a verified OTP stays usable to actually set the new password.
OTP_VERIFIED_SESSION_MINUTES = int(os.getenv('OTP_VERIFIED_SESSION_MINUTES', '10'))

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Manila'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATICFILES_DIRS = [_BUNDLE_DIR / 'static']
STATIC_ROOT = _BUNDLE_DIR / 'staticfiles'
STATICFILES_STORAGE = os.getenv(
    'STATICFILES_STORAGE',
    'whitenoise.storage.CompressedManifestStaticFilesStorage'
)

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / os.getenv('MEDIA_ROOT', 'media')

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = '/accounts/login/'
LOGIN_REDIRECT_URL = '/dashboard/'
LOGOUT_REDIRECT_URL = '/accounts/login/'

EMBEDDING_ENCRYPTION_KEY = os.getenv('EMBEDDING_ENCRYPTION_KEY', '')

# ── Offline Sync ───────────────────────────────────────────────────────────────
# Leave SYNC_API_URL empty for offline-only (no sync) operation.
# When set, sync_beneficiaries will push unsynced records to this endpoint.
# SYNC_API_KEY must match the Bearer token expected by the central server.
# The EMBEDDING_ENCRYPTION_KEY above MUST be identical on the central server.
SYNC_API_URL = os.getenv('SYNC_API_URL', '')
SYNC_API_KEY = os.getenv('SYNC_API_KEY', '')
SYNC_TIMEOUT = int(os.getenv('SYNC_TIMEOUT', '30'))
SYNC_BATCH_SIZE = int(os.getenv('SYNC_BATCH_SIZE', '50'))

# ── Face Matching ─────────────────────────────────────────────────────────────
# v2.1.13 (Issue 2) — three-zone decision band:
#   score >= AUTO_VERIFY_THRESHOLD              VERIFIED (auto, releasable)
#   VERIFICATION_THRESHOLD <= score <            MANUAL_REVIEW (admin must
#       AUTO_VERIFY_THRESHOLD                       approve before release)
#   VERIFICATION_THRESHOLD * 0.85 <= score <     MANUAL_REVIEW (low-band)
#       VERIFICATION_THRESHOLD
#   score < VERIFICATION_THRESHOLD * 0.85        NOT_VERIFIED (denied)
#
# WHY a separate auto-verify band: FaceNet on webcam captures can produce
# 0.80-0.86 similarity even for wrong-person, baby-photo, or low-quality
# faces. Treating "above the lower threshold" as auto-verified caused a
# false-accept risk (observed baby-photo at 0.82-0.83 above the 0.75 line).
# Auto-verify now requires a clearly above-noise score; the band in between
# is routed to a human reviewer who must explicitly approve before any
# stipend is released.
#
# Full enforcement lower bound: 0.75 (review-band ceiling; do not auto-verify below)
# Auto-verify threshold:       0.88 (must be clearly above to release without review)
# Assisted Rollout lower bound: 0.60 (accommodates webcam quality variation)
# DEMO_MODE=True activates the Assisted Rollout lower bound automatically.
DEMO_MODE = _bool_env('DEMO_MODE', default=False)
VERIFICATION_THRESHOLD = float(os.getenv('VERIFICATION_THRESHOLD', '0.75'))
AUTO_VERIFY_THRESHOLD = float(os.getenv('AUTO_VERIFY_THRESHOLD', '0.88'))
DEMO_THRESHOLD = float(os.getenv('DEMO_THRESHOLD', '0.60'))
DEMO_AUTO_VERIFY_THRESHOLD = float(os.getenv('DEMO_AUTO_VERIFY_THRESHOLD', '0.80'))
MAX_RETRY_ATTEMPTS = int(os.getenv('MAX_RETRY_ATTEMPTS', '2'))

# v2.1.13 (Issue 2) — low-quality faces are routed to MANUAL_REVIEW even when
# the FaceNet similarity score is above AUTO_VERIFY_THRESHOLD. Low-quality
# captures produce unreliable embeddings whose high scores can be coincidental.
LOW_QUALITY_FORCES_MANUAL_REVIEW = _bool_env('LOW_QUALITY_FORCES_MANUAL_REVIEW', default=True)

# v2.1.13 (Issue 1) — minimum landmark motion (degrees) required before the
# pixel-level PAD near-duplicate / static-sequence signals are suppressed.
# A real person turning their head produces well over this; a held photo /
# phone screen produces zero or near-zero. Raising this value makes the PAD
# strictness rise (more false rejects of real users); lowering it makes the
# landmark gate more forgiving.
PAD_LANDMARK_MOTION_MIN_DEG = float(os.getenv('PAD_LANDMARK_MOTION_MIN_DEG', '2.0'))

# Face verification session expiration (seconds)
FACE_VERIFICATION_EXPIRATION_SECONDS = int(os.getenv('FACE_VERIFICATION_EXPIRATION_SECONDS', '600'))  # 10 minutes

# ── Liveness ──────────────────────────────────────────────────────────────────
# LIVENESS_REQUIRED=False  Assisted Rollout Mode: liveness runs and is recorded
#                          but does NOT block face matching. Use during gradual
#                          rollout to collect real-world data without blocking
#                          real users who fail the liveness challenge.
# LIVENESS_REQUIRED=True   Strict Mode: a failed liveness check denies the
#                          verification entirely. Enable after validating that
#                          the liveness check is reliable for your hardware.
# Default is True (strict) so the production deployment always fails closed if
# liveness is bypassed. Operators may opt in to assisted-rollout mode by setting
# LIVENESS_REQUIRED=False in .env during initial pilot.
LIVENESS_REQUIRED = _bool_env('LIVENESS_REQUIRED', default=True)
# Alias for clarity in code that checks strict enforcement
LIVENESS_STRICT_MODE = LIVENESS_REQUIRED

# Anti-spoofing texture score threshold (0.0-1.0).
# 0.25 balances false rejects for elderly users while blocking printed photos.
# Raise to 0.3-0.5 if a trained CNN PAD model is integrated.
ANTI_SPOOF_THRESHOLD = float(os.getenv('ANTI_SPOOF_THRESHOLD', '0.25'))

# Challenge trigger threshold: if server anti-spoof score is below this value,
# the backend treats the challenge as required even if the client did not show it
# (defence-in-depth against a tampered client). Should match verify.js (0.30).
LIVENESS_CHALLENGE_TRIGGER_THRESHOLD = float(os.getenv('LIVENESS_CHALLENGE_TRIGGER_THRESHOLD', '0.30'))

# Set to True to force the head-movement challenge on EVERY verification attempt
# regardless of anti-spoof score (global override; normally False).
# For representative claims the challenge is always required independently of this.
REQUIRE_LIVENESS_CHALLENGE = _bool_env('REQUIRE_LIVENESS_CHALLENGE', default=False)

# Registration liveness: require anti-spoof + head-movement challenge before
# saving a face embedding during beneficiary registration.
REGISTRATION_LIVENESS_REQUIRED = _bool_env('REGISTRATION_LIVENESS_REQUIRED', default=True)

# Registration challenge: set True to force the head-movement challenge on EVERY
# registration attempt regardless of anti-spoof score.
# Default False — challenge is only required when anti-spoof score is below
# LIVENESS_CHALLENGE_TRIGGER_THRESHOLD (0.30) or face quality is poor.
REGISTRATION_CHALLENGE_REQUIRED = _bool_env('REGISTRATION_CHALLENGE_REQUIRED', default=False)

# Registration strong-live threshold.  Above this value the anti-spoof score is
# considered a clearly-passing live capture and no head-movement challenge is needed.
# Scores below LIVENESS_CHALLENGE_TRIGGER_THRESHOLD (0.30) trigger the challenge.
REGISTRATION_STRONG_LIVE_THRESHOLD = float(os.getenv('REGISTRATION_STRONG_LIVE_THRESHOLD', '0.50'))

# ── Liveness-Proof-Bound Verification ─────────────────────────────────────────
# Server-issued LivenessTransaction token is required before verify_submit runs.
# The token is created during verify_check_liveness once anti-spoof + challenge
# pass.  The FaceNet embedding from the liveness frame is stored in the token.
# verify_submit uses ONLY the stored liveness-frame embedding for identity
# matching — preventing any face-switching between liveness and final submission.
SERVER_SIDE_LIVENESS_REQUIRED = _bool_env('SERVER_SIDE_LIVENESS_REQUIRED', default=True)
LIVENESS_PROOF_REQUIRED = _bool_env('LIVENESS_PROOF_REQUIRED', default=True)

# Time window (seconds) during which a LivenessTransaction token is valid.
# Short enough to prevent replay between sessions; long enough for slow connections.
LIVENESS_PROOF_EXPIRY_SECONDS = int(os.getenv('LIVENESS_PROOF_EXPIRY_SECONDS', '120'))

# Same-face consistency threshold.
# The liveness-frame embedding is compared against the final submission frame.
# Values below this are flagged as a possible face-switch attack.
# Set 0.0 to disable (use only liveness-frame embedding for matching without re-check).
SAME_FACE_SEQUENCE_THRESHOLD = float(os.getenv('SAME_FACE_SEQUENCE_THRESHOLD', '0.30'))

# Maximum number of liveness attempts before the session is locked.
LIVENESS_MAX_ATTEMPTS = int(os.getenv('LIVENESS_MAX_ATTEMPTS', '3'))

# ── Presentation Attack Detection ─────────────────────────────────────────────
# Heuristic-based presentation attack detector (phone screen / printed photo).
# Set PAD_REQUIRED=True to deny when suspicious; False to warn but not block.
PAD_REQUIRED = _bool_env('PAD_REQUIRED', default=True)
PHONE_SCREEN_SPOOF_THRESHOLD = float(os.getenv('PHONE_SCREEN_SPOOF_THRESHOLD', '0.40'))
PRESENTATION_ATTACK_REVIEW_OR_DENY = os.getenv('PRESENTATION_ATTACK_REVIEW_OR_DENY', 'deny')
STRICT_PRESENTATION_ATTACK_CHECK = _bool_env('STRICT_PRESENTATION_ATTACK_CHECK', default=True)

# Set True to save liveness debug frames to BASE_DIR/liveness_debug/ for calibration.
# MUST be False in production — debug frames contain biometric data.
SAVE_LIVENESS_DEBUG_FRAMES = _bool_env('SAVE_LIVENESS_DEBUG_FRAMES', default=False)

# Maximum age (seconds) of a verify_start-issued challenge before
# verify_check_liveness refuses to issue a LivenessTransaction for it
# (v2.1.16 Security Hardening Round #5, Blocker 1 — timestamp-window
# binding). Distinct from LIVENESS_PROOF_EXPIRY_SECONDS, which bounds how
# long the ISSUED TOKEN remains usable by verify_submit afterward.
LIVENESS_CHALLENGE_MAX_AGE_SECONDS = int(os.getenv('LIVENESS_CHALLENGE_MAX_AGE_SECONDS', '180'))

# How far back (hours) verify_check_liveness scans prior LivenessTransaction
# rows for a perceptual near-duplicate match (evidence_phash) when
# evaluating replay evidence (Blocker 1). Bounds query cost while still
# covering any plausible same-day replay window.
LIVENESS_REPLAY_LOOKBACK_HOURS = int(os.getenv('LIVENESS_REPLAY_LOOKBACK_HOURS', '72'))

# Maximum Hamming distance (out of 128 bits — two 64-bit dHashes, one per
# frame) between this capture's perceptual hash and a prior transaction's
# for the two to be treated as a near-duplicate replay. Lower = stricter
# (more false rejects of legitimate near-still captures); higher = looser.
LIVENESS_PHASH_HAMMING_THRESHOLD = int(os.getenv('LIVENESS_PHASH_HAMMING_THRESHOLD', '8'))

# ── Logging ───────────────────────────────────────────────────────────────────
# Console handler: INFO+ goes to stdout/stderr (captured to logs/stdout.log in
# the installed EXE via launcher.py's ensure_console_streams).
# Error file handler: ERROR+ is written to logs/django-errors.log so IT can
# inspect tracebacks without enabling DEBUG=True.
# The logs/ directory is created below (before the dict) so the handler does
# not fail on first use.
_log_dir = BASE_DIR / 'logs'
try:
    _log_dir.mkdir(parents=True, exist_ok=True)
except Exception:
    pass

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'simple': {'format': '%(levelname)s %(name)s: %(message)s'},
        'timestamped': {
            'format': '%(asctime)s %(levelname)s %(name)s %(message)s',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'simple',
        },
        'error_file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': str(_log_dir / 'django-errors.log'),
            'maxBytes': 5 * 1024 * 1024,
            'backupCount': 3,
            'level': 'ERROR',
            'formatter': 'timestamped',
            'delay': True,
        },
    },
    'loggers': {
        'verification': {
            'handlers': ['console', 'error_file'],
            'level': 'INFO',
            'propagate': False,
        },
        'logs': {
            'handlers': ['console', 'error_file'],
            'level': 'INFO',
            'propagate': False,
        },
        'fans': {
            'handlers': ['console', 'error_file'],
            'level': 'INFO',
            'propagate': False,
        },
        'beneficiaries.sync': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'django.request': {
            'handlers': ['error_file'],
            'level': 'ERROR',
            'propagate': True,
        },
    },
}

SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SAMESITE = 'Lax'
# Auto-logout after 8 hours of inactivity so a forgotten kiosk session does
# not grant indefinite access. Re-login is required on the next request.
SESSION_COOKIE_AGE = 8 * 60 * 60
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
X_FRAME_OPTIONS = 'DENY'
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER = True
# Restrict referrer info sent to other origins (privacy + security baseline).
SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'

# HSTS — only meaningful when Caddy serves over HTTPS. We rely on Caddy to set
# the header in production (see Caddyfile) so Django doesn't issue a duplicate;
# the value below is a safe fallback when Django is reached directly over HTTPS.
SECURE_HSTS_SECONDS = int(os.getenv('SECURE_HSTS_SECONDS', '0'))
SECURE_HSTS_INCLUDE_SUBDOMAINS = _bool_env('SECURE_HSTS_INCLUDE_SUBDOMAINS', default=False)
SECURE_HSTS_PRELOAD = _bool_env('SECURE_HSTS_PRELOAD', default=False)

# ── Secure-cookie mode ────────────────────────────────────────────────────────
# When True, session and CSRF cookies carry the `Secure` flag.  The browser
# will then refuse to send them over plain http://.  Any login attempt at
# http://192.168.x.x:8000 will fail with:
#
#   Forbidden (CSRF cookie not set.): /accounts/login/
#
# Default behaviour (backward-compatible):
#   DEBUG=True  → secure cookies OFF  (dev server over http:// works fine)
#   DEBUG=False → secure cookies ON   (production HTTPS path, intended default)
#
# To allow login over plain HTTP (LAN IP fallback / testing) while keeping
# DEBUG=False, add SECURE_COOKIES=False to .env.
#
# Do NOT set SECURE_COOKIES=False in a production deployment that is exposed
# beyond a trusted LAN — it allows session tokens to travel unencrypted.
_secure_cookies = _bool_env('SECURE_COOKIES', default=not DEBUG)
SESSION_COOKIE_SECURE = _secure_cookies
CSRF_COOKIE_SECURE = _secure_cookies

# ── Reverse-proxy / HTTPS (centralized server deployment) ────────────────────
# These settings are no-ops when left empty; safe for local development.
#
# For barangay LAN deployment behind Caddy with HTTPS termination:
#
#   CSRF_TRUSTED_ORIGINS=https://fans-barangay.local
#   SECURE_PROXY_SSL_HEADER=HTTP_X_FORWARDED_PROTO,https   (default on)
#   USE_X_FORWARDED_HOST=True                              (default on)
#
# Why CSRF_TRUSTED_ORIGINS is required for HTTPS deployments:
#   Caddy terminates TLS and forwards plain HTTP to Waitress/Django on
#   127.0.0.1:8000.  From Django's perspective every request arrives over
#   plain HTTP, but the browser sent it from an https:// origin.  Django's
#   CSRF middleware compares the Origin header against CSRF_TRUSTED_ORIGINS;
#   if the origin is missing from the list, all form POST requests (login,
#   verification, registration) are rejected with HTTP 403.  Setting
#   CSRF_TRUSTED_ORIGINS to the HTTPS hostname fixes this without weakening
#   any security — the origin check becomes: "did this request come from
#   our own HTTPS hostname?" which is exactly what we want.
#
# If using a raw IP instead of a hostname (less preferred):
#   CSRF_TRUSTED_ORIGINS=https://192.168.1.77
#   Note: mkcert cannot issue certs for raw IPs by default; hostname-based
#   access (fans-barangay.local) is strongly preferred.
_csrf_origins = os.getenv('CSRF_TRUSTED_ORIGINS', '').strip()
if _csrf_origins:
    CSRF_TRUSTED_ORIGINS = [o.strip() for o in _csrf_origins.split(',') if o.strip()]

# When the proxy (Caddy) forwards requests over HTTP internally, tell Django
# the real protocol via X-Forwarded-Proto so that request.is_secure(),
# SESSION_COOKIE_SECURE, CSRF_COOKIE_SECURE, and redirect URLs all reference
# https:// rather than http://.
#
# DEFAULT: 'HTTP_X_FORWARDED_PROTO,https' is always active unless overridden
# with SECURE_PROXY_SSL_HEADER=off in .env.  Waitress binds to 127.0.0.1
# only, so only Caddy can send this header; trusting it is safe.  Older .env
# files that predate this default still benefit without any manual update.
_proxy_ssl_header_raw = os.getenv(
    'SECURE_PROXY_SSL_HEADER', 'HTTP_X_FORWARDED_PROTO,https'
).strip()
if _proxy_ssl_header_raw.lower() not in ('off', 'false', '0', ''):
    if ',' in _proxy_ssl_header_raw:
        _hdr_name, _hdr_value = _proxy_ssl_header_raw.split(',', 1)
        SECURE_PROXY_SSL_HEADER = (_hdr_name.strip(), _hdr_value.strip())

# Required when the proxy sets the Host header from the original client
# request instead of the internal upstream address.  Defaults to True so
# Django uses the correct public hostname (fans-barangay.local) for URLs.
USE_X_FORWARDED_HOST = _bool_env('USE_X_FORWARDED_HOST', default=True)

# ── First-run validation ───────────────────────────────────────────────────────
# Emit clear warnings at startup instead of crashing with opaque errors.
# Critical missing config is raised immediately; non-critical issues are warnings.
_startup_errors = []
_startup_warnings = []

# Skip hard-fail validation during management commands that need to run before
# .env can be configured (e.g. generate_key itself, migrate on a fresh checkout).
_BOOTSTRAP_CMDS = {'generate_key', 'collectstatic', 'makemigrations', 'migrate',
                   'shell', 'createsuperuser', 'check', 'help', 'check_system'}
_running_bootstrap_cmd = any(arg in _BOOTSTRAP_CMDS for arg in sys.argv[1:])

if not EMBEDDING_ENCRYPTION_KEY:
    _startup_errors.append(
        'EMBEDDING_ENCRYPTION_KEY is not set in .env. '
        'Run: python manage.py generate_key  and paste the result into .env. '
        'Without this key, face embeddings cannot be stored or verified.'
    )
else:
    try:
        from cryptography.fernet import Fernet as _Fernet
        _test_key = EMBEDDING_ENCRYPTION_KEY
        _Fernet(_test_key.encode() if isinstance(_test_key, str) else _test_key)
    except Exception as _e:
        _startup_errors.append(
            f'EMBEDDING_ENCRYPTION_KEY is set but is not a valid Fernet key: {_e}. '
            'Re-generate with: python manage.py generate_key'
        )

if SECRET_KEY in ('django-insecure-change-this-immediately', '', 'your-secret-key-here-change-this-in-production'):
    if DEBUG:
        _startup_warnings.append(
            'SECRET_KEY is the default placeholder. '
            'Set a long random value in .env before deploying.'
        )
    else:
        _startup_errors.append(
            'SECRET_KEY is still the default placeholder while DEBUG=False. '
            'Refusing to start a production server with an insecure key. '
            'Generate a new key and set it in .env.'
        )

# v2.1.16 (Security Hardening Round #2, H-01): a wildcard ALLOWED_HOSTS
# disables Django's Host-header validation entirely, accepting requests for
# any hostname. The launcher's generated .env template previously included a
# trailing ',*' — fixed there, but guard here too so a hand-edited or
# copied-forward .env can't silently reintroduce it in production.
if '*' in ALLOWED_HOSTS:
    if DEBUG:
        _startup_warnings.append(
            "ALLOWED_HOSTS contains '*', disabling Host-header validation. "
            'List only the specific hostnames/IPs that should answer requests before deploying.'
        )
    else:
        _startup_errors.append(
            "ALLOWED_HOSTS contains '*' while DEBUG=False. Refusing to start a "
            'production server with Host-header validation disabled. List the '
            'specific hostnames/IPs (e.g. fans-barangay.local, the LAN IP, localhost) in .env.'
        )

# ── Production fail-closed configuration guard ────────────────────────────────
# v2.1.16 Security Hardening Round #5 (Blocker 4): "production" is no longer
# defined ONLY as `not DEBUG` (a single user-controlled flag that could
# itself be misconfigured, silently skipping every other check below).
# IS_FROZEN is the PyInstaller-packaged executable — the actual artifact
# that ships to a barangay LAN server — using the same `sys.frozen` signal
# already relied on above for BASE_DIR. A frozen build is ALWAYS scrutinized
# regardless of DEBUG; a non-frozen run is scrutinized whenever DEBUG=False,
# preserving all pre-existing behavior for a manually-deployed server.
IS_FROZEN = getattr(sys, 'frozen', False)
_production_mode = IS_FROZEN or not DEBUG

from .production_guard import collect_production_errors as _collect_production_errors
_startup_errors.extend(_collect_production_errors(
    debug=DEBUG,
    is_frozen=IS_FROZEN,
    demo_mode=DEMO_MODE,
    liveness_required=LIVENESS_REQUIRED,
    pad_required=PAD_REQUIRED,
    anti_spoof_threshold=ANTI_SPOOF_THRESHOLD,
    save_liveness_debug_frames=SAVE_LIVENESS_DEBUG_FRAMES,
    presentation_attack_review_or_deny=PRESENTATION_ATTACK_REVIEW_OR_DENY,
    strict_presentation_attack_check=STRICT_PRESENTATION_ATTACK_CHECK,
    phone_screen_spoof_threshold=PHONE_SCREEN_SPOOF_THRESHOLD,
))

# In production, refuse to start with unsafe configuration — the system
# cannot safely store new face data / cannot safely gate liveness or PAD /
# etc., and a silent fallback would mask the misconfig.
if _startup_errors and _production_mode and not _running_bootstrap_cmd:
    raise RuntimeError(
        '[FANS-C] Refusing to start in production: '
        + ' | '.join(_startup_errors)
    )

# Only print warnings when running the server or management commands, not during testing
_running_tests = 'test' in sys.argv or 'pytest' in sys.modules
if not _running_tests and not _running_bootstrap_cmd:
    import logging as _logging
    _slog = _logging.getLogger('fans.settings')
    for _msg in _startup_warnings:
        _slog.warning('[FANS-C] %s', _msg)
    for _msg in _startup_errors:
        _slog.error('[FANS-C CRITICAL] %s', _msg)
        if sys.stderr is not None:
            print(f'\n[FANS-C CRITICAL] {_msg}\n', file=sys.stderr)

    # ── Startup settings summary (shown in console/log for installer) ────────
    try:
        _csrf_display = str(CSRF_TRUSTED_ORIGINS)
    except NameError:
        _csrf_display = '(not set — required for HTTPS form POSTs via Caddy)'
    _startup_info = [
        f'[FANS-C] ALLOWED_HOSTS       : {ALLOWED_HOSTS}',
        f'[FANS-C] CSRF_TRUSTED_ORIGINS: {_csrf_display}',
        f'[FANS-C] SECURE_COOKIES      : {_secure_cookies}',
        (
            f'[FANS-C] LAN IP              : {_detected_lan_ip}  [OK] auto-added to ALLOWED_HOSTS'
            if _detected_lan_ip else
            '[FANS-C] LAN IP              : not detected (server may not be connected to LAN)'
        ),
    ]
    for _line in _startup_info:
        _slog.info(_line)
        if sys.stderr is not None:
            print(_line, file=sys.stderr)

# ── Security check suppressions ───────────────────────────────────────────────
# W004: SECURE_HSTS_SECONDS — Caddy sets Strict-Transport-Security in Caddyfile.
#        Django setting this too would send a duplicate header.
# W008: SECURE_SSL_REDIRECT — Caddy handles HTTP→HTTPS redirect. Enabling this
#        in Django causes redirect loops (Django sees plain HTTP from Caddy on
#        loopback and would redirect indefinitely).
# W012: SESSION_COOKIE_SECURE — set dynamically via _secure_cookies above.
#        True when DEBUG=False (production). Django's check fires because the
#        value is not hardcoded True, even though runtime value is correct.
# W016: CSRF_COOKIE_SECURE — same reason as W012.
SILENCED_SYSTEM_CHECKS = [
    'security.W004',  # HSTS — handled by Caddy
    'security.W008',  # SSL redirect — handled by Caddy
    'security.W012',  # SESSION_COOKIE_SECURE — dynamic, True in production
    'security.W016',  # CSRF_COOKIE_SECURE    — dynamic, True in production
]