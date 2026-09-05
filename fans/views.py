from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse, HttpResponse, Http404
from django.shortcuts import render
from django.template import TemplateDoesNotExist
from django.template.loader import render_to_string
from django.views.static import serve as _django_static_serve

from fans.context_processors import _detect_lan_ip
from fans.backup_status import (
    _FANS_BACKUP_NAME_RE,
    _is_valid_db_backup_bytes,
    _is_fans_backup_restore_ready,
    _scan_fans_backup_directories,
    _list_completed_fans_backups,
    _count_incomplete_fans_backups,
)


# ─── Error pages ─────────────────────────────────────────────────────────────
# Render generic, content-only pages when DEBUG=False so exception details and
# stack traces never reach clients. Templates are optional — a plain text body
# is used as a fallback so the system always responds with a useful status page.

def _safe_error_page(request, status_code, title, message):
    template = f'errors/{status_code}.html'
    try:
        body = render_to_string(template, {'title': title, 'message': message},
                                request=request)
        return HttpResponse(body, status=status_code, content_type='text/html')
    except TemplateDoesNotExist:
        plain = f'{status_code} {title}\n\n{message}\n'
        return HttpResponse(plain, status=status_code, content_type='text/plain')


def error_400(request, exception=None):
    return _safe_error_page(
        request, 400, 'Bad Request',
        'The server could not process the request. Try again or contact your administrator.'
    )


def error_403(request, exception=None):
    return _safe_error_page(
        request, 403, 'Access Denied',
        'You do not have permission to access this page.'
    )


def error_404(request, exception=None):
    return _safe_error_page(
        request, 404, 'Page Not Found',
        'The page you requested could not be found.'
    )


def error_500(request):
    return _safe_error_page(
        request, 500, 'Server Error',
        'The server encountered an unexpected error. The Technical Administrator has been notified via the audit log.'
    )


def health_check(request):
    """GET /health/ — returns {"status": "ok"} for uptime monitoring."""
    return JsonResponse({'status': 'ok'})


def health_network(request):
    """
    GET /health/network/ — returns LAN IP and connection info as JSON.
    No login required; used by the installer during setup to verify
    that the server is reachable on the LAN.
    """
    lan_ip = _detect_lan_ip()
    return JsonResponse({
        'status': 'ok',
        'lan_ip': lan_ip,
        'reachable_from_lan': lan_ip is not None,
        'scheme': 'https' if request.is_secure() else 'http',
        'host': request.get_host(),
    })


@login_required
def connect_help(request):
    """
    GET /help/connect/ — connection guide page.
    Restricted to IT role; President, Admin, and Staff are denied because
    this page contains technical network details they don't need.
    """
    if not request.user.is_admin_it:
        raise PermissionDenied
    return render(request, 'help/connect.html')


@login_required
def system_connection(request):
    """
    GET /system/connection/ — full technical status page, IT role only.
    Shows effective ALLOWED_HOSTS, CSRF origins, LAN IP, access mode, and
    troubleshooting hints.  Not linked from President, Admin, or Staff UI.
    """
    if not request.user.is_admin_it:
        raise PermissionDenied
    from django.conf import settings
    try:
        csrf_origins = settings.CSRF_TRUSTED_ORIGINS
    except AttributeError:
        csrf_origins = None

    # HTTPS / proxy diagnostics — surfaced on the page so IT can verify that
    # Caddy headers are reaching Django correctly without enabling DEBUG=True.
    fwd_proto    = request.META.get('HTTP_X_FORWARDED_PROTO', '')
    fwd_host     = request.META.get('HTTP_X_FORWARDED_HOST', '')
    fwd_for      = request.META.get('HTTP_X_FORWARDED_FOR', '')
    real_ip      = request.META.get('HTTP_X_REAL_IP', '')
    http_host    = request.META.get('HTTP_HOST', '')
    remote_addr  = request.META.get('REMOTE_ADDR', '')
    server_port  = request.META.get('SERVER_PORT', '')
    wsgi_scheme  = request.META.get('wsgi.url_scheme', '')
    try:
        proxy_ssl_header = settings.SECURE_PROXY_SSL_HEADER
    except AttributeError:
        proxy_ssl_header = None

    from verification.views import _camera_verification_allowed
    camera_allowed = _camera_verification_allowed(request)

    return render(request, 'system/connection.html', {
        'allowed_hosts': settings.ALLOWED_HOSTS,
        'csrf_origins': csrf_origins,
        'secure_cookies': settings.SESSION_COOKIE_SECURE,
        'debug': settings.DEBUG,
        'lan_ip': _detect_lan_ip(),
        # Request-level HTTPS diagnostics
        'diag_scheme': request.scheme,
        'diag_is_secure': request.is_secure(),
        'diag_fwd_proto': fwd_proto,
        'diag_fwd_host': fwd_host,
        'diag_fwd_for': fwd_for,
        'diag_real_ip': real_ip,
        'diag_http_host': http_host,
        'diag_get_host': request.get_host(),
        'diag_proxy_ssl_header': proxy_ssl_header,
        'diag_camera_allowed': camera_allowed,
        'diag_use_x_fwd_host': getattr(settings, 'USE_X_FORWARDED_HOST', False),
        # Raw WSGI environ entries — prove what Waitress actually delivers to Django
        'diag_remote_addr': remote_addr,
        'diag_server_port': server_port,
        'diag_wsgi_scheme': wsgi_scheme,
    })


