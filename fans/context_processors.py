import socket
from functools import lru_cache


@lru_cache(maxsize=1)
def _detect_lan_ip():
    """
    Return this machine's LAN IP by checking the routing table.
    Uses a UDP connect trick — no packet is actually sent.
    Result is cached for the process lifetime; restart the server if the IP changes.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.1)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


def server_access_info(request):
    """
    Inject server access URLs into every template context.

    This lets any template show staff how to connect without reading the README.
    Available variables:
        server_lan_ip      — e.g. '192.168.1.100'  (None if undetectable)
        server_lan_url     — e.g. 'http://192.168.1.100:8000'  (None if undetectable)
        server_local_url   — 'http://127.0.0.1:8000'  (always set)
        server_domain_url  — 'https://fans-barangay.local'  (always set)
    """
    lan_ip = _detect_lan_ip()
    return {
        'server_lan_ip': lan_ip,
        'server_lan_url': f'http://{lan_ip}:8000' if lan_ip else None,
        'server_local_url': 'http://127.0.0.1:8000',
        'server_domain_url': 'https://fans-barangay.local',
    }
