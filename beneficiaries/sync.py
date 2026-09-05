"""
Offline-Fallback Sync Service for FANS-C.

Architecture overview
---------------------
FANS-C is designed primarily for centralized LAN deployment where all
workstations write directly to a shared PostgreSQL database.  This module
supports the fallback scenario: barangay workstations that occasionally
operate offline (no LAN/internet access) and need to push locally-captured
registrations to the central server when connectivity is later restored.

Sync state machine
------------------
Every Beneficiary has a ``sync_status`` field that progresses through these states:

    pending_sync  → synced        (HTTP 200/201 — central server accepted)
    pending_sync  → sync_conflict (HTTP 409    — server has conflicting data)
    pending_sync  → sync_rejected (HTTP 4xx    — server rejected the payload)
    sync_conflict → pending_sync  (admin resets to retry after reviewing)
    sync_rejected → pending_sync  (admin resets to retry after reviewing)

``sync_error`` stores the human-readable reason for the last failure/conflict.
It is cleared on successful sync.  ``sync_attempted_at`` records when the last
attempt (any outcome) was made.  ``offline_device`` records the hostname of
the workstation that originally created the record offline.

Configuration (.env)
--------------------
SYNC_API_URL    — Base URL of the central API, e.g. https://central.fans-c.gov.ph/api
                  Leave empty to disable sync (centralized deployment mode).
SYNC_API_KEY    — Bearer token / API key for the central server.
SYNC_TIMEOUT    — HTTP request timeout in seconds (default 30).
SYNC_BATCH_SIZE — Max records per sync run (default 50).

Key sharing
-----------
EMBEDDING_ENCRYPTION_KEY must be identical on both the offline device and the
central server.  The encrypted embedding bytes are transferred as-is; the
central server decrypts them with the same Fernet key.

Usage
-----
    from beneficiaries.sync import sync_all, is_online, mark_created

    # Call immediately after saving a new offline registration:
    mark_created(beneficiary)

    # Periodic sync (called by sync_beneficiaries management command):
    if is_online():
        result = sync_all()
        # result = {'synced': n, 'failed': n, 'conflicts': n, 'rejected': n}
"""
import logging
import socket
from datetime import datetime, timezone as dt_timezone

logger = logging.getLogger('beneficiaries.sync')

# ── Connectivity check ─────────────────────────────────────────────────────────


def is_online(host: str = '8.8.8.8', port: int = 53, timeout: float = 3.0) -> bool:
    """
    Return True if the machine can reach the internet.

    Uses a raw TCP connect to a well-known host (Google DNS by default).
    No DNS query is made, so this works even when DNS is broken.
    """
    try:
        socket.setdefaulttimeout(timeout)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect((host, port))
        return True
    except OSError:
        return False


# ── Registration hook ──────────────────────────────────────────────────────────

def mark_created(beneficiary) -> None:
    """
    Record that a beneficiary was registered on this offline device.

    Call this immediately after ``beneficiary.save()`` in any offline
    registration flow.  Sets ``sync_status='pending_sync'`` and stamps
    ``offline_device`` with the current hostname.

    This is a no-op if the record is somehow already in a terminal state
    (synced/conflict/rejected) — which should never happen for a brand-new
    registration but is guarded against defensively.
    """
    from .models import Beneficiary
    if beneficiary.sync_status != Beneficiary.SYNC_PENDING:
        return

    device = _device_id()
    beneficiary.offline_device = device
    beneficiary.sync_status = Beneficiary.SYNC_PENDING
    beneficiary.save(update_fields=['offline_device', 'sync_status'])
    logger.info(
        'SYNC MARK | beneficiary=%s device=%s',
        beneficiary.beneficiary_id,
        device,
    )


# ── Payload builder ────────────────────────────────────────────────────────────