@login_required
def privacy_consent(request):
    """
    GET /system/privacy/ — Privacy & Data Consent Notice.
    Accessible to all logged-in users so they understand how their and
    beneficiary data is handled.
    """
    return render(request, 'system/privacy_consent.html')


@login_required
def role_matrix(request):
    """
    GET /system/roles/ — Role Permission Matrix page.
    Visible to President / Admin / IT (admin roles) so they can confirm
    which actions each role can perform.
    """
    if not request.user.is_admin:
        raise PermissionDenied
    return render(request, 'system/role_matrix.html')


@login_required
def system_health(request):
    """
    GET /system/health/ — Database & Backup Status page.
    IT/Admin only. Shows database engine, last backup timestamp (best
    effort), media folder status, basic record counts, and storage paths.
    """
    if not request.user.is_admin:
        raise PermissionDenied
    from django.conf import settings
    from django.db import connection
    from pathlib import Path
    import datetime as _dt

    db_engine_full = settings.DATABASES['default'].get('ENGINE', '')
    db_engine_short = db_engine_full.rsplit('.', 1)[-1] if db_engine_full else 'unknown'
    db_name = str(settings.DATABASES['default'].get('NAME', ''))
    db_status = 'unknown'
    db_error = ''
    try:
        with connection.cursor() as cur:
            cur.execute('SELECT 1')
            cur.fetchone()
        db_status = 'connected'
    except Exception as exc:
        db_status = 'error'
        db_error = f'{type(exc).__name__}: {exc}'

    # SQLite file size and last-modified — only meaningful when SQLite.
    db_file_size = None
    db_file_mtime = None
    if 'sqlite' in db_engine_short and db_name:
        try:
            p = Path(db_name)
            if p.exists():
                db_file_size = p.stat().st_size
                db_file_mtime = _dt.datetime.fromtimestamp(p.stat().st_mtime)
        except Exception:
            pass

    # Last backup: scan backups/<YYYY-MM-DD_HHmm[_N]>/ directories created by
    # scripts/admin/daily-backup.ps1. A directory only counts as a valid,
    # restore-ready backup when it passes the same restore-readiness
    # predicate the backup script's own rotation logic uses (v2.1.16
    # correction, findings F-03/F-04) -- status=complete alone is not
    # trusted; required files must still physically exist and match the
    # recorded size. This intentionally reuses the stored integrity_check
    # result from that manifest (verified once, at backup-creation time)
    # rather than re-running PRAGMA integrity_check on every health-page load.
    last_backup = None
    backup_count = 0
    backup_dir = None
    latest_backup_db_present = False
    latest_backup_env_present = False
    latest_backup_integrity = None
    incomplete_backup_count = 0
    try:
        backups_root = settings.BASE_DIR / 'backups'
        # Best-effort: mirror any new backup directories into AuditLog so the
        # app's own audit trail (not just this live-scanned page) records
        # backup success/failure. Never blocks the page on failure.
        try:
            from django.core.management import call_command
            call_command('sync_backup_audit')
        except Exception:
            pass
        completed_backups = _list_completed_fans_backups(backups_root)
        backup_count = len(completed_backups)
        if completed_backups:
            newest = completed_backups[0]
            backup_dir = newest['path'].parent
            last_backup = newest['timestamp']
            latest_backup_db_present = (newest['path'] / 'db.sqlite3').is_file()
            latest_backup_env_present = (newest['path'] / '.env').is_file()
            latest_backup_integrity = newest['manifest'].get('integrity_check')
        incomplete_backup_count = _count_incomplete_fans_backups(backups_root)
    except Exception:
        pass

    # Media folder status
    media_root = Path(settings.MEDIA_ROOT)
    media_exists = media_root.exists()
    media_file_count = 0
    media_size_bytes = 0
    if media_exists:
        try:
            for f in media_root.rglob('*'):
                if f.is_file():
                    media_file_count += 1
                    try:
                        media_size_bytes += f.stat().st_size
                    except Exception:
                        pass
        except Exception:
            pass

    # Record counts (best effort — silently 0 on error)
    beneficiary_count = 0
    audit_count = 0
    embedding_count = 0
    try:
        from beneficiaries.models import Beneficiary
        beneficiary_count = Beneficiary.objects.count()
    except Exception:
        pass
    try:
        from logs.models import AuditLog
        audit_count = AuditLog.objects.count()
    except Exception:
        pass
    try:
        from verification.models import FaceEmbedding
        embedding_count = FaceEmbedding.objects.count()
    except Exception:
        pass

    embedding_key_set = bool(getattr(settings, 'EMBEDDING_ENCRYPTION_KEY', ''))

    return render(request, 'system/health.html', {
        'db_status': db_status,
        'db_error': db_error,
        'db_engine_full': db_engine_full,
        'db_engine_short': db_engine_short,
        'db_name': db_name,
        'db_file_size': db_file_size,
        'db_file_mtime': db_file_mtime,
        'last_backup': last_backup,
        'backup_count': backup_count,
        'backup_dir': str(backup_dir) if backup_dir else None,
        'latest_backup_db_present': latest_backup_db_present,
        'latest_backup_env_present': latest_backup_env_present,
        'latest_backup_integrity': latest_backup_integrity,
        'incomplete_backup_count': incomplete_backup_count,
        'media_root': str(media_root),
        'media_exists': media_exists,
        'media_file_count': media_file_count,
        'media_size_bytes': media_size_bytes,
        'beneficiary_count': beneficiary_count,
        'audit_count': audit_count,
        'embedding_count': embedding_count,
        'base_dir': str(settings.BASE_DIR),
        'embedding_key_set': embedding_key_set,
    })


