"""
FANS-C Windows Launcher
=======================
Entry point for the packaged one-click installer (fans_c.exe).

First run  (.env absent) -- shows an 8-step tkinter setup wizard:
  1. Generate security keys and write .env
  2. Run database migrations + collectstatic
  3. Generate HTTPS certificate via mkcert
  4. Add fans-barangay.local to Windows hosts file
  5. Create admin account (interactive tkinter form)
  6. Register Task Scheduler autostart tasks
  7. Start Waitress + Caddy and wait for both ports
  8. Show success screen, open browser

Subsequent runs (.env present) -- fast path:
  - Load config, start Waitress + Caddy, wait for ports, open browser
  - Show a brief status window, then close it once browser opens

All errors are shown as user-friendly messageboxes.
No Python tracebacks are ever shown to the end user.
UAC elevation is requested automatically at startup.
"""

# ---------------------------------------------------------------------------
# Safety: In PyInstaller windowed (no-console) mode sys.stdout and sys.stderr
# are None. Third-party libraries (TensorFlow, Keras, MTCNN) call
# sys.stderr.write() or print() during import, raising:
#   AttributeError: 'NoneType' object has no attribute 'write'
# Redirect both streams to log files BEFORE any ML-library import can run.
# This must execute before ctypes/logging/tkinter are imported.
# ---------------------------------------------------------------------------
import sys as _sys
import os as _os


def ensure_console_streams():
    """
    Redirect sys.stdout/sys.stderr to log files if they are None.

    In PyInstaller windowed (--noconsole) mode both streams are None.
    Any library that calls print() or sys.stderr.write() — including
    TensorFlow, Keras, and MTCNN — will crash with:
        AttributeError: 'NoneType' object has no attribute 'write'
    This function is idempotent; calling it in normal dev mode is a no-op.
    """
    if _sys.stdout is not None and _sys.stderr is not None:
        return

    from pathlib import Path as _Path

    if getattr(_sys, 'frozen', False):
        _base = _Path(_sys.executable).parent
    else:
        _base = _Path(__file__).resolve().parent.parent

    _logs = _Path(_os.environ.get('FANS_RUNTIME_DIR', str(_base))) / 'logs'

    def _open_fallback(name):
        try:
            _logs.mkdir(parents=True, exist_ok=True)
            return open(str(_logs / name), 'a', encoding='utf-8', buffering=1)
        except Exception:
            return open(_os.devnull, 'w', encoding='utf-8')

    if _sys.stdout is None:
        _sys.stdout = _open_fallback('stdout.log')
    if _sys.stderr is None:
        _sys.stderr = _open_fallback('stderr.log')


ensure_console_streams()

import ctypes
import logging
import os
import re
import sys
import time
import socket
import secrets
import shutil
import subprocess
import threading
import webbrowser
from pathlib import Path

# DJANGO_SETTINGS_MODULE must be set BEFORE any Django/Waitress import happens
# anywhere in this process (including the daemon thread in _start_waitress).
# Direct assignment (not setdefault): an inherited/wrong value from the parent
# environment must not survive into our process.
os.environ["DJANGO_SETTINGS_MODULE"] = "fans.settings"

# ---------------------------------------------------------------------------
# UAC elevation -- must be the very first thing executed
# ---------------------------------------------------------------------------

def _is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


if not _is_admin():
    # Re-launch with a UAC "Run as Administrator" prompt.
    # sys.executable is fans_c.exe when frozen, so this re-launches the same exe.
    ctypes.windll.shell32.ShellExecuteW(
        None,
        "runas",
        sys.executable,
        " ".join(f'"{a}"' for a in sys.argv),
        None,
        1,
    )
    sys.exit()


# ---------------------------------------------------------------------------
# Base directory and sys.path setup
# ---------------------------------------------------------------------------

if getattr(sys, "frozen", False):
    # Running as a PyInstaller onedir bundle.
    #   BASE_DIR   = folder containing fans_c.exe -- writable runtime state
    #                (.env, certs, db.sqlite3, media/, Caddy CWD).
    #   BUNDLE_DIR = sys._MEIPASS, i.e. the _internal/ folder where PyInstaller
    #                stages all bundled data files (tools/, Caddyfile,
    #                assets/, manage.py, Django app packages).
    BASE_DIR   = Path(sys.executable).parent
    BUNDLE_DIR = Path(sys._MEIPASS)
else:
    # Running from source: dev/launcher.py -> one level up is project root.
    BASE_DIR   = Path(__file__).resolve().parent.parent
    BUNDLE_DIR = BASE_DIR

_base_str   = str(BASE_DIR)
_bundle_str = str(BUNDLE_DIR)
# BUNDLE_DIR (sys._MEIPASS) is where PyInstaller stages the fans/, accounts/,
# beneficiaries/, verification/, logs/ packages. Add it to sys.path BEFORE
# BASE_DIR so `import fans` resolves to the bundled package, not anything that
# may sit next to fans_c.exe.
if _bundle_str not in sys.path:
    sys.path.insert(0, _bundle_str)
if _base_str not in sys.path:
    sys.path.insert(0, _base_str)
# Propagate to PYTHONPATH so any subprocess we spawn inherits the same view.
os.environ["PYTHONPATH"] = os.pathsep.join(
    p for p in (_bundle_str, _base_str, os.environ.get("PYTHONPATH", ""))
    if p
)

# Caddy needs to find fans-cert.pem / fans-cert-key.pem (written at runtime)
# via the relative paths in Caddyfile, so CWD must point at BASE_DIR.
os.chdir(_base_str)

# ---------------------------------------------------------------------------
# Error logging -- writes Django / Waitress tracebacks to logs/django-errors.log
# next to fans_c.exe so 500 Internal Server Error pages on the LAN can be
# diagnosed without enabling DEBUG=True.
# ---------------------------------------------------------------------------
_log_dir = BASE_DIR / "logs"
try:
    _log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(_log_dir / "django-errors.log"),
        level=logging.ERROR,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # Route warnings.warn() through the logging system so they are captured
    # in django-errors.log rather than writing to sys.stderr directly.
    logging.captureWarnings(True)
except Exception:
    # Logging setup must never crash the launcher.
    pass

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Runtime state -- written on first run, must live next to fans_c.exe
ENV_FILE      = BASE_DIR / ".env"
CERT_FILE     = BASE_DIR / "fans-cert.pem"
KEY_FILE      = BASE_DIR / "fans-cert-key.pem"

# Bundled assets -- shipped read-only inside _internal/ (sys._MEIPASS)
CADDY_EXE     = BUNDLE_DIR / "tools" / "caddy.exe"
MKCERT_EXE    = BUNDLE_DIR / "tools" / "mkcert" / "mkcert.exe"
CADDYFILE     = BUNDLE_DIR / "Caddyfile"
HOSTS_FILE    = Path(r"C:\Windows\System32\drivers\etc\hosts")
FANS_URL      = "https://fans-barangay.local"
WAITRESS_HOST = "127.0.0.1"
WAITRESS_PORT = 8000
CADDY_PORT    = 443

# ---------------------------------------------------------------------------
# tkinter imports
# ---------------------------------------------------------------------------

import tkinter as tk
from tkinter import ttk, messagebox

# ---------------------------------------------------------------------------
# FANS-C brand constants (v2.1.16 Issue 9) — shared across every setup-wizard
# window so the installer matches the web app's navy/gold identity instead of
# each Toplevel re-declaring its own slightly different ad-hoc colors.
# Values mirror static/css/main.css :root (--qc-blue / --qc-blue-dark / --qc-gold).
# ---------------------------------------------------------------------------

BRAND_NAVY       = "#002b6e"   # --qc-blue-dark — header/panel backgrounds
BRAND_NAVY_LIGHT = "#003d99"   # --qc-blue — hover/accent
BRAND_GOLD       = "#d4a017"   # --qc-gold — accent border/highlight
BRAND_GOLD_LIGHT = "#fef9c3"   # --qc-gold-light
BRAND_FONT       = "Segoe UI"

# ---------------------------------------------------------------------------
# Friendly error / warning helpers
# ---------------------------------------------------------------------------

def _write_diagnostic_report(error: str = "") -> None:
    """
    Write a complete snapshot of the runtime environment to
    logs/diagnostic.log. Called at every launch (before Waitress) and
    again on any fatal error, so IT always has a fresh report next to the
    install regardless of whether the GUI ever managed to surface a dialog.
    """
    try:
        report_path = _log_dir / "diagnostic.log"
        lines = []
        lines.append("=== FANS-C Diagnostic Report ===")
        lines.append(f"timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"frozen: {getattr(sys, 'frozen', False)}")
        lines.append(f"sys.executable: {sys.executable}")
        lines.append(f"sys._MEIPASS: {getattr(sys, '_MEIPASS', 'N/A')}")
        lines.append(f"BASE_DIR: {BASE_DIR}")
        lines.append(f"BUNDLE_DIR: {BUNDLE_DIR}")
        lines.append(f"cwd: {os.getcwd()}")
        lines.append(f"DJANGO_SETTINGS_MODULE: "
                     f"{os.environ.get('DJANGO_SETTINGS_MODULE')}")
        lines.append(f"PYTHONPATH: {os.environ.get('PYTHONPATH', '')}")
        lines.append(f".env (BASE_DIR) exists: {ENV_FILE.exists()}")
        lines.append(f".env (BUNDLE_DIR) exists: "
                     f"{(BUNDLE_DIR / '.env').exists()}")
        lines.append(f"db.sqlite3 (BASE_DIR) exists: "
                     f"{(BASE_DIR / 'db.sqlite3').exists()}")
        lines.append(f"db.sqlite3 (BUNDLE_DIR) exists: "
                     f"{(BUNDLE_DIR / 'db.sqlite3').exists()}")
        lines.append(f"staticfiles (BUNDLE_DIR) exists: "
                     f"{(BUNDLE_DIR / 'staticfiles').is_dir()}")
        lines.append(f"staticfiles manifest exists: "
                     f"{(BUNDLE_DIR / 'staticfiles' / 'staticfiles.json').exists()}")
        lines.append(f"sys.path (first 10):")
        for p in sys.path[:10]:
            lines.append(f"  - {p}")
        lines.append("")
        lines.append("=== Relevant environment variables ===")
        keys_of_interest = (
            "DJANGO", "PYTHON", "PATH", "FANS", "SECRET", "DEBUG",
            "ALLOWED", "CSRF", "EMBEDDING", "USE_SQLITE",
        )
        for k in sorted(os.environ):
            if any(x in k.upper() for x in keys_of_interest):
                v = os.environ[k]
                # Truncate huge PATH lists so the file stays readable
                if len(v) > 600:
                    v = v[:600] + "...(truncated)"
                # Don't leak secrets into the log
                if any(s in k.upper() for s in ("SECRET", "EMBEDDING")):
                    v = "(set, len={})".format(len(v))
                lines.append(f"{k}={v}")
        if error:
            lines.append("")
            lines.append("=== Error context ===")
            lines.append(error)
        report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        # Diagnostic writing must never crash the launcher.
        pass


def _fatal(message: str, detail: str = "") -> None:
    """Show a user-friendly error dialog and exit immediately."""
    # Refresh diagnostic.log so IT has the exact state at the time of failure.
    _write_diagnostic_report(error=f"{message} | detail: {detail}")
    body = message
    if detail:
        body += f"\n\nError detail: {detail}"
    body += "\n\nPlease contact your Technical Administrator."
    try:
        r = tk.Tk()
        r.withdraw()
        r.attributes("-topmost", True)
        messagebox.showerror("FANS-C Setup Error", body)
        r.destroy()
    except Exception:
        pass
    os._exit(1)


def _warn(message: str) -> None:
    try:
        r = tk.Tk()
        r.withdraw()
        r.attributes("-topmost", True)
        messagebox.showwarning("FANS-C", message)
        r.destroy()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Network utilities
# ---------------------------------------------------------------------------

def _get_lan_ip() -> str:
    """Return the machine's primary LAN IP, or 127.0.0.1 on failure."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _wait_port(port: int, host: str = "127.0.0.1", timeout: int = 60, interval: int = 2) -> bool:
    """Poll host:port every `interval` seconds until it accepts a connection or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(interval)
    return False


# ---------------------------------------------------------------------------
# Django helpers (lazy -- called only after .env is written)
# ---------------------------------------------------------------------------

_django_ready = False


def _init_django() -> None:
    global _django_ready
    if _django_ready:
        return
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=str(ENV_FILE), override=True)
    # DJANGO_SETTINGS_MODULE is pinned at module top via direct assignment;
    # we restate it here in case .env tried to override it.
    os.environ["DJANGO_SETTINGS_MODULE"] = "fans.settings"
    os.environ["DEBUG"] = "False"
    import django
    django.setup()
    _django_ready = True
    _ensure_facenet_cache_dir()


