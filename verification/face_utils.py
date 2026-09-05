"""
Face processing utilities — FANS-C.

Covers: face detection + alignment, FaceNet embedding generation, embedding
encryption/decryption, multi-template comparison, and duplicate/lookalike detection.

Key design decisions
─────────────────────
Face detection hierarchy
  1. RetinaFace  — highest accuracy, deeplearning-based; used when available.
  2. MTCNN       — reliable CNN detector; fallback if RetinaFace is not installed.
  3. OpenCV Haar cascade — lightweight, CPU-only; last resort for resource-constrained
     machines or when the other detectors fail.

Alignment (_align_face_similarity)
  A 4-DOF similarity transform (rotation + scale + translation) maps the detected eye
  landmarks to canonical positions in a 160×160 output crop. This is the standard
  MTCNN/FaceNet preprocessing step and ensures consistent crops regardless of face
  distance, head tilt, or camera resolution. Both registration and verification use the
  same transform so embeddings are directly comparable.

CLAHE preprocessing
  Applied to the L channel in LAB colour space before the FaceNet embedding step.
  This lifts shadows and improves contrast for low-light barangay offices, dark skin
  tones, and cameras with poor auto-exposure, without distorting hue or saturation.
  clipLimit=2.0 is conservative — visible improvement without over-sharpening artifacts.

FaceNet embedding (keras-facenet)
  512-d L2-normalised vectors (default checkpoint 20180402-114759 — see get_embedding()
  below for how this was verified against the installed package; do not assume 128-d).
  Cosine similarity = dot product of two unit vectors, range [−1, 1]; in practice
  well-aligned frontal faces score 0.6–0.95 for the same person. Multi-template matching
  (compare_with_all_embeddings) returns the best score across all stored templates
  (primary + additional). This compensates for appearance changes in older beneficiaries
  since initial registration.

Embedding encryption
  Each embedding is JSON-serialised, then encrypted with Fernet (AES-128-CBC + HMAC-SHA256).
  The EMBEDDING_ENCRYPTION_KEY must be the same on all devices that share a database.
  See .env.example for key sharing instructions in multi-device deployments.

Lookalike/twin detection (check_duplicate_face)
  Used during registration to flag possible duplicates and during verification to
  escalate near-matches to manual review (LOOKALIKE_BAND=0.05 by default).
"""
import io
import logging
import os
import numpy as np
import base64
import json
from pathlib import Path
from django.conf import settings

_logger = logging.getLogger('verification')


# ─── Encryption ──────────────────────────────────────────────────────────────

def get_fernet():
    from cryptography.fernet import Fernet
    key = settings.EMBEDDING_ENCRYPTION_KEY
    if not key:
        # Fail loudly. A silent ephemeral key would make every restart produce
        # a new key, instantly making every previously-stored face embedding
        # unreadable without any visible error to the operator.
        raise RuntimeError(
            'EMBEDDING_ENCRYPTION_KEY is not set in the environment. '
            'Run `python manage.py generate_key` and paste the result into .env. '
            'Refusing to encrypt or decrypt face data without a stable key.'
        )
    if isinstance(key, str):
        key = key.encode()
    try:
        return Fernet(key)
    except Exception as exc:
        raise RuntimeError(
            'EMBEDDING_ENCRYPTION_KEY is set but is not a valid Fernet key. '
            'Regenerate with `python manage.py generate_key` and update .env. '
            f'(underlying error: {type(exc).__name__})'
        ) from exc


def encrypt_embedding(embedding: np.ndarray) -> bytes:
    fernet = get_fernet()
    embedding_json = json.dumps(embedding.tolist())
    return fernet.encrypt(embedding_json.encode())


def decrypt_embedding(encrypted_data: bytes) -> np.ndarray:
    fernet = get_fernet()
    if isinstance(encrypted_data, memoryview):
        encrypted_data = bytes(encrypted_data)
    decrypted = fernet.decrypt(encrypted_data)
    return np.array(json.loads(decrypted.decode()), dtype=np.float32)


# ─── Image Loading ────────────────────────────────────────────────────────────

def load_image_from_bytes(image_bytes: bytes) -> np.ndarray:
    import cv2
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError('Failed to decode image.')
    return img


def load_image_from_base64(b64_string: str) -> np.ndarray:
    if ',' in b64_string:
        b64_string = b64_string.split(',')[1]
    image_bytes = base64.b64decode(b64_string)
    return load_image_from_bytes(image_bytes)


# ─── CLAHE Preprocessing ─────────────────────────────────────────────────────

def _apply_clahe(img: np.ndarray) -> np.ndarray:
    """
    Apply CLAHE (Contrast Limited Adaptive Histogram Equalization) to improve
    contrast in the face image before FaceNet embedding.

    Operates on the L channel of LAB colour space to avoid distorting hue/saturation.
    This substantially improves recognition accuracy for:
      - Low-light barangay office environments
      - Webcams with poor auto-exposure
      - Users with darker skin tones

    clipLimit=2.0 is conservative — enough to lift shadows without over-sharpening.
    """
    import cv2
    try:
        lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
        l_ch, a_ch, b_ch = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        l_enhanced = clahe.apply(l_ch)
        lab_enhanced = cv2.merge([l_enhanced, a_ch, b_ch])
        return cv2.cvtColor(lab_enhanced, cv2.COLOR_LAB2BGR)
    except Exception:
        return img  # fallback: return original if CLAHE fails


# ─── Face Detection (RetinaFace / MTCNN / OpenCV cascade) ────────────────────

def _check_face_within_frame(img_shape, x1, y1, x2, y2, margin_px: int = 3) -> None:
    """
    Raise ValueError if the detected face bounding box touches/exceeds the
    edge of the captured image — meaning part of the face was outside the
    camera's field of view when the detector ran (not merely small or
    off-centre, which are handled separately by the eye-distance and
    alignment steps). Checked BEFORE any padding/clamping is applied to the
    box, since clamping would otherwise hide the fact that the raw detection
    was cut off.
    """
    h, w = img_shape[:2]
    if x1 <= margin_px or y1 <= margin_px or x2 >= w - margin_px or y2 >= h - margin_px:
        raise ValueError(
            'Face is partially outside the camera frame. '
            'Please position your entire face inside the camera frame.'
        )


_mtcnn_detector = None  # cached MTCNN instance (lazy init, thread-safe enough for Django dev)


def _get_mtcnn():
    global _mtcnn_detector
    if _mtcnn_detector is None:
        from mtcnn import MTCNN
        _mtcnn_detector = MTCNN()
    return _mtcnn_detector


