# fans_c.spec  --  PyInstaller Build Specification for FANS-C
# =========================================================
#
# PURPOSE
# -------
# This file tells PyInstaller exactly how to package the FANS-C Django
# application into a self-contained Windows directory (onedir build).
# The output is dist/FANS-C/  --  a folder containing FANS-C.exe and all
# required libraries, DLLs, and data files.
#
# WHY ONEDIR (not onefile)?
# -------------------------
# TensorFlow, keras-facenet, and OpenCV collectively contain hundreds of
# shared libraries (DLLs) and large binary data files (>1 GB total).
# A onefile build would extract all of these into a temporary directory
# on every launch, which:
#   * Takes 30-120 seconds per startup on average hardware
#   * Writes gigabytes to the user's %TEMP% on every launch
#   * Is blocked by some antivirus tools that watch %TEMP% for DLL drops
#
# A onedir build extracts everything once at install time.  Launch is fast
# (2-5 seconds for Django + TF initialisation) and AV tools see stable
# file paths, not temp extractions.
#
# HOW IT WORKS
# ------------
# PyInstaller statically analyses launcher.py and its imports, then bundles:
#   * All imported Python modules (pure .py and compiled .pyd/.pyo)
#   * All discovered DLLs and shared libraries
#   * Explicitly listed data files (templates, static/, staticfiles/, etc.)
#   * Data files collected from tensorflow, keras, cv2, and mtcnn via
#     collect_all() / collect_data_files() hooks
#
# The Analysis object is the core configuration.  PYZ compresses pure Python
# modules.  EXE produces the launcher executable.  COLLECT assembles
# everything into the final dist/FANS-C/ directory.
#
# IMPORTANT LIMITATIONS
# ---------------------
# * keras-facenet downloads its model weights (~90 MB, file
#   20180402-114759-weights.h5) to <install folder>\models\keras-facenet\ on
#   the FIRST IMPORT after packaging -- a machine-local cache next to
#   fans_c.exe (see verification/face_utils.py get_facenet_cache_dir()),
#   NOT %USERPROFILE%\.keras-facenet\ or ~/.keras/ (an unrelated Keras
#   config directory). Because the cache path is derived from the install
#   directory rather than any Windows account's profile, both an elevated
#   interactive first run and the SYSTEM-account autostart/watchdog process
#   share the same cache -- no per-account re-download. The target machine
#   still needs internet access once for the initial download. This is NOT
#   bundled by this spec (no collect_data_files/collect_all for
#   keras_facenet) -- see SETUP.md, section "ML Model Notes".
# * .env is NOT bundled  --  it contains secrets.  The user must create .env
#   in the dist/FANS-C/ folder before running the application.
# * db.sqlite3 is NOT bundled  --  it is created fresh by `migrate` on first
#   launch.  Do NOT include a development database in a distributed build.
# * If the build fails with "cannot find tensorflow", make sure you are
#   running PyInstaller from the project's .venv where TF is installed.
#
# USAGE
# -----
#   # From the project root, inside .venv:
#   pyinstaller fans_c.spec
#
#   # Or use the build script:
#   .\build_exe.ps1

import os
import sys

# ---------------------------------------------------------------------------
# PyInstaller import helpers
# ---------------------------------------------------------------------------
# These functions walk installed packages and return (src, dest) tuples of
# all data files and DLLs that PyInstaller would otherwise miss.
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, collect_all

block_cipher = None

# The spec file lives in dev/, one level below the project root
project_root = os.path.abspath(os.path.join(SPECPATH, '..'))   # SPECPATH is set by PyInstaller

# ---------------------------------------------------------------------------
# Collect data and binaries from heavyweight ML packages
# ---------------------------------------------------------------------------
# collect_all() returns (datas, binaries, hiddenimports) for a package.
# This is the safest way to include TensorFlow and OpenCV, both of which
# have many resource files (proto definitions, DLLs, etc.) that a plain
# Analysis would miss.
#
# Note: collect_all('tensorflow') can be slow (several minutes) on the first
# run because PyInstaller must walk thousands of TF files.  Subsequent runs
# use the build cache and are much faster.

print('[spec] Collecting tensorflow data files (this may take several minutes)...')
tf_datas, tf_binaries, tf_hiddenimports = collect_all('tensorflow')