def _build_payload(beneficiary) -> dict:
    """
    Serialise a Beneficiary record into a JSON-safe dict for the central API.

    Encrypted embedding data is base64-encoded so it survives JSON transport.
    """
    import base64

    payload = {
        'id': str(beneficiary.id),
        'beneficiary_id': beneficiary.beneficiary_id,
        'first_name': beneficiary.first_name,
        'middle_name': beneficiary.middle_name,
        'last_name': beneficiary.last_name,
        'date_of_birth': beneficiary.date_of_birth.isoformat(),
        'gender': beneficiary.gender,
        'address': beneficiary.address,
        'barangay': beneficiary.barangay,
        'municipality': beneficiary.municipality,
        'province': beneficiary.province,
        'contact_number': beneficiary.contact_number,
        'senior_citizen_id': beneficiary.senior_citizen_id,
        'valid_id_type': beneficiary.valid_id_type,
        'valid_id_number': beneficiary.valid_id_number,
        'status': beneficiary.status,
        'consent_given': beneficiary.consent_given,
        'has_representative': beneficiary.has_representative,
        'created_at': beneficiary.created_at.isoformat(),
        'updated_at': beneficiary.updated_at.isoformat(),
        'offline_device': beneficiary.offline_device,
        # Face embedding — bytes are base64-encoded for JSON transport
        'face_embedding': None,
    }

    try:
        embedding_obj = beneficiary.face_embedding
        raw_bytes = bytes(embedding_obj.embedding_data)
        payload['face_embedding'] = {
            'data_b64': base64.b64encode(raw_bytes).decode('ascii'),
            'version': embedding_obj.embedding_version,
        }
    except Exception:
        # No face data registered yet — that is fine
        pass

    return payload


# ── Core sync function ─────────────────────────────────────────────────────────

def sync_record(beneficiary, api_url: str, api_key: str, timeout: int = 30) -> str:
    """
    Send a single Beneficiary record to the central API.

    Returns the new sync_status string:
        'synced'        — accepted by server (HTTP 200/201)
        'sync_conflict' — server has conflicting data (HTTP 409)
        'sync_rejected' — server rejected the payload (HTTP 400/422)
        'pending_sync'  — transient failure (network error, 5xx); will retry next run

    Updates sync_status, last_synced_at, sync_error, and sync_attempted_at in-place.
    """
    import requests
    from .models import Beneficiary

    endpoint = api_url.rstrip('/') + '/beneficiaries/sync/'
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
        'X-FANS-Device': _device_id(),
    }

    now = datetime.now(dt_timezone.utc)

    try:
        payload = _build_payload(beneficiary)
        response = requests.post(endpoint, json=payload, headers=headers, timeout=timeout)

        # Stamp the attempt time regardless of outcome
        beneficiary.sync_attempted_at = now

        if response.status_code in (200, 201):
            # ── Accepted ──────────────────────────────────────────────────────
            beneficiary.sync_status = Beneficiary.SYNC_SYNCED
            beneficiary.last_synced_at = now
            beneficiary.sync_error = ''
            beneficiary.save(update_fields=[
                'sync_status', 'last_synced_at', 'sync_error', 'sync_attempted_at'
            ])
            logger.info(
                'SYNC OK | beneficiary=%s id=%s',
                beneficiary.beneficiary_id,
                beneficiary.id,
            )
            return Beneficiary.SYNC_SYNCED

        elif response.status_code == 409:
            # ── Conflict — server already has a different record for this ID ──
            try:
                reason = response.json().get('detail', response.text)[:500]
            except Exception:
                reason = response.text[:500]
            beneficiary.sync_status = Beneficiary.SYNC_CONFLICT
            beneficiary.sync_error = reason
            beneficiary.save(update_fields=[
                'sync_status', 'sync_error', 'sync_attempted_at'
            ])
            logger.warning(
                'SYNC CONFLICT | beneficiary=%s id=%s reason=%s',
                beneficiary.beneficiary_id,
                beneficiary.id,
                reason,
            )
            return Beneficiary.SYNC_CONFLICT

        elif response.status_code in (400, 422):
            # ── Rejected — payload is invalid; admin must review ──────────────
            try:
                reason = response.json().get('detail', response.text)[:500]
            except Exception:
                reason = response.text[:500]
            beneficiary.sync_status = Beneficiary.SYNC_REJECTED
            beneficiary.sync_error = reason
            beneficiary.save(update_fields=[
                'sync_status', 'sync_error', 'sync_attempted_at'
            ])
            logger.warning(
                'SYNC REJECTED | beneficiary=%s id=%s status=%d reason=%s',
                beneficiary.beneficiary_id,
                beneficiary.id,
                response.status_code,
                reason,
            )
            return Beneficiary.SYNC_REJECTED

        else:
            # ── Other HTTP error (5xx, etc.) — transient, will retry ──────────
            error_msg = f'HTTP {response.status_code}: {response.text[:200]}'
            beneficiary.sync_error = error_msg
            beneficiary.save(update_fields=['sync_error', 'sync_attempted_at'])
            logger.warning(
                'SYNC FAIL | beneficiary=%s id=%s error=%s',
                beneficiary.beneficiary_id,
                beneficiary.id,
                error_msg,
            )
            return Beneficiary.SYNC_PENDING

    except Exception as exc:
        # ── Network / timeout error — transient, will retry ───────────────────
        error_msg = str(exc)[:500]
        beneficiary.sync_attempted_at = now
        beneficiary.sync_error = error_msg
        beneficiary.save(update_fields=['sync_error', 'sync_attempted_at'])
        logger.warning(
            'SYNC FAIL | beneficiary=%s id=%s error=%s',
            beneficiary.beneficiary_id,
            beneficiary.id,
            error_msg,
        )
        return Beneficiary.SYNC_PENDING