def _detect_face_mtcnn(img: np.ndarray) -> np.ndarray:
    """
    Detect face with MTCNN and return an aligned 160x160 crop.

    MTCNN provides 5-point landmarks (left_eye, right_eye, nose, mouth_left,
    mouth_right).  We feed the eye positions into _align_face_similarity() for
    the same 4-DOF similarity transform used by the RetinaFace path, giving
    rotation- and scale-normalised crops that are directly comparable across
    registration and verification sessions.

    Falls back to the OpenCV Haar cascade if MTCNN fails or is not installed.
    """
    import cv2
    try:
        detector = _get_mtcnn()
        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        detections = detector.detect_faces(img_rgb)
    except Exception:
        return _detect_face_opencv(img)

    if not detections:
        raise ValueError(
            'No face detected. Center your face in the frame and ensure good lighting.'
        )

    # Best detection by confidence
    best = max(detections, key=lambda d: d.get('confidence', 0))
    if best.get('confidence', 0) < 0.85:
        raise ValueError(
            f"Face detection confidence too low ({best.get('confidence', 0):.2f}). "
            'Move closer to the camera or improve lighting.'
        )

    bx, by, bw, bh = best['box']
    _check_face_within_frame(img.shape, bx, by, bx + bw, by + bh)

    kp = best.get('keypoints', {})
    if 'left_eye' in kp and 'right_eye' in kp:
        left_eye  = np.array(kp['left_eye'],  dtype=np.float32)
        right_eye = np.array(kp['right_eye'], dtype=np.float32)

        inter_eye_dist = float(np.linalg.norm(right_eye - left_eye))
        if inter_eye_dist < 15:
            raise ValueError(
                f'Face too small (eye distance {inter_eye_dist:.0f}px). '
                'Move closer to the camera.'
            )

        return _align_face_similarity(img, left_eye, right_eye, output_size=(160, 160))

    # Fallback: bounding box only (no landmarks)
    x, y, w, h = best['box']
    x, y = max(0, x), max(0, y)
    x2, y2 = min(img.shape[1], x + w), min(img.shape[0], y + h)
    face_crop = img[y:y2, x:x2]
    if face_crop.size == 0:
        raise ValueError('Face bounding box is empty.')
    return cv2.resize(face_crop, (160, 160))


def detect_pose_keypoints(img: np.ndarray) -> dict | None:
    """
    Server-side-only face keypoint detection for liveness pose estimation
    (v2.1.16 Security Hardening Round #4 — Blocker 1).

    Runs the SAME face detector already used for face matching (RetinaFace,
    falling back to MTCNN) directly on raw image pixels the server decoded
    from the request body — never on any client-supplied coordinate data.
    Returns {'left_eye': (x, y), 'right_eye': (x, y), 'nose': (x, y)} in
    image pixel coordinates from the highest-confidence detected face, or
    None if no face (or no usable keypoints) could be detected.

    Deliberately does not raise — a liveness pose check should fail closed
    (no movement evidence) rather than propagate a detector exception,
    mirroring the fail-closed contract of the code that consumes this.
    """
    import cv2

    try:
        from retinaface import RetinaFace
        faces = RetinaFace.detect_faces(img)
        if faces and not isinstance(faces, tuple):
            best_key = max(faces, key=lambda k: faces[k].get('score', 0))
            landmarks = faces[best_key].get('landmarks', {}) or {}
            if 'left_eye' in landmarks and 'right_eye' in landmarks and 'nose' in landmarks:
                return {
                    'left_eye': tuple(float(v) for v in landmarks['left_eye']),
                    'right_eye': tuple(float(v) for v in landmarks['right_eye']),
                    'nose': tuple(float(v) for v in landmarks['nose']),
                }
            return None
    except ImportError:
        pass
    except Exception:
        pass

    try:
        detector = _get_mtcnn()
        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        detections = detector.detect_faces(img_rgb)
    except Exception:
        return None

    if not detections:
        return None

    best = max(detections, key=lambda d: d.get('confidence', 0))
    kp = best.get('keypoints', {}) or {}
    if 'left_eye' in kp and 'right_eye' in kp and 'nose' in kp:
        return {
            'left_eye': tuple(float(v) for v in kp['left_eye']),
            'right_eye': tuple(float(v) for v in kp['right_eye']),
            'nose': tuple(float(v) for v in kp['nose']),
        }
    return None


def detect_and_align_face(img: np.ndarray) -> np.ndarray:
    """
    Detect face and return a 160x160 aligned face crop.

    Detection priority:
      1. RetinaFace  (best accuracy, requires optional retinaface package)
      2. MTCNN       (installed by default — provides eye landmarks for alignment)
      3. OpenCV Haar (last resort — bounding box only, no alignment)

    Steps 1 and 2 both use _align_face_similarity() for consistent 4-DOF
    similarity-transform crops regardless of head tilt or camera distance.
    This is the primary reason false-rejects occur when OpenCV is used:
    unaligned crops vary between registration and verification sessions,
    lowering cosine similarity even for the same person.

    Raises ValueError with a user-friendly message if no face is found.
    """
    try:
        from retinaface import RetinaFace
        faces = RetinaFace.detect_faces(img)
    except ImportError:
        return _detect_face_mtcnn(img)
    except Exception:
        # RetinaFace is installed but failed — DLL error, stdout/stderr write
        # in no-console mode, or model load failure.  Fall back to MTCNN.
        return _detect_face_mtcnn(img)

    if not faces or isinstance(faces, tuple):
        raise ValueError(
            'No face detected. Center your face in the frame and ensure good lighting.'
        )

    # Pick the face with the highest confidence score
    best_key = max(faces, key=lambda k: faces[k].get('score', 0))
    face_data = faces[best_key]
    score = face_data.get('score', 0)

    if score < 0.5:
        raise ValueError(
            f'Face detection confidence too low ({score:.2f}). '
            'Move closer to the camera or improve lighting.'
        )

    landmarks = face_data.get('landmarks', {})
    facial_area = face_data.get('facial_area', [])

    if facial_area:
        _check_face_within_frame(img.shape, *facial_area)

    if landmarks and 'left_eye' in landmarks and 'right_eye' in landmarks:
        left_eye = np.array(landmarks['left_eye'], dtype=np.float32)
        right_eye = np.array(landmarks['right_eye'], dtype=np.float32)

        # Validate face size — too small means too far from camera.
        # Lowered from 20 to 15 to be more forgiving at normal desk distance.
        inter_eye_dist = float(np.linalg.norm(right_eye - left_eye))
        if inter_eye_dist < 15:
            raise ValueError(
                f'Face too small (eye distance {inter_eye_dist:.0f}px). '
                'Move closer to the camera.'
            )

        face_crop = _align_face_similarity(img, left_eye, right_eye, output_size=(160, 160))
    elif facial_area:
        # Fallback: use bounding box without alignment
        x1, y1, x2, y2 = facial_area
        h, w = img.shape[:2]
        pad = int((x2 - x1) * 0.15)
        x1 = max(0, x1 - pad)
        y1 = max(0, y1 - pad)
        x2 = min(w, x2 + pad)
        y2 = min(h, y2 + pad)
        face_crop = img[y1:y2, x1:x2]
        if face_crop.size == 0:
            raise ValueError('Face crop area is empty.')
        import cv2
        face_crop = cv2.resize(face_crop, (160, 160))
    else:
        raise ValueError('Face detected but landmarks/area unavailable.')

    return face_crop