# Drop tensorflow/include/** -- these are C++ build-time headers (not loaded at
# runtime). Their deeply-nested paths blow past Windows MAX_PATH (260 chars)
# and cause Inno Setup compression to abort with "system cannot find the path
# specified". Removing them shrinks the bundle and unblocks the installer build.
_tf_before = len(tf_datas)
tf_datas = [
    (src, dst) for (src, dst) in tf_datas
    if not dst.replace('\\', '/').startswith('tensorflow/include')
]
print('[spec] Excluded {} tensorflow/include/** entries from datas'.format(_tf_before - len(tf_datas)))

print('[spec] Collecting keras data files...')
keras_datas, keras_binaries, keras_hiddenimports = collect_all('keras')

print('[spec] Collecting cv2 (OpenCV) data files...')
cv2_datas, cv2_binaries, cv2_hiddenimports = collect_all('cv2')

# mtcnn includes cascade XML files that must travel with the package
mtcnn_datas = collect_data_files('mtcnn')

# ---------------------------------------------------------------------------
# Project data files
# ---------------------------------------------------------------------------
# Each entry is (source_path, dest_folder_inside_bundle).
# Destination '.' means "root of the bundle" (alongside FANS-C.exe).
#
# What is included and why:
#   fans/            --  Django project package (settings, urls, wsgi)
#   accounts/        --  user auth app
#   beneficiaries/   --  core beneficiary management app
#   verification/    --  FaceNet verification app
#   logs/            --  audit log app
#   templates/       --  Django HTML templates (APP_DIRS won't find them in bundle)
#   static/          --  source static assets (CSS/JS/images)
#   staticfiles/     --  collected/hashed static files served by whitenoise
#   manage.py        --  required by some Django management utilities
#   .env.example     --  shipped as a reference; user must create .env from it
#
# What is NOT included and why:
#   .env             --  contains secrets (encryption key, secret key); must be
#                     created by the user on the target machine
#   db.sqlite3       --  development data; created fresh by migrate on first launch
#   .venv/           --  the virtual environment itself is NOT needed; PyInstaller
#                     extracts only the files actually used

project_datas = [
    # Django project package
    (os.path.join(project_root, 'fans'),          'fans'),
    # Django application packages
    (os.path.join(project_root, 'accounts'),      'accounts'),
    (os.path.join(project_root, 'beneficiaries'), 'beneficiaries'),
    (os.path.join(project_root, 'verification'),  'verification'),
    (os.path.join(project_root, 'logs'),          'logs'),
    # Templates (Django's loader needs these at runtime)
    (os.path.join(project_root, 'templates'),     'templates'),
    # Static files (source  --  referenced during development / fallback)
    (os.path.join(project_root, 'static'),        'static'),
    # Collected/hashed static files  --  required by whitenoise in production
    # (DEBUG=False, CompressedManifestStaticFilesStorage).
    # Run `python manage.py collectstatic --noinput` before packaging.
    (os.path.join(project_root, 'staticfiles'),   'staticfiles'),
    # manage.py at the bundle root (used internally by some management calls)
    (os.path.join(project_root, 'manage.py'),     '.'),
    # Caddy configuration -- required at root so Caddy finds it with --config Caddyfile
    (os.path.join(project_root, 'Caddyfile'),     '.'),
    # Bundled tools: Caddy exe + mkcert exe (no separate download needed)
    (os.path.join(project_root, 'tools'),         'tools'),
    # CLIENT-SETUP folder distributed to staff devices via USB
    (os.path.join(project_root, 'CLIENT-SETUP'),  'CLIENT-SETUP'),
    # App icon used by launcher.py tkinter windows
    (os.path.join(project_root, 'assets'),        'assets'),
    # .env template for first-time setup on the target machine
    (os.path.join(project_root, '.env.example'),  '.'),
]

# media/ must NEVER be bundled -- it contains real beneficiary photos uploaded
# by staff during operation.  It is created at runtime in the writable install
# location (e.g. C:\FANSC\media\).  Bundling it would ship private user data
# in every installer and pre-populate all installs with one machine's photos.
_media_dir = os.path.join(project_root, 'media')
if os.path.isdir(_media_dir) and any(os.scandir(_media_dir)):
    print('[spec] WARNING: media/ is non-empty but will NOT be bundled.')
    print('[spec]          It contains real user data and must never be shipped.')
    print('[spec]          Clean your dev media/ before publishing a release.')

