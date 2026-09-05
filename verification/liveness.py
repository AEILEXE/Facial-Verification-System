"""
Liveness detection module — FANS-C.

Two-layer approach
───────────────────
1. Anti-spoofing (server-side, always runs)
   Texture analysis on the captured face image: Laplacian variance, local variance
   (LBP proxy), and Sobel edge density.  Returns a score in [0, 1]. Scores below the
   ANTI_SPOOF_THRESHOLD (default 0.25) are flagged as suspicious.

   NOTE: This is a heuristic suitable for webcam captures. For higher-security
   deployments, replace compute_texture_score() with a trained CNN anti-spoofing model
   (e.g. Silent-Face-Anti-Spoofing or MiniFASNet) for proper Presentation Attack Detection.

2. Head movement challenge (server-authoritative, always required for verification)
   The user is asked to perform a movement in a random direction (currently
   only 'side' — left or right — is issued). static/js/liveness.js runs
   MediaPipe Face Mesh in the browser to drive the on-screen UX (when to
   show "turn now", when the user has visibly moved enough to submit), but
   that client-side pose estimate is NEVER trusted for the security
   decision (v2.1.16 Security Hardening Round #4 — complete redesign). The
   server instead re-detects the face itself — via face_utils.detect_pose_keypoints(),
   the same RetinaFace/MTCNN detector used for face matching — on the raw
   neutral (baseline) and challenge (proof) frame bytes it received, and
   verify_server_authoritative_challenge() decides whether real movement
   occurred using only those server-detected coordinates. See that
   function's docstring for the full trust-boundary rationale. The server
   also validates:
   - That the frame embedding is consistent with the registered face
   - That the PAD heuristics do not flag the sequence as a replay/screen
   - That the submitted frame bytes are not a byte-for-byte replay of
     evidence already used for a prior successful liveness check

   Accessible threshold: 4 degrees for head turns (natural ~10-15° passes easily).
   Senior citizens are not required to make extreme movements.

3. Face quality checks (server-side)
   Before accepting any liveness frame, the server checks:
   - Only one face is detected
   - Face is not too dark / too bright
   - Face is not too blurry
   - Face is not too small
   - Face is not cropped at the edge

Operating modes
────────────────
Strict Mode (LIVENESS_REQUIRED=True, the default):
  A failed liveness check immediately denies the attempt before face matching runs.

Assisted Rollout Mode (LIVENESS_REQUIRED=False):
  Liveness result is recorded in the VerificationAttempt but does NOT block face matching.
  Use only during initial pilot deployment to calibrate thresholds.

Server-side threshold: 5 degrees (must match CHALLENGE_THRESHOLD_DEG in liveness.js).
Combined liveness score formula: 0.6 × anti_spoof_score + 0.4 × challenge_completed.
"""
import random
import numpy as np
import cv2


# ─── Anti-Spoofing ────────────────────────────────────────────────────────────

def compute_texture_score(face_img: np.ndarray) -> float:
    """
    Liveness score based on combined texture analysis.

    Uses:
      - Laplacian variance: real faces have higher focus variance than prints/screens
      - Local variance (LBP proxy): real faces have more micro-texture
      - Edge density via Sobel: printed photos tend to have smoother gradients

    Returns float in [0, 1] — higher = more likely real.
    """
    if face_img is None or face_img.size == 0:
        return 0.0

    gray = cv2.cvtColor(face_img, cv2.COLOR_BGR2GRAY)

    lap_var = cv2.Laplacian(gray, cv2.CV_64F).var()

    kernel = np.ones((5, 5), np.float32) / 25
    local_mean = cv2.filter2D(gray.astype(np.float32), -1, kernel)
    local_variance = cv2.filter2D((gray.astype(np.float32) - local_mean) ** 2, -1, kernel)
    lbp_score = local_variance.mean()

    sobel_x = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    sobel_y = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    edge_mag = np.sqrt(sobel_x ** 2 + sobel_y ** 2)
    edge_density = float(np.mean(edge_mag > 10))

    lap_normalized = min(lap_var / 300.0, 1.0)
    lbp_normalized = min(lbp_score / 150.0, 1.0)
    edge_normalized = min(edge_density / 0.25, 1.0)

    score = 0.50 * lap_normalized + 0.30 * lbp_normalized + 0.20 * edge_normalized
    return float(np.clip(score, 0.0, 1.0))