def _align_face_similarity(
    img: np.ndarray,
    left_eye: np.ndarray,
    right_eye: np.ndarray,
    output_size=(160, 160),
) -> np.ndarray:
    """
    Align face using a 4-DOF similarity transform (rotation + uniform scale + translation).
    Maps the detected left_eye and right_eye to canonical positions in output_size.

    Canonical positions (FaceNet/MTCNN standard for 160x160):
      left_eye  -> (38.29, 51.70)
      right_eye -> (73.53, 51.50)

    The transform is computed analytically from 2 point pairs and is exact.
    """
    import cv2

    # Canonical target positions in the 160x160 output
    dst_left  = np.array([38.29, 51.70], dtype=np.float64)
    dst_right = np.array([73.53, 51.50], dtype=np.float64)

    src = np.array([left_eye, right_eye], dtype=np.float64)
    dst = np.array([dst_left, dst_right], dtype=np.float64)

    # Compute similarity transform analytically from 2 point pairs
    # Transform: [x'] = [a -b] [x] + [tx]
    #            [y']   [b  a] [y]   [ty]
    src_vec = src[1] - src[0]        # source eye-to-eye vector
    dst_vec = dst[1] - dst[0]        # destination eye-to-eye vector
    src_len_sq = float(np.dot(src_vec, src_vec))

    if src_len_sq < 1e-6:
        raise ValueError('Eye landmarks are at the same position — cannot align.')

    a = float(np.dot(src_vec, dst_vec)) / src_len_sq
    b = float(src_vec[0] * dst_vec[1] - src_vec[1] * dst_vec[0]) / src_len_sq

    tx = dst[0][0] - (a * src[0][0] - b * src[0][1])
    ty = dst[0][1] - (b * src[0][0] + a * src[0][1])

    M = np.array([[a, -b, tx],
                  [b,  a, ty]], dtype=np.float32)

    aligned = cv2.warpAffine(
        img, M, output_size,
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REFLECT_101,
    )
    return aligned


def _detect_face_opencv(img: np.ndarray) -> np.ndarray:
    """Fallback face detector using OpenCV Haar cascade."""
    import cv2
    cascade_path = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
    face_cascade = cv2.CascadeClassifier(cascade_path)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    faces = face_cascade.detectMultiScale(
        gray, scaleFactor=1.1, minNeighbors=5, minSize=(60, 60)
    )
    if len(faces) == 0:
        raise ValueError(
            'No face detected. Center your face and ensure adequate lighting.'
        )
    # Use the largest detected face
    x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
    _check_face_within_frame(img.shape, x, y, x + w, y + h)
    pad = int(w * 0.10)
    img_h, img_w = img.shape[:2]
    x1 = max(0, x - pad)
    y1 = max(0, y - pad)
    x2 = min(img_w, x + w + pad)
    y2 = min(img_h, y + h + pad)
    face_crop = img[y1:y2, x1:x2]
    face_crop = cv2.resize(face_crop, (160, 160))
    return face_crop


# ─── Face Quality Check ───────────────────────────────────────────────────────

def check_face_quality(face_img: np.ndarray) -> dict:
    """
    Assess face crop quality. Returns a dict with 'ok', 'score', and 'reason'.
    A low-quality crop will produce unreliable FaceNet embeddings.

    Checks:
      - Sharpness via Laplacian variance (blur detection)
      - Brightness (underexposure / overexposure)
      - Contrast via standard deviation of grayscale
      - Glare detection (overexposed highlight regions)
    """
    import cv2
    if face_img is None or face_img.size == 0:
        return {'ok': False, 'score': 0.0, 'reason': 'Empty face image.'}

    gray = cv2.cvtColor(face_img, cv2.COLOR_BGR2GRAY)

    # Sharpness via Laplacian variance (higher = sharper)
    blur_score = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    # Brightness
    brightness = float(gray.mean())
    # Contrast: std deviation of grayscale
    contrast = float(gray.std())
    # Glare: fraction of pixels that are overexposed (>250)
    glare_fraction = float(np.mean(gray > 250))

    issues = []
    if blur_score < 15:
        issues.append('image too blurry — hold still and stay in focus')
    if brightness < 30:
        issues.append('too dark — face a light source or turn on more lights')
    elif brightness > 230:
        issues.append('overexposed — reduce bright light or backlight behind you')
    if contrast < 12:
        issues.append('low contrast — improve lighting')
    if glare_fraction > 0.15:
        issues.append('glare detected — move away from direct light or glass')

    # Composite score [0-1]
    sharpness_score  = min(blur_score / 200.0, 1.0)
    brightness_score = 1.0 - abs(brightness - 120) / 120.0
    contrast_score   = min(contrast / 60.0, 1.0)
    glare_penalty    = max(0.0, 1.0 - glare_fraction * 4)

    quality_score = (
        0.50 * sharpness_score
        + 0.25 * brightness_score
        + 0.15 * contrast_score
        + 0.10 * glare_penalty
    )

    ok = len(issues) == 0
    reason = 'Good quality.' if ok else ('Poor quality: ' + '; '.join(issues) + '.')

    return {
        'ok': ok,
        'score': float(np.clip(quality_score, 0.0, 1.0)),
        'blur_score': blur_score,
        'brightness': brightness,
        'contrast': contrast,
        'glare_fraction': glare_fraction,
        'reason': reason,
    }


# ─── FaceNet Model Cache Location ─────────────────────────────────────────────

