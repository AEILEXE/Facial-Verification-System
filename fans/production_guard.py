"""
Production configuration guard — FANS-C.

v2.1.16 Security Hardening Round #5 (Blocker 4): the pre-existing startup
validation in settings.py only ever ran its hard-fail checks when
`not DEBUG` — meaning DEBUG itself (the single most important thing to get
right) was never independently checked, and a packaged production build
that somehow started with DEBUG=True would skip every other check too.

This module defines "production mode" independently of DEBUG:

    is_production_mode(debug, is_frozen) -> True if either:
      - is_frozen  (a PyInstaller-packaged fans_c.exe — literally what ships
        to a barangay LAN server; see fans/settings.py's BASE_DIR split for
        why `sys.frozen` is the deployment signal already used elsewhere in
        this codebase), or
      - not debug  (the pre-existing definition, preserved so a
        manually-deployed non-frozen production server with DEBUG=False
        keeps getting the same scrutiny it always has).

Extracted into a standalone module (rather than left inline in settings.py)
specifically so it can be unit-tested with synthetic parameter combinations
without reloading Django settings mid-test-run — settings.py is evaluated
once at process start and importlib.reload() on it is unreliable/unsafe.

Every check below is additive to the pre-existing SECRET_KEY /
EMBEDDING_ENCRYPTION_KEY / ALLOWED_HOSTS checks already in settings.py,
which are untouched.
"""

# Below this anti-spoof threshold, compute_texture_score()'s `score >=
# threshold` comparison is satisfied by almost any input (including a flat,
# textureless printed photo or screen), effectively disabling the check.
# The shipped installer template (dev/launcher.py _ENV_TEMPLATE) and this
# repository's own .env both use 0.15 as a deliberately-calibrated value for
# elderly users on webcam hardware — the floor here is set well below that
# so it only catches a threshold that has been driven toward zero (i.e.
# effectively disabled), not legitimate calibration.
MIN_SAFE_ANTI_SPOOF_THRESHOLD = 0.05

# v2.1.16 Final Hardening Patch (Codex NO-GO #1): same rationale as
# MIN_SAFE_ANTI_SPOOF_THRESHOLD above, applied to the phone/screen
# presentation-attack heuristic. The shipped default is 0.40; this floor
# only catches a threshold that has been driven toward zero (effectively
# disabling phone-screen/printed-photo detection), not legitimate tuning.
MIN_SAFE_PHONE_SCREEN_SPOOF_THRESHOLD = 0.10


def is_production_mode(debug: bool, is_frozen: bool) -> bool:
    return bool(is_frozen) or not bool(debug)


def collect_production_errors(
    *,
    debug: bool,
    is_frozen: bool,
    demo_mode: bool,
    liveness_required: bool,
    pad_required: bool,
    anti_spoof_threshold: float,
    save_liveness_debug_frames: bool,
    presentation_attack_review_or_deny: str,
    strict_presentation_attack_check: bool,
    phone_screen_spoof_threshold: float,
) -> list[str]:
    """
    Returns a list of human-readable error strings for unsafe settings.
    Callers should only treat these as fatal when is_production_mode(debug,
    is_frozen) is True — the same values are safe to leave as warnings (or
    ignore entirely) in ordinary development use.
    """
    errors: list[str] = []

    if debug and is_frozen:
        errors.append(
            'DEBUG=True in a packaged production build (frozen executable). '
            'DEBUG must be False for any deployed installation — it exposes stack '
            'traces, settings values, and SQL queries to end users. Set DEBUG=False in .env.'
        )

    if demo_mode:
        errors.append(
            'DEMO_MODE=True (Assisted Rollout Mode) is enabled in production. This '
            'lowers the face-match acceptance threshold below the full-enforcement '
            'value and is intended only for a supervised pilot rollout, never for a '
            'standard production deployment. Set DEMO_MODE=False in .env.'
        )

    if not liveness_required:
        errors.append(
            'LIVENESS_REQUIRED=False in production. This puts liveness checking in '
            'non-blocking Assisted Rollout Mode — a failed liveness/anti-spoof check '
            'no longer denies the verification, only records it. Set LIVENESS_REQUIRED=True '
            'in .env before deploying to production.'
        )

    if not pad_required:
        errors.append(
            'PAD_REQUIRED=False in production. This disables Presentation Attack '
            'Detection enforcement — a suspected phone-screen/printed-photo replay is '
            'only recorded, never denied. Set PAD_REQUIRED=True in .env before deploying '
            'to production.'
        )

    if anti_spoof_threshold < MIN_SAFE_ANTI_SPOOF_THRESHOLD:
        errors.append(
            f'ANTI_SPOOF_THRESHOLD={anti_spoof_threshold} is too low for production '
            f'(minimum {MIN_SAFE_ANTI_SPOOF_THRESHOLD}). A threshold this low accepts '
            'almost any image, including printed photos and screens, as a live face. '
            'Raise ANTI_SPOOF_THRESHOLD in .env.'
        )

    # v2.1.16 Final Hardening Patch (Codex NO-GO #1): PAD_REQUIRED=True alone
    # does not actually enforce anything — verification/views.py only denies
    # a suspected presentation attack when PAD_REQUIRED AND
    # STRICT_PRESENTATION_ATTACK_CHECK are both true AND
    # PRESENTATION_ATTACK_REVIEW_OR_DENY == 'deny' (see the gate in
    # verify_check_liveness: `if pa_suspicious and (pad_required and
    # strict_pad) and deny_action == 'deny'`). Any one of these three left in
    # its "soft" state silently turns PAD enforcement into logging-only,
    # regardless of what PAD_REQUIRED itself says. All three are checked
    # unconditionally (not only when pad_required is True) so production can
    # never start with a suspected phone-screen/printed-photo replay routed
    # to manual review or merely warned about instead of denied.
    if presentation_attack_review_or_deny != 'deny':
        errors.append(
            f"PRESENTATION_ATTACK_REVIEW_OR_DENY={presentation_attack_review_or_deny!r} in "
            "production. A suspected presentation attack (phone-screen/printed-photo replay) "
            "must be denied outright, not routed to manual review or merely logged — 'review' "
            "mode lets an attacker's capture continue toward a human reviewer instead of being "
            "blocked immediately. Set PRESENTATION_ATTACK_REVIEW_OR_DENY=deny in .env."
        )

    if not strict_presentation_attack_check:
        errors.append(
            'STRICT_PRESENTATION_ATTACK_CHECK=False in production. This silently turns '
            'PAD_REQUIRED=True into a no-op — a suspected presentation attack is recorded but '
            'never denied. Set STRICT_PRESENTATION_ATTACK_CHECK=True in .env.'
        )

    if phone_screen_spoof_threshold < MIN_SAFE_PHONE_SCREEN_SPOOF_THRESHOLD:
        errors.append(
            f'PHONE_SCREEN_SPOOF_THRESHOLD={phone_screen_spoof_threshold} is too low for '
            f'production (minimum {MIN_SAFE_PHONE_SCREEN_SPOOF_THRESHOLD}). A threshold this low '
            'accepts almost any capture, including a phone/screen replay, as clean. Raise '
            'PHONE_SCREEN_SPOOF_THRESHOLD in .env.'
        )

    if save_liveness_debug_frames:
        errors.append(
            'SAVE_LIVENESS_DEBUG_FRAMES=True in production. This writes captured '
            'biometric frames to disk for calibration and must never be enabled outside '
            'a controlled debugging session. Set SAVE_LIVENESS_DEBUG_FRAMES=False in .env.'
        )

    return errors