def sync_all(batch_size: int = 50) -> dict:
    """
    Sync all pending_sync Beneficiary records to the central server.

    Only processes records with sync_status='pending_sync'.
    Records in sync_conflict or sync_rejected are skipped — they require
    admin review via the sync conflict dashboard before being retried.

    Returns a summary dict:
        {
            'synced':    int,   # successfully accepted by server
            'failed':    int,   # transient failures (will retry next run)
            'conflicts': int,   # newly entered sync_conflict state
            'rejected':  int,   # newly entered sync_rejected state
            'skipped':   int,   # skipped because SYNC_API_URL is not configured
        }
    """
    from django.conf import settings
    from .models import Beneficiary

    api_url = getattr(settings, 'SYNC_API_URL', '').strip()
    api_key = getattr(settings, 'SYNC_API_KEY', '').strip()
    timeout = int(getattr(settings, 'SYNC_TIMEOUT', 30))

    if not api_url:
        logger.debug('SYNC SKIP | SYNC_API_URL not configured — offline mode only')
        return {'synced': 0, 'failed': 0, 'conflicts': 0, 'rejected': 0, 'skipped': 1}

    pending = (
        Beneficiary.objects
        .filter(sync_status=Beneficiary.SYNC_PENDING)
        .order_by('created_at')[:batch_size]
    )
    count = pending.count()

    if count == 0:
        logger.debug('SYNC OK | No pending_sync records found')
        return {'synced': 0, 'failed': 0, 'conflicts': 0, 'rejected': 0, 'skipped': 0}

    logger.info('SYNC START | %d pending_sync record(s) to process', count)
    synced = failed = conflicts = rejected = 0

    for beneficiary in pending:
        outcome = sync_record(beneficiary, api_url, api_key, timeout)
        if outcome == Beneficiary.SYNC_SYNCED:
            synced += 1
        elif outcome == Beneficiary.SYNC_CONFLICT:
            conflicts += 1
        elif outcome == Beneficiary.SYNC_REJECTED:
            rejected += 1
        else:
            failed += 1

    logger.info(
        'SYNC DONE | synced=%d failed=%d conflicts=%d rejected=%d',
        synced, failed, conflicts, rejected,
    )
    return {
        'synced': synced,
        'failed': failed,
        'conflicts': conflicts,
        'rejected': rejected,
        'skipped': 0,
    }


# ── Helpers ────────────────────────────────────────────────────────────────────

def _device_id() -> str:
    """
    Return a stable device identifier (hostname) for the X-FANS-Device header
    and for the offline_device audit field.

    The central server can use this to identify which barangay workstation
    originally created a record.
    """
    try:
        return socket.gethostname()
    except Exception:
        return 'unknown'


def pending_count() -> int:
    """Return the number of records awaiting sync (sync_status='pending_sync')."""
    from .models import Beneficiary
    return Beneficiary.objects.filter(sync_status=Beneficiary.SYNC_PENDING).count()


def conflict_count() -> int:
    """Return the number of records in sync_conflict state (require admin review)."""
    from .models import Beneficiary
    return Beneficiary.objects.filter(sync_status=Beneficiary.SYNC_CONFLICT).count()


def rejected_count() -> int:
    """Return the number of records in sync_rejected state (require admin review)."""
    from .models import Beneficiary
    return Beneficiary.objects.filter(sync_status=Beneficiary.SYNC_REJECTED).count()