def get_facenet_cache_dir() -> str:
    """
    Resolve the persistent, machine-local directory keras-facenet should use
    to cache its downloaded weights — instead of keras-facenet's own default
    of ``%USERPROFILE%\\.keras-facenet``, which is a different physical
    directory for every Windows account.

    This matters because the packaged installer's autostart Task Scheduler
    entry runs fans_c.exe under the SYSTEM account, while first-run setup
    happens under whichever admin account is doing the interactive install —
    two different USERPROFILE values pointing at two different caches on the
    same machine. Resolving relative to settings.BASE_DIR instead sidesteps
    that entirely: BASE_DIR is computed in fans/settings.py as the directory
    containing fans_c.exe when frozen (Path(sys.executable).parent) — the
    same value regardless of which Windows account's process computes it —
    or the project root in source/dev mode. No USERPROFILE lookup is
    involved either way.

    Resolution order:
      1. FANS_FACENET_CACHE_DIR env var, if set to an absolute path — an
         explicit operator override (e.g. to move the ~90 MB cache to a
         drive with more free space). A relative path is rejected (logged
         and ignored) rather than silently resolved against whatever the
         current working directory happens to be.
      2. <settings.BASE_DIR>/models/keras-facenet — the default for both
         frozen and source execution.
    """
    override = os.environ.get('FANS_FACENET_CACHE_DIR', '').strip()
    if override:
        override_path = Path(override).expanduser()
        if override_path.is_absolute():
            return str(override_path)
        _logger.warning(
            'FANS_FACENET_CACHE_DIR=%r is not an absolute path; ignoring it '
            'and using the default FaceNet cache location instead.',
            override,
        )
    return str(Path(settings.BASE_DIR) / 'models' / 'keras-facenet')


# ─── FaceNet Embedding ────────────────────────────────────────────────────────

_facenet_model = None
_using_mock = False
_model_load_error = None  # stores actual error message for UI display
_facenet_load_failed = False  # sticky: once True, get_facenet_model() fails fast


# Safe to show an operator/end-user: no traceback, no file paths, no internal
# state. The real diagnostic detail (TF version mismatch, DLL errors, network
# failure) stays in _model_load_error / get_model_load_error() for the server
# log only.
_SAFE_UNAVAILABLE_MESSAGE = (
    'Face recognition model is unavailable. Biometric verification is '
    'temporarily disabled. Connect this computer to the internet during '
    'initial setup or contact your Technical Administrator, then restart FANS-C.'
)


class FaceNetUnavailableError(RuntimeError):
    """
    Raised when the real FaceNet model cannot be loaded (TensorFlow missing/
    broken, keras-facenet missing, or the model weights could not be
    downloaded/verified — e.g. no internet on a clean machine's first run).

    This is a SYSTEM availability failure, not a biometric mismatch. Callers
    must never substitute a random/mock embedding when this is raised —
    get_facenet_model() and get_embedding() never return a working mock
    object in normal operation; this exception is the only way "model not
    available" is communicated. See _MockFaceNet for the test-only exception
    to that rule.
    """


def get_facenet_model():
    """
    Return the loaded FaceNet model, or raise FaceNetUnavailableError.

    NEVER returns a mock/random-embedding model as part of normal
    application operation — that automatic fallback was removed because a
    biometric verification system silently comparing against random vectors
    is worse than refusing to verify at all. The only way a mock model is
    ever used is a test explicitly monkeypatching this module's functions
    (is_using_mock_model, get_embedding, process_face_for_*, etc.) — no
    production code path or environment variable can trigger it.

    Once a load attempt fails, the failure is cached for the lifetime of the
    process (_facenet_load_failed) so every subsequent call fails fast
    instead of re-attempting a slow network download per request. Restarting
    FANS-C (e.g. after connecting to the internet) clears this and retries.
    """
    global _facenet_model, _using_mock, _model_load_error, _facenet_load_failed

    if _facenet_model is not None:
        return _facenet_model
    if _facenet_load_failed:
        raise FaceNetUnavailableError(_SAFE_UNAVAILABLE_MESSAGE)

    # Step 1: check TensorFlow separately for a clearer error message.
    # Catch ALL exceptions (not just ImportError) — TF writes to stdout/stderr
    # during import, which raises AttributeError in PyInstaller no-console mode
    # if ensure_console_streams() was not called early enough.
    try:
        import tensorflow as tf  # noqa: F401
    except Exception as e:
        err_str = str(e)
        if isinstance(e, ImportError):
            if 'DLL load failed' in err_str or 'DLL' in err_str:
                _model_load_error = (
                    f'TensorFlow DLL load failed: {e}. '
                    'This almost always means Python version mismatch. '
                    'tensorflow-cpu 2.13.x requires Python 3.10 or 3.11. '
                    'Your Python is likely 3.12 or 3.13. '
                    'Fix: delete .venv, install Python 3.11, recreate .venv, '
                    'then run: pip install -r requirements.txt'
                )
            else:
                _model_load_error = (
                    f'TensorFlow not installed ({e}). '
                    'Run: pip install tensorflow-cpu>=2.13.0,<2.14.0'
                )
        else:
            # AttributeError / OSError / other — most likely sys.stdout or
            # sys.stderr was None when TF tried to write startup output.
            _model_load_error = (
                f'TensorFlow failed to load ({type(e).__name__}: {e}). '
                'If running as a no-console EXE, ensure ensure_console_streams() '
                'is called before any TensorFlow import.'
            )
        _logger.warning(_model_load_error)
        _using_mock = True
        _facenet_load_failed = True
        raise FaceNetUnavailableError(_SAFE_UNAVAILABLE_MESSAGE) from e

    # Step 2: load keras-facenet (this is also where, on a clean machine with
    # no cached weights, the one-time ~90 MB download happens — a network
    # failure here surfaces as an Exception, not a partial/mock model).
    #
    # cache_folder is pinned to the shared machine-local cache (see
    # get_facenet_cache_dir()) rather than keras-facenet's own
    # %USERPROFILE%\.keras-facenet default, so an elevated interactive
    # first run and the SYSTEM-account autostart/watchdog process always
    # agree on where the weights live.
    try:
        from keras_facenet import FaceNet
        cache_dir = get_facenet_cache_dir()
        os.makedirs(cache_dir, exist_ok=True)
        _facenet_model = FaceNet(cache_folder=cache_dir)
        _using_mock = False
        _model_load_error = None
        _facenet_load_failed = False
        return _facenet_model
    except ImportError as e:
        _model_load_error = (
            f'keras-facenet not installed ({e}). '
            'Run: pip install keras-facenet'
        )
        _logger.warning(_model_load_error)
        _using_mock = True
        _facenet_load_failed = True
        raise FaceNetUnavailableError(_SAFE_UNAVAILABLE_MESSAGE) from e
    except Exception as e:
        _model_load_error = (
            f'Model load failed ({type(e).__name__}: {e}). '
            'Check TensorFlow version compatibility, or — if this is a clean '
            'install — confirm internet connectivity for the one-time '
            'FaceNet weights download.'
        )
        _logger.warning(_model_load_error)
        _using_mock = True
        _facenet_load_failed = True
        raise FaceNetUnavailableError(_SAFE_UNAVAILABLE_MESSAGE) from e


