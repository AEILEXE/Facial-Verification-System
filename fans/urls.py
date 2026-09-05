from django.contrib import admin
from django.urls import path, re_path, include
from django.conf import settings
from django.views.generic import RedirectView
from . import views as fans_views

# Custom error handlers — used by Django when DEBUG=False instead of the
# default debug stack trace. Each renders a static page with no internals.
handler400 = 'fans.views.error_400'
handler403 = 'fans.views.error_403'
handler404 = 'fans.views.error_404'
handler500 = 'fans.views.error_500'

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('accounts.urls')),
    path('dashboard/', include('beneficiaries.urls')),
    path('verification/', include('verification.urls')),
    path('logs/', include('logs.urls')),
    # Health + system info (installer / superuser tools)
    path('health/', fans_views.health_check, name='health_check'),
    path('health/network/', fans_views.health_network, name='health_network'),
    path('help/connect/', fans_views.connect_help, name='connect_help'),
    path('system/connection/', fans_views.system_connection, name='system_connection'),
    path('system/privacy/', fans_views.privacy_consent, name='privacy_consent'),
    path('system/roles/', fans_views.role_matrix, name='role_matrix'),
    path('system/health/', fans_views.system_health, name='system_health'),
    path('', RedirectView.as_view(url='/dashboard/', permanent=False), name='home'),
]

# Serve uploaded media files through Django/Waitress — AUTHENTICATED.
# v2.1.16 Security Hardening Round #2 (H-02): beneficiary/representative/user
# photos and the representative's authorization document live under
# MEDIA_ROOT. django.conf.urls.static.static(..., insecure=True) (the
# previous wiring here) registers django.views.static.serve directly, which
# performs NO authentication or authorization check — any device on the LAN
# could fetch /media/<path> with no login. fans_views.serve_protected_media
# wraps the same file-serving logic behind @login_required while keeping the
# exact same MEDIA_URL prefix, so every existing <field>.url template
# reference keeps working unchanged.
urlpatterns += [
    re_path(
        r'^%s(?P<path>.*)$' % settings.MEDIA_URL.lstrip('/'),
        fans_views.serve_protected_media,
        name='serve_media',
    ),
]