# ---------------------------------------------------------------------------
# Hidden imports
# ---------------------------------------------------------------------------
# PyInstaller's static analysis cannot always detect dynamically imported
# modules (e.g. Django's template engine loads backends by string name,
# TF uses plugin registries).  List them explicitly here.

hidden_imports = [
    # -- Django internals ----------------------------------------------------
    # Django discovers apps, auth backends, and template loaders at runtime
    # via string-based imports that static analysis cannot follow.
    'django.contrib.admin',
    'django.contrib.admin.apps',
    'django.contrib.auth',
    'django.contrib.auth.backends',
    'django.contrib.auth.hashers',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.sessions.backends.db',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.staticfiles.finders',
    'django.template.backends.django',
    'django.template.loaders.app_directories',
    'django.template.loaders.filesystem',
    'django.template.context_processors',
    'django.db.backends.sqlite3',
    'django.db.backends.sqlite3.base',

    # -- FANS-C apps ---------------------------------------------------------
    'fans',
    'fans.settings',
    'fans.urls',
    'fans.wsgi',
    'fans.context_processors',
    'fans.middleware',
    'fans.views',
    'accounts',
    'accounts.models',
    'accounts.views',
    'accounts.urls',
    'beneficiaries',
    'beneficiaries.models',
    'beneficiaries.views',
    'beneficiaries.urls',
    'beneficiaries.sync',
    'beneficiaries.forms',
    'beneficiaries.qc_barangays',
    'verification',
    'verification.models',
    'verification.views',
    'verification.urls',
    'verification.face_utils',
    'verification.liveness',
    'logs',
    'logs.models',
    'logs.views',
    'logs.urls',
    'logs.templatetags',
    'logs.templatetags.fans_filters',

    # -- Third-party Django packages -----------------------------------------
    'whitenoise',
    'whitenoise.middleware',
    'whitenoise.storage',
    'dotenv',

    # -- Reporting / Excel export --------------------------------------------
    'openpyxl',
    'openpyxl.workbook',
    'openpyxl.styles',

    # -- Optional PostgreSQL driver (listed in requirements; only used when
    #    USE_SQLITE=False, but include so a future deployment can switch DBs
    #    without rebuilding the bundle) ---------------------------------------
    'psycopg2',

    # -- WSGI server (packaged build) -----------------------------------------
    'waitress',
    'waitress.server',
    'waitress.task',
    'waitress.channel',

    # -- Crypto --------------------------------------------------------------
    'cryptography',
    'cryptography.fernet',
    'cryptography.hazmat',
    'cryptography.hazmat.primitives',

    # -- Image processing ----------------------------------------------------
    'PIL',
    'PIL.Image',
    'cv2',

    # -- Numerical / ML ------------------------------------------------------
    'numpy',
    'scipy',
    'scipy.spatial',
    'scipy.spatial.distance',
    'scipy.io',
    'scipy.linalg',
    'scipy.stats',
    'sklearn',
    'sklearn.utils',
    'sklearn.utils._cython_blas',

    # -- Face recognition ----------------------------------------------------
    'keras_facenet',
    'mtcnn',
    'mtcnn.mtcnn',
    'tensorflow',

    # -- Standard library items used dynamically ------------------------------
    'tkinter',
    'tkinter.messagebox',
    'webbrowser',
    'threading',
    'socket',
]

# Merge auto-collected hidden imports from collect_all()
all_hidden_imports = hidden_imports + tf_hiddenimports + keras_hiddenimports + cv2_hiddenimports

# ---------------------------------------------------------------------------
# Analysis  --  the main PyInstaller configuration object
# ---------------------------------------------------------------------------