# ─── Protected media serving ──────────────────────────────────────────────────
# v2.1.16 Security Hardening Round #2 (H-02): beneficiary/representative/user
# photos and the representative's authorization document live under
# MEDIA_ROOT. They were previously served by django.views.static.serve via a
# blanket, unauthenticated URL pattern in fans/urls.py — any device on the LAN
# could fetch /media/<path> directly with no login. This wraps the same
# file-serving logic behind @login_required so only an authenticated FANS-C
# session can retrieve them, while keeping the exact /media/<path> URL shape
# so every existing <field>.url template reference (beneficiary/list detail
# pages, the shared-representative review page) keeps working unchanged.
#
# v2.1.16 Security Hardening Round #3 (Blocker 3): @login_required alone only
# proves the requester is SOME authenticated FANS-C user — it does not check
# whether the specific file requested belongs to an object that user is
# allowed to view. Any logged-in account (including one with no legitimate
# reason to see a given file) could fetch any predictable /media/ path. This
# resolves the path back to the exact record that stores it and authorizes
# per object type:
#   - Beneficiary.profile_picture: any authenticated FANS-C user, the same
#     access level beneficiaries.views.beneficiary_detail already grants
#     (this system has no per-officer beneficiary assignment — Staff, Admin,
#     President, and Technical Administrator all handle the shared roster —
#     so there is no narrower legitimate boundary to enforce here without
#     breaking normal operations).
#   - SharedRepresentativeReview.authorization_document: an admin-review
#     artifact (legal guardianship / authorization proof uploaded when a
#     representative's face matches an existing one for another
#     beneficiary). Restricted to request.user.is_admin, mirroring the exact
#     gate verification.views.shared_rep_review_list/_detail already use — a
#     Staff account has no view into that queue at all, so it should not be
#     able to fetch its documents by path either.
#   - CustomUser.profile_picture: the account's own owner, or an
#     administrative role (President/Admin/IT — see CustomUser.is_admin),
#     mirroring the same is_admin gate accounts.views already uses for
#     viewing/managing OTHER users' accounts (user_list_full etc). A Staff
#     account has no administrative reason to view another user's photo.
# A path that does not match any tracked record's stored file (a guess, a
# stale/renamed file, directory-traversal-shaped input) is denied with 404
# rather than confirming or denying its existence any more specifically.
def _resolve_media_owner(path):
    """Return ('beneficiary'|'shared_rep_review'|'user', obj) for the record
    whose FileField/ImageField stores exactly `path`, or (None, None)."""
    if not path:
        return None, None
    from beneficiaries.models import Beneficiary, SharedRepresentativeReview
    from accounts.models import CustomUser

    beneficiary = Beneficiary.objects.filter(profile_picture=path).first()
    if beneficiary is not None:
        return 'beneficiary', beneficiary

    review = SharedRepresentativeReview.objects.filter(authorization_document=path).first()
    if review is not None:
        return 'shared_rep_review', review

    user = CustomUser.objects.filter(profile_picture=path).first()
    if user is not None:
        return 'user', user

    return None, None


@login_required
def serve_protected_media(request, path):
    kind, obj = _resolve_media_owner(path)
    if kind is None:
        raise Http404('No matching media record.')

    if kind == 'user' and not (request.user.is_admin or request.user.pk == obj.pk):
        raise PermissionDenied
    if kind == 'shared_rep_review' and not request.user.is_admin:
        raise PermissionDenied

    return _django_static_serve(request, path, document_root=settings.MEDIA_ROOT)
