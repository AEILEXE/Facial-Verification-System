// register.js liveness build: 2026-05-24 risk-based-challenge
/**
 * Registration face capture with risk-based liveness check — FANS-C.
 *
 * Registration Flow
 * ─────────────────
 * 1. Camera starts → face quality preview begins.
 * 2. Capture button → anti-spoof check (server call to check_liveness).
 * 3. Decide whether head-movement challenge is required:
 *      • anti-spoof score < 0.30  (borderline/suspicious → challenge required)
 *      • face quality poor        (blurry/dark → challenge required)
 *      • neither of the above     → fast path, no challenge, proceed to capture
 * 4. If challenge required: head-movement challenge with progress overlay.
 * 5. Capture best-quality frame after liveness passes.
 * 6. Submit → backend independently validates liveness before saving embedding.
 *
 * Phone/screen protection:
 *   • Low texture score (< 0.25) → anti-spoof fails → registration blocked at step 2.
 *   • Borderline score (< 0.30)  → challenge required → static image cannot move → blocked at step 4.
 *   • Strong score (≥ 0.30) + good quality → fast path accepted by both frontend and backend.
 *
 * Challenge trigger threshold: 0.30 — matches backend LIVENESS_CHALLENGE_TRIGGER_THRESHOLD.
 */
const REG_CHALLENGE_TRIGGER_THRESHOLD = 0.30;