a = Analysis(
    # Entry point: the launcher script (lives in dev/ alongside this spec file)
    [os.path.join(project_root, 'dev', 'launcher.py')],

    # pathex: additional directories to search for modules.
    # The project root ensures that `import fans`, `import accounts`, etc.
    # resolve correctly during the analysis phase.
    pathex=[project_root],

    # binaries: (src, dest) pairs for shared libraries / DLLs.
    # We rely on collect_all() to find TF, keras, and cv2 binaries.
    binaries=tf_binaries + keras_binaries + cv2_binaries,

    # datas: (src, dest) pairs for non-Python resource files.
    datas=project_datas + tf_datas + keras_datas + cv2_datas + mtcnn_datas,

    # hiddenimports: modules that static analysis will miss.
    hiddenimports=all_hidden_imports,

    # hookspath: custom hook directories (not needed here  --  we handle
    # everything explicitly above, but keep as an empty list for clarity).
    hookspath=[],

    # hooksconfig: pass extra options to built-in hooks.
    hooksconfig={},

    # runtime_hooks: scripts that run before any user code in the bundle.
    runtime_hooks=[],

    # excludes: packages to intentionally omit to reduce build size.
    # These are development/test tools not needed at runtime.
    excludes=[
        'IPython',
        'ipykernel',
        'jupyter',
        'notebook',
        'pytest',
        'pytest_cov',
        'coverage',
        'matplotlib',
        'pandas',
        'setuptools',
        'pip',
        'docutils',
        'sphinx',
        'black',
        'flake8',
        'mypy',
    ],

    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# ---------------------------------------------------------------------------
# PYZ  --  compressed pure-Python archive
# ---------------------------------------------------------------------------
# Pure Python modules are stored in a compressed archive inside the exe.
# Binary extensions (.pyd) cannot be compressed and travel as separate files.

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ---------------------------------------------------------------------------
# EXE  --  the launcher executable
# ---------------------------------------------------------------------------
# icon: set to fans_c.ico if it exists in the project root.
# To use a custom icon:
#   1. Export a 256x256 PNG of your logo.
#   2. Convert to ICO with IcoFX or https://cloudconvert.com/png-to-ico
#   3. Save as fans_c.ico in the project root.
#   4. Rebuild with: pyinstaller fans_c.spec

_ico = os.path.join(project_root, 'assets', 'logo.ico')
_icon_arg = _ico if os.path.isfile(_ico) else None

# Windows VersionInfo resource (FileVersion/ProductVersion) -- see
# dev/version_info.txt. Keep its filevers/prodvers and FileVersion/
# ProductVersion StringStructs in sync with the release version whenever it
# changes, alongside dev/installer/fans_c.iss's VersionInfoVersion.
_version_file = os.path.join(project_root, 'dev', 'version_info.txt')
_version_arg = _version_file if os.path.isfile(_version_file) else None

exe = EXE(
    pyz,
    a.scripts,
    [],                         # no embedded binaries  --  they go into COLLECT
    exclude_binaries=True,      # binaries are handled by COLLECT (onedir)
    name='fans_c',              # produces fans_c.exe; Inno Setup references dist\fans_c\
    debug=False,                # set True temporarily to debug import errors
    bootloader_ignore_signals=False,
    strip=False,                # stripping debug symbols can break TF on Windows
    upx=True,                   # compress the exe with UPX if available

    # console=False: no black terminal window for end users.
    # All status and errors are shown via tkinter dialogs (see launcher.py).
    console=False,

    icon=_icon_arg,
    version=_version_arg,
)

# ---------------------------------------------------------------------------
# COLLECT  --  assembles the final dist/FANS-C/ directory
# ---------------------------------------------------------------------------
# COLLECT gathers the EXE, all binaries (DLLs), and all data files into
# one directory.  The result is dist/FANS-C/ which is what the Inno Setup
# installer packages and distributes.

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[
        # UPX should not compress TensorFlow DLLs  --  it often corrupts them
        '_tensorflow*',
        'libtensorflow*',
        'tensorflow*.dll',
        '_pywrap*.pyd',
    ],
    name='fans_c',              # output directory name: dist/fans_c/
)

# ---------------------------------------------------------------------------
# Post-COLLECT cleanup -- safety net for tensorflow/include
# ---------------------------------------------------------------------------
# Even with the tf_datas filter above, hook files shipped with PyInstaller or
# transitive collect_all() calls can re-introduce tensorflow/include. Delete
# the folder unconditionally after COLLECT so the Inno Setup compressor never
# sees those long paths.
import shutil
_tf_include = os.path.join(DISTPATH, 'fans_c', '_internal', 'tensorflow', 'include')
if os.path.isdir(_tf_include):
    print('[spec] Removing {} (MAX_PATH safety) ...'.format(_tf_include))
    shutil.rmtree(_tf_include, ignore_errors=True)