def _ensure_facenet_cache_dir() -> None:
    """
    Create the shared FaceNet weights cache directory and verify this
    process can write to it, before any FaceNet load is attempted.

    Uses verification.face_utils.get_facenet_cache_dir() -- the single
    resolver also used by get_facenet_model() -- so the launcher and the
    biometric layer can never disagree on the path. That resolver derives
    the location from Django's settings.BASE_DIR, which is already computed
    identically (Path(sys.executable).parent when frozen) whether this
    process is the elevated interactive first run or the SYSTEM-account
    autostart/watchdog process restarting the same fans_c.exe -- neither
    depends on USERPROFILE.

    Not fatal: a failure here is logged and get_facenet_model() will still
    raise a clear FaceNetUnavailableError later if the directory truly
    cannot be used.
    """
    try:
        from verification.face_utils import get_facenet_cache_dir
        cache_dir = Path(get_facenet_cache_dir())
        cache_dir.mkdir(parents=True, exist_ok=True)
        probe = cache_dir / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        try:
            probe.unlink()
        except Exception:
            pass
    except Exception as exc:
        logging.warning("FaceNet cache directory check failed: %s", exc)


def _run_migrate() -> None:
    """Run migrate in-process; capture stdout+stderr to logs/migrate.log."""
    import io
    from django.core.management import call_command
    buf = io.StringIO()
    err = io.StringIO()
    try:
        call_command(
            "migrate", "--noinput", "--run-syncdb",
            verbosity=2, stdout=buf, stderr=err,
        )
    finally:
        try:
            (_log_dir / "migrate.log").write_text(
                buf.getvalue() + ("\n--- stderr ---\n" + err.getvalue()
                                  if err.getvalue() else ""),
                encoding="utf-8",
            )
        except Exception:
            pass