def is_using_mock_model() -> bool:
    """
    Returns True if the real FaceNet model is not currently available.

    Despite the name (kept for compatibility with existing callers/tests),
    this no longer means "a mock model is loaded and will be used" — no
    production path ever uses a mock model. It means get_embedding() will
    raise FaceNetUnavailableError if called right now, so callers should
    treat True the same as catching that exception.
    """
    try:
        get_facenet_model()
    except FaceNetUnavailableError:
        return True
    return False


def get_model_load_error() -> str:
    """Returns the error that caused the load failure, or None if the model loaded successfully."""
    try:
        get_facenet_model()
    except FaceNetUnavailableError:
        pass
    return _model_load_error


class _MockFaceNet:
    """
    TEST-ONLY. Never instantiated by production code (see get_facenet_model
    docstring) — kept here only so tests that want to exercise "what if the
    model were mocked" behaviour can explicitly patch it in, e.g.:
        mock.patch('verification.face_utils.get_facenet_model',
                    return_value=_MockFaceNet())
    Returns random unit-normalized embeddings — never valid for real
    matching. There is no environment variable or production code path that
    selects this automatically.
    """
    def embeddings(self, faces):
        _logger.warning(
            'MOCK FaceNet: embeddings are random. Test-only — this must never '
            'be reachable from production code.'
        )
        n = len(faces)
        embs = np.random.randn(n, 128).astype(np.float32)
        norms = np.linalg.norm(embs, axis=1, keepdims=True)
        return embs / (norms + 1e-10)


def get_embedding(face_img: np.ndarray) -> np.ndarray:
    """
    Generate a 512-d FaceNet embedding from a 160x160 BGR face crop.

    keras-facenet's FaceNet() defaults to checkpoint 20180402-114759 (an
    Inception-ResNet-v1 trained on VGGFace2), which outputs 512-dimensional
    embeddings — verified directly against the installed package
    (keras_facenet/metadata.py: MODEL_METADATA['20180402-114759']['dimensions']
    == 512), NOT the 128-d embeddings some FaceNet literature/older keras-facenet
    checkpoints (20170511-185253, 20170512-110547) describe. Do not assume 128-d
    when reading similarity-score code elsewhere in this project.

    Preprocessing pipeline:
      1. Apply CLAHE for contrast normalisation (helps low-light captures)
      2. BGR -> RGB conversion
      3. Resize to 160x160
      4. keras-facenet applies fixed_image_standardization internally for this
         checkpoint: (pixel - 127.5) / 127.5, a fixed rescale to [-1, 1] — NOT
         the classic per-image mean/std "prewhiten" used by its 128-d checkpoints.
      5. L2-normalize output

    This pipeline is identical for registration and verification, ensuring that
    embeddings from both stages are directly comparable via cosine similarity.
    """
    import cv2
    # Apply CLAHE before colour conversion for better low-light performance
    face_enhanced = _apply_clahe(face_img)
    face_rgb = cv2.cvtColor(face_enhanced, cv2.COLOR_BGR2RGB)
    face_resized = cv2.resize(face_rgb, (160, 160))
    faces_batch = np.expand_dims(face_resized, axis=0)  # (1, 160, 160, 3) uint8

    model = get_facenet_model()
    embedding = model.embeddings(faces_batch)[0]

    # L2-normalize (keras-facenet already does this, but enforce it)
    norm = np.linalg.norm(embedding)
    if norm > 1e-10:
        embedding = embedding / norm

    return embedding.astype(np.float32)


# ─── Similarity ───────────────────────────────────────────────────────────────

def cosine_similarity(emb1: np.ndarray, emb2: np.ndarray) -> float:
    """Cosine similarity between two embeddings (re-normalizes for safety)."""
    n1 = np.linalg.norm(emb1)
    n2 = np.linalg.norm(emb2)
    if n1 < 1e-10 or n2 < 1e-10:
        return 0.0
    return float(np.dot(emb1 / n1, emb2 / n2))


# ─── Decision (shared by verify_submit and the BPA-2 evaluation workflow) ─────

def decide_base_outcome(score: float, threshold: float, auto_verify_threshold: float) -> str:
    """
    Pure three-zone similarity-score decision (v2.1.13 band), shared by
    production `verify_submit` and the controlled evaluation trial workflow
    (BPA-2) so both use identical decision rules. Returns the BASE decision
    only — the caller is responsible for any subsequent quality-override or
    lookalike-escalation logic (verify_submit applies both; those are
    context-specific and not part of this pure function).

    Zones:
      score >= auto_verify_threshold             -> 'verified'
      threshold <= score < auto_verify_threshold  -> 'manual_review' (high-band)
      review_band <= score < threshold            -> 'manual_review' (low-band)
      score < review_band                         -> 'not_verified'
    where review_band = threshold * 0.85.
    """
    review_band = threshold * 0.85
    if score >= auto_verify_threshold:
        return 'verified'
    elif score >= threshold:
        return 'manual_review'
    elif score >= review_band:
        return 'manual_review'
    else:
        return 'not_verified'


def apply_quality_override(decision: str, quality, low_quality_forces_mr: bool = True):
    """
    Pure. Mirrors verify_submit's v2.1.13 "low-quality captures cannot
    auto-verify" rule (BPA-2.1 shared helper — see EvaluationTrial parity
    tests). `quality` is a dict like `{'ok': bool, 'reason': str, ...}`
    (from face_utils.check_face_quality) or None.

    IMPORTANT parity note: in production, `quality` is only non-None on
    verify_submit's rare "fallback" identity path (no LivenessTransaction
    embedding available) — on the common TX-bound path this rule is
    effectively dormant, because the quality dict computed for the audit
    record there is never passed to this check. Callers must reproduce
    that same conditionality deliberately (pass `quality=None` on a
    TX-bound/liveness-proof-bound path) rather than pass a quality dict
    that happens to be available for other reasons.

    Returns (new_decision, applied: bool).
    """
    quality_low = bool(quality) and not quality.get('ok', True)
    if low_quality_forces_mr and quality_low and decision == 'verified':
        return 'manual_review', True
    return decision, False


