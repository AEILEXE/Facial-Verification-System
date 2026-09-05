"""
Presentation Attack Detection (PAD) — FANS-C.

Heuristic-based detector for phone-screen, printed-photo, and replay attacks.
Does NOT require a trained CNN model; runs offline inside the PyInstaller bundle.

Four signal categories
──────────────────────
1. specular_glare      — screen reflections; bright saturated blobs on face region
2. screen_flatness     — unnaturally uniform texture across the face (prints/screens)
3. high_sharpness_low_texture — suspicious sharpness/texture ratio (phone display)
4. sequence_static     — landmark positions do not change across frames (photo/replay)

Score convention: higher = more suspicious (0 = clean, 1 = definite attack).
Threshold: PHONE_SCREEN_SPOOF_THRESHOLD (default 0.40) in settings.py.

IMPORTANT: These are heuristics, not a trained CNN.  They raise the bar for
unsophisticated attacks (phone screen under office lighting, printed A4 photo)
but will not catch professional replay equipment or very high quality displays.
For high-security deployments, replace or supplement with a trained PAD model
such as Silent-Face-Anti-Spoofing or MiniFASNet.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import List

import numpy as np
import cv2

logger = logging.getLogger('verification')


@dataclass
class PresentationAttackResult:
    suspicious: bool = False
    score: float = 0.0
    flags: dict = field(default_factory=dict)
    reason: str = ''

    # Individual signal scores
    specular_glare: float = 0.0
    screen_flatness: float = 0.0
    sharpness_texture_ratio: float = 0.0
    sequence_static: float = 0.0
    # v2.1.11 — near-duplicate frame signal (photo held still / video loop)
    near_duplicate: float = 0.0


class PresentationAttackDetector:
    """Heuristic PAD detector for offline use inside the FANS-C installer."""

    # Default weights for combining signals into an overall score.
    # v2.1.11: raised screen_flatness and sharpness_texture so phone/screen
    # captures with smooth panels cross the suspicious threshold even when
    # the static-sequence signal is masked by replayed video (Issue 3).
    WEIGHTS = {
        'sequence_static':             0.70,
        'specular_glare_high':         0.80,
        'specular_glare_warn':         0.60,
        # v2.1.16 (Issue 8): weight for glare > 0.7 that lacks corroborating
        # screen_flatness -- kept low enough that isolated glare alone (e.g.
        # bright room lighting or glasses reflections on a real face) cannot
        # cross PHONE_SCREEN_SPOOF_THRESHOLD (0.40) by itself, while glare
        # backed by flatness still gets the full specular_glare_high weight.
        'specular_glare_uncorroborated': 0.35,
        'screen_flatness':             0.55,
        'sharpness_texture':           0.50,
    }

    def __init__(self, threshold: float = 0.40):
        self.threshold = threshold

    # ── Per-frame analysis ────────────────────────────────────────────────────

    def analyze(self, face_img: np.ndarray) -> PresentationAttackResult:
        """Analyze a single face crop for presentation attack signals."""
        result = PresentationAttackResult()
        if face_img is None or face_img.size == 0:
            result.suspicious = True
            result.score = 1.0
            result.reason = 'Empty frame.'
            return result

        glare = self._check_specular_glare(face_img)
        flatness = self._check_screen_flatness(face_img)
        sht = self._check_sharpness_texture_ratio(face_img)

        result.specular_glare = glare
        result.screen_flatness = flatness
        result.sharpness_texture_ratio = sht
        result.flags = {
            'specular_glare': glare,
            'screen_flatness': flatness,
            'sharpness_texture_ratio': sht,
        }

        # Composite score from single-frame signals
        score = 0.0
        # v2.1.16 (Issue 8): isolated high glare with no corroborating flatness
        # is also produced by bright ambient lighting, glasses reflections, or
        # an overexposed webcam on a real 3D face -- not just an actual screen
        # surface. Only award the high-confidence weight when flatness backs
        # it up (a genuine screen/photo is both glary AND unnaturally flat);
        # uncorroborated glare gets a weight too low to trigger denial on its
        # own, so it can no longer single-handedly false-reject a real user.
        if glare > 0.7 and flatness > 0.3:
            score = max(score, self.WEIGHTS['specular_glare_high'] * glare)
        elif glare > 0.7:
            score = max(score, self.WEIGHTS['specular_glare_uncorroborated'] * glare)
        elif glare > 0.4:
            score = max(score, self.WEIGHTS['specular_glare_warn'] * glare)
        if flatness > 0.6:
            score = max(score, self.WEIGHTS['screen_flatness'] * flatness)
        if sht > 0.6:
            score = max(score, self.WEIGHTS['sharpness_texture'] * sht)
        # v2.1.11 (Issue 3): when both screen_flatness and sharpness_texture are
        # elevated together, that is a strong signature of an LCD/phone screen.
        # Boost the score so the combination crosses the suspicious threshold
        # even if each signal alone would not.
        if flatness > 0.5 and sht > 0.5:
            combined = float(np.clip((flatness + sht) / 2.0, 0.0, 1.0))
            score = max(score, 0.75 * combined)

        result.score = float(np.clip(score, 0.0, 1.0))
        result.suspicious = result.score >= self.threshold
        result.reason = self._build_reason(result)
        return result

    def analyze_sequence(
        self,
        frames: List[np.ndarray],
        landmark_motion_ok: bool = False,
        landmark_motion_debug: dict = None,
    ) -> PresentationAttackResult:
        """
        Analyze a temporal sequence of face crops.
        Combines per-frame signals with inter-frame motion analysis.
        Falls back to single-frame analysis if fewer than 3 frames.

        v2.1.13 (Issue 1): `landmark_motion_ok=True` indicates the client's
        FaceMesh tracked clear head movement during the challenge. In that
        case the pixel-level near-duplicate and static-sequence signals are
        suppressed, because a real person making a brief turn-and-return can
        produce visually similar frames at sampling instants even though the
        head genuinely moved. Landmark motion is a stronger live-evidence
        signal than raw pixel diff; we let it override pixel-only false
        positives. The texture-based signals (glare, flatness, sharpness)
        remain in force — landmark motion does NOT loosen anti-photo or
        anti-screen gates.
        """
        if not frames:
            r = PresentationAttackResult(suspicious=True, score=1.0, reason='No frames provided.')
            return r

        if len(frames) < 3:
            return self.analyze(frames[-1])

        # Per-frame signals: take worst case across sequence
        per_frame = [self.analyze(f) for f in frames]
        max_glare   = max(r.specular_glare for r in per_frame)
        max_flat    = max(r.screen_flatness for r in per_frame)
        max_sht     = max(r.sharpness_texture_ratio for r in per_frame)

        # Inter-frame motion check: real faces have micro-motion; stills do not
        static_score = self._check_sequence_static(frames)
        # v2.1.11 — near-duplicate frame detection (replay/photo-held-still)
        dup_score = self._check_near_duplicate_frames(frames)

        # v2.1.13 — when FaceMesh landmarks confirm clear head movement, the
        # pixel-level near-duplicate / static-sequence signals are unreliable
        # for live users (a brief turn-and-return can look pixel-similar at
        # sampling instants). Suppress them to zero so a real person is not
        # falsely flagged as a phone/photo/replay attack. Texture-based PAD
        # signals (glare, flatness, sharpness) are unaffected.
        static_score_raw = static_score
        dup_score_raw = dup_score
        if landmark_motion_ok:
            static_score = 0.0
            dup_score = 0.0
            logger.info(
                '[PAD] Landmark motion confirmed (debug=%s); '
                'suppressing static_sequence raw=%.3f and near_duplicate raw=%.3f.',
                landmark_motion_debug or {}, static_score_raw, dup_score_raw,
            )

        result = PresentationAttackResult()
        result.specular_glare = max_glare
        result.screen_flatness = max_flat
        result.sharpness_texture_ratio = max_sht
        result.sequence_static = static_score
        result.near_duplicate = dup_score
        result.flags = {
            'specular_glare': max_glare,
            'screen_flatness': max_flat,
            'sharpness_texture_ratio': max_sht,
            'sequence_static': static_score,
            'near_duplicate': dup_score,
        }

        score = 0.0
        if static_score > 0.7:
            score = max(score, self.WEIGHTS['sequence_static'] * static_score)
        if dup_score > 0.5:
            # Near-duplicate frames are strong evidence of replay; weight 0.70.
            score = max(score, 0.70 * dup_score)
        # v2.1.16 (Issue 8): same corroboration requirement as analyze() above.
        if max_glare > 0.7 and max_flat > 0.3:
            score = max(score, self.WEIGHTS['specular_glare_high'] * max_glare)
        elif max_glare > 0.7:
            score = max(score, self.WEIGHTS['specular_glare_uncorroborated'] * max_glare)
        elif max_glare > 0.4:
            score = max(score, self.WEIGHTS['specular_glare_warn'] * max_glare)
        if max_flat > 0.6:
            score = max(score, self.WEIGHTS['screen_flatness'] * max_flat)
        if max_sht > 0.6:
            score = max(score, self.WEIGHTS['sharpness_texture'] * max_sht)
        # v2.1.11 (Issue 3): combined flatness + sharpness boost.
        if max_flat > 0.5 and max_sht > 0.5:
            combined = float(np.clip((max_flat + max_sht) / 2.0, 0.0, 1.0))
            score = max(score, 0.75 * combined)

        result.score = float(np.clip(score, 0.0, 1.0))
        result.suspicious = result.score >= self.threshold
        result.reason = self._build_reason(result)
        return result

    # ── Signal detectors ──────────────────────────────────────────────────────

    def _check_specular_glare(self, face_img: np.ndarray) -> float:
        """
        Detect specular highlights typical of screen reflections.
        Phone/LCD screens produce bright, saturated blobs when lit from the side.
        Returns 0..1; 0=no glare, 1=strong glare.
        """
        if face_img is None or face_img.size == 0:
            return 0.0
        hsv = cv2.cvtColor(face_img, cv2.COLOR_BGR2HSV)
        v_channel = hsv[:, :, 2]
        s_channel = hsv[:, :, 1]
        # High-value, low-saturation pixels are specular highlights
        glare_mask = (v_channel > 220) & (s_channel < 40)
        glare_fraction = float(np.mean(glare_mask))
        # Scale: 0.01 fraction → score 0.20; 0.05 fraction → score 1.0
        score = min(glare_fraction / 0.05, 1.0)
        return float(score)

    def _check_screen_flatness(self, face_img: np.ndarray) -> float:
        """
        Detect unnaturally uniform texture (printed photos, LCD screens).
        Real faces have varied micro-texture; screens/photos have very smooth gradients.
        Uses the variance of local texture blocks.
        Returns 0..1; higher = more suspicious.
        """
        if face_img is None or face_img.size == 0:
            return 0.0
        gray = cv2.cvtColor(face_img, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape
        if h < 32 or w < 32:
            return 0.0
        # Divide into 4×4 blocks and measure LBP variance in each block
        block_h, block_w = h // 4, w // 4
        variances = []
        for r in range(4):
            for c in range(4):
                block = gray[r * block_h:(r + 1) * block_h, c * block_w:(c + 1) * block_w]
                variances.append(float(block.var()))
        mean_var = float(np.mean(variances))
        # Real webcam face: mean block variance typically 100-600
        # Screen/print: often < 40 (very flat) or abnormally uniform across blocks
        if mean_var > 200:
            return 0.0
        elif mean_var < 20:
            return 0.9
        return float(np.clip(1.0 - mean_var / 200.0, 0.0, 1.0))

    def _check_sharpness_texture_ratio(self, face_img: np.ndarray) -> float:
        """
        Detect suspicious high-sharpness / low-natural-texture ratio.
        LCD screens are very sharp but lack the stochastic micro-texture of real skin.
        Returns 0..1; higher = more suspicious.
        """
        if face_img is None or face_img.size == 0:
            return 0.0
        gray = cv2.cvtColor(face_img, cv2.COLOR_BGR2GRAY)
        # Sharpness: Laplacian variance
        lap_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        # Texture: local standard deviation
        kernel = np.ones((7, 7), np.float32) / 49
        local_mean = cv2.filter2D(gray.astype(np.float32), -1, kernel)
        local_sq   = cv2.filter2D(gray.astype(np.float32) ** 2, -1, kernel)
        local_std  = np.sqrt(np.maximum(local_sq - local_mean ** 2, 0))
        mean_local_std = float(local_std.mean())

        if mean_local_std < 1e-6:
            return 1.0
        ratio = lap_var / (mean_local_std * 100.0)
        # Real face: ratio typically 0.3–3; phone screen: can be 5–20+
        suspicious = float(np.clip((ratio - 3.0) / 17.0, 0.0, 1.0))
        return suspicious

    def _check_sequence_static(self, frames: List[np.ndarray]) -> float:
        """
        Detect static images / replays by measuring optical flow between frames.
        A still photo or looping video has near-zero motion across all frames.
        Returns 0..1; higher = more suspicious (more static).

        v2.1.11 (Issue 3): widened the "low motion" band so phone screens that
        flicker slightly between video frames also score as suspicious
        (< 0.5 pixel diff → flagged). The "real movement" floor stays at
        > 3.0 so genuine head turns still produce a clean score.
        """
        if len(frames) < 2:
            return 0.0
        motion_scores = []
        prev_gray = cv2.cvtColor(frames[0], cv2.COLOR_BGR2GRAY)
        for i in range(1, len(frames)):
            curr_gray = cv2.cvtColor(frames[i], cv2.COLOR_BGR2GRAY)
            diff = cv2.absdiff(prev_gray, curr_gray)
            motion = float(diff.mean())
            motion_scores.append(motion)
            prev_gray = curr_gray
        mean_motion = float(np.mean(motion_scores))
        # Typical natural motion (even resting face): 0.5–5 pixel diff units
        # Static photo: < 0.1
        if mean_motion > 3.0:
            return 0.0
        elif mean_motion < 0.15:
            return 0.97
        elif mean_motion < 0.5:
            return float(np.clip(1.0 - mean_motion / 1.5, 0.6, 0.95))
        return float(np.clip(1.0 - mean_motion / 3.0, 0.0, 1.0))

    def _check_near_duplicate_frames(self, frames: List[np.ndarray]) -> float:
        """
        Detect when the sequence contains near-identical frames — a fingerprint
        of replayed video loops or a photo that was simply held still.

        Uses a small dHash-style perceptual hash per frame and counts the number
        of frame pairs whose Hamming distance is below the duplicate threshold.

        Returns 0..1; 1.0 = many duplicates (strong replay signal),
        0.0 = all frames visually distinct.
        """
        if len(frames) < 3:
            return 0.0
        try:
            hashes = []
            for f in frames:
                if f is None or f.size == 0:
                    continue
                g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
                small = cv2.resize(g, (9, 8), interpolation=cv2.INTER_AREA)
                # dHash: compare adjacent pixel pairs horizontally
                bits = (small[:, 1:] > small[:, :-1]).flatten()
                hashes.append(bits.astype(np.uint8))
            if len(hashes) < 3:
                return 0.0
            # Pairwise Hamming distance; values <= 4 (out of 64 bits) are near-duplicates.
            near_dup = 0
            total = 0
            for i in range(len(hashes)):
                for j in range(i + 1, len(hashes)):
                    total += 1
                    if int(np.sum(hashes[i] ^ hashes[j])) <= 4:
                        near_dup += 1
            if total == 0:
                return 0.0
            ratio = near_dup / total
            # Many duplicates → strong replay signal; some duplicates → mild signal
            if ratio >= 0.5:
                return 0.95
            if ratio >= 0.3:
                return 0.7
            if ratio >= 0.15:
                return 0.4
            return 0.0
        except Exception:
            return 0.0

    # ── Helpers ───────────────────────────────────────────────────────────────

    # Signals that indicate a hostile presentation attempt (replayed video,
    # held photo, actual screen/print surface) vs. signals that a real face
    # can trigger under ordinary environmental conditions (bright light,
    # glasses reflections, webcam overexposure). v2.1.16 (Issue 8) uses this
    # split to give users an accurate, differentiated denial message instead
    # of one generic "possible attack" bucket for both cases.
    ATTACK_LIKE_FLAGS = {
        'static sequence (no face motion)',
        'near-duplicate frames (possible video replay or held photo)',
        'abnormally flat texture',
        'high sharpness / low skin texture',
    }

    def _build_reason(self, result: PresentationAttackResult) -> str:
        if not result.suspicious:
            return 'No presentation attack detected.'
        flags = []
        if result.sequence_static > 0.7:
            flags.append('static sequence (no face motion)')
        if result.near_duplicate > 0.5:
            flags.append('near-duplicate frames (possible video replay or held photo)')
        if result.specular_glare > 0.7:
            flags.append('strong screen glare')
        elif result.specular_glare > 0.4:
            flags.append('possible screen glare')
        if result.screen_flatness > 0.6:
            flags.append('abnormally flat texture')
        if result.sharpness_texture_ratio > 0.6:
            flags.append('high sharpness / low skin texture')
        return f'Denied: possible phone screen or photo presentation attack ({", ".join(flags) or "composite score"}).'

    def classify_denial(self, result: PresentationAttackResult) -> str:
        """Returns 'attack' if an attack-like signal dominates the denial, or
        'environment' if only glare (no corroborating flatness/sharpness/
        motion signal) is driving it -- i.e. the isolated-glare case that is
        now weighted too low to deny on its own unless something else also
        crossed its own bar. Used to pick the right user-facing message."""
        if not result.suspicious:
            return 'environment'
        has_attack_signal = (
            result.sequence_static > 0.7
            or result.near_duplicate > 0.5
            or result.screen_flatness > 0.6
            or result.sharpness_texture_ratio > 0.6
        )
        return 'attack' if has_attack_signal else 'environment'