def _run_collectstatic() -> None:
    """Run collectstatic; capture output to logs/collectstatic.log."""
    import io
    from django.core.management import call_command
    buf = io.StringIO()
    err = io.StringIO()
    try:
        call_command(
            "collectstatic", "--noinput",
            verbosity=1, stdout=buf, stderr=err,
        )
    finally:
        try:
            (_log_dir / "collectstatic.log").write_text(
                buf.getvalue() + ("\n--- stderr ---\n" + err.getvalue()
                                  if err.getvalue() else ""),
                encoding="utf-8",
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Service management
# ---------------------------------------------------------------------------

_caddy_proc = None

# Keys that must be present in .env for HTTPS to work. Older .env files written
# by an early installer build may be missing them; _repair_env() adds them on
# every normal startup so users don't have to reinstall to get the fix.
_REQUIRED_ENV_KEYS = {
    'SECURE_PROXY_SSL_HEADER': 'HTTP_X_FORWARDED_PROTO,https',
    'USE_X_FORWARDED_HOST': 'True',
}


def _repair_env() -> None:
    """Append any missing required .env keys without touching existing values."""
    try:
        text = ENV_FILE.read_text(encoding='utf-8')
        existing_keys = {
            line.split('=', 1)[0].strip()
            for line in text.splitlines()
            if '=' in line and not line.lstrip().startswith('#')
        }
        additions = [
            f'{k}={v}'
            for k, v in _REQUIRED_ENV_KEYS.items()
            if k not in existing_keys
        ]
        if additions:
            with open(str(ENV_FILE), 'a', encoding='utf-8') as f:
                f.write('\n# Added by launcher repair (upgrade compatibility)\n')
                f.write('\n'.join(additions) + '\n')
    except Exception:
        pass


def _sync_rootca_to_client_setup() -> None:
    """Copy rootCA.pem from mkcert CAROOT into CLIENT-SETUP for USB distribution.

    Called on both first-run and normal start so that upgrade installs (which
    skip first-run) still get an up-to-date rootCA.pem in CLIENT-SETUP.
    No-op if mkcert has not been run yet, or if the copy fails for any reason.
    """
    try:
        result = subprocess.run(
            [str(MKCERT_EXE), "-CAROOT"],
            capture_output=True, text=True, check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        caroot = result.stdout.strip()
        if not caroot:
            return
        rootca_src = Path(caroot) / "rootCA.pem"
        if not rootca_src.exists():
            return
        rootca_dst = BUNDLE_DIR / "CLIENT-SETUP" / "rootCA.pem"
        # Only copy if missing or content differs (size check is sufficient).
        if (not rootca_dst.exists() or
                rootca_src.stat().st_size != rootca_dst.stat().st_size):
            shutil.copy(str(rootca_src), str(rootca_dst))
    except Exception:
        pass  # Non-fatal; IT admin can always get rootCA.pem manually.


def _ensure_hosts_entry(lan_ip: str) -> None:
    """Re-add fans-barangay.local to the hosts file if the entry is missing.

    Called on every normal start so that an upgrade install over a previous
    version that may have had a different hosts setup is self-healing.
    """
    try:
        text = HOSTS_FILE.read_text(encoding="utf-8", errors="replace")
        additions = []
        if "127.0.0.1    fans-barangay.local" not in text:
            additions.append("127.0.0.1    fans-barangay.local")
        if lan_ip and lan_ip != "127.0.0.1":
            entry = f"{lan_ip}    fans-barangay.local"
            if entry not in text:
                additions.append(entry)
        if additions:
            with open(str(HOSTS_FILE), "a", encoding="utf-8") as f:
                f.write("\n# FANS-C Verification System\n")
                f.write("\n".join(additions) + "\n")
    except Exception:
        pass  # Non-fatal; user can add hosts entry manually.


def _check_certs_exist() -> bool:
    """Return True if both HTTPS cert files exist next to fans_c.exe."""
    return CERT_FILE.exists() and KEY_FILE.exists()


def _kill_caddy() -> None:
    """Kill any running caddy.exe before starting a fresh instance.

    Necessary after a reinstall: the Inno Setup installer kills processes in
    CurStepChanged(ssInstall), but if the user launched FANS-C between
    uninstall and reinstall a stale caddy.exe may still hold port 443.
    """
    try:
        subprocess.run(
            ['taskkill', '/F', '/IM', 'caddy.exe'],
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception:
        pass


def _kill_stale_fans_c() -> None:
    """Kill other fans_c.exe processes that are not this process.

    After a reinstall the Task Scheduler watchdog can restart fans_c.exe
    while the installer's CurStepChanged kill is still in progress, leaving
    two instances alive.  The new instance kills the stale one here on
    startup so only one process owns port 8000 and spawns Caddy.
    """
    my_pid = os.getpid()
    try:
        result = subprocess.run(
            ['tasklist', '/FI', 'IMAGENAME eq fans_c.exe', '/FO', 'CSV', '/NH'],
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            # CSV: "fans_c.exe","1234","Console","1","xxx K"
            parts = line.strip('"').split('","')
            if len(parts) < 2:
                continue
            try:
                pid = int(parts[1])
            except ValueError:
                continue
            if pid == my_pid:
                continue
            subprocess.run(
                ['taskkill', '/F', '/PID', str(pid)],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            print(f"[FANS-C] Killed stale fans_c.exe PID {pid}", file=sys.stderr, flush=True)
    except Exception:
        pass


def _start_waitress() -> threading.Thread:
    """Start the Waitress WSGI server in a daemon thread."""
    # Emit proof-of-config lines to stderr.log so IT can confirm the frozen
    # EXE is actually applying the proxy-trust settings.  These lines are
    # written synchronously before the daemon thread starts.
    _ph = "x-forwarded-for, x-forwarded-host, x-forwarded-proto, x-forwarded-port"
    print(f"[FANS-C] Waitress trusted_proxy: 127.0.0.1", file=sys.stderr, flush=True)
    print(f"[FANS-C] Waitress trusted_proxy_count: 1", file=sys.stderr, flush=True)
    print(f"[FANS-C] Waitress trusted_proxy_headers: {_ph}", file=sys.stderr, flush=True)
    print(f"[FANS-C] Waitress clear_untrusted_proxy_headers: True", file=sys.stderr, flush=True)
    print(f"[FANS-C] Waitress log_untrusted_proxy_headers: True", file=sys.stderr, flush=True)

    def _serve():
        try:
            from waitress import serve
            from fans.wsgi import application
            # Trust only local Caddy (127.0.0.1) to set forwarded headers.
            # Without this Waitress strips X-Forwarded-Proto and Django sees
            # every request as HTTP, blocking camera verification.
            serve(
                application,
                host=WAITRESS_HOST,
                port=WAITRESS_PORT,
                threads=4,
                _quiet=True,
                trusted_proxy="127.0.0.1",
                trusted_proxy_count=1,
                trusted_proxy_headers={
                    "x-forwarded-for",
                    "x-forwarded-host",
                    "x-forwarded-proto",
                    "x-forwarded-port",
                },
                clear_untrusted_proxy_headers=True,
                log_untrusted_proxy_headers=True,
            )
        except Exception as e:
            _fatal("The web server (Waitress) crashed unexpectedly.", str(e))

    t = threading.Thread(target=_serve, daemon=True, name="waitress")
    t.start()
    return t


def _start_caddy() -> None:
    """Start Caddy reverse proxy as a hidden background process."""
    global _caddy_proc
    try:
        _caddy_proc = subprocess.Popen(
            [str(CADDY_EXE), "run", "--config", str(CADDYFILE)],
            cwd=_base_str,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception as e:
        _warn(
            "Could not start the HTTPS server (Caddy).\n\n"
            f"Detail: {e}\n\n"
            "The system may fall back to HTTP on port 8000.\n"
            "Contact IT if https://fans-barangay.local is unreachable."
        )


# ---------------------------------------------------------------------------
# .env content generated on first run
# ---------------------------------------------------------------------------

_ENV_TEMPLATE = """\
DEBUG=False
SECRET_KEY={secret_key}
EMBEDDING_ENCRYPTION_KEY={embedding_key}
USE_SQLITE=True
ALLOWED_HOSTS=fans-barangay.local,{lan_ip},localhost,127.0.0.1
CSRF_TRUSTED_ORIGINS=https://fans-barangay.local,https://{lan_ip}
SECURE_PROXY_SSL_HEADER=HTTP_X_FORWARDED_PROTO,https
USE_X_FORWARDED_HOST=True
DEMO_MODE=False
LIVENESS_REQUIRED=True
VERIFICATION_THRESHOLD=0.75
DEMO_THRESHOLD=0.60
ANTI_SPOOF_THRESHOLD=0.15
MAX_RETRY_ATTEMPTS=2
CONN_MAX_AGE=0
MEDIA_ROOT=media
"""

# Appended to _ENV_TEMPLATE only when the first-run wizard's optional email
# step is enabled. Left out entirely otherwise, so EMAIL_HOST stays unset
# and EMAIL_CONFIGURED stays False -- identical to pre-email-step behaviour.
_EMAIL_ENV_TEMPLATE = """\
# Email OTP (configured during first-run setup)
EMAIL_HOST={host}
EMAIL_PORT={port}
EMAIL_HOST_USER={user}
EMAIL_HOST_PASSWORD={password}
EMAIL_USE_TLS=True
DEFAULT_FROM_EMAIL={from_email}
"""


# ---------------------------------------------------------------------------
# First-run setup window
# ---------------------------------------------------------------------------

class _SetupWindow:
    """
    tkinter window that shows 8-step first-time setup progress.

    The setup steps run in a background thread; the main thread runs
    root.mainloop().  Cross-thread communication uses root.after() for
    UI updates and threading.Event for blocking the setup thread while
    the admin account form (a modal Toplevel) is open.
    """

    _STEPS = [
        "Generating security keys",
        "Setting up database",
        "Generating HTTPS certificate",
        "Configuring network hostname",
        "Creating admin account",
        "Registering autostart",
        "Starting FANS-C",
        "Setup complete",
    ]
    _TOTAL = len(_STEPS)

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("FANS-C  —  First-Time Setup")
        self.root.geometry("580x340")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind("<Escape>", lambda _e: self._on_close())
        self._set_icon()
        self._build_progress_ui()

        # Shutdown flags
        self._setup_done = False
        self._cancelling = False

        # Cross-thread state for the admin form
        self._admin_result: list = [None]
        self._admin_event  = threading.Event()

        # Cross-thread state for the optional email-OTP configuration form
        self._email_result: list = [None]
        self._email_event  = threading.Event()

    def _set_icon(self):
        ico = BUNDLE_DIR / "assets" / "logo.ico"
        if ico.exists():
            try:
                self.root.iconbitmap(str(ico))
            except Exception:
                pass

    def _build_progress_ui(self):
        self.root.configure(bg="#f4f6fb")
        self._setup_ttk_style()

        # Navy header bar with a thin gold accent border, matching the web
        # login header (static/css/main.css .login-header).
        hdr = tk.Frame(self.root, bg=BRAND_NAVY, height=72)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        hdr_text = tk.Frame(hdr, bg=BRAND_NAVY)
        hdr_text.pack(side="left", padx=18, pady=10)
        tk.Label(
            hdr_text,
            text="FANS-C  —  First-Time Setup",
            font=(BRAND_FONT, 13, "bold"),
            fg="white",
            bg=BRAND_NAVY,
            anchor="w",
        ).pack(anchor="w")
        tk.Label(
            hdr_text,
            text="Facial Verification System — Barangay Santol, Quezon City",
            font=(BRAND_FONT, 8),
            fg="#c9d8f2",
            bg=BRAND_NAVY,
            anchor="w",
        ).pack(anchor="w")
        tk.Frame(self.root, bg=BRAND_GOLD, height=4).pack(fill="x")

        # Content area
        self._content = tk.Frame(self.root, bg="#f4f6fb", padx=26, pady=18)
        self._content.pack(fill="both", expand=True)

        self._step_var   = tk.StringVar(value="Preparing…")
        self._detail_var = tk.StringVar(value="")

        tk.Label(
            self._content,
            textvariable=self._step_var,
            font=(BRAND_FONT, 10, "bold"),
            bg="#f4f6fb",
            fg="#1a2744",
            anchor="w",
        ).pack(fill="x")

        tk.Label(
            self._content,
            textvariable=self._detail_var,
            font=(BRAND_FONT, 9),
            bg="#f4f6fb",
            fg="#555",
            anchor="w",
            wraplength=510,
            justify="left",
        ).pack(fill="x", pady=(3, 12))

        self._bar = ttk.Progressbar(
            self._content, length=510, mode="determinate", maximum=100,
            style="FansC.Horizontal.TProgressbar",
        )
        self._bar.pack(fill="x")

        self._pct_var = tk.StringVar(value="")
        tk.Label(
            self._content,
            textvariable=self._pct_var,
            font=(BRAND_FONT, 8),
            bg="#f4f6fb",
            fg="#888",
        ).pack(anchor="e", pady=(2, 0))

        # Fills the previously-empty lower half of the window with a short,
        # always-visible description of what setup is doing, instead of dead
        # whitespace — steps are listed once rather than left implicit.
        steps_frame = tk.Frame(self._content, bg="#f4f6fb")
        steps_frame.pack(fill="x", pady=(14, 0))
        tk.Label(
            steps_frame,
            text="This wizard will generate your security configuration, set up the "
                 "database, install an HTTPS certificate, and create your administrator "
                 "account. This only runs once.",
            font=(BRAND_FONT, 8),
            bg="#f4f6fb",
            fg="#7a8399",
            anchor="w",
            justify="left",
            wraplength=510,
        ).pack(fill="x")

    def _setup_ttk_style(self):
        """One shared ttk.Style so every Toplevel's progress bar matches the
        brand instead of the default OS theme (which ignores color options
        under the default 'vista' theme on Windows)."""
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "FansC.Horizontal.TProgressbar",
            troughcolor="#dbe3f0",
            background=BRAND_GOLD,
            bordercolor="#dbe3f0",
            lightcolor=BRAND_GOLD,
            darkcolor=BRAND_GOLD,
            thickness=14,
        )

    # ------------------------------------------------------------------
    # X-button / Esc / cancel handler
    # ------------------------------------------------------------------

    def _on_close(self):
        if self._setup_done:
            self.root.destroy()
            return
        if self._cancelling:
            return
        if messagebox.askyesno(
            "Exit Setup",
            "Setup is not complete.\n\nExit FANS-C setup?",
            icon="warning",
            parent=self.root,
        ):
            self._cancel_setup()

    def _cancel_setup(self):
        """Stop all services started during setup and exit the process."""
        if self._cancelling:
            return
        self._cancelling = True
        # Unblock the setup thread if it is waiting on the admin form
        self._admin_result[0] = "CANCELLED"
        self._admin_event.set()
        # Stop Caddy if already started
        global _caddy_proc
        if _caddy_proc is not None:
            try:
                _caddy_proc.terminate()
            except Exception:
                pass
        try:
            self.root.destroy()
        except Exception:
            pass
        os._exit(0)

    # ------------------------------------------------------------------
    # Thread-safe progress update
    # ------------------------------------------------------------------

    def _do_update(self, step_idx: int, detail: str):
        pct = int((step_idx - 1) / self._TOTAL * 100)
        self._step_var.set(
            f"Step {step_idx} of {self._TOTAL}:  {self._STEPS[step_idx - 1]}"
        )
        self._detail_var.set(detail)
        self._bar["value"] = pct
        self._pct_var.set(f"{pct}%")

    def update(self, step_idx: int, detail: str = ""):
        """Call from the setup thread to update progress bar."""
        self.root.after(0, lambda s=step_idx, d=detail: self._do_update(s, d))

    # ------------------------------------------------------------------
    # Admin account form (modal Toplevel, opened on the main thread)
    # ------------------------------------------------------------------

    def _open_admin_form(self):
        """
        Opens the admin account creation form as a resizable modal window.
        Layout: left info panel | right scrollable form + fixed bottom button bar.
        Runs on the main thread via root.after().  Blocks via wait_window().
        """
        # v2.1.19 UX pass (section 8A/40): the bootstrap account created by
        # first-run setup is always the Technical Administrator (role=IT) —
        # no role picker. It is technical/research access for initial setup,
        # diagnostics, and controlled biometric evaluation, NOT a barangay
        # organizational position. The barangay's actual President account
        # is created afterward, inside the app, by the Technical
        # Administrator (Dashboard -> "Create President Account", available
        # only until a President exists).
        FIXED_ROLE = "it"
        TECHNICAL_ADMIN_BLURB = (
            "Technical access for initial system setup, diagnostics, "
            "security review, maintenance, and controlled biometric "
            "evaluation. This account is not a barangay organizational "
            "position."
        )
        USERNAME_RE   = re.compile(r"^[a-z0-9_]+$")
        SPECIAL_CHARS = r"!@#$%^&*()_+-=[]{}|;:,.<>?"

        # Brand colors (v2.1.16 Issue 9) — reuse the shared FANS-C constants
        # instead of this window's previous ad-hoc indigo (#1a3a6b), which
        # didn't match the web app's navy/gold identity or Window 1.
        BG          = "#f8f8f8"
        PANEL_BG    = BRAND_NAVY
        PANEL_FG    = "#b8cfea"
        LABEL_FG    = "#333333"
        HINT_FG     = "#888888"
        ERROR_FG    = "#cc0000"
        OK_FG       = "#1b7a32"
        BORDER      = "#cccccc"
        FOCUS_BLUE  = BRAND_NAVY_LIGHT
        BTN_BG      = BRAND_NAVY
        BTN_HOVER   = BRAND_NAVY_LIGHT
        BTN_DISABLE = "#888a8e"
        FONT        = (BRAND_FONT, 11)
        FONT_BOLD   = (BRAND_FONT, 11, "bold")
        FONT_TITLE  = (BRAND_FONT, 14, "bold")
        FONT_SUB    = (BRAND_FONT, 10)
        FONT_HINT   = (BRAND_FONT, 9)

        # ------------------------------------------------------------------
        # Window
        # ------------------------------------------------------------------
        top = tk.Toplevel(self.root)
        top.title("FANS-C  —  Create Technical Administrator Account")
        top.configure(bg=BG)
        top.resizable(True, True)
        top.minsize(720, 480)
        top.grab_set()
        top.attributes("-topmost", True)
        top.after(300, lambda: top.attributes("-topmost", False))
        ico = BUNDLE_DIR / "assets" / "logo.ico"
        if ico.exists():
            try:
                top.iconbitmap(str(ico))
            except Exception:
                pass

        # ------------------------------------------------------------------
        # Cancel / X-close handler
        # ------------------------------------------------------------------
        def _on_admin_cancel():
            if self._cancelling:
                return
            if messagebox.askyesno(
                "Exit Setup",
                "Setup is not complete.\n\nExit FANS-C setup?\n"
                "The application will close.",
                parent=top,
                icon="warning",
            ):
                self._admin_result[0] = "CANCELLED"
                self._admin_event.set()
                try:
                    top.destroy()
                except Exception:
                    pass
                self._cancel_setup()

        top.protocol("WM_DELETE_WINDOW", _on_admin_cancel)
        top.bind("<Escape>", lambda _e: _on_admin_cancel())

        # ------------------------------------------------------------------
        # Fixed button bar — packed FIRST so it always stays at the bottom
        # ------------------------------------------------------------------
        tk.Frame(top, bg=BORDER, height=1).pack(side="bottom", fill="x")
        btn_bar = tk.Frame(top, bg="#efefef", pady=10, padx=16)
        btn_bar.pack(side="bottom", fill="x")

        cancel_btn = tk.Button(
            btn_bar, text="Cancel",
            bg="#d0d0d0", fg="#333",
            activebackground="#b8b8b8", activeforeground="#111",
            font=FONT_BOLD, relief="flat", bd=0,
            cursor="hand2", padx=14, pady=8,
            command=_on_admin_cancel,
        )
        cancel_btn.pack(side="right", padx=(8, 0))

        btn_var = tk.StringVar(value="Create Account  →")
        btn = tk.Button(
            btn_bar,
            textvariable=btn_var,
            bg=BTN_DISABLE, fg="white",
            activebackground=BTN_HOVER, activeforeground="white",
            font=FONT_BOLD, relief="flat", bd=0,
            cursor="arrow", state="disabled",
            padx=14, pady=8,
        )
        btn.pack(side="right")

        def _btn_enter(_):
            if str(btn["state"]) != "disabled":
                btn.configure(bg=BTN_HOVER)
        def _btn_leave(_):
            if str(btn["state"]) != "disabled":
                btn.configure(bg=BTN_BG)
        btn.bind("<Enter>", _btn_enter)
        btn.bind("<Leave>", _btn_leave)

        # ------------------------------------------------------------------
        # Main area: left info panel + right form area
        # ------------------------------------------------------------------
        main_area = tk.Frame(top, bg=BG)
        main_area.pack(fill="both", expand=True)

        # LEFT INFO PANEL (fixed 210px, brand navy with a gold top accent —
        # mirrors the web login header's navy-bar + gold-border motif).
        LEFT_W = 210
        left = tk.Frame(main_area, bg=PANEL_BG, width=LEFT_W)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        tk.Frame(left, bg=BRAND_GOLD, height=3).pack(fill="x")

        logo_png = BUNDLE_DIR / "assets" / "logo.png"
        if logo_png.exists():
            try:
                img = tk.PhotoImage(file=str(logo_png))
                _w = img.width()
                if _w > 52:
                    factor = max(1, _w // 52)
                    img = img.subsample(factor, factor)
                _lbl = tk.Label(left, image=img, bg=PANEL_BG)
                _lbl.image = img
                _lbl.pack(pady=(20, 8))
            except Exception:
                tk.Frame(left, bg=PANEL_BG, height=28).pack()
        else:
            tk.Frame(left, bg=PANEL_BG, height=28).pack()

        tk.Label(
            left, text="Create Technical\nAdministrator Account",
            font=("Segoe UI", 11, "bold"),
            fg="white", bg=PANEL_BG, justify="center",
        ).pack(padx=12)
        tk.Frame(left, bg="#2a4d8c", height=1).pack(fill="x", padx=14, pady=10)
        tk.Label(
            left,
            text="Technical/research access\nfor initial setup. Not a\nbarangay position.",
            font=("Segoe UI", 9), fg=PANEL_FG, bg=PANEL_BG,
            justify="center", wraplength=LEFT_W - 20,
        ).pack(padx=12)
        tk.Frame(left, bg="#2a4d8c", height=1).pack(fill="x", padx=14, pady=10)
        tk.Label(
            left, text="Password rules:",
            font=("Segoe UI", 9, "bold"), fg="white", bg=PANEL_BG, anchor="w",
        ).pack(fill="x", padx=14)
        for _req in [
            "✔  10+ characters",
            "✔  Uppercase letter",
            "✔  Lowercase letter",
            "✔  A digit (0–9)",
            "✔  A special char",
            "    (!@#$%^&*...)",
        ]:
            tk.Label(
                left, text=_req, font=("Segoe UI", 8),
                fg=PANEL_FG, bg=PANEL_BG, anchor="w",
            ).pack(fill="x", padx=16)

        # Spacer + version marker pinned to bottom of left panel
        tk.Frame(left, bg=PANEL_BG).pack(fill="both", expand=True)
        tk.Label(
            left, text="Admin Setup UI v2.0.1",
            font=("Segoe UI", 7), fg="#4a6a8a", bg=PANEL_BG,
            anchor="center",
        ).pack(fill="x", pady=(0, 10))

        # RIGHT FORM AREA
        right = tk.Frame(main_area, bg=BG)
        right.pack(side="left", fill="both", expand=True)

        # Form header (non-scrolling title at top of right panel)
        form_header = tk.Frame(right, bg=BG, padx=22, pady=14)
        form_header.pack(fill="x")
        tk.Label(
            form_header, text="Create Your Admin Account",
            font=FONT_TITLE, fg=LABEL_FG, bg=BG, anchor="w",
        ).pack(fill="x")
        tk.Label(
            form_header,
            text="This account will have full access to the system.",
            font=FONT_SUB, fg=HINT_FG, bg=BG, anchor="w",
        ).pack(fill="x")
        tk.Frame(right, bg=BORDER, height=1).pack(fill="x", padx=4)

        # Scrollable form body via Canvas + Scrollbar
        scroll_outer = tk.Frame(right, bg=BG)
        scroll_outer.pack(fill="both", expand=True)

        form_canvas = tk.Canvas(scroll_outer, bg=BG, highlightthickness=0)
        v_scroll = ttk.Scrollbar(
            scroll_outer, orient="vertical", command=form_canvas.yview,
        )
        body = tk.Frame(form_canvas, bg=BG, padx=22, pady=8)

        form_canvas.pack(side="left", fill="both", expand=True)
        v_scroll.pack(side="right", fill="y")
        form_canvas.configure(yscrollcommand=v_scroll.set)

        body_win = form_canvas.create_window((0, 0), window=body, anchor="nw")

        def _on_body_resize(_e):
            form_canvas.configure(scrollregion=form_canvas.bbox("all"))

        def _on_canvas_resize(e):
            form_canvas.itemconfig(body_win, width=e.width)

        body.bind("<Configure>", _on_body_resize)
        form_canvas.bind("<Configure>", _on_canvas_resize)

        def _mousewheel(e):
            form_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")

        def _bind_mousewheel_recursive(widget):
            """Bind mousewheel to widget and all descendants so scrolling works
            regardless of which child widget the cursor is over."""
            widget.bind("<MouseWheel>", _mousewheel, add="+")
            for child in widget.winfo_children():
                _bind_mousewheel_recursive(child)

        form_canvas.bind("<MouseWheel>", _mousewheel)
        body.bind("<MouseWheel>", _mousewheel)

        # ------------------------------------------------------------------
        # Form fields
        # ------------------------------------------------------------------
        widgets = {}

        def _make_field(parent, key, label_text, placeholder, secret=False,
                        rules_text=None):
            row = tk.Frame(parent, bg=BG)
            row.pack(fill="x", pady=(0, 10))

            lbl_var = tk.StringVar(value=label_text)
            lbl = tk.Label(
                row, textvariable=lbl_var,
                font=FONT_BOLD, fg=LABEL_FG, bg=BG, anchor="w",
            )
            lbl.pack(fill="x")

            border = tk.Frame(row, bg=BORDER, padx=1, pady=1)
            border.pack(fill="x", pady=(2, 0))

            # Inner row: entry + optional show/hide button side by side
            inner = tk.Frame(border, bg="white")
            inner.pack(fill="x")

            entry_kwargs = dict(
                font=FONT, relief="flat", bd=0,
                highlightthickness=0, bg="white", fg=LABEL_FG,
            )
            entry = tk.Entry(inner, show="", **entry_kwargs)
            entry.pack(side="left", fill="x", expand=True, ipady=6, padx=(6, 0))

            # Show/Hide toggle for secret fields
            pw_visible = {"on": False}
            if secret:
                def _toggle_visibility(e=None, _entry=entry, _state=pw_visible, _ph=None):
                    if _ph and _ph.get("showing", False):
                        return
                    _state["on"] = not _state["on"]
                    _entry.configure(show="" if _state["on"] else "*")
                    eye_btn.configure(text="\U0001F441" if _state["on"] else "\U0001F576")
                eye_btn = tk.Label(
                    inner, text="\U0001F576",
                    font=("Segoe UI", 9), bg="white", fg="#888",
                    cursor="hand2", padx=6,
                )
                eye_btn.pack(side="right", fill="y")
                # We need ph_state defined before binding, so we do it after
            else:
                eye_btn = None

            ph_state = {"showing": True}

            if secret and eye_btn is not None:
                eye_btn.bind("<Button-1>", lambda e, _e=entry, _s=pw_visible, _ph=ph_state:
                             _toggle_visibility(e, _entry=_e, _state=_s, _ph=_ph))

            def _show_placeholder(_=None, _entry=entry, _ph=ph_state, _ev=pw_visible):
                if not _entry.get():
                    _ph["showing"] = True
                    _entry.configure(fg=HINT_FG)
                    if secret:
                        _ev["on"] = False
                        _entry.configure(show="")
                        if eye_btn:
                            eye_btn.configure(text="\U0001F576")
                    _entry.delete(0, "end")
                    _entry.insert(0, placeholder)

            def _clear_placeholder(_=None, _entry=entry, _ph=ph_state):
                if _ph["showing"]:
                    _entry.delete(0, "end")
                    _entry.configure(fg=LABEL_FG)
                    if secret:
                        _entry.configure(show="*")
                    _ph["showing"] = False

            entry.bind("<FocusIn>",  _clear_placeholder)
            entry.bind("<FocusOut>", _show_placeholder)
            _show_placeholder()

            def _on_focus_in(_):  border.configure(bg=FOCUS_BLUE)
            def _on_focus_out(_): border.configure(bg=BORDER)
            entry.bind("<FocusIn>",  _on_focus_in,  add="+")
            entry.bind("<FocusOut>", _on_focus_out, add="+")

            if secret:
                def _swallow(_): return "break"
                for seq in ("<Control-c>", "<Control-C>",
                            "<Control-x>", "<Control-X>",
                            "<<Copy>>", "<<Cut>>",
                            "<Button-3>", "<Control-Insert>"):
                    entry.bind(seq, _swallow)

            if rules_text:
                tk.Label(
                    row, text=rules_text,
                    font=FONT_HINT, fg=HINT_FG, bg=BG,
                    anchor="w", justify="left", wraplength=380,
                ).pack(fill="x", pady=(2, 0))

            err_var = tk.StringVar(value="")
            err_lbl = tk.Label(
                row, textvariable=err_var,
                font=FONT_HINT, fg=ERROR_FG, bg=BG,
                anchor="w", justify="left", wraplength=380,
            )
            err_lbl.pack(fill="x")

            widgets[key] = {
                "entry": entry, "label": lbl, "label_var": lbl_var,
                "label_text": label_text, "border": border,
                "err_var": err_var, "err_lbl": err_lbl,
                "row": row, "is_placeholder": ph_state,
            }
            return entry

        _make_field(body, "first_name", "First Name", "e.g. Juan")
        _make_field(body, "last_name", "Last Name", "e.g. Dela Cruz")
        _make_field(
            body, "username", "Username", "e.g. admin_juan",
            rules_text="Lowercase letters, numbers, and underscores only.",
        )
        _make_field(
            body, "password", "Password", "",
            secret=True,
            rules_text=(
                "At least 10 characters. Must include uppercase, lowercase, "
                "a number, and a special character (!@#$%^&*)."
            ),
        )

        pw_row = widgets["password"]["row"]
        strength_frame = tk.Frame(pw_row, bg=BG)
        strength_frame.pack(fill="x", pady=(4, 0),
                            before=widgets["password"]["err_lbl"])
        strength_bar_bg = tk.Frame(strength_frame, bg="#e0e0e0", height=6)
        strength_bar_bg.pack(fill="x")
        strength_bar = tk.Frame(strength_bar_bg, bg="#e0e0e0", height=6)
        strength_bar.place(relx=0, rely=0, relwidth=0, relheight=1)
        strength_var = tk.StringVar(value="")
        strength_lbl = tk.Label(
            strength_frame, textvariable=strength_var,
            font=FONT_HINT, bg=BG, anchor="w",
        )
        strength_lbl.pack(fill="x")

        _make_field(body, "confirm_password", "Confirm Password", "", secret=True)
        cp_row = widgets["confirm_password"]["row"]
        match_var = tk.StringVar(value="")
        match_lbl = tk.Label(
            cp_row, textvariable=match_var,
            font=FONT_HINT, bg=BG, anchor="w",
        )
        match_lbl.pack(fill="x", pady=(2, 0),
                       before=widgets["confirm_password"]["err_lbl"])

        # Role is fixed to Technical Administrator — no picker. See
        # FIXED_ROLE / TECHNICAL_ADMIN_BLURB above.
        role_row = tk.Frame(body, bg=BG)
        role_row.pack(fill="x", pady=(0, 10))
        role_lbl_var = tk.StringVar(value="System Role")
        role_lbl = tk.Label(
            role_row, textvariable=role_lbl_var,
            font=FONT_BOLD, fg=LABEL_FG, bg=BG, anchor="w",
        )
        role_lbl.pack(fill="x")
        tk.Label(
            role_row, text="Technical Administrator",
            font=FONT, fg=LABEL_FG, bg=BG, anchor="w",
        ).pack(fill="x", pady=(2, 0))
        role_var = tk.StringVar(value="IT")
        role_desc_var = tk.StringVar(value=TECHNICAL_ADMIN_BLURB)
        tk.Label(
            role_row, textvariable=role_desc_var,
            font=FONT_HINT, fg=HINT_FG, bg=BG,
            anchor="w", justify="left", wraplength=380,
        ).pack(fill="x", pady=(2, 0))
        role_err_var = tk.StringVar(value="")
        tk.Label(
            role_row, textvariable=role_err_var,
            font=FONT_HINT, fg=ERROR_FG, bg=BG, anchor="w",
        ).pack(fill="x")
        widgets["role"] = {
            "label": role_lbl, "label_var": role_lbl_var,
            "label_text": "System Role", "err_var": role_err_var, "row": role_row,
        }

        # ------------------------------------------------------------------
        # Helpers
        # ------------------------------------------------------------------
        def _value(key):
            w = widgets[key]
            if w.get("is_placeholder", {}).get("showing"):
                return ""
            return w["entry"].get()

        def _clear_field_error(key):
            w = widgets[key]
            w["err_var"].set("")
            w["label"].configure(fg=LABEL_FG)

        def _set_field_error(key, msg):
            w = widgets[key]
            w["err_var"].set(msg)
            w["label"].configure(fg=ERROR_FG)

        def _password_strength(pw):
            if not pw:
                return ("", "#e0e0e0", 0.0)
            score = 0
            if len(pw) >= 10:      score += 1
            if re.search(r"[a-z]", pw): score += 1
            if re.search(r"[A-Z]", pw): score += 1
            if re.search(r"\d",    pw): score += 1
            if any(c in SPECIAL_CHARS for c in pw): score += 1
            if len(pw) >= 14:      score += 1
            if score <= 2: return ("Weak",   "#cc0000", 0.33)
            if score <= 4: return ("Fair",   "#e08e0b", 0.66)
            return                 ("Strong", "#1b7a32", 1.0)

        def _update_strength(*_):
            pw = _value("password")
            label, color, frac = _password_strength(pw)
            strength_bar.configure(bg=color)
            strength_bar.place_configure(relwidth=frac)
            if label:
                strength_var.set("Strength: " + label)
                strength_lbl.configure(fg=color)
            else:
                strength_var.set("")

        def _update_match(*_):
            pw  = _value("password")
            cpw = _value("confirm_password")
            if not cpw:
                match_var.set("")
                return
            if pw == cpw:
                match_var.set("[OK] Passwords match")
                match_lbl.configure(fg=OK_FG)
            else:
                match_var.set("[X] Passwords do not match")
                match_lbl.configure(fg=ERROR_FG)

        def _all_filled():
            return (
                bool(_value("first_name")) and
                bool(_value("last_name")) and
                bool(_value("username")) and
                bool(_value("password")) and
                bool(_value("confirm_password")) and
                bool(role_var.get())
            )

        def _update_button_state(*_):
            if _all_filled():
                btn.configure(state="normal", bg=BTN_BG, cursor="hand2")
            else:
                btn.configure(state="disabled", bg=BTN_DISABLE, cursor="arrow")

        for key in ("first_name", "last_name", "username", "password", "confirm_password"):
            entry = widgets[key]["entry"]
            entry.bind("<KeyRelease>", _update_button_state, add="+")
            entry.bind("<FocusOut>",   _update_button_state, add="+")
            entry.bind("<FocusIn>",    _update_button_state, add="+")
        widgets["password"]["entry"].bind(
            "<KeyRelease>", _update_strength, add="+")
        widgets["password"]["entry"].bind(
            "<KeyRelease>", _update_match, add="+")
        widgets["confirm_password"]["entry"].bind(
            "<KeyRelease>", _update_match, add="+")

        # Bind mousewheel to every widget now that all form fields are created.
        # Without this, wheel events are consumed by Entry/Label widgets and
        # do not propagate to the Canvas, so the form won't scroll.
        _bind_mousewheel_recursive(body)

        # Enter submits when button is active; Esc cancels
        top.bind("<Return>",
                 lambda _e: _submit() if str(btn["state"]) == "normal" else None)

        # ------------------------------------------------------------------
        # Validation + submit
        # ------------------------------------------------------------------
        def _validate():
            errs = []
            fname = _value("first_name").strip()
            lname = _value("last_name").strip()
            uname = _value("username").strip()
            pw    = _value("password")
            cpw   = _value("confirm_password")
            role  = role_var.get()

            if not fname:
                errs.append(("first_name", "First name is required."))
            if not lname:
                errs.append(("last_name", "Last name is required."))
            if not uname:
                errs.append(("username", "Username is required."))
            else:
                if len(uname) < 3:
                    errs.append(("username",
                                 "Username must be at least 3 characters."))
                elif not USERNAME_RE.match(uname):
                    errs.append(("username",
                                 "Use only lowercase letters, numbers, "
                                 "and underscores."))
                if pw and uname.lower() == pw.lower():
                    errs.append(("username",
                                 "Username must not match the password."))
            if len(pw) < 10:
                errs.append(("password",
                             "Password must be at least 10 characters."))
            else:
                if not re.search(r"[A-Z]", pw):
                    errs.append(("password",
                                 "Password must contain an uppercase letter."))
                elif not re.search(r"[a-z]", pw):
                    errs.append(("password",
                                 "Password must contain a lowercase letter."))
                elif not re.search(r"\d", pw):
                    errs.append(("password",
                                 "Password must contain a digit."))
                elif not any(c in SPECIAL_CHARS for c in pw):
                    errs.append(("password",
                                 "Password must contain a special character "
                                 "from " + SPECIAL_CHARS))
            if pw != cpw:
                errs.append(("confirm_password", "Passwords do not match."))
            if not role:
                errs.append(("role", "Role is required."))
            return errs

        def _submit():
            if self._cancelling:
                return
            for key in ("first_name", "last_name", "username", "password",
                        "confirm_password", "role"):
                _clear_field_error(key)

            errs = _validate()
            if errs:
                seen = set()
                for key, msg in errs:
                    if key in seen:
                        continue
                    seen.add(key)
                    _set_field_error(key, msg)
                first_key = errs[0][0]
                if first_key in widgets and "entry" in widgets[first_key]:
                    widgets[first_key]["entry"].focus_set()
                return

            first = _value("first_name").strip()
            last  = _value("last_name").strip()
            uname = _value("username").strip()
            pw    = _value("password")
            role  = FIXED_ROLE

            btn_var.set("Creating account…")
            btn.configure(state="disabled", bg=BTN_DISABLE, cursor="arrow")
            top.update_idletasks()

            try:
                from accounts.models import CustomUser
                if CustomUser.objects.filter(username=uname).exists():
                    _set_field_error(
                        "username",
                        'Username "{}" already exists. Choose another.'
                        .format(uname),
                    )
                    btn_var.set("Create Account  →")
                    _update_button_state()
                    return
                CustomUser.objects.create_user(
                    username=uname,
                    email="{}@fans.local".format(uname),
                    password=pw,
                    first_name=first,
                    last_name=last,
                    role=role,
                    employee_id="ADM-001",
                    is_superuser=False,
                    is_staff=False,
                )
            except Exception as exc:
                _set_field_error("username",
                                 "Could not create account: {}".format(exc))
                btn_var.set("Create Account  →")
                _update_button_state()
                return

            self._admin_result[0] = (uname, first, last, role_var.get())
            _show_success(uname, "Technical Administrator")

        btn.configure(command=_submit)

        # ------------------------------------------------------------------
        # Success state — replaces main_area + btn_bar content
        # ------------------------------------------------------------------
        def _continue():
            self._admin_event.set()
            try:
                top.destroy()
            except Exception:
                pass

        def _show_success(username_value, role_label):
            for w in main_area.winfo_children():
                w.destroy()
            for w in btn_bar.winfo_children():
                w.destroy()

            success_frame = tk.Frame(main_area, bg=BG)
            success_frame.pack(fill="both", expand=True)

            tk.Label(
                success_frame, text="[ OK ]",
                font=("Segoe UI", 32, "bold"), fg=OK_FG, bg=BG,
            ).pack(pady=(20, 4))
            tk.Label(
                success_frame, text="Account Created!",
                font=("Segoe UI", 16, "bold"), fg=LABEL_FG, bg=BG,
            ).pack(pady=(0, 12))
            tk.Label(
                success_frame,
                text="Username: {}".format(username_value),
                font=FONT, fg=LABEL_FG, bg=BG,
            ).pack()
            tk.Label(
                success_frame,
                text="Role: {}".format(role_label),
                font=FONT, fg=LABEL_FG, bg=BG,
            ).pack(pady=(0, 18))

            cont_btn = tk.Button(
                btn_bar, text="Continue  →",
                bg=BTN_BG, fg="white",
                activebackground=BTN_HOVER, activeforeground="white",
                font=FONT_BOLD, relief="flat", bd=0,
                cursor="hand2", padx=14, pady=10,
                command=_continue,
            )
            cont_btn.pack(side="right")

            def _c_enter(_): cont_btn.configure(bg=BTN_HOVER)
            def _c_leave(_): cont_btn.configure(bg=BTN_BG)
            cont_btn.bind("<Enter>", _c_enter)
            cont_btn.bind("<Leave>", _c_leave)

            top.update_idletasks()
            _center_top()

        # ------------------------------------------------------------------
        # Initial sizing + centering
        # ------------------------------------------------------------------
        def _center_top():
            top.update_idletasks()
            sw = top.winfo_screenwidth()
            sh = top.winfo_screenheight()
            w  = min(max(top.winfo_reqwidth(), 820), int(sw * 0.92))
            h  = min(max(top.winfo_reqheight(), 520), int(sh * 0.90))
            x  = (sw // 2) - (w // 2)
            y  = (sh // 2) - (h // 2)
            top.geometry("{}x{}+{}+{}".format(w, h, x, y))

        _on_role_change()
        _update_button_state()
        top.update_idletasks()
        _center_top()
        top.lift()
        top.focus_force()

        top.wait_window()

    # ------------------------------------------------------------------
    # Email OTP configuration form (modal Toplevel, opened on the main
    # thread). Optional -- skipping it (checkbox left unchecked) reproduces
    # today's .env output exactly (no EMAIL_* keys, EMAIL_CONFIGURED=False).
    # ------------------------------------------------------------------

    def _open_email_form(self):
        BG        = "#f8f8f8"
        PANEL_BG  = BRAND_NAVY
        PANEL_FG  = "#b8cfea"
        LABEL_FG  = "#333333"
        HINT_FG   = "#888888"
        ERROR_FG  = "#cc0000"
        BTN_BG    = BRAND_NAVY
        BTN_HOVER = BRAND_NAVY_LIGHT
        FONT      = (BRAND_FONT, 11)
        FONT_BOLD = (BRAND_FONT, 11, "bold")
        FONT_TITLE = (BRAND_FONT, 13, "bold")
        FONT_HINT = (BRAND_FONT, 9)

        top = tk.Toplevel(self.root)
        top.title("FANS-C  —  Email OTP Configuration (Optional)")
        top.configure(bg=BG)
        top.resizable(False, False)
        top.grab_set()
        top.attributes("-topmost", True)
        top.after(300, lambda: top.attributes("-topmost", False))
        ico = BUNDLE_DIR / "assets" / "logo.ico"
        if ico.exists():
            try:
                top.iconbitmap(str(ico))
            except Exception:
                pass

        def _on_cancel():
            if self._cancelling:
                return
            if messagebox.askyesno(
                "Exit Setup",
                "Setup is not complete.\n\nExit FANS-C setup?\n"
                "The application will close.",
                parent=top,
                icon="warning",
            ):
                self._email_result[0] = "CANCELLED"
                self._email_event.set()
                try:
                    top.destroy()
                except Exception:
                    pass
                self._cancel_setup()

        top.protocol("WM_DELETE_WINDOW", _on_cancel)
        top.bind("<Escape>", lambda _e: _on_cancel())

        # Left info panel
        panel = tk.Frame(top, bg=PANEL_BG, width=220)
        panel.pack(side="left", fill="y")
        panel.pack_propagate(False)
        tk.Label(
            panel, text="Email OTP\nConfiguration", font=FONT_TITLE,
            fg="white", bg=PANEL_BG, justify="left", anchor="w",
        ).pack(fill="x", padx=18, pady=(24, 10))
        tk.Label(
            panel,
            text=(
                "FANS-C can email a 6-digit code so staff can reset a "
                "forgotten password themselves.\n\n"
                "This is optional. If you skip it, locked-out staff can "
                "still request help from an administrator inside the app "
                "(Settings → Account Recovery Administration).\n\n"
                "You can also add this later by editing .env and "
                "restarting FANS-C — see SETUP.md."
            ),
            font=("Segoe UI", 9), fg=PANEL_FG, bg=PANEL_BG,
            justify="left", anchor="nw", wraplength=190,
        ).pack(fill="both", expand=True, padx=18)

        # Right form area
        form = tk.Frame(top, bg=BG, padx=24, pady=20)
        form.pack(side="left", fill="both", expand=True)
        form.grid_columnconfigure(0, weight=1)

        enable_var = tk.BooleanVar(value=False)
        host_var   = tk.StringVar(value="smtp.gmail.com")
        port_var   = tk.StringVar(value="587")
        user_var   = tk.StringVar(value="fansc.system@gmail.com")
        pass_var   = tk.StringVar(value="")
        from_var   = tk.StringVar(value="fansc.system@gmail.com")
        error_var  = tk.StringVar(value="")

        row_counter = [1]  # row 0 is reserved for the enable checkbox

        def _field(label_text, var, show=None, hint=None):
            r = row_counter[0]
            tk.Label(
                form, text=label_text, font=FONT_BOLD, fg=LABEL_FG, bg=BG,
                anchor="w",
            ).grid(row=r, column=0, sticky="w", pady=(10, 0))
            e = tk.Entry(
                form, textvariable=var, font=FONT, width=34, relief="solid",
                bd=1, highlightthickness=0, show=show or "",
            )
            e.grid(row=r + 1, column=0, sticky="we", ipady=4)
            next_r = r + 2
            if hint:
                tk.Label(
                    form, text=hint, font=FONT_HINT, fg=HINT_FG, bg=BG,
                    anchor="w", wraplength=380, justify="left",
                ).grid(row=next_r, column=0, sticky="w")
                next_r += 1
            row_counter[0] = next_r
            return e

        host_e = _field("SMTP Host", host_var)
        port_e = _field("SMTP Port", port_var)
        user_e = _field("SMTP Username", user_var)
        pass_e = _field(
            "SMTP Password / App Password", pass_var, show="*",
            hint="Gmail: use a 16-character App Password, not the normal account password.",
        )
        from_e = _field("Default From Email", from_var)

        def _toggle():
            state = "normal" if enable_var.get() else "disabled"
            fg = LABEL_FG if state == "normal" else HINT_FG
            for e in (host_e, port_e, user_e, pass_e, from_e):
                e.configure(state=state, fg=fg)

        chk = tk.Checkbutton(
            form, text="Enable Email OTP delivery", variable=enable_var,
            font=FONT_BOLD, bg=BG, fg=LABEL_FG, anchor="w", command=_toggle,
        )
        chk.grid(row=0, column=0, sticky="w")
        _toggle()  # start disabled to match the unchecked default

        err_lbl = tk.Label(
            form, textvariable=error_var, font=FONT_HINT, fg=ERROR_FG,
            bg=BG, anchor="w", wraplength=380, justify="left",
        )
        err_lbl.grid(row=row_counter[0], column=0, sticky="w", pady=(8, 0))
        row_counter[0] += 1

        btn_row = tk.Frame(form, bg=BG)
        btn_row.grid(row=row_counter[0], column=0, sticky="e", pady=(14, 0))

        def _finish(result: dict):
            self._email_result[0] = result
            self._email_event.set()
            try:
                top.destroy()
            except Exception:
                pass

        def _skip():
            if self._cancelling:
                return
            _finish({"enabled": False})

        def _submit():
            if self._cancelling:
                return
            error_var.set("")
            if not enable_var.get():
                _finish({"enabled": False})
                return

            host = host_var.get().strip()
            port_raw = port_var.get().strip()
            user = user_var.get().strip()
            # Gmail App Passwords are often copy-pasted with spaces added
            # for readability -- strip them rather than reject a valid one.
            pwd = "".join(pass_var.get().split())
            frm = from_var.get().strip()

            if not host:
                error_var.set("SMTP Host is required.")
                host_e.focus_set()
                return
            if not port_raw.isdigit() or not (1 <= int(port_raw) <= 65535):
                error_var.set("SMTP Port must be a number between 1 and 65535.")
                port_e.focus_set()
                return
            if not user or "@" not in user:
                error_var.set("SMTP Username must be a valid email address.")
                user_e.focus_set()
                return
            if not pwd:
                error_var.set("SMTP Password / App Password is required.")
                pass_e.focus_set()
                return
            if not frm or "@" not in frm:
                error_var.set("Default From Email must be a valid email address.")
                from_e.focus_set()
                return

            _finish({
                "enabled": True,
                "host": host,
                "port": port_raw,
                "user": user,
                "password": pwd,
                "from_email": frm,
            })

        tk.Button(
            btn_row, text="Skip", command=_skip, relief="flat", padx=14,
            pady=6,
        ).pack(side="left", padx=(0, 8))
        tk.Button(
            btn_row, text="Continue  →", command=_submit, bg=BTN_BG,
            fg="white", relief="flat", padx=18, pady=6,
            activebackground=BTN_HOVER, activeforeground="white",
        ).pack(side="left")

        top.update_idletasks()
        w, h = top.winfo_reqwidth(), top.winfo_reqheight()
        x = (top.winfo_screenwidth() // 2) - (w // 2)
        y = (top.winfo_screenheight() // 2) - (h // 2)
        top.geometry("{}x{}+{}+{}".format(w, h, x, y))
        top.lift()
        top.focus_force()

        top.wait_window()

    def request_email_form(self):
        """Setup thread calls this; blocks until submitted, skipped, or the
        whole setup is cancelled. Returns a dict (possibly {'enabled': False}
        for a skip) or None if the user cancelled setup entirely."""
        self._email_event.clear()
        self._email_result[0] = None
        self.root.after(0, self._open_email_form)
        self._email_event.wait()
        result = self._email_result[0]
        if result == "CANCELLED" or self._cancelling:
            return None
        return result

    def request_admin_form(self):
        """Setup thread calls this; blocks until the form is submitted or cancelled."""
        self._admin_event.clear()
        self._admin_result[0] = None
        self.root.after(0, self._open_admin_form)
        self._admin_event.wait()
        result = self._admin_result[0]
        if result == "CANCELLED" or self._cancelling:
            return None
        return result

    # ------------------------------------------------------------------
    # Success screen (replaces progress bar content in the same window)
    # ------------------------------------------------------------------

    def _show_success(self, lan_ip: str, facenet_ready: bool = True):
        self._setup_done = True
        self.root.geometry("580x430")
        self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)

        for w in self._content.winfo_children():
            w.destroy()

        if facenet_ready:
            self.root.title("FANS-C is Ready!")
            tk.Label(
                self._content,
                text="✅  Setup Complete!",
                font=("Segoe UI", 13, "bold"),
                fg="#1b5e20",
                anchor="w",
            ).pack(fill="x")
            msg = (
                "Your FANS-C system is now running. FANS-C is ready to use, "
                "including face registration and verification.\n\n"
                "Open any browser on this PC and go to:\n"
                "    https://fans-barangay.local\n\n"
                "To give other staff devices access:\n"
                "    1. Copy the CLIENT-SETUP folder to a USB drive\n"
                "    2. Run trust-local-cert.bat on each staff device once\n"
                "    3. Staff open https://fans-barangay.local in their browser\n\n"
                f"Server IP: {lan_ip}\n\n"
                "The system starts automatically every time this PC is turned on."
            )
        else:
            # NOT a biometric-mismatch/verification-failure message -- no
            # comparison was ever attempted. The FaceNet model itself could
            # not initialize (most likely no internet on this Windows
            # account's first run), so face registration/verification are
            # disabled until it is resolved. Every non-biometric feature
            # (accounts, HTTPS, reports) is unaffected. No traceback or
            # internal error detail is shown here -- see logs\django-errors.log.
            self.root.title("FANS-C — Biometric Verification Not Ready")
            tk.Label(
                self._content,
                text="⚠  Setup Completed — Biometric Verification Not Ready",
                font=("Segoe UI", 13, "bold"),
                fg="#b45309",
                anchor="w",
                wraplength=530,
                justify="left",
            ).pack(fill="x")
            msg = (
                "Basic system setup completed. FANS-C is running, but the "
                "face-recognition (FaceNet) model could not initialize.\n\n"
                "Until this is resolved:\n"
                "    • Beneficiary face registration is disabled\n"
                "    • Face verification is disabled\n"
                "    • Accounts, HTTPS, and every other feature work normally\n\n"
                "To fix this:\n"
                "    1. Connect this computer to the internet\n"
                "    2. Restart FANS-C to retry the one-time model download\n"
                "    3. Contact your Technical Administrator if it continues to fail\n\n"
                "Open any browser on this PC and go to:\n"
                "    https://fans-barangay.local\n\n"
                f"Server IP: {lan_ip}\n\n"
                "The system starts automatically every time this PC is turned on."
            )
        tk.Label(
            self._content,
            text=msg,
            justify="left",
            anchor="nw",
            font=("Segoe UI", 10),
        ).pack(fill="x", pady=(10, 14))

        btn_row = tk.Frame(self._content)
        btn_row.pack()
        tk.Button(
            btn_row,
            text="Open Browser Now",
            command=lambda: webbrowser.open(FANS_URL),
            bg="#1a237e",
            fg="white",
            font=("Segoe UI", 10, "bold"),
            relief="flat",
            padx=14,
            pady=6,
        ).pack(side="left", padx=8)
        tk.Button(
            btn_row,
            text="Close",
            command=self.root.destroy,
            relief="flat",
            padx=14,
            pady=6,
        ).pack(side="left", padx=8)

    def show_success(self, lan_ip: str, facenet_ready: bool = True):
        self.root.after(0, lambda: self._show_success(lan_ip, facenet_ready))

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run_with(self, target):
        """Start setup thread and enter tkinter mainloop."""
        t = threading.Thread(target=target, daemon=True)
        t.start()
        self.root.mainloop()


# ---------------------------------------------------------------------------
# First-run setup steps
# ---------------------------------------------------------------------------

def _step1_gen_env(win: _SetupWindow, lan_ip: str):
    win.update(1, "Configuring email (optional)…")
    email_cfg = win.request_email_form()
    if email_cfg is None:
        # Whole setup was cancelled from the email step's close button.
        time.sleep(1)
        os._exit(0)

    win.update(1, "Generating encryption keys and writing .env...")
    try:
        from cryptography.fernet import Fernet
        secret_key    = secrets.token_urlsafe(50)
        embedding_key = Fernet.generate_key().decode()
        content = _ENV_TEMPLATE.format(
            secret_key=secret_key,
            embedding_key=embedding_key,
            lan_ip=lan_ip,
        )
        if email_cfg.get("enabled"):
            content += _EMAIL_ENV_TEMPLATE.format(
                host=email_cfg["host"],
                port=email_cfg["port"],
                user=email_cfg["user"],
                password=email_cfg["password"],
                from_email=email_cfg["from_email"],
            )
        ENV_FILE.write_text(content, encoding="utf-8")
    except Exception as exc:
        _fatal("Failed to generate security keys.", str(exc))


def _step2_database(win: _SetupWindow) -> bool:
    win.update(2, "Initializing Django…")
    try:
        _init_django()
    except Exception as exc:
        _fatal("Django failed to initialize.", str(exc))

    win.update(2, "Running database migrations…")
    try:
        _run_migrate()
    except Exception as exc:
        _fatal("Database migration failed.", str(exc))

    win.update(2, "Collecting static files…")
    try:
        _run_collectstatic()
    except Exception as exc:
        _fatal("collectstatic failed.", str(exc))

    return _check_facenet_ready(win)


def _check_facenet_ready(win: _SetupWindow) -> bool:
    """
    Verify the real FaceNet model loads before the wizard reaches the final
    "Setup complete" screen. NOT fatal — user accounts, HTTPS, and every
    non-biometric feature are unaffected — but this must never be silent:
    face_utils.py already fails closed (it never substitutes a random/mock
    model), so if this check fails, registration and verification will be
    blocked until it is resolved. The operator must be told that clearly
    now rather than discovering it later as an unexplained error.
    """
    win.update(2, "Checking face-recognition model availability…")
    try:
        from verification.face_utils import get_facenet_model
        get_facenet_model()
        return True
    except Exception as exc:
        logging.warning("FaceNet preflight failed during first-run setup: %s", exc)
        _warn(
            "The face-recognition (FaceNet) model is not ready yet.\n\n"
            "FANS-C needs a one-time internet connection, on this Windows "
            "account, to download the FaceNet model (~90 MB). Until that "
            "succeeds, beneficiary registration and face verification will "
            "be BLOCKED with a clear message — FANS-C never falls back to "
            "unreliable/random matching.\n\n"
            "You can continue with the rest of setup now (accounts, HTTPS, "
            "and every non-biometric feature work normally). Once this "
            "computer has internet access, restart FANS-C to retry the "
            "model download.\n\n"
            f"Detail: {exc}"
        )
        return False


def _step3_cert(win: _SetupWindow, lan_ip: str):
    win.update(3, "Installing local certificate authority into Windows trust store…")
    try:
        subprocess.run(
            [str(MKCERT_EXE), "-install"],
            cwd=_base_str,
            check=True,
            capture_output=True,
        )
    except Exception as exc:
        _fatal("mkcert -install failed.", str(exc))

    win.update(
        3,
        f"Generating HTTPS certificate for fans-barangay.local and {lan_ip}…",
    )
    try:
        subprocess.run(
            [
                str(MKCERT_EXE),
                "-cert-file", str(CERT_FILE),
                "-key-file",  str(KEY_FILE),
                "fans-barangay.local",
                lan_ip,
                "localhost",
                "127.0.0.1",
            ],
            cwd=_base_str,
            check=True,
            capture_output=True,
        )
    except Exception as exc:
        _fatal("Certificate generation failed.", str(exc))

    # Copy rootCA.pem to CLIENT-SETUP so it can be distributed via USB.
    # Uses the shared helper so the logic matches normal-start sync.
    _sync_rootca_to_client_setup()


def _step4_hosts(win: _SetupWindow, lan_ip: str):
    win.update(4, "Updating Windows hosts file for fans-barangay.local…")
    try:
        text = HOSTS_FILE.read_text(encoding="utf-8", errors="replace")
        additions = []
        if "127.0.0.1    fans-barangay.local" not in text:
            additions.append("127.0.0.1    fans-barangay.local")
        if lan_ip != "127.0.0.1":
            entry = f"{lan_ip}    fans-barangay.local"
            if entry not in text:
                additions.append(entry)
        if additions:
            with open(str(HOSTS_FILE), "a", encoding="utf-8") as f:
                f.write("\n# FANS-C Verification System\n")
                f.write("\n".join(additions) + "\n")
    except Exception as exc:
        _fatal("Failed to update the Windows hosts file.", str(exc))


def _step5_admin(win: _SetupWindow):
    win.update(5, "Please fill in the admin account form that has appeared…")
    result = win.request_admin_form()
    if result is None:
        # User cancelled — _cancel_setup() has already been called or is running.
        # Give it a moment to call os._exit; fall through to os._exit as safety.
        time.sleep(1)
        os._exit(0)


def _ps_quote(value: str) -> str:
    """Escape a value for embedding inside a single-quoted PowerShell literal."""
    return value.replace("'", "''")


def _register_scheduled_task(task_label: str, cmd: list) -> bool:
    """
    Run a scheduled-task registration command and surface failures instead of
    silently swallowing them.

    Phase 3A validation hit "ERROR: Access is denied." from schtasks and the
    old code (`subprocess.run(..., check=False)` inside a bare `except: pass`)
    let first-run setup carry on as if the task had been created. Returns
    True on success; on failure it logs the exact stdout/stderr to
    logs/django-errors.log and shows a warning dialog so the failure is never
    silent, without aborting the rest of first-run setup.
    """
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except Exception as exc:
        logging.error("Scheduled task registration failed to launch: %s -- %s", task_label, exc)
        _warn(
            f"Could not register the '{task_label}' scheduled task.\n\n{exc}\n\n"
            "This automatic task will not run until an administrator sets it "
            "up manually. See docs/SETUP.md."
        )
        return False
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip() or f"exit code {result.returncode}"
        logging.error("Scheduled task registration failed: %s -- %s", task_label, detail)
        _warn(
            f"Could not register the '{task_label}' scheduled task.\n\n"
            f"Windows Task Scheduler reported:\n{detail}\n\n"
            "This automatic task will not run until an administrator sets it "
            "up manually. See docs/SETUP.md."
        )
        return False
    return True


def _step6_autostart(win: _SetupWindow) -> bool:
    """
    Registers all three required scheduled tasks.

    Returns True only if EVERY required task registered successfully
    (Codex finding F-02). The caller (_run_setup_steps_6_and_7) must not
    proceed to starting services / declaring first-run setup successful when
    this returns False -- a first-run install must not silently claim a
    working automatic-startup/backup configuration when Task Scheduler
    rejected part of it.
    """
    win.update(6, "Registering Windows Task Scheduler autostart tasks…")
    fans_exe = (
        str(BASE_DIR / "fans_c.exe")
        if getattr(sys, "frozen", False)
        else sys.executable
    )

    # Main autostart: FANS-C starts at every system boot under SYSTEM account
    ok_main = _register_scheduled_task(
        "FANS-C Verification System",
        [
            "schtasks", "/Create",
            "/TN", "FANS-C Verification System",
            "/TR", fans_exe,
            "/SC", "ONSTART",
            "/RU", "SYSTEM",
            "/RL", "HIGHEST",
            "/F",
        ],
    )

    # Watchdog: checks every 60 seconds and restarts fans_c.exe if it is not running
    watchdog_cmd = (
        f"while($true){{"
        f"if(!(Get-Process -Name fans_c -ErrorAction SilentlyContinue))"
        f"{{Start-Process \\\"{fans_exe}\\\" -WindowStyle Hidden}};"
        f"Start-Sleep 60}}"
    )
    ok_watchdog = _register_scheduled_task(
        "FANS-C Watchdog",
        [
            "schtasks", "/Create",
            "/TN", "FANS-C Watchdog",
            "/TR", (
                f"powershell.exe -NonInteractive -WindowStyle Hidden "
                f"-Command \"{watchdog_cmd}\""
            ),
            "/SC", "ONSTART",
            "/DELAY", "0002:30",
            "/RU", "SYSTEM",
            "/RL", "HIGHEST",
            "/F",
        ],
    )

    # Daily backup: hot-backup db.sqlite3 / .env / media\ at 21:00 every day.
    # Registered via Register-ScheduledTask (not raw schtasks) so it gets the
    # same StartWhenAvailable / execution-time-limit / battery settings as the
    # IT-run scripts\setup\setup-autostart.ps1 path — schtasks.exe's simple
    # /Create flags cannot express those settings without an XML definition.
    #
    # Codex finding F-05: no pre-unregister step. Register-ScheduledTask
    # -Force replaces an existing task's definition directly; if the new
    # definition is somehow rejected, the previous known-good task is left
    # in place rather than being deleted first and then never replaced.
    backup_script = str(BASE_DIR / "scripts" / "admin" / "daily-backup.ps1")
    backup_ps_command = (
        "$action = New-ScheduledTaskAction -Execute 'powershell.exe' "
        "-Argument '-WindowStyle Hidden -ExecutionPolicy Bypass -NonInteractive -File \""
        + _ps_quote(backup_script) + "\"' "
        "-WorkingDirectory '" + _ps_quote(str(BASE_DIR)) + "'; "
        "$trigger = New-ScheduledTaskTrigger -Daily -At '21:00'; "
        "$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 30) "
        "-StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries; "
        "$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest; "
        "Register-ScheduledTask -TaskName 'FANS-C Daily Backup' -Action $action -Trigger $trigger "
        "-Settings $settings -Principal $principal "
        "-Description 'Daily hot-backup of db.sqlite3, .env, and media\\ after office hours. "
        "Keeps last 14 completed backups. Log: logs\\fans-backup.log' -Force | Out-Null"
    )
    ok_backup = _register_scheduled_task(
        "FANS-C Daily Backup",
        [
            "powershell.exe", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-Command", backup_ps_command,
        ],
    )

    return ok_main and ok_watchdog and ok_backup


def _run_setup_steps_6_and_7(win: _SetupWindow):
    """
    Registers scheduled tasks (step 6) and, only if ALL required tasks
    registered successfully, starts Waitress/Caddy (step 7).

    Split out of _first_run's inner _run() closure specifically so this
    failure-propagation decision (Codex finding F-02) can be unit tested
    without invoking the full first-run wizard. Uses _fatal -- the existing
    FANS-C "show an error dialog and terminate" mechanism -- rather than a
    new error-handling framework: a first-run install must not reach the
    final success screen when required Task Scheduler setup failed.
    """
    if not _step6_autostart(win):
        _fatal(
            "Required Windows Task Scheduler setup failed.",
            "One or more required scheduled tasks (FANS-C Verification System, "
            "FANS-C Watchdog, FANS-C Daily Backup) could not be registered. "
            "FANS-C cannot guarantee automatic startup or backups until this "
            "is fixed. See the warning dialog(s) shown for details, then "
            "contact your Technical Administrator and re-run setup."
        )
        return
    _step7_start_services(win)


def _step7_start_services(win: _SetupWindow):
    # Snapshot the runtime environment to logs/diagnostic.log BEFORE Waitress
    # starts so any subsequent crash leaves a readable file behind.
    _write_diagnostic_report()
    win.update(7, "Starting Django web server (Waitress)…")
    _kill_stale_fans_c()
    _start_waitress()

    win.update(
        7,
        "Waiting for Django to load (30–60 seconds on first run; "
        "TensorFlow is large)…",
    )
    if not _wait_port(WAITRESS_PORT, timeout=90):
        _fatal(
            "The web server did not start within 90 seconds.",
            "TensorFlow or Django may have failed to load. "
            "Try running FANS-C again, or contact your Technical Administrator.",
        )

    win.update(7, "Starting HTTPS reverse proxy (Caddy)…")
    _start_caddy()

    if not _wait_port(CADDY_PORT, timeout=30):
        _warn(
            "The HTTPS server (Caddy) did not respond within 30 seconds.\n\n"
            "The system may be accessible at http://localhost:8000 as a fallback.\n"
            "Contact IT if https://fans-barangay.local is not reachable."
        )


# ---------------------------------------------------------------------------
# First-run orchestrator
# ---------------------------------------------------------------------------

def _first_run():
    win = _SetupWindow()

    def _run():
        try:
            lan_ip = _get_lan_ip()

            _step1_gen_env(win, lan_ip)
            facenet_ready = _step2_database(win)
            _step3_cert(win, lan_ip)
            _step4_hosts(win, lan_ip)
            _step5_admin(win)
            _run_setup_steps_6_and_7(win)

            win.update(8, "All done! Opening browser…")
            win.show_success(lan_ip, facenet_ready)
            webbrowser.open(FANS_URL)

        except SystemExit:
            os._exit(1)
        except Exception as exc:
            _fatal("An unexpected error occurred during setup.", str(exc))

    win.run_with(_run)
    _keep_alive()


# ---------------------------------------------------------------------------
# Normal-startup window (brief status shown on subsequent runs)
# ---------------------------------------------------------------------------

class _StartingWindow:
    """Small status window shown while services boot on a daily launch."""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("FANS-C")
        self.root.geometry("380x130")
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)
        self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)

        ico = BUNDLE_DIR / "assets" / "logo.ico"
        if ico.exists():
            try:
                self.root.iconbitmap(str(ico))
            except Exception:
                pass

        hdr = tk.Frame(self.root, bg=BRAND_NAVY, height=44)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(
            hdr,
            text="  FANS-C Verification System",
            font=(BRAND_FONT, 10, "bold"),
            fg="white",
            bg=BRAND_NAVY,
        ).pack(side="left", pady=10)
        tk.Frame(self.root, bg=BRAND_GOLD, height=3).pack(fill="x")

        self._msg = tk.StringVar(value="Starting…")
        tk.Label(
            self.root, textvariable=self._msg, font=(BRAND_FONT, 10), pady=12
        ).pack()

        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "FansC.Horizontal.TProgressbar",
            troughcolor="#dbe3f0", background=BRAND_GOLD,
            bordercolor="#dbe3f0", lightcolor=BRAND_GOLD, darkcolor=BRAND_GOLD,
        )
        self._bar = ttk.Progressbar(
            self.root, mode="indeterminate", length=340,
            style="FansC.Horizontal.TProgressbar",
        )
        self._bar.pack(padx=20)
        self._bar.start(10)

    def update(self, msg: str):
        self.root.after(0, lambda: self._msg.set(msg))

    def close(self):
        self.root.after(0, self.root.destroy)

    def run_with(self, target):
        t = threading.Thread(target=target, daemon=True)
        t.start()
        self.root.mainloop()


# ---------------------------------------------------------------------------
# Normal startup orchestrator
# ---------------------------------------------------------------------------

def _normal_start():
    win = _StartingWindow()

    def _run():
        try:
            win.update("Loading configuration…")
            # Ensure upgrade-added .env keys (SECURE_PROXY_SSL_HEADER etc.) are
            # present even if the .env was written by an older installer.
            _repair_env()
            try:
                _init_django()
            except Exception as exc:
                _fatal("Failed to load FANS-C configuration.", str(exc))

            # Refresh diagnostic.log every normal launch so we always have a
            # current snapshot of paths / env when the LAN reports problems.
            _write_diagnostic_report()

            # Sync rootCA.pem to CLIENT-SETUP on every launch so that
            # upgrade installs (which skip first-run) still populate CLIENT-SETUP.
            _sync_rootca_to_client_setup()

            # Re-add hosts entry if it went missing (e.g. after a manual edit,
            # OS update that reset hosts, or reinstall without clean removal).
            lan_ip = _get_lan_ip()
            _ensure_hosts_entry(lan_ip)

            # Verify HTTPS cert files exist before attempting to start Caddy.
            # Missing certs are the most common cause of ERR_CONNECTION_REFUSED
            # after an upgrade install that preserved .env but not cert files.
            if not _check_certs_exist():
                _fatal(
                    "HTTPS certificate files are missing.\n\n"
                    "fans-cert.pem and fans-cert-key.pem were not found "
                    "in the install folder.\n\n"
                    "This usually happens when:\n"
                    "  • A previous installation’s certificates were deleted\n"
                    "  • The install folder was partially cleared manually\n\n"
                    "Fix:\n"
                    "  Delete the file  .env  from the install folder\n"
                    "  (C:\\FANSC\\.env) and launch FANS-C again.\n"
                    "  The setup wizard will regenerate all certificates.",
                )

            win.update("Starting web server…")
            # Kill any other fans_c.exe instances before we bind port 8000.
            # A stale instance left by a reinstall race would otherwise hold
            # the port, preventing Waitress from starting.
            _kill_stale_fans_c()
            _start_waitress()

            win.update("Starting HTTPS proxy…")
            # Kill any stale caddy.exe (left over from a previous run or a
            # reinstall that didn't cleanly terminate the old process).
            _kill_caddy()
            _start_caddy()

            win.update("Waiting for services (30–60 seconds)…")
            if not _wait_port(WAITRESS_PORT, timeout=90):
                _fatal(
                    "The web server did not start within 90 seconds.",
                    "Contact your Technical Administrator.",
                )

            _wait_port(CADDY_PORT, timeout=30)

            win.update("Opening browser…")
            time.sleep(0.5)
            webbrowser.open(FANS_URL)
            time.sleep(1)
            win.close()

        except SystemExit:
            os._exit(1)
        except Exception as exc:
            _fatal("FANS-C failed to start.", str(exc))

    win.run_with(_run)
    _keep_alive()


# ---------------------------------------------------------------------------
# Keep process alive while daemon threads and Caddy subprocess run
# ---------------------------------------------------------------------------

def _keep_alive():
    try:
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    if not ENV_FILE.exists():
        _first_run()
    else:
        _normal_start()


if __name__ == "__main__":
    main()