def check_lookalike_escalation(live_embedding, score: float, threshold: float,
                                exclude_beneficiary_id, lookalike_band: float = 0.05) -> dict:
    """
    Mirrors verify_submit's lookalike/twin-detection escalation exactly —
    only meaningful when the caller's decision is 'verified' and
    score >= threshold (the caller checks this before calling). Searches
    all OTHER beneficiaries' stored embeddings for a near-duplicate match.

    Returns:
        {'escalate': bool, 'checked': int, 'lookalike_threshold': float,
         'top_match': {'beneficiary_id', 'full_name', 'score'} | None}
    """
    lookalike_threshold = max(score - lookalike_band, threshold * 0.85)
    dup_check = check_duplicate_face(
        live_embedding, threshold=lookalike_threshold, exclude_beneficiary_id=exclude_beneficiary_id,
    )
    top_match = dup_check['matches'][0] if dup_check.get('matches') else None
    return {
        'escalate': bool(dup_check.get('duplicates_found')),
        'checked': dup_check.get('checked', 0),
        'lookalike_threshold': lookalike_threshold,
        'top_match': top_match,
    }


def check_representative_beneficiary_fallback(live_embedding, beneficiary, threshold: float) -> dict:
    """
    Hard rule mirrored from verify_submit: a representative claim must
    NEVER succeed via the beneficiary's own face (the senior showing their
    own face for a representative claim). Probes the live embedding
    against the BENEFICIARY's stored templates; if it matches at or above
    the review threshold, the representative claim must be blocked.

    Returns {'blocked': bool, 'score': float, 'templates_checked': int}.
    """
    ben_probe = compare_with_all_embeddings(live_embedding, beneficiary)
    if not ben_probe.get('success'):
        return {'blocked': False, 'score': 0.0, 'templates_checked': 0}
    ben_score = float(ben_probe.get('score') or 0.0)
    return {
        'blocked': ben_score >= threshold,
        'score': ben_score,
        'templates_checked': ben_probe.get('templates_checked', 0),
    }


# ─── Full Pipeline ────────────────────────────────────────────────────────────

def process_face_for_registration(image_bytes: bytes) -> dict:
    """
    Full pipeline for registration:
      load image -> detect & align face -> quality check -> CLAHE -> embedding -> encrypt

    Quality gate: rejects clearly unusable captures so the stored embedding is reliable.
    The threshold is intentionally lenient (blur_score < 8) — staff can retake if needed.
    """
    if not image_bytes:
        return {'success': False, 'error': 'No image data received. Please capture a face photo first.'}

    try:
        img = load_image_from_bytes(image_bytes)
        face_img = detect_and_align_face(img)

        quality = check_face_quality(face_img)
        # Reject severely blurry or dark images during registration
        if quality['blur_score'] < 8:
            return {
                'success': False,
                'error': f'Image quality too poor for registration. {quality["reason"]}',
            }

        # Check model availability BEFORE running the embedding to avoid calling
        # mock embeddings unnecessarily (mock model is rejected at registration time).
        if is_using_mock_model():
            return {
                'success': False,
                'error': (
                    'Face recognition model not loaded. '
                    'Install keras-facenet and tensorflow, then restart the server.'
                ),
                'model_unavailable': True,
            }

        embedding = get_embedding(face_img)
        encrypted = encrypt_embedding(embedding)
        return {
            'success': True,
            'encrypted_embedding': encrypted,
            'quality': quality,
            'embedding_shape': embedding.shape,
        }
    except FaceNetUnavailableError as e:
        # Belt-and-suspenders: the is_using_mock_model() pre-check above
        # already catches this in normal operation, but get_embedding() is
        # the actual central guarantee — no FaceEmbedding is ever stored
        # from a state where the real model wasn't loaded.
        return {'success': False, 'error': str(e), 'model_unavailable': True}
    except ValueError as e:
        return {'success': False, 'error': str(e)}
    except Exception as e:
        _logger.exception('process_face_for_registration failed')
        return {'success': False, 'error': f'Face processing error: {str(e)}'}


def process_face_for_verification(image_bytes: bytes, reject_poor_quality: bool = True) -> dict:
    """
    Full pipeline for verification:
      load image -> detect & align face -> quality check -> CLAHE -> embedding

    Args:
        reject_poor_quality: If True, frames with blur_score < 5 are rejected
          rather than producing an unreliable embedding. This prevents random
          low scores from very blurry captures misleading the decision.

    Returns dict with 'success', 'embedding', 'quality', or 'error'.
    """
    if not image_bytes:
        return {'success': False, 'error': 'No image data received. Please capture a face photo.'}

    try:
        img = load_image_from_bytes(image_bytes)
        face_img = detect_and_align_face(img)
        quality = check_face_quality(face_img)

        # Hard reject only severely blurry frames during verification
        # Threshold lowered from 5 to 3 to avoid rejecting borderline captures
        if reject_poor_quality and quality['blur_score'] < 3:
            return {
                'success': False,
                'error': (
                    f'Frame too blurry for reliable verification. '
                    f'{quality["reason"]} Please hold still and retry.'
                ),
                'quality': quality,
            }

        embedding = get_embedding(face_img)

        return {
            'success': True,
            'embedding': embedding,
            'quality': quality,
            # success is only ever True when get_embedding() returned a real
            # embedding — get_facenet_model() raises FaceNetUnavailableError
            # (caught below) rather than returning a mock model, so this can
            # no longer be True on a successful result.
            'using_mock': False,
        }
    except FaceNetUnavailableError as e:
        # SYSTEM availability failure, not a biometric mismatch — callers
        # must not treat this the same as a failed face match (see
        # verify_submit's _service_unavailable()).
        return {'success': False, 'error': str(e), 'model_unavailable': True}
    except ValueError as e:
        return {'success': False, 'error': str(e)}
    except Exception as e:
        _logger.exception('process_face_for_verification failed')
        return {'success': False, 'error': f'Face processing error: {str(e)}'}