document.addEventListener('DOMContentLoaded', async () => {
  const video             = document.getElementById('video');
  const captureCanvas     = document.getElementById('captureCanvas');
  const captureBtn        = document.getElementById('captureBtn');
  const retakeBtn         = document.getElementById('retakeBtn');
  const submitBtn         = document.getElementById('submitBtn');
  const statusMsg         = document.getElementById('statusMsg');
  const capturedPreview   = document.getElementById('capturedPreview');
  const previewImg        = document.getElementById('previewImg');
  const processingSpinner = document.getElementById('processingSpinner');
  const qualityHint       = document.getElementById('qualityHint');
  const challengeBox      = document.getElementById('regChallengeBox');
  const challengeText     = document.getElementById('regChallengeText');
  const challengeTimer    = document.getElementById('regChallengeTimer');
  const challengeProgress = document.getElementById('regChallengeProgress');
  const livenessStatus    = document.getElementById('regLivenessStatus');

  let capturedImageData = null;
  let livenessData = {
    passed: false,
    anti_spoof_score: 0,
    challenge_completed: false,
  };

  function setStatus(html, type) {
    statusMsg.className = `alert alert-${type} mb-3`;
    statusMsg.innerHTML = html;
  }

  function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
  function pct(v) { return Math.round((v || 0) * 100); }

  // ── Start camera ─────────────────────────────────────────────────────────────
  setStatus('<i class="bi bi-hourglass-split me-1"></i> Starting camera…', 'info');

  const camResult = await startCamera(video);
  if (!camResult.success) {
    setStatus(`<i class="bi bi-camera-video-off me-1"></i> Camera error: ${camResult.error}`, 'danger');
    if (captureBtn) captureBtn.disabled = true;
    return;
  }

  await new Promise((resolve) => {
    video.addEventListener('play', resolve, { once: true });
    setTimeout(resolve, 1500);
  });

  setStatus('<i class="bi bi-check-circle me-1"></i> Camera ready. Position the beneficiary\'s face in the oval and click Capture.', 'success');
  if (captureBtn) captureBtn.disabled = false;

  // ── MediaPipe head-tracking init ─────────────────────────────────────────────
  let mpAvailable = false;
  if (typeof LivenessTracker !== 'undefined') {
    mpAvailable = await LivenessTracker.init(video, () => {});
    console.log('FANS-C register: LivenessTracker.init =>', mpAvailable);
  }

  // ── Capture button ────────────────────────────────────────────────────────────
  captureBtn.addEventListener('click', async () => {
    captureBtn.disabled = true;
    capturedImageData = null;
    livenessData = { passed: false, anti_spoof_score: 0, challenge_completed: false };

    // Defensive check — every face capture template must define CHECK_LIVENESS_URL.
    if (typeof CHECK_LIVENESS_URL === 'undefined' || !CHECK_LIVENESS_URL) {
      setStatus('<i class="bi bi-x-circle me-1"></i> Liveness configuration error. Please contact administrator.', 'danger');
      captureBtn.disabled = false;
      return;
    }

    // ── Step 1: capture frame and run anti-spoof check ───────────────────────
    setStatus('<i class="bi bi-hourglass-split me-1"></i> Checking face…', 'info');

    const frameData = captureFrame(video, captureCanvas);
    let livenessApiResult;
    try {
      livenessApiResult = await postJSON(CHECK_LIVENESS_URL, {
        image: frameData,
        challenge_completed: false,
      }, CSRF_TOKEN);
    } catch (err) {
      setStatus(`<i class="bi bi-x-circle me-1"></i> Cannot reach server: ${err.message}`, 'danger');
      captureBtn.disabled = false;
      return;
    }

    if (!livenessApiResult.success) {
      setStatus(`<i class="bi bi-x-circle me-1"></i> Anti-spoof check failed: ${livenessApiResult.error || 'Unknown error.'}`, 'danger');
      captureBtn.disabled = false;
      return;
    }

    if (!livenessApiResult.face_detected) {
      setStatus('<i class="bi bi-exclamation-triangle me-1"></i> No face detected. Center the face in the oval and try again.', 'warning');
      captureBtn.disabled = false;
      return;
    }

    livenessData.anti_spoof_score = livenessApiResult.anti_spoof_score || 0;
    const antiSpoofPassed = livenessApiResult.anti_spoof_passed;

    if (!antiSpoofPassed) {
      setStatus(
        `<i class="bi bi-shield-x me-1"></i> <strong>Registration blocked:</strong> Anti-spoof score too low (${pct(livenessData.anti_spoof_score)}%). ` +
        'A phone screen or printed photo was detected. Please use a live person.', 'danger');
      captureBtn.disabled = false;
      return;
    }

    // ── Step 2: decide whether head-movement challenge is required ───────────
    // Challenge is only required when the capture is suspicious or borderline.
    // Strong anti-spoof scores with good quality go straight to capture.
    const antiSpoofSuspicious = livenessData.anti_spoof_score < REG_CHALLENGE_TRIGGER_THRESHOLD;
    const qualityPoor = livenessApiResult.face_quality_ok === false;
    const needsChallenge = antiSpoofSuspicious || qualityPoor;

    console.log(
      'FANS-C register: anti_spoof_score=' + livenessData.anti_spoof_score.toFixed(3) +
      ' suspicious=' + antiSpoofSuspicious +
      ' quality_ok=' + livenessApiResult.face_quality_ok +
      ' qualityPoor=' + qualityPoor +
      ' needsChallenge=' + needsChallenge +
      ' mpAvailable=' + mpAvailable
    );

    if (needsChallenge) {
      // ── Challenge required path ──────────────────────────────────────────
      const challengeDir     = REG_CHALLENGE;
      const challengeDisplay = REG_CHALLENGE_DISPLAY;

      let challengeReason = '';
      if (antiSpoofSuspicious) {
        challengeReason = `Anti-spoof score borderline (${pct(livenessData.anti_spoof_score)}%) — please complete the movement check.`;
      } else if (qualityPoor) {
        challengeReason = `Image quality concern detected — please complete the movement check.`;
      }

      if (challengeReason) {
        setStatus(`<i class="bi bi-exclamation-triangle me-1"></i> ${challengeReason}`, 'warning');
      } else {
        setStatus('<i class="bi bi-person-bounding-box me-1"></i> Starting liveness challenge…', 'info');
      }

      // If MediaPipe unavailable and challenge is required, block immediately.
      // A suspicious/borderline capture cannot be accepted without movement proof.
      if (!mpAvailable) {
        setStatus(
          '<i class="bi bi-shield-x me-1"></i> <strong>Additional liveness check required</strong>, but head tracking is unavailable. ' +
          'Please use a supported browser (Chrome/Edge), ensure camera permissions are granted, improve lighting, and retry.',
          'danger'
        );
        captureBtn.disabled = false;
        return;
      }

      // Landmark warmup: wait up to 2.5 s for FaceMesh to produce stable results.
      if (mpAvailable && !LivenessTracker.hasLandmarks()) {
        setStatus('<i class="bi bi-hourglass-split me-1"></i> Preparing head tracking… keep face fully visible.', 'info');
        const _warmupEnd = Date.now() + 2500;
        while (!LivenessTracker.hasLandmarks() && Date.now() < _warmupEnd) {
          await sleep(100);
        }
        if (!LivenessTracker.hasLandmarks()) {
          console.warn('FANS-C register: No landmarks after warmup — baseline may be inaccurate.');
          setStatus('<i class="bi bi-exclamation-triangle me-1"></i> Head tracking initializing — keep full face inside the oval.', 'warning');
          await sleep(500);
        }
      }
      // Let pose history accumulate a few stable frames before recording baseline.
      if (mpAvailable && LivenessTracker.hasLandmarks()) {
        await sleep(300);
      }
      if (mpAvailable) {
        LivenessTracker.setBaseline();
        if (LivenessTracker.getDebugInfo) {
          console.log('FANS-C register: baseline set | challenge=' + challengeDir,
            LivenessTracker.getDebugInfo(challengeDir));
        }
      }

      if (challengeBox) challengeBox.style.display = '';
      if (challengeText) challengeText.textContent = challengeDisplay;

      if (challengeTimer) {
        challengeTimer.style.width = '100%';
        challengeTimer.style.transition = 'none';
        await sleep(20);
        challengeTimer.style.transition = 'width 7s linear';
        challengeTimer.style.width = '0%';
      }

      let challengeCompleted = false;
      let _lastDebugLogMs = 0;
      await new Promise((resolve) => {
        let elapsed = 0;
        const iv = setInterval(() => {
          elapsed += 200;

          // Real-time movement progress overlay
          if (mpAvailable && challengeProgress && LivenessTracker.getDebugInfo) {
            const dbg = LivenessTracker.getDebugInfo(challengeDir);
            if (dbg.hasLandmarks) {
              const p = dbg.progress;
              const delta = Math.abs(dbg.movementDelta).toFixed(1);
              challengeProgress.textContent = 'Tracking: ' + delta + '° / ' + dbg.threshold + '° needed (' + p + '%)';
              challengeProgress.style.color = p >= 100 ? '#15803d' : p >= 50 ? '#a16207' : '#0c4a6e';
            } else {
              challengeProgress.textContent = 'Tracking: face not detected — keep full face in frame';
              challengeProgress.style.color = '#9d174d';
            }
          }

          // Debug log every ~1.5 s
          if (mpAvailable && Date.now() - _lastDebugLogMs > 1500 && LivenessTracker.getDebugInfo) {
            _lastDebugLogMs = Date.now();
            const dbg = LivenessTracker.getDebugInfo(challengeDir);
            console.log(
              'FANS-C reg challenge ' + elapsed + 'ms dir=' + challengeDir +
              ' landmarks=' + dbg.hasLandmarks +
              (dbg.hasLandmarks
                ? ' yawΔ=' + dbg.yawDelta.toFixed(2) + ' pitchΔ=' + dbg.pitchDelta.toFixed(2) + ' progress=' + dbg.progress + '%'
                : '')
            );
          }

          if (mpAvailable && LivenessTracker.checkChallenge(challengeDir)) {
            challengeCompleted = true;
            console.log('FANS-C register: Challenge PASSED at ' + elapsed + 'ms direction=' + challengeDir);
            clearInterval(iv);
            resolve();
            return;
          }

          if (elapsed >= 7000) {
            // Timeout: challenge not completed. Never auto-accept when challenge is required
            // (suspicious/borderline score) — require real movement.
            challengeCompleted = false;
            console.log('FANS-C register: Challenge TIMEOUT at 7000ms — not completed. needsChallenge=' + needsChallenge);
            clearInterval(iv);
            resolve();
          }
        }, 200);
      });

      if (challengeBox) challengeBox.style.display = 'none';
      if (challengeProgress) challengeProgress.textContent = '';

      if (!challengeCompleted) {
        setStatus(
          '<i class="bi bi-shield-x me-1"></i> <strong>Liveness check failed.</strong> ' +
          'No head movement detected within 7 seconds. Tips: center your face, improve lighting, ' +
          'keep the full face in the oval, then move your head slowly and clearly in the indicated direction.',
          'danger'
        );
        captureBtn.disabled = false;
        // Reset challenge state for next attempt
        if (mpAvailable && LivenessTracker.reset) LivenessTracker.reset();
        return;
      }

      livenessData.challenge_completed = true;
      livenessData.passed = true;

      setStatus('<i class="bi bi-shield-check me-1"></i> Movement detected. Capturing face…', 'success');

    } else {
      // ── Fast path: strong anti-spoof, no challenge needed ────────────────
      // Anti-spoof score is clearly passing. No head-movement challenge required.
      // challenge_completed=true tells the backend this was a clean, trusted capture.
      livenessData.challenge_completed = true;
      livenessData.passed = true;

      setStatus(
        `<i class="bi bi-shield-check me-1"></i> Live face detected (score: ${pct(livenessData.anti_spoof_score)}%). No movement check needed. Capturing…`,
        'success'
      );
      console.log('FANS-C register: fast path — anti-spoof strong, no challenge required.');
    }

    // ── Step 3: capture best-quality frame after liveness passes ────────────
    capturedImageData = await captureFrameHighQuality(video, captureCanvas, 7, 70);

    previewImg.src = capturedImageData;
    capturedPreview.style.display = '';
    captureBtn.style.display = 'none';
    retakeBtn.style.display = '';
    submitBtn.style.display = '';
    if (qualityHint) qualityHint.style.display = '';

    if (livenessStatus) {
      const modeLabel = needsChallenge
        ? 'Anti-spoof and head movement verified.'
        : `Anti-spoof passed (${pct(livenessData.anti_spoof_score)}%) — no movement check needed.`;
      livenessStatus.innerHTML = `<i class="bi bi-shield-check text-success me-1"></i> <strong class="text-success">Liveness passed.</strong> ${modeLabel}`;
      livenessStatus.style.display = '';
    }

    setStatus('<i class="bi bi-eye me-1"></i> Check the preview. If the face is clear and centered, click Submit Registration.', 'info');
  });

  // ── Retake button ─────────────────────────────────────────────────────────────
  retakeBtn.addEventListener('click', () => {
    capturedImageData = null;
    livenessData = { passed: false, anti_spoof_score: 0, challenge_completed: false };
    capturedPreview.style.display = 'none';
    captureBtn.style.display = '';
    captureBtn.disabled = false;
    retakeBtn.style.display = 'none';
    submitBtn.style.display = 'none';
    if (qualityHint) qualityHint.style.display = 'none';
    if (livenessStatus) livenessStatus.style.display = 'none';
    if (challengeBox) challengeBox.style.display = 'none';
    if (challengeProgress) challengeProgress.textContent = '';
    if (mpAvailable && LivenessTracker.reset) LivenessTracker.reset();
    setStatus('<i class="bi bi-camera me-1"></i> Ready. Position the face and click Capture.', 'success');
  });

  // Track duplicate name+DOB override state across retries
  let dupOverrideConfirmed = false;
  let dupOverrideReason = '';
  let dupOverrideInfo = '';

  async function doSubmit() {
    submitBtn.disabled = true;
    retakeBtn.disabled = true;
    if (processingSpinner) processingSpinner.style.display = '';
    setStatus('<i class="bi bi-hourglass-split me-1"></i> Processing face embedding… Please wait.', 'info');

    try {
      const result = await postJSON(SUBMIT_URL, {
        image: capturedImageData,
        liveness_passed: livenessData.passed,
        challenge_completed: livenessData.challenge_completed,
        anti_spoof_score: livenessData.anti_spoof_score,
        duplicate_override_confirmed: dupOverrideConfirmed,
        duplicate_override_reason: dupOverrideReason,
        duplicate_distinguishing_info: dupOverrideInfo,
      }, CSRF_TOKEN);

      if (result.success) {
        stopCamera();
        setStatus(`<i class="bi bi-check-circle-fill me-1"></i> ${result.message}`, 'success');
        if (processingSpinner) processingSpinner.style.display = 'none';
        setTimeout(() => { window.location.href = result.redirect; }, 1500);
        return;
      }

      // Special-case: duplicate name+DOB detected — show review modal.
      if (result.duplicate_namedob_detected) {
        if (processingSpinner) processingSpinner.style.display = 'none';
        submitBtn.disabled = false;
        retakeBtn.disabled = false;
        showDuplicateReviewModal(result);
        return;
      }

      setStatus(`<i class="bi bi-x-circle me-1"></i> ${result.error}`, 'danger');
      if (processingSpinner) processingSpinner.style.display = 'none';
      submitBtn.disabled = false;
      retakeBtn.disabled = false;
    } catch (err) {
      setStatus(`<i class="bi bi-x-circle me-1"></i> Network error: ${err.message}`, 'danger');
      if (processingSpinner) processingSpinner.style.display = 'none';
      submitBtn.disabled = false;
      retakeBtn.disabled = false;
    }
  }

  function showDuplicateReviewModal(result) {
    const ex = result.existing || {};
    let modal = document.getElementById('dupNameDobModal');
    if (modal) modal.remove();
    const html = `
      <div class="modal fade" id="dupNameDobModal" tabindex="-1" aria-hidden="true">
        <div class="modal-dialog modal-lg modal-dialog-centered">
          <div class="modal-content">
            <div class="modal-header bg-warning text-dark">
              <h5 class="modal-title">
                <i class="bi bi-exclamation-triangle-fill me-2"></i>
                Possible Duplicate Beneficiary Detected
              </h5>
              <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
            </div>
            <div class="modal-body">
              <p class="mb-2">
                A beneficiary with the <strong>same name and date of birth</strong> is already registered.
              </p>
              <div class="card mb-3">
                <div class="card-body p-3">
                  <div><strong>${escapeHtml(ex.full_name || '')}</strong></div>
                  <div class="small text-muted font-monospace">${escapeHtml(ex.beneficiary_id || '')}</div>
                  <div class="small">DOB: ${escapeHtml(ex.date_of_birth || '')}</div>
                  <div class="small">Address: ${escapeHtml(ex.address || '—')}</div>
                  <div class="small">Status: <span class="badge bg-secondary">${escapeHtml(ex.status || '')}</span></div>
                  <div class="mt-2">
                    <a href="${ex.detail_url}" target="_blank" rel="noopener" class="btn btn-sm btn-outline-primary">
                      <i class="bi bi-box-arrow-up-right me-1"></i> View Existing Beneficiary
                    </a>
                  </div>
                </div>
              </div>
              <div class="alert alert-info small">
                <i class="bi bi-info-circle me-1"></i>
                If this is the <strong>same person</strong>, cancel and use the existing record.
                If this is a <strong>different person</strong> who happens to share the same name and birthdate,
                submit a Different-Person override request with a written reason for President/Admin review.
              </div>
              <div class="mb-2">
                <label class="form-label small fw-semibold mb-1">Written reason <span class="text-danger">*</span></label>
                <textarea id="dupOverrideReasonInput" class="form-control" rows="3"
                          minlength="10" required
                          placeholder="Explain why this is a different person (minimum 10 characters)."></textarea>
              </div>
              <div class="mb-0">
                <label class="form-label small fw-semibold mb-1">Distinguishing info (optional)</label>
                <textarea id="dupOverrideInfoInput" class="form-control" rows="2"
                          placeholder="Different address, contact number, valid ID number, etc."></textarea>
              </div>
            </div>
            <div class="modal-footer">
              <button type="button" class="btn btn-outline-secondary" data-bs-dismiss="modal">
                <i class="bi bi-x me-1"></i> Cancel Registration
              </button>
              <button type="button" class="btn btn-warning" id="dupOverrideConfirmBtn">
                <i class="bi bi-shield-exclamation me-1"></i> Submit Different-Person Override Request
              </button>
            </div>
          </div>
        </div>
      </div>`;
    document.body.insertAdjacentHTML('beforeend', html);
    modal = document.getElementById('dupNameDobModal');
    const bsModal = bootstrap.Modal.getOrCreateInstance(modal);
    bsModal.show();

    document.getElementById('dupOverrideConfirmBtn').addEventListener('click', () => {
      const r = (document.getElementById('dupOverrideReasonInput').value || '').trim();
      const d = (document.getElementById('dupOverrideInfoInput').value || '').trim();
      if (r.length < 10) {
        alert('A written reason of at least 10 characters is required.');
        return;
      }
      dupOverrideConfirmed = true;
      dupOverrideReason = r;
      dupOverrideInfo = d;
      bsModal.hide();
      doSubmit();
    });
  }

  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  // ── Submit button ─────────────────────────────────────────────────────────────
  submitBtn.addEventListener('click', async () => {
    if (!capturedImageData) return;
    if (!livenessData.passed) {
      setStatus('<i class="bi bi-shield-x me-1"></i> Liveness check did not pass. Please retake.', 'danger');
      return;
    }
    await doSubmit();
  });
});