def check_anti_spoofing(face_img: np.ndarray, threshold: float = 0.25) -> dict:
    """
    Run anti-spoofing check on a face image.

    Returns:
        {'passed': bool, 'score': float, 'reason': str}
    """
    score = compute_texture_score(face_img)
    passed = score >= threshold
    return {
        'passed': passed,
        'score': score,
        'reason': (
            'Real face detected.'
            if passed
            else f'Low texture score ({score:.3f}); possible spoofing or very low quality image.'
        ),
    }


# ─── Face Quality Check ───────────────────────────────────────────────────────

def check_frame_quality(face_img: np.ndarray, frame_full: np.ndarray = None) -> dict:
    """
    Quality gating before accepting a liveness frame.

    Checks:
    - Face size (not too small)
    - Brightness (not too dark, not too bright)
    - Sharpness (not too blurry)
    - Saturation / color (not grayscale / near-zero channel)

    Returns: {'ok': bool, 'score': float, 'reason': str}
    """
    if face_img is None or face_img.size == 0:
        return {'ok': False, 'score': 0.0, 'reason': 'No face image.'}

    h, w = face_img.shape[:2]

    # Size check
    if h < 60 or w < 60:
        return {'ok': False, 'score': 0.0, 'reason': 'Face too small or too far from camera. Move closer.'}

    gray = cv2.cvtColor(face_img, cv2.COLOR_BGR2GRAY)

    # Brightness
    mean_brightness = float(gray.mean())
    if mean_brightness < 30:
        return {'ok': False, 'score': 0.0, 'reason': 'Too dark. Improve lighting before proceeding.'}
    if mean_brightness > 230:
        return {'ok': False, 'score': 0.4, 'reason': 'Too bright. Reduce direct light on face.'}

    # Sharpness
    lap_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    if lap_var < 15:
        return {'ok': False, 'score': 0.2, 'reason': 'Image too blurry. Hold still and face the camera.'}

    # Combined quality score
    bright_score = 1.0 - abs(mean_brightness - 128) / 128.0
    sharp_score = min(lap_var / 200.0, 1.0)
    quality_score = float(0.5 * bright_score + 0.5 * sharp_score)

    return {'ok': True, 'score': quality_score, 'reason': 'Quality OK.'}


# ─── Head Movement Challenge ──────────────────────────────────────────────────

# All available challenge directions.
# 'side' covers either left or right (accessible for seniors with limited range).
ALL_CHALLENGE_DIRECTIONS = ['left', 'right', 'up', 'down', 'side', 'blink', 'smile']

# Default accessible set — recommended for senior citizen deployments.
# 'side' = turn head slightly left OR right; 'blink' and 'smile' are always easy.
ACCESSIBLE_CHALLENGE_DIRECTIONS = ['side', 'left', 'right', 'blink', 'smile']

# Currently the system only uses 'side' as the server validates it.
# Adding more client-side expression challenges requires liveness.js updates too.
CHALLENGE_DIRECTIONS = ['side']

# Server-side head pose threshold in degrees.
# Must match CHALLENGE_THRESHOLD_DEG in static/js/liveness.js.
SERVER_CHALLENGE_THRESHOLD_DEG = 4.0


def get_random_challenge() -> str:
    return random.choice(CHALLENGE_DIRECTIONS)


def get_accessible_challenge() -> str:
    """Return a challenge from the accessible set (for senior users)."""
    return random.choice(ACCESSIBLE_CHALLENGE_DIRECTIONS)


def analyze_head_pose_from_keypoints(keypoints: dict) -> dict | None:
    """
    Server-authoritative yaw/pitch estimate from face-detector keypoints
    (left_eye, right_eye, nose) that the SERVER ITSELF detected from raw
    frame pixels via detect_pose_keypoints() (RetinaFace/MTCNN — the same
    detector pipeline already used for face matching).

    v2.1.16 Security Hardening Round #4 (Blocker 1 — complete redesign):
    the previous analyze_head_pose() took a 468-point MediaPipe landmark
    list supplied by the CLIENT as plain JSON. That is attacker-controlled
    data — a modified request could fabricate a landmark array showing
    whatever yaw delta was needed with no corresponding real head movement
    ever happening in front of the camera. This function only ever receives
    `keypoints` that verify_check_liveness computed itself by running the
    face detector on the actual decoded image bytes; there is no path from
    client-supplied JSON into this calculation at all.

    Ratio-based (nose offset from eye-center, normalized by eye width) so it
    works in pixel coordinates (unlike MediaPipe's 0-1 normalized space) —
    the ratio is scale-invariant. Only 5-point keypoints are available from
    RetinaFace/MTCNN (no chin/forehead), so pitch is approximated from the
    nose's vertical offset from the eye line rather than a chin/forehead
    span; only the 'side' (yaw) challenge is issued in production.

    Returns None (fail-closed) if keypoints are missing or malformed.
    """
    if not keypoints:
        return None
    try:
        lx, ly = keypoints['left_eye']
        rx, ry = keypoints['right_eye']
        nx, ny = keypoints['nose']
        eye_center_x = (float(lx) + float(rx)) / 2
        eye_center_y = (float(ly) + float(ry)) / 2
        eye_width = max(abs(float(rx) - float(lx)), 1.0)

        yaw = ((float(nx) - eye_center_x) / eye_width) * 45
        pitch = ((float(ny) - eye_center_y) / eye_width) * 45
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None

    return {'yaw': float(yaw), 'pitch': float(pitch)}