def count_faces_in_frame(image_bytes: bytes) -> dict:
    """
    Detect how many human faces are present in `image_bytes` WITHOUT alignment
    or embedding extraction.

    Returns:
        {
            'success':       True | False,
            'count':         int    (only meaningful when success=True),
            'detector':      'retinaface' | 'mtcnn' | 'opencv',
            'confidences':   [float, ...]  (when available),
            'error':         str    (only on success=False)
        }

    This helper is used by verify_submit to enforce the rule:
        - 0 faces  → DENY (object/animal/empty frame)
        - >1 faces → DENY (subject ambiguity)
        - 1 face   → proceed to same-face consistency check vs. liveness frame.

    The function does not raise; it returns a structured result so callers can
    treat detection failure as a denial reason rather than a 500-error path.
    """
    if not image_bytes:
        return {'success': False, 'error': 'No image data.', 'count': 0}

    try:
        img = load_image_from_bytes(image_bytes)
    except Exception as exc:
        return {'success': False, 'error': f'Image decode failed: {exc}', 'count': 0}

    # 1) RetinaFace (best when available). Returns dict of face_id -> {score, facial_area, ...}.
    try:
        from retinaface import RetinaFace
        faces = RetinaFace.detect_faces(img)
        if isinstance(faces, dict):
            confs = [float(f.get('score', 0.0)) for f in faces.values()
                     if float(f.get('score', 0.0)) >= 0.5]
            return {
                'success': True,
                'count': len(confs),
                'detector': 'retinaface',
                'confidences': confs,
            }
        # RetinaFace returns an empty tuple when no face is found.
        return {
            'success': True, 'count': 0,
            'detector': 'retinaface', 'confidences': [],
        }
    except ImportError:
        pass
    except Exception:
        # RetinaFace failed at runtime — fall through to MTCNN.
        pass

    # 2) MTCNN — `detector.detect_faces` returns a list of dicts.
    try:
        import cv2
        detector = _get_mtcnn()
        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        detections = detector.detect_faces(img_rgb) or []
        confs = [float(d.get('confidence', 0.0)) for d in detections
                 if float(d.get('confidence', 0.0)) >= 0.85]
        return {
            'success': True,
            'count': len(confs),
            'detector': 'mtcnn',
            'confidences': confs,
        }
    except Exception:
        pass

    # 3) OpenCV Haar — last resort. Tolerates the worst lighting but has no
    # confidence score, so count alone is the signal.
    try:
        import cv2
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        haar_path = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
        cascade = cv2.CascadeClassifier(haar_path)
        faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5,
                                          minSize=(40, 40))
        return {
            'success': True,
            'count': int(len(faces)),
            'detector': 'opencv',
            'confidences': [],
        }
    except Exception as exc:
        return {
            'success': False,
            'error': f'Face counter unavailable: {exc}',
            'count': 0,
        }


def get_embedding_only(image_bytes: bytes) -> dict:
    """
    Detect face, align, and compute FaceNet embedding without full quality gating.
    Used by verify_check_liveness to capture the liveness-frame embedding for
    storage in the LivenessTransaction.

    Returns {'success': bool, 'embedding': np.ndarray} or {'success': False, 'error': str}.
    """
    if not image_bytes:
        return {'success': False, 'error': 'No image data.'}
    try:
        img = load_image_from_bytes(image_bytes)
        face_img = detect_and_align_face(img)
        embedding = get_embedding(face_img)
        return {'success': True, 'embedding': embedding, 'using_mock': False}
    except FaceNetUnavailableError as e:
        return {'success': False, 'error': str(e), 'model_unavailable': True}
    except ValueError as e:
        return {'success': False, 'error': str(e)}
    except Exception as e:
        return {'success': False, 'error': f'Embedding error: {str(e)}'}


def compare_with_stored(live_embedding: np.ndarray, encrypted_stored: bytes) -> dict:
    """Compare live embedding against a single stored encrypted embedding."""
    try:
        stored_embedding = decrypt_embedding(encrypted_stored)
        # Skip embeddings whose dimension doesn't match the live embedding
        # (can happen after a model version change or corrupt storage)
        if stored_embedding.shape != live_embedding.shape:
            return {
                'success': False,
                'error': (
                    f'Embedding dimension mismatch: '
                    f'live={live_embedding.shape}, stored={stored_embedding.shape}. '
                    'Re-register this beneficiary to fix.'
                ),
                'score': 0.0,
            }
        score = cosine_similarity(live_embedding, stored_embedding)
        return {'success': True, 'score': score}
    except Exception as e:
        return {'success': False, 'error': f'Comparison error: {str(e)}', 'score': 0.0}


def compare_with_all_embeddings(live_embedding: np.ndarray, beneficiary) -> dict:
    """
    Compare live embedding against ALL stored embeddings for a beneficiary
    and return the best (highest) score plus template tracking details.

    Supports multi-template matching: primary FaceEmbedding + any additional
    AdditionalFaceEmbedding records. Returns the best score across all templates.

    Return keys:
      success          – bool
      score            – float, best cosine similarity found
      matched_template – str, label of the winning template
      templates_checked– int, number of templates compared
      all_scores       – list of {'template': str, 'score': float}
      error            – str (only when success=False)
    """
    best_score = None
    matched_template = ''
    all_scores = []
    errors = []

    _logger.debug(
        'compare_with_all_embeddings | beneficiary=%s | live_embedding.shape=%s',
        getattr(beneficiary, 'beneficiary_id', '?'),
        live_embedding.shape,
    )

    # Primary embedding
    try:
        primary = beneficiary.face_embedding
        result = compare_with_stored(live_embedding, primary.embedding_data)
        if result['success']:
            s = result['score']
            all_scores.append({'template': 'primary', 'score': s})
            _logger.debug('primary score=%.4f', s)
            if best_score is None or s > best_score:
                best_score = s
                matched_template = 'primary'
        else:
            err = result.get('error', 'Primary comparison failed')
            _logger.debug('primary FAILED: %s', err)
            errors.append(err)
    except Exception as e:
        _logger.debug('primary embedding error: %s', e)
        errors.append(f'Primary embedding error: {e}')

    # Additional templates (AdditionalFaceEmbedding)
    try:
        additional = list(beneficiary.additional_embeddings.all())
        for idx, tmpl in enumerate(additional, start=1):
            label = getattr(tmpl, 'label', None) or f'additional_{idx}'
            r = compare_with_stored(live_embedding, tmpl.embedding_data)
            if r['success']:
                s = r['score']
                all_scores.append({'template': label, 'score': s})
                _logger.debug('%s score=%.4f', label, s)
                if best_score is None or s > best_score:
                    best_score = s
                    matched_template = label
            else:
                _logger.debug('%s FAILED: %s', label, r.get('error', ''))
    except AttributeError:
        pass  # No additional embeddings model — fine
    except Exception as e:
        errors.append(f'Additional template error: {e}')

    templates_checked = len(all_scores)
    _logger.debug(
        'BEST score=%s | matched=%s | templates_checked=%d',
        best_score, matched_template, templates_checked,
    )

    if best_score is None:
        return {
            'success': False,
            'error': '; '.join(errors) or 'No embeddings available.',
            'score': 0.0,
            'matched_template': '',
            'templates_checked': 0,
            'all_scores': [],
        }

    return {
        'success': True,
        'score': best_score,
        'matched_template': matched_template,
        'templates_checked': templates_checked,
        'all_scores': all_scores,
    }


