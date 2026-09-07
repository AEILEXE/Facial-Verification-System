"""
Verification views — FANS-C (FaceNet-Based Verification System).

This module implements the core stipend verification workflow:

1. verify_select     — Staff searches for a beneficiary by name or ID.
2. verify_start      — Loads the camera capture page; initialises the session with a
                       random liveness challenge and claimant type (beneficiary or representative).
3. verify_check_liveness — AJAX endpoint called immediately after the staff clicks
                       "Capture & Verify". Runs server-side anti-spoofing (texture analysis)
                       and face quality check. Result is logged on every attempt.
                       Does NOT gate the face match — that decision is made by the client
                       using the risk-based logic in verify.js.
4. verify_submit     — Main verification endpoint. Receives the captured frame plus
                       liveness data from the client, runs FaceNet face matching, and
                       returns a decision: verified / manual_review / retry / fallback.

Risk-Based Liveness Strategy
──────────────────────────────
The head-movement liveness challenge is NOT shown for every verification (it would frustrate
elderly beneficiaries). Instead, verify.js triggers the challenge only when a risk condition
is detected: low anti-spoof score, poor image quality, a retry attempt, or a representative
claim. Backend liveness logging is always on regardless of the visible challenge.

Decision Flow (verify_submit)
──────────────────────────────
  strict mode (LIVENESS_REQUIRED=True) + liveness failed  →  denied immediately
  face match score >= threshold                            →  verified (+ ClaimRecord created)
  score in review band [threshold * 0.85, threshold)      →  manual_review
  score < review band AND retries remain                  →  retry (new challenge)
  retries exhausted                                        →  fallback (ID-based verification)
  possible lookalike (another beneficiary within
    LOOKALIKE_BAND of the target score)                    →  escalated to manual_review

Multi-template matching (compare_with_all_embeddings) uses the best score across all
stored FaceEmbedding / AdditionalFaceEmbedding records for the beneficiary. This helps
seniors whose appearance has changed since initial registration.
"""
import json
import base64
import hashlib
import uuid
import logging
import threading
from django.db import IntegrityError, transaction
from django.shortcuts import render, redirect, get_object_or_404

logger = logging.getLogger('verification')
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods, require_POST
from django.views.decorators.cache import never_cache
from django.http import JsonResponse
from django.utils import timezone
from django.conf import settings as django_settings

from beneficiaries.models import Beneficiary
from .models import (
    FaceEmbedding, VerificationAttempt, SystemConfig, StipendEvent,
    AdditionalFaceEmbedding, FaceUpdateLog,
    FaceUpdateRequest, ManualVerificationRequest,
    ClaimRecord, SpecialClaimRequest,
    RepresentativeFaceEmbedding, UserFaceEmbedding,
    LivenessTransaction, LivenessEvidenceReservation,
    EvaluationDataset, EvaluationTrial,
)
from beneficiaries.models import Representative
from .face_utils import (
    process_face_for_verification,
    process_face_for_registration,
    compare_with_all_embeddings,
    compare_with_stored,
    load_image_from_bytes,
    detect_and_align_face,
    detect_pose_keypoints,
    is_using_mock_model,
    get_model_load_error,
    encrypt_embedding,
    check_duplicate_face,
    decrypt_embedding,
    decide_base_outcome,
    get_embedding,
    check_face_quality,
    apply_quality_override,
    check_lookalike_escalation,
    check_representative_beneficiary_fallback,
    cosine_similarity,
)
from .liveness import (
    run_full_liveness_check,
    get_random_challenge,
    check_anti_spoofing,
    check_frame_quality,
    verify_server_authoritative_challenge,
    analyze_head_pose_from_keypoints,
    SERVER_CHALLENGE_THRESHOLD_DEG,
    compute_dhash,
    hamming_distance_hex,
)
from .pad import PresentationAttackDetector
from django.urls import reverse
from logs.models import AuditLog, Notification
from logs.notifications import notify_admins

# v2.1.16 Final Hardening Patch (Codex NO-GO #4 — perceptual-hash replay
# race): serializes the replay-evidence duplicate-scan + reservation-insert
# step in verify_check_liveness (see the `with _liveness_replay_lock:` block
# there and LivenessEvidenceReservation's docstring for the full rationale).
# A single in-process lock is sufficient because this app is a single
# Waitress process (threads=4 — see dev/launcher.py's _start_waitress()),
# not multiple worker processes; it is not a substitute for a DB-level
# primitive in a hypothetical multi-process deployment, but no such
# deployment exists for this app.
_liveness_replay_lock = threading.Lock()


def check_and_reserve_liveness_evidence(evidence_hash, evidence_pixel_hash, evidence_phash):
    """
    Atomically scan for replay evidence matching the given hashes AND, if
    none is found, reserve this evidence so a concurrent call sees it —
    v2.1.16 Final Hardening Patch (Codex NO-GO #4 — perceptual-hash replay
    race).

    The scan and the reservation insert must happen as a single atomic
    step. Without this, two concurrent requests replaying the same
    (transformed/recompressed) evidence could both pass the scan — neither
    has written anything yet — then both spend several seconds in the
    anti-spoof/PAD/embedding pipeline that follows in verify_check_liveness,
    and both reach LivenessTransaction.objects.create() successfully:
    evidence_hash/evidence_pixel_hash differ after transformation (so the DB
    unique constraint on those columns never fires), and evidence_phash has
    no unique constraint at all — it is compared by Hamming distance
    (near-duplicate), which a naive column-level uniqueness check cannot
    express. Holding _liveness_replay_lock across the scan AND the
    reservation insert closes that window: whichever caller gets here first
    reserves the evidence before the other's scan can run.

    Returns the replay layer string ('exact_bytes' / 'pixel_content' /
    'perceptual') if this evidence is a replay of something already seen,
    or None if it was successfully reserved as new evidence.
    """
    _phash_lookback = timezone.now() - timezone.timedelta(
        hours=getattr(django_settings, 'LIVENESS_REPLAY_LOOKBACK_HOURS', 72)
    )
    _phash_threshold = getattr(django_settings, 'LIVENESS_PHASH_HAMMING_THRESHOLD', 8)
    with _liveness_replay_lock:
        if (
            LivenessTransaction.objects.filter(evidence_hash=evidence_hash).exists()
            or LivenessEvidenceReservation.objects.filter(evidence_hash=evidence_hash).exists()
        ):
            return 'exact_bytes'
        if (
            LivenessTransaction.objects.filter(evidence_pixel_hash=evidence_pixel_hash).exists()
            or LivenessEvidenceReservation.objects.filter(evidence_pixel_hash=evidence_pixel_hash).exists()
        ):
            return 'pixel_content'

        _candidate_phashes = list(
            LivenessTransaction.objects.filter(created_at__gte=_phash_lookback)
            .exclude(evidence_phash='').values_list('evidence_phash', flat=True)[:5000]
        ) + list(
            LivenessEvidenceReservation.objects.filter(reserved_at__gte=_phash_lookback)
            .exclude(evidence_phash='').values_list('evidence_phash', flat=True)[:5000]
        )
        for _cand_phash in _candidate_phashes:
            if hamming_distance_hex(_cand_phash, evidence_phash) <= _phash_threshold:
                return 'perceptual'

        # Reserve NOW, still holding the lock, so a concurrent caller's scan
        # (above) sees this evidence as already claimed even though the
        # real LivenessTransaction for this request won't be created until
        # much later (it needs the resolved beneficiary, PAD score, and
        # embedding, none of which exist yet at this point in the request).
        try:
            LivenessEvidenceReservation.objects.create(
                evidence_hash=evidence_hash,
                evidence_pixel_hash=evidence_pixel_hash,
                evidence_phash=evidence_phash,
            )
        except IntegrityError:
            # Lost a same-instant race on the exact-match unique
            # constraints despite the lock+scan above (e.g. a legacy/
            # manually-created row) — the DB is the final authority; treat
            # it exactly like a detected replay.
            return 'exact_bytes'
        return None


def is_face_verification_valid(request):
    """Check if face verification is valid and not expired."""
    if not request.session.get('face_verified'):
        return False

    verified_at = request.session.get('face_verified_at')
    if not verified_at:
        return False

    expiration_seconds = getattr(django_settings, 'FACE_VERIFICATION_EXPIRATION_SECONDS', 600)  # 10 minutes default
    now = timezone.now().timestamp()
    if now - verified_at > expiration_seconds:
        # Expired
        request.session['face_verified'] = False
        request.session.pop('face_verified_at', None)
        try:
            AuditLog.log(
                action=AuditLog.ACTION_VERIFY,
                user=request.user if request.user.is_authenticated else None,
                details={
                    'outcome': 'verification_expired',
                    'username': request.user.username if request.user.is_authenticated else '',
                },
                request=request,
            )
        except Exception:
            logger.exception('Failed to log verification expiration')
        return False

    return True


def _challenge_display(direction: str) -> str:
    return {
        'left':  'Look LEFT — turn your head to the left',
        'right': 'Look RIGHT — turn your head to the right',
        'up':    'Look UP — raise your chin slightly',
        'down':  'Look DOWN — lower your chin slightly',
        'side':  'Move your head slightly to either side',
    }.get(direction, 'Slowly move your head')


def _get_demo_mode():
    return getattr(django_settings, 'DEMO_MODE', False)


def _get_liveness_required():
    return getattr(django_settings, 'LIVENESS_REQUIRED', True)


def _camera_verification_allowed(request) -> bool:
    """
    Camera-based verification requires HTTPS.

    Returns True when the request arrived over a secure connection.
    In DEBUG mode, plain HTTP is also allowed so developers can test
    locally without certificates.  In production (DEBUG=False) plain
    HTTP is always rejected — the browser blocks camera access anyway
    and we must not accept face/liveness data that may have been
    tampered in transit.
    """
    if request.is_secure():
        return True
    if getattr(django_settings, 'DEBUG', False):
        return True
    return False


def _http_camera_blocked_response():
    """JSON error returned when a camera verification POST arrives over HTTP."""
    return JsonResponse(
        {
            'success': False,
            'error': (
                'Camera verification requires a secure HTTPS connection. '
                'Please access the system at https://fans-barangay.local'
            ),
            'http_fallback': True,
        },
        status=403,
    )


@login_required
@require_http_methods(['GET', 'POST'])
def face_verify(request):
    """Protected endpoint for user face verification outside the login flow."""
    if request.method == 'GET':
        return render(request, 'verification/face_verify.html')

    image = request.FILES.get('image')
    if not image:
        return JsonResponse({
            'success': False,
            'score': 0.0,
            'error': 'Missing image upload. Please attach a face photo under the "image" field.',
        }, status=400)

    upload_bytes = image.read()
    result = process_face_for_verification(upload_bytes)

    if result.get('model_unavailable'):
        # SYSTEM availability failure, not a failed/rejected verification —
        # do not log this as a verification attempt outcome.
        logger.error(
            'Face recognition model unavailable for user %s: %s',
            request.user.username, result.get('error'),
        )
        request.session['face_verified'] = False
        return JsonResponse({
            'success': False,
            'score': 0.0,
            'error': result.get('error'),
            'system_unavailable': True,
        }, status=503)

    if not result['success']:
        details = {
            'username': request.user.username,
            'outcome': 'verification_precheck_failed',
            'reason': result.get('error', 'Unknown failure'),
        }
        try:
            AuditLog.log(
                action=AuditLog.ACTION_VERIFY,
                user=request.user,
                details=details,
                request=request,
            )
        except Exception:
            logger.exception('Failed to log verification attempt')

        request.session['face_verified'] = False
        return JsonResponse({
            'success': False,
            'score': 0.0,
            'error': result.get('error', 'Face verification failed.'),
        }, status=400)

    try:
        stored_embedding = request.user.user_face_embedding.embedding_data
    except (UserFaceEmbedding.DoesNotExist, AttributeError):
        message = 'No stored face enrollment found for your account. Contact an administrator to enroll your face data.'
        logger.warning('Face verification attempted without enrolled embedding for user %s', request.user.username)
        request.session['face_verified'] = False
        return JsonResponse({
            'success': False,
            'score': 0.0,
            'error': message,
        }, status=404)

    compare = compare_with_stored(result['embedding'], stored_embedding)
    score = float(compare.get('score', 0.0)) if compare.get('success') else 0.0
    threshold = getattr(django_settings, 'VERIFICATION_THRESHOLD', 0.75)
    verified = compare.get('success', False) and score >= threshold

    details = {
        'username': request.user.username,
        'outcome': 'verified' if verified else 'rejected',
        'score': round(score, 4),
        'threshold': threshold,
        'error': compare.get('error', '') if not verified else '',
    }
    try:
        AuditLog.log(
            action=AuditLog.ACTION_VERIFY,
            user=request.user,
            details=details,
            request=request,
        )
    except Exception:
        logger.exception('Failed to log face verification attempt')

    request.session['face_verified'] = verified
    if verified:
        request.session['face_verified_at'] = timezone.now().timestamp()
        # Redirect to stored path or default
        redirect_to = request.session.pop('post_verify_redirect', reverse('beneficiaries:dashboard'))
        return JsonResponse({
            'success': True,
            'score': round(score, 4),
            'threshold': threshold,
            'redirect': redirect_to,
        })
    return JsonResponse({
        'success': False,
        'score': round(score, 4),
        'threshold': threshold,
        'error': details.get('error', ''),
    })


def _create_claim_record(
    beneficiary,
    stipend_event,
    claimant_type,
    representative,
    claimed_by,
    verification_attempt,
    *,
    status=None,
    verification_method=None,
    approved_by=None,
    approved_at=None,
    notes='',
    is_special_additional=False,
    payout_remarks='',
):
    """Create a ClaimRecord with payout-tracking fields populated.

    For released claims (status='claimed'): snapshots stipend_event.amount, sets
    released_by/released_at to claimed_by/now, and assigns a reference_number.
    For pending claims: leaves released_* blank and reference_number empty —
    they are filled in by the HB approval path when the claim is finalized."""
    status = status or ClaimRecord.STATUS_CLAIMED
    method = verification_method or ClaimRecord.VERIFY_FACE
    now = timezone.now()
    is_released = (status == ClaimRecord.STATUS_CLAIMED)
    amount = stipend_event.amount if (is_released and stipend_event) else 0
    reference_number = (
        ClaimRecord.generate_reference_number(stipend_event=stipend_event, when=now)
        if is_released else ''
    )
    # Nested atomic() -> Django issues a SAVEPOINT here (every call site invokes
    # this helper from inside its own outer transaction.atomic()). If the
    # UniqueConstraint (unique_claimed_per_beneficiary_event) rejects this
    # INSERT because a concurrent transaction committed a claim for the same
    # (beneficiary, stipend_event) first, only this savepoint rolls back —
    # the caller's outer transaction (e.g. an already-saved MVR approval or
    # attempt decision) is left intact and can still commit. IntegrityError
    # propagates to the caller, which must catch it and record the truthful
    # "concurrent duplicate" outcome rather than creating a second claim.
    with transaction.atomic():
        claim = ClaimRecord.objects.create(
            beneficiary=beneficiary,
            stipend_event=stipend_event,
            claimant_type=claimant_type,
            representative=representative,
            claimed_by=claimed_by,
            verification_attempt=verification_attempt,
            status=status,
            approved_by=approved_by,
            approved_at=approved_at,
            notes=notes,
            is_special_additional=is_special_additional,
            amount=amount,
            reference_number=reference_number,
            verification_method=method,
            released_by=claimed_by if is_released else None,
            released_at=now if is_released else None,
            payout_remarks=payout_remarks,
        )
    if status == ClaimRecord.STATUS_PENDING_APPROVAL:
        notify_admins(
            category=Notification.CATEGORY_APPROVAL_REQUIRED,
            title='Claim awaiting approval',
            message=f'{beneficiary.full_name} — no active payout event, awaiting President approval.',
            url=reverse('verification:pending_claim_review', args=[claim.pk]),
            dedupe_key=f'claim_pending:{claim.pk}',
        )
    return claim


def _lock_event_for_finalization(stipend_event):
    """Shared Phase B.5 finalization gate — call immediately before creating a
    ClaimRecord, inside the same transaction.atomic() block as that creation.

    Re-fetches stipend_event with select_for_update() (so a concurrent
    deactivation/approval-revocation can't race the check — see
    StipendEvent.check_claim_eligible_now for the TOCTOU rationale) and
    returns (locked_event, eligible, reason). Callers must use the returned
    locked_event — not the possibly-stale instance passed in — for the
    ClaimRecord's stipend_event FK.
    """
    locked_event = StipendEvent.objects.select_for_update().get(pk=stipend_event.pk)
    eligible, reason = locked_event.check_claim_eligible_now()
    return locked_event, eligible, reason


def _payout_blocked_message(reason: str) -> str:
    return (
        f'Identity verification completed, but the stipend payout was not '
        f'released because {StipendEvent.describe_claim_ineligible_reason(reason)}.'
    )


# ─── Verification Selection ───────────────────────────────────────────────────

@login_required
def verify_select(request):
    from django.db.models import Q
    query = request.GET.get('q', '')
    beneficiaries = []
    if query:
        # Single query with OR conditions — faster than unioning four separate querysets.
        beneficiaries = (
            Beneficiary.objects
            .filter(
                Q(last_name__icontains=query) |
                Q(first_name__icontains=query) |
                Q(beneficiary_id__icontains=query) |
                Q(senior_citizen_id__icontains=query),
                status=Beneficiary.STATUS_ACTIVE,
            )
            .distinct()
            .order_by('last_name', 'first_name')
            .select_related('face_embedding')
            .prefetch_related('representatives__face_embedding')
        )

    # Informational only (nothing is bound to a verification/claim from this
    # page) — show every currently-open event rather than silently picking
    # one, since more than one can be legitimately valid at once (Phase B.3).
    open_events = StipendEvent.get_open_events_now()

    # Show upcoming events for context in the sidebar
    from datetime import timedelta
    from django.utils import timezone as _tz
    today = _tz.localdate()
    upcoming_events = StipendEvent.objects.filter(
        is_active=True, date__gte=today, date__lte=today + timedelta(days=30)
    ).order_by('date')[:3]

    return render(request, 'verification/verify_select.html', {
        'beneficiaries': beneficiaries,
        'query': query,
        'open_events': open_events,
        'upcoming_events': upcoming_events,
        'today': today,
    })


# ─── Verify Start ─────────────────────────────────────────────────────────────

@login_required
def verify_start(request, pk):
    """
    Load the camera capture page for a specific beneficiary.

    Responsibilities:
    - Guard against ineligible beneficiaries (inactive, deceased, pending).
    - Guard against birthday-bonus events for ineligible birth months.
    - Prevent duplicate claims for the same stipend event.
    - Route representative claims (verifies the representative's face, not the beneficiary's).
    - Generate a unique session ID and random liveness challenge.
    - Pass require_liveness_challenge to the template so JS knows whether the
      head-movement challenge is always required (True for rep claims) or risk-based
      (False for self-claims — JS decides dynamically from anti-spoof + quality signals).
    """
    beneficiary = get_object_or_404(
        Beneficiary.objects.select_related('face_embedding'),
        pk=pk,
    )

    # Guard: only active beneficiaries can claim
    if not beneficiary.is_eligible_to_claim:
        from django.contrib import messages
        if beneficiary.duplicate_review_required:
            messages.error(
                request,
                f'{beneficiary.full_name} has an unresolved duplicate-face conflict '
                'and cannot be verified until an administrator reviews it in the '
                'Duplicate Face Review queue.'
            )
        else:
            messages.error(
                request,
                f'{beneficiary.full_name} is not eligible to claim '
                f'(status: {beneficiary.get_status_display()}). '
                'Inactive, deceased, or pending beneficiaries cannot claim a stipend.'
            )
        return redirect('verification:verify_select')

    claimant_type = request.GET.get('claimant', VerificationAttempt.CLAIMANT_BENEFICIARY)
    if claimant_type not in (VerificationAttempt.CLAIMANT_BENEFICIARY, VerificationAttempt.CLAIMANT_REPRESENTATIVE):
        claimant_type = VerificationAttempt.CLAIMANT_BENEFICIARY

    # Detect active stipend event(s) using payout window (respects time
    # window). ClaimRecord uniqueness is scoped to (beneficiary, event), not
    # beneficiary alone, so a beneficiary may legitimately hold separate
    # claimed payouts for two different events open at once (e.g. a Regular
    # Monthly Stipend and a Birthday Bonus both scheduled today) — nothing in
    # the model prevents or resolves that as a conflict. When more than one
    # event is simultaneously valid, silently binding whichever one a
    # creation-order tiebreak happens to prefer would risk attaching a real
    # payout to the wrong event, so the operator must explicitly choose
    # (Phase B.3) rather than the system guessing.
    open_events = StipendEvent.get_open_events_now()
    if len(open_events) > 1:
        chosen_id = (request.GET.get('event') or '').strip()
        active_event = next((ev for ev in open_events if str(ev.pk) == chosen_id), None)
        if active_event is None:
            return render(request, 'verification/verify_choose_event.html', {
                'beneficiary': beneficiary,
                'open_events': open_events,
                'claimant_type': claimant_type,
                'rep_id': request.GET.get('rep_id', ''),
            })
    else:
        active_event = open_events[0] if open_events else None

    # Birthday bonus eligibility check
    if (active_event
            and active_event.event_type == StipendEvent.EVENT_TYPE_BIRTHDAY
            and claimant_type == VerificationAttempt.CLAIMANT_BENEFICIARY):
        if not active_event.is_beneficiary_eligible(beneficiary):
            from django.contrib import messages
            messages.error(
                request,
                f'{beneficiary.full_name} is not eligible for this Birthday Bonus event. '
                f'Only beneficiaries born in {active_event.date.strftime("%B")} are eligible.'
            )
            return redirect('verification:verify_select')

    # Duplicate claim guard
    if active_event:
        already_claimed = ClaimRecord.objects.filter(
            beneficiary=beneficiary,
            stipend_event=active_event,
            status=ClaimRecord.STATUS_CLAIMED,
        ).exists()
        if already_claimed:
            from django.contrib import messages
            messages.warning(
                request,
                f'{beneficiary.full_name} has already claimed the stipend for '
                f'"{active_event.title}". To request an additional claim, '
                'go to the beneficiary profile and submit a Special Claim Request.'
            )
            return redirect('beneficiaries:beneficiary_detail', pk=beneficiary.pk)

    # Representative claim → biometric face verification (NOT ID-only)
    if claimant_type == VerificationAttempt.CLAIMANT_REPRESENTATIVE:
        # If camera is blocked (HTTP mode), show the block card immediately — there
        # is no point validating the representative before staff can switch to HTTPS.
        if not _camera_verification_allowed(request):
            return render(request, 'verification/verify_capture.html', {
                'beneficiary': beneficiary,
                'representative': None,
                'session_id': '',
                'challenge': '',
                'challenge_display': '',
                'claimant_type': claimant_type,
                'liveness_required': True,
                'demo_mode': False,
                'require_liveness_challenge': True,
                'active_event': active_event,
                'is_birthday_event': False,
                'using_mock': False,
                'model_load_error': None,
                'no_event_block': active_event is None,
                'http_camera_blocked': True,
            })
        from django.contrib import messages
        rep_id_param = request.GET.get('rep_id')
        qs = beneficiary.representatives.filter(is_active=True).select_related('face_embedding')
        if rep_id_param:
            try:
                rep = qs.get(pk=rep_id_param)
            except (Representative.DoesNotExist, Exception):
                messages.error(request, 'The specified representative was not found or is not active.')
                return redirect('beneficiaries:beneficiary_detail', pk=beneficiary.pk)
        else:
            rep = qs.first()

        if not rep:
            messages.error(
                request,
                'No active representative registered for this beneficiary. '
                'Add a representative and register their face on the beneficiary profile.'
            )
            return redirect('beneficiaries:beneficiary_detail', pk=beneficiary.pk)

        if not rep.has_face_data:
            messages.error(
                request,
                f'{rep.full_name} is registered as representative but has no face data. '
                'Register their face before verifying.'
            )
            return redirect('verification:register_rep_face', pk=beneficiary.pk, rep_pk=rep.pk)

        session_id = str(uuid.uuid4())
        challenge = get_random_challenge()
        request.session['verification_session'] = {
            'beneficiary_id': str(pk),
            'representative_id': str(rep.id),
            'session_id': session_id,
            'attempt_number': 1,
            'challenge': challenge,
            'claimant_type': claimant_type,
            'stipend_event_id': str(active_event.id) if active_event else None,
            # v2.1.16 Security Hardening Round #5 (Blocker 1 — timestamp
            # window binding): when this attempt's challenge was minted, so
            # verify_check_liveness can refuse to issue a LivenessTransaction
            # for a challenge that has sat unused far longer than a real
            # capture flow ever would.
            'challenge_issued_at': timezone.now().isoformat(),
        }
        liveness_required = _get_liveness_required()
        demo_mode = _get_demo_mode()
        using_mock = is_using_mock_model()
        # Representative claims always require the visible liveness challenge — higher-risk
        # since a third party is claiming on the beneficiary's behalf.
        require_liveness_challenge = True
        no_event_block = active_event is None
        http_camera_blocked = not _camera_verification_allowed(request)
        return render(request, 'verification/verify_capture.html', {
            'beneficiary': beneficiary,
            'representative': rep,
            'session_id': session_id,
            'challenge': challenge,
            'challenge_display': _challenge_display(challenge),
            'claimant_type': claimant_type,
            'liveness_required': liveness_required,
            'demo_mode': demo_mode,
            'require_liveness_challenge': require_liveness_challenge,
            'active_event': active_event,
            'is_birthday_event': False,
            'using_mock': using_mock,
            'model_load_error': get_model_load_error() if using_mock else None,
            'no_event_block': no_event_block,
            'http_camera_blocked': http_camera_blocked,
        })

    # Early-return for HTTP mode: show the "Camera Unavailable" card with the
    # "Open Secure HTTPS" button before checking face embedding. Staff on HTTP
    # cannot use the camera at all, so the embedding check is premature — they
    # need to switch to HTTPS first.
    if not _camera_verification_allowed(request):
        return render(request, 'verification/verify_capture.html', {
            'beneficiary': beneficiary,
            'representative': None,
            'session_id': '',
            'challenge': '',
            'challenge_display': '',
            'claimant_type': claimant_type,
            'liveness_required': True,
            'demo_mode': False,
            'require_liveness_challenge': False,
            'active_event': active_event,
            'is_birthday_event': False,
            'using_mock': False,
            'model_load_error': None,
            'no_event_block': active_event is None,
            'http_camera_blocked': True,
        })

    # Beneficiary face scan — requires an embedding
    if not hasattr(beneficiary, 'face_embedding'):
        from django.contrib import messages
        messages.error(request, 'No face embedding found for this beneficiary. Please complete face registration first.')
        return redirect('verification:verify_select')

    session_id = str(uuid.uuid4())
    challenge = get_random_challenge()
    request.session['verification_session'] = {
        'beneficiary_id': str(pk),
        'session_id': session_id,
        'attempt_number': 1,
        'challenge': challenge,
        'claimant_type': claimant_type,
        'stipend_event_id': str(active_event.id) if active_event else None,
        # v2.1.16 Security Hardening Round #5 (Blocker 1 — timestamp window
        # binding): see the representative-claim branch above for rationale.
        'challenge_issued_at': timezone.now().isoformat(),
    }

    liveness_required = _get_liveness_required()
    demo_mode = _get_demo_mode()

    using_mock = is_using_mock_model()
    is_birthday_event = (
        active_event and active_event.event_type == StipendEvent.EVENT_TYPE_BIRTHDAY
    )
    # Beneficiary self-claims use the risk-based fast path: the liveness challenge
    # is only triggered by the client when a risk condition is detected (low anti-spoof
    # score, poor image quality, or a retry attempt). Pre-set to False here; JS decides.
    require_liveness_challenge = False
    no_event_block = active_event is None
    http_camera_blocked = not _camera_verification_allowed(request)
    return render(request, 'verification/verify_capture.html', {
        'beneficiary': beneficiary,
        'session_id': session_id,
        'challenge': challenge,
        'challenge_display': _challenge_display(challenge),
        'claimant_type': claimant_type,
        'liveness_required': liveness_required,
        'demo_mode': demo_mode,
        'require_liveness_challenge': require_liveness_challenge,
        'active_event': active_event,
        'is_birthday_event': is_birthday_event,
        'using_mock': using_mock,
        'model_load_error': get_model_load_error() if using_mock else None,
        'no_event_block': no_event_block,
        'http_camera_blocked': http_camera_blocked,
    })


# ─── Liveness Check ───────────────────────────────────────────────────────────

def _challenge_is_stale(session_data: dict) -> bool:
    """
    True if verification_session['challenge_issued_at'] is older than
    LIVENESS_CHALLENGE_MAX_AGE_SECONDS. Returns False (not stale) when the
    key is absent — legacy sessions / test fixtures built without it are not
    blocked, matching the same backward-compatible exemption pattern used
    elsewhere for fields added in this hardening round.
    """
    issued_at_str = session_data.get('challenge_issued_at')
    if not issued_at_str:
        return False
    try:
        from datetime import datetime as _dt
        issued_at = _dt.fromisoformat(issued_at_str)
    except (TypeError, ValueError):
        return False
    max_age = getattr(django_settings, 'LIVENESS_CHALLENGE_MAX_AGE_SECONDS', 180)
    return (timezone.now() - issued_at).total_seconds() > max_age


@login_required
@require_POST
def verify_check_liveness(request):
    """
    Server-side liveness + PAD check — called after the head-movement challenge completes.

    SECURITY ARCHITECTURE — Liveness-Proof-Bound Verification
    ──────────────────────────────────────────────────────────
    Mode A (challenge_completed=False):
      Quick pre-challenge check. Runs anti-spoof + quality only.
      Returns anti_spoof_score to guide the JS challenge flow.
      Does NOT issue a LivenessTransaction.

    Mode B (challenge_completed=True, frames provided):
      Full liveness proof.  Called after the challenge is completed.
      Runs, in order: a byte-identical replay check against prior TX
      evidence, anti-spoof on the neutral frame, the sequence-frame-count
      gate, PAD on the sequence, server-authoritative movement validation
      (detect_pose_keypoints() re-detects the face on the raw neutral and
      challenge frame bytes itself — see verify_server_authoritative_challenge()
      in liveness.py; client-supplied challenge_completed/movement/landmark
      fields are never trusted for this decision), and finally the FaceNet
      embedding.  Only if ALL of these pass does it create a
      LivenessTransaction and return a tx_token.

      verify_submit MUST supply this tx_token.  It uses the stored
      liveness-frame embedding for identity matching, preventing any
      face that was not present during the challenge from passing.

    Every JSON response includes: success, passed, tx_token, error, reason,
    debug_stage, face_detected, anti_spoof_score, liveness_score, pa_score,
    embedding_created.  Never returns passed=True with tx_token=null.
    """
    if not _camera_verification_allowed(request):
        return _http_camera_blocked_response()

    _t_start = timezone.now().timestamp()

    def _full_fail(stage, error, reason=None, **extra):
        """Structured failure response — always includes all required fields."""
        msg = reason or error
        logger.warning('[LIVENESS] FAIL stage=%s error=%s', stage, error)
        resp = {
            'success': False,
            'passed': False,
            'tx_token': None,
            'error': error,
            'reason': msg,
            'debug_stage': stage,
            'face_detected': False,
            'anti_spoof_score': 0.0,
            'liveness_score': 0.0,
            'pa_score': 0.0,
            'embedding_created': False,
        }
        resp.update(extra)
        return JsonResponse(resp)

    try:
        data = json.loads(request.body)
        image_data = data.get('image', '')
        challenge_completed = bool(data.get('challenge_completed', False))
        # Sequence frames for PAD (small JPEGs captured ~600 ms apart during challenge)
        sequence_frames_b64 = data.get('frames', [])
        # Neutral/frontal frame captured BEFORE head-movement challenge.
        # Used for anti-spoof gating and FaceNet embedding (not the turned proof frame).
        neutral_image_data = data.get('neutral_image', '')
        # v2.1.16 Security Hardening Round #4 (Blocker 1 — complete redesign):
        # the client may still send `baseline_landmarks`/`challenge_landmarks`
        # (legacy MediaPipe FaceMesh JSON, kept for frontend backward-compat)
        # but the server no longer reads them for anything — client-supplied
        # coordinate data can be fabricated with no real camera movement
        # behind it. The movement decision below is derived entirely from
        # detect_pose_keypoints() run by the server on the raw neutral/proof
        # frame image bytes it decoded itself. See verify_server_authoritative_challenge().
        # v2.1.11 (Issue 10) — optional client-reported movement metrics so the
        # backend can include them in the audit log. These are advisory only —
        # the server still enforces sequence_frames + PAD + anti-spoof gates.
        client_movement = data.get('movement', {}) or {}
        try:
            client_peak_yaw = float(client_movement.get('peak_yaw_delta', 0.0))
        except (TypeError, ValueError):
            client_peak_yaw = 0.0
        try:
            client_peak_pitch = float(client_movement.get('peak_pitch_delta', 0.0))
        except (TypeError, ValueError):
            client_peak_pitch = 0.0
        try:
            client_movement_threshold = float(client_movement.get('threshold', 0.0))
        except (TypeError, ValueError):
            client_movement_threshold = 0.0
        try:
            client_face_lost_count = int(client_movement.get('face_lost_count', 0))
        except (TypeError, ValueError):
            client_face_lost_count = 0
        try:
            client_challenge_duration_ms = int(client_movement.get('challenge_duration_ms', 0))
        except (TypeError, ValueError):
            client_challenge_duration_ms = 0

        if not image_data:
            logger.warning('[LIVENESS] No image received in request.')
            return _full_fail('no_image', 'No image received. Please retry.')

        logger.info(
            '[LIVENESS] check_liveness: challenge_completed=%s frames=%d '
            'peak_yaw=%.2f peak_pitch=%.2f movement_threshold=%.2f face_lost=%d duration_ms=%d',
            challenge_completed, len(sequence_frames_b64),
            client_peak_yaw, client_peak_pitch, client_movement_threshold,
            client_face_lost_count, client_challenge_duration_ms,
        )

        if ',' in image_data:
            image_data = image_data.split(',')[1]

        try:
            image_bytes = base64.b64decode(image_data)
        except Exception as dec_err:
            logger.warning('[LIVENESS] Image base64 decode failed: %s', dec_err)
            return _full_fail('image_decode_failed', f'Image decode failed: {dec_err}')

        logger.debug('[LIVENESS_TIMING] decode=%.3fs', timezone.now().timestamp() - _t_start)

        try:
            img = load_image_from_bytes(image_bytes)
        except Exception as load_err:
            logger.warning('[LIVENESS] Image load failed: %s', load_err)
            return _full_fail('image_load_failed', f'Image load failed: {load_err}')

        try:
            face_img = detect_and_align_face(img)
        except ValueError as e:
            logger.info('[LIVENESS] No face detected: %s', e)
            return JsonResponse({
                'success': True,
                'passed': False,
                'tx_token': None,
                'face_detected': False,
                'anti_spoof_score': 0.0,
                'liveness_score': 0.0,
                'pa_score': 0.0,
                'error': str(e),
                'reason': str(e),
                'debug_stage': 'no_face_detected',
                'embedding_created': False,
            })

        logger.debug('[LIVENESS_TIMING] face_detect=%.3fs', timezone.now().timestamp() - _t_start)

        anti_spoof_threshold = getattr(django_settings, 'ANTI_SPOOF_THRESHOLD', 0.25)
        _t_liveness = timezone.now().timestamp()
        liveness_result = run_full_liveness_check(
            face_img=face_img,
            challenge_completed=challenge_completed,
            anti_spoof_threshold=anti_spoof_threshold,
        )
        logger.debug(
            '[LIVENESS_TIMING] liveness=%.3fs anti_spoof=%.3f passed=%s',
            timezone.now().timestamp() - _t_liveness,
            liveness_result['anti_spoof_score'], liveness_result['passed'],
        )

        from .face_utils import check_face_quality
        quality = check_face_quality(face_img)

        base_response = {
            'success': True,
            'face_detected': True,
            'passed': liveness_result['passed'],
            'anti_spoof_passed': liveness_result.get('anti_spoof_passed', False),
            'anti_spoof_score': liveness_result['anti_spoof_score'],
            'liveness_score': liveness_result['liveness_score'],
            'reason': liveness_result['reason'],
            'face_quality_ok': quality['ok'],
            'face_quality_score': round(quality['score'], 3),
            'face_quality_reason': quality['reason'],
            'presentation_attack_suspected': False,
            'presentation_attack_score': 0.0,
            'presentation_attack_flags': {},
            'pa_score': 0.0,
            'tx_token': None,
            'debug_stage': 'liveness_checked',
            'error': None,
            'embedding_created': False,
        }

        # ── Mode A: pre-challenge quick check, no TX issued ──────────────────
        if not challenge_completed:
            base_response['debug_stage'] = 'mode_a_complete'
            return JsonResponse(base_response)

        # ── Mode B: challenge completed — run PAD + issue LivenessTransaction ─
        base_response['debug_stage'] = 'mode_b_pad'

        # ── Neutral/frontal frame processing (Bug B fix) ──────────────────────
        # Process the neutral frame sent alongside the challenge/proof frame.
        # Anti-spoof and embedding are computed from the FRONTAL frame, not the
        # turned proof frame, to avoid false rejects from angled face captures.
        neutral_image_bytes = None
        neutral_face_img = None
        neutral_full_img = None
        neutral_anti_spoof_score = 0.0
        neutral_anti_spoof_passed = False

        if neutral_image_data:
            _nid = neutral_image_data
            if ',' in _nid:
                _nid = _nid.split(',')[1]
            try:
                neutral_image_bytes = base64.b64decode(_nid)
                _n_img = load_image_from_bytes(neutral_image_bytes)
                neutral_full_img = _n_img
                neutral_face_img = detect_and_align_face(_n_img)
                _n_spoof = check_anti_spoofing(neutral_face_img, threshold=anti_spoof_threshold)
                neutral_anti_spoof_score = float(_n_spoof['score'])
                neutral_anti_spoof_passed = bool(_n_spoof['passed'])
                from .face_utils import check_face_quality as _cfq_n
                _n_quality = _cfq_n(neutral_face_img)
                base_response['anti_spoof_score'] = neutral_anti_spoof_score
                base_response['anti_spoof_passed'] = neutral_anti_spoof_passed
                if _n_quality:
                    base_response['face_quality_ok'] = _n_quality['ok']
                    base_response['face_quality_score'] = round(_n_quality['score'], 3)
                    base_response['face_quality_reason'] = _n_quality['reason']
                logger.info(
                    '[LIVENESS_TX] Neutral frame: anti_spoof=%.3f passed=%s quality_ok=%s',
                    neutral_anti_spoof_score, neutral_anti_spoof_passed,
                    _n_quality['ok'] if _n_quality else 'unknown',
                )
            except ValueError:
                logger.warning('[LIVENESS_TX] No face in neutral_image — falling back to proof frame anti-spoof.')
            except Exception as _ne:
                logger.warning('[LIVENESS_TX] Neutral image processing error: %s', _ne)

        # ── Replay-evidence check (v2.1.16 Security Hardening Round #5 —
        # Blocker 1: complete redesign) ────────────────────────────────────
        # A single SHA-256 of the raw frame bytes (Round #4) only catches a
        # byte-for-byte identical replay. It does NOT catch a captured frame
        # that was re-saved with different JPEG quality (recompression),
        # scaled to a different resolution (resizing), or had its EXIF/
        # comment metadata stripped or edited — all three change the raw
        # byte stream (so evidence_hash never matches) while the visual
        # content an attacker is replaying stays identical or
        # near-identical. Three independent layers close this:
        #
        #   1. evidence_hash        — exact raw-byte SHA-256 (Round #4).
        #                              Catches byte-for-byte replay.
        #   2. evidence_pixel_hash  — SHA-256 of the DECODED pixel arrays.
        #                              Decoding strips all metadata, so this
        #                              catches metadata-only edits and
        #                              lossless re-saves the raw-byte hash
        #                              misses, with zero false-positive risk
        #                              (exact equality, not a threshold).
        #   3. evidence_phash       — dHash perceptual hash, compared by
        #                              Hamming distance against recent
        #                              transactions. Normalizes away
        #                              resolution and tolerates the small
        #                              pixel deltas from JPEG recompression,
        #                              catching the two replay variants
        #                              layers 1-2 cannot.
        #
        # A genuine live capture is essentially never byte-identical NOR
        # pixel-identical NOR perceptually near-identical to any other
        # capture — sensor noise, autoexposure drift, and actual head
        # movement between captures differ every time a frame is grabbed.
        # All three checks run BEFORE any expensive detection/embedding work.
        _evidence_hash = hashlib.sha256(
            (neutral_image_bytes or b'') + b'||' + image_bytes
        ).hexdigest()
        _evidence_pixel_hash = hashlib.sha256(
            (neutral_full_img.tobytes() if neutral_full_img is not None else b'')
            + b'||' + img.tobytes()
        ).hexdigest()
        _evidence_phash = (
            (compute_dhash(neutral_full_img) if neutral_full_img is not None else '0' * 16)
            + compute_dhash(img)
        )

        # v2.1.16 Final Hardening Patch (Codex NO-GO #4 — perceptual-hash
        # replay race): see check_and_reserve_liveness_evidence()'s
        # docstring for why the scan and the reservation of this evidence
        # must happen as a single atomic (locked) step rather than a plain
        # exists()-then-create() check.
        _replay_layer = check_and_reserve_liveness_evidence(
            _evidence_hash, _evidence_pixel_hash, _evidence_phash,
        )

        if _replay_layer:
            _replay_msg = (
                'This capture matches evidence already used for a previous liveness check. '
                'Please retry with a fresh camera capture.'
            )
            base_response['passed'] = False
            base_response['debug_stage'] = 'replay_detected'
            base_response['reason'] = _replay_msg
            base_response['error'] = _replay_msg
            logger.warning(
                '[LIVENESS_TX] Replay detected (%s): evidence_hash=%s pixel_hash=%s phash=%s',
                _replay_layer, _evidence_hash[:12], _evidence_pixel_hash[:12], _evidence_phash[:12],
            )
            return JsonResponse(base_response)

        # Anti-spoof gate: use neutral frontal score where available
        _eff_anti_spoof_score = (
            neutral_anti_spoof_score if neutral_face_img is not None
            else liveness_result['anti_spoof_score']
        )
        _eff_anti_spoof_passed = (
            neutral_anti_spoof_passed if neutral_face_img is not None
            else liveness_result.get('anti_spoof_passed', False)
        )
        if not _eff_anti_spoof_passed:
            _spoof_fail = (
                f'Anti-spoofing failed on frontal frame '
                f'(score {_eff_anti_spoof_score:.3f} < threshold {anti_spoof_threshold:.3f}). '
                'Present a clear, live face directly at the camera and retry.'
            )
            base_response['passed'] = False
            base_response['debug_stage'] = 'neutral_antispoof_failed'
            base_response['reason'] = _spoof_fail
            base_response['error'] = _spoof_fail
            logger.warning(
                '[LIVENESS_TX] Anti-spoof gate FAILED on %s frame: score=%.3f threshold=%.3f',
                'neutral' if neutral_face_img is not None else 'proof',
                _eff_anti_spoof_score, anti_spoof_threshold,
            )
            return JsonResponse(base_response)

        # ── Sequence frame requirement (Bug E fix) ────────────────────────────
        # At least 3 valid sequence frames are required for adequate PAD analysis.
        # Fewer frames indicate a static photo/phone that cannot produce movement.
        _min_seq = 3
        if not sequence_frames_b64 or len(sequence_frames_b64) < _min_seq:
            _seq_fail = (
                f'Insufficient sequence frames for security check '
                f'({len(sequence_frames_b64 or [])} received, {_min_seq} required). '
                'Move your head clearly during the challenge and retry.'
            )
            base_response['passed'] = False
            base_response['debug_stage'] = 'insufficient_sequence_frames'
            base_response['reason'] = _seq_fail
            base_response['error'] = _seq_fail
            logger.warning(
                '[LIVENESS_TX] Sequence frames %d < minimum %d — blocking TX.',
                len(sequence_frames_b64 or []), _min_seq,
            )
            return JsonResponse(base_response)

        pa_score = 0.0
        pa_flags: dict = {}
        pa_suspicious = False
        pad_threshold = getattr(django_settings, 'PHONE_SCREEN_SPOOF_THRESHOLD', 0.40)
        pad_required = getattr(django_settings, 'PAD_REQUIRED', True)
        strict_pad = getattr(django_settings, 'STRICT_PRESENTATION_ATTACK_CHECK', True)

        # ── Server-authoritative movement pre-check (Issue 1, v2.1.17) ────────
        # detect_pose_keypoints() re-runs the server's OWN face detector on the
        # raw neutral/challenge frame bytes it decoded — the identical evidence
        # source verify_server_authoritative_challenge() uses further below, and
        # never client-supplied coordinates (unlike the v2.1.13 override this
        # replaces, which trusted the client's self-reported FaceMesh numbers
        # and was removed in the prior hardening pass for exactly that reason).
        # A confirmed yaw/pitch delta here is honest, server-observed evidence
        # that the head actually moved, so it is safe to feed into PAD's
        # `landmark_motion_ok` safety valve: real users completing the small,
        # accessible head-turn challenge (SERVER_CHALLENGE_THRESHOLD_DEG) were
        # otherwise being caught by the pixel-level sequence_static /
        # near_duplicate signals, which cannot distinguish "small real turn"
        # from "static replay" at this threshold. Texture-based PAD signals
        # (glare, flatness, sharpness) are NOT affected by this flag — only
        # the two pixel-motion signals are suppressed, exactly as designed in
        # PresentationAttackDetector.analyze_sequence(). The specific
        # challenge DIRECTION is still independently verified later before
        # any LivenessTransaction is issued, so a wrong-direction or absent
        # movement is still denied regardless of this pre-check's outcome.
        _neutral_keypoints = (
            detect_pose_keypoints(neutral_full_img) if neutral_full_img is not None else None
        )
        _challenge_keypoints = detect_pose_keypoints(img)
        _server_movement_confirmed = False
        _server_pose_delta = None
        if _neutral_keypoints is not None and _challenge_keypoints is not None:
            _pre_initial_pose = analyze_head_pose_from_keypoints(_neutral_keypoints)
            _pre_current_pose = analyze_head_pose_from_keypoints(_challenge_keypoints)
            if _pre_initial_pose is not None and _pre_current_pose is not None:
                _srv_yaw_delta = _pre_current_pose['yaw'] - _pre_initial_pose['yaw']
                _srv_pitch_delta = _pre_current_pose['pitch'] - _pre_initial_pose['pitch']
                _server_pose_delta = {
                    'yaw_delta': round(_srv_yaw_delta, 2),
                    'pitch_delta': round(_srv_pitch_delta, 2),
                }
                _server_movement_confirmed = (
                    abs(_srv_yaw_delta) >= SERVER_CHALLENGE_THRESHOLD_DEG
                    or abs(_srv_pitch_delta) >= SERVER_CHALLENGE_THRESHOLD_DEG
                )

        pad_detector = PresentationAttackDetector(threshold=pad_threshold)
        _t_pad = timezone.now().timestamp()
        seq_imgs = []
        for f_b64 in sequence_frames_b64[:8]:  # cap at 8 frames for performance
            try:
                raw = f_b64.split(',')[-1] if ',' in f_b64 else f_b64
                seq_img = load_image_from_bytes(base64.b64decode(raw))
                try:
                    seq_face = detect_and_align_face(seq_img)
                    seq_imgs.append(seq_face)
                except ValueError:
                    seq_imgs.append(seq_img)
            except Exception:
                continue

        # v2.1.16 (Security Hardening #1 — CRITICAL): the v2.1.13 "landmark
        # motion override" suppressed PAD's pixel-level sequence_static /
        # near_duplicate signals whenever the CLIENT claimed FaceMesh saw
        # enough yaw/pitch movement (client_movement.peak_yaw_delta /
        # peak_pitch_delta / mediapipe_available). Those numbers are computed
        # entirely in the browser and sent as plain JSON — a modified request
        # could fabricate them with no corroborating server-side evidence,
        # disabling the exact signals designed to catch a static photo or
        # looped-video replay, so that override was removed outright.
        # client_movement below is retained ONLY for audit/diagnostic
        # logging — it MUST NOT influence the PAD decision. The
        # `landmark_motion_ok` flag passed to analyze_sequence() below comes
        # from `_server_movement_confirmed` instead (computed above from
        # detect_pose_keypoints() run by the server on the actual frame
        # pixels) — a different, non-spoofable source, not a reinstatement
        # of client trust. Texture-based PAD signals (glare, flatness,
        # sharpness) are unaffected either way and remain in force.
        _landmark_motion_debug = {
            'mediapipe_available': bool(client_movement.get('mediapipe_available', False)),
            'peak_yaw_delta': round(client_peak_yaw, 2),
            'peak_pitch_delta': round(client_peak_pitch, 2),
            'face_lost_count': client_face_lost_count,
            'note': 'client-reported fields are advisory/audit only, not used for PAD decisioning',
            'server_pose_delta': _server_pose_delta,
            'server_movement_confirmed': _server_movement_confirmed,
        }

        if len(seq_imgs) >= 3:
            pa_result = pad_detector.analyze_sequence(
                seq_imgs,
                landmark_motion_ok=_server_movement_confirmed,
                landmark_motion_debug=_landmark_motion_debug,
            )
        else:
            pa_result = pad_detector.analyze(face_img)

        logger.info(
            '[LIVENESS_TIMING] pad=%.3fs suspicious=%s score=%.3f seq_with_face=%d '
            'client_reported_movement=%s',
            timezone.now().timestamp() - _t_pad, pa_result.suspicious, pa_result.score, len(seq_imgs),
            _landmark_motion_debug,
        )

        # Ensure pa_flags values are plain Python floats (JSON-serializable)
        pa_score = float(pa_result.score)
        pa_flags = {k: float(v) for k, v in pa_result.flags.items()}
        pa_suspicious = pa_result.suspicious

        base_response['presentation_attack_suspected'] = pa_suspicious
        base_response['presentation_attack_score'] = round(pa_score, 3)
        base_response['presentation_attack_flags'] = pa_flags
        base_response['pa_score'] = round(pa_score, 3)

        deny_action = getattr(django_settings, 'PRESENTATION_ATTACK_REVIEW_OR_DENY', 'deny')
        if pa_suspicious and (pad_required and strict_pad) and deny_action == 'deny':
            # v2.1.16 (Issue 8): differentiate an attack-like denial from an
            # environment-like one (isolated glare with no corroborating
            # attack signal) so real users under bright lighting/glasses
            # reflections get accurate, actionable guidance instead of being
            # told they're a suspected attacker.
            denial_class = pad_detector.classify_denial(pa_result)
            if denial_class == 'environment':
                pad_denial = (
                    'Unable to verify liveness due to lighting or camera conditions. '
                    'Please adjust your position and try again.'
                )
            else:
                pad_denial = (
                    'Possible presentation attack detected. Please use your live face '
                    'directly in front of the camera.'
                )
            base_response['passed'] = False
            base_response['reason'] = pad_denial
            base_response['error'] = pad_denial
            base_response['debug_stage'] = 'pad_denied'
            base_response['pad_denial_class'] = denial_class
            logger.warning(
                '[LIVENESS_TX] PAD blocked TX issuance: score=%.3f flags=%s class=%s detail=%s',
                pa_score, pa_flags, denial_class, pa_result.reason,
            )
            return JsonResponse(base_response)

        logger.info(
            '[LIVENESS_TX] Mode B gates passed: neutral_anti_spoof=%.3f seq_frames=%d pa_score=%.3f suspicious=%s',
            _eff_anti_spoof_score, len(seq_imgs), pa_score, pa_suspicious,
        )

        # ── Issue LivenessTransaction ──────────────────────────────────────────
        base_response['debug_stage'] = 'tx_creation'
        liveness_proof_required = getattr(django_settings, 'LIVENESS_PROOF_REQUIRED', True)
        tx_token = None
        _no_token_reason = None
        encrypted_emb = None
        session_data = None

        if not liveness_proof_required:
            _no_token_reason = 'LIVENESS_PROOF_REQUIRED=False: TX skipped (test/debug mode).'
            logger.info('[LIVENESS_TX] LIVENESS_PROOF_REQUIRED=False — TX skipped.')
        else:
            session_data = request.session.get('verification_session')
            if not session_data:
                _no_token_reason = (
                    'Verification session not found. '
                    'Please return to the beneficiary search page and start verification again.'
                )
                logger.warning(
                    '[LIVENESS_TX] No verification_session in Django session — TX cannot be issued.'
                )
            elif str(data.get('session_id', '') or '') != session_data.get('session_id'):
                # Stale-tab / session-collision guard (Follow-up Issue 32) — see
                # the matching check in verify_submit for the full rationale.
                # Refusing here also avoids issuing a LivenessTransaction bound
                # to whichever beneficiary now occupies the shared session.
                logger.warning(
                    '[LIVENESS_TX] Stale/mismatched session_id — submitted=%s current=%s. '
                    'Refusing TX issuance (likely a second verification was started '
                    'in another tab).',
                    data.get('session_id', '(none)'), session_data.get('session_id'),
                )
                _no_token_reason = (
                    'Verification session expired or was replaced by a newer '
                    'verification (e.g. started in another tab). Please refresh '
                    'and start again for the correct beneficiary.'
                )
                session_data = None
            elif _challenge_is_stale(session_data):
                # v2.1.16 Security Hardening Round #5 (Blocker 1 — timestamp
                # window binding): the challenge issued at verify_start has a
                # bounded lifetime independent of the LivenessTransaction's
                # own post-issuance expiry (LIVENESS_PROOF_EXPIRY_SECONDS,
                # which governs how long the TOKEN is usable by verify_submit
                # AFTER issuance). This gate instead bounds how long may
                # elapse between the server minting the challenge and the
                # proof frames actually arriving here, closing the window an
                # attacker would otherwise have to pre-stage frames captured
                # long before submitting them against a freshly started
                # session. Sessions with no recorded challenge_issued_at
                # (legacy/test fixtures) are not blocked by this check.
                _no_token_reason = (
                    'Liveness challenge expired. Please restart verification '
                    'for a fresh challenge.'
                )
                logger.warning(
                    '[LIVENESS_TX] Challenge issued_at too old — refusing TX issuance '
                    '(beneficiary=%s).', session_data.get('beneficiary_id'),
                )
                session_data = None
            else:
                beneficiary_id = session_data.get('beneficiary_id')
                claimant_type = session_data.get('claimant_type', 'beneficiary')
                rep_id = session_data.get('representative_id')
                stipend_event_id = session_data.get('stipend_event_id')
                challenge_dir = session_data.get('challenge', 'side')
                attempt_number = session_data.get('attempt_number', 1)
                expiry_secs = getattr(django_settings, 'LIVENESS_PROOF_EXPIRY_SECONDS', 120)

                logger.info(
                    '[LIVENESS_TX] Preparing TX: beneficiary=%s claimant=%s event=%s '
                    'challenge=%s attempt=%s expiry=%ds',
                    beneficiary_id, claimant_type, stipend_event_id,
                    challenge_dir, attempt_number, expiry_secs,
                )

                # ── Server-derived movement challenge validation (Blocker 1) ──────
                # `challenge_completed` above is a client-reported boolean with no
                # proof attached to it. `challenge_dir` is the direction the
                # SERVER assigned at verify_start() (session-stored, never
                # client-supplied). Movement evidence itself is likewise
                # server-only: detect_pose_keypoints() runs the server's own
                # face detector on the raw neutral/proof frame pixels it
                # decoded from the request body — no client-supplied
                # coordinate JSON is read here at all (see
                # verify_server_authoritative_challenge()'s docstring). A
                # request with no neutral frame, an undetectable face on
                # either frame, or real detected movement that doesn't match
                # the required direction is rejected here — before any
                # embedding is computed or LivenessTransaction is issued.
                # _neutral_keypoints / _challenge_keypoints were already computed
                # above (server-authoritative movement pre-check, ahead of the
                # PAD gate) — reused here rather than re-running face detection.
                _movement_result = verify_server_authoritative_challenge(
                    _neutral_keypoints, _challenge_keypoints, challenge_dir,
                )
                if not _movement_result['completed']:
                    _no_token_reason = (
                        f'Server could not verify the head movement challenge: '
                        f'{_movement_result["reason"]}'
                    )
                    logger.warning(
                        '[LIVENESS_TX] Server-side movement validation FAILED: '
                        'direction=%s reason=%s neutral_kp=%s challenge_kp=%s '
                        'client_claimed_completed=%s',
                        challenge_dir, _movement_result['reason'],
                        _neutral_keypoints is not None, _challenge_keypoints is not None,
                        challenge_completed,
                    )
                    base_response['debug_stage'] = 'movement_validation_failed'
                    base_response['passed'] = False
                    base_response['error'] = _no_token_reason
                    base_response['reason'] = _no_token_reason
                    base_response['tx_token'] = None
                    return JsonResponse(base_response)
                logger.info(
                    '[LIVENESS_TX] Server-side movement validation PASSED: '
                    'direction=%s reason=%s', challenge_dir, _movement_result['reason'],
                )

                # Compute FaceNet embedding from the NEUTRAL/FRONTAL frame (Bug B+C fix).
                # Using the frontal frame prevents false rejects from the turned/angled
                # challenge frame.  TX is BLOCKED if embedding fails — no empty-embedding TX.
                _emb_source = neutral_image_bytes if neutral_image_bytes is not None else image_bytes
                _emb_src_label = 'neutral' if neutral_image_bytes is not None else 'proof'
                liveness_embedding = None
                _t_emb = timezone.now().timestamp()
                try:
                    from .face_utils import get_embedding_only
                    emb_result = get_embedding_only(_emb_source)
                    liveness_embedding = emb_result.get('embedding') if emb_result.get('success') else None
                    if liveness_embedding is None:
                        _emb_fail = (
                            'Face embedding could not be computed from the frontal capture. '
                            'Ensure good lighting, face the camera directly, and retry.'
                        )
                        logger.warning(
                            '[LIVENESS_TX] Embedding FAILED (source=%s): success=%s error=%s — blocking TX.',
                            _emb_src_label, emb_result.get('success'), emb_result.get('error'),
                        )
                        base_response['debug_stage'] = 'embedding_failed'
                        base_response['passed'] = False
                        base_response['error'] = _emb_fail
                        base_response['reason'] = _emb_fail
                        base_response['embedding_created'] = False
                        return JsonResponse(base_response)
                    logger.info(
                        '[LIVENESS_TX] Embedding computed (source=%s): dim=%d in %.3fs.',
                        _emb_src_label, len(liveness_embedding), timezone.now().timestamp() - _t_emb,
                    )
                except Exception as emb_err:
                    _emb_exc = (
                        f'Embedding computation raised {type(emb_err).__name__}. '
                        'Check server logs and retry.'
                    )
                    logger.warning('[LIVENESS_TX] Embedding exception — blocking TX: %s', emb_err)
                    base_response['debug_stage'] = 'embedding_exception'
                    base_response['passed'] = False
                    base_response['error'] = _emb_exc
                    base_response['reason'] = _emb_exc
                    base_response['embedding_created'] = False
                    return JsonResponse(base_response)

                if liveness_embedding is not None:
                    try:
                        encrypted_emb = encrypt_embedding(liveness_embedding)
                    except Exception as enc_err:
                        logger.warning('[LIVENESS_TX] Embedding encryption failed: %s', enc_err)

                _t_tx = timezone.now().timestamp()
                try:
                    beneficiary_obj = Beneficiary.objects.get(pk=beneficiary_id)
                    rep_obj = None
                    if rep_id:
                        try:
                            rep_obj = Representative.objects.get(pk=rep_id)
                        except Representative.DoesNotExist:
                            logger.warning(
                                '[LIVENESS_TX] Representative pk=%s not found — proceeding without rep.', rep_id,
                            )
                    stipend_obj = None
                    if stipend_event_id:
                        try:
                            stipend_obj = StipendEvent.objects.get(pk=stipend_event_id)
                        except StipendEvent.DoesNotExist:
                            logger.warning('[LIVENESS_TX] StipendEvent pk=%s not found.', stipend_event_id)

                    # Store neutral frame anti-spoof score in TX for verify_submit gate check
                    _tx_anti_spoof = (
                        neutral_anti_spoof_score
                        if neutral_face_img is not None
                        else liveness_result['anti_spoof_score']
                    )
                    expiry_dt = timezone.now() + timezone.timedelta(seconds=expiry_secs)
                    # v2.1.16 Security Hardening Round #5 (Blocker 2): the
                    # unique constraints on evidence_hash/evidence_pixel_hash
                    # (see LivenessTransaction.Meta) make the DATABASE the
                    # final authority against a replay race, not the exists()
                    # pre-check above. create() must run inside its own
                    # atomic() savepoint so an IntegrityError from a lost race
                    # can be caught below without poisoning the surrounding
                    # transaction (required for this to work on PostgreSQL;
                    # harmless on SQLite).
                    with transaction.atomic():
                        tx = LivenessTransaction.objects.create(
                            beneficiary=beneficiary_obj,
                            claimant_type=claimant_type,
                            representative=rep_obj,
                            stipend_event=stipend_obj,
                            performed_by=request.user,
                            challenge_direction=challenge_dir,
                            attempt_number=attempt_number,
                            anti_spoof_score=_tx_anti_spoof,
                            liveness_score=liveness_result['liveness_score'],
                            pa_score=pa_score,
                            pa_flags=pa_flags,
                            embedding_data=encrypted_emb,
                            expires_at=expiry_dt,
                            evidence_hash=_evidence_hash,
                            evidence_pixel_hash=_evidence_pixel_hash,
                            evidence_phash=_evidence_phash,
                            session_id=session_data.get('session_id', ''),
                        )
                    tx_token = str(tx.token)
                    logger.info(
                        # v2.1.16 (Security Hardening Round #2, H-03): the
                        # token is a bearer credential for the (short) TX
                        # window — log only a truncated prefix, never the
                        # full value, matching the pattern already used for
                        # tx_token_str elsewhere in this file.
                        '[LIVENESS_TX] ISSUED token=%s… id=%s beneficiary=%s claimant=%s '
                        'anti_spoof=%.3f(neutral=%s) liveness=%.3f pa_score=%.3f near_dup=%s '
                        'static=%s seq_frames=%d peak_yaw=%.2f peak_pitch=%.2f face_lost=%d '
                        'duration_ms=%d has_embedding=%s expires=%s create=%.3fs',
                        tx_token[:8], tx.id, beneficiary_id, claimant_type,
                        _tx_anti_spoof, neutral_face_img is not None,
                        liveness_result['liveness_score'],
                        pa_score, round(pa_flags.get('near_duplicate', 0.0), 3),
                        round(pa_flags.get('sequence_static', 0.0), 3),
                        len(seq_imgs),
                        client_peak_yaw, client_peak_pitch, client_face_lost_count,
                        client_challenge_duration_ms,
                        encrypted_emb is not None, expiry_dt.isoformat(),
                        timezone.now().timestamp() - _t_tx,
                    )
                except Beneficiary.DoesNotExist:
                    _no_token_reason = (
                        f'Beneficiary (ID={beneficiary_id}) not found in database. '
                        'Please restart verification from the beneficiary search page.'
                    )
                    logger.error('[LIVENESS_TX] Beneficiary pk=%s not found — TX not issued.', beneficiary_id)
                except IntegrityError:
                    # v2.1.16 Security Hardening Round #5 (Blocker 2): lost
                    # the atomic race — a concurrent request already
                    # committed a LivenessTransaction with this exact
                    # evidence_hash/evidence_pixel_hash between our exists()
                    # pre-check and this INSERT. The database, not the
                    # earlier read, is the final authority: treat this
                    # exactly like a detected replay rather than silently
                    # issuing a second TX for the same evidence.
                    _no_token_reason = (
                        'This capture matches evidence already used for a previous liveness '
                        'check (detected concurrently). Please retry with a fresh camera capture.'
                    )
                    logger.warning(
                        '[LIVENESS_TX] Replay detected via DB unique-constraint race: '
                        'evidence_hash=%s pixel_hash=%s — a concurrent request already '
                        'claimed this evidence.',
                        _evidence_hash[:12], _evidence_pixel_hash[:12],
                    )
                except Exception as tx_err:
                    _no_token_reason = (
                        f'Liveness transaction creation failed ({type(tx_err).__name__}). '
                        'Check the server error log for details and retry.'
                    )
                    logger.exception('[LIVENESS_TX] TX creation failed: %s', tx_err)

        # ── Finalize response — never passed=True without tx_token ────────────
        base_response['tx_token'] = tx_token
        base_response['embedding_created'] = encrypted_emb is not None

        if tx_token:
            base_response['passed'] = True
            base_response['debug_stage'] = 'tx_created'
            base_response['error'] = None
            logger.info(
                '[LIVENESS_TX] Response OK: passed=True tx=%s embedding=%s total=%.3fs',
                tx_token[:8] + '…', encrypted_emb is not None,
                timezone.now().timestamp() - _t_start,
            )
        else:
            # CRITICAL: never return passed=True without a tx_token
            base_response['passed'] = False
            base_response['debug_stage'] = 'tx_not_issued'
            _error_msg = _no_token_reason or 'Liveness transaction could not be created. Please retry.'
            base_response['error'] = _error_msg
            # Replace generic liveness-pass reason so the frontend sees the real failure cause
            if not base_response.get('reason') or base_response['reason'] == 'Liveness check passed.':
                base_response['reason'] = _error_msg
            _ben_id = session_data.get('beneficiary_id') if session_data else 'unknown'
            logger.warning(
                '[LIVENESS_TX] No token issued: reason=%s beneficiary=%s '
                'anti_spoof=%.3f total=%.3fs',
                _no_token_reason, _ben_id,
                liveness_result['anti_spoof_score'],
                timezone.now().timestamp() - _t_start,
            )

        return JsonResponse(base_response)

    except Exception as e:
        logger.exception('verify_check_liveness unhandled exception: %s', e)
        return JsonResponse({
            'success': False,
            'passed': False,
            'tx_token': None,
            'error': 'Liveness check failed due to a server error. Please retry.',
            'reason': 'Server error during liveness check.',
            'debug_stage': 'server_exception',
            'face_detected': False,
            'anti_spoof_score': 0.0,
            'liveness_score': 0.0,
            'pa_score': 0.0,
            'embedding_created': False,
        })


# ─── Verify Submit ────────────────────────────────────────────────────────────

@login_required
@require_POST
def verify_submit(request):
    """
    Main verification endpoint — liveness-proof-bound identity matching.

    Security architecture:
    ──────────────────────
    verify_check_liveness (Mode B) must run FIRST and issue a LivenessTransaction
    token (tx_token).  This token binds the liveness-verified frame embedding to
    the subsequent identity match.

    Processing pipeline:
    1. Validate session + tx_token (required unless LIVENESS_PROOF_REQUIRED=False).
    2. Resolve LivenessTransaction: not expired, not used, matches session beneficiary.
    3. Retrieve the FaceNet embedding stored in the liveness transaction.
    4. If no liveness embedding: fall back to processing the submitted frame.
    5. Same-face consistency check: compare submitted frame against liveness embedding.
    6. Compare liveness embedding against stored beneficiary/rep embedding.
    7. Evaluate score, run lookalike detection.
    8. Persist VerificationAttempt + ClaimRecord (on pass).
    9. Mark LivenessTransaction as consumed (single-use enforcement).

    Decision outcomes:
      verified       — score >= threshold; ClaimRecord created.
      manual_review  — score in review band, or lookalike detected.
      retry          — score below threshold; retries remain.
      fallback       — retries exhausted; ID-based fallback.
      denied         — liveness proof missing/expired/reused, PAD suspicious,
                       same-face mismatch, or face processing error.
    """
    if not _camera_verification_allowed(request):
        return _http_camera_blocked_response()

    session_data = request.session.get('verification_session')
    if not session_data:
        return JsonResponse({'success': False, 'error': 'Verification session expired. Please start again.'})

    try:
        data = json.loads(request.body)
        image_data = data.get('image', '')
        challenge_completed = data.get('challenge_completed', False)
        liveness_score_client = float(data.get('liveness_score', 0.0))
        anti_spoof_score_client = float(data.get('anti_spoof_score', 0.0))
        liveness_passed_client = bool(data.get('liveness_passed', False))
        tx_token_str = data.get('tx_token', '')

        if not image_data:
            return JsonResponse({'success': False, 'error': 'No image provided.'})

        beneficiary_id = session_data['beneficiary_id']
        attempt_number = session_data.get('attempt_number', 1)
        session_id = session_data['session_id']
        claimant_type = session_data.get('claimant_type', VerificationAttempt.CLAIMANT_BENEFICIARY)
        stipend_event_id = session_data.get('stipend_event_id')

        # ── Stale-tab / session-collision guard (Follow-up Issue 32) ─────────
        # request.session is keyed on the login cookie, which is shared across
        # every browser tab. Starting verify_start() for a second beneficiary
        # in another tab overwrites verification_session — a still-open first
        # tab would otherwise submit its capture against whichever beneficiary
        # now occupies the (shared) session, silently mis-attributing the
        # match. The page embeds the session_id verify_start() generated for
        # IT specifically; if it no longer matches what's currently in the
        # session, this submission belongs to a superseded verification and
        # must be refused rather than evaluated against the current occupant.
        submitted_session_id = str(data.get('session_id', '') or '')
        if not submitted_session_id or submitted_session_id != session_id:
            logger.warning(
                '[VERIFY_SESSION] Stale/mismatched session_id on submit — '
                'submitted=%s current=%s beneficiary=%s. Refusing to evaluate '
                '(likely a second verification was started in another tab).',
                submitted_session_id or '(none)', session_id, beneficiary_id,
            )
            return JsonResponse({
                'success': False,
                'error': (
                    'Verification session expired or was replaced by a newer '
                    'verification (e.g. started in another tab). Please refresh '
                    'and start again for the correct beneficiary.'
                ),
            })

        beneficiary = get_object_or_404(Beneficiary, pk=beneficiary_id)
        threshold = SystemConfig.get_threshold()
        liveness_required = _get_liveness_required()
        demo_mode = _get_demo_mode()
        liveness_proof_required = getattr(django_settings, 'LIVENESS_PROOF_REQUIRED', True)
        same_face_threshold = getattr(django_settings, 'SAME_FACE_SEQUENCE_THRESHOLD', 0.30)

        # Resolve representative (for representative claims).
        # SECURITY: a representative claim must compare against the registered
        # representative's face. If no representative is bound to the session,
        # we MUST refuse to verify — falling back to the beneficiary's embedding
        # would let the senior's own face approve a representative claim.
        representative = None
        if claimant_type == VerificationAttempt.CLAIMANT_REPRESENTATIVE:
            rep_id = session_data.get('representative_id')
            if not rep_id:
                logger.warning(
                    '[VERIFY_REP] Representative claim has no representative_id in session — '
                    'beneficiary=%s. Refusing to compare against beneficiary face.',
                    beneficiary_id,
                )
                return JsonResponse({
                    'success': False,
                    'error': (
                        'Representative verification requires a selected representative. '
                        'Return to the beneficiary profile and start the representative '
                        'verification from the Representatives section.'
                    ),
                })
            try:
                representative = (
                    Representative.objects
                    .select_related('face_embedding')
                    .get(pk=rep_id, beneficiary=beneficiary, is_active=True)
                )
            except Representative.DoesNotExist:
                return JsonResponse({
                    'success': False,
                    'error': 'Representative not found or no longer active.',
                })
            if not representative.has_face_data:
                logger.warning(
                    '[VERIFY_REP] Representative %s has no face embedding — denying claim '
                    '(no beneficiary fallback).', representative.pk,
                )
                return JsonResponse({
                    'success': False,
                    'error': (
                        'Representative verification requires the registered '
                        'representative’s face data. This representative has no face '
                        'enrolled yet — register their face before verifying.'
                    ),
                })

            # Shared-representative review gate (added 2026-05-28).
            # A representative whose face matches another beneficiary's rep is
            # held pending admin review. Verification — and the resulting
            # payout — must remain BLOCKED until an admin explicitly approves.
            if representative.is_blocked_for_review:
                logger.warning(
                    '[VERIFY_REP] Representative %s shared_review_status=%s — '
                    'release BLOCKED pending admin review.',
                    representative.pk, representative.shared_review_status,
                )
                return JsonResponse({
                    'success': False,
                    'shared_rep_blocked': True,
                    'error': (
                        'This representative is linked to another beneficiary and '
                        'requires administrator review before release. Please wait '
                        'for admin approval.'
                    ),
                })

        if ',' in image_data:
            image_data = image_data.split(',')[1]
        image_bytes = base64.b64decode(image_data)

        # Resolve stipend event
        stipend_event = None
        if stipend_event_id:
            try:
                stipend_event = StipendEvent.objects.get(pk=stipend_event_id)
            except StipendEvent.DoesNotExist:
                pass

        if not stipend_event:
            return JsonResponse({
                'success': False,
                'error': (
                    'No active payout event. '
                    'Create or activate a payout event before processing stipend claims.'
                ),
                'no_event': True,
            }, status=400)

        # ── Liveness Transaction validation ──────────────────────────────────
        # The liveness transaction token must be present, valid, not expired,
        # not used, and bound to this session's beneficiary + claimant type.
        # No FaceNet identity comparison runs until this gate passes.
        liveness_tx = None
        liveness_tx_denial_reason = None

        # Structured diagnostic flags — logged regardless of outcome.
        _tx_diag = {
            'tx_token_present': bool(tx_token_str),
            'tx_found': False,
            'tx_valid': False,
            'tx_used': False,
            'tx_expired': False,
            'tx_claimant_match': None,
            'strict_liveness_required': liveness_proof_required,
            'final_block_reason': None,
        }

        if liveness_proof_required:
            if not tx_token_str:
                _tx_diag['final_block_reason'] = 'tx_token_missing'
                logger.warning(
                    '[VERIFY_TX] BLOCKED: tx_token absent — beneficiary=%s claimant=%s strict=%s',
                    beneficiary_id, claimant_type, liveness_proof_required,
                )
                liveness_tx_denial_reason = (
                    'Verification blocked: no valid server-issued liveness proof. '
                    'Complete the liveness challenge before submitting for verification.'
                )
            else:
                logger.info('[VERIFY_TX] token received: %s — beneficiary=%s.',
                            tx_token_str[:8] + '…', beneficiary_id)
                try:
                  # v2.1.16 (Security Hardening Round #3 — Blocker 2): the
                  # select_for_update() lookup and the atomic claim() below
                  # must run inside transaction.atomic() — select_for_update()
                  # requires an enclosing transaction, and this block is the
                  # only place that acquires/claims the token, so its scope is
                  # kept tight (closed before any expensive verification work
                  # — face embedding decrypt/compare, decision logic,
                  # ClaimRecord creation — begins below).
                  with transaction.atomic():
                    uuid.UUID(tx_token_str)  # raises ValueError for non-UUID strings
                    liveness_tx = LivenessTransaction.objects.select_for_update().get(
                        token=tx_token_str,
                        beneficiary=beneficiary,
                    )
                    _tx_diag['tx_found'] = True
                    _tx_diag['tx_used'] = liveness_tx.is_used
                    _tx_diag['tx_expired'] = liveness_tx.is_expired
                    _tx_diag['tx_claimant_match'] = (
                        str(liveness_tx.claimant_type) == str(claimant_type)
                    )

                    if liveness_tx.is_expired:
                        _tx_diag['final_block_reason'] = 'tx_expired'
                        logger.warning(
                            '[VERIFY_TX] BLOCKED: expired — id=%s expires=%s tx_present=%s '
                            'tx_found=%s tx_expired=%s strict=%s',
                            liveness_tx.pk, liveness_tx.expires_at,
                            _tx_diag['tx_token_present'], _tx_diag['tx_found'],
                            _tx_diag['tx_expired'], liveness_proof_required,
                        )
                        liveness_tx_denial_reason = (
                            'Verification blocked: no valid server-issued liveness proof. '
                            'Liveness proof has expired — please repeat the liveness challenge.'
                        )
                        liveness_tx = None
                    elif liveness_tx.is_used:
                        _tx_diag['final_block_reason'] = 'tx_already_used'
                        logger.warning(
                            '[VERIFY_TX] BLOCKED: already used — id=%s used_at=%s tx_present=%s '
                            'tx_found=%s tx_used=%s strict=%s',
                            liveness_tx.pk, liveness_tx.used_at,
                            _tx_diag['tx_token_present'], _tx_diag['tx_found'],
                            _tx_diag['tx_used'], liveness_proof_required,
                        )
                        liveness_tx_denial_reason = (
                            'Verification blocked: no valid server-issued liveness proof. '
                            'Liveness proof has already been used — each proof is single-use.'
                        )
                        liveness_tx = None
                    elif not _tx_diag['tx_claimant_match']:
                        _tx_diag['final_block_reason'] = 'tx_claimant_mismatch'
                        logger.warning(
                            '[VERIFY_TX] BLOCKED: claimant mismatch — token=%s session=%s '
                            'tx_present=%s tx_found=%s strict=%s',
                            liveness_tx.claimant_type, claimant_type,
                            _tx_diag['tx_token_present'], _tx_diag['tx_found'],
                            liveness_proof_required,
                        )
                        liveness_tx_denial_reason = (
                            'Verification blocked: no valid server-issued liveness proof. '
                            f'Liveness proof claimant type mismatch '
                            f'(token={liveness_tx.claimant_type}, session={claimant_type}).'
                        )
                        liveness_tx = None
                    elif liveness_tx.session_id and liveness_tx.session_id != session_id:
                        # v2.1.16 Security Hardening Round #5 (Blocker 3 —
                        # verification-attempt binding): a LivenessTransaction
                        # is bound to the verify_start session_id (a random,
                        # unguessable per-attempt UUID) that was active when
                        # it was issued. A token minted during one
                        # verification attempt must not be honored against a
                        # DIFFERENT attempt — even for the same beneficiary,
                        # claimant type, and operator — e.g. a second
                        # verify_start for the same beneficiary (new attempt,
                        # new session_id, possibly a different event/
                        # representative) must not accept a token left over
                        # from an earlier attempt. Legacy rows with no
                        # session_id recorded (pre-existing data, direct test
                        # fixtures) are not blocked by this check.
                        _tx_diag['final_block_reason'] = 'tx_attempt_mismatch'
                        logger.warning(
                            '[VERIFY_TX] BLOCKED: attempt/session mismatch — token_session=%s '
                            'current_session=%s tx_present=%s tx_found=%s strict=%s',
                            liveness_tx.session_id, session_id,
                            _tx_diag['tx_token_present'], _tx_diag['tx_found'],
                            liveness_proof_required,
                        )
                        liveness_tx_denial_reason = (
                            'Verification blocked: no valid server-issued liveness proof. '
                            'This liveness proof belongs to a different verification attempt.'
                        )
                        liveness_tx = None
                    elif (
                        liveness_tx.challenge_direction
                        and session_data.get('challenge')
                        and liveness_tx.challenge_direction != session_data.get('challenge')
                    ):
                        # v2.1.16 Final Hardening Patch (Codex NO-GO #2 —
                        # challenge direction binding): verify_submit's
                        # decision='retry' branch assigns a NEW
                        # session_data['challenge'] direction for the next
                        # attempt while keeping the SAME session_id (see the
                        # attempt_number/challenge reassignment a few hundred
                        # lines below). Without this check, a TX minted for
                        # an earlier, now-superseded challenge direction
                        # would still pass the session_id check above (it
                        # never changed) and be honored against an attempt
                        # the session has since moved on from. The random
                        # challenge direction only works as a proof of live,
                        # server-directed movement if the proof presented at
                        # submit time is for the direction the session
                        # CURRENTLY expects — a stale direction is exactly
                        # the kind of proof a pre-staged/replayed capture
                        # could satisfy. Legacy rows with no
                        # challenge_direction recorded, or a session with no
                        # 'challenge' key (direct test fixtures), are not
                        # blocked by this check.
                        _tx_diag['final_block_reason'] = 'tx_challenge_direction_mismatch'
                        logger.warning(
                            '[VERIFY_TX] BLOCKED: challenge direction mismatch — '
                            'token_direction=%s current_direction=%s tx_present=%s tx_found=%s '
                            'strict=%s',
                            liveness_tx.challenge_direction, session_data.get('challenge'),
                            _tx_diag['tx_token_present'], _tx_diag['tx_found'],
                            liveness_proof_required,
                        )
                        liveness_tx_denial_reason = (
                            'Verification blocked: no valid server-issued liveness proof. '
                            'This liveness proof was issued for a different liveness challenge.'
                        )
                        liveness_tx = None
                    elif liveness_tx.stipend_event_id is not None and liveness_tx.stipend_event_id != stipend_event.id:
                        # v2.1.16 Security Hardening Round #5 (Blocker 3 —
                        # stipend-event binding): a token issued while Event A
                        # was the session's active event must not be
                        # accepted for a claim against Event B, even for the
                        # same beneficiary/claimant/operator. Legacy rows
                        # issued with no stipend_event recorded (e.g. no
                        # event was active at issuance time) are not blocked
                        # by this check — verify_submit already refuses to
                        # proceed at all when no event resolves for the
                        # CURRENT session, so stipend_event here is always a
                        # concrete event.
                        _tx_diag['final_block_reason'] = 'tx_event_mismatch'
                        logger.warning(
                            '[VERIFY_TX] BLOCKED: stipend event mismatch — token_event=%s '
                            'current_event=%s tx_present=%s tx_found=%s strict=%s',
                            liveness_tx.stipend_event_id, stipend_event.id,
                            _tx_diag['tx_token_present'], _tx_diag['tx_found'],
                            liveness_proof_required,
                        )
                        liveness_tx_denial_reason = (
                            'Verification blocked: no valid server-issued liveness proof. '
                            'This liveness proof was issued for a different stipend event.'
                        )
                        liveness_tx = None
                    elif liveness_tx.representative_id is not None and liveness_tx.representative_id != (
                        representative.id if representative else None
                    ):
                        # v2.1.16 Security Hardening Round #5 (Blocker 3 —
                        # representative binding): a token issued for
                        # Representative A's challenge must not be accepted
                        # for a claim now resolving to Representative B (or to
                        # a plain beneficiary self-claim), even for the same
                        # beneficiary/operator. Legacy rows issued with no
                        # representative recorded are not blocked by this
                        # check.
                        _tx_diag['final_block_reason'] = 'tx_representative_mismatch'
                        logger.warning(
                            '[VERIFY_TX] BLOCKED: representative mismatch — token_rep=%s '
                            'current_rep=%s tx_present=%s tx_found=%s strict=%s',
                            liveness_tx.representative_id,
                            representative.id if representative else None,
                            _tx_diag['tx_token_present'], _tx_diag['tx_found'],
                            liveness_proof_required,
                        )
                        liveness_tx_denial_reason = (
                            'Verification blocked: no valid server-issued liveness proof. '
                            'This liveness proof was issued for a different representative.'
                        )
                        liveness_tx = None
                    elif liveness_tx.performed_by_id is not None and liveness_tx.performed_by_id != request.user.id:
                        # v2.1.16 (Security Hardening Round #2, H-03): a
                        # LivenessTransaction is bound to the officer who ran
                        # the challenge (performed_by, set at issuance). A
                        # submit from a DIFFERENT logged-in user with this
                        # token — e.g. a shared kiosk session or a leaked
                        # token — must not be accepted as that other
                        # officer's proof. Legacy rows with no performed_by
                        # (pre-existing data) are not blocked by this check.
                        _tx_diag['final_block_reason'] = 'tx_owner_mismatch'
                        logger.warning(
                            '[VERIFY_TX] BLOCKED: owner mismatch — token_owner=%s submitting_user=%s '
                            'tx_present=%s tx_found=%s strict=%s',
                            liveness_tx.performed_by_id, request.user.id,
                            _tx_diag['tx_token_present'], _tx_diag['tx_found'],
                            liveness_proof_required,
                        )
                        liveness_tx_denial_reason = (
                            'Verification blocked: no valid server-issued liveness proof. '
                            'This liveness proof was issued to a different user session.'
                        )
                        liveness_tx = None
                    else:
                        # v2.1.16 (Security Hardening Round #3 — Blocker 2):
                        # claim ownership of this token NOW, atomically,
                        # before any expensive verification work (face
                        # embedding decrypt/compare, decision logic,
                        # ClaimRecord creation) runs below. This is a
                        # conditional UPDATE (used_at IS NULL → now()) —
                        # the same atomic primitive consume() used to apply
                        # only at the very end of this view. A concurrent
                        # request that reached this same else-branch a
                        # moment earlier (and already committed its claim)
                        # causes this claim() to return False here.
                        if liveness_tx.claim():
                            _tx_diag['tx_valid'] = True
                            logger.info(
                                '[VERIFY_TX] VALID + CLAIMED: id=%s beneficiary=%s claimant=%s '
                                'tx_present=%s tx_found=%s tx_valid=%s strict=%s',
                                liveness_tx.pk, beneficiary_id, claimant_type,
                                _tx_diag['tx_token_present'], _tx_diag['tx_found'],
                                _tx_diag['tx_valid'], liveness_proof_required,
                            )
                        else:
                            _tx_diag['final_block_reason'] = 'tx_claim_race_lost'
                            _tx_diag['tx_used'] = True
                            logger.warning(
                                '[VERIFY_TX] BLOCKED: lost atomic claim race — id=%s '
                                'beneficiary=%s claimant=%s (concurrent request claimed '
                                'this token first).',
                                liveness_tx.pk, beneficiary_id, claimant_type,
                            )
                            liveness_tx_denial_reason = (
                                'Verification blocked: no valid server-issued liveness proof. '
                                'Liveness proof has already been used — each proof is single-use.'
                            )
                            liveness_tx = None
                except (LivenessTransaction.DoesNotExist, ValueError):
                    _tx_diag['final_block_reason'] = 'tx_not_found'
                    logger.warning(
                        '[VERIFY_TX] BLOCKED: not found — token=%s beneficiary=%s '
                        'tx_present=%s tx_found=%s strict=%s',
                        tx_token_str[:8] + '…', beneficiary_id,
                        _tx_diag['tx_token_present'], _tx_diag['tx_found'],
                        liveness_proof_required,
                    )
                    liveness_tx_denial_reason = (
                        'Verification blocked: no valid server-issued liveness proof. '
                        'Liveness proof not found or does not match this beneficiary — '
                        'complete the liveness challenge before submitting.'
                    )

        # ── Server-side anti-spoof recheck on submitted frame ────────────────
        # Even with a valid TX, we recheck the submitted frame so a stale/replayed
        # frame is caught by anti-spoof even if the TX is valid.
        try:
            _img = load_image_from_bytes(image_bytes)
            _face_for_spoof = detect_and_align_face(_img)
            spoof_check = check_anti_spoofing(
                _face_for_spoof,
                threshold=getattr(django_settings, 'ANTI_SPOOF_THRESHOLD', 0.25),
            )
            server_anti_spoof_score = float(spoof_check['score'])
            server_anti_spoof_passed = bool(spoof_check['passed'])
        except ValueError:
            server_anti_spoof_score = 0.0
            server_anti_spoof_passed = False

        # v2.1.16 (Security Hardening #1 — CRITICAL): `challenge_completed` is
        # a client-supplied boolean with no accompanying server-verified
        # proof — a modified request could send challenge_completed=true
        # with no evidence at all. Fail CLOSED: server_liveness_passed can
        # ONLY become True via the TX-verified branch below, which uses
        # anti_spoof_score/pa_score computed by the server at challenge time
        # and bound to a single-use LivenessTransaction token. This also
        # closes the gap where LIVENESS_PROOF_REQUIRED=False left liveness_tx
        # permanently None while liveness_required stayed True — previously
        # that combination fell back to trusting the client boolean alone;
        # now, with no server-verified evidence available, it fails closed.
        server_liveness_passed = False
        server_liveness_score = float(
            0.6 * server_anti_spoof_score + 0.4 * (1.0 if challenge_completed else 0.0)
        )

        # Use liveness TX scores if available (computed at challenge time — authoritative).
        # In strict mode liveness passes only when:
        #   TX anti_spoof_score >= ANTI_SPOOF_THRESHOLD  (anti-spoof gate)
        #   TX pa_score < PHONE_SCREEN_SPOOF_THRESHOLD   (PAD gate, unless warn-only)
        # Client values are kept for display/logging only.
        _tx_pad_block = False
        _tx_pad_reason = ''
        if liveness_tx:
            _anti_spoof_threshold = getattr(django_settings, 'ANTI_SPOOF_THRESHOLD', 0.25)
            _pad_threshold = getattr(django_settings, 'PHONE_SCREEN_SPOOF_THRESHOLD', 0.40)
            _pad_required = getattr(django_settings, 'PAD_REQUIRED', True)
            _pa_action = getattr(django_settings, 'PRESENTATION_ATTACK_REVIEW_OR_DENY', 'deny')
            server_anti_spoof_score = max(server_anti_spoof_score, liveness_tx.anti_spoof_score)
            server_liveness_score = max(server_liveness_score, liveness_tx.liveness_score)
            _tx_anti_spoof_passed = liveness_tx.anti_spoof_score >= _anti_spoof_threshold
            _tx_pad_ok = (
                liveness_tx.pa_score < _pad_threshold
                or not _pad_required
                or _pa_action != 'deny'
            )
            # v2.1.16 (Security Hardening #1): also require server_anti_spoof_passed
            # — the FRESH anti-spoof recheck on the exact frame submitted here (a
            # few lines above), independent of the TX's challenge-time score. A
            # valid TX proves liveness was demonstrated at CHALLENGE time; this
            # recheck catches a stale/replayed/swapped frame submitted afterward,
            # even when the TX itself is valid. Both are server-computed —
            # neither depends on any client-supplied claim.
            if _tx_anti_spoof_passed and _tx_pad_ok and server_anti_spoof_passed:
                server_liveness_passed = True
            elif liveness_tx.anti_spoof_score > 0:
                logger.warning(
                    '[VERIFY_SUBMIT] TX liveness criteria not met: '
                    'anti_spoof=%.3f threshold=%.3f pa_score=%.3f pad_ok=%s submit_recheck_passed=%s',
                    liveness_tx.anti_spoof_score, _anti_spoof_threshold,
                    liveness_tx.pa_score, _tx_pad_ok, server_anti_spoof_passed,
                )
            # Issue 3: even with a valid TX, deny when the TX PAD score itself
            # crosses the suspicious threshold and PAD is in deny mode. This
            # closes the window where a passing TX could mask a presentation
            # attack flagged in the same challenge.
            if (_pad_required and _pa_action == 'deny'
                    and liveness_tx.pa_score >= _pad_threshold):
                _tx_pad_block = True
                _tx_pad_reason = (
                    f'Denied: presentation-attack score {liveness_tx.pa_score:.3f} '
                    f'>= threshold {_pad_threshold:.2f}. '
                    'Phone screen or printed photo signature detected during the '
                    'liveness challenge. Re-attempt with the real person present.'
                )
                logger.warning(
                    '[VERIFY_SUBMIT] PAD-block at submit: pa_score=%.3f flags=%s',
                    liveness_tx.pa_score, liveness_tx.pa_flags,
                )

        # Build attempt record
        attempt = VerificationAttempt(
            beneficiary=beneficiary,
            performed_by=request.user,
            claimant_type=claimant_type,
            representative=representative,
            liveness_passed=server_liveness_passed,
            liveness_score=server_liveness_score,
            anti_spoof_score=server_anti_spoof_score,
            head_movement_completed=bool(challenge_completed),
            threshold_used=threshold,
            attempt_number=attempt_number,
            session_id=session_id,
            stipend_event=stipend_event,
            demo_mode_active=demo_mode and not liveness_required,
        )

        # Annotate divergence between client and server liveness values
        if abs(server_anti_spoof_score - anti_spoof_score_client) > 0.05:
            attempt.notes = (
                f'Liveness mismatch — client reported '
                f'spoof={anti_spoof_score_client:.3f}/passed={liveness_passed_client}, '
                f'server measured spoof={server_anti_spoof_score:.3f}/'
                f'passed={server_liveness_passed}. Server values used.'
            )

        def _deny(reason, save=True):
            attempt.decision = VerificationAttempt.DECISION_DENIED
            attempt.decision_reason = reason
            if not attempt.notes:
                attempt.notes = reason
            if save:
                attempt.save()
            _log_verify(request, beneficiary, 'denied', attempt_number,
                        server_liveness_passed, None, threshold, claimant_type,
                        reason, stipend_event,
                        extra={
                            'anti_spoof_score': round(float(server_anti_spoof_score or 0.0), 3),
                            'tx_token_present': _tx_diag.get('tx_token_present'),
                            'tx_valid': _tx_diag.get('tx_valid'),
                            'tx_used': _tx_diag.get('tx_used'),
                            'tx_expired': _tx_diag.get('tx_expired'),
                            'tx_claimant_match': _tx_diag.get('tx_claimant_match'),
                            'final_block_reason': _tx_diag.get('final_block_reason'),
                            'challenge_motion_detected': bool(challenge_completed),
                            'static_sequence_detected': bool(_tx_pad_block),
                            'pa_score': round(float(getattr(liveness_tx, 'pa_score', 0.0) or 0.0), 3) if liveness_tx else None,
                            'pa_flags': getattr(liveness_tx, 'pa_flags', None) if liveness_tx else None,
                        })
            request.session.pop('verification_session', None)
            return JsonResponse({
                'success': True,
                'decision': 'denied',
                'reason': reason,
                'redirect': f'/verification/result/{attempt.id}/',
            })

        def _service_unavailable(message):
            """
            The face-recognition model itself is unavailable (e.g. FaceNet
            weights could not be loaded/downloaded) — no biometric comparison
            was ever performed. This is a SYSTEM availability failure, not a
            biometric mismatch: unlike _deny(), no VerificationAttempt is
            created/saved, no 'denied' decision is recorded, and the
            verification_session is left intact so the operator can retry
            once connectivity/the model is restored, without restarting the
            whole flow.
            """
            logger.error(
                '[VERIFY_SUBMIT] Face recognition model unavailable — beneficiary=%s: %s',
                beneficiary_id, message,
            )
            # Release the token this request claimed above (Blocker 2) — the
            # model outage is a system failure, not a spent proof, so the
            # operator should be able to retry with the same liveness proof
            # rather than repeating the challenge. Safe: claim() only ever
            # has one winner, and this request is it.
            if liveness_tx is not None:
                try:
                    liveness_tx.release_claim()
                except Exception:
                    logger.exception('[LIVENESS_TX] release_claim failed (non-blocking)')
            return JsonResponse({
                'success': False,
                'system_unavailable': True,
                'error': message,
            }, status=503)

        # Deny if PAD signaled presentation attack at challenge time.
        if _tx_pad_block:
            return _deny(_tx_pad_reason)

        # Deny if liveness TX is invalid/missing (when required).
        # FaceNet identity comparison must NOT run without a valid server-issued TX.
        if liveness_proof_required and liveness_tx is None:
            _block_reason = liveness_tx_denial_reason or (
                'Verification blocked: no valid server-issued liveness proof.'
            )
            logger.warning(
                '[VERIFY_TX] Blocking identity match — tx_token_present=%s tx_found=%s '
                'tx_valid=%s tx_used=%s tx_expired=%s strict=%s reason=%s',
                _tx_diag['tx_token_present'], _tx_diag['tx_found'],
                _tx_diag['tx_valid'], _tx_diag['tx_used'],
                _tx_diag['tx_expired'], liveness_proof_required,
                _tx_diag['final_block_reason'],
            )
            try:
                AuditLog.log(
                    action=AuditLog.ACTION_VERIFY_TX_STALE,
                    user=request.user,
                    target_type='Beneficiary',
                    target_id=beneficiary.beneficiary_id,
                    details={
                        'beneficiary_id': beneficiary.beneficiary_id,
                        'claimant_type': claimant_type,
                        'reason': _tx_diag['final_block_reason'],
                        'tx_token_present': _tx_diag['tx_token_present'],
                        'tx_found': _tx_diag['tx_found'],
                        'tx_valid': _tx_diag['tx_valid'],
                        'tx_used': _tx_diag['tx_used'],
                        'tx_expired': _tx_diag['tx_expired'],
                    },
                    request=request,
                )
            except Exception:
                logger.exception('AuditLog write for VERIFY_TX_STALE failed (non-blocking)')
            return _deny(_block_reason)

        # Deny if liveness failed in strict mode
        if liveness_required and not server_liveness_passed:
            if not server_anti_spoof_passed:
                _detail = (
                    f'Anti-spoofing score {server_anti_spoof_score:.3f} is below threshold '
                    f'(possible low-quality capture, obstructed face, or spoof attempt).'
                )
            elif not challenge_completed:
                _detail = (
                    f'Head movement challenge not completed '
                    f'(anti-spoof score {server_anti_spoof_score:.3f} passed, but no head movement confirmed). '
                    f'A phone screen, printed photo, or replay cannot complete the movement challenge.'
                )
            else:
                _detail = f'Anti-spoofing score {server_anti_spoof_score:.3f} is below threshold.'
            return _deny(
                f'Denied: liveness check failed (strict mode). {_detail} '
                f'Liveness score: {server_liveness_score:.3f}.'
            )

        # ── Retrieve liveness-frame embedding (face-bound identity) ──────────
        # Use the embedding stored in the LivenessTransaction as the identity proof.
        # This is the face that was PRESENT during the liveness challenge.
        # It cannot be replaced by a different face submitted after liveness.
        live_embedding = None
        from .face_utils import decrypt_embedding as _decrypt

        if liveness_tx and liveness_tx.embedding_data:
            try:
                live_embedding = _decrypt(bytes(liveness_tx.embedding_data))
                logger.info('[VERIFY] Using liveness-TX embedding for beneficiary=%s', beneficiary_id)
            except Exception as emb_err:
                logger.warning('[VERIFY] TX embedding decrypt failed: %s — falling back', emb_err)
                live_embedding = None

        # Fallback: if no TX embedding (e.g. model mock during liveness), process submitted frame
        face_result = None  # initialized here; only assigned if TX embedding path is not used
        if live_embedding is None:
            face_result = process_face_for_verification(image_bytes)
            if face_result.get('model_unavailable'):
                return _service_unavailable(face_result['error'])
            if not face_result['success']:
                return _deny(f'Denied: {face_result["error"]}', save=False)
            if face_result.get('quality'):
                q = face_result['quality']
                attempt.face_quality_score = q.get('score')
                attempt.face_quality_ok = q.get('ok')
            live_embedding = face_result['embedding']
        else:
            # Still check quality on the submitted frame for the audit record
            try:
                from .face_utils import check_face_quality as _cfq
                _qa_img = load_image_from_bytes(image_bytes)
                _qa_face = detect_and_align_face(_qa_img)
                _qa = _cfq(_qa_face)
                attempt.face_quality_score = _qa.get('score')
                attempt.face_quality_ok = _qa.get('ok')
            except Exception:
                pass

        # ── Final-frame integrity gate (added 2026-05-28) ─────────────────────
        # Even with a valid liveness TX, the SUBMITTED frame can be a different
        # subject than the one approved by liveness (user steps out of frame,
        # holds up a photo, brings an animal/object). We must reject these
        # cases with an outright DENY — never let them proceed to identity
        # matching, which would otherwise reuse the liveness-frame embedding
        # and produce a high (misleading) score.
        #
        # Rules enforced here (only when a liveness TX is in use):
        #   1. Exactly one human face must be detectable in the submitted frame.
        #      Zero faces  → DENY (object/animal/empty frame).
        #      >1 faces    → DENY (ambiguous subject).
        #   2. The submitted face's embedding must be extractable.
        #      Failure     → DENY (not a usable face capture).
        #   3. The submitted-frame face must match the liveness-frame face
        #      (cosine similarity >= SAME_FACE_SEQUENCE_THRESHOLD).
        #      Below       → DENY (subject changed after liveness).
        #
        # These checks intentionally precede the existing identity match. They
        # do NOT modify FaceNet matching, liveness/PAD, or thresholds.
        if liveness_tx is not None and liveness_tx.embedding_data:
            from .face_utils import (
                count_faces_in_frame as _count_faces,
                cosine_similarity as _cos_sim,
            )

            _face_count_result = _count_faces(image_bytes)
            _face_count = int(_face_count_result.get('count', 0))
            _face_detector = _face_count_result.get('detector', 'unknown')

            if not _face_count_result.get('success'):
                logger.warning(
                    '[VERIFY_INTEGRITY] Face counter unavailable — beneficiary=%s err=%s',
                    beneficiary_id, _face_count_result.get('error'),
                )
            else:
                logger.info(
                    '[VERIFY_INTEGRITY] beneficiary=%s submitted_face_count=%d detector=%s',
                    beneficiary_id, _face_count, _face_detector,
                )

            # Rule 1a: zero faces in the final frame.
            if _face_count_result.get('success') and _face_count == 0:
                try:
                    AuditLog.log(
                        action=AuditLog.ACTION_VERIFY_NO_FACE,
                        user=request.user,
                        target_type='Beneficiary',
                        target_id=beneficiary.beneficiary_id,
                        details={
                            'beneficiary_id': beneficiary.beneficiary_id,
                            'claimant_type': claimant_type,
                            'detector': _face_detector,
                            'stipend_event': getattr(stipend_event, 'title', ''),
                        },
                        request=request,
                    )
                except Exception:
                    logger.exception('AuditLog write for VERIFY_NO_FACE failed (non-blocking)')
                return _deny(
                    'Denied: no human face detected in the verification frame. '
                    'The frame shown after the liveness challenge contains no recognisable face '
                    '(empty frame, object, animal, or completely covered). '
                    'Stay in front of the camera throughout the verification and retry.'
                )

            # Rule 1b: more than one face in the final frame.
            if _face_count_result.get('success') and _face_count > 1:
                try:
                    AuditLog.log(
                        action=AuditLog.ACTION_VERIFY_MULTIPLE_FACES,
                        user=request.user,
                        target_type='Beneficiary',
                        target_id=beneficiary.beneficiary_id,
                        details={
                            'beneficiary_id': beneficiary.beneficiary_id,
                            'claimant_type': claimant_type,
                            'face_count': _face_count,
                            'detector': _face_detector,
                            'stipend_event': getattr(stipend_event, 'title', ''),
                        },
                        request=request,
                    )
                except Exception:
                    logger.exception('AuditLog write for VERIFY_MULTIPLE_FACES failed (non-blocking)')
                return _deny(
                    f'Denied: multiple faces ({_face_count}) detected in the verification frame. '
                    'Only the claimant should be visible during verification. '
                    'Ask any bystanders to step out of frame and retry.'
                )

            # Rule 2 + Rule 3: process the submitted frame and require it to
            # match the liveness face. We use process_face_for_verification so
            # quality + embedding both come from the actual submitted frame.
            _sub_result = process_face_for_verification(image_bytes)
            if _sub_result.get('model_unavailable'):
                return _service_unavailable(_sub_result['error'])
            if (not _sub_result.get('success')
                    or _sub_result.get('embedding') is None):
                try:
                    AuditLog.log(
                        action=AuditLog.ACTION_VERIFY_FRAME_INVALID,
                        user=request.user,
                        target_type='Beneficiary',
                        target_id=beneficiary.beneficiary_id,
                        details={
                            'beneficiary_id': beneficiary.beneficiary_id,
                            'claimant_type': claimant_type,
                            'detector': _face_detector,
                            'face_count': _face_count,
                            'using_mock': bool(_sub_result.get('using_mock')),
                            'reason': _sub_result.get('error', '')[:300],
                        },
                        request=request,
                    )
                except Exception:
                    logger.exception('AuditLog write for VERIFY_FRAME_INVALID failed (non-blocking)')
                return _deny(
                    'Denied: the final verification frame could not be processed as a usable face '
                    f'({_sub_result.get("error") or "no embedding"}). '
                    'Recapture with the claimant centered in the frame and good lighting.'
                )

            # Rule 3: same-face consistency. Compare the submitted-frame embedding
            # against the liveness-frame embedding stored in the LivenessTransaction.
            if same_face_threshold > 0:
                try:
                    _sub_emb = _sub_result['embedding']
                    _same_face_score = float(_cos_sim(live_embedding, _sub_emb))
                except Exception as _sf_err:
                    logger.warning(
                        '[VERIFY_INTEGRITY] Same-face cosine failed — beneficiary=%s err=%s',
                        beneficiary_id, _sf_err,
                    )
                    try:
                        AuditLog.log(
                            action=AuditLog.ACTION_VERIFY_FRAME_INVALID,
                            user=request.user,
                            target_type='Beneficiary',
                            target_id=beneficiary.beneficiary_id,
                            details={
                                'beneficiary_id': beneficiary.beneficiary_id,
                                'claimant_type': claimant_type,
                                'reason': f'same_face_cosine_failed: {_sf_err}'[:300],
                            },
                            request=request,
                        )
                    except Exception:
                        logger.exception('AuditLog write for VERIFY_FRAME_INVALID failed (non-blocking)')
                    return _deny(
                        'Denied: same-face consistency check could not be evaluated. '
                        'Recapture the verification frame.'
                    )

                logger.info(
                    '[VERIFY_INTEGRITY] beneficiary=%s same_face_score=%.4f threshold=%.4f',
                    beneficiary_id, _same_face_score, same_face_threshold,
                )

                if _same_face_score < same_face_threshold:
                    try:
                        AuditLog.log(
                            action=AuditLog.ACTION_VERIFY_SUBJECT_CHANGED,
                            user=request.user,
                            target_type='Beneficiary',
                            target_id=beneficiary.beneficiary_id,
                            details={
                                'beneficiary_id': beneficiary.beneficiary_id,
                                'claimant_type': claimant_type,
                                'same_face_score': round(_same_face_score, 4),
                                'same_face_threshold': same_face_threshold,
                                'detector': _face_detector,
                                'tx_id': str(liveness_tx.pk),
                            },
                            request=request,
                        )
                    except Exception:
                        logger.exception('AuditLog write for VERIFY_SUBJECT_CHANGED failed (non-blocking)')
                    return _deny(
                        f'Denied: the face in the verification frame is not the same person '
                        f'who passed the liveness challenge '
                        f'(consistency score {_same_face_score:.3f} < threshold {same_face_threshold:.2f}). '
                        'The claimant must remain in front of the camera through the entire verification. '
                        'Do not swap subjects or show a photo / phone screen after liveness.'
                    )

            # Carry the submitted-frame quality into the attempt record for the
            # audit trail. The TX embedding is still what identity matching uses.
            try:
                _q = _sub_result.get('quality') or {}
                if _q:
                    attempt.face_quality_score = _q.get('score')
                    attempt.face_quality_ok = _q.get('ok')
            except Exception:
                pass

        # ── Same-face consistency check (legacy non-TX path) ──────────────────
        # The block below is the original same-face check. It only runs when
        # the TX-bound path above did NOT execute (e.g. legacy clients with no
        # TX embedding). The TX-bound path enforces the strict-DENY rules.
        if (liveness_tx and liveness_tx.embedding_data and
                same_face_threshold > 0 and live_embedding is not None):
            try:
                sub_result = process_face_for_verification(image_bytes)
                if sub_result.get('success') and sub_result.get('embedding') is not None:
                    sub_emb = sub_result['embedding']
                    from .face_utils import cosine_similarity as _cos_sim
                    same_face_score = float(_cos_sim(live_embedding, sub_emb))
                    logger.info(
                        '[SAME_FACE] beneficiary=%s same_face_score=%.4f threshold=%.4f',
                        beneficiary_id, same_face_score, same_face_threshold,
                    )
                    if same_face_score < same_face_threshold:
                        return _deny(
                            f'Denied: liveness identity and verification identity do not match '
                            f'(consistency score {same_face_score:.3f} < threshold {same_face_threshold:.2f}). '
                            'The face shown after the liveness challenge differs from the liveness face. '
                            'Do not show a photo or phone screen.'
                        )
            except Exception as sf_err:
                logger.warning('[SAME_FACE] Same-face check failed (non-blocking): %s', sf_err)

        # ── Compare liveness-frame embedding against stored identity ─────────
        # Hard rule: claimant_type=representative MUST compare against the
        # representative embedding. We never fall back to the beneficiary's
        # embedding for a representative claim — that would let the senior's
        # own face approve a representative claim.
        reference_embedding_source = (
            'representative_primary' if claimant_type == VerificationAttempt.CLAIMANT_REPRESENTATIVE
            else 'beneficiary_primary_or_additional'
        )
        if claimant_type == VerificationAttempt.CLAIMANT_REPRESENTATIVE:
            if not representative or not representative.has_face_data:
                # Defence-in-depth: earlier guards should have returned already.
                logger.error(
                    '[VERIFY_REP] Reached comparison with no representative for a '
                    'representative claim — denying. beneficiary=%s', beneficiary_id,
                )
                return _deny(
                    'Denied: representative verification requires the registered '
                    'representative’s face. No representative is bound to this session.'
                )
            rep_result = compare_with_stored(live_embedding, representative.face_embedding.embedding_data)
            if not rep_result['success']:
                comparison = {'success': False, 'error': rep_result.get('error', 'Rep comparison failed'), 'score': 0.0}
            else:
                comparison = {
                    'success': True,
                    'score': rep_result['score'],
                    'matched_template': 'representative_primary',
                    'templates_checked': 1,
                    'all_scores': [{'template': 'representative_primary', 'score': rep_result['score']}],
                }

            # Secondary safety probe: if the live face matches the BENEFICIARY (senior)
            # better than the threshold, block the claim with a clear reason. This
            # catches the case where the senior shows their own face for a
            # representative claim. Shared with the BPA-2.1 evaluation workflow —
            # see face_utils.check_representative_beneficiary_fallback.
            try:
                fallback_probe = check_representative_beneficiary_fallback(live_embedding, beneficiary, threshold)
                if fallback_probe['blocked']:
                    logger.warning(
                        '[VERIFY_REP] Live face matches beneficiary (score=%.3f >= %.3f) '
                        'on a REPRESENTATIVE claim — blocking. rep=%s ben=%s',
                        fallback_probe['score'], threshold, representative.pk, beneficiary_id,
                    )
                    attempt.similarity_score = comparison.get('score', 0.0)
                    attempt.matched_template = 'beneficiary_face_on_rep_claim'
                    attempt.templates_checked = (comparison.get('templates_checked') or 1) + fallback_probe['templates_checked']
                    return _deny(
                        'Representative verification requires the registered '
                        'representative’s face, not the beneficiary’s face. '
                        'This appears to be the beneficiary, not the registered '
                        'representative.'
                    )
            except Exception as _probe_err:
                logger.warning('[VERIFY_REP] Beneficiary cross-probe failed (non-blocking): %s', _probe_err)
        else:
            comparison = compare_with_all_embeddings(live_embedding, beneficiary)

        logger.info(
            '[VERIFY_REF] beneficiary=%s claimant=%s reference=%s representative=%s '
            'probe_embedding_created=%s',
            beneficiary_id, claimant_type, reference_embedding_source,
            getattr(representative, 'pk', None), live_embedding is not None,
        )

        if not comparison['success']:
            attempt.decision = VerificationAttempt.DECISION_DENIED
            attempt.decision_reason = f'Comparison error: {comparison.get("error", "unknown")}'
            attempt.notes = attempt.decision_reason
            attempt.save()
            return JsonResponse({'success': False, 'error': comparison.get('error', 'Comparison failed')})

        score = comparison['score']
        attempt.similarity_score = score
        attempt.matched_template = comparison.get('matched_template', '')
        attempt.templates_checked = comparison.get('templates_checked', 0)

        _using_neutral_tx = (
            liveness_tx is not None and liveness_tx.embedding_data is not None
        )
        logger.info(
            '[VERIFY] beneficiary=%s score=%.4f threshold=%.4f gap=%+.4f '
            'matched=%s checked=%d demo=%s neutral_tx_embedding=%s',
            beneficiary.beneficiary_id,
            score,
            threshold,
            score - threshold,
            comparison.get('matched_template', '?'),
            comparison.get('templates_checked', 0),
            demo_mode,
            _using_neutral_tx,
        )
        if comparison.get('all_scores'):
            for entry in comparison['all_scores']:
                logger.info(
                    '[VERIFY_SCORE] template=%s score=%.4f threshold=%.4f gap=%+.4f',
                    entry['template'], entry['score'], threshold, entry['score'] - threshold,
                )

        # ── Decision logic (v2.1.13 — three-zone band) ────────────────────────
        # WHY the three-zone band: FaceNet on webcam captures can produce
        # 0.80-0.86 similarity even for wrong-person, baby-photo, or low-quality
        # faces. Treating "above the lower threshold" as auto-verified caused a
        # false-accept risk (observed baby-photo at 0.82-0.83 above the 0.75
        # auto-verify line in v2.1.12). Now:
        #   score >= auto_verify_threshold (0.88)        → VERIFIED (auto release)
        #   threshold <= score < auto_verify_threshold   → MANUAL_REVIEW
        #   review_band <= score < threshold             → MANUAL_REVIEW (low)
        #   score < review_band                          → NOT_VERIFIED
        auto_verify_threshold = SystemConfig.get_auto_verify_threshold()
        review_band = threshold * 0.85
        gap = score - threshold  # positive = above lower threshold
        gap_auto = score - auto_verify_threshold  # positive = above auto-verify

        tmpl_info = f'template={comparison.get("matched_template", "?")}, checked={comparison.get("templates_checked", 0)}'

        liveness_warning = ''
        if not server_liveness_passed and not liveness_required:
            liveness_warning = ' [Liveness warning — assisted rollout mode, non-blocking]'

        # Shared pure decision helper (face_utils.decide_base_outcome) — also
        # used by the BPA-2 controlled evaluation trial workflow, so both
        # paths apply identical decision rules. See EvaluationDecisionParityTest.
        decision = decide_base_outcome(score, threshold, auto_verify_threshold)

        if decision == VerificationAttempt.DECISION_VERIFIED:
            reason = (
                f'Verified: score {score:.3f} >= auto-verify threshold '
                f'{auto_verify_threshold:.2f} (+{gap_auto:.3f}). {tmpl_info}.'
                + liveness_warning
            )
        elif decision == VerificationAttempt.DECISION_MANUAL_REVIEW and score >= threshold:
            reason = (
                f'Manual review — high-band: score {score:.3f} is above the lower '
                f'threshold ({threshold:.2f}) but below the auto-verify threshold '
                f'({auto_verify_threshold:.2f}, gap {gap_auto:+.3f}). '
                f'{tmpl_info}. Release BLOCKED pending administrator review — '
                'similarity in this band can come from look-alike, baby-photo, '
                'or low-quality captures and must be confirmed by a human.'
            )
        elif decision == VerificationAttempt.DECISION_MANUAL_REVIEW:
            reason = (
                f'Manual review — low-band: score {score:.3f} in review band '
                f'({review_band:.2f}–{threshold:.2f}), gap {gap:.3f}. '
                f'{tmpl_info}. Administrator action required.'
            )
        else:
            reason = (
                f'Not verified: score {score:.3f} < threshold {threshold:.2f} '
                f'(gap {gap:.3f}). {tmpl_info}.'
            )

        # v2.1.13 (Issue 2) — low-quality captures cannot auto-verify even at
        # high similarity. Low-quality embeddings produce unreliable cosine
        # similarities; a high score on a blurry/dim/glare frame can be
        # coincidental rather than a true identity match.
        # Shared with the BPA-2.1 evaluation workflow — see
        # face_utils.apply_quality_override. NOTE (unchanged behavior):
        # `quality` is only non-None here on the rare fallback identity path
        # (face_result is not None) — on the common TX-bound path this rule
        # is a deliberate no-op, exactly as before this extraction.
        _low_quality_forces_mr = getattr(
            django_settings, 'LOW_QUALITY_FORCES_MANUAL_REVIEW', True,
        )
        _quality_for_override = (
            face_result['quality'] if (face_result is not None and face_result.get('quality')) else None
        )
        decision, _quality_overridden = apply_quality_override(decision, _quality_for_override, _low_quality_forces_mr)
        if _quality_overridden:
            _qreason = _quality_for_override.get('reason', 'low quality')
            reason = (
                f'Manual review — low face quality: score {score:.3f} is above '
                f'the auto-verify threshold ({auto_verify_threshold:.2f}), but '
                f'face quality is degraded ({_qreason}). Release BLOCKED pending '
                'administrator review — low-quality captures can produce high '
                'similarity by coincidence and must be confirmed by a human.'
            )

        # Append quality note if applicable (only when face_result was computed — fallback path)
        if face_result is not None and face_result.get('quality') and not face_result['quality']['ok']:
            reason += f' Note: {face_result["quality"]["reason"]}'

        # ── Lookalike / twin detection ─────────────────────────────────────────
        # If the target would pass, check whether another registered beneficiary
        # also has a score within LOOKALIKE_BAND of the target score. If so,
        # escalate to manual review — staff must confirm with ID before releasing.
        # Shared with the BPA-2.1 evaluation workflow — see
        # face_utils.check_lookalike_escalation.
        LOOKALIKE_BAND = getattr(django_settings, 'LOOKALIKE_BAND', 0.05)
        _lookalike_audit_details = None
        if decision == VerificationAttempt.DECISION_VERIFIED and score >= threshold:
            lookalike_result = check_lookalike_escalation(
                live_embedding, score, threshold,
                exclude_beneficiary_id=str(beneficiary.beneficiary_id),
                lookalike_band=LOOKALIKE_BAND,
            )
            top_match = lookalike_result['top_match']
            _closest_score = top_match['score'] if top_match else None
            _gap = round(score - _closest_score, 4) if _closest_score is not None else None
            logger.info(
                '[LOOKALIKE] beneficiary=%s claimed_score=%.4f threshold=%.4f '
                'lookalike_band=%.3f lookalike_threshold=%.4f '
                'embeddings_checked=%d closest_id=%s closest_score=%s gap=%s '
                'manual_review_triggered=%s',
                beneficiary.beneficiary_id,
                score,
                threshold,
                LOOKALIKE_BAND,
                lookalike_result['lookalike_threshold'],
                lookalike_result['checked'],
                top_match['beneficiary_id'] if top_match else None,
                f'{_closest_score:.4f}' if _closest_score is not None else 'none',
                f'{_gap:.4f}' if _gap is not None else 'none',
                lookalike_result['escalate'],
            )
            if lookalike_result['escalate']:
                decision = VerificationAttempt.DECISION_MANUAL_REVIEW
                reason = (
                    f'MANUAL REVIEW — POSSIBLE DUPLICATE OR LOOKALIKE. '
                    f'Liveness and face matching passed (score {score:.3f} >= threshold {threshold:.2f}), '
                    f'but another beneficiary record ({top_match["beneficiary_id"]}: {top_match["full_name"]}, '
                    f'score {top_match["score"]:.3f}) also matched this face within the lookalike safety band '
                    f'({LOOKALIKE_BAND:.2f}). '
                    'Staff must confirm the claimant\'s identity using a valid ID before releasing stipend.'
                )
                _lookalike_audit_details = {
                    'beneficiary_id': beneficiary.beneficiary_id,
                    'score': round(score, 4),
                    'lookalike_id': top_match['beneficiary_id'],
                    'lookalike_name': top_match['full_name'],
                    'lookalike_score': top_match['score'],
                    'band': LOOKALIKE_BAND,
                }
        # ──────────────────────────────────────────────────────────────────────

        attempt.decision = decision
        attempt.decision_reason = reason
        attempt.save()

        # Bind the (already atomically claimed — see claim() above, Blocker 2)
        # liveness transaction to this attempt now that it has a pk. This is
        # a plain unconditional update, not a compare-and-swap: claim()
        # already excluded every other concurrent request from this token
        # before any of the expensive verification work above ran, so there
        # is no race left to detect here.
        if liveness_tx is not None:
            try:
                liveness_tx.bind_attempt(attempt)
            except Exception as _tx_err:
                logger.warning('[LIVENESS_TX] bind_attempt failed: %s', _tx_err)

        # Write lookalike audit log after save so attempt.id is populated.
        if _lookalike_audit_details is not None:
            AuditLog.log(
                action=AuditLog.ACTION_DUPLICATE_FACE,
                user=request.user,
                target_type='VerificationAttempt',
                target_id=attempt.id,
                details=_lookalike_audit_details,
                request=request,
            )

        max_retries = getattr(django_settings, 'MAX_RETRY_ATTEMPTS', 2)
        _log_verify(request, beneficiary, decision, attempt_number,
                    server_liveness_passed, score, threshold, claimant_type,
                    reason, stipend_event,
                    all_scores=comparison.get('all_scores', []),
                    extra={
                        'anti_spoof_score': round(float(server_anti_spoof_score or 0.0), 3),
                        'tx_token_present': _tx_diag.get('tx_token_present'),
                        'tx_valid': _tx_diag.get('tx_valid'),
                        'tx_used': _tx_diag.get('tx_used'),
                        'tx_expired': _tx_diag.get('tx_expired'),
                        'tx_claimant_match': _tx_diag.get('tx_claimant_match'),
                        'final_block_reason': _tx_diag.get('final_block_reason'),
                        'challenge_motion_detected': bool(challenge_completed),
                        'static_sequence_detected': bool(_tx_pad_block),
                        'pa_score': round(float(getattr(liveness_tx, 'pa_score', 0.0) or 0.0), 3) if liveness_tx else None,
                        'pa_flags': getattr(liveness_tx, 'pa_flags', None) if liveness_tx else None,
                        'reference_embedding_source': reference_embedding_source,
                    })

        if decision == VerificationAttempt.DECISION_VERIFIED:
            # ── Create ClaimRecord ────────────────────────────────────────────
            # Case A: active stipend event exists → claim directly.
            # Case B: no event → President may claim directly; all other
            #         roles create a pending_approval record for President to approve.
            if stipend_event:
                with transaction.atomic():
                    _locked_ben = Beneficiary.objects.select_for_update().get(
                        pk=beneficiary.pk)
                    _locked_event, _event_ok, _ineligible_reason = _lock_event_for_finalization(stipend_event)
                    if not _event_ok:
                        # Phase B.5 — the event was still open when verification
                        # started but is no longer claimable now that the
                        # decision is final. The biometric decision (VERIFIED)
                        # stays exactly as computed above; only the payout is
                        # blocked, and the block is visible on both the audit
                        # trail and the result page (attempt.notes).
                        from django.contrib import messages as _messages
                        _block_note = _payout_blocked_message(_ineligible_reason)
                        attempt.notes = ((attempt.notes + ' ') if attempt.notes else '') + _block_note
                        attempt.save(update_fields=['notes'])
                        _messages.warning(request, _block_note)
                        AuditLog.log(
                            action=AuditLog.ACTION_REGISTER,
                            user=request.user,
                            target_type='VerificationAttempt',
                            target_id=attempt.id,
                            details={
                                'beneficiary_id': beneficiary.beneficiary_id,
                                'stipend_event': stipend_event.title,
                                'claimant_type': claimant_type,
                                'reason': f'Payout finalization blocked — {_ineligible_reason}',
                                'attempt_id': str(attempt.id),
                            },
                            request=request,
                        )
                    elif not ClaimRecord.objects.filter(
                        beneficiary=_locked_ben,
                        stipend_event=_locked_event,
                        status=ClaimRecord.STATUS_CLAIMED,
                    ).exists():
                        try:
                            new_claim = _create_claim_record(
                                beneficiary=_locked_ben,
                                stipend_event=_locked_event,
                                claimant_type=claimant_type,
                                representative=representative,
                                claimed_by=request.user,
                                verification_attempt=attempt,
                                status=ClaimRecord.STATUS_CLAIMED,
                                verification_method=ClaimRecord.VERIFY_FACE,
                            )
                        except IntegrityError:
                            # The exists() check above passed, but a concurrent
                            # transaction committed a claim for this
                            # beneficiary/event before our INSERT ran — the DB
                            # UniqueConstraint is the final safety net. Treat
                            # exactly like the exists()-detected duplicate below.
                            new_claim = None
                            AuditLog.log(
                                action=AuditLog.ACTION_DUPLICATE_PAYOUT_ATTEMPT,
                                user=request.user,
                                target_type='Beneficiary',
                                target_id=beneficiary.id,
                                details={
                                    'beneficiary_id': beneficiary.beneficiary_id,
                                    'stipend_event': stipend_event.title,
                                    'reason': 'Already claimed for this event (concurrent attempt, detected at commit)',
                                    'attempt_id': str(attempt.id),
                                },
                                request=request,
                            )
                        if new_claim is not None:
                            AuditLog.log(
                                action=AuditLog.ACTION_CLAIM,
                                user=request.user,
                                target_type='ClaimRecord',
                                target_id=new_claim.id,
                                details={
                                    'beneficiary_id': beneficiary.beneficiary_id,
                                    'stipend_event': stipend_event.title,
                                    'claimant_type': claimant_type,
                                    'attempt_id': str(attempt.id),
                                    'amount': str(new_claim.amount),
                                    'reference_number': new_claim.reference_number,
                                    'verification_method': new_claim.verification_method,
                                },
                                request=request,
                            )
                    else:
                        # Concurrent duplicate — another station got there first.
                        AuditLog.log(
                            action=AuditLog.ACTION_DUPLICATE_PAYOUT_ATTEMPT,
                            user=request.user,
                            target_type='Beneficiary',
                            target_id=beneficiary.id,
                            details={
                                'beneficiary_id': beneficiary.beneficiary_id,
                                'stipend_event': stipend_event.title,
                                'reason': 'Already claimed for this event (concurrent attempt)',
                                'attempt_id': str(attempt.id),
                            },
                            request=request,
                        )
            else:
                # No active stipend event — identity verified but no claim created.
                # A ClaimRecord cannot exist without a linked event; verification
                # is logged via VerificationAttempt only. Staff must create/schedule
                # a payout event before a stipend claim can be processed.
                AuditLog.log(
                    action=AuditLog.ACTION_REGISTER,
                    user=request.user,
                    target_type='VerificationAttempt',
                    target_id=attempt.id,
                    details={
                        'beneficiary_id': beneficiary.beneficiary_id,
                        'claimant_type': claimant_type,
                        'reason': 'Identity verified but no claim created — no active payout event.',
                        'attempt_id': str(attempt.id),
                    },
                    request=request,
                )

            request.session.pop('verification_session', None)
            return JsonResponse({
                'success': True,
                'decision': 'verified',
                'score': round(score, 3),
                'threshold': threshold,
                'reason': reason,
                'redirect': f'/verification/result/{attempt.id}/',
            })

        elif decision == VerificationAttempt.DECISION_MANUAL_REVIEW:
            request.session.pop('verification_session', None)
            return JsonResponse({
                'success': True,
                'decision': 'manual_review',
                'score': round(score, 3),
                'threshold': threshold,
                'reason': reason,
                'redirect': f'/verification/result/{attempt.id}/',
            })

        elif attempt_number <= max_retries:
            new_challenge = get_random_challenge()
            session_data['attempt_number'] = attempt_number + 1
            session_data['challenge'] = new_challenge
            request.session['verification_session'] = session_data
            request.session.modified = True
            return JsonResponse({
                'success': True,
                'decision': 'retry',
                'score': round(score, 3),
                'threshold': threshold,
                'reason': reason,
                'message': (
                    f'Score {score:.3f} is below threshold {threshold:.2f}. '
                    f'Attempt {attempt_number} of {max_retries + 1}. '
                    'Ensure good lighting, center your face, and hold still.'
                ),
                'new_challenge': new_challenge,
                'new_challenge_display': _challenge_display(new_challenge),
                'attempt_id': str(attempt.id),
                'attempt_number': attempt_number,
                'max_retries': max_retries,
            })

        else:
            attempt.fallback_triggered = True
            attempt.save()
            request.session.pop('verification_session', None)
            AuditLog.log(
                action=AuditLog.ACTION_FALLBACK,
                user=request.user,
                target_type='Beneficiary',
                target_id=beneficiary.id,
                details={
                    'score': round(score, 3),
                    'reason': f'Max retries ({max_retries}) reached — fallback triggered',
                    'threshold': threshold,
                },
                request=request
            )
            return JsonResponse({
                'success': True,
                'decision': 'fallback',
                'score': round(score, 3),
                'threshold': threshold,
                'reason': reason,
                'message': (
                    f'Facial verification failed after {max_retries + 1} attempts. '
                    'Proceeding to ID verification.'
                ),
                'redirect': f'/verification/fallback/{attempt.id}/',
            })

    except Exception as e:
        # Log full detail server-side for IT investigation, but never
        # echo raw exception text back to the client.
        logger.exception('verify_submit failed: %s', e)
        return JsonResponse({
            'success': False,
            'error': 'Verification error. Please retry; if the problem persists, contact your Technical Administrator.',
        })


def _log_verify(request, beneficiary, decision, attempt_number,
                liveness_passed, score, threshold, claimant_type, reason,
                stipend_event=None, all_scores=None, extra=None):
    """
    Write a structured AuditLog entry for a completed verification attempt.

    Captures the full decision context — score, threshold, liveness result, template
    breakdown, demo_mode flag, and (v2.1.11) the PAD/liveness diagnostic block
    so administrators can audit each decision later.

    `extra` is merged into `details` and is intended for the
    PAD/liveness/transaction signals required by the v2.1.11 logging spec:
      anti_spoof_score, liveness_result, tx_token_present, tx_valid,
      tx_used, tx_expired, challenge_motion_detected, static_sequence_detected,
      face_lost_count, facenet_score, final_decision, final_block_reason.
    """
    details = {
        'decision': decision,
        'final_decision': decision,
        'claimant_type': claimant_type,
        'score': round(score, 3) if score is not None else None,
        'facenet_score': round(score, 3) if score is not None else None,
        'threshold': threshold,
        'liveness_passed': liveness_passed,
        'liveness_result': 'passed' if liveness_passed else 'failed',
        'attempt_number': attempt_number,
        'reason': reason,
        'stipend_event': str(stipend_event.id) if stipend_event else None,
        'stipend_event_title': stipend_event.title if stipend_event else None,
        'demo_mode': getattr(django_settings, 'DEMO_MODE', True),
        'all_template_scores': [
            {'template': s['template'], 'score': round(s['score'], 3)}
            for s in (all_scores or [])
        ],
    }
    if extra:
        details.update(extra)
    AuditLog.log(
        action=AuditLog.ACTION_VERIFY,
        user=request.user,
        target_type='Beneficiary',
        target_id=beneficiary.id,
        details=details,
        request=request
    )


# ─── Result / Fallback / Override ────────────────────────────────────────────

@login_required
def verify_result(request, attempt_id):
    attempt = get_object_or_404(VerificationAttempt, pk=attempt_id)
    mock_denial = (
        attempt.similarity_score is None
        and 'model not loaded' in (attempt.decision_reason or '').lower()
    )

    # Detect specific denial sub-reasons for clearer UI labels
    denial_reason_label = ''
    if attempt.decision in (
        VerificationAttempt.DECISION_DENIED,
        VerificationAttempt.DECISION_NOT_VERIFIED,
    ):
        reason_lower = (attempt.decision_reason or '').lower()
        if attempt.liveness_passed is False:
            denial_reason_label = 'liveness_failed'
        elif attempt.similarity_score is not None and attempt.similarity_score < attempt.threshold_used:
            if attempt.claimant_type == VerificationAttempt.CLAIMANT_REPRESENTATIVE:
                denial_reason_label = 'rep_face_mismatch'
            else:
                denial_reason_label = 'face_mismatch'
        elif attempt.claimant_type == VerificationAttempt.CLAIMANT_REPRESENTATIVE:
            if 'no registered face data' in reason_lower or 'no face data' in reason_lower:
                denial_reason_label = 'rep_no_face'
            elif 'not found or no longer active' in reason_lower or 'deactivated' in reason_lower:
                denial_reason_label = 'rep_not_active'

    # Check if there is a pending manual verification request for this attempt
    pending_manual_request = ManualVerificationRequest.objects.filter(
        verification_attempt=attempt,
        status=ManualVerificationRequest.STATUS_PENDING,
    ).first()

    # Find the ClaimRecord linked to this attempt (if any)
    claim_record = ClaimRecord.objects.filter(verification_attempt=attempt).first()

    return render(request, 'verification/result.html', {
        'attempt': attempt,
        'mock_denial': mock_denial,
        'model_load_error': get_model_load_error() if mock_denial else None,
        'denial_reason_label': denial_reason_label,
        'pending_manual_request': pending_manual_request,
        'claim_record': claim_record,
        # v2.1.13 (Issue 2) — surface the auto-verify threshold so the score
        # meter can show both the lower threshold and the auto-verify line.
        'auto_verify_threshold': SystemConfig.get_auto_verify_threshold(),
    })


@login_required
@require_http_methods(['GET', 'POST'])
def verify_fallback(request, attempt_id):
    attempt = get_object_or_404(VerificationAttempt, pk=attempt_id)
    from django.contrib import messages

    # Representatives must use biometric face verification — ID-only is blocked.
    if attempt.claimant_type == VerificationAttempt.CLAIMANT_REPRESENTATIVE:
        messages.error(
            request,
            'Representatives must verify using face scan. '
            'ID-only verification is not permitted for representatives.'
        )
        return redirect('beneficiaries:beneficiary_detail', pk=attempt.beneficiary.pk)

    if request.method == 'POST':
        id_type = request.POST.get('id_type', '').strip()
        id_verified = request.POST.get('id_verified') == 'true'
        notes = request.POST.get('notes', '').strip()
        reason = request.POST.get('reason', '').strip()

        attempt.fallback_id_verified = id_verified
        attempt.fallback_id_type = id_type

        is_representative = False  # always False now (blocked above)

        if is_representative:
            # ── Representative path: direct ID verification — no face match involved ──
            attempt.decision = (
                VerificationAttempt.DECISION_VERIFIED if id_verified
                else VerificationAttempt.DECISION_DENIED
            )
            attempt.decision_reason = (
                f'Representative ID verification: {id_type} '
                f'{"accepted" if id_verified else "rejected"}. '
                f'Fallback used by {request.user.get_full_name() or request.user.username}.'
            )
            attempt.notes = notes
            attempt.save()

            AuditLog.log(
                action=AuditLog.ACTION_FALLBACK,
                user=request.user,
                target_type='VerificationAttempt',
                target_id=attempt.id,
                details={
                    'id_type': id_type,
                    'id_verified': id_verified,
                    'decision': attempt.decision,
                    'claimant_type': attempt.claimant_type,
                    'beneficiary_id': attempt.beneficiary.beneficiary_id,
                },
                request=request
            )
            # Representative path verified — create ClaimRecord atomically.
            if id_verified and attempt.stipend_event:
                with transaction.atomic():
                    _locked_ben = Beneficiary.objects.select_for_update().get(
                        pk=attempt.beneficiary.pk)
                    _locked_event, _event_ok, _ineligible_reason = _lock_event_for_finalization(attempt.stipend_event)
                    if not _event_ok:
                        # Phase B.5 — identity confirmed via ID check, but the
                        # event is no longer claimable now. Biometric/ID
                        # decision stays VERIFIED; only the payout is blocked.
                        _block_note = _payout_blocked_message(_ineligible_reason)
                        attempt.notes = ((attempt.notes + ' ') if attempt.notes else '') + _block_note
                        attempt.save(update_fields=['notes'])
                        messages.warning(request, _block_note)
                        AuditLog.log(
                            action=AuditLog.ACTION_REGISTER,
                            user=request.user,
                            target_type='VerificationAttempt',
                            target_id=attempt.id,
                            details={
                                'beneficiary_id': attempt.beneficiary.beneficiary_id,
                                'stipend_event': attempt.stipend_event.title,
                                'claimant_type': attempt.claimant_type,
                                'reason': f'Payout finalization blocked — {_ineligible_reason}',
                                'attempt_id': str(attempt.id),
                            },
                            request=request,
                        )
                    elif not ClaimRecord.objects.filter(
                        beneficiary=_locked_ben,
                        stipend_event=_locked_event,
                        status=ClaimRecord.STATUS_CLAIMED,
                    ).exists():
                        try:
                            new_claim = _create_claim_record(
                                beneficiary=_locked_ben,
                                stipend_event=_locked_event,
                                claimant_type=attempt.claimant_type,
                                representative=None,
                                claimed_by=request.user,
                                verification_attempt=attempt,
                                status=ClaimRecord.STATUS_CLAIMED,
                                verification_method=ClaimRecord.VERIFY_FALLBACK,
                            )
                        except IntegrityError:
                            # exists() passed, but a concurrent transaction
                            # committed a claim for this beneficiary/event
                            # first — DB UniqueConstraint is the final guard.
                            new_claim = None
                            AuditLog.log(
                                action=AuditLog.ACTION_DUPLICATE_PAYOUT_ATTEMPT,
                                user=request.user,
                                target_type='Beneficiary',
                                target_id=attempt.beneficiary.id,
                                details={
                                    'beneficiary_id': attempt.beneficiary.beneficiary_id,
                                    'stipend_event': attempt.stipend_event.title,
                                    'reason': 'Already claimed for this event (concurrent attempt, detected at commit)',
                                    'attempt_id': str(attempt.id),
                                    'via': 'representative_fallback',
                                },
                                request=request,
                            )
                        if new_claim is not None:
                            AuditLog.log(
                                action=AuditLog.ACTION_PAYOUT_FALLBACK_RELEASED,
                                user=request.user,
                                target_type='ClaimRecord',
                                target_id=new_claim.id,
                                details={
                                    'beneficiary_id': attempt.beneficiary.beneficiary_id,
                                    'stipend_event': attempt.stipend_event.title,
                                    'claimant_type': attempt.claimant_type,
                                    'via': 'representative_fallback',
                                    'amount': str(new_claim.amount),
                                    'reference_number': new_claim.reference_number,
                                },
                                request=request,
                            )
            return redirect('verification:verify_result', attempt_id=attempt.id)

        else:
            # ── Beneficiary path: face scan failed; ID check alone is insufficient ──
            # Staff cannot directly release the stipend.  Create a ManualVerificationRequest
            # for admin review.  Attempt stays in manual_review state until admin acts.

            if not id_verified:
                # ID also failed — outright denial, no request needed
                attempt.decision = VerificationAttempt.DECISION_DENIED
                attempt.decision_reason = (
                    f'Face match failed and ID verification also rejected '
                    f'({id_type}). Denied by {request.user.get_full_name() or request.user.username}.'
                )
                attempt.notes = notes
                attempt.save()
                AuditLog.log(
                    action=AuditLog.ACTION_FALLBACK,
                    user=request.user,
                    target_type='VerificationAttempt',
                    target_id=attempt.id,
                    details={
                        'id_type': id_type,
                        'id_verified': False,
                        'decision': attempt.decision,
                        'claimant_type': attempt.claimant_type,
                        'beneficiary_id': attempt.beneficiary.beneficiary_id,
                    },
                    request=request
                )
                return redirect('verification:verify_result', attempt_id=attempt.id)

            # ID passed — submit pending manual verification request for admin approval
            if not reason:
                reason = (
                    f'Face match failed. ID check ({id_type}) passed by staff. '
                    'Requesting admin approval to release stipend.'
                )

            mvr = ManualVerificationRequest.objects.create(
                beneficiary=attempt.beneficiary,
                claimant_type=attempt.claimant_type,
                requested_by=request.user,
                verification_attempt=attempt,
                stipend_event=attempt.stipend_event,
                reason=reason,
                notes=notes,
                similarity_score=attempt.similarity_score,
                liveness_passed=attempt.liveness_passed,
                liveness_score=attempt.liveness_score,
                id_type_checked=id_type,
                id_verified=True,
            )

            # Keep attempt in manual_review — decision only updates when admin approves/rejects
            attempt.decision = VerificationAttempt.DECISION_MANUAL_REVIEW
            attempt.decision_reason = (
                f'Pending admin approval — manual verification request submitted by '
                f'{request.user.get_full_name() or request.user.username}. '
                f'ID check ({id_type}) passed.'
            )
            attempt.notes = notes
            attempt.save()

            AuditLog.log(
                action=AuditLog.ACTION_MANUAL_VERIFY_REQUEST,
                user=request.user,
                target_type='ManualVerificationRequest',
                target_id=mvr.id,
                details={
                    'beneficiary_id': attempt.beneficiary.beneficiary_id,
                    'id_type': id_type,
                    'similarity_score': attempt.similarity_score,
                    'liveness_passed': attempt.liveness_passed,
                    'reason': reason,
                },
                request=request
            )
            notify_admins(
                category=Notification.CATEGORY_VERIFICATION_REVIEW,
                title='Verification review needed',
                message=f'{attempt.beneficiary.full_name} — fallback ID check submitted, awaiting approval.',
                url=reverse('verification:manual_verify_review', args=[mvr.id]),
                dedupe_key=f'manual_verify_request:{mvr.id}',
            )

            messages.info(
                request,
                'Manual verification request submitted. An admin must approve '
                'before the stipend can be released.'
            )
            return redirect('verification:verify_result', attempt_id=attempt.id)

    rep_id_type = ''
    rep_id_number = ''
    rep_name = ''
    if attempt.claimant_type == VerificationAttempt.CLAIMANT_REPRESENTATIVE:
        rep_id_type = attempt.beneficiary.rep_id_type
        rep_id_number = attempt.beneficiary.rep_id_number
        rep_name = f'{attempt.beneficiary.rep_first_name} {attempt.beneficiary.rep_last_name}'.strip()

    return render(request, 'verification/fallback.html', {
        'attempt': attempt,
        'rep_id_type': rep_id_type,
        'rep_id_number': rep_id_number,
        'rep_name': rep_name,
    })


@login_required
@require_http_methods(['GET', 'POST'])
def admin_override(request, attempt_id):
    # Financial/decision override authority — Technical Administrator keeps
    # read access to the verification result page but not this mutation.
    if not request.user.has_financial_authority:
        from django.contrib import messages
        messages.error(request, 'Only the President or Admin can override a verification decision.')
        return redirect('beneficiaries:dashboard')

    attempt = get_object_or_404(VerificationAttempt, pk=attempt_id)

    if attempt.overridden:
        from django.contrib import messages
        messages.error(
            request,
            'This verification attempt has already been overridden and cannot be overridden again.'
        )
        return redirect('verification:verify_result', attempt_id=attempt.id)

    # Segregation of duties: the admin who performed (or initiated) an attempt
    # cannot also override it. President sign-off is required so a single
    # operator cannot both fail and unilaterally release the same payment.
    if attempt.performed_by_id == request.user.id and not request.user.is_president:
        from django.contrib import messages
        messages.error(
            request,
            'You performed this verification attempt and cannot also override the decision. '
            'A different admin (President) must review it.'
        )
        return redirect('verification:verify_result', attempt_id=attempt.id)

    if request.method == 'POST':
        decision = request.POST.get('decision', '')
        reason = request.POST.get('reason', '').strip()

        if decision not in (
            VerificationAttempt.DECISION_VERIFIED,
            VerificationAttempt.DECISION_DENIED,
            VerificationAttempt.DECISION_NOT_VERIFIED,
        ):
            from django.contrib import messages
            messages.error(request, 'Invalid decision selected.')
            return render(request, 'verification/override.html', {'attempt': attempt})

        if len(reason) < 20:
            from django.contrib import messages
            messages.error(request, 'Override reason must be at least 20 characters.')
            return render(request, 'verification/override.html', {'attempt': attempt})

        attempt.overridden = True
        attempt.override_by = request.user
        attempt.override_reason = reason
        attempt.override_at = timezone.now()
        attempt.decision = decision
        attempt.decision_reason = (
            f'Admin override by {request.user.get_full_name() or request.user.username}: '
            f'{reason[:200]}'
        )
        attempt.save()

        AuditLog.log(
            action=AuditLog.ACTION_OVERRIDE,
            user=request.user,
            target_type='VerificationAttempt',
            target_id=attempt.id,
            details={
                'decision': decision,
                'reason': reason,
                'beneficiary': str(attempt.beneficiary.id),
                'beneficiary_id': attempt.beneficiary.beneficiary_id,
                'original_score': attempt.similarity_score,
            },
            request=request
        )
        return redirect('verification:verify_result', attempt_id=attempt.id)

    return render(request, 'verification/override.html', {'attempt': attempt})


@login_required
@require_http_methods(['GET', 'POST'])
def override_release_payout(request, attempt_id):
    """
    Separate payout-release action for admin-overridden VERIFIED attempts.

    admin_override() corrects the decision and logs ACTION_OVERRIDE.
    This view creates the ClaimRecord and logs ACTION_CLAIM as a distinct
    second step, preserving separate audit trails for the two events.

    Security:
    - Requires has_financial_authority (President/Admin — not Technical
      Administrator; payout release is a financial mutation, not a
      diagnostic action).
    - Applies same SoD rule as admin_override: performed_by cannot release
      (unless President).
    - Runs inside transaction.atomic() with select_for_update() on both
      VerificationAttempt and Beneficiary to prevent concurrent double-release.
    - DB UniqueConstraint on (beneficiary, stipend_event, status='claimed')
      is the final safety net; IntegrityError is caught and surfaced as a
      user-facing error rather than a 500.
    """
    from django.contrib import messages
    from django.db import IntegrityError

    if not request.user.has_financial_authority:
        messages.error(request, 'Only the President or Admin can release an overridden payout.')
        return redirect('beneficiaries:dashboard')

    attempt = get_object_or_404(VerificationAttempt, pk=attempt_id)

    def _preflight_guards(attempt, user, redirect_on_fail):
        """Run all pre-release guards. Return an error redirect or None."""
        if attempt.decision != VerificationAttempt.DECISION_VERIFIED:
            messages.error(
                request,
                'Payout can only be released for a VERIFIED attempt. '
                f'Current decision: {attempt.get_decision_display() or attempt.decision}.'
            )
            return redirect_on_fail

        if not attempt.overridden:
            messages.error(
                request,
                'This attempt has not been overridden. '
                'Use the Override Decision action first.'
            )
            return redirect_on_fail

        if attempt.stipend_event_id is None:
            messages.error(
                request,
                'No stipend event is linked to this attempt. '
                'A payout cannot be created without a stipend event.'
            )
            return redirect_on_fail

        if not attempt.beneficiary.is_eligible_to_claim:
            if attempt.beneficiary.duplicate_review_required:
                messages.error(
                    request,
                    f'{attempt.beneficiary.full_name} has an unresolved duplicate-face '
                    'conflict. Payout release is blocked until an administrator resolves '
                    'it in the Duplicate Face Review queue — a manual-review override on '
                    'this attempt does not resolve a separate duplicate-identity conflict.'
                )
            else:
                messages.error(
                    request,
                    f'{attempt.beneficiary.full_name} is no longer eligible to claim '
                    f'(status: {attempt.beneficiary.get_status_display()}). '
                    'The beneficiary must be Active with consent given.'
                )
            return redirect_on_fail

        if ClaimRecord.objects.filter(verification_attempt=attempt).exists():
            messages.warning(
                request,
                'A claim record already exists for this attempt. '
                'No further payout release is needed.'
            )
            return redirect_on_fail

        # Segregation of duties: the operator who performed the original
        # biometric verification cannot also release the payout.
        # President is exempt (consistent with admin_override SoD rule).
        if attempt.performed_by_id == user.id and not user.is_president:
            messages.error(
                request,
                'You performed this verification attempt and cannot also release '
                'the payout. A different admin must complete the release.'
            )
            return redirect_on_fail

        return None

    result_redirect = redirect('verification:verify_result', attempt_id=attempt.id)
    guard_result = _preflight_guards(attempt, request.user, result_redirect)
    if guard_result:
        return guard_result

    if request.method == 'POST':
        try:
            with transaction.atomic():
                # Re-fetch with row locks inside the atomic block to prevent
                # concurrent double-release from two browser tabs / admins.
                locked_attempt = (
                    VerificationAttempt.objects
                    .select_for_update()
                    .get(pk=attempt.pk)
                )
                locked_ben = (
                    Beneficiary.objects
                    .select_for_update()
                    .get(pk=attempt.beneficiary_id)
                )

                # Re-run guards on the freshly-locked objects inside the
                # atomic block — state may have changed between GET and POST.
                inner_redirect = _preflight_guards(locked_attempt, request.user, result_redirect)
                if inner_redirect:
                    return inner_redirect

                # Additional event-level duplicate guard (catches concurrent
                # releases via the representative path or a different attempt).
                if ClaimRecord.objects.filter(
                    beneficiary=locked_ben,
                    stipend_event=locked_attempt.stipend_event,
                    status=ClaimRecord.STATUS_CLAIMED,
                ).exists():
                    messages.error(
                        request,
                        f'{locked_ben.full_name} has already received a payout '
                        f'for "{locked_attempt.stipend_event.title}". '
                        'Duplicate release prevented.'
                    )
                    return result_redirect

                # Phase B.5 — the override decision (VERIFIED) may have been
                # recorded while the event was still open; it must be
                # revalidated here, immediately before payout release. A
                # closed/inactive/unapproved event blocks the release but does
                # not undo the override — the override decision/audit record
                # made by admin_override() is left exactly as it is.
                locked_event, _event_ok, _ineligible_reason = _lock_event_for_finalization(
                    locked_attempt.stipend_event
                )
                if not _event_ok:
                    messages.error(
                        request,
                        f'Payout cannot be released for {locked_ben.full_name} — '
                        f'{StipendEvent.describe_claim_ineligible_reason(_ineligible_reason)}. '
                        'The admin override decision remains recorded; a new stipend event '
                        'may be selected via a fresh verification if this beneficiary is '
                        'still entitled to a payout.'
                    )
                    AuditLog.log(
                        action=AuditLog.ACTION_REGISTER,
                        user=request.user,
                        target_type='VerificationAttempt',
                        target_id=locked_attempt.id,
                        details={
                            'beneficiary_id': locked_ben.beneficiary_id,
                            'stipend_event': locked_attempt.stipend_event.title,
                            'via': 'override_release_payout',
                            'reason': f'Payout finalization blocked — {_ineligible_reason}',
                        },
                        request=request,
                    )
                    return result_redirect

                new_claim = _create_claim_record(
                    beneficiary=locked_ben,
                    stipend_event=locked_event,
                    claimant_type=locked_attempt.claimant_type,
                    representative=locked_attempt.representative,
                    claimed_by=request.user,
                    verification_attempt=locked_attempt,
                    approved_by=request.user,
                    approved_at=timezone.now(),
                    status=ClaimRecord.STATUS_CLAIMED,
                    verification_method=ClaimRecord.VERIFY_OVERRIDE,
                    notes=(
                        f'Payout released via admin override by '
                        f'{request.user.get_full_name() or request.user.username}.'
                    ),
                )

                AuditLog.log(
                    action=AuditLog.ACTION_CLAIM,
                    user=request.user,
                    target_type='ClaimRecord',
                    target_id=new_claim.id,
                    details={
                        'beneficiary_id': locked_ben.beneficiary_id,
                        'stipend_event': locked_attempt.stipend_event.title,
                        'claimant_type': locked_attempt.claimant_type,
                        'via': 'override_release_payout',
                        'amount': str(new_claim.amount),
                        'reference_number': new_claim.reference_number,
                        'verification_method': new_claim.verification_method,
                        'original_override_by': str(locked_attempt.override_by_id),
                    },
                    request=request,
                )

        except IntegrityError:
            messages.error(
                request,
                'A payout for this beneficiary and event was recorded by a '
                'concurrent action. No duplicate claim was created.'
            )
            return result_redirect

        messages.success(
            request,
            f'Payout released. Reference: {new_claim.reference_number}.'
        )
        return redirect('verification:verify_result', attempt_id=attempt.id)

    # GET — render confirmation page
    claim_record = ClaimRecord.objects.filter(verification_attempt=attempt).first()
    return render(request, 'verification/override_release_confirm.html', {
        'attempt': attempt,
        'claim_record': claim_record,
    })


# ─── System Config ────────────────────────────────────────────────────────────

@login_required
def verify_config(request):
    if not request.user.is_admin:
        from django.contrib import messages
        messages.error(request, 'Admin access required.')
        return redirect('beneficiaries:dashboard')

    current_threshold = SystemConfig.get_threshold()
    demo_mode = _get_demo_mode()

    if request.method == 'POST':
        try:
            new_threshold = float(request.POST.get('threshold', 0.60))
            if not 0.1 <= new_threshold <= 1.0:
                raise ValueError('Threshold must be between 0.1 and 1.0')

            config, _ = SystemConfig.objects.get_or_create(
                key='verification_threshold',
                defaults={'description': 'Cosine similarity threshold for FaceNet face matching'}
            )
            old_value = config.value
            config.value = str(new_threshold)
            config.updated_by = request.user
            config.save()

            AuditLog.log(
                action=AuditLog.ACTION_CONFIG_CHANGE,
                user=request.user,
                details={
                    'key': 'verification_threshold',
                    'old_value': old_value,
                    'new_value': new_threshold,
                },
                request=request
            )
            from django.contrib import messages
            messages.success(request, f'Threshold updated to {new_threshold:.2f}')
            current_threshold = new_threshold
        except ValueError as e:
            from django.contrib import messages
            messages.error(request, str(e))

    review_band_min = round(current_threshold * 0.85, 3)
    review_band_max = round(current_threshold - 0.001, 3)
    auto_verify_threshold = SystemConfig.get_auto_verify_threshold()
    using_mock = is_using_mock_model()
    return render(request, 'verification/config.html', {
        # 'current_threshold' is kept as the form's edit-value name (POST
        # handler above reads/writes 'verification_threshold' under this
        # name); 'manual_review_threshold' is the same value under an
        # unambiguous display name — this is NOT the threshold that triggers
        # automatic verification (see 'auto_verify_threshold' below), it is
        # the floor at/above which a score becomes eligible for the review
        # pipeline instead of an outright Not Verified.
        'current_threshold': current_threshold,
        'manual_review_threshold': current_threshold,
        'threshold_review_min': review_band_min,
        'threshold_review_max': review_band_max,
        'auto_verify_threshold': auto_verify_threshold,  # v2.1.13 — the ONLY threshold that triggers automatic VERIFIED
        'liveness_required': _get_liveness_required(),
        'anti_spoof_threshold': getattr(django_settings, 'ANTI_SPOOF_THRESHOLD', 0.25),
        'demo_mode': demo_mode,
        'demo_threshold': getattr(django_settings, 'DEMO_THRESHOLD', 0.60),
        'demo_auto_verify_threshold': getattr(django_settings, 'DEMO_AUTO_VERIFY_THRESHOLD', 0.80),
        'prod_threshold': getattr(django_settings, 'VERIFICATION_THRESHOLD', 0.75),
        'prod_auto_verify_threshold': getattr(django_settings, 'AUTO_VERIFY_THRESHOLD', 0.88),
        'max_retries': getattr(django_settings, 'MAX_RETRY_ATTEMPTS', 2),
        'using_mock': using_mock,
        'model_load_error': get_model_load_error() if using_mock else None,
    })


# ─── Stipend Events ───────────────────────────────────────────────────────────

import datetime as _dt
from zoneinfo import ZoneInfo as _ZoneInfo

_PAYOUT_EARLIEST = _dt.time(7, 0)
_PAYOUT_LATEST   = _dt.time(20, 0)
_MANILA_TZ = _ZoneInfo('Asia/Manila')


def _current_president():
    """
    Resolve the single currently-assigned President via the System Role field —
    the same source of truth `is_president` / `active_president_exists` already
    use, so it stays consistent with the "only one active President" invariant
    enforced at account-edit time (v2.1.12). Returns None if the seat is
    currently vacant — callers must handle that (no notification recipient).
    """
    from accounts.models import CustomUser
    return CustomUser.objects.filter(
        role=CustomUser.ROLE_PRESIDENT,
        is_active=True,
        account_status=CustomUser.STATUS_ACTIVE,
    ).first()


def _stipend_pending_dedupe_key(event_id):
    return f'stipend_pending_approval:{event_id}'


def _resolve_stipend_pending_notifications(event):
    """Clear the unread badge for the approval-required notification once the
    President has acted, so the bell never keeps showing a stale item that
    was already approved or rejected (v2.2.0 Post-UAT Phase 3)."""
    from logs.notifications import resolve_notification
    resolve_notification(_stipend_pending_dedupe_key(event.pk))
    resolve_notification(f'approval_reminder_stipend:{event.pk}')


def _validate_payout_window(payout_start, payout_end, payout_start_time, payout_end_time,
                             orig_payout_start=None, event_date=None, orig_event_date=None):
    """
    Validate payout date/time window. Returns a list of user-facing error strings.
    orig_payout_start: pass the original payout_start_date on edit so an unchanged
    past date is not re-rejected.
    event_date / orig_event_date: the schedule's main `date` (new/original) — used
    as the effective payout-start fallback when payout_start_date is left blank,
    so the same-day closing-time check below cannot be bypassed by omitting an
    explicit payout window (see v2.2.0 Phase 2).
    """
    errors = []
    today = timezone.localdate()

    if payout_start and payout_start < today and payout_start != orig_payout_start:
        errors.append('Payout start date cannot be in the past.')

    if payout_start and payout_end and payout_end < payout_start:
        errors.append('Payout end date cannot be before payout start date.')

    if bool(payout_start_time) != bool(payout_end_time):
        errors.append('If a payout time is set, both start and end times are required.')
        return errors

    if payout_start_time and payout_end_time:
        if payout_start_time < _PAYOUT_EARLIEST:
            errors.append('Payout start time cannot be earlier than 7:00 AM.')
        if payout_end_time > _PAYOUT_LATEST:
            errors.append('Payout end time cannot be later than 8:00 PM.')
        if payout_end_time <= payout_start_time:
            errors.append('Payout end time must be after payout start time.')
        else:
            _window = (
                _dt.datetime.combine(_dt.date.today(), payout_end_time)
                - _dt.datetime.combine(_dt.date.today(), payout_start_time)
            )
            if _window < _dt.timedelta(hours=1):
                errors.append('Payout time window must be at least 1 hour.')

        if payout_start and payout_start == today:
            now_manila = _dt.datetime.now(tz=_MANILA_TZ).time().replace(second=0, microsecond=0)
            if payout_start_time <= now_manila:
                errors.append("Payout start time has already passed for today's date.")
    else:
        # v2.2.0 Phase 2: a blank payout time window defaults to "active all
        # day" for claim purposes (StipendEvent.is_within_time_window), but a
        # *new* same-day schedule must still respect the official system-wide
        # claiming hours (7:00 AM-8:00 PM) at the moment it is created/edited.
        # Without this, leaving both time fields blank silently bypassed the
        # office-hours rule entirely — this is enforced server-side so it
        # cannot be worked around via blank fields, direct POST, or API calls.
        effective_start = payout_start or event_date
        orig_effective_start = orig_payout_start or orig_event_date
        if effective_start == today and effective_start != orig_effective_start:
            now_manila = _dt.datetime.now(tz=_MANILA_TZ).time().replace(second=0, microsecond=0)
            if now_manila >= _PAYOUT_LATEST:
                errors.append(
                    f"The claiming period for {today.strftime('%B %d')} has already ended "
                    "(office hours are 7:00 AM–8:00 PM). Please select a future payout date."
                )

    return errors


@login_required
def stipend_list(request):
    from django.db.models.functions import Coalesce
    from django.core.paginator import Paginator
    today = timezone.localdate()
    upcoming = (
        StipendEvent.objects
        .filter(is_active=True, approval_status=StipendEvent.APPROVAL_APPROVED)
        .annotate(effective_end=Coalesce('payout_end_date', 'date'))
        .filter(effective_end__gte=today)
        .order_by('date')
    )
    # Base past queryset (unfiltered) — used both for the years filter list
    # and for the calendar, which should show everything regardless of the
    # Past Events search/filter controls below.
    past_base_qs = (
        StipendEvent.objects
        .annotate(effective_end=Coalesce('payout_end_date', 'date'))
        .filter(effective_end__lt=today)
        .order_by('-effective_end')
    )
    past_years = sorted({d.year for d in past_base_qs.dates('date', 'year')}, reverse=True)

    past_query = (request.GET.get('past_q') or '').strip()
    past_month = (request.GET.get('past_month') or '').strip()
    past_year = (request.GET.get('past_year') or '').strip()
    past_filtered_qs = past_base_qs
    if past_query:
        past_filtered_qs = past_filtered_qs.filter(title__icontains=past_query)
    if past_month.isdigit():
        past_filtered_qs = past_filtered_qs.filter(date__month=int(past_month))
    if past_year.isdigit():
        past_filtered_qs = past_filtered_qs.filter(date__year=int(past_year))

    past_paginator = Paginator(past_filtered_qs, 10)
    past = past_paginator.get_page(request.GET.get('past_page'))
    past_page_range = list(past_paginator.get_elided_page_range(past.number, on_each_side=1, on_ends=1))

    # Inactive events with a future/current schedule — admin-only visibility.
    inactive = (
        StipendEvent.objects
        .filter(is_active=False)
        .annotate(effective_end=Coalesce('payout_end_date', 'date'))
        .filter(effective_end__gte=today)
        .order_by('date')
    )
    # Pending approval — Issue 5
    pending_approval = (
        StipendEvent.objects
        .filter(approval_status=StipendEvent.APPROVAL_PENDING)
        .order_by('date')
    )
    active_event = StipendEvent.get_active_event_for_date(today)

    # Calendar view — same events visible in the list sections above (not
    # affected by the Past Events search/filter controls), serialized for
    # the client-side month calendar. No new statuses are invented here;
    # these mirror the badges already used in the list sections.
    calendar_source = list(upcoming) + list(past_base_qs)
    if request.user.is_admin:
        calendar_source += list(inactive) + list(pending_approval)
    calendar_events = []
    seen_ids = set()
    for event in calendar_source:
        if event.id in seen_ids:
            continue
        seen_ids.add(event.id)
        if event.approval_status == StipendEvent.APPROVAL_REJECTED:
            status, css_class = 'Rejected', 'danger'
        elif event.approval_status == StipendEvent.APPROVAL_PENDING:
            status, css_class = 'Pending Approval', 'warning'
        elif not event.is_active:
            status, css_class = 'Inactive', 'secondary'
        elif event.get_claim_end() < today:
            status, css_class = 'Completed', 'success'
        else:
            status, css_class = 'Upcoming', 'primary'
        calendar_events.append({
            'id': str(event.id),
            'title': event.title,
            'start': event.get_claim_start().isoformat(),
            'end': event.get_claim_end().isoformat(),
            'status': status,
            'css_class': css_class,
            'url': reverse('verification:stipend_edit', args=[event.pk]) if request.user.is_admin else '',
        })

    from urllib.parse import urlencode
    import calendar as _calendar_mod
    past_filter_params = {}
    if past_query:
        past_filter_params['past_q'] = past_query
    if past_month.isdigit():
        past_filter_params['past_month'] = past_month
    if past_year.isdigit():
        past_filter_params['past_year'] = past_year
    past_querystring = urlencode(past_filter_params)
    month_choices = [(i, _calendar_mod.month_name[i]) for i in range(1, 13)]

    return render(request, 'verification/stipend_list.html', {
        'upcoming': upcoming,
        'past': past,
        'past_query': past_query,
        'past_month': past_month,
        'past_year': past_year,
        'past_years': past_years,
        'past_page_range': past_page_range,
        'past_querystring': past_querystring,
        'month_choices': month_choices,
        'inactive': inactive,
        'pending_approval': pending_approval,
        'today': today,
        'active_event': active_event,
        'calendar_events': calendar_events,
    })


@login_required
@require_POST
def stipend_approve(request, event_id):
    """President approves a pending schedule (Issue 5).

    v2.2.0 Phase 2: if the payout window has already ended, approval does
    NOT happen silently — a late-approval reason is required and recorded
    on the event and in the audit log (never auto-approved past its date)."""
    from django.contrib import messages
    event = get_object_or_404(StipendEvent, pk=event_id)
    if not request.user.is_president:
        messages.error(request, 'Only the President may approve a stipend schedule.')
        return redirect('verification:stipend_list')
    if event.approval_status != StipendEvent.APPROVAL_PENDING:
        messages.warning(request, 'This schedule is not pending approval.')
        return redirect('verification:stipend_list')

    today = timezone.localdate()
    is_overdue = event.get_claim_end() < today
    late_reason = (request.POST.get('late_approval_reason') or '').strip()
    if is_overdue and len(late_reason) < 10:
        messages.error(
            request,
            f'This schedule\'s payout window ended on {event.get_claim_end()}. '
            'A late-approval reason (at least 10 characters) is required before it can be approved.'
        )
        return redirect('verification:stipend_list')

    event.approval_status = StipendEvent.APPROVAL_APPROVED
    event.approved_by = request.user
    event.approved_at = timezone.now()
    event.published_at = timezone.now()
    update_fields = ['approval_status', 'approved_by', 'approved_at', 'published_at']
    if is_overdue:
        event.late_approval_reason = late_reason
        update_fields.append('late_approval_reason')
    event.save(update_fields=update_fields)
    AuditLog.log(
        action=AuditLog.ACTION_CONFIG_CHANGE,
        user=request.user,
        details={
            'event': 'schedule_approved',
            'stipend_event': event.title,
            'date': str(event.date),
            'late_approval': is_overdue,
            'late_approval_reason': late_reason if is_overdue else '',
        },
        request=request,
    )
    _resolve_stipend_pending_notifications(event)
    if is_overdue:
        messages.success(
            request,
            f'Schedule "{event.title}" approved after its scheduled date. Reason recorded in the audit log.'
        )
    else:
        messages.success(request, f'Schedule "{event.title}" approved and published.')
    return redirect('verification:stipend_list')


@login_required
@require_POST
def stipend_reject(request, event_id):
    """President rejects a pending schedule (Issue 5)."""
    from django.contrib import messages
    event = get_object_or_404(StipendEvent, pk=event_id)
    if not request.user.is_president:
        messages.error(request, 'Only the President may reject a stipend schedule.')
        return redirect('verification:stipend_list')
    if event.approval_status != StipendEvent.APPROVAL_PENDING:
        messages.warning(request, 'This schedule is not pending approval.')
        return redirect('verification:stipend_list')
    reason = (request.POST.get('reason') or '').strip()
    if len(reason) < 5:
        messages.error(request, 'A rejection reason of at least 5 characters is required.')
        return redirect('verification:stipend_list')
    event.approval_status = StipendEvent.APPROVAL_REJECTED
    event.approved_by = request.user
    event.approved_at = timezone.now()
    event.rejection_reason = reason
    event.is_active = False
    event.save(update_fields=['approval_status', 'approved_by', 'approved_at', 'rejection_reason', 'is_active'])
    AuditLog.log(
        action=AuditLog.ACTION_CONFIG_CHANGE,
        user=request.user,
        details={
            'event': 'schedule_rejected',
            'stipend_event': event.title,
            'date': str(event.date),
            'reason': reason,
        },
        request=request,
    )
    _resolve_stipend_pending_notifications(event)
    messages.warning(request, f'Schedule "{event.title}" rejected.')
    return redirect('verification:stipend_list')


@login_required
@require_http_methods(['GET', 'POST'])
def stipend_create(request):
    # Official stipend-event creation is a barangay operational decision —
    # Technical Administrator keeps read access to stipend_list but not this.
    if not request.user.has_financial_authority:
        from django.contrib import messages
        messages.error(request, 'Only the President or Admin can create a stipend event.')
        return redirect('verification:stipend_list')

    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        date_str = request.POST.get('date', '')
        description = request.POST.get('description', '').strip()
        event_type = request.POST.get('event_type', StipendEvent.EVENT_TYPE_REGULAR)
        custom_event_type = request.POST.get('custom_event_type', '').strip()
        payout_start_str = request.POST.get('payout_start_date', '').strip()
        payout_end_str = request.POST.get('payout_end_date', '').strip()
        payout_start_time_str = request.POST.get('payout_start_time', '').strip()
        payout_end_time_str = request.POST.get('payout_end_time', '').strip()
        amount_str = request.POST.get('amount', '0').strip()

        _valid_types = (
            StipendEvent.EVENT_TYPE_REGULAR,
            StipendEvent.EVENT_TYPE_BIRTHDAY,
            StipendEvent.EVENT_TYPE_CUSTOM,
        )
        if event_type not in _valid_types:
            event_type = StipendEvent.EVENT_TYPE_REGULAR

        # Re-echo whatever the operator already typed back into the form on any
        # validation failure below, so a rejected submission doesn't force
        # re-entering the whole form from scratch. Best-effort per field (a
        # malformed date/time simply isn't echoed back as that field) — this
        # never affects which validation error fires or how the event saves.
        import datetime as _dt_echo
        from types import SimpleNamespace as _SimpleNamespace

        def _echo_parse(parser, raw):
            try:
                return parser(raw) if raw else None
            except ValueError:
                return None

        _echo = _SimpleNamespace(
            title=title, event_type=event_type, custom_event_type=custom_event_type,
            amount=amount_str,
            date=_echo_parse(_dt_echo.date.fromisoformat, date_str),
            payout_start_date=_echo_parse(_dt_echo.date.fromisoformat, payout_start_str),
            payout_end_date=_echo_parse(_dt_echo.date.fromisoformat, payout_end_str),
            payout_start_time=_echo_parse(_dt_echo.time.fromisoformat, payout_start_time_str),
            payout_end_time=_echo_parse(_dt_echo.time.fromisoformat, payout_end_time_str),
        )

        if not title or not date_str:
            from django.contrib import messages
            messages.error(request, 'Title and date are required.')
            return render(request, 'verification/stipend_form.html', {'action': 'Create', 'event': _echo})

        if event_type == StipendEvent.EVENT_TYPE_CUSTOM and not custom_event_type:
            from django.contrib import messages
            messages.error(request, 'Custom Distribution Name is required when "Other / Custom" is selected.')
            return render(request, 'verification/stipend_form.html', {'action': 'Create', 'event': _echo})

        if custom_event_type and len(custom_event_type) > 100:
            from django.contrib import messages
            messages.error(request, 'Custom Distribution Name must be 100 characters or fewer.')
            return render(request, 'verification/stipend_form.html', {'action': 'Create', 'event': _echo})

        import datetime
        from decimal import Decimal, InvalidOperation
        try:
            event_date = datetime.date.fromisoformat(date_str)
            payout_start = datetime.date.fromisoformat(payout_start_str) if payout_start_str else None
            payout_end = datetime.date.fromisoformat(payout_end_str) if payout_end_str else None
            payout_start_time = datetime.time.fromisoformat(payout_start_time_str) if payout_start_time_str else None
            payout_end_time = datetime.time.fromisoformat(payout_end_time_str) if payout_end_time_str else None
        except ValueError:
            from django.contrib import messages
            messages.error(request, 'Invalid date or time format.')
            return render(request, 'verification/stipend_form.html', {'action': 'Create', 'event': _echo})

        try:
            amount = Decimal(amount_str or '0')
            if amount < 0:
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            from django.contrib import messages
            messages.error(request, 'Amount must be a non-negative number.')
            return render(request, 'verification/stipend_form.html', {'action': 'Create', 'event': _echo})

        _payout_errors = _validate_payout_window(
            payout_start, payout_end, payout_start_time, payout_end_time,
            event_date=event_date,
        )
        if _payout_errors:
            from django.contrib import messages
            for _e in _payout_errors:
                messages.error(request, _e)
            return render(request, 'verification/stipend_form.html', {'action': 'Create', 'event': _echo})

        # Issue 5: Admin-created schedule starts in Pending Approval.
        # President-created schedule is published immediately.
        if request.user.is_president:
            _approval_status = StipendEvent.APPROVAL_APPROVED
            _approved_by = request.user
            _approved_at = timezone.now()
            _published_at = timezone.now()
        else:
            _approval_status = StipendEvent.APPROVAL_PENDING
            _approved_by = None
            _approved_at = None
            _published_at = None

        event = StipendEvent.objects.create(
            title=title,
            date=event_date,
            event_type=event_type,
            custom_event_type=custom_event_type if event_type == StipendEvent.EVENT_TYPE_CUSTOM else '',
            description=description,
            payout_start_date=payout_start,
            payout_end_date=payout_end,
            payout_start_time=payout_start_time,
            payout_end_time=payout_end_time,
            amount=amount,
            created_by=request.user,
            approval_status=_approval_status,
            approved_by=_approved_by,
            approved_at=_approved_at,
            published_at=_published_at,
        )
        AuditLog.log(
            action=AuditLog.ACTION_CONFIG_CHANGE,
            user=request.user,
            details={
                'stipend_event': title,
                'date': str(event_date),
                'event_type': event_type,
                'amount': str(amount),
                'payout_start': str(payout_start) if payout_start else None,
                'payout_end': str(payout_end) if payout_end else None,
                'approval_status': _approval_status,
            },
            request=request
        )
        from django.contrib import messages
        if _approval_status == StipendEvent.APPROVAL_PENDING:
            # v2.2.0 Post-UAT Phase 3: the dashboard's "N payout schedules
            # awaiting approval" count was a live query, so it always looked
            # right — but nothing ever notified the President, so the bell
            # stayed silent for the exact action it exists to flag. Resolve
            # the current System Role President (never a hard-coded user) and
            # notify only them, not every admin/IT user.
            _president = _current_president()
            if _president:
                from logs.notifications import notify_user
                from logs.models import Notification
                notify_user(
                    _president,
                    category=Notification.CATEGORY_APPROVAL_REQUIRED,
                    title=f'Payout schedule "{title}" awaiting your approval',
                    message=(
                        f'{request.user.get_full_name() or request.user.username} created a '
                        f'payout schedule for {event_date.strftime("%B %d, %Y")}. It cannot be '
                        'used for claiming until you approve it.'
                    ),
                    url=reverse('verification:stipend_list'),
                    dedupe_key=_stipend_pending_dedupe_key(event.pk),
                )
            messages.success(
                request,
                f'Stipend event "{title}" created and is now PENDING President approval. '
                'It will not be usable for claiming until approved.'
            )
        else:
            messages.success(request, f'Stipend event "{title}" created and published.')
        return redirect('verification:stipend_list')

    return render(request, 'verification/stipend_form.html', {'action': 'Create'})


@login_required
@require_http_methods(['GET', 'POST'])
def stipend_edit(request, event_id):
    if not request.user.has_financial_authority:
        from django.contrib import messages
        messages.error(request, 'Only the President or Admin can edit a stipend event.')
        return redirect('verification:stipend_list')

    event = get_object_or_404(StipendEvent, pk=event_id)
    _orig_payout_start = event.payout_start_date
    _orig_event_date = event.date

    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        date_str = request.POST.get('date', '')
        description = request.POST.get('description', '').strip()
        is_active = request.POST.get('is_active') == 'on'
        event_type = request.POST.get('event_type', StipendEvent.EVENT_TYPE_REGULAR)
        custom_event_type = request.POST.get('custom_event_type', '').strip()
        payout_start_str = request.POST.get('payout_start_date', '').strip()
        payout_end_str = request.POST.get('payout_end_date', '').strip()
        payout_start_time_str = request.POST.get('payout_start_time', '').strip()
        payout_end_time_str = request.POST.get('payout_end_time', '').strip()
        amount_str = request.POST.get('amount', '0').strip()

        _valid_types = (
            StipendEvent.EVENT_TYPE_REGULAR,
            StipendEvent.EVENT_TYPE_BIRTHDAY,
            StipendEvent.EVENT_TYPE_CUSTOM,
        )
        if event_type not in _valid_types:
            event_type = StipendEvent.EVENT_TYPE_REGULAR

        if not title or not date_str:
            from django.contrib import messages
            messages.error(request, 'Title and date are required.')
            return render(request, 'verification/stipend_form.html', {'action': 'Edit', 'event': event})

        if event_type == StipendEvent.EVENT_TYPE_CUSTOM and not custom_event_type:
            from django.contrib import messages
            messages.error(request, 'Custom Distribution Name is required when "Other / Custom" is selected.')
            return render(request, 'verification/stipend_form.html', {'action': 'Edit', 'event': event})

        if custom_event_type and len(custom_event_type) > 100:
            from django.contrib import messages
            messages.error(request, 'Custom Distribution Name must be 100 characters or fewer.')
            return render(request, 'verification/stipend_form.html', {'action': 'Edit', 'event': event})

        import datetime
        from decimal import Decimal, InvalidOperation
        try:
            event.date = datetime.date.fromisoformat(date_str)
            event.payout_start_date = datetime.date.fromisoformat(payout_start_str) if payout_start_str else None
            event.payout_end_date = datetime.date.fromisoformat(payout_end_str) if payout_end_str else None
            event.payout_start_time = datetime.time.fromisoformat(payout_start_time_str) if payout_start_time_str else None
            event.payout_end_time = datetime.time.fromisoformat(payout_end_time_str) if payout_end_time_str else None
        except ValueError:
            from django.contrib import messages
            messages.error(request, 'Invalid date or time format.')
            return render(request, 'verification/stipend_form.html', {'action': 'Edit', 'event': event})

        try:
            event.amount = Decimal(amount_str or '0')
            if event.amount < 0:
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            from django.contrib import messages
            messages.error(request, 'Amount must be a non-negative number.')
            return render(request, 'verification/stipend_form.html', {'action': 'Edit', 'event': event})

        _payout_errors = _validate_payout_window(
            event.payout_start_date, event.payout_end_date,
            event.payout_start_time, event.payout_end_time,
            orig_payout_start=_orig_payout_start,
            event_date=event.date, orig_event_date=_orig_event_date,
        )
        if _payout_errors:
            from django.contrib import messages
            for _e in _payout_errors:
                messages.error(request, _e)
            return render(request, 'verification/stipend_form.html', {'action': 'Edit', 'event': event})

        event.title = title
        event.event_type = event_type
        event.custom_event_type = custom_event_type if event_type == StipendEvent.EVENT_TYPE_CUSTOM else ''
        event.description = description
        event.is_active = is_active
        event.save()

        from django.contrib import messages
        messages.success(request, f'Stipend event "{title}" updated.')
        return redirect('verification:stipend_list')

    return render(request, 'verification/stipend_form.html', {'action': 'Edit', 'event': event})


@login_required
@require_POST
def stipend_delete(request, event_id):
    if not request.user.has_financial_authority:
        from django.contrib import messages
        messages.error(request, 'Only the President or Admin can delete a stipend event.')
        return redirect('verification:stipend_list')

    event = get_object_or_404(StipendEvent, pk=event_id)
    # Soft-delete: deactivate rather than hard delete if there are linked claims
    if event.claims.exists():
        event.is_active = False
        event.save()
        from django.contrib import messages
        messages.warning(request, f'"{event.title}" has linked claims — deactivated instead of deleted.')
    else:
        title = event.title
        event.delete()
        from django.contrib import messages
        messages.success(request, f'Stipend event "{title}" deleted.')

    return redirect('verification:stipend_list')


# ─── Face Update (Re-enrollment) ─────────────────────────────────────────────

@login_required
@require_http_methods(['GET'])
def update_face_data(request, pk):
    """
    Show the face update UI for a beneficiary.
    Staff selects a reason then captures a new face image.
    Available for all staff (not admin-only) since barangay encoders handle this.
    """
    beneficiary = get_object_or_404(Beneficiary, pk=pk)

    if not beneficiary.is_eligible_to_claim:
        from django.contrib import messages
        messages.error(
            request,
            f'{beneficiary.full_name} is not active — face update is only allowed for active beneficiaries.'
        )
        return redirect('beneficiaries:beneficiary_detail', pk=pk)

    has_embedding = hasattr(beneficiary, 'face_embedding')
    recent_failures = beneficiary.verification_attempts.filter(
        decision__in=[
            VerificationAttempt.DECISION_NOT_VERIFIED,
            VerificationAttempt.DECISION_DENIED,
        ],
    ).order_by('-timestamp')[:5]

    update_history = FaceUpdateLog.objects.filter(beneficiary=beneficiary).order_by('-timestamp')[:10]
    additional_count = AdditionalFaceEmbedding.objects.filter(beneficiary=beneficiary).count()

    from . import template_analytics as _template_analytics
    reenrollment_suggested, reenrollment_reason = _template_analytics.reenrollment_suggestion_for(beneficiary)

    using_mock = is_using_mock_model()
    return render(request, 'verification/update_face.html', {
        'beneficiary': beneficiary,
        'has_embedding': has_embedding,
        'recent_failures': recent_failures,
        'update_history': update_history,
        'additional_count': additional_count,
        'update_reasons': FaceUpdateLog.REASON_CHOICES,
        'using_mock': using_mock,
        'model_load_error': get_model_load_error() if using_mock else None,
        'reenrollment_suggested': reenrollment_suggested,
        'reenrollment_reason': reenrollment_reason,
    })


@login_required
@require_POST
def update_face_submit(request, pk):
    """
    Process the face update form submission (JSON from webcam capture).

    Action 'replace': replaces the primary FaceEmbedding. Old embedding is gone.
    Action 'augment': adds a new AdditionalFaceEmbedding; primary is kept.
                      Use when appearance changed but original still partially works.

    Both actions are fully logged in FaceUpdateLog.
    """
    beneficiary = get_object_or_404(Beneficiary, pk=pk)

    try:
        data = json.loads(request.body)
        image_data = data.get('image', '')
        reason = data.get('reason', FaceUpdateLog.REASON_STAFF_DECISION)
        action = data.get('action', FaceUpdateLog.ACTION_REPLACE)
        notes = data.get('notes', '').strip()

        if not image_data:
            return JsonResponse({'success': False, 'error': 'No image received.'})

        if reason not in dict(FaceUpdateLog.REASON_CHOICES):
            reason = FaceUpdateLog.REASON_STAFF_DECISION
        if action not in (FaceUpdateLog.ACTION_REPLACE, FaceUpdateLog.ACTION_AUGMENT):
            action = FaceUpdateLog.ACTION_REPLACE

        if ',' in image_data:
            image_data = image_data.split(',')[1]
        try:
            image_bytes = base64.b64decode(image_data)
        except Exception:
            return JsonResponse({'success': False, 'error': 'Invalid image data. Please retake the photo and try again.'})

        # Run full registration pipeline (detection + quality + embedding + encrypt)
        result = process_face_for_registration(image_bytes)

        if not result['success']:
            FaceUpdateLog.objects.create(
                beneficiary=beneficiary,
                performed_by=request.user,
                reason=reason,
                action=action,
                notes=notes or result.get('error', ''),
                success=False,
            )
            return JsonResponse({'success': False, 'error': result['error']})

        encrypted = result['encrypted_embedding']

        # ── Duplicate face check (CRITICAL SECURITY) ──────────────────────────
        # A re-enrollment capture is compared against every OTHER beneficiary's
        # stored templates before the request is created. This closes the same
        # gap as registration: without it, a beneficiary's face could be
        # "updated" to match someone else's face and pass a later verification
        # under the wrong identity. A match never blocks the request — it's
        # surfaced to the admin on the review screen, same as registration.
        dup_threshold = getattr(django_settings, 'FACE_DEDUP_THRESHOLD', 0.80)
        _dup_match_obj = None
        _dup_score = None
        _dup_match_desc = None
        try:
            live_emb = decrypt_embedding(encrypted)
            dup_result = check_duplicate_face(
                live_emb,
                threshold=dup_threshold,
                exclude_beneficiary_id=str(beneficiary.beneficiary_id),
            )
            if dup_result['duplicates_found']:
                top = dup_result['matches'][0]
                _dup_score = top['score']
                try:
                    _dup_match_obj = Beneficiary.objects.get(beneficiary_id=top['beneficiary_id'])
                except Beneficiary.DoesNotExist:
                    pass
                if top.get('source') == 'representative':
                    _dup_match_desc = (
                        f'representative {top["representative_name"]} '
                        f'(registered for beneficiary {top["beneficiary_id"]} — {top["full_name"]})'
                    )
                elif _dup_match_obj:
                    _dup_match_desc = f'{_dup_match_obj.full_name} ({_dup_match_obj.beneficiary_id})'
        except RuntimeError:
            return JsonResponse({
                'success': False,
                'error': 'Face encryption key is not configured. Contact IT.',
            })
        # ──────────────────────────────────────────────────────────────────────

        # ── Security: face updates do NOT apply immediately ──────────────────
        # Create a pending FaceUpdateRequest; an admin must approve before the
        # active embedding is changed.  The encrypted embedding is stored here
        # and only written to FaceEmbedding / AdditionalFaceEmbedding on approval.
        fur = FaceUpdateRequest.objects.create(
            beneficiary=beneficiary,
            requested_by=request.user,
            reason=reason,
            action=action,
            notes=notes,
            new_embedding_data=encrypted,
            duplicate_match_beneficiary=_dup_match_obj,
            duplicate_match_score=_dup_score,
        )

        notify_admins(
            category=Notification.CATEGORY_APPROVAL_REQUIRED,
            title='Face update approval needed',
            message=f'{beneficiary.full_name} — new face template submitted, awaiting approval.',
            url=reverse('verification:face_update_review', args=[fur.id]),
            dedupe_key=f'face_update_request:{fur.id}',
        )

        if _dup_match_obj is not None:
            AuditLog.log(
                action=AuditLog.ACTION_DUPLICATE_FACE,
                user=request.user,
                target_type='FaceUpdateRequest',
                target_id=fur.id,
                details={
                    'beneficiary_id': beneficiary.beneficiary_id,
                    'matched_beneficiary_id': _dup_match_obj.beneficiary_id,
                    'matched_name': _dup_match_obj.full_name,
                    'score': _dup_score,
                    'threshold': dup_threshold,
                    'action': 'pending_face_update_review',
                },
                request=request,
            )
            notify_admins(
                category=Notification.CATEGORY_FRAUD_ALERT,
                title='Duplicate face detected in face update',
                message=(
                    f'{beneficiary.full_name} — new face capture matches '
                    f'{_dup_match_desc or "an existing enrolment"}.'
                ),
                url=reverse('verification:face_update_review', args=[fur.id]),
                dedupe_key=f'face_update_duplicate:{fur.id}',
            )

        AuditLog.log(
            action=AuditLog.ACTION_FACE_UPDATE_REQUEST,
            user=request.user,
            target_type='FaceUpdateRequest',
            target_id=fur.id,
            details={
                'beneficiary_id': beneficiary.beneficiary_id,
                'reason': reason,
                'action': action,
                'notes': notes,
                'quality_ok': result.get('quality', {}).get('ok'),
                'duplicate_detected': _dup_match_obj is not None,
            },
            request=request,
        )

        quality_note = ''
        if result.get('quality') and not result['quality']['ok']:
            quality_note = f' Note: {result["quality"]["reason"]}'

        return JsonResponse({
            'success': True,
            'pending': True,
            'message': (
                f'Face capture submitted for {beneficiary.full_name}.{quality_note} '
                'An admin must approve this request before the face data is updated.'
            ),
            'redirect': f'/dashboard/beneficiaries/{beneficiary.id}/',
        })

    except Exception as e:
        logger.exception('update_face_submit failed: %s', e)
        return JsonResponse({
            'success': False,
            'error': 'Face update failed. Please retry; if the problem persists, contact your Technical Administrator.',
        })


# ─── Admin Approval: Unified Queue ────────────────────────────────────────────

@login_required
def manual_review_list(request):
    if not request.user.is_admin:
        from django.contrib import messages
        messages.error(request, 'Admin access required.')
        return redirect('beneficiaries:dashboard')

    try:
        from logs.notifications import sync_approval_reminders
        sync_approval_reminders()
    except Exception:
        logger.exception('sync_approval_reminders failed (non-fatal)')

    pending_verifications = VerificationAttempt.objects.filter(
        decision=VerificationAttempt.DECISION_MANUAL_REVIEW,
        overridden=False,
    ).select_related('beneficiary', 'performed_by', 'stipend_event').order_by('-timestamp')

    pending_manual_requests = ManualVerificationRequest.objects.filter(
        status=ManualVerificationRequest.STATUS_PENDING,
    ).select_related(
        'beneficiary', 'requested_by', 'verification_attempt', 'stipend_event'
    ).order_by('-created_at')

    pending_face_requests = FaceUpdateRequest.objects.filter(
        status=FaceUpdateRequest.STATUS_PENDING,
    ).select_related('beneficiary', 'requested_by').order_by('-created_at')

    pending_special_claims = SpecialClaimRequest.objects.filter(
        status=SpecialClaimRequest.STATUS_PENDING,
    ).select_related('beneficiary', 'requested_by', 'stipend_event', 'original_claim').order_by('-created_at')

    pending_registrations = (
        Beneficiary.objects
        .filter(status=Beneficiary.STATUS_PENDING)
        .select_related('registered_by')
        .order_by('created_at')
    )

    # Claims queued because there was no active payout event (needs HB approval)
    pending_no_event_claims = ClaimRecord.objects.filter(
        status=ClaimRecord.STATUS_PENDING_APPROVAL,
        stipend_event__isnull=True,
    ).select_related('beneficiary', 'claimed_by', 'verification_attempt').order_by('-claimed_at')

    # Shared-representative review queue (added 2026-05-28).
    from beneficiaries.models import SharedRepresentativeReview
    pending_shared_reps = (
        SharedRepresentativeReview.objects
        .filter(status=SharedRepresentativeReview.STATUS_PENDING)
        .select_related('representative', 'representative__beneficiary', 'flagged_by')
        .order_by('-flagged_at')
    )

    auto_approval_on = SystemConfig.get_bool('auto_approve_beneficiaries', default=False)

    # Sum of every queue section actually rendered on this page — the header
    # badge previously only summed 4 of the 7 sections, understating the
    # true queue size whenever shared-rep, low-score, or no-event-claim
    # items were pending.
    total_pending = (
        pending_verifications.count() + pending_manual_requests.count()
        + pending_face_requests.count() + pending_special_claims.count()
        + pending_registrations.count() + pending_no_event_claims.count()
        + pending_shared_reps.count()
    )

    return render(request, 'verification/manual_review.html', {
        'pending': pending_verifications,
        'pending_manual_requests': pending_manual_requests,
        'pending_face_requests': pending_face_requests,
        'pending_special_claims': pending_special_claims,
        'pending_registrations': pending_registrations,
        'pending_no_event_claims': pending_no_event_claims,
        'pending_shared_reps': pending_shared_reps,
        'auto_approval_on': auto_approval_on,
        'total_pending': total_pending,
    })


@login_required
@require_http_methods(['GET', 'POST'])
def manual_verify_review(request, request_id):
    """Admin approves or rejects a ManualVerificationRequest."""
    from django.contrib import messages

    if not request.user.is_admin:
        messages.error(request, 'Admin access required.')
        return redirect('beneficiaries:dashboard')

    mvr = get_object_or_404(ManualVerificationRequest, pk=request_id)

    if request.method == 'POST':
        # Technical Administrator can view this page (is_admin, above) for
        # diagnostics but cannot approve/reject — that's a Manual Review
        # decision authority reserved for President/Admin.
        if not request.user.has_financial_authority:
            messages.error(request, 'Only the President or Admin can approve or reject a Manual Review request.')
            return redirect('verification:manual_review')

        action = request.POST.get('action', '')
        review_notes = request.POST.get('review_notes', '').strip()

        if action not in ('approve', 'reject'):
            messages.error(request, 'Invalid action.')
            return redirect('verification:manual_review')

        # Capture name before the atomic block for use in the flash message.
        beneficiary_name = mvr.beneficiary.full_name
        _approved = False
        _payout_blocked_reason = ''
        _payout_duplicate_detected = False

        # Single atomic block covers the status check, all related saves, the
        # audit log write, and the ClaimRecord creation.  Re-fetching the MVR
        # with select_for_update() prevents two admins from concurrently
        # approving the same request, which would otherwise produce duplicate
        # audit log entries and a double attempt.save().
        with transaction.atomic():
            mvr = ManualVerificationRequest.objects.select_for_update().get(pk=request_id)
            if mvr.status != ManualVerificationRequest.STATUS_PENDING:
                messages.warning(
                    request,
                    'This request has already been reviewed by another admin.'
                )
                return redirect('verification:manual_review')

            mvr.reviewed_by = request.user
            mvr.reviewed_at = timezone.now()
            mvr.review_notes = review_notes

            if action == 'approve':
                mvr.status = ManualVerificationRequest.STATUS_APPROVED
                if mvr.verification_attempt:
                    attempt = mvr.verification_attempt
                    attempt.decision = VerificationAttempt.DECISION_VERIFIED
                    attempt.decision_reason = (
                        f'Manual verification approved by '
                        f'{request.user.get_full_name() or request.user.username}. '
                        f'{review_notes[:200]}'
                    )
                    attempt.overridden = True
                    attempt.override_by = request.user
                    attempt.override_reason = f'Admin approved manual verification request: {review_notes}'
                    attempt.override_at = timezone.now()
                    attempt.save()
                AuditLog.log(
                    action=AuditLog.ACTION_MANUAL_VERIFY_APPROVED,
                    user=request.user,
                    target_type='ManualVerificationRequest',
                    target_id=mvr.id,
                    details={
                        'beneficiary_id': mvr.beneficiary.beneficiary_id,
                        'review_notes': review_notes,
                        'attempt_id': str(mvr.verification_attempt_id) if mvr.verification_attempt_id else None,
                    },
                    request=request,
                )
                if mvr.stipend_event:
                    _locked_ben = Beneficiary.objects.select_for_update().get(
                        pk=mvr.beneficiary.pk)
                    _locked_event, _event_ok, _ineligible_reason = _lock_event_for_finalization(mvr.stipend_event)
                    if not _event_ok:
                        # Phase B.5 — the identity-review resolution above
                        # (attempt marked VERIFIED, mvr.status APPROVED) is
                        # kept as-is; only the ClaimRecord creation is skipped.
                        _payout_blocked_reason = _ineligible_reason
                        if mvr.verification_attempt:
                            _block_note = _payout_blocked_message(_ineligible_reason)
                            attempt.notes = ((attempt.notes + ' ') if attempt.notes else '') + _block_note
                            attempt.save(update_fields=['notes'])
                        AuditLog.log(
                            action=AuditLog.ACTION_REGISTER,
                            user=request.user,
                            target_type='ManualVerificationRequest',
                            target_id=mvr.id,
                            details={
                                'beneficiary_id': mvr.beneficiary.beneficiary_id,
                                'stipend_event': mvr.stipend_event.title,
                                'via': 'manual_verification_approved',
                                'reason': f'Payout finalization blocked — {_ineligible_reason}',
                            },
                            request=request,
                        )
                    elif not ClaimRecord.objects.filter(
                        beneficiary=_locked_ben,
                        stipend_event=_locked_event,
                        status=ClaimRecord.STATUS_CLAIMED,
                    ).exists():
                        try:
                            new_claim = _create_claim_record(
                                beneficiary=_locked_ben,
                                stipend_event=_locked_event,
                                claimant_type=mvr.claimant_type,
                                representative=None,
                                claimed_by=mvr.requested_by,
                                verification_attempt=mvr.verification_attempt,
                                approved_by=request.user,
                                approved_at=timezone.now(),
                                status=ClaimRecord.STATUS_CLAIMED,
                                verification_method=ClaimRecord.VERIFY_MANUAL,
                                notes=f'Admin manual verification approved by {request.user.get_full_name() or request.user.username}.',
                            )
                        except IntegrityError:
                            # exists() passed, but a concurrent transaction
                            # committed a claim for this beneficiary/event
                            # first. The MVR approval/attempt decision above
                            # is kept as-is (mirrors the _event_ok=False
                            # branch) — only the ClaimRecord creation is
                            # skipped, and it is recorded truthfully.
                            new_claim = None
                            _payout_duplicate_detected = True
                            AuditLog.log(
                                action=AuditLog.ACTION_DUPLICATE_PAYOUT_ATTEMPT,
                                user=request.user,
                                target_type='Beneficiary',
                                target_id=mvr.beneficiary.id,
                                details={
                                    'beneficiary_id': mvr.beneficiary.beneficiary_id,
                                    'stipend_event': mvr.stipend_event.title,
                                    'reason': 'Already claimed for this event (concurrent attempt, detected at commit)',
                                    'via': 'manual_verification_approved',
                                },
                                request=request,
                            )
                        if new_claim is not None:
                            AuditLog.log(
                                action=AuditLog.ACTION_CLAIM,
                                user=request.user,
                                target_type='ClaimRecord',
                                target_id=new_claim.id,
                                details={
                                    'beneficiary_id': mvr.beneficiary.beneficiary_id,
                                    'stipend_event': mvr.stipend_event.title,
                                    'claimant_type': mvr.claimant_type,
                                    'via': 'manual_verification_approved',
                                    'amount': str(new_claim.amount),
                                    'reference_number': new_claim.reference_number,
                                    'verification_method': new_claim.verification_method,
                                },
                                request=request,
                            )
                _approved = True
            else:
                mvr.status = ManualVerificationRequest.STATUS_REJECTED
                if mvr.verification_attempt:
                    attempt = mvr.verification_attempt
                    attempt.decision = VerificationAttempt.DECISION_DENIED
                    attempt.decision_reason = (
                        f'Manual verification rejected by '
                        f'{request.user.get_full_name() or request.user.username}. '
                        f'{review_notes[:200]}'
                    )
                    attempt.save()
                AuditLog.log(
                    action=AuditLog.ACTION_MANUAL_VERIFY_REJECTED,
                    user=request.user,
                    target_type='ManualVerificationRequest',
                    target_id=mvr.id,
                    details={
                        'beneficiary_id': mvr.beneficiary.beneficiary_id,
                        'review_notes': review_notes,
                    },
                    request=request,
                )

            mvr.save()

            from logs.notifications import resolve_notification
            resolve_notification(f'manual_verify_request:{mvr.id}')
            resolve_notification(f'approval_reminder_manual_verify:{mvr.id}')

        if _approved and _payout_duplicate_detected:
            messages.warning(
                request,
                f'Manual verification approved for {beneficiary_name}, but the payout was not '
                f'released — a claim for this beneficiary and event was already recorded by a '
                f'concurrent action. No duplicate claim was created.'
            )
        elif _approved and _payout_blocked_reason:
            messages.warning(
                request,
                f'Manual verification approved for {beneficiary_name}, but the payout was not '
                f'released — {StipendEvent.describe_claim_ineligible_reason(_payout_blocked_reason)}.'
            )
        elif _approved:
            messages.success(request, f'Manual verification approved for {beneficiary_name}.')
        else:
            messages.warning(request, f'Manual verification request rejected for {beneficiary_name}.')
        return redirect('verification:manual_review')

    return render(request, 'verification/manual_verify_review.html', {'mvr': mvr})


@login_required
@require_http_methods(['GET', 'POST'])
def face_update_review(request, request_id):
    """Admin approves or rejects a FaceUpdateRequest."""
    from django.contrib import messages

    if not request.user.is_admin:
        messages.error(request, 'Admin access required.')
        return redirect('beneficiaries:dashboard')

    fur = get_object_or_404(FaceUpdateRequest, pk=request_id)

    if request.method == 'POST':
        action = request.POST.get('action', '')
        review_notes = request.POST.get('review_notes', '').strip()
        duplicate_confirmed = request.POST.get('duplicate_confirmed') == 'on'

        if action not in ('approve', 'reject'):
            messages.error(request, 'Invalid action.')
            return redirect('verification:manual_review')

        # Pre-UAT follow-up (Issue 33 class): approving a face-update request
        # that matched an existing beneficiary must not be a plain click —
        # the template already warns the admin, but nothing server-side
        # stopped approval from silently writing a duplicate face into
        # FaceEmbedding/AdditionalFaceEmbedding. Registration-time duplicates
        # already require going through a dedicated resolution page
        # (duplicate_review_detail); this mirrors that same "must be an
        # explicit, informed decision" requirement for the update path,
        # without needing a whole separate resolution model.
        if action == 'approve' and fur.duplicate_match_beneficiary_id and not duplicate_confirmed:
            messages.error(
                request,
                f'{fur.beneficiary.full_name}\'s new face capture matches an existing '
                'beneficiary. You must explicitly confirm the duplicate-face warning '
                'below before this update can be approved.'
            )
            return render(request, 'verification/face_update_review.html', {'fur': fur})

        beneficiary_name = fur.beneficiary.full_name
        _approved = False

        # Atomic block with a row lock prevents two admins from concurrently
        # approving the same FaceUpdateRequest, which would otherwise apply
        # the embedding twice and create two FaceUpdateLog records.
        with transaction.atomic():
            fur = FaceUpdateRequest.objects.select_for_update().get(pk=request_id)
            if fur.status != FaceUpdateRequest.STATUS_PENDING:
                messages.warning(
                    request,
                    'This face update request has already been reviewed by another admin.'
                )
                return redirect('verification:manual_review')

            fur.reviewed_by = request.user
            fur.reviewed_at = timezone.now()
            fur.review_notes = review_notes

            if action == 'approve':
                fur.status = FaceUpdateRequest.STATUS_APPROVED

                beneficiary = fur.beneficiary
                encrypted = bytes(fur.new_embedding_data)

                if fur.action == FaceUpdateLog.ACTION_REPLACE:
                    if hasattr(beneficiary, 'face_embedding'):
                        emb = beneficiary.face_embedding
                        emb.embedding_data = encrypted
                        emb.created_by = fur.requested_by
                        emb.save()
                    else:
                        FaceEmbedding.objects.create(
                            beneficiary=beneficiary,
                            embedding_data=encrypted,
                            created_by=fur.requested_by,
                        )
                    action_label = 'Primary embedding replaced'
                else:
                    AdditionalFaceEmbedding.objects.create(
                        beneficiary=beneficiary,
                        embedding_data=encrypted,
                        label=f'update-{fur.created_at.strftime("%Y-%m-%d")}',
                        created_by=fur.requested_by,
                    )
                    action_label = 'Additional template added'

                FaceUpdateLog.objects.create(
                    beneficiary=beneficiary,
                    performed_by=fur.requested_by,
                    reason=fur.reason,
                    action=fur.action,
                    notes=f'Approved by {request.user.get_full_name() or request.user.username}. {review_notes}',
                    success=True,
                )
                AuditLog.log(
                    action=AuditLog.ACTION_FACE_UPDATE_APPROVED,
                    user=request.user,
                    target_type='FaceUpdateRequest',
                    target_id=fur.id,
                    details={
                        'beneficiary_id': beneficiary.beneficiary_id,
                        'action': fur.action,
                        'action_label': action_label,
                        'review_notes': review_notes,
                    },
                    request=request,
                )
                _approved = True
            else:
                fur.status = FaceUpdateRequest.STATUS_REJECTED
                FaceUpdateLog.objects.create(
                    beneficiary=fur.beneficiary,
                    performed_by=fur.requested_by,
                    reason=fur.reason,
                    action=fur.action,
                    notes=f'Rejected by {request.user.get_full_name() or request.user.username}. {review_notes}',
                    success=False,
                )
                AuditLog.log(
                    action=AuditLog.ACTION_FACE_UPDATE_REJECTED,
                    user=request.user,
                    target_type='FaceUpdateRequest',
                    target_id=fur.id,
                    details={
                        'beneficiary_id': fur.beneficiary.beneficiary_id,
                        'review_notes': review_notes,
                    },
                    request=request,
                )

            fur.save()

            from logs.notifications import resolve_notification
            resolve_notification(f'face_update_request:{fur.id}')
            resolve_notification(f'face_update_duplicate:{fur.id}')
            resolve_notification(f'approval_reminder_face_update:{fur.id}')

        if _approved:
            messages.success(request, f'Face update approved and applied for {beneficiary_name}.')
        else:
            messages.warning(request, f'Face update request rejected for {beneficiary_name}.')
        return redirect('verification:manual_review')

    return render(request, 'verification/face_update_review.html', {'fur': fur})


# ─── Special Claim Request ────────────────────────────────────────────────────

@login_required
@require_POST
def special_claim_request(request, pk):
    """
    Staff submits a SpecialClaimRequest to allow a second claim for a beneficiary
    that has already claimed the current stipend event.

    Event resolution (Phase B.3): when more than one stipend event is
    date-eligible today, this request is specifically about "the event the
    beneficiary already claimed" (per this view's own purpose — a *second*
    claim), so the correct event is whichever of today's candidates the
    beneficiary holds an existing CLAIMED ClaimRecord for, never an arbitrary
    date-matching pick. If the beneficiary has claimed more than one distinct
    event today (legitimate — ClaimRecord uniqueness is per beneficiary+event,
    not beneficiary alone), this action is refused rather than guessing which
    one the request is about.
    """
    from django.contrib import messages

    beneficiary = get_object_or_404(Beneficiary, pk=pk)
    today = timezone.localdate()
    todays_events = StipendEvent.get_active_events_for_date(today)
    claimed_today = list(ClaimRecord.objects.filter(
        beneficiary=beneficiary,
        stipend_event__in=todays_events,
        status=ClaimRecord.STATUS_CLAIMED,
    ).values_list('stipend_event_id', flat=True).distinct())

    reason = request.POST.get('reason', '').strip()

    if not reason:
        messages.error(request, 'A reason is required for the special claim request.')
        return redirect('beneficiaries:beneficiary_detail', pk=pk)

    if len(claimed_today) > 1:
        messages.error(
            request,
            f'{beneficiary.full_name} has already claimed more than one distinct stipend event today. '
            'This request cannot determine which one an additional claim is for — please have an '
            'administrator review this case directly via the Payout Claims Report.'
        )
        return redirect('beneficiaries:beneficiary_detail', pk=pk)
    elif len(claimed_today) == 1:
        active_event = next(ev for ev in todays_events if ev.pk == claimed_today[0])
    else:
        # No existing claim today yet — preserve prior behavior (first
        # date-matching candidate) as a reasonable fallback for this edge case.
        active_event = todays_events[0] if todays_events else None

    if not active_event:
        messages.error(request, 'No active stipend event found. Special claim requests require an active event.')
        return redirect('beneficiaries:beneficiary_detail', pk=pk)

    # Find the original claim to reference
    original_claim = ClaimRecord.objects.filter(
        beneficiary=beneficiary,
        stipend_event=active_event,
        status=ClaimRecord.STATUS_CLAIMED,
    ).first()

    # Check for an already-pending request to avoid duplicates
    if SpecialClaimRequest.objects.filter(
        beneficiary=beneficiary,
        stipend_event=active_event,
        status=SpecialClaimRequest.STATUS_PENDING,
    ).exists():
        messages.warning(
            request,
            'A special claim request for this beneficiary and event is already pending admin review.'
        )
        return redirect('beneficiaries:beneficiary_detail', pk=pk)

    scr = SpecialClaimRequest.objects.create(
        beneficiary=beneficiary,
        stipend_event=active_event,
        original_claim=original_claim,
        requested_by=request.user,
        reason=reason,
        notes=request.POST.get('notes', '').strip(),
    )

    notify_admins(
        category=Notification.CATEGORY_APPROVAL_REQUIRED,
        title='Special claim request needs review',
        message=f'{beneficiary.full_name} — second claim requested for {active_event.title}.',
        url=reverse('verification:special_claim_review', args=[scr.id]),
        dedupe_key=f'special_claim_request:{scr.id}',
    )

    AuditLog.log(
        action=AuditLog.ACTION_SPECIAL_CLAIM_REQUEST,
        user=request.user,
        target_type='SpecialClaimRequest',
        target_id=scr.id,
        details={
            'beneficiary_id': beneficiary.beneficiary_id,
            'stipend_event': active_event.title,
            'reason': reason,
        },
        request=request,
    )

    messages.info(
        request,
        f'Special claim request submitted for {beneficiary.full_name}. '
        'An admin must approve before a second claim can be recorded.'
    )
    return redirect('beneficiaries:beneficiary_detail', pk=pk)


@login_required
@require_http_methods(['GET', 'POST'])
def special_claim_review(request, request_id):
    """Admin approves or rejects a SpecialClaimRequest."""
    from django.contrib import messages

    if not request.user.is_admin:
        messages.error(request, 'Admin access required.')
        return redirect('beneficiaries:dashboard')

    scr = get_object_or_404(SpecialClaimRequest, pk=request_id)

    if request.method == 'POST':
        # Special-claim approval is a financial decision — Technical
        # Administrator can view (is_admin, above) but not approve/reject.
        if not request.user.has_financial_authority:
            messages.error(request, 'Only the President or Admin can approve or reject a special claim request.')
            return redirect('verification:manual_review')

        action = request.POST.get('action', '')
        review_notes = request.POST.get('review_notes', '').strip()

        if action not in ('approve', 'reject'):
            messages.error(request, 'Invalid action.')
            return redirect('verification:manual_review')

        beneficiary_name = scr.beneficiary.full_name
        _approved = False
        _payout_blocked_reason = ''

        # Atomic block with a row lock prevents two admins from concurrently
        # approving the same SpecialClaimRequest, which would otherwise create
        # two is_special_additional ClaimRecords for the same event.
        with transaction.atomic():
            scr = SpecialClaimRequest.objects.select_for_update().get(pk=request_id)
            if scr.status != SpecialClaimRequest.STATUS_PENDING:
                messages.warning(
                    request,
                    'This special claim request has already been reviewed by another admin.'
                )
                return redirect('verification:manual_review')

            scr.reviewed_by = request.user
            scr.reviewed_at = timezone.now()
            scr.review_notes = review_notes

            if action == 'approve':
                scr.status = SpecialClaimRequest.STATUS_APPROVED

                if scr.stipend_event:
                    # Phase B.5 — same finalization policy as every other
                    # claim-creation path: the request may still be approved,
                    # but the second ClaimRecord is only created if the event
                    # is still claimable right now.
                    locked_event, _event_ok, _ineligible_reason = _lock_event_for_finalization(scr.stipend_event)
                    if not _event_ok:
                        _payout_blocked_reason = _ineligible_reason
                        scr.review_notes = (
                            (scr.review_notes + ' ') if scr.review_notes else ''
                        ) + _payout_blocked_message(_ineligible_reason)
                    else:
                        _create_claim_record(
                            beneficiary=scr.beneficiary,
                            stipend_event=locked_event,
                            claimant_type=VerificationAttempt.CLAIMANT_BENEFICIARY,
                            representative=None,
                            claimed_by=scr.requested_by,
                            verification_attempt=None,
                            approved_by=request.user,
                            approved_at=timezone.now(),
                            status=ClaimRecord.STATUS_CLAIMED,
                            is_special_additional=True,
                            verification_method=ClaimRecord.VERIFY_MANUAL,
                            notes=f'Special additional claim approved by {request.user.get_full_name() or request.user.username}. {review_notes}',
                        )

                AuditLog.log(
                    action=(
                        AuditLog.ACTION_SPECIAL_CLAIM_APPROVED if not _payout_blocked_reason
                        else AuditLog.ACTION_REGISTER
                    ),
                    user=request.user,
                    target_type='SpecialClaimRequest',
                    target_id=scr.id,
                    details={
                        'beneficiary_id': scr.beneficiary.beneficiary_id,
                        'stipend_event': scr.stipend_event.title if scr.stipend_event else None,
                        'review_notes': review_notes,
                        **({'reason': f'Payout finalization blocked — {_payout_blocked_reason}'} if _payout_blocked_reason else {}),
                    },
                    request=request,
                )
                _approved = True
            else:
                scr.status = SpecialClaimRequest.STATUS_REJECTED
                AuditLog.log(
                    action=AuditLog.ACTION_SPECIAL_CLAIM_REJECTED,
                    user=request.user,
                    target_type='SpecialClaimRequest',
                    target_id=scr.id,
                    details={
                        'beneficiary_id': scr.beneficiary.beneficiary_id,
                        'stipend_event': scr.stipend_event.title if scr.stipend_event else None,
                        'review_notes': review_notes,
                    },
                    request=request,
                )

            scr.save()

            from logs.notifications import resolve_notification
            resolve_notification(f'special_claim_request:{scr.id}')
            resolve_notification(f'approval_reminder_special_claim:{scr.id}')

        if _approved and _payout_blocked_reason:
            messages.warning(
                request,
                f'Special claim approved for {beneficiary_name}, but the payout was not '
                f'released — {StipendEvent.describe_claim_ineligible_reason(_payout_blocked_reason)}.'
            )
        elif _approved:
            messages.success(
                request,
                f'Special claim approved for {beneficiary_name}. A second claim record has been created.'
            )
        else:
            messages.warning(request, f'Special claim request rejected for {beneficiary_name}.')
        return redirect('verification:manual_review')

    return render(request, 'verification/special_claim_review.html', {'scr': scr})


# ─── Registration Approval ────────────────────────────────────────────────────

@login_required
def registration_review_list(request):
    """Admin queue showing all pending beneficiary registrations."""
    from django.contrib import messages

    if not request.user.is_admin:
        messages.error(request, 'Admin access required.')
        return redirect('beneficiaries:dashboard')

    # v2.2.0 Follow-up Issue 31/33: a beneficiary with an unresolved duplicate-
    # face conflict must go through the dedicated Duplicate Face Review queue
    # (beneficiaries:duplicate_review_list/detail), never the generic
    # registration approval below — that flow has no concept of "confirm
    # legitimate twin vs. reject as fraud" and would let an admin approve
    # straight past an unresolved identity conflict. Excluded here (mirrors
    # the same exclusion already applied to the auto-approval pending queue)
    # and hard-blocked again server-side in registration_review() below as
    # defense-in-depth against a stale/bookmarked direct link.
    pending = (
        Beneficiary.objects
        .filter(status=Beneficiary.STATUS_PENDING, duplicate_review_required=False)
        .select_related('registered_by')
        .order_by('created_at')
    )
    duplicate_pending_count = Beneficiary.objects.filter(
        status=Beneficiary.STATUS_PENDING, duplicate_review_required=True,
    ).count()
    return render(request, 'verification/registration_review_list.html', {
        'pending': pending,
        'duplicate_pending_count': duplicate_pending_count,
    })


@login_required
@require_http_methods(['GET', 'POST'])
def registration_review(request, pk):
    """Admin approves or rejects a single pending beneficiary registration."""
    from django.contrib import messages

    if not request.user.is_admin:
        messages.error(request, 'Admin access required.')
        return redirect('beneficiaries:dashboard')

    # Non-locking fetch for the GET page; POST re-fetches with a lock below.
    beneficiary = get_object_or_404(Beneficiary, pk=pk)

    # Hard gate (Follow-up Issue 31/33): this record has an unresolved
    # duplicate-face conflict — it must be resolved via the dedicated
    # Duplicate Face Review case, never approved/rejected as an ordinary
    # registration. Applies to GET (redirect away) and POST (refuse the
    # action) alike, so a stale/bookmarked link can't bypass it either.
    if beneficiary.duplicate_review_required:
        messages.error(
            request,
            f'{beneficiary.full_name} has an unresolved duplicate-face conflict. '
            'Resolve it via Duplicate Face Review before this registration can be approved or rejected.'
        )
        return redirect('beneficiaries:duplicate_review_detail', pk=beneficiary.pk)

    if beneficiary.status != Beneficiary.STATUS_PENDING and request.method == 'GET':
        messages.warning(request, 'This registration has already been reviewed.')
        return redirect('verification:registration_review_list')

    if request.method == 'POST':
        action = request.POST.get('action', '')
        review_notes = request.POST.get('review_notes', '').strip()

        if action not in ('approve', 'reject'):
            messages.error(request, 'Invalid action.')
            return redirect('verification:registration_review_list')

        if not review_notes:
            messages.error(request, 'Review notes are required.')
            return render(request, 'verification/registration_review.html', {
                'beneficiary': beneficiary,
            })

        # Atomic block with a row lock prevents two admins from concurrently
        # approving the same pending registration, which would otherwise
        # produce duplicate audit log entries and double status saves.
        with transaction.atomic():
            beneficiary = Beneficiary.objects.select_for_update().get(pk=pk)
            # Re-check under lock — a duplicate flag set concurrently (by a
            # registration racing this approval) must still block release.
            if beneficiary.duplicate_review_required:
                messages.error(
                    request,
                    f'{beneficiary.full_name} has an unresolved duplicate-face conflict. '
                    'Resolve it via Duplicate Face Review before this registration can be approved or rejected.'
                )
                return redirect('beneficiaries:duplicate_review_detail', pk=beneficiary.pk)
            if beneficiary.status != Beneficiary.STATUS_PENDING:
                messages.warning(
                    request,
                    'This registration has already been reviewed by another admin.'
                )
                return redirect('verification:registration_review_list')

            if action == 'approve':
                beneficiary.status = Beneficiary.STATUS_ACTIVE
                beneficiary.save()
                AuditLog.log(
                    action=AuditLog.ACTION_REGISTER_APPROVED,
                    user=request.user,
                    target_type='Beneficiary',
                    target_id=beneficiary.id,
                    details={
                        'beneficiary_id': beneficiary.beneficiary_id,
                        'name': beneficiary.full_name,
                        'review_notes': review_notes,
                    },
                    request=request,
                )
                messages.success(
                    request,
                    f'Registration approved for {beneficiary.full_name} '
                    f'(ID: {beneficiary.beneficiary_id}). They are now active.'
                )
            else:
                # v2.2.0 Post-UAT Phase 4: this registration was never
                # approved, so it is disapproved — not "inactive" (which
                # would misleadingly imply it was once an active beneficiary).
                beneficiary.status = Beneficiary.STATUS_DISAPPROVED
                beneficiary.deactivated_reason = (
                    f'Registration rejected by '
                    f'{request.user.get_full_name() or request.user.username}: {review_notes}'
                )
                beneficiary.save()
                AuditLog.log(
                    action=AuditLog.ACTION_REGISTER_REJECTED,
                    user=request.user,
                    target_type='Beneficiary',
                    target_id=beneficiary.id,
                    details={
                        'beneficiary_id': beneficiary.beneficiary_id,
                        'name': beneficiary.full_name,
                        'review_notes': review_notes,
                    },
                    request=request,
                )
                messages.warning(
                    request,
                    f'Registration rejected for {beneficiary.full_name}.'
                )

        return redirect('verification:registration_review_list')

    return render(request, 'verification/registration_review.html', {
        'beneficiary': beneficiary,
    })


# ─── Representative Face Registration ────────────────────────────────────────

@login_required
@require_http_methods(['GET'])
def register_rep_face(request, pk, rep_pk):
    """Show the face capture UI for a representative."""
    beneficiary = get_object_or_404(Beneficiary, pk=pk)
    rep = get_object_or_404(Representative, pk=rep_pk, beneficiary=beneficiary)
    challenge = get_random_challenge()
    request.session['rep_reg_liveness_challenge'] = challenge
    return render(request, 'verification/register_rep_face.html', {
        'beneficiary': beneficiary,
        'representative': rep,
        'using_mock': is_using_mock_model(),
        'model_load_error': get_model_load_error() if is_using_mock_model() else None,
        'challenge': challenge,
        'challenge_display': _challenge_display(challenge),
    })


@login_required
@require_POST
def register_rep_face_submit(request, pk, rep_pk):
    """Ajax endpoint to save a representative's face embedding."""
    beneficiary = get_object_or_404(Beneficiary, pk=pk)
    rep = get_object_or_404(Representative, pk=rep_pk, beneficiary=beneficiary)
    try:
        data = json.loads(request.body)
        image_data = data.get('image', '')
        if not image_data:
            return JsonResponse({'success': False, 'error': 'No image provided.'})

        if ',' in image_data:
            image_data = image_data.split(',')[1]
        try:
            image_bytes = base64.b64decode(image_data)
        except Exception:
            return JsonResponse({'success': False, 'error': 'Invalid image data. Please retake the photo and try again.'})

        result = process_face_for_registration(image_bytes)
        if not result['success']:
            return JsonResponse({'success': False, 'error': result['error']})

        # Shared-representative review (added 2026-05-28).
        # A representative whose face is already enrolled for a different
        # beneficiary is NOT auto-rejected — one person may legitimately
        # represent multiple seniors (child for two parents, caregiver, etc.).
        # We save the face data, mark the representative as
        # "pending representative review", create a SharedRepresentativeReview
        # case for an admin to approve/reject, and tell staff the claim is
        # blocked pending admin action.
        from beneficiaries.models import SharedRepresentativeReview
        dup_threshold = getattr(django_settings, 'FACE_DEDUP_THRESHOLD', 0.80)
        shared_review_created = None
        try:
            live_emb = decrypt_embedding(result['encrypted_embedding'])

            # Same-beneficiary hard gate (added 2026-09-07). A beneficiary
            # cannot serve as their own authorized representative — check the
            # new capture against the REPRESENTED beneficiary's own stored
            # face(s) first, using the same canonical matcher verify_submit
            # uses (compare_with_all_embeddings) and the same "confident
            # duplicate" bar (dup_threshold) check_duplicate_face already
            # applies below. Unlike a cross-beneficiary match — which can be
            # a legitimate shared representative — there is no legitimate
            # case for this one, so it is rejected outright rather than
            # routed into SharedRepresentativeReview: nothing is saved, so
            # `has_face_data` stays False and the existing "No Face Data"
            # badge (not "Ready to Verify") is what renders.
            if hasattr(beneficiary, 'face_embedding'):
                self_match = compare_with_all_embeddings(live_emb, beneficiary)
                if self_match['success'] and self_match['score'] >= dup_threshold:
                    AuditLog.log(
                        action=AuditLog.ACTION_SHARED_REP_FLAGGED,
                        user=request.user,
                        target_type='Representative',
                        target_id=rep.id,
                        details={
                            'beneficiary_id': beneficiary.beneficiary_id,
                            'reason': (
                                'Representative face enrollment blocked — matches '
                                'the represented beneficiary\'s own registered face.'
                            ),
                            'score': round(self_match['score'], 4),
                            'threshold': dup_threshold,
                        },
                        request=request,
                    )
                    return JsonResponse({
                        'success': False,
                        'error': (
                            "Representative face matches the beneficiary's registered "
                            "face. An authorized representative must be a different "
                            "person."
                        ),
                    })

            dup = check_duplicate_face(
                live_emb,
                threshold=dup_threshold,
                exclude_beneficiary_id=str(beneficiary.beneficiary_id),
                exclude_representative_id=str(rep.pk),
            )
            if dup['duplicates_found']:
                top = dup['matches'][0]
                # Flag the rep so verification cannot proceed until admin
                # reviews. Embedding is still saved (admin needs it for review).
                rep.shared_review_status = Representative.SHARED_PENDING
                rep.save(update_fields=['shared_review_status'])
                if top.get('source') == 'representative':
                    _match_desc = (
                        f'representative {top["representative_name"]} '
                        f'(for beneficiary {top["beneficiary_id"]} — {top["full_name"]})'
                    )
                else:
                    _match_desc = f'beneficiary {top["beneficiary_id"]} ({top["full_name"]})'
                shared_review_created = SharedRepresentativeReview.objects.create(
                    representative=rep,
                    matched_beneficiary_id=top['beneficiary_id'],
                    matched_beneficiary_name=top['full_name'],
                    matched_score=float(top['score']),
                    matched_threshold=float(dup_threshold),
                    status=SharedRepresentativeReview.STATUS_PENDING,
                    flag_reason=(
                        f'Face matches an existing enrolment for {_match_desc} at '
                        f'similarity {top["score"]:.3f} (threshold {dup_threshold:.2f}). '
                        'Same person may legitimately represent multiple seniors '
                        '— admin must approve before verification is allowed.'
                    ),
                    flagged_by=request.user,
                )
                AuditLog.log(
                    action=AuditLog.ACTION_SHARED_REP_FLAGGED,
                    user=request.user,
                    target_type='Representative',
                    target_id=rep.id,
                    details={
                        'beneficiary_id': beneficiary.beneficiary_id,
                        'matched_beneficiary_id': top['beneficiary_id'],
                        'matched_name': top['full_name'],
                        'score': top['score'],
                        'threshold': dup_threshold,
                        'review_id': str(shared_review_created.pk),
                    },
                    request=request,
                )
                notify_admins(
                    category=Notification.CATEGORY_FRAUD_ALERT,
                    title='Shared representative flagged',
                    message=f'{rep.full_name} — face already enrolled for another beneficiary.',
                    url=reverse('verification:shared_rep_review_detail', args=[shared_review_created.pk]),
                    dedupe_key=f'shared_rep_flagged:{shared_review_created.pk}',
                )
        except RuntimeError:
            # decrypt_embedding now raises on missing key — surface as a generic
            # failure here; the underlying setting error is logged on first call.
            return JsonResponse({
                'success': False,
                'error': 'Face encryption key is not configured. Contact IT.',
            })

        RepresentativeFaceEmbedding.objects.update_or_create(
            representative=rep,
            defaults={
                'embedding_data': result['encrypted_embedding'],
                'created_by': request.user,
            },
        )

        AuditLog.log(
            action=AuditLog.ACTION_REGISTER,
            user=request.user,
            target_type='Representative',
            target_id=rep.id,
            details={
                'representative_name': rep.full_name,
                'beneficiary_id': beneficiary.beneficiary_id,
                'action': 'face_registered',
                'shared_review_pending': bool(shared_review_created),
            },
            request=request,
        )

        if shared_review_created:
            return JsonResponse({
                'success': True,
                'shared_review': True,
                'message': (
                    f'{rep.full_name} is linked to another beneficiary and requires '
                    'administrator review before release. Please wait for admin '
                    'approval — verification is blocked until the review is completed.'
                ),
                'redirect': f'/dashboard/beneficiaries/{beneficiary.pk}/',
            })

        return JsonResponse({
            'success': True,
            'message': f'Face registered for {rep.full_name}.',
            'redirect': f'/dashboard/beneficiaries/{beneficiary.pk}/',
        })
    except Exception as e:
        logger.exception('register_rep_face_submit failed: %s', e)
        return JsonResponse({
            'success': False,
            'error': 'Face registration failed. Please retry; if the problem persists, contact your Technical Administrator.',
        })


# ─── Pending Claim Review (no active payout event) ────────────────────────────

@login_required
@require_http_methods(['GET', 'POST'])
def pending_claim_review(request, claim_id):
    """
    President approves or rejects a claim that was recorded when no
    active payout event existed. This is a financial decision, gated with
    `has_financial_authority` (President/Admin) — NOT `is_admin`, which
    would also admit the Technical Administrator. This view previously kept
    a "President (and IT)" business-continuity exception; the owner closed
    it (FINAL PRE-EXE COMPLETION checkpoint, section 5) because Technical
    Administrator has broad diagnostic read access but no normal financial
    mutation authority — see `CustomUser.has_financial_authority`.
    """
    from django.contrib import messages

    if not request.user.has_financial_authority:
        messages.error(request, 'This action requires financial authority (President or Admin).')
        return redirect('beneficiaries:dashboard')

    claim = get_object_or_404(
        ClaimRecord.objects.select_related('beneficiary', 'claimed_by', 'verification_attempt'),
        pk=claim_id,
        status=ClaimRecord.STATUS_PENDING_APPROVAL,
    )

    if request.method == 'POST':
        action = request.POST.get('action', '')
        review_notes = request.POST.get('review_notes', '').strip()

        if action not in ('approve', 'reject'):
            messages.error(request, 'Invalid action.')
            return redirect('verification:manual_review')

        beneficiary_name = claim.beneficiary.full_name

        with transaction.atomic():
            claim = ClaimRecord.objects.select_for_update().get(pk=claim_id)
            if claim.status != ClaimRecord.STATUS_PENDING_APPROVAL:
                messages.warning(request, 'This claim has already been reviewed.')
                return redirect('verification:manual_review')

            claim.reviewed_by = request.user
            claim.approved_by = request.user
            claim.approved_at = timezone.now()
            claim.notes = (claim.notes or '') + f' | Review: {review_notes}'

            if action == 'approve':
                claim.status = ClaimRecord.STATUS_CLAIMED
                # Promote to a released payout: snapshot amount (0 here since
                # no event), assign reference number, mark released_at/by now.
                if not claim.reference_number:
                    claim.reference_number = ClaimRecord.generate_reference_number(
                        stipend_event=claim.stipend_event, when=timezone.now())
                if not claim.released_by:
                    claim.released_by = request.user
                if not claim.released_at:
                    claim.released_at = timezone.now()
                claim.save()
                AuditLog.log(
                    action=AuditLog.ACTION_CLAIM_PENDING_APPROVED,
                    user=request.user,
                    target_type='ClaimRecord',
                    target_id=claim.id,
                    details={
                        'beneficiary_id': claim.beneficiary.beneficiary_id,
                        'claimed_by': claim.claimed_by.username if claim.claimed_by else None,
                        'released_by': request.user.username,
                        'reference_number': claim.reference_number,
                        'review_notes': review_notes,
                    },
                    request=request,
                )
                messages.success(request, f'Pending claim approved for {beneficiary_name}.')
            else:
                claim.status = ClaimRecord.STATUS_REJECTED
                claim.save()
                AuditLog.log(
                    action=AuditLog.ACTION_CLAIM_PENDING_REJECTED,
                    user=request.user,
                    target_type='ClaimRecord',
                    target_id=claim.id,
                    details={
                        'beneficiary_id': claim.beneficiary.beneficiary_id,
                        'claimed_by': claim.claimed_by.username if claim.claimed_by else None,
                        'review_notes': review_notes,
                    },
                    request=request,
                )
                messages.warning(request, f'Pending claim rejected for {beneficiary_name}.')

            from logs.notifications import resolve_notification
            resolve_notification(f'claim_pending:{claim.pk}')
            resolve_notification(f'approval_reminder_claim_pending:{claim.pk}')

        return redirect('verification:manual_review')

    return render(request, 'verification/pending_claim_review.html', {'claim': claim})


# ─── Reports / Export ────────────────────────────────────────────────────────

def _parse_date(val):
    import datetime
    if not val:
        return None
    try:
        return datetime.date.fromisoformat(val)
    except ValueError:
        return None


def _date_range_invalid(date_from, date_to):
    """True when both bounds are given and From is after To. _apply_date_range
    already fails safe in this case (the gte/lte filters combine to match
    nothing), but a silent all-zero page reads as "no activity" rather than
    "you asked for an impossible range" — surface it instead."""
    return bool(date_from and date_to and date_from > date_to)


def _analytics_date_presets():
    """Server-computed (not client-clock-dependent) date ranges for the
    shared Analytics toolbar presets. Each preset is just a concrete
    (date_from, date_to) pair that round-trips through the exact same
    ?date_from=&date_to= query params the existing Custom form already
    used — the presets are a convenience for producing those two values,
    not a separate date-filtering code path."""
    import datetime
    today = timezone.localdate()
    return today, {
        'today': (today, today),
        'last7': (today - datetime.timedelta(days=6), today),
        'last30': (today - datetime.timedelta(days=29), today),
        'this_month': (today.replace(day=1), today),
    }


def _active_analytics_preset(date_from, date_to, presets):
    """Which preset (if any) the current date_from/date_to already match, so
    the toolbar can highlight it. 'custom' covers everything else, including
    the unbounded (both blank) default."""
    for key, (preset_from, preset_to) in presets.items():
        if date_from == preset_from and date_to == preset_to:
            return key
    return 'custom'


def _analytics_toolbar_context(date_from, date_to):
    """Shared context for the Executive/Operational/Security toolbar
    (_analytics_nav.html): today's date plus each preset's (from, to) as
    ISO strings for building plain query-string links, and which preset (if
    any) the current selection matches."""
    today, presets = _analytics_date_presets()
    active_preset = _active_analytics_preset(date_from, date_to, presets)
    return {
        'today': today,
        'date_presets': {
            key: {'from': preset_from.isoformat(), 'to': preset_to.isoformat()}
            for key, (preset_from, preset_to) in presets.items()
        },
        'active_preset': active_preset,
    }


# ─── Analytics Dashboard (Executive / Operational / Security) ───────────────
# Read-only tabs built entirely from existing tables — see verification/analytics.py
# for the aggregation queries. No new models; admin-only, same inline gating
# convention as the report_* views above.

def _export_csv_response(filename, rows, report_name, request):
    """Shared CSV export for the analytics tabs — mirrors the csv.writer
    pattern already used by report_suspicious_attempts/report_claims etc.
    `rows` is a list of (label, value) tuples or ['Section header'] markers."""
    import csv
    from django.http import HttpResponse
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)
    for row in rows:
        writer.writerow(row)
    AuditLog.log(
        action=AuditLog.ACTION_REPORT_EXPORT,
        user=request.user,
        details={'report': report_name, 'format': 'csv'},
        request=request,
    )
    return response


@login_required
@never_cache
def analytics_executive(request):
    from django.contrib import messages
    from . import analytics as _analytics
    if not request.user.is_admin:
        messages.error(request, 'Admin access required.')
        return redirect('beneficiaries:dashboard')

    date_from = _parse_date(request.GET.get('date_from', ''))
    date_to = _parse_date(request.GET.get('date_to', ''))
    metrics = _analytics.get_executive_metrics(date_from, date_to)

    if request.GET.get('export') == 'csv':
        bc = metrics['beneficiary_counts']
        uc = metrics['user_counts']
        rows = [
            ['Metric', 'Value'],
            ['Total Beneficiaries', bc['total']],
            ['Active Beneficiaries', bc['active']],
            ['Pending Beneficiaries', bc['pending']],
            ['Inactive Beneficiaries', bc['inactive']],
            ['Deceased Beneficiaries', bc['deceased']],
            ['Total User Accounts', uc['total']],
            ['Active User Accounts', uc['active']],
            ['Staff Accounts', uc['staff']],
            ['Admin-Level Accounts', uc['admin_level']],
            ['Total Verifications (range)', metrics['total_verifications']],
            ['Verified Count (range)', metrics['verified_count']],
            ['Manual Review Count (range)', metrics['manual_review_count']],
            ['Verified Rate (%)', metrics['verified_rate']],
            ['Claims Count (range)', metrics['claims_count']],
            ['Claims Amount PHP (range)', f"{metrics['claims_amount']:,.2f}"],
            ['Pending Claims (current)', metrics['pending_claims']],
            [], ['Beneficiary Growth (last 12 months)'], ['Month', 'New', 'Cumulative'],
        ]
        for row in metrics['beneficiary_growth']:
            rows.append([row['month'].strftime('%Y-%m'), row['new'], row['cumulative']])
        rows += [[], ['Monthly Distribution (last 12 months)'], ['Month', 'Total PHP', 'Claims']]
        for row in metrics['monthly_distribution']:
            rows.append([row['month'].strftime('%Y-%m'), f"{row['total'] or 0:,.2f}", row['count']])
        return _export_csv_response('fansc-analytics-executive.csv', rows, 'analytics_executive', request)

    chart_data = {
        'beneficiary_growth': [
            {'month': row['month'].strftime('%Y-%m'), 'new': row['new'], 'cumulative': row['cumulative']}
            for row in metrics['beneficiary_growth']
        ],
        'monthly_distribution': [
            {'month': row['month'].strftime('%Y-%m'), 'total': float(row['total'] or 0)}
            for row in metrics['monthly_distribution']
        ],
        # v2.2.0 Post-UAT Phase 13: Claim Progress + Payout Completion for the
        # current/nearest stipend event.
        'distribution_progress': metrics['distribution_progress'],
    }

    return render(request, 'verification/analytics_executive.html', {
        **metrics,
        'chart_data': chart_data,
        'date_from': date_from,
        'date_to': date_to,
        'date_range_invalid': _date_range_invalid(date_from, date_to),
        'server_time': timezone.localtime(),
        **_analytics_toolbar_context(date_from, date_to),
    })


@login_required
@never_cache
def analytics_operational(request):
    from django.contrib import messages
    from . import analytics as _analytics
    if not request.user.is_admin:
        messages.error(request, 'Admin access required.')
        return redirect('beneficiaries:dashboard')

    date_from = _parse_date(request.GET.get('date_from', ''))
    date_to = _parse_date(request.GET.get('date_to', ''))
    metrics = _analytics.get_operational_metrics(date_from, date_to)

    chart_data = {
        'daily_trend': [
            {
                'day': row['day'].isoformat(), 'total': row['total'], 'verified': row['verified'],
                'manual_review': row['manual_review'], 'not_verified_denied': row['not_verified_denied'],
            }
            for row in metrics['daily_trend']
        ],
        'registration_trend': [
            {'day': row['day'].isoformat(), 'n': row['n']}
            for row in metrics['registration_trend']
        ],
        # v2.2.0 Post-UAT Phase 13: Verification Results — same data already
        # exported to CSV below, now also charted.
        'decision_breakdown': [
            {'decision': row['decision'] or 'unknown', 'n': row['n']}
            for row in metrics['decision_breakdown']
        ],
    }

    if request.GET.get('export') == 'csv':
        summ = metrics['summary']
        rows = [
            ['Top Operational Summary (range)'],
            ['Metric', 'Value'],
            ['Verification Attempts', summ['total']],
            ['Verified', summ['verified']],
            ['Manual Review', summ['manual_review']],
            ['Not Verified / Denied', summ['not_verified_denied']],
            ['Success Rate (%)', summ['success_rate']],
            ['Manual Review Rate (%)', summ['manual_review_rate']],
        ]
        rows += [[], ['Decision Breakdown (range)'], ['Decision', 'Count']]
        for row in metrics['decision_breakdown']:
            rows.append([row['decision'] or '(none)', row['n']])
        rows += [[], ['Daily Verification Trend'], ['Date', 'Total', 'Verified', 'Manual Review', 'Not Verified/Denied']]
        for row in metrics['daily_trend']:
            rows.append([row['day'].isoformat(), row['total'], row['verified'], row['manual_review'], row['not_verified_denied']])
        rows += [[], ['Daily Registration Trend'], ['Date', 'New Registrations']]
        for row in metrics['registration_trend']:
            rows.append([row['day'].isoformat(), row['n']])
        rows += [[], ['Top Staff by Verification Volume (range)'], ['Staff', 'Attempts', 'Verified', 'Manual Review', 'Claims Released', 'Success Rate (%)']]
        for row in metrics['staff_activity']:
            name = f"{row['performed_by__first_name']} {row['performed_by__last_name'] or row['performed_by__username']}"
            rows.append([name, row['n'], row['verified'], row['manual_review'], row['claims_released'], row['success_rate']])
        rows += [[], ['Distribution Summary by Barangay (top 15, range)'], ['Barangay', 'Claims', 'Total Released (PHP)']]
        for row in metrics['barangay_distribution']:
            rows.append([row['beneficiary__barangay'] or '(Unspecified)', row['claims_count'], f"{row['total_amount'] or 0:,.2f}"])
        return _export_csv_response('fansc-analytics-operational.csv', rows, 'analytics_operational', request)

    return render(request, 'verification/analytics_operational.html', {
        **metrics,
        'chart_data': chart_data,
        'date_from': date_from,
        'date_to': date_to,
        'date_range_invalid': _date_range_invalid(date_from, date_to),
        'server_time': timezone.localtime(),
        **_analytics_toolbar_context(date_from, date_to),
    })


@login_required
@never_cache
def analytics_security(request):
    from django.contrib import messages
    from . import analytics as _analytics
    if not request.user.is_admin:
        messages.error(request, 'Admin access required.')
        return redirect('beneficiaries:dashboard')

    date_from = _parse_date(request.GET.get('date_from', ''))
    date_to = _parse_date(request.GET.get('date_to', ''))
    metrics = _analytics.get_security_metrics(date_from, date_to)

    if request.GET.get('export') == 'csv':
        frc = metrics['fraud_risk_counts']
        rows = [
            ['Metric', 'Value'],
            ['Failed Logins (range)', metrics['failed_logins']],
            ['Failed Verification Attempts (range)', metrics['failed_verifications']],
            ['Duplicate Face Detections (range)', metrics['duplicate_faces']],
            ['Blocked Duplicate Payout Attempts (range)', metrics['duplicate_payout_attempts']],
            ['Payout Overrides (range)', metrics['payout_overrides']],
            ['Config Changes (range)', metrics['config_changes']],
            ['Manual Review Queue (current)', metrics['manual_review_pending']],
            ['Beneficiaries with 3+ Attempts (range)', metrics['high_attempt_beneficiaries']],
            ['Security Risk Indicators — HIGH (fixed lookback window, not date filter)', frc.get('HIGH', 0)],
            ['Security Risk Indicators — MEDIUM (fixed lookback window, not date filter)', frc.get('MEDIUM', 0)],
            ['Security Risk Indicators — LOW (fixed lookback window, not date filter)', frc.get('LOW', 0)],
        ]
        return _export_csv_response('fansc-analytics-security.csv', rows, 'analytics_security', request)

    # v2.2.0 Post-UAT Phase 13: Review/Security Cases chart.
    chart_data = {
        'review_cases': metrics['review_cases'],
        'security_events_trend': [
            {
                'day': row['day'].isoformat(), 'failed_logins': row['failed_logins'],
                'failed_verifications': row['failed_verifications'],
                'duplicate_faces': row['duplicate_faces'], 'payout_overrides': row['payout_overrides'],
            }
            for row in metrics['security_events_trend']
        ],
    }

    return render(request, 'verification/analytics_security.html', {
        **metrics,
        'chart_data': chart_data,
        'date_from': date_from,
        'date_to': date_to,
        'date_range_invalid': _date_range_invalid(date_from, date_to),
        'server_time': timezone.localtime(),
        **_analytics_toolbar_context(date_from, date_to),
    })


# ─── Biometric Performance Analytics (BPA-4) ─────────────────────────────────
# Presents the reviewed BPA-3/BPA-3.1 controlled-evaluation metric engine
# (verification.biometric_analytics) for one explicitly selected
# EvaluationDataset at a time. Deliberately separate from the operational
# Analytics tabs above (Executive/Operational/Security) — this page never
# reads VerificationAttempt/ClaimRecord/analytics.py, and those pages never
# read EvaluationTrial. Gated read-only for Technical Administrator (IT) and
# President — see _evaluation_admin_required below and BPA-4 checkpoint §3.

@login_required
def analytics_biometric_performance(request):
    denied = _evaluation_admin_required(request, write=False)
    if denied:
        return denied

    from . import biometric_analytics as ba

    datasets = EvaluationDataset.objects.all().order_by('-created_at')

    dataset = None
    invalid_selection = False
    raw_dataset_id = (request.GET.get('dataset') or '').strip()
    if raw_dataset_id:
        try:
            dataset_uuid = uuid.UUID(raw_dataset_id)
        except (ValueError, AttributeError, TypeError):
            invalid_selection = True
        else:
            dataset = datasets.filter(pk=dataset_uuid).first()
            if dataset is None:
                invalid_selection = True
    elif datasets.count() == 1:
        # Sole-dataset convenience preselection (BPA-4 §4) — the page still
        # names the dataset explicitly in the banner/summary below, it is
        # never silently aggregated across datasets.
        dataset = datasets.first()

    report = None
    threshold_snapshot_summary = None
    chart_payload = None
    if dataset is not None:
        candidates = ba.default_exploratory_threshold_candidates(dataset)
        report = ba.build_metric_report(dataset, threshold_candidates=candidates)
        threshold_snapshot_summary = ba.get_threshold_snapshot_summary(dataset)
        if dataset.status == EvaluationDataset.STATUS_ARCHIVED:
            banner_state = 'archived'
        elif dataset.status == EvaluationDataset.STATUS_COMPLETED:
            banner_state = 'finalized'
        else:
            banner_state = 'interim'

        sd = report['score_distributions']
        single_policy_reference = None
        if threshold_snapshot_summary['policies'] and not threshold_snapshot_summary['multiple_policies_represented']:
            single_policy_reference = {
                'review_threshold': threshold_snapshot_summary['policies'][0]['review_threshold_snapshot'],
                'auto_verify_threshold': threshold_snapshot_summary['policies'][0]['auto_verify_threshold_snapshot'],
            }
        chart_payload = {
            'score_domain': sd['score_domain'],
            'genuine_histogram': sd['genuine']['histogram'],
            'impostor_histogram': sd['impostor']['histogram'],
            'threshold_reference': single_policy_reference,
            'roc_points': report['roc']['points'],
        }
    else:
        banner_state = None

    return render(request, 'verification/analytics_biometric.html', {
        'active_tab': 'biometric',
        'datasets': datasets,
        'selected_dataset': dataset,
        'invalid_selection': invalid_selection,
        'banner_state': banner_state,
        'report': report,
        'threshold_snapshot_summary': threshold_snapshot_summary,
        'chart_payload': chart_payload,
        'attack_type_labels': dict(EvaluationTrial.PRESENTATION_GROUND_TRUTH_CHOICES),
        'liveness_pathway_labels': dict(EvaluationTrial.LIVENESS_PATHWAY_CHOICES),
        'decision_labels': dict(VerificationAttempt.DECISION_CHOICES),
    })


# ─── Fraud Detection Phase 1 (rule-based flags, no auto-block) ──────────────
# Report-only — see verification/fraud_signals.py. Nothing here writes to any
# other model or account status; every row links to the relevant beneficiary/
# staff page for a human to follow up on.

@login_required
def fraud_signals_report(request):
    from django.contrib import messages
    from . import fraud_signals as _fraud_signals
    if not request.user.is_admin:
        messages.error(request, 'Admin access required.')
        return redirect('beneficiaries:dashboard')

    try:
        _fraud_signals.sync_fraud_notifications()
    except Exception:
        logger.exception('sync_fraud_notifications failed (non-fatal)')

    return render(request, 'verification/report_fraud_signals.html', {
        'repeated_failures': _fraud_signals.repeated_failures(),
        'anomalous_staff_volume': _fraud_signals.anomalous_staff_volume(),
        'mass_edit_detection': _fraud_signals.mass_edit_detection(),
        'repeated_login_failures': _fraud_signals.repeated_login_failures(),
        'payout_anomalies': _fraud_signals.payout_anomalies(),
    })


# ─── Per-Template Match Analytics (P1.6) ─────────────────────────────────────
# Advisory only — see verification/template_analytics.py. update_face_data
# (above) surfaces a per-beneficiary suggestion; this is the global report.
#
# Access: Technical Administrator (IT) full access, President read-only
# (this page has no write actions, so "read-only" and "access" coincide),
# Admin/Staff no access — same policy as the rest of the controlled
# biometric-evaluation/technical-monitoring surface (see
# _evaluation_admin_required's docstring), matching the "System
# Evaluation & Research" nav gate this page's menu entry lives under
# (templates/base.html's Technical Administration dropdown).

@login_required
def template_match_report(request):
    from django.contrib import messages
    from . import template_analytics as _template_analytics
    if not (request.user.is_admin_it or request.user.is_president):
        messages.error(
            request,
            'Face Template Health is restricted to the Technical Administrator and President (read-only).',
        )
        return redirect('beneficiaries:dashboard')

    win_stats = _template_analytics.get_template_win_stats()

    # Aggregate the per-row win counts into the distribution/rate figures
    # that make this page genuine analytics rather than a raw record table:
    # total match events tallied, how they split between the primary
    # template and an additional (re-enrolled) one, and which beneficiaries
    # the existing re-enrollment-candidate signal (template_analytics.py)
    # is currently flagging.
    total_matches = sum(row['wins'] for row in win_stats)
    primary_matches = sum(
        row['wins'] for row in win_stats
        if row['matched_template'] == _template_analytics.PRIMARY_TEMPLATE_LABEL
    )
    additional_matches = total_matches - primary_matches
    distinct_beneficiaries = len({row['beneficiary__id'] for row in win_stats})

    candidate_ids = _template_analytics.get_reenrollment_candidates()
    reenrollment_candidates = Beneficiary.objects.filter(id__in=candidate_ids)

    return render(request, 'verification/report_template_match.html', {
        'win_stats': win_stats,
        'total_matches': total_matches,
        'primary_matches': primary_matches,
        'additional_matches': additional_matches,
        'primary_share': round(primary_matches / total_matches * 100, 1) if total_matches else None,
        'distinct_beneficiaries': distinct_beneficiaries,
        'reenrollment_candidates': reenrollment_candidates,
    })


# ─── Payout Detail / Admin Override ──────────────────────────────────────────

@login_required
def payout_detail(request, claim_id):
    """Read-only payout detail page.

    Staff may view payouts they processed (claimed_by or released_by themselves);
    Admins (HB / IT) may view any payout. The override controls only appear for
    admins on terminal records (locked) — see is_locked on the model.
    """
    from django.contrib import messages

    claim = get_object_or_404(
        ClaimRecord.objects.select_related(
            'beneficiary', 'stipend_event', 'claimed_by',
            'approved_by', 'released_by', 'override_by',
            'verification_attempt', 'representative',
        ),
        pk=claim_id,
    )

    # Object-level access control: staff can only see records they touched.
    if not request.user.is_admin:
        if claim.claimed_by_id != request.user.id and claim.released_by_id != request.user.id:
            messages.error(request, 'You do not have access to this payout record.')
            return redirect('beneficiaries:dashboard')

    # Issue 2: count prior cancellations for this beneficiary+event so the
    # admin override UI can warn before a repeated cancellation is submitted.
    prior_cancelled_count = (
        ClaimRecord.objects
        .filter(
            beneficiary=claim.beneficiary,
            stipend_event=claim.stipend_event,
            status=ClaimRecord.STATUS_CANCELLED,
        )
        .exclude(pk=claim.pk)
        .count()
    )
    repeat_threshold = getattr(django_settings, 'CANCEL_REPEAT_PRESIDENT_THRESHOLD', 3)

    # Section 6 — liveness/PAD evidence for this specific attempt, when a
    # LivenessTransaction was actually consumed for it. Never fabricated:
    # older attempts predating LivenessTransaction simply have none.
    liveness_tx = None
    if claim.verification_attempt_id:
        liveness_tx = claim.verification_attempt.liveness_transaction.first()

    # Section 6 — compact lifecycle sequence built ONLY from timestamps this
    # ClaimRecord actually persisted (never a fabricated/synthetic step).
    lifecycle = [{
        'label': 'Verification',
        'timestamp': claim.claimed_at,
        'actor': claim.claimed_by,
        'detail': claim.get_verification_method_display(),
    }]
    if claim.approved_at:
        lifecycle.append({
            'label': 'Review / Approval',
            'timestamp': claim.approved_at,
            'actor': claim.approved_by,
            'detail': None,
        })
    if claim.released_at:
        lifecycle.append({
            'label': 'Claim / Release',
            'timestamp': claim.released_at,
            'actor': claim.released_by,
            'detail': claim.reference_number,
        })
    if claim.override_at:
        lifecycle.append({
            'label': 'Administrative Correction',
            'timestamp': claim.override_at,
            'actor': claim.override_by,
            'detail': claim.override_reason,
        })
    lifecycle.sort(key=lambda step: step['timestamp'] or claim.claimed_at)

    return render(request, 'verification/payout_detail.html', {
        'claim': claim,
        'can_admin': request.user.is_admin,
        # Section 31 — Administrative Correction is authorized-roles only;
        # Technical Administrator can view this page but payout_action denies
        # the mutation, so don't show controls it can't actually use.
        'can_correct': request.user.has_financial_authority,
        'prior_cancelled_count': prior_cancelled_count,
        'cancel_repeat_threshold': repeat_threshold,
        'cancel_repeat_blocked': prior_cancelled_count >= repeat_threshold and not (request.user.is_president or request.user.role == request.user.ROLE_ADMIN or request.user.is_admin_it),
        # Section 16 — current AUTO_VERIFY threshold, shown alongside the
        # attempt's stored threshold_used (the manual-review floor) so the
        # two are never confused as a single unlabeled "Threshold". Not a
        # per-attempt snapshot (VerificationAttempt has no such field, and
        # adding one would require a migration this pass deliberately
        # avoids) — labeled "current setting" in the template for that reason.
        'current_auto_verify_threshold': SystemConfig.get_auto_verify_threshold(),
        'liveness_tx': liveness_tx,
        'lifecycle': lifecycle,
    })


@login_required
@require_http_methods(['POST'])
def payout_action(request, claim_id):
    """Admin-only payout lifecycle actions on a single ClaimRecord.

    Supported actions:
      cancel   — mark a released payout as STATUS_CANCELLED  (reason required)
      fail     — mark a released payout as STATUS_FAILED     (reason required)
      override — edit amount/reference_number/payout_remarks on a locked record
                 (reason required; records override_by/override_reason/override_at)

    All actions go through audit log. Refuses to act on records that are not
    yet in the claimed lifecycle (pending_approval / rejected are handled via
    pending_claim_review). is_locked records are the only ones eligible for
    cancel/fail/override since those are the post-release transitions.
    """
    from django.contrib import messages

    if not request.user.has_financial_authority:
        messages.error(request, 'Only the President or Admin can perform payout actions.')
        return redirect('beneficiaries:dashboard')

    action = request.POST.get('action', '')
    reason = request.POST.get('override_reason', '').strip()

    if action not in ('cancel', 'fail', 'override'):
        messages.error(request, 'Invalid action.')
        return redirect('verification:payout_detail', claim_id=claim_id)

    if not reason:
        messages.error(request, 'A written reason is required for any payout override.')
        return redirect('verification:payout_detail', claim_id=claim_id)

    with transaction.atomic():
        claim = ClaimRecord.objects.select_for_update().get(pk=claim_id)

        if claim.status == ClaimRecord.STATUS_PENDING_APPROVAL:
            messages.error(
                request,
                'This claim is still pending approval. Use the Pending Claim Review page instead.'
            )
            return redirect('verification:payout_detail', claim_id=claim_id)

        old_status = claim.status

        # Common override-audit fields. Every cancel / fail / edit captures who
        # changed the record and the written reason — never silently mutate.
        claim.override_by = request.user
        claim.override_reason = (
            f'[{action.upper()}] ' + reason
            + (f'  (was {old_status})' if action != 'override' else '')
        )
        claim.override_at = timezone.now()

        log_action = AuditLog.ACTION_PAYOUT_OVERRIDE
        log_details = {
            'claim_id': str(claim.id),
            'beneficiary_id': claim.beneficiary.beneficiary_id,
            'stipend_event': claim.stipend_event.title if claim.stipend_event else None,
            'reason': reason,
            'reference_number': claim.reference_number,
            'old_status': old_status,
        }

        if action == 'cancel':
            # ── Repeated cancellation safeguard (Issue 2) ─────────────────────
            # Count prior cancellations for the same beneficiary+event. If the
            # threshold is exceeded, require President/Admin and a longer
            # written reason; record the repeat-count in the audit details.
            prior_cancelled = (
                ClaimRecord.objects
                .filter(
                    beneficiary=claim.beneficiary,
                    stipend_event=claim.stipend_event,
                    status=ClaimRecord.STATUS_CANCELLED,
                )
                .exclude(pk=claim.pk)
                .count()
            )
            repeat_threshold = getattr(
                django_settings, 'CANCEL_REPEAT_PRESIDENT_THRESHOLD', 3,
            )
            min_repeat_reason_chars = getattr(
                django_settings, 'CANCEL_REPEAT_REASON_MIN_CHARS', 30,
            )
            if prior_cancelled >= repeat_threshold:
                if not (request.user.is_president or request.user.is_admin_it
                        or request.user.role == request.user.ROLE_ADMIN):
                    messages.error(
                        request,
                        f'Repeated cancellation blocked — this beneficiary already has '
                        f'{prior_cancelled} cancelled records for this event. Only '
                        f'President or Admin may cancel further records.'
                    )
                    return redirect('verification:payout_detail', claim_id=claim_id)
                if len(reason) < min_repeat_reason_chars:
                    messages.error(
                        request,
                        f'A more detailed written reason (>= {min_repeat_reason_chars} chars) '
                        f'is required because this beneficiary already has {prior_cancelled} '
                        f'cancelled records for this event.'
                    )
                    return redirect('verification:payout_detail', claim_id=claim_id)
            claim.status = ClaimRecord.STATUS_CANCELLED
            log_action = AuditLog.ACTION_PAYOUT_CANCELLED
            log_details['prior_cancellation_count'] = prior_cancelled
            log_details['repeat_threshold'] = repeat_threshold
        elif action == 'fail':
            claim.status = ClaimRecord.STATUS_FAILED
            log_action = AuditLog.ACTION_PAYOUT_FAILED
        else:  # override (edit amount / ref number / remarks on a locked record)
            new_amount_str = request.POST.get('amount', '').strip()
            new_reference  = request.POST.get('reference_number', '').strip()
            new_remarks    = request.POST.get('payout_remarks', '').strip()
            if new_amount_str:
                from decimal import Decimal, InvalidOperation
                try:
                    new_amount = Decimal(new_amount_str)
                    if new_amount < 0:
                        raise InvalidOperation
                    log_details['old_amount'] = str(claim.amount)
                    log_details['new_amount'] = str(new_amount)
                    claim.amount = new_amount
                except (InvalidOperation, ValueError):
                    messages.error(request, 'Amount must be a non-negative number.')
                    return redirect('verification:payout_detail', claim_id=claim_id)
            if new_reference and new_reference != claim.reference_number:
                log_details['old_reference'] = claim.reference_number
                log_details['new_reference'] = new_reference
                claim.reference_number = new_reference
            if new_remarks and new_remarks != claim.payout_remarks:
                claim.payout_remarks = new_remarks

        claim.save()

        AuditLog.log(
            action=log_action,
            user=request.user,
            target_type='ClaimRecord',
            target_id=claim.id,
            details=log_details,
            request=request,
        )

    messages.success(
        request,
        f'Payout {claim.reference_number or claim.id} updated: {action}.'
    )
    return redirect('verification:payout_detail', claim_id=claim_id)


@login_required
def report_claims(request):
    """
    Detailed Payout Report — master payout/claims list.

    Filters: date range (claim or release), stipend event, status, claimant type,
    released_by staff, barangay, verification_method, fallback/override flags,
    quick-filter shortcut (?quick=pending|released|failed|cancelled|month).
    Exports: CSV, Excel, print-friendly HTML.
    Roles: President, Admin, IT only.

    Why one master report instead of 11 separate ones: every requirement on the
    user's list (Distribution Summary, Pending, Released, Failed/Cancelled,
    Monthly, Custom Date Range, Detailed Payout) is a different filter combo
    over the same dataset.  A single page with quick-filter chips + saveable
    URLs is more maintainable than 11 nearly-identical views.
    """
    from django.contrib import messages
    from django.db.models import Sum, Count, Q

    if not request.user.is_admin:
        messages.error(request, 'Admin access required.')
        return redirect('beneficiaries:dashboard')

    # ── Filters ───────────────────────────────────────────────────────────────
    date_from    = _parse_date(request.GET.get('date_from', ''))
    date_to      = _parse_date(request.GET.get('date_to', ''))
    event_id     = request.GET.get('event', '')
    status_f     = request.GET.get('status', '')
    claimant_f   = request.GET.get('claimant', '')
    method_f     = request.GET.get('method', '')
    released_by_f = request.GET.get('released_by', '')
    barangay_f   = request.GET.get('barangay', '').strip()
    quick        = request.GET.get('quick', '').strip()
    export_fmt   = request.GET.get('export', '')   # '' | 'excel' | 'csv' | 'print'

    # Quick-filter shortcut maps to one or more concrete filters. These are
    # named so the URL is self-documenting: /reports/claims/?quick=pending
    if quick == 'pending':
        status_f = ClaimRecord.STATUS_PENDING_APPROVAL
    elif quick == 'released':
        status_f = ClaimRecord.STATUS_CLAIMED
    elif quick == 'failed':
        status_f = ClaimRecord.STATUS_FAILED
    elif quick == 'cancelled':
        status_f = ClaimRecord.STATUS_CANCELLED
    elif quick == 'month':
        import datetime
        today = timezone.localdate()
        date_from = today.replace(day=1)
        date_to = today

    qs = (
        ClaimRecord.objects
        .select_related(
            'beneficiary', 'stipend_event', 'claimed_by', 'approved_by',
            'representative', 'released_by', 'override_by', 'verification_attempt',
        )
        .order_by('-claimed_at')
    )

    if date_from:
        qs = qs.filter(claimed_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(claimed_at__date__lte=date_to)
    if event_id:
        qs = qs.filter(stipend_event_id=event_id)
    if status_f:
        qs = qs.filter(status=status_f)
    if claimant_f:
        qs = qs.filter(claimant_type=claimant_f)
    if method_f:
        qs = qs.filter(verification_method=method_f)
    if released_by_f:
        # v2.1.19 UX pass (section 28): released_by is now a user-ID selector,
        # not a free-text substring search — the old version silently matched
        # against the hidden username even though the table only displays the
        # full name (e.g. searching "Exe" matched username "aeilexe" while the
        # table shows "Whinelit Recto"), which read as broken/mysterious. A
        # non-numeric value (e.g. an old bookmarked ?released_by=text URL)
        # is treated as "no match" rather than a crash.
        if released_by_f.isdigit():
            qs = qs.filter(released_by_id=released_by_f)
        else:
            qs = qs.none()
    if barangay_f:
        qs = qs.filter(beneficiary__barangay__icontains=barangay_f)

    events = StipendEvent.objects.order_by('-date')
    from accounts.models import CustomUser as _CustomUser
    releasers = _CustomUser.objects.filter(released_claims__isnull=False).distinct().order_by('last_name', 'first_name')

    # ── Summary totals (always computed — kept in sync with whichever filters apply) ──
    totals = qs.aggregate(
        total_amount   = Sum('amount'),
        total_records  = Count('id'),
        total_paid     = Count('id', filter=Q(status=ClaimRecord.STATUS_CLAIMED)),
        total_pending  = Count('id', filter=Q(status=ClaimRecord.STATUS_PENDING_APPROVAL)),
        total_failed   = Count('id', filter=Q(status=ClaimRecord.STATUS_FAILED)),
        total_cancelled = Count('id', filter=Q(status=ClaimRecord.STATUS_CANCELLED)),
        total_rejected = Count('id', filter=Q(status=ClaimRecord.STATUS_REJECTED)),
        amount_released  = Sum('amount', filter=Q(status=ClaimRecord.STATUS_CLAIMED)),
        amount_failed    = Sum('amount', filter=Q(status=ClaimRecord.STATUS_FAILED)),
        amount_cancelled = Sum('amount', filter=Q(status=ClaimRecord.STATUS_CANCELLED)),
        n_overrides      = Count('id', filter=Q(override_by__isnull=False)),
        n_fallback       = Count('id', filter=Q(verification_method=ClaimRecord.VERIFY_FALLBACK)),
        n_manual         = Count('id', filter=Q(verification_method=ClaimRecord.VERIFY_MANUAL)),
    )

    common_filter_log = {
        'date_from': str(date_from) if date_from else None,
        'date_to':   str(date_to)   if date_to   else None,
        'event':     event_id or None,
        'status':    status_f or None,
        'claimant':  claimant_f or None,
        'method':    method_f or None,
        'released_by': released_by_f or None,
        'barangay':  barangay_f or None,
        'quick':     quick or None,
    }

    # ── Excel export ──────────────────────────────────────────────────────────
    if export_fmt == 'excel':
        from django.http import HttpResponse
        from fans.report_export import build_report_workbook

        def _naive_local(dt):
            # openpyxl rejects timezone-aware datetimes outright — convert to
            # local wall-clock time and drop tzinfo so the cell is a real
            # Excel datetime (sortable, filterable), not a pre-formatted
            # string that overflows a narrow column into ########.
            if not dt:
                return None
            return timezone.localtime(dt).replace(tzinfo=None)

        headers = [
            'Reference No.', 'Status', 'Beneficiary ID', 'Senior Citizen ID', 'Full Name',
            'Age', 'Barangay', 'Address', 'Stipend Event', 'Amount (PHP)',
            'Claimant Type', 'Representative', 'Verification Method', 'Verification Score',
            'Released By', 'Released At', 'Claimed By', 'Approved By',
            'Override By', 'Override Reason', 'Remarks', 'Created', 'Updated',
        ]
        AMOUNT_COL = headers.index('Amount (PHP)')
        DATE_COLS = [headers.index('Released At'), headers.index('Created'), headers.index('Updated')]

        def _rows():
            for r in qs:
                yield [
                    r.reference_number,
                    r.get_status_display(),
                    r.beneficiary.beneficiary_id,
                    r.beneficiary.senior_citizen_id,
                    r.beneficiary.full_name,
                    r.beneficiary.age,
                    r.beneficiary.barangay,
                    r.beneficiary.address,
                    r.stipend_event.title if r.stipend_event else '(No Event)',
                    float(r.amount),
                    r.get_claimant_type_display() if hasattr(r, 'get_claimant_type_display') else r.claimant_type,
                    r.representative.full_name if r.representative else '',
                    r.get_verification_method_display(),
                    r.verification_attempt.similarity_score if r.verification_attempt else None,
                    (r.released_by.get_full_name() or r.released_by.username) if r.released_by else '',
                    _naive_local(r.released_at),
                    (r.claimed_by.get_full_name() or r.claimed_by.username) if r.claimed_by else '',
                    (r.approved_by.get_full_name() or r.approved_by.username) if r.approved_by else '',
                    (r.override_by.get_full_name() or r.override_by.username) if r.override_by else '',
                    r.override_reason,
                    r.payout_remarks,
                    _naive_local(r.claimed_at),
                    _naive_local(r.updated_at),
                ]

        rows = list(_rows())
        row_count = len(rows)

        totals_row = [None] * len(headers)
        totals_row[0] = f'TOTALS ({row_count} record{"s" if row_count != 1 else ""})'
        totals_row[AMOUNT_COL] = float(totals['total_amount'] or 0)

        wb = build_report_workbook(
            title='Detailed Payout Claims Report',
            sheet_name='Detailed Payouts',
            headers=headers,
            rows=rows,
            generated_by=request.user.get_full_name() or request.user.username,
            filters=common_filter_log,
            currency_columns=[AMOUNT_COL],
            date_columns=DATE_COLS,
            totals_row=totals_row,
        )

        AuditLog.log(
            action=AuditLog.ACTION_REPORT_EXPORT,
            user=request.user,
            details={
                'report': 'detailed_payouts',
                'format': 'excel',
                'filters': common_filter_log,
                'row_count': row_count,
            },
            request=request,
        )

        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        response['Content-Disposition'] = 'attachment; filename="fansc-detailed-payouts.xlsx"'
        wb.save(response)
        return response

    # ── CSV export ────────────────────────────────────────────────────────────
    if export_fmt == 'csv':
        import csv
        from django.http import HttpResponse

        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="fansc-detailed-payouts.csv"'
        writer = csv.writer(response)
        writer.writerow([
            'Reference No.', 'Beneficiary ID', 'Senior Citizen ID', 'Full Name',
            'Age', 'Barangay', 'Stipend Event', 'Amount (PHP)', 'Status',
            'Verification Method', 'Claimant Type', 'Representative',
            'Released By', 'Released At', 'Claimed By', 'Approved By',
            'Override By', 'Override Reason', 'Remarks', 'Created', 'Updated',
        ])
        for r in qs:
            writer.writerow([
                r.reference_number,
                r.beneficiary.beneficiary_id,
                r.beneficiary.senior_citizen_id,
                r.beneficiary.full_name,
                r.beneficiary.age,
                r.beneficiary.barangay,
                r.stipend_event.title if r.stipend_event else '',
                f'{r.amount:,.2f}',
                r.get_status_display(),
                r.get_verification_method_display(),
                r.claimant_type,
                r.representative.full_name if r.representative else '',
                r.released_by.username if r.released_by else '',
                r.released_at.astimezone().strftime('%Y-%m-%d %H:%M') if r.released_at else '',
                r.claimed_by.username if r.claimed_by else '',
                r.approved_by.username if r.approved_by else '',
                r.override_by.username if r.override_by else '',
                r.override_reason,
                r.payout_remarks,
                r.claimed_at.astimezone().strftime('%Y-%m-%d %H:%M') if r.claimed_at else '',
                r.updated_at.astimezone().strftime('%Y-%m-%d %H:%M') if r.updated_at else '',
            ])

        AuditLog.log(
            action=AuditLog.ACTION_REPORT_EXPORT,
            user=request.user,
            details={
                'report': 'detailed_payouts',
                'format': 'csv',
                'filters': common_filter_log,
                'row_count': qs.count(),
            },
            request=request,
        )
        return response

    # ── Print / HTML export ───────────────────────────────────────────────────
    if export_fmt == 'print':
        AuditLog.log(
            action=AuditLog.ACTION_REPORT_EXPORT,
            user=request.user,
            details={
                'report': 'detailed_payouts',
                'format': 'print',
                'filters': common_filter_log,
                'row_count': qs.count(),
            },
            request=request,
        )
        return render(request, 'verification/report_claims_print.html', {
            'claims': qs,
            'totals': totals,
            'date_from': date_from,
            'date_to': date_to,
            'event_id': event_id,
            'status_f': status_f,
            'method_f': method_f,
            'barangay_f': barangay_f,
        })

    # Paginate the on-screen view so a barangay with thousands of payouts
    # doesn't render an 8k-row HTML table in the browser.
    from django.core.paginator import Paginator
    paginator = Paginator(qs, 50)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    return render(request, 'verification/report_claims.html', {
        'claims': page_obj,
        'page_obj': page_obj,
        'paginator': paginator,
        'events': events,
        'releasers': releasers,
        'date_from': date_from,
        'date_to': date_to,
        'event_id': event_id,
        'status_f': status_f,
        'claimant_f': claimant_f,
        'method_f': method_f,
        'released_by_f': released_by_f,
        'barangay_f': barangay_f,
        'quick': quick,
        'status_choices': ClaimRecord.STATUS_CHOICES,
        'claimant_choices': VerificationAttempt.CLAIMANT_CHOICES,
        'method_choices': ClaimRecord.VERIFICATION_METHOD_CHOICES,
        'totals': totals,
        'total': qs.count(),
    })


@login_required
def report_event_summary(request):
    """
    Per-event payout summary: total eligible, claimed, pending, fallback, denied.
    Roles: President, Admin, IT only.

    Date filtering (Date From/To) applies to StipendEvent.date — the event's
    own reference/schedule date, which is what each summary row is grouped
    by (same field the Payout Schedule list's Past Events filter already
    uses). It selects WHICH EVENTS appear in the report; it intentionally
    does not re-slice each event's claim/attempt counts by a different
    timestamp, since those totals are per-event lifetime figures and mixing
    in a second, differently-scoped date field would make the numbers
    inconsistent with the rest of the app. Both bounds are inclusive;
    StipendEvent.date is a plain DateField so no timezone conversion applies.
    """
    from django.contrib import messages
    from django.db.models import Count, Sum, Q as Qm

    if not request.user.is_admin:
        messages.error(request, 'Admin access required.')
        return redirect('beneficiaries:dashboard')

    export_fmt = request.GET.get('export', '')
    date_from = _parse_date(request.GET.get('date_from', ''))
    date_to = _parse_date(request.GET.get('date_to', ''))
    event_type_filter = request.GET.get('event_type', '').strip()
    valid_event_types = {v for v, _ in StipendEvent.EVENT_TYPE_CHOICES}
    if event_type_filter not in valid_event_types:
        event_type_filter = ''
    date_range_invalid = _date_range_invalid(date_from, date_to)

    event_qs = StipendEvent.objects.order_by('-date')
    if date_from:
        event_qs = event_qs.filter(date__gte=date_from)
    if date_to:
        event_qs = event_qs.filter(date__lte=date_to)
    if event_type_filter:
        event_qs = event_qs.filter(event_type=event_type_filter)
    # date_from > date_to combines to an impossible gte/lte pair above, so the
    # queryset already comes back empty; date_range_invalid only drives the
    # warning banner (same convention as the analytics tabs).

    summaries = []
    for ev in event_qs:
        claims_qs = ClaimRecord.objects.filter(stipend_event=ev)
        total_claimed    = claims_qs.filter(status=ClaimRecord.STATUS_CLAIMED).count()
        total_pending    = claims_qs.filter(status=ClaimRecord.STATUS_PENDING_APPROVAL).count()
        total_rejected   = claims_qs.filter(status=ClaimRecord.STATUS_REJECTED).count()
        total_attempts   = VerificationAttempt.objects.filter(stipend_event=ev).count()
        total_fallback   = VerificationAttempt.objects.filter(
            stipend_event=ev, fallback_triggered=True
        ).count()
        summaries.append({
            'event': ev,
            'claimed': total_claimed,
            'pending': total_pending,
            'rejected': total_rejected,
            'attempts': total_attempts,
            'fallback': total_fallback,
        })

    # Report-wide KPI cards — aggregated over the same filtered event set and
    # the same status/field definitions as the per-event rows above, so the
    # cards can never disagree with the table they summarize.
    claim_totals = ClaimRecord.objects.filter(stipend_event__in=event_qs).aggregate(
        claimed=Count('id', filter=Qm(status=ClaimRecord.STATUS_CLAIMED)),
        pending=Count('id', filter=Qm(status=ClaimRecord.STATUS_PENDING_APPROVAL)),
        rejected=Count('id', filter=Qm(status=ClaimRecord.STATUS_REJECTED)),
        released_amount=Sum('amount', filter=Qm(status=ClaimRecord.STATUS_CLAIMED)),
    )
    attempt_totals = VerificationAttempt.objects.filter(stipend_event__in=event_qs).aggregate(
        attempts=Count('id'),
        fallback=Count('id', filter=Qm(fallback_triggered=True)),
    )
    kpi_totals = {
        'events': len(summaries),
        'claimed': claim_totals['claimed'] or 0,
        'pending': claim_totals['pending'] or 0,
        'rejected': claim_totals['rejected'] or 0,
        'attempts': attempt_totals['attempts'] or 0,
        'fallback': attempt_totals['fallback'] or 0,
        'released_amount': claim_totals['released_amount'] or 0,
    }

    if export_fmt == 'excel':
        from django.http import HttpResponse
        from fans.report_export import build_report_workbook

        headers = ['Event', 'Date', 'Type', 'Claimed', 'Pending Approval',
                   'Rejected', 'Total Attempts', 'Fallbacks']
        rows = [
            [
                s['event'].title,
                s['event'].date,
                s['event'].get_event_type_display(),
                s['claimed'],
                s['pending'],
                s['rejected'],
                s['attempts'],
                s['fallback'],
            ]
            for s in summaries
        ]

        wb = build_report_workbook(
            title='Distribution / Event Summary',
            sheet_name='Event Summary',
            headers=headers,
            rows=rows,
            generated_by=request.user.get_full_name() or request.user.username,
        )

        AuditLog.log(
            action=AuditLog.ACTION_REPORT_EXPORT,
            user=request.user,
            details={'report': 'event_summary', 'format': 'excel'},
            request=request,
        )

        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        response['Content-Disposition'] = 'attachment; filename="fansc-distribution-summary.xlsx"'
        wb.save(response)
        return response

    if export_fmt == 'print':
        AuditLog.log(
            action=AuditLog.ACTION_REPORT_EXPORT,
            user=request.user,
            details={'report': 'event_summary', 'format': 'print'},
            request=request,
        )
        return render(request, 'verification/report_event_summary_print.html', {
            'summaries': summaries,
            'kpi_totals': kpi_totals,
            'date_from': date_from,
            'date_to': date_to,
        })

    return render(request, 'verification/report_event_summary.html', {
        'summaries': summaries,
        'kpi_totals': kpi_totals,
        'date_from': date_from,
        'date_to': date_to,
        'event_type_filter': event_type_filter,
        'event_type_choices': StipendEvent.EVENT_TYPE_CHOICES,
        'date_range_invalid': date_range_invalid,
    })


# ─── Staff Distribution Activity Report ───────────────────────────────────────

@login_required
def report_staff_performance(request):
    """Aggregate payouts grouped by released_by / claimed_by user.

    Shows: per-staff total released, count claimed, count failed, count cancelled,
    number of admin overrides. These are raw activity/volume counts only — not a
    productivity score, quality rating, or efficiency ranking (no normalization for
    hours worked, shift length, or task difficulty). Useful for spotting workload
    distribution or unusual patterns (e.g. one operator with high override rates).
    """
    from django.contrib import messages
    from django.db.models import Sum, Count, Q
    if not request.user.is_admin:
        messages.error(request, 'Admin access required.')
        return redirect('beneficiaries:dashboard')

    date_from = _parse_date(request.GET.get('date_from', ''))
    date_to   = _parse_date(request.GET.get('date_to', ''))
    event_id  = request.GET.get('event', '')
    export_fmt = request.GET.get('export', '')

    qs = ClaimRecord.objects.filter(released_by__isnull=False)
    if date_from:
        qs = qs.filter(released_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(released_at__date__lte=date_to)
    if event_id:
        qs = qs.filter(stipend_event_id=event_id)

    rows = (
        qs.values('released_by__id', 'released_by__username',
                  'released_by__first_name', 'released_by__last_name',
                  'released_by__role')
          .annotate(
              total_released = Sum('amount', filter=Q(status=ClaimRecord.STATUS_CLAIMED)),
              n_claimed      = Count('id', filter=Q(status=ClaimRecord.STATUS_CLAIMED)),
              n_failed       = Count('id', filter=Q(status=ClaimRecord.STATUS_FAILED)),
              n_cancelled    = Count('id', filter=Q(status=ClaimRecord.STATUS_CANCELLED)),
              n_overrides    = Count('id', filter=Q(override_by__isnull=False)),
              n_fallback     = Count('id', filter=Q(verification_method=ClaimRecord.VERIFY_FALLBACK)),
              n_manual       = Count('id', filter=Q(verification_method=ClaimRecord.VERIFY_MANUAL)),
              n_total        = Count('id'),
          )
          .order_by('-n_claimed')
    )

    events = StipendEvent.objects.order_by('-date')

    if export_fmt == 'csv':
        import csv
        from django.http import HttpResponse
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="fansc-staff-performance.csv"'
        writer = csv.writer(response)
        from accounts.models import CustomUser as _CustomUser
        role_labels = dict(_CustomUser.ROLE_CHOICES)
        role_labels[_CustomUser.ROLE_IT] = _CustomUser.TECHNICAL_ADMIN_LABEL
        writer.writerow(['Username', 'Full Name', 'Role',
                         'Total Released (PHP)', 'Claimed Count', 'Failed', 'Cancelled',
                         'Overrides', 'Manual Verification (ID)', 'Manual Verify', 'Total Records'])
        for r in rows:
            full_name = f"{r['released_by__first_name']} {r['released_by__last_name']}".strip()
            writer.writerow([
                r['released_by__username'],
                full_name or r['released_by__username'],
                role_labels.get(r['released_by__role'], r['released_by__role']),
                f"{r['total_released'] or 0:,.2f}",
                r['n_claimed'], r['n_failed'], r['n_cancelled'],
                r['n_overrides'], r['n_fallback'], r['n_manual'], r['n_total'],
            ])
        AuditLog.log(
            action=AuditLog.ACTION_REPORT_EXPORT,
            user=request.user,
            details={'report': 'staff_performance', 'format': 'csv',
                     'row_count': len(rows)},
            request=request,
        )
        return response

    return render(request, 'verification/report_staff_performance.html', {
        'rows': rows,
        'events': events,
        'date_from': date_from,
        'date_to': date_to,
        'event_id': event_id,
    })


# ─── Manual Override & Fallback Verification Report ──────────────────────────

@login_required
def report_override_fallback(request):
    """All payouts touched by an admin override OR released via fallback ID /
    approved manual verification. The audit cluster for "money moved by a path
    other than a clean face match"."""
    from django.contrib import messages
    from django.db.models import Q
    if not request.user.is_admin:
        messages.error(request, 'Admin access required.')
        return redirect('beneficiaries:dashboard')

    date_from = _parse_date(request.GET.get('date_from', ''))
    date_to   = _parse_date(request.GET.get('date_to', ''))
    event_id  = request.GET.get('event', '')
    export_fmt = request.GET.get('export', '')

    qs = (
        ClaimRecord.objects
        .filter(
            Q(override_by__isnull=False)
            | Q(verification_method=ClaimRecord.VERIFY_FALLBACK)
            | Q(verification_method=ClaimRecord.VERIFY_MANUAL)
            | Q(verification_method=ClaimRecord.VERIFY_OVERRIDE)
        )
        .select_related('beneficiary', 'stipend_event', 'released_by', 'override_by',
                        'claimed_by', 'approved_by', 'verification_attempt')
        .order_by('-claimed_at')
    )
    if date_from:
        qs = qs.filter(claimed_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(claimed_at__date__lte=date_to)
    if event_id:
        qs = qs.filter(stipend_event_id=event_id)

    events = StipendEvent.objects.order_by('-date')

    if export_fmt == 'csv':
        import csv
        from django.http import HttpResponse
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="fansc-override-fallback.csv"'
        writer = csv.writer(response)
        writer.writerow(['Reference', 'Beneficiary ID', 'Full Name', 'Event',
                         'Amount', 'Status', 'Verification Method',
                         'Override By', 'Override Reason', 'Released By', 'Released At'])
        for r in qs:
            writer.writerow([
                r.reference_number,
                r.beneficiary.beneficiary_id,
                r.beneficiary.full_name,
                r.stipend_event.title if r.stipend_event else '',
                f'{r.amount:,.2f}',
                r.get_status_display(),
                r.get_verification_method_display(),
                r.override_by.username if r.override_by else '',
                r.override_reason,
                r.released_by.username if r.released_by else '',
                r.released_at.astimezone().strftime('%Y-%m-%d %H:%M') if r.released_at else '',
            ])
        AuditLog.log(
            action=AuditLog.ACTION_REPORT_EXPORT,
            user=request.user,
            details={'report': 'override_fallback', 'format': 'csv',
                     'row_count': qs.count()},
            request=request,
        )
        return response

    from django.core.paginator import Paginator
    paginator = Paginator(qs, 50)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    return render(request, 'verification/report_override_fallback.html', {
        'rows': page_obj,
        'page_obj': page_obj,
        'paginator': paginator,
        'events': events,
        'date_from': date_from,
        'date_to': date_to,
        'event_id': event_id,
        'total': qs.count(),
    })


# ─── Suspicious / Duplicate Payout Attempts Report ───────────────────────────

@login_required
def report_suspicious_attempts(request):
    """Cluster of security-relevant events:
       - DUPLICATE_FACE audit entries (lookalike triggered manual review)
       - DUPLICATE_PAYOUT_ATTEMPT (blocked second claim for same event)
       - Beneficiaries with 3+ verification attempts for the same event
    """
    from django.contrib import messages
    from django.db.models import Count, Q
    if not request.user.is_admin:
        messages.error(request, 'Admin access required.')
        return redirect('beneficiaries:dashboard')

    date_from = _parse_date(request.GET.get('date_from', ''))
    date_to   = _parse_date(request.GET.get('date_to', ''))
    export_fmt = request.GET.get('export', '')

    audit_qs = AuditLog.objects.filter(
        action__in=[
            AuditLog.ACTION_DUPLICATE_FACE,
            AuditLog.ACTION_DUPLICATE_PAYOUT_ATTEMPT,
        ]
    ).select_related('user').order_by('-timestamp')
    if date_from:
        audit_qs = audit_qs.filter(timestamp__date__gte=date_from)
    if date_to:
        audit_qs = audit_qs.filter(timestamp__date__lte=date_to)

    # Beneficiaries with high attempt counts (potential repeat fraud attempts).
    high_attempt = (
        VerificationAttempt.objects
        .values('beneficiary__beneficiary_id', 'beneficiary__id',
                'beneficiary__first_name', 'beneficiary__last_name',
                'stipend_event__title', 'stipend_event__id')
        .annotate(n_attempts=Count('id'),
                  n_failed=Count('id', filter=Q(decision=VerificationAttempt.DECISION_NOT_VERIFIED)))
        .filter(n_attempts__gte=3)
        .order_by('-n_attempts')[:200]
    )

    if export_fmt == 'csv':
        import csv
        from django.http import HttpResponse
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="fansc-suspicious.csv"'
        writer = csv.writer(response)
        writer.writerow(['Timestamp', 'Action', 'User', 'Beneficiary', 'Details'])
        for entry in audit_qs[:2000]:
            writer.writerow([
                entry.timestamp.astimezone().strftime('%Y-%m-%d %H:%M:%S'),
                entry.get_action_display(),
                entry.user.username if entry.user else '',
                entry.details.get('beneficiary_id', ''),
                json.dumps(entry.details, default=str),
            ])
        AuditLog.log(
            action=AuditLog.ACTION_REPORT_EXPORT,
            user=request.user,
            details={'report': 'suspicious_attempts', 'format': 'csv'},
            request=request,
        )
        return response

    from django.core.paginator import Paginator
    paginator = Paginator(audit_qs, 50)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    return render(request, 'verification/report_suspicious.html', {
        'audit_rows': page_obj,
        'page_obj': page_obj,
        'paginator': paginator,
        'high_attempt': high_attempt,
        'date_from': date_from,
        'date_to': date_to,
    })


# ─── Beneficiary Payout History ──────────────────────────────────────────────

@login_required
def report_beneficiary_history(request, beneficiary_id):
    """Per-beneficiary payout + verification history. Staff may view their
    own beneficiaries (records they claimed/released); admins see everyone.
    """
    from django.contrib import messages
    from django.db.models import Q
    beneficiary = get_object_or_404(Beneficiary, pk=beneficiary_id)

    if not request.user.is_admin:
        # Object-level: staff can see this page if they processed any claim or
        # verification attempt for this beneficiary.
        has_touched = (
            ClaimRecord.objects.filter(
                beneficiary=beneficiary,
            ).filter(Q(claimed_by=request.user) | Q(released_by=request.user)).exists()
            or VerificationAttempt.objects.filter(
                beneficiary=beneficiary, performed_by=request.user,
            ).exists()
        )
        if not has_touched:
            messages.error(request, 'You do not have access to this beneficiary\'s payout history.')
            return redirect('beneficiaries:dashboard')

    # Exclude legacy placeholder rows: pending_approval claims with no event and ₱0
    # (created before the no-event claim block was removed in v2.0.5).
    # These are not real payout records and should not appear in history.
    claims = (
        ClaimRecord.objects
        .filter(beneficiary=beneficiary)
        .exclude(
            status=ClaimRecord.STATUS_PENDING_APPROVAL,
            stipend_event__isnull=True,
            amount=0,
        )
        .select_related('stipend_event', 'released_by', 'claimed_by', 'approved_by',
                        'override_by', 'verification_attempt')
        .order_by('-claimed_at')
    )
    attempts = (
        VerificationAttempt.objects
        .filter(beneficiary=beneficiary)
        .select_related('stipend_event', 'performed_by')
        .order_by('-timestamp')[:50]
    )

    from django.db.models import Sum, Count
    totals = claims.aggregate(
        total_claimed_amount = Sum('amount', filter=Q(status=ClaimRecord.STATUS_CLAIMED)),
        total_records        = Count('id'),
        total_claimed        = Count('id', filter=Q(status=ClaimRecord.STATUS_CLAIMED)),
    )

    export_fmt = request.GET.get('export', '')
    if export_fmt == 'csv':
        import csv
        from django.http import HttpResponse
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = (
            f'attachment; filename="fansc-history-{beneficiary.beneficiary_id}.csv"'
        )
        writer = csv.writer(response)
        writer.writerow(['Reference', 'Event', 'Amount', 'Status', 'Method',
                         'Released By', 'Released At'])
        for c in claims:
            writer.writerow([
                c.reference_number,
                c.stipend_event.title if c.stipend_event else '',
                f'{c.amount:,.2f}',
                c.get_status_display(),
                c.get_verification_method_display(),
                c.released_by.username if c.released_by else '',
                c.released_at.astimezone().strftime('%Y-%m-%d %H:%M') if c.released_at else '',
            ])
        AuditLog.log(
            action=AuditLog.ACTION_REPORT_EXPORT,
            user=request.user,
            details={'report': 'beneficiary_history', 'format': 'csv',
                     'beneficiary_id': beneficiary.beneficiary_id},
            request=request,
        )
        return response

    if export_fmt == 'pdf':
        return _beneficiary_history_pdf(request, beneficiary, claims, attempts, totals)

    return render(request, 'verification/report_beneficiary_history.html', {
        'beneficiary': beneficiary,
        'claims': claims,
        'attempts': attempts,
        'totals': totals,
    })


def _beneficiary_history_pdf(request, beneficiary, claims, attempts, totals):
    """Generate a PDF export of a beneficiary's payout and verification history."""
    from io import BytesIO
    from django.http import HttpResponse
    from django.utils import timezone as _tz

    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        )
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.enums import TA_CENTER, TA_LEFT
    except ImportError:
        return HttpResponse(
            'PDF export requires the reportlab library. '
            'Run: python -m pip install reportlab',
            status=503,
            content_type='text/plain',
        )

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=15*mm, rightMargin=15*mm,
                            topMargin=15*mm, bottomMargin=15*mm)
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle('h1', parent=styles['Heading1'], fontSize=14, spaceAfter=4)
    h2 = ParagraphStyle('h2', parent=styles['Heading2'], fontSize=11, spaceAfter=3,
                        spaceBefore=8)
    small = ParagraphStyle('small', parent=styles['Normal'], fontSize=8,
                           textColor=colors.grey)
    body = ParagraphStyle('body', parent=styles['Normal'], fontSize=9)

    elements = []

    # Header
    elements.append(Paragraph('FANSC — Beneficiary Payout History', h1))
    elements.append(Paragraph(
        f'{beneficiary.full_name} &nbsp;·&nbsp; '
        f'ID: {beneficiary.beneficiary_id} &nbsp;·&nbsp; '
        f'Age {beneficiary.age} &nbsp;·&nbsp; '
        f'{beneficiary.barangay}, {beneficiary.municipality}',
        body))
    if beneficiary.senior_citizen_id:
        elements.append(Paragraph(f'OSCA / SC ID: {beneficiary.senior_citizen_id}', body))
    elements.append(Paragraph(
        f'Generated: {_tz.localtime(_tz.now()).strftime("%Y-%m-%d %H:%M")} '
        f'by {request.user.get_full_name() or request.user.username}',
        small))
    elements.append(Spacer(1, 4*mm))

    # Summary cards
    total_amt = totals.get('total_claimed_amount') or 0
    total_records = totals.get('total_records') or 0
    total_claimed = totals.get('total_claimed') or 0
    elements.append(Paragraph(
        f'Total Distributed: <b>PHP {total_amt:,.2f}</b> &nbsp;|&nbsp; '
        f'Claims: <b>{total_records}</b> &nbsp;|&nbsp; '
        f'Successfully Claimed: <b>{total_claimed}</b>',
        body))
    elements.append(Spacer(1, 3*mm))

    # Claim / Payout History table
    elements.append(Paragraph('Claim / Payout History', h2))
    tbl_data = [['Reference', 'Event', 'Amount (PHP)', 'Status', 'Method', 'Released At']]
    for c in claims:
        tbl_data.append([
            c.reference_number or '—',
            (c.stipend_event.title if c.stipend_event else '(No Event)'),
            f'{c.amount:,.2f}',
            c.get_status_display(),
            c.get_verification_method_display(),
            (c.released_at.astimezone().strftime('%Y-%m-%d %H:%M') if c.released_at else '—'),
        ])
    if len(tbl_data) == 1:
        tbl_data.append(['No claims recorded.', '', '', '', '', ''])

    col_widths = [38*mm, 52*mm, 22*mm, 22*mm, 30*mm, 30*mm]
    tbl = Table(tbl_data, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a3a6b')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 7.5),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8fafc')]),
        ('GRID', (0, 0), (-1, -1), 0.25, colors.lightgrey),
        ('ALIGN', (2, 1), (2, -1), 'RIGHT'),
        ('LEFTPADDING', (0, 0), (-1, -1), 3),
        ('RIGHTPADDING', (0, 0), (-1, -1), 3),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
    ]))
    elements.append(tbl)
    elements.append(Spacer(1, 3*mm))

    # Recent Verification Attempts table
    elements.append(Paragraph('Recent Verification Attempts (last 50)', h2))
    atbl_data = [['Timestamp', 'Decision', 'Event', 'Score', 'Threshold', 'Performed By']]
    for a in attempts:
        atbl_data.append([
            a.timestamp.astimezone().strftime('%Y-%m-%d %H:%M'),
            a.get_decision_display(),
            (a.stipend_event.title if a.stipend_event else '—'),
            f'{a.similarity_score:.3f}' if a.similarity_score is not None else '—',
            f'{a.threshold_used:.2f}',
            (a.performed_by.username if a.performed_by else '—'),
        ])
    if len(atbl_data) == 1:
        atbl_data.append(['No verification attempts recorded.', '', '', '', '', ''])

    acol_widths = [35*mm, 25*mm, 50*mm, 18*mm, 18*mm, 30*mm]
    atbl = Table(atbl_data, colWidths=acol_widths, repeatRows=1)
    atbl.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a3a6b')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 7.5),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8fafc')]),
        ('GRID', (0, 0), (-1, -1), 0.25, colors.lightgrey),
        ('LEFTPADDING', (0, 0), (-1, -1), 3),
        ('RIGHTPADDING', (0, 0), (-1, -1), 3),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
    ]))
    elements.append(atbl)

    def _footer(canvas, doc_):
        canvas.saveState()
        canvas.setFont('Helvetica', 7)
        canvas.setFillColor(colors.grey)
        canvas.drawRightString(
            doc_.pagesize[0] - 15*mm, 8*mm,
            f'Page {doc_.page} — Generated {_tz.localtime(_tz.now()).strftime("%Y-%m-%d %H:%M")} — Administrative use only',
        )
        canvas.restoreState()

    doc.build(elements, onFirstPage=_footer, onLaterPages=_footer)
    pdf_bytes = buf.getvalue()
    buf.close()

    filename = f'fansc-history-{beneficiary.beneficiary_id}.pdf'
    AuditLog.log(
        action=AuditLog.ACTION_REPORT_EXPORT,
        user=request.user,
        details={'report': 'beneficiary_history', 'format': 'pdf',
                 'beneficiary_id': beneficiary.beneficiary_id},
        request=request,
    )
    resp = HttpResponse(pdf_bytes, content_type='application/pdf')
    resp['Content-Disposition'] = f'attachment; filename="{filename}"'
    return resp


# ─── Shared-Representative Review (added 2026-05-28) ──────────────────────────
#
# A representative whose face matches an existing representative for a
# different beneficiary is flagged for admin review instead of being silently
# rejected. The admin reviews the case here and decides whether the shared
# linkage is legitimate.

@login_required
def shared_rep_review_list(request):
    """Queue of representatives flagged as shared (same face across beneficiaries)."""
    from django.contrib import messages
    if not request.user.is_admin:
        messages.error(request, 'Admin access required.')
        return redirect('beneficiaries:dashboard')

    from beneficiaries.models import SharedRepresentativeReview
    status_filter = request.GET.get('status', 'pending_review').strip()
    qs = (
        SharedRepresentativeReview.objects
        .select_related(
            'representative', 'representative__beneficiary',
            'flagged_by', 'decided_by',
        )
        .order_by('-flagged_at')
    )
    if status_filter and status_filter != 'all':
        qs = qs.filter(status=status_filter)
    return render(request, 'verification/shared_rep_review_list.html', {
        'reviews': qs,
        'status_filter': status_filter,
        'status_choices': SharedRepresentativeReview.STATUS_CHOICES,
    })


@login_required
@require_http_methods(['GET', 'POST'])
def shared_rep_review_detail(request, review_id):
    """Admin reviews and decides on a single shared-representative case."""
    from django.contrib import messages
    from beneficiaries.models import (
        SharedRepresentativeReview, Representative, Beneficiary,
    )

    if not request.user.is_admin:
        messages.error(request, 'Admin access required.')
        return redirect('beneficiaries:dashboard')

    review = get_object_or_404(
        SharedRepresentativeReview.objects.select_related(
            'representative', 'representative__beneficiary',
            'flagged_by', 'decided_by',
        ),
        pk=review_id,
    )
    rep = review.representative

    # Other beneficiaries this representative-of-record is linked to (same face).
    # Best-effort lookup: the matched_beneficiary_id is captured at flag time.
    matched_beneficiary = None
    try:
        matched_beneficiary = Beneficiary.objects.get(
            beneficiary_id=review.matched_beneficiary_id,
        )
    except Beneficiary.DoesNotExist:
        pass

    # Claim history for this representative (across any beneficiary).
    rep_claims = ClaimRecord.objects.filter(representative=rep).select_related(
        'beneficiary', 'stipend_event'
    ).order_by('-claimed_at')[:50]

    if request.method == 'POST':
        if review.status != SharedRepresentativeReview.STATUS_PENDING:
            messages.warning(request, 'This case is already decided.')
            return redirect('verification:shared_rep_review_detail', review_id=review.id)

        action = request.POST.get('action', '').strip()
        notes = (request.POST.get('decision_notes', '') or '').strip()
        if not notes or len(notes) < 5:
            messages.error(request, 'A decision note (min 5 chars) is required.')
            return redirect('verification:shared_rep_review_detail', review_id=review.id)

        # Optional uploaded authorization document
        upload = request.FILES.get('authorization_document')
        if upload:
            review.authorization_document = upload

        action_to_status = {
            'approve':       SharedRepresentativeReview.STATUS_APPROVED,
            'reject':        SharedRepresentativeReview.STATUS_REJECTED,
            'docs_required': SharedRepresentativeReview.STATUS_DOCS_REQUIRED,
            'block':         SharedRepresentativeReview.STATUS_BLOCKED,
        }
        if action not in action_to_status:
            messages.error(request, 'Unknown action.')
            return redirect('verification:shared_rep_review_detail', review_id=review.id)

        new_status = action_to_status[action]
        review.status = new_status
        review.decision_notes = notes
        review.decided_by = request.user
        review.decided_at = timezone.now()
        review.save()

        # Mirror the status on the Representative record so verify_submit's
        # gate sees the latest decision without an extra join.
        rep.shared_review_status = {
            SharedRepresentativeReview.STATUS_APPROVED:      Representative.SHARED_APPROVED,
            SharedRepresentativeReview.STATUS_REJECTED:      Representative.SHARED_REJECTED,
            SharedRepresentativeReview.STATUS_DOCS_REQUIRED: Representative.SHARED_DOCS_REQUIRED,
            SharedRepresentativeReview.STATUS_BLOCKED:       Representative.SHARED_BLOCKED,
        }[new_status]
        rep.save(update_fields=['shared_review_status'])

        # Reject also forces the representative inactive to prevent any future use.
        if action == 'reject' or action == 'block':
            rep.is_active = False
            rep.save(update_fields=['is_active'])

        audit_action = {
            'approve':       AuditLog.ACTION_SHARED_REP_APPROVED,
            'reject':        AuditLog.ACTION_SHARED_REP_REJECTED,
            'docs_required': AuditLog.ACTION_SHARED_REP_DOCS_REQUIRED,
            'block':         AuditLog.ACTION_SHARED_REP_BLOCKED,
        }[action]
        AuditLog.log(
            action=audit_action,
            user=request.user,
            target_type='SharedRepresentativeReview',
            target_id=review.id,
            details={
                'representative_id': str(rep.pk),
                'representative_name': rep.full_name,
                'beneficiary_id': rep.beneficiary.beneficiary_id,
                'matched_beneficiary_id': review.matched_beneficiary_id,
                'matched_score': review.matched_score,
                'decided_by': request.user.username,
                'notes': notes,
                'authorization_document_uploaded': bool(upload),
            },
            request=request,
        )
        from logs.notifications import resolve_notification
        resolve_notification(f'shared_rep_flagged:{review.pk}')

        messages.success(
            request,
            f'Shared-representative review for {rep.full_name} → {new_status.replace("_", " ").title()}.',
        )
        return redirect('verification:shared_rep_review_list')

    return render(request, 'verification/shared_rep_review_detail.html', {
        'review': review,
        'rep': rep,
        'beneficiary': rep.beneficiary,
        'matched_beneficiary': matched_beneficiary,
        'rep_claims': rep_claims,
    })


# ══════════════════════════════════════════════════════════════════════════
# BPA-2 — Controlled Biometric Evaluation Trial Workflow
# ══════════════════════════════════════════════════════════════════════════
# Populates the BPA-1 EvaluationDataset/EvaluationTrial data foundation by
# reusing the SAME biometric primitives as verify_submit (face_utils.
# compare_with_all_embeddings/compare_with_stored, liveness.check_anti_spoofing,
# pad.PresentationAttackDetector, face_utils.decide_base_outcome) — never the
# production views themselves, and NEVER VerificationAttempt/ClaimRecord.
# This is what makes the workflow architecturally incapable of creating a
# payout, and keeps it invisible to live Analytics (verification/analytics.py
# and template_analytics.py only ever query VerificationAttempt).
#
# Access: Technical Administrator (IT) full read+write, President read-only,
# Admin/Staff no access — see _evaluation_admin_required's own docstring
# below for the exact matrix. See docs/BIOMETRIC-EVALUATION-METHODOLOGY.md
# for the full access-matrix rationale and the exact timing-metric definition.
#
# Scope note: this checkpoint's capture UI is a simplified single/multi-frame
# still capture (no interactive JS head-turn overlay). It reuses the exact
# same server-side anti-spoof (check_anti_spoofing) and PAD
# (PresentationAttackDetector) functions production's frontal-frame gate
# uses, on whatever frame(s) are submitted, but does not replicate
# production's live movement-challenge UI. See methodology doc §"Capture".

def _evaluation_admin_required(request, write=True):
    """
    Access matrix for Biometric Evaluation (owner-policy correction —
    FINAL PRE-EXE COMPLETION checkpoint, section 3):
      Technical Administrator (role=IT) — full access (read + write).
      President                         — read-only (list/detail/analytics
                                            and other non-mutating views).
      Admin / Staff                     — no access at all.

    This supersedes the prior pass's rationale for keeping President at
    full read+write "because the BPA test suite was built with President as
    the admin-tier actor" — tests validate product policy, they do not
    define it. Every view that mutates evaluation state (create/start/
    finalize/archive a dataset, set up/run/abort/withdraw a trial) must
    call this with the default `write=True` so President is denied;
    read-only views (list, detail, analytics, template health) must pass
    `write=False` so President keeps oversight visibility.
    """
    from django.contrib import messages
    user = request.user
    if user.is_admin_it:
        return None
    if user.is_president and not write:
        return None
    if user.is_president and write:
        messages.error(
            request,
            'President has read-only access to Biometric Evaluation. This action is restricted to the Technical Administrator.'
        )
        return redirect('verification:evaluation_dataset_list')
    messages.error(
        request,
        'Biometric Evaluation is restricted to the Technical Administrator (read + write) and President (read-only).'
    )
    return redirect('beneficiaries:dashboard')


def _dataset_purpose_choices_display():
    """(value, label) pairs using EvaluationDataset's clearer user-facing
    purpose labels (section 21) instead of the raw PURPOSE_CHOICES text."""
    return [
        (value, EvaluationDataset._PURPOSE_DISPLAY_OVERRIDE.get(value, label))
        for value, label in EvaluationDataset.PURPOSE_CHOICES
    ]


def _evaluation_trial_duration_ms(trial, now):
    """Server-authoritative CONTROLLED VERIFICATION ELAPSED TIME (see methodology
    doc): evaluation_started_at -> now, in whole milliseconds, never negative."""
    if not trial.evaluation_started_at:
        return None
    delta_ms = (now - trial.evaluation_started_at).total_seconds() * 1000
    return max(0, int(round(delta_ms)))


def _abort_trial(trial, user, reason, request=None):
    """Technical failure / abandonment — the trial never reached a decision.
    Distinct from a legitimate COMPLETED+DENIED security decision (see
    EvaluationTrial.TRIAL_STATUS_ABORTED docstring). Never fabricates a
    duration or a system_decision.

    Data minimization (BPA-5.1 — see docs/BIOMETRIC-EVALUATION-METHODOLOGY.md
    "Liveness-proof retention"): ABORTED is a terminal state exactly like
    COMPLETED — an aborted trial can never legitimately resume (evaluation_
    trial_run/evaluation_trial_liveness both require trial_status ==
    PENDING), so any encrypted liveness-proof embedding captured before the
    abort is no longer needed and must be cleared here, mirroring
    _complete_trial's identical clearing. Covers every abort call site,
    including the ones that fire AFTER stage-1 liveness already stored a
    proof (e.g. a stage-2 capture/comparison failure) and the explicit
    operator-initiated abort of a PENDING trial that already has a proof."""
    trial.trial_status = EvaluationTrial.TRIAL_STATUS_ABORTED
    trial.system_decision = None
    trial.similarity_score = None
    trial.verification_duration_ms = None
    trial.evaluated_at = timezone.now()
    trial.notes = (trial.notes + f'\n[ABORTED] {reason}').strip()
    trial.liveness_proof_embedding = None
    trial.full_clean()
    trial.save()
    AuditLog.log(
        action=AuditLog.ACTION_EVALUATION_TRIAL_ABORTED,
        user=user,
        target_type='EvaluationTrial',
        target_id=trial.id,
        details={'dataset_id': str(trial.dataset_id), 'reason': reason},
        request=request,
    )


def _complete_trial(trial, user, *, decision, score, threshold, auto_verify_threshold,
                     liveness_passed=None, liveness_score=None,
                     anti_spoof_passed=None, anti_spoof_score=None, pa_score=None,
                     liveness_pathway='', challenge_direction='', head_movement_completed=False,
                     matcher_base_decision=None, quality_override_applied=False,
                     lookalike_escalation_applied=False, representative_fallback_blocked=False,
                     request=None):
    """Records a real system decision (VERIFIED/MANUAL_REVIEW/NOT_VERIFIED/
    DENIED) — a legitimate, fully-reached evaluation result. Ground truth on
    `trial` is untouched here; it was fixed at trial setup, before this
    result was known (see EvaluationTrialSetupTest / GroundTruthIndependenceTest).

    `decision` is always the FINAL FANS-C system decision (post quality-override/
    lookalike-escalation/representative-fallback-block, matching what
    VerificationAttempt.decision would actually be). `matcher_base_decision`
    preserves the raw decide_base_outcome() result before those overrides —
    see BPA-2.1 methodology doc, "Decision levels"."""
    now = timezone.now()
    trial.system_decision = decision
    trial.similarity_score = score
    trial.review_threshold_snapshot = threshold
    trial.auto_verify_threshold_snapshot = auto_verify_threshold
    trial.liveness_passed = liveness_passed
    trial.liveness_score = liveness_score
    trial.anti_spoof_passed = anti_spoof_passed
    trial.anti_spoof_score = anti_spoof_score
    trial.pa_score = pa_score
    trial.liveness_pathway = liveness_pathway
    trial.challenge_direction = challenge_direction
    trial.head_movement_completed = head_movement_completed
    trial.matcher_base_decision = matcher_base_decision
    trial.quality_override_applied = quality_override_applied
    trial.lookalike_escalation_applied = lookalike_escalation_applied
    trial.representative_fallback_blocked = representative_fallback_blocked
    trial.trial_status = EvaluationTrial.TRIAL_STATUS_COMPLETED
    trial.evaluated_at = now
    trial.verification_duration_ms = _evaluation_trial_duration_ms(trial, now)
    # ── Data minimization (BPA-5 §25) ─────────────────────────────────────
    # The encrypted liveness-proof embedding (BPA-2.1) has already done its
    # only two jobs by this point — identity comparison and the same-face
    # check both run BEFORE _complete_trial() is called — and no analytics
    # function (biometric_analytics.py) ever reads it. Retaining a raw
    # biometric template after it is no longer needed for the decision is
    # unnecessary exposure, so it is cleared here once the trial reaches a
    # final, persisted outcome. Harmless no-op on paths where it was never
    # set (e.g. a pre-liveness technical DENIED).
    trial.liveness_proof_embedding = None
    trial.full_clean()
    trial.save()
    AuditLog.log(
        action=AuditLog.ACTION_EVALUATION_TRIAL_COMPLETED,
        user=user,
        target_type='EvaluationTrial',
        target_id=trial.id,
        details={
            'dataset_id': str(trial.dataset_id),
            'identity_ground_truth': trial.identity_ground_truth,
            'system_decision': decision,
            'matcher_base_decision': matcher_base_decision,
            'similarity_score': score,
            'liveness_pathway': liveness_pathway,
        },
        request=request,
    )


# ── Dataset workflow ─────────────────────────────────────────────────────

@login_required
def evaluation_dataset_list(request):
    denied = _evaluation_admin_required(request, write=False)
    if denied:
        return denied
    datasets = EvaluationDataset.objects.all().order_by('-created_at')
    return render(request, 'verification/evaluation_dataset_list.html', {'datasets': datasets})


@login_required
def evaluation_dataset_create(request):
    denied = _evaluation_admin_required(request)
    if denied:
        return denied
    from django.contrib import messages
    if request.method == 'POST':
        name = (request.POST.get('name') or '').strip()
        protocol_version = (request.POST.get('protocol_version') or '').strip()
        description = (request.POST.get('description') or '').strip()
        purpose = (request.POST.get('purpose') or '').strip()
        errors = []
        if not name or not protocol_version:
            errors.append('Name and protocol version are required.')
        if purpose not in dict(EvaluationDataset.PURPOSE_CHOICES):
            errors.append('A valid dataset purpose (Research Study / Pilot-Calibration / QA-Synthetic) is required.')
        if errors:
            for e in errors:
                messages.error(request, e)
            return render(request, 'verification/evaluation_dataset_form.html', {
                'name': name, 'protocol_version': protocol_version, 'description': description,
                'purpose': purpose, 'purpose_choices': _dataset_purpose_choices_display(),
            })
        dataset = EvaluationDataset.objects.create(
            name=name,
            protocol_version=protocol_version,
            description=description,
            purpose=purpose,
            status=EvaluationDataset.STATUS_DRAFT,
            created_by=request.user,
        )
        AuditLog.log(
            action=AuditLog.ACTION_EVALUATION_DATASET_CREATED,
            user=request.user,
            target_type='EvaluationDataset',
            target_id=dataset.id,
            details={'name': dataset.name, 'protocol_version': dataset.protocol_version, 'purpose': dataset.purpose},
            request=request,
        )
        messages.success(request, f'Evaluation dataset "{dataset.name}" created (DRAFT).')
        return redirect('verification:evaluation_dataset_detail', pk=dataset.pk)
    return render(request, 'verification/evaluation_dataset_form.html', {'purpose_choices': _dataset_purpose_choices_display()})


@login_required
def evaluation_dataset_detail(request, pk):
    denied = _evaluation_admin_required(request, write=False)
    if denied:
        return denied
    dataset = get_object_or_404(EvaluationDataset, pk=pk)
    trials = dataset.trials.all().order_by('-created_at')
    pending_count = trials.filter(trial_status=EvaluationTrial.TRIAL_STATUS_PENDING).count()
    completed_count = trials.filter(trial_status=EvaluationTrial.TRIAL_STATUS_COMPLETED).count()
    aborted_count = trials.filter(trial_status=EvaluationTrial.TRIAL_STATUS_ABORTED).count()
    withdrawn_count = trials.filter(withdrawn=True).count()
    from . import biometric_analytics as ba
    stale_pending_proof_count = ba.get_stale_pending_trials_with_proof(dataset).count()
    return render(request, 'verification/evaluation_dataset_detail.html', {
        'dataset': dataset,
        'trials': trials,
        'pending_count': pending_count,
        'completed_count': completed_count,
        'aborted_count': aborted_count,
        'withdrawn_count': withdrawn_count,
        'quality_report': ba.get_dataset_quality_report(dataset),
        'readiness': ba.get_study_readiness(dataset),
        'stale_pending_proof_count': stale_pending_proof_count,
        'stale_pending_proof_hours': ba.STALE_PENDING_PROOF_HOURS,
    })


@login_required
@require_POST
def evaluation_dataset_start(request, pk):
    """DRAFT -> COLLECTING. Trials may only be created once collecting."""
    denied = _evaluation_admin_required(request)
    if denied:
        return denied
    from django.contrib import messages
    dataset = get_object_or_404(EvaluationDataset, pk=pk)
    if dataset.status != EvaluationDataset.STATUS_DRAFT:
        messages.error(request, f'Cannot start collection — dataset is {dataset.get_status_display()}, not Draft.')
        return redirect('verification:evaluation_dataset_detail', pk=dataset.pk)
    dataset.status = EvaluationDataset.STATUS_COLLECTING
    dataset.started_at = dataset.started_at or timezone.now()
    dataset.full_clean()
    dataset.save()
    AuditLog.log(
        action=AuditLog.ACTION_EVALUATION_DATASET_STARTED,
        user=request.user,
        target_type='EvaluationDataset',
        target_id=dataset.id,
        request=request,
    )
    messages.success(request, f'Dataset "{dataset.name}" is now COLLECTING.')
    return redirect('verification:evaluation_dataset_detail', pk=dataset.pk)


@login_required
@require_POST
def evaluation_dataset_finalize(request, pk):
    """
    COLLECTING -> COMPLETED. Blocks obvious incomplete-state mistakes (BPA-2
    §7): no trials at all, or any trial still PENDING. Does NOT require any
    sample-size/statistical target — that is a research/adviser decision.
    """
    denied = _evaluation_admin_required(request)
    if denied:
        return denied
    from django.contrib import messages
    dataset = get_object_or_404(EvaluationDataset, pk=pk)
    if dataset.status != EvaluationDataset.STATUS_COLLECTING:
        messages.error(request, f'Cannot finalize — dataset is {dataset.get_status_display()}, not Collecting.')
        return redirect('verification:evaluation_dataset_detail', pk=dataset.pk)
    trial_count = dataset.trials.count()
    # Excludes withdrawn (BPA-5) — a participant who withdrew before their
    # trial ever ran is not "still pending" from a workflow-completeness
    # standpoint; it is already excluded from analysis (EvaluationTrial.
    # withdrawn) and must not block finalization forever.
    pending_count = dataset.trials.filter(trial_status=EvaluationTrial.TRIAL_STATUS_PENDING, withdrawn=False).count()
    if trial_count == 0:
        messages.error(request, 'Cannot finalize — this dataset has no trials yet.')
        return redirect('verification:evaluation_dataset_detail', pk=dataset.pk)
    if pending_count > 0:
        messages.error(
            request,
            f'Cannot finalize — {pending_count} trial(s) are still PENDING (not run or aborted). '
            'Run or abort every pending trial first.',
        )
        return redirect('verification:evaluation_dataset_detail', pk=dataset.pk)
    dataset.status = EvaluationDataset.STATUS_COMPLETED
    dataset.completed_at = timezone.now()
    dataset.full_clean()
    dataset.save()
    AuditLog.log(
        action=AuditLog.ACTION_EVALUATION_DATASET_FINALIZED,
        user=request.user,
        target_type='EvaluationDataset',
        target_id=dataset.id,
        details={'trial_count': trial_count},
        request=request,
    )
    messages.success(request, f'Dataset "{dataset.name}" finalized (COMPLETED).')
    return redirect('verification:evaluation_dataset_detail', pk=dataset.pk)


@login_required
@require_POST
def evaluation_dataset_archive(request, pk):
    """COMPLETED -> ARCHIVED. Read-only through the normal workflow afterward."""
    denied = _evaluation_admin_required(request)
    if denied:
        return denied
    from django.contrib import messages
    dataset = get_object_or_404(EvaluationDataset, pk=pk)
    if dataset.status != EvaluationDataset.STATUS_COMPLETED:
        messages.error(request, f'Cannot archive — dataset is {dataset.get_status_display()}, not Completed.')
        return redirect('verification:evaluation_dataset_detail', pk=dataset.pk)
    dataset.status = EvaluationDataset.STATUS_ARCHIVED
    dataset.full_clean()
    dataset.save()
    messages.success(request, f'Dataset "{dataset.name}" archived.')
    return redirect('verification:evaluation_dataset_detail', pk=dataset.pk)


# ── Trial setup (ground truth recorded BEFORE any biometric processing) ───

@login_required
def evaluation_trial_setup(request, dataset_pk):
    denied = _evaluation_admin_required(request)
    if denied:
        return denied
    from django.contrib import messages
    from django.db.models import Q
    dataset = get_object_or_404(EvaluationDataset, pk=dataset_pk)
    if not dataset.can_accept_trials:
        messages.error(request, f'Dataset is {dataset.get_status_display()} — trials may only be added while Collecting.')
        return redirect('verification:evaluation_dataset_detail', pk=dataset.pk)

    # Target type selector (BPA-2.1 §12/§15): the researcher first picks
    # WHICH kind of enrolled identity this trial targets, then searches
    # within that type. The search UI may show real names/IDs (restricted,
    # admin-tier only — needed so the researcher can pick the correct
    # record); EvaluationTrial itself still only stores the pseudonymous
    # target_identity_code plus the restricted FK, never the name/ID as an
    # analytical field. See BIOMETRIC-EVALUATION-METHODOLOGY.md "Target
    # identity UI privacy".
    target_type = (request.GET.get('target_type') or request.POST.get('target_type') or 'beneficiary').strip()
    if target_type not in ('beneficiary', 'representative'):
        target_type = 'beneficiary'
    query = (request.GET.get('q') or request.POST.get('q') or '').strip()
    target_candidates = []
    if query and target_type == 'beneficiary':
        target_candidates = list(
            Beneficiary.objects.filter(
                Q(last_name__icontains=query) |
                Q(first_name__icontains=query) |
                Q(beneficiary_id__icontains=query) |
                Q(senior_citizen_id__icontains=query),
                status=Beneficiary.STATUS_ACTIVE,
            ).distinct().order_by('last_name', 'first_name')[:20]
        )
    elif query and target_type == 'representative':
        target_candidates = list(
            Representative.objects.filter(
                Q(last_name__icontains=query) |
                Q(first_name__icontains=query) |
                Q(beneficiary__last_name__icontains=query) |
                Q(beneficiary__first_name__icontains=query) |
                Q(beneficiary__beneficiary_id__icontains=query),
                is_active=True,
            ).select_related('beneficiary').distinct().order_by('last_name', 'first_name')[:20]
        )

    if request.method == 'POST' and request.POST.get('action') == 'create_trial':
        participant_code = (request.POST.get('participant_code') or '').strip()
        target_identity_code = (request.POST.get('target_identity_code') or '').strip()
        identity_ground_truth = request.POST.get('identity_ground_truth') or ''
        presentation_ground_truth = request.POST.get('presentation_ground_truth') or EvaluationTrial.PRESENTATION_NOT_TESTED
        notes = (request.POST.get('notes') or '').strip()
        target_beneficiary_id = request.POST.get('target_beneficiary_id') or None
        target_representative_id = request.POST.get('target_representative_id') or None

        errors = []
        if not participant_code:
            errors.append('Participant code is required.')
        if not target_identity_code:
            errors.append('Target identity code is required.')
        if identity_ground_truth not in dict(EvaluationTrial.IDENTITY_GROUND_TRUTH_CHOICES):
            errors.append('A valid identity ground truth (Genuine/Impostor) is required.')
        target_beneficiary = None
        target_representative = None
        if target_beneficiary_id and target_representative_id:
            errors.append('Select a beneficiary OR a representative target, not both.')
        elif target_beneficiary_id:
            target_beneficiary = Beneficiary.objects.filter(pk=target_beneficiary_id).first()
            if not target_beneficiary:
                errors.append('Selected target beneficiary could not be found.')
        elif target_representative_id:
            target_representative = Representative.objects.filter(pk=target_representative_id).first()
            if not target_representative:
                errors.append('Selected target representative could not be found.')

        if errors:
            for e in errors:
                messages.error(request, e)
        else:
            trial = EvaluationTrial(
                dataset=dataset,
                participant_code=participant_code,
                target_identity_code=target_identity_code,
                identity_ground_truth=identity_ground_truth,
                presentation_ground_truth=presentation_ground_truth,
                target_beneficiary=target_beneficiary,
                target_representative=target_representative,
                notes=notes,
                created_by=request.user,
            )
            trial.full_clean()
            trial.save()
            AuditLog.log(
                action=AuditLog.ACTION_EVALUATION_TRIAL_CREATED,
                user=request.user,
                target_type='EvaluationTrial',
                target_id=trial.id,
                details={
                    'dataset_id': str(dataset.id),
                    'identity_ground_truth': identity_ground_truth,
                    'presentation_ground_truth': presentation_ground_truth,
                    'target_kind': 'representative' if target_representative else 'beneficiary',
                },
                request=request,
            )
            messages.success(request, 'Controlled trial created. Ground truth is locked — run the trial to capture the system result.')
            return redirect('verification:evaluation_trial_detail', pk=trial.pk)

    return render(request, 'verification/evaluation_trial_setup.html', {
        'dataset': dataset,
        'query': query,
        'target_type': target_type,
        'target_candidates': target_candidates,
        'identity_choices': EvaluationTrial.IDENTITY_GROUND_TRUTH_CHOICES,
        'presentation_choices': EvaluationTrial.PRESENTATION_GROUND_TRUTH_CHOICES,
    })


@login_required
def evaluation_trial_detail(request, pk):
    denied = _evaluation_admin_required(request, write=False)
    if denied:
        return denied
    trial = get_object_or_404(
        EvaluationTrial.objects.select_related('dataset', 'target_beneficiary', 'target_representative'),
        pk=pk,
    )
    return render(request, 'verification/evaluation_trial_detail.html', {'trial': trial})


@login_required
@require_POST
def evaluation_trial_abort(request, pk):
    """Researcher-initiated abandonment of a PENDING trial (e.g. participant
    withdrew, technical setup failed before capture). Never fabricates a
    decision or duration."""
    denied = _evaluation_admin_required(request)
    if denied:
        return denied
    from django.contrib import messages
    trial = get_object_or_404(EvaluationTrial, pk=pk)
    if trial.trial_status != EvaluationTrial.TRIAL_STATUS_PENDING:
        messages.error(request, 'Only a PENDING trial can be aborted.')
        return redirect('verification:evaluation_trial_detail', pk=trial.pk)
    _abort_trial(trial, request.user, reason='Manually aborted by operator.', request=request)
    messages.success(request, 'Trial aborted.')
    return redirect('verification:evaluation_dataset_detail', pk=trial.dataset_id)


@login_required
@require_POST
def evaluation_trial_withdraw(request, pk):
    """Participant withdrawal (BPA-5). Marks a trial `withdrawn=True` —
    NEVER deletes the row. The metric engine (biometric_analytics.py,
    `_active_trials`) excludes withdrawn=True trials from every rate/count it
    computes; the row itself, and its audit trail, are retained for research
    integrity. Never touches operational Beneficiary/Representative/
    FaceEmbedding/VerificationAttempt/ClaimRecord data — this only changes
    the EvaluationTrial row (and, when the dataset was already finalized,
    the dataset's amendment marker below).

    Restricted to the Technical Administrator, same as every other
    mutating action in this workflow (`_evaluation_admin_required`,
    default `write=True`). Previously restricted to President-only under
    the theory that withdrawal is a destructive-to-analysis action distinct
    from routine collection; the owner-approved authorization model
    (FINAL PRE-EXE COMPLETION checkpoint, section 3) makes President
    strictly read-only for all evaluation mutation, withdrawal included —
    President may inspect the resulting withdrawn/amended state but must
    not perform it.

    Finalized-dataset amendment (BPA-5.1): withdrawal is intentionally
    ALLOWED regardless of dataset.status — a participant's right to withdraw
    must never be blocked merely to protect an already-finalized statistic
    (see docs/BIOMETRIC-EVALUATION-METHODOLOGY.md "Post-finalization
    withdrawal"). What changes is disclosure: if the dataset was already
    finalized (dataset.completed_at is set — true for COMPLETED and, since
    there is no unarchive path, for ARCHIVED too), this marks
    EvaluationDataset.amended_after_finalization_at so every future viewer
    of that dataset's "finalized" results sees they were amended, without
    rewriting completed_at (the ORIGINAL finalization date stays
    reconstructable) or reopening the dataset for new trials.

    Data minimization: also clears any encrypted liveness-proof embedding
    still on the trial (a withdrawn trial can never legitimately resume —
    evaluation_trial_run/evaluation_trial_liveness both refuse a withdrawn
    trial, see below) — mirrors _abort_trial/_complete_trial."""
    denied = _evaluation_admin_required(request)
    if denied:
        return denied
    from django.contrib import messages
    trial = get_object_or_404(EvaluationTrial.objects.select_related('dataset'), pk=pk)
    if trial.withdrawn:
        messages.info(request, 'This trial is already withdrawn.')
        return redirect('verification:evaluation_trial_detail', pk=trial.pk)
    reason = (request.POST.get('withdrawal_reason') or '').strip()
    if not reason:
        messages.error(request, 'A withdrawal reason is required.')
        return redirect('verification:evaluation_trial_detail', pk=trial.pk)
    now = timezone.now()
    dataset = trial.dataset
    dataset_was_finalized = dataset.completed_at is not None
    trial.withdrawn = True
    trial.withdrawal_reason = reason
    trial.withdrawn_at = now
    trial.withdrawn_by = request.user
    trial.liveness_proof_embedding = None
    trial.full_clean()
    trial.save()
    AuditLog.log(
        action=AuditLog.ACTION_EVALUATION_TRIAL_WITHDRAWN,
        user=request.user,
        target_type='EvaluationTrial',
        target_id=trial.id,
        details={
            'dataset_id': str(dataset.id),
            'dataset_status_at_withdrawal': dataset.status,
            'dataset_was_finalized_at_withdrawal': dataset_was_finalized,
            'dataset_completed_at': dataset.completed_at.isoformat() if dataset.completed_at else None,
        },
        request=request,
    )
    if dataset_was_finalized:
        dataset.amended_after_finalization_at = now
        dataset.full_clean()
        dataset.save()
        AuditLog.log(
            action=AuditLog.ACTION_EVALUATION_DATASET_AMENDED,
            user=request.user,
            target_type='EvaluationDataset',
            target_id=dataset.id,
            details={
                'reason': 'post_finalization_trial_withdrawal',
                'withdrawn_trial_id': str(trial.id),
                'original_completed_at': dataset.completed_at.isoformat(),
                'amended_at': now.isoformat(),
            },
            request=request,
        )
        messages.warning(
            request,
            'Trial withdrawn. This dataset was already FINALIZED — it is now marked '
            'AMENDED AFTER PARTICIPANT WITHDRAWAL and every reported metric reflects the '
            'withdrawal. The original finalization date is preserved and unchanged.',
        )
    else:
        messages.success(request, 'Trial withdrawn — excluded from all biometric metric calculations. Row retained for audit.')
    return redirect('verification:evaluation_trial_detail', pk=trial.pk)


# ── Controlled facial verification runner (BPA-2.1 — two-stage) ────────────
# Stage 1 (evaluation_trial_liveness): processes the neutral/liveness frame.
# Mirrors production's TX-issuance gate exactly: anti-spoof (check_anti_spoofing
# on the frontal frame) + PAD, NOT gated on challenge completion (matching
# verify_submit's server_liveness_passed override once a TX exists — see
# BIOMETRIC-EVALUATION-METHODOLOGY.md "Liveness policy"). On success, stores
# an encrypted liveness-proof embedding on the trial (never a raw image) and
# leaves the trial PENDING. On failure, completes the trial as a real DENIED
# decision (pre-comparison — similarity_score stays NULL).
#
# Stage 2 (evaluation_trial_run POST): only accepted once a liveness proof
# exists. Same-face-checks the final frame against the proof embedding
# (cosine_similarity >= SAME_FACE_SEQUENCE_THRESHOLD), then compares the
# PROOF embedding (never the final frame's own embedding) against the target
# — exactly mirroring verify_submit's use of liveness_tx.embedding_data.
# Applies the same shared post-score policy as production (quality override,
# lookalike escalation, representative-fallback-block) so system_decision is
# the FINAL FANS-C decision, not just the raw matcher output.

def _random_challenge_direction():
    try:
        return get_random_challenge()
    except Exception:
        return 'side'


@login_required
def evaluation_trial_run(request, pk):
    denied = _evaluation_admin_required(request)
    if denied:
        return denied
    from django.contrib import messages
    trial = get_object_or_404(
        EvaluationTrial.objects.select_related('dataset', 'target_beneficiary', 'target_representative'),
        pk=pk,
    )
    if trial.trial_status != EvaluationTrial.TRIAL_STATUS_PENDING:
        messages.info(request, 'This trial has already been run or aborted.')
        return redirect('verification:evaluation_trial_detail', pk=trial.pk)
    if trial.withdrawn:
        messages.error(request, 'This trial has been withdrawn by the participant and cannot be run.')
        return redirect('verification:evaluation_trial_detail', pk=trial.pk)
    if not trial.dataset.can_accept_trials:
        messages.error(request, 'Dataset is not currently Collecting — cannot run trials.')
        return redirect('verification:evaluation_dataset_detail', pk=trial.dataset_id)

    if request.method == 'GET':
        # Authorize the start exactly once — a page refresh must not reset
        # the server-authoritative clock or reissue a new session token.
        # The SAME token binds both stage 1 (liveness) and stage 2 (submit)
        # to this specific trial.
        if trial.evaluation_session_token is None:
            trial.evaluation_session_token = uuid.uuid4()
            trial.evaluation_started_at = timezone.now()
            trial.save(update_fields=['evaluation_session_token', 'evaluation_started_at'])
        stage = 'submit' if trial.liveness_proof_embedding else 'liveness'
        context = {'trial': trial, 'stage': stage}
        if stage == 'liveness':
            context['challenge_direction'] = _random_challenge_direction()
        return render(request, 'verification/evaluation_trial_run.html', context)

    # ── POST: stage 2 — final frame submission ──────────────────────────────
    if not trial.liveness_proof_embedding:
        messages.error(request, 'Complete the liveness step first.')
        return redirect('verification:evaluation_trial_run', pk=trial.pk)

    submitted_token = request.POST.get('session_token', '')
    if not trial.evaluation_session_token or str(trial.evaluation_session_token) != submitted_token:
        messages.error(
            request,
            'Evaluation session token mismatch or missing — reload the runner page and try again. '
            'This guards against a submission accidentally attaching to a different trial.',
        )
        return redirect('verification:evaluation_trial_run', pk=trial.pk)

    image_data_uri = request.POST.get('image', '')
    if not image_data_uri:
        _abort_trial(trial, request.user, reason='No final-frame image was captured.', request=request)
        messages.error(request, 'No image captured — trial aborted (technical failure, not a system decision).')
        return redirect('verification:evaluation_trial_detail', pk=trial.pk)

    try:
        raw = image_data_uri.split(',')[-1]
        image_bytes = base64.b64decode(raw)
    except Exception:
        _abort_trial(trial, request.user, reason='Malformed final-frame image data submitted.', request=request)
        messages.error(request, 'Malformed image — trial aborted.')
        return redirect('verification:evaluation_trial_detail', pk=trial.pk)

    threshold = trial.review_threshold_snapshot
    auto_verify_threshold = trial.auto_verify_threshold_snapshot

    try:
        img = load_image_from_bytes(image_bytes)
        final_face_img = detect_and_align_face(img)
        final_embedding = get_embedding(final_face_img)
    except ValueError as e:
        _abort_trial(trial, request.user, reason=f'Final-frame face detection failed: {e}', request=request)
        messages.error(request, f'Face detection failed — trial aborted: {e}')
        return redirect('verification:evaluation_trial_detail', pk=trial.pk)
    except Exception as e:
        logger.exception('[EVAL_TRIAL] unexpected final-frame processing error')
        _abort_trial(trial, request.user, reason=f'Unexpected processing error: {e}', request=request)
        messages.error(request, 'Unexpected processing error — trial aborted.')
        return redirect('verification:evaluation_trial_detail', pk=trial.pk)

    proof_embedding = decrypt_embedding(bytes(trial.liveness_proof_embedding))

    # ── Same-face binding (BPA-2.1 §7) — mirrors verify_submit's final-frame
    # integrity gate: the submitted frame must be the SAME person as the
    # liveness proof, or this is denied before any target comparison.
    same_face_threshold = getattr(django_settings, 'SAME_FACE_SEQUENCE_THRESHOLD', 0.30)
    same_face_score = cosine_similarity(proof_embedding, final_embedding)
    if same_face_score < same_face_threshold:
        _complete_trial(
            trial, request.user,
            decision=VerificationAttempt.DECISION_DENIED,
            score=None,
            threshold=threshold, auto_verify_threshold=auto_verify_threshold,
            liveness_passed=trial.liveness_passed, liveness_score=trial.liveness_score,
            anti_spoof_passed=trial.anti_spoof_passed, anti_spoof_score=trial.anti_spoof_score,
            pa_score=trial.pa_score, liveness_pathway=trial.liveness_pathway,
            challenge_direction=trial.challenge_direction,
            head_movement_completed=trial.head_movement_completed,
            request=request,
        )
        messages.error(request, 'Denied: final frame does not match the liveness-proof frame (subject changed).')
        return redirect('verification:evaluation_trial_detail', pk=trial.pk)

    # ── Identity comparison — uses the PROOF embedding, never the final
    # frame's own embedding, exactly like verify_submit uses liveness_tx.
    # embedding_data. Never falls back to the beneficiary's face for a
    # representative-target trial (BPA-2.1 §13) — see
    # check_representative_beneficiary_fallback.
    representative_fallback_blocked = False
    comparison = None
    if trial.target_representative_id:
        rep = trial.target_representative
        if not hasattr(rep, 'face_embedding'):
            _abort_trial(
                trial, request.user,
                reason='Target representative has no enrolled face data — no fallback to the beneficiary face is permitted.',
                request=request,
            )
            messages.error(request, 'Trial aborted — target representative has no enrolled face data.')
            return redirect('verification:evaluation_trial_detail', pk=trial.pk)
        comparison = compare_with_stored(proof_embedding, rep.face_embedding.embedding_data)
        if comparison.get('success'):
            fallback_probe = check_representative_beneficiary_fallback(proof_embedding, rep.beneficiary, threshold)
            representative_fallback_blocked = fallback_probe['blocked']
    elif trial.target_beneficiary_id:
        comparison = compare_with_all_embeddings(proof_embedding, trial.target_beneficiary)

    if not comparison or not comparison.get('success'):
        _abort_trial(
            trial, request.user,
            reason='No resolvable target identity with a stored template (target_beneficiary/target_representative not set or has no enrolled face data).',
            request=request,
        )
        messages.error(request, 'Trial aborted — target identity has no comparable enrolled face data.')
        return redirect('verification:evaluation_trial_detail', pk=trial.pk)

    score = float(comparison['score'])
    matcher_base_decision = decide_base_outcome(score, threshold, auto_verify_threshold)
    decision = matcher_base_decision
    quality_override_applied = False  # see face_utils.apply_quality_override — dormant on this
    # proof-bound path by deliberate parity with production's TX-bound path.
    lookalike_escalation_applied = False

    if representative_fallback_blocked:
        decision = VerificationAttempt.DECISION_DENIED
    elif decision == VerificationAttempt.DECISION_VERIFIED:
        lookalike_band = getattr(django_settings, 'LOOKALIKE_BAND', 0.05)
        exclude_id = str(
            rep.beneficiary.beneficiary_id if trial.target_representative_id else trial.target_beneficiary.beneficiary_id
        )
        lookalike_result = check_lookalike_escalation(
            proof_embedding, score, threshold, exclude_beneficiary_id=exclude_id, lookalike_band=lookalike_band,
        )
        if lookalike_result['escalate']:
            decision = VerificationAttempt.DECISION_MANUAL_REVIEW
            lookalike_escalation_applied = True

    _complete_trial(
        trial, request.user,
        decision=decision,
        score=score,
        threshold=threshold, auto_verify_threshold=auto_verify_threshold,
        liveness_passed=trial.liveness_passed, liveness_score=trial.liveness_score,
        anti_spoof_passed=trial.anti_spoof_passed, anti_spoof_score=trial.anti_spoof_score,
        pa_score=trial.pa_score, liveness_pathway=trial.liveness_pathway,
        challenge_direction=trial.challenge_direction,
        head_movement_completed=trial.head_movement_completed,
        matcher_base_decision=matcher_base_decision,
        quality_override_applied=quality_override_applied,
        lookalike_escalation_applied=lookalike_escalation_applied,
        representative_fallback_blocked=representative_fallback_blocked,
        request=request,
    )
    return redirect('verification:evaluation_trial_detail', pk=trial.pk)


@login_required
@require_POST
def evaluation_trial_liveness(request, pk):
    """Stage 1 of the controlled trial runner — see module comment above."""
    denied = _evaluation_admin_required(request)
    if denied:
        return denied
    from django.contrib import messages
    trial = get_object_or_404(EvaluationTrial.objects.select_related('dataset'), pk=pk)
    if trial.trial_status != EvaluationTrial.TRIAL_STATUS_PENDING:
        messages.info(request, 'This trial has already been run or aborted.')
        return redirect('verification:evaluation_trial_detail', pk=trial.pk)
    if trial.withdrawn:
        messages.error(request, 'This trial has been withdrawn by the participant and cannot be run.')
        return redirect('verification:evaluation_trial_detail', pk=trial.pk)
    if trial.liveness_proof_embedding:
        messages.info(request, 'Liveness already captured for this trial — proceed to the final frame.')
        return redirect('verification:evaluation_trial_run', pk=trial.pk)

    submitted_token = request.POST.get('session_token', '')
    if not trial.evaluation_session_token or str(trial.evaluation_session_token) != submitted_token:
        messages.error(request, 'Evaluation session token mismatch or missing — reload the runner page and try again.')
        return redirect('verification:evaluation_trial_run', pk=trial.pk)

    head_movement_completed = request.POST.get('head_movement_completed') in ('1', 'true', 'on', 'True')
    challenge_direction = (request.POST.get('challenge_direction') or '').strip()

    image_data_uri = request.POST.get('neutral_image', '')
    if not image_data_uri:
        _abort_trial(trial, request.user, reason='No liveness/neutral image was captured.', request=request)
        messages.error(request, 'No liveness image captured — trial aborted.')
        return redirect('verification:evaluation_trial_detail', pk=trial.pk)

    try:
        image_bytes = base64.b64decode(image_data_uri.split(',')[-1])
        neutral_img = load_image_from_bytes(image_bytes)
        neutral_face = detect_and_align_face(neutral_img)
    except ValueError as e:
        _abort_trial(trial, request.user, reason=f'Liveness face detection failed: {e}', request=request)
        messages.error(request, f'Face detection failed — trial aborted: {e}')
        return redirect('verification:evaluation_trial_detail', pk=trial.pk)
    except Exception as e:
        logger.exception('[EVAL_TRIAL] unexpected liveness processing error')
        _abort_trial(trial, request.user, reason=f'Unexpected processing error: {e}', request=request)
        messages.error(request, 'Unexpected processing error — trial aborted.')
        return redirect('verification:evaluation_trial_detail', pk=trial.pk)

    # Anti-spoof — same core function (check_anti_spoofing) production uses
    # for its authoritative frontal-frame gate.
    anti_spoof_threshold = getattr(django_settings, 'ANTI_SPOOF_THRESHOLD', 0.25)
    anti_spoof_result = check_anti_spoofing(neutral_face, threshold=anti_spoof_threshold)
    anti_spoof_passed = bool(anti_spoof_result['passed'])
    anti_spoof_score = float(anti_spoof_result['score'])

    # Optional PAD sequence frames (burst capture) — landmark_motion_ok is set
    # from the researcher's attestation that real head movement was performed
    # (a stand-in for production's MediaPipe-confirmed motion signal; BPA-2.1
    # does not implement automated landmark tracking — documented limitation).
    sequence_b64 = [s for s in request.POST.getlist('sequence_frames') if s]
    seq_imgs = []
    for s in sequence_b64:
        try:
            s_bytes = base64.b64decode(s.split(',')[-1])
            seq_imgs.append(detect_and_align_face(load_image_from_bytes(s_bytes)))
        except Exception:
            continue  # skip unusable burst frames, mirrors production's tolerant handling

    pad_threshold = getattr(django_settings, 'PHONE_SCREEN_SPOOF_THRESHOLD', 0.40)
    pad_required = getattr(django_settings, 'PAD_REQUIRED', True)
    pa_action = getattr(django_settings, 'PRESENTATION_ATTACK_REVIEW_OR_DENY', 'deny')
    detector = PresentationAttackDetector(threshold=pad_threshold)
    if len(seq_imgs) >= 3:
        pad_result = detector.analyze_sequence(seq_imgs, landmark_motion_ok=head_movement_completed)
    else:
        pad_result = detector.analyze(neutral_face)
    pa_score = float(pad_result.score)
    pad_ok = (pa_score < pad_threshold) or (not pad_required) or (pa_action != 'deny')

    # Mirrors verify_submit's TX-issuance gate exactly: anti-spoof + PAD only.
    # challenge_completed does NOT gate this in production once a TX exists
    # (server_liveness_passed is overridden to True by _tx_anti_spoof_passed
    # and _tx_pad_ok, regardless of challenge_completed) — see
    # BIOMETRIC-EVALUATION-METHODOLOGY.md "Liveness policy" for the trace.
    liveness_passed = anti_spoof_passed and pad_ok

    # liveness_score uses the SAME formula/function production stores for
    # display (run_full_liveness_check) — informational only, not a gate.
    combined = run_full_liveness_check(neutral_face, head_movement_completed, anti_spoof_threshold)
    liveness_score = combined['liveness_score']

    if liveness_passed:
        pathway = (
            EvaluationTrial.LIVENESS_PATHWAY_ACTIVE_CHALLENGE if head_movement_completed
            else EvaluationTrial.LIVENESS_PATHWAY_PASSIVE_ONLY
        )
    else:
        pathway = (
            EvaluationTrial.LIVENESS_PATHWAY_FAILED_ACTIVE if head_movement_completed
            else EvaluationTrial.LIVENESS_PATHWAY_FAILED_PASSIVE
        )

    if not liveness_passed:
        threshold = SystemConfig.get_threshold()
        auto_verify_threshold = SystemConfig.get_auto_verify_threshold()
        _complete_trial(
            trial, request.user,
            decision=VerificationAttempt.DECISION_DENIED,
            score=None,
            threshold=threshold, auto_verify_threshold=auto_verify_threshold,
            liveness_passed=liveness_passed, liveness_score=liveness_score,
            anti_spoof_passed=anti_spoof_passed, anti_spoof_score=anti_spoof_score,
            pa_score=pa_score, liveness_pathway=pathway,
            challenge_direction=challenge_direction, head_movement_completed=head_movement_completed,
            request=request,
        )
        messages.error(request, 'Denied: liveness check failed. See trial detail for the reason.')
        return redirect('verification:evaluation_trial_detail', pk=trial.pk)

    try:
        proof_embedding = get_embedding(neutral_face)
    except Exception as e:
        logger.exception('[EVAL_TRIAL] liveness embedding failed')
        _abort_trial(trial, request.user, reason=f'Liveness embedding generation failed: {e}', request=request)
        messages.error(request, 'Embedding generation failed — trial aborted.')
        return redirect('verification:evaluation_trial_detail', pk=trial.pk)

    trial.liveness_proof_embedding = encrypt_embedding(proof_embedding)
    trial.liveness_captured_at = timezone.now()
    trial.liveness_passed = liveness_passed
    trial.liveness_score = liveness_score
    trial.anti_spoof_passed = anti_spoof_passed
    trial.anti_spoof_score = anti_spoof_score
    trial.pa_score = pa_score
    trial.liveness_pathway = pathway
    trial.challenge_direction = challenge_direction
    trial.head_movement_completed = head_movement_completed
    # Threshold snapshot (BPA-1 §8) is taken here, at the point liveness
    # passes and the trial is committed to proceeding — reproducible even
    # if SystemConfig changes before stage 2's final submission.
    trial.review_threshold_snapshot = SystemConfig.get_threshold()
    trial.auto_verify_threshold_snapshot = SystemConfig.get_auto_verify_threshold()
    trial.full_clean()
    trial.save()
    messages.success(request, 'Liveness passed — capture the final frame to complete the trial.')
    return redirect('verification:evaluation_trial_run', pk=trial.pk)