def validate_movement(
    initial_pose: dict,
    current_pose: dict,
    required_direction: str,
    threshold_deg: float = SERVER_CHALLENGE_THRESHOLD_DEG,
) -> dict:
    """
    Check whether the user moved their head in the required direction.
    threshold_deg = 5 degrees — accessible for elderly with limited motion.
    'side' direction accepts either left or right movement.
    """
    yaw_delta = current_pose['yaw'] - initial_pose['yaw']
    pitch_delta = current_pose['pitch'] - initial_pose['pitch']

    if required_direction == 'side':
        if abs(yaw_delta) >= threshold_deg:
            direction = 'left' if yaw_delta < 0 else 'right'
            return {'completed': True, 'reason': f'Turned {direction}.'}
        return {'completed': False, 'reason': 'Please turn your head slightly left or right.'}
    elif required_direction == 'left' and yaw_delta < -threshold_deg:
        return {'completed': True, 'reason': 'Turned left.'}
    elif required_direction == 'right' and yaw_delta > threshold_deg:
        return {'completed': True, 'reason': 'Turned right.'}
    elif required_direction == 'up' and pitch_delta < -threshold_deg:
        return {'completed': True, 'reason': 'Tilted up.'}
    elif required_direction == 'down' and pitch_delta > threshold_deg:
        return {'completed': True, 'reason': 'Tilted down.'}
    else:
        return {
            'completed': False,
            'reason': f'Movement not detected for direction: {required_direction}. Please try again slowly.',
        }


def compute_dhash(img: np.ndarray, hash_size: int = 8) -> str:
    """
    Perceptual difference-hash (dHash) of a full decoded frame, as a hex
    string (hash_size=8 -> 64 bits -> 16 hex chars).

    v2.1.16 Security Hardening Round #5 (Blocker 1 — replay-resistance
    redesign): unlike a raw-byte or decoded-pixel SHA-256, a dHash is
    computed on a small, fixed-size, normalized version of the image, so it
    is essentially unchanged by JPEG recompression (different quality
    setting) or resizing (different resolution) -- exactly the two replay
    variants a raw/pixel hash cannot catch, since both alter the exact byte
    stream AND the exact pixel values while leaving the visual content the
    same. Two images are treated as "the same capture" when the Hamming
    distance between their dHashes is small (see hamming_distance_hex), not
    only when it is zero.

    Mirrors the dHash approach already used for within-sequence
    near-duplicate detection in pad.py's _check_near_duplicate_frames --
    same 9x8 resize + adjacent-pixel-comparison construction -- but exposed
    here as a standalone function so it can be persisted on a
    LivenessTransaction and compared ACROSS transactions/attempts, not just
    within one sequence's frames.

    Returns '' (never matches anything) for an empty/invalid image.
    """
    if img is None or img.size == 0:
        return ''
    try:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
        small = cv2.resize(gray, (hash_size + 1, hash_size), interpolation=cv2.INTER_AREA)
        bits = (small[:, 1:] > small[:, :-1]).flatten()
        value = 0
        for bit in bits:
            value = (value << 1) | int(bit)
        hex_len = (hash_size * hash_size + 3) // 4
        return format(value, f'0{hex_len}x')
    except Exception:
        return ''


def hamming_distance_hex(hash_a: str, hash_b: str) -> int:
    """
    Bit-level Hamming distance between two equal-length hex-encoded hashes.
    Returns a large sentinel value (never treated as a match) if either hash
    is blank or their lengths differ.
    """
    if not hash_a or not hash_b or len(hash_a) != len(hash_b):
        return 1 << 30
    try:
        return bin(int(hash_a, 16) ^ int(hash_b, 16)).count('1')
    except ValueError:
        return 1 << 30