# ─── Duplicate Face Detection ─────────────────────────────────────────────────

def check_duplicate_face(
    live_embedding: np.ndarray,
    threshold: float = 0.80,
    exclude_beneficiary_id: str = None,
    include_representatives: bool = True,
    exclude_representative_id: str = None,
) -> dict:
    """
    Compare a new embedding against ALL stored beneficiary embeddings (and, by
    default, all stored representative embeddings) to detect duplicate or
    near-duplicate faces during registration.

    Representative embeddings are included by default because a biometric
    identity is a single pool regardless of the role a record is registered
    under — the same face enrolled as a beneficiary in one record and as a
    representative in another (or as two different representatives) is the
    same identity-integrity risk as two beneficiary records sharing a face,
    and must surface the same way.

    Args:
        live_embedding: 512-d L2-normalized embedding from the new capture.
        threshold: Cosine similarity threshold above which a face is considered
            a duplicate. Default 0.80 (tighter than verification threshold to
            flag even partial lookalikes for review).
        exclude_beneficiary_id: Skip embeddings belonging to this beneficiary
            (used when re-registering an existing record).
        include_representatives: Also search RepresentativeFaceEmbedding.
            Defaults True; callers doing a narrow beneficiary-only check may
            pass False.
        exclude_representative_id: Skip the embedding belonging to this
            representative (used when a representative is updating their own
            face — must not flag itself as a duplicate of itself).

    Returns a dict with:
        duplicates_found – bool, True if any match exceeds threshold
        matches          – list of dicts: {beneficiary_id, full_name, score,
            template, source, representative_id, representative_name}.
            `beneficiary_id`/`full_name` always identify an existing
            beneficiary record — for a representative-sourced match
            (source='representative') this is the beneficiary the matching
            representative represents, and `representative_id`/
            `representative_name` identify the representative whose face
            actually matched.
        highest_score    – float, best match found (0.0 if none)
        checked          – int, total number of embeddings compared
    """
    from .models import FaceEmbedding, AdditionalFaceEmbedding, RepresentativeFaceEmbedding

    matches = []
    highest_score = 0.0
    checked = 0

    # Check primary embeddings
    primary_qs = FaceEmbedding.objects.select_related('beneficiary').all()
    if exclude_beneficiary_id:
        primary_qs = primary_qs.exclude(beneficiary__beneficiary_id=exclude_beneficiary_id)

    for fe in primary_qs:
        try:
            stored = decrypt_embedding(fe.embedding_data)
            if stored.shape != live_embedding.shape:
                continue
            score = cosine_similarity(live_embedding, stored)
            checked += 1
            if score > highest_score:
                highest_score = score
            if score >= threshold:
                matches.append({
                    'beneficiary_id': fe.beneficiary.beneficiary_id,
                    'full_name': fe.beneficiary.full_name,
                    'score': round(score, 4),
                    'template': 'primary',
                    'source': 'beneficiary',
                    'representative_id': None,
                    'representative_name': None,
                })
        except Exception:
            continue

    # Check additional embeddings
    additional_qs = AdditionalFaceEmbedding.objects.select_related('beneficiary').all()
    if exclude_beneficiary_id:
        additional_qs = additional_qs.exclude(beneficiary__beneficiary_id=exclude_beneficiary_id)

    for afe in additional_qs:
        try:
            stored = decrypt_embedding(afe.embedding_data)
            if stored.shape != live_embedding.shape:
                continue
            score = cosine_similarity(live_embedding, stored)
            checked += 1
            if score > highest_score:
                highest_score = score
            if score >= threshold:
                label = getattr(afe, 'label', None) or 'additional'
                matches.append({
                    'beneficiary_id': afe.beneficiary.beneficiary_id,
                    'full_name': afe.beneficiary.full_name,
                    'score': round(score, 4),
                    'template': label,
                    'source': 'beneficiary',
                    'representative_id': None,
                    'representative_name': None,
                })
        except Exception:
            continue

    # Check representative embeddings — same biometric pool as beneficiaries.
    # A representative's face is matched against every OTHER active
    # representative's face (excluding the caller's own, if updating) so that
    # two different representatives sharing a face are caught even when
    # neither one's face happens to match a beneficiary record directly.
    if include_representatives:
        rep_qs = (
            RepresentativeFaceEmbedding.objects
            .select_related('representative', 'representative__beneficiary')
            .filter(representative__is_active=True)
        )
        if exclude_representative_id:
            rep_qs = rep_qs.exclude(representative_id=exclude_representative_id)
        if exclude_beneficiary_id:
            rep_qs = rep_qs.exclude(representative__beneficiary__beneficiary_id=exclude_beneficiary_id)

        for rfe in rep_qs:
            try:
                stored = decrypt_embedding(rfe.embedding_data)
                if stored.shape != live_embedding.shape:
                    continue
                score = cosine_similarity(live_embedding, stored)
                checked += 1
                if score > highest_score:
                    highest_score = score
                if score >= threshold:
                    rep = rfe.representative
                    matches.append({
                        'beneficiary_id': rep.beneficiary.beneficiary_id,
                        'full_name': rep.beneficiary.full_name,
                        'score': round(score, 4),
                        'template': 'representative',
                        'source': 'representative',
                        'representative_id': str(rep.id),
                        'representative_name': rep.full_name,
                    })
            except Exception:
                continue

    # Sort by score descending
    matches.sort(key=lambda m: m['score'], reverse=True)

    return {
        'duplicates_found': len(matches) > 0,
        'matches': matches,
        'highest_score': round(highest_score, 4),
        'checked': checked,
    }