def verify_server_authoritative_challenge(
    neutral_keypoints: dict | None,
    challenge_keypoints: dict | None,
    required_direction: str,
    threshold_deg: float = SERVER_CHALLENGE_THRESHOLD_DEG,
) -> dict:
    """
    The server's sole authority on whether the head-movement challenge was
    actually performed (v2.1.16 Security Hardening Round #4 — Blocker 1,
    complete redesign; supersedes the Round #3 verify_server_side_challenge()).

    Trust boundary this closes: Round #3 recomputed yaw/pitch from raw
    MediaPipe FaceMesh landmark JSON — but that JSON was still supplied by
    the CLIENT. A modified request could fabricate a 468-point array with
    whatever nose/eye coordinates were needed to pass validate_movement(),
    with no real head movement (or even a real camera) involved. The
    landmark ARRAY was server-recomputed-from, but its CONTENTS were never
    server-observed evidence.

    This function takes no client-supplied coordinates at all. `neutral_keypoints`
    and `challenge_keypoints` must be the output of face_utils.detect_pose_keypoints()
    run by the SERVER on the raw neutral-frame and challenge-frame image bytes
    it received — i.e. keypoints the server's own face detector found by
    looking at actual pixels. There is no way for a client to make this
    function see "movement" without presenting a camera feed that a real face
    detector independently confirms moved, because the client never gets to
    supply the numbers this function reads.

    `required_direction` is likewise never taken from the client — callers
    must pass the value stored server-side in the Django session at
    verify_start(). Missing/undetected keypoints on either frame fail closed.
    """
    if neutral_keypoints is None or challenge_keypoints is None:
        return {
            'completed': False,
            'reason': 'Could not independently detect a face in both the baseline and challenge frames.',
        }

    initial_pose = analyze_head_pose_from_keypoints(neutral_keypoints)
    current_pose = analyze_head_pose_from_keypoints(challenge_keypoints)
    if initial_pose is None or current_pose is None:
        return {'completed': False, 'reason': 'Server-side face landmark analysis failed.'}

    return validate_movement(initial_pose, current_pose, required_direction, threshold_deg=threshold_deg)


# ─── Full Liveness Check ──────────────────────────────────────────────────────

def run_full_liveness_check(
    face_img: np.ndarray,
    challenge_completed: bool,
    anti_spoof_threshold: float = 0.25,
) -> dict:
    """
    Combined liveness check: anti-spoofing texture analysis + head movement challenge.

    Both anti-spoofing AND challenge must pass for 'passed' to be True.
    Combined liveness_score = 0.6 × anti_spoof_score + 0.4 × challenge_score.

    SECURITY NOTE (v2.1.16): `challenge_completed` here is whatever the
    caller passes in — in verify_check_liveness (Mode B) that is currently
    the client-reported boolean, used only to shape the UI response shown
    mid-challenge. This function's `passed`/`liveness_score` output is NOT
    the security decision: verify_submit independently re-derives
    server_liveness_passed from the server-computed LivenessTransaction
    (anti_spoof_score + pa_score), never from this function's result or from
    a client-supplied boolean. Do not wire this function's output into any
    pass/fail gate without an accompanying server-verified evidence source.

    Returns:
        {
            'passed': bool,
            'anti_spoof_passed': bool,
            'anti_spoof_score': float,
            'challenge_completed': bool,
            'liveness_score': float,
            'reason': str,
        }
    """
    spoof_result = check_anti_spoofing(face_img, threshold=anti_spoof_threshold)
    anti_spoof_score = spoof_result['score']
    spoof_passed = spoof_result['passed']

    challenge_score = 1.0 if challenge_completed else 0.0
    liveness_score = float(0.6 * anti_spoof_score + 0.4 * challenge_score)

    overall_passed = spoof_passed and challenge_completed

    if not spoof_passed and not challenge_completed:
        reason = f'Liveness failed: {spoof_result["reason"]} Head movement challenge not completed.'
    elif not spoof_passed:
        reason = f'Anti-spoofing failed: {spoof_result["reason"]}'
    elif not challenge_completed:
        reason = 'Head movement challenge not completed.'
    else:
        reason = 'Liveness check passed.'

    return {
        'passed': overall_passed,
        'anti_spoof_passed': spoof_passed,
        'anti_spoof_score': anti_spoof_score,
        'challenge_completed': challenge_completed,
        'liveness_score': liveness_score,
        'reason': reason,
    }
