// verify.js liveness build: 2026-05-27 v2.1.12 elapsed-scope-fix + stuck-processing-recovery
/**
 * Verification flow controller — FANS-C.
 *
 * Strict Gate Flow
 * ────────────────
 * The liveness challenge is now ALWAYS required for final verification.
 * Anti-spoofing (server texture check) and the active head-movement challenge
 * are both mandatory gates. FaceNet identity matching only runs after both pass.
 *
 *   1. Align Face
 *   2. Capture & Verify  →  anti-spoof check (server)
 *   3. Head-movement challenge  (MediaPipe, always shown)
 *   4. Process Verification  →  FaceNet match (server, only if liveness passed)
 *
 * A static photo, phone screen, or replay cannot complete the head-movement
 * challenge and will be rejected even if the face is clearly detected and sharp.
 *
 * Backend also enforces challenge_completed=True server-side in verify_submit.
 * In strict mode (LIVENESS_REQUIRED=True), a failed liveness check denies
 * the attempt before FaceNet runs.
 */

document.addEventListener('DOMContentLoaded', async () => {
  const video           = document.getElementById('video');
  const captureCanvas   = document.getElementById('captureCanvas');
  const startBtn        = document.getElementById('startBtn');
  const startBtnHint    = document.getElementById('startBtnHint');
  const verifyBtn       = document.getElementById('verifyBtn');
  const challengeBox    = document.getElementById('challengeBox');
  const challengeText   = document.getElementById('challengeText');
  const challengeTimer  = document.getElementById('challengeTimer');
  const step1Icon       = document.getElementById('step1Icon');
  const step1Msg        = document.getElementById('step1Msg');
  const step2Icon       = document.getElementById('step2Icon');
  const step2Msg        = document.getElementById('step2Msg');
  const step2Label      = document.getElementById('step2Label');
  const livenessScoreBox    = document.getElementById('livenessScoreBox');
  const livenessScoreVal    = document.getElementById('livenessScoreVal');
  const livenessScoreBar    = document.getElementById('livenessScoreBar');
  const livenessResultEl    = document.getElementById('livenessResult');
  const livenessResultBody  = document.getElementById('livenessResultBody');
  const processingOverlay   = document.getElementById('processingOverlay');
  const faceBorder          = document.getElementById('faceBorder');
  const statusPanel         = document.getElementById('statusPanel');
  const guidancePanel       = document.getElementById('guidancePanel');
  const qualityIndicator    = document.getElementById('qualityIndicator');

  // Step progress dots (1–4)
  const stepDots = [null,
    document.getElementById('step_dot_1'),
    document.getElementById('step_dot_2'),
    document.getElementById('step_dot_3'),
    document.getElementById('step_dot_4'),
  ];

  let livenessData = {
    passed: false,
    anti_spoof_score: 0,
    liveness_score: 0,
    face_detected: false,
    challenge_completed: false,
  };

  // Liveness-proof token issued by the server after challenge completes.
  // Must be sent with verify_submit to bind liveness to the identity match.
  let livenessToken = null;

  // Neutral/frontal frame captured at "Capture & Verify" time (Mode A frame).
  // Sent to verify_check_liveness Mode B so the server computes the FaceNet
  // embedding from the frontal face, not the turned challenge frame.
  let neutralFrameData = null;

  // Sequence frames captured during the challenge window (~600ms intervals).
  // Sent to verify_check_liveness Mode B for PAD sequence analysis.
  let sequenceFrames = [];

  let currentChallenge = CHALLENGE;
  let _qualityPollTimer = null;

  // Tracks whether this is a retry attempt (previous attempt score was too low).
  // Retries always trigger the full liveness challenge regardless of anti-spoof score.
  let isRetry = false;

  // ── Progress dot helper ───────────────────────────────────────────────────────
  function activateDot(n) {
    for (let i = 1; i <= 4; i++) {
      if (!stepDots[i]) continue;
      stepDots[i].style.background = i <= n ? '#1a4c8c' : '#c7d7fa';
      stepDots[i].style.color      = i <= n ? 'white'   : '#1a4c8c';
    }
  }

  // ── Start Camera ─────────────────────────────────────────────────────────────
  activateDot(1);
  await initCameraOrShowRetry();

  async function initCameraOrShowRetry() {
    const camResult = await startCamera(video);
    if (!camResult.success) {
      step1Icon.innerHTML = iconX();
      step1Msg.textContent = `Camera error: ${camResult.error}`;
      showCameraError(camResult.error);
      startBtn.disabled = true;
      if (startBtnHint) {
        startBtnHint.innerHTML = '<i class="bi bi-camera-video-off text-danger me-1"></i>Camera unavailable — check permissions and try again.';
      }
      return false;
    }
    clearStatus();
    startBtn.disabled = false;
    if (startBtnHint) startBtnHint.style.display = 'none';
    startBtn.innerHTML = '<i class="bi bi-camera me-1"></i> Capture &amp; Verify';
    showGuidance('Center your face in the oval, look at the camera, and click Capture &amp; Verify.', 'info');
    return true;
  }

  function showCameraError(rawMsg) {
    if (!statusPanel) return;
    const msg = String(rawMsg || 'No camera detected.');
    const lower = msg.toLowerCase();
    let hint = 'Allow camera access in the browser, close any other app using the camera, then click Retry Camera. If the problem persists, refresh the page.';
    if (lower.includes('permission') || lower.includes('denied') || lower.includes('notallowed')) {
      hint = 'Camera permission was denied. Click the camera icon in the address bar, choose "Always allow", then click Retry Camera.';
    } else if (lower.includes('notreadable') || lower.includes('in use')) {
      hint = 'Another app is using the camera. Close Zoom, Teams, OBS, or any other camera app, then click Retry Camera.';
    } else if (lower.includes('notfound') || lower.includes('no camera')) {
      hint = 'No camera detected. Connect a working webcam, then click Retry Camera.';
    } else if (lower.includes('https') || lower.includes('secure')) {
      hint = 'Camera requires a secure HTTPS connection. Open the system at https://fans-barangay.local instead.';
    }
    statusPanel.innerHTML = `
      <div class="alert alert-danger d-flex flex-column gap-2 mt-2 text-start">
        <div class="d-flex gap-2 align-items-start">
          <i class="bi bi-camera-video-off-fill flex-shrink-0 mt-1"></i>
          <div>
            <strong>Camera unavailable.</strong>
            <div class="small mt-1 font-mono">${escapeHtml(msg)}</div>
            <div class="small mt-1">${escapeHtml(hint)}</div>
          </div>
        </div>
        <button id="retryCameraBtn" type="button" class="btn btn-sm btn-outline-primary align-self-start">
          <i class="bi bi-arrow-clockwise me-1"></i> Retry Camera
        </button>
      </div>`;
    const retryBtn = document.getElementById('retryCameraBtn');
    if (retryBtn) {
      retryBtn.addEventListener('click', async () => {
        retryBtn.disabled = true;
        retryBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Trying camera…';
        const ok = await initCameraOrShowRetry();
        if (ok) startQualityPreview();
      });
    }
  }

  function escapeHtml(s) {
    return String(s || '').replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
  }

  // ── MediaPipe (optional) ─────────────────────────────────────────────────────
  let mpAvailable = false;
  if (typeof LivenessTracker !== 'undefined') {
    mpAvailable = await LivenessTracker.init(video, () => {});
    console.log('FANS-C: LivenessTracker.init =>', mpAvailable);
  } else {
    console.warn('FANS-C: LivenessTracker not defined — head tracking unavailable.');
  }

  // ── Continuous quality preview (lightweight client-side only) ──────────────
  function startQualityPreview() {
    if (!qualityIndicator) return;
    _qualityPollTimer = setInterval(() => {
      const w = video.videoWidth || 320;
      const h = video.videoHeight || 240;
      if (!w || !h) return;
      const tmpC   = document.createElement('canvas');
      const tmpCtx = tmpC.getContext('2d', { willReadFrequently: true });
      tmpC.width = w; tmpC.height = h;
      tmpCtx.drawImage(video, 0, 0, w, h);
      const sharp = estimateSharpnessSobel(tmpCtx, w, h);
      // Simple client-side brightness check
      const sample = tmpCtx.getImageData(w / 4, h / 4, w / 2, h / 2);
      let lumSum = 0;
      for (let i = 0; i < sample.data.length; i += 4) {
        lumSum += 0.299 * sample.data[i] + 0.587 * sample.data[i + 1] + 0.114 * sample.data[i + 2];
      }
      const brightness = lumSum / (sample.data.length / 4);
      updateQualityIndicator(sharp, brightness);
    }, 800);
  }

  function stopQualityPreview() {
    if (_qualityPollTimer) { clearInterval(_qualityPollTimer); _qualityPollTimer = null; }
  }

  function updateQualityIndicator(sharp, brightness) {
    if (!qualityIndicator) return;
    let level = 'good', msg = 'Good';
    if (sharp < 20) { level = 'bad'; msg = 'Too blurry'; }
    else if (sharp < 60) { level = 'warning'; msg = 'Hold still'; }
    if (brightness < 30) { level = 'bad'; msg = 'Too dark'; }
    else if (brightness > 230) { level = 'warning'; msg = 'Too bright'; }
    const colorMap = { good: '#22c55e', warning: '#f59e0b', bad: '#ef4444' };
    qualityIndicator.innerHTML = `
      <span class="quality-dot ${level}" style="background:${colorMap[level]};"></span>
      <span style="font-size:0.78rem; font-weight:600; color:${colorMap[level]};">${msg}</span>`;
  }

  startQualityPreview();

  // ── Start Button ─────────────────────────────────────────────────────────────
  startBtn.addEventListener('click', async () => {
    startBtn.disabled = true;
    startBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Checking&hellip;';
    clearStatus();
    clearGuidance();
    clearRetryAlerts();
    stopQualityPreview();
    activateDot(2);

    // ── Step 1: Server-side anti-spoofing + quality check ────────────────────
    // This runs on every attempt regardless of whether the challenge is shown.
    // Result is logged to the VerificationAttempt record even in assisted rollout mode.
    step1Icon.innerHTML = iconSpinner();
    step1Msg.textContent = 'Checking face\u2026';

    const frameData = captureFrame(video, captureCanvas);
    neutralFrameData = frameData;  // preserve for Mode B neutral-image embedding
    let livenessApiResult;

    try {
      livenessApiResult = await postJSON(CHECK_LIVENESS_URL, {
        image: frameData,
        challenge_completed: false,
        session_id: SESSION_ID,
      }, CSRF_TOKEN);
    } catch (err) {
      // Unhandled network error (server unreachable, DNS failure, etc.)
      step1Icon.innerHTML = iconX();
      const errMsg = String(err && err.message || err);
      let friendlyErr = 'Cannot reach the verification server. Check that the system is running.';
      if (/HTTP 403/i.test(errMsg)) {
        friendlyErr = 'Camera verification requires HTTPS. Open https://fans-barangay.local to use the full system.';
      }
      showStatus(friendlyErr, 'danger');
      startBtn.style.display = '';
      startBtn.disabled = false;
      startBtn.innerHTML = '<i class="bi bi-camera me-1"></i> Capture &amp; Verify';
      activateDot(1);
      startQualityPreview();
      return;
    }

    // Structured error returned by the server (HTTP fallback block, no-event block, etc.)
    if (livenessApiResult && livenessApiResult._httpStatus) {
      step1Icon.innerHTML = iconX();
      let blockMsg = livenessApiResult.error || 'Verification blocked by server.';
      if (livenessApiResult.http_fallback) {
        blockMsg = 'Camera verification requires HTTPS. Open https://fans-barangay.local to continue.';
      } else if (livenessApiResult.no_event) {
        blockMsg = 'No active payout event. Create or activate a payout event before processing claims.';
      }
      showStatus(blockMsg, 'danger');
      startBtn.style.display = '';
      startBtn.disabled = false;
      startBtn.innerHTML = '<i class="bi bi-camera me-1"></i> Capture &amp; Verify';
      activateDot(1);
      startQualityPreview();
      return;
    }

    if (!livenessApiResult.success) {
      step1Icon.innerHTML = iconWarn();
      step1Msg.textContent = livenessApiResult.error || 'Check failed \u2014 proceeding';
      livenessApiResult = {
        passed: false, face_detected: false,
        anti_spoof_score: 0, liveness_score: 0,
        reason: livenessApiResult.error,
      };
    }

    livenessData.face_detected    = livenessApiResult.face_detected !== false;
    livenessData.anti_spoof_score = livenessApiResult.anti_spoof_score || 0;
    livenessData.liveness_score   = livenessApiResult.liveness_score   || 0;

    // No face detected — show specific guidance and let staff retry
    if (!livenessData.face_detected) {
      step1Icon.innerHTML = iconX();
      step1Msg.textContent = livenessApiResult.reason || 'No face detected';
      if (faceBorder) faceBorder.style.borderColor = 'rgba(239,68,68,0.85)';
      showGuidance(buildFaceGuidance(livenessApiResult.reason || ''), 'warning');
      startBtn.style.display = '';
      startBtn.disabled = false;
      startBtn.innerHTML = '<i class="bi bi-camera me-1"></i> Capture &amp; Verify';
      activateDot(1);
      startQualityPreview();
      return;
    }

    // Show face quality guidance if poor
    if (livenessApiResult.face_quality_ok === false && livenessApiResult.face_quality_reason) {
      showGuidance(livenessApiResult.face_quality_reason, 'warning');
    }

    if (livenessApiResult.anti_spoof_passed) {
      step1Icon.innerHTML = iconCheck();
      step1Msg.textContent = `Passed (score: ${pct(livenessData.anti_spoof_score)}%)`;
      if (faceBorder) faceBorder.style.borderColor = 'rgba(34,197,94,0.85)';
    } else {
      step1Icon.innerHTML = iconWarn();
      step1Msg.textContent = `Low score (${pct(livenessData.anti_spoof_score)}%) \u2014 verification will be denied`;
      if (faceBorder) faceBorder.style.borderColor = 'rgba(234,179,8,0.85)';
    }

    // ── Liveness challenge is always required for final verification ─────────
    // Both anti-spoofing AND active head-movement challenge are mandatory gates.
    // FaceNet matching only runs on the server after both pass.
    // A static photo, phone screen, or replay cannot complete the movement.
    const needsChallenge = true;

    {
      // ── Active liveness challenge (always shown) ─────────────────────────
      let challengeReason = '';
      if (REQUIRE_LIVENESS_CHALLENGE) {
        challengeReason = 'Representative claim &mdash; liveness verification is required.';
      } else if (isRetry) {
        challengeReason = 'Previous attempt failed &mdash; please complete the liveness check to continue.';
      } else if (livenessData.anti_spoof_score < 0.30) {
        challengeReason = `Anti-spoof score low (${pct(livenessData.anti_spoof_score)}%) &mdash; liveness verification required.`;
      } else if (livenessApiResult.face_quality_ok === false) {
        challengeReason = 'Image quality concern detected &mdash; please complete the liveness check.';
      }

      if (step2Label) step2Label.textContent = 'Liveness Challenge';
      step2Icon.innerHTML = iconSpinner();
      step2Msg.textContent = 'Follow the on-screen challenge…';

      // Show challenge box immediately so the user knows where to align their face.
      if (challengeBox) challengeBox.style.display = '';
      if (challengeText) challengeText.textContent = CHALLENGE_DISPLAY;

      // Inject movement-progress indicator into the challenge box (real-time %).
      let _progressEl = document.getElementById('challengeProgress');
      if (!_progressEl && challengeBox) {
        _progressEl = document.createElement('div');
        _progressEl.id = 'challengeProgress';
        _progressEl.style.cssText = 'font-size:0.72rem;font-family:monospace;margin-top:6px;color:#0c4a6e;';
        challengeBox.appendChild(_progressEl);
      }

      // Wait for 10 consecutive stable frames before capturing baseline and starting the
      // 7-second challenge timer. This prevents false "face tracking lost" caused by
      // FaceMesh receiving empty/uninitialized frames while the camera is warming up.
      // The challenge window only begins after FaceMesh has confirmed stable detection.
      if (mpAvailable) {
        showGuidance('Align your face inside the oval.', 'info');
        console.log('FANS-C: Waiting for 10 stable frames before baseline…');
        const _stableStart = Date.now();
        while (Date.now() - _stableStart < 7000) {
          const stable = LivenessTracker.getStableFrameCount ? LivenessTracker.getStableFrameCount() : 10;
          if (stable >= 10) break;
          if (_progressEl) _progressEl.textContent = 'Tracking: aligning… (' + stable + '/10 frames)';
          await sleep(100);
        }
        const _stableReached = LivenessTracker.getStableFrameCount ? LivenessTracker.getStableFrameCount() : 0;
        console.log('FANS-C: stable frames =', _stableReached, 'after', (Date.now() - _stableStart), 'ms');
        if (_stableReached < 10) {
          console.warn('FANS-C: stable detection timed out — baseline may be inaccurate.');
        }
      }

      if (mpAvailable) {
        const _preBasePose = LivenessTracker.getCurrentPose ? LivenessTracker.getCurrentPose() : null;
        if (!_preBasePose) {
          console.warn('[FANS-C Challenge] getCurrentPose() returned null before setBaseline() — baseline may be inaccurate.');
        }
        LivenessTracker.setBaseline();
        const _capturedBaselineYaw = LivenessTracker.getBaselineYaw ? LivenessTracker.getBaselineYaw() : null;
        const _capturedStable = LivenessTracker.getStableFrameCount ? LivenessTracker.getStableFrameCount() : null;
        console.log('[FANS-C Challenge] baseline captured', {
          stableFrameCount: _capturedStable,
          baselineYaw: _capturedBaselineYaw,
          currentYaw: _preBasePose ? _preBasePose.yaw : null,
          challengeStarted: true,
        });
        if (_capturedBaselineYaw === null) {
          console.warn('[FANS-C Challenge] WARNING: baselineYaw is null after setBaseline() — FaceMesh may not have landmarks yet.');
        }
        if (LivenessTracker.getDebugInfo) {
          console.log('FANS-C: Challenge =', currentChallenge, '| display =', CHALLENGE_DISPLAY,
            '| baseline info:', LivenessTracker.getDebugInfo(currentChallenge));
        }
      }

      // Show challenge reason (if any) now that baseline is captured; otherwise clear alignment guidance.
      if (challengeReason) {
        showGuidance(challengeReason, 'warning');
      } else {
        clearGuidance();
      }

      // Start 7-second timer only after stable detection + baseline capture.
      // This window does not penalise the user for the FaceMesh warm-up time.
      if (challengeTimer) {
        challengeTimer.style.width = '100%';
        challengeTimer.style.transition = 'none';
        await sleep(20);
        challengeTimer.style.transition = 'width 7s linear';
        challengeTimer.style.width = '0%';
      }

      // Collect sequence frames every 600 ms during challenge for server-side PAD.
      // Limited to 6 frames maximum to keep the request payload small and response fast.
      sequenceFrames = [];
      let _seqIv = setInterval(() => {
        if (sequenceFrames.length >= 6) return;  // cap: 6 frames max
        try {
          const w = video.videoWidth || 320;
          const h = video.videoHeight || 240;
          if (w && h) {
            const tmp = document.createElement('canvas');
            tmp.width = 320; tmp.height = Math.round(320 * h / w);
            tmp.getContext('2d', { willReadFrequently: true }).drawImage(video, 0, 0, tmp.width, tmp.height);
            sequenceFrames.push(tmp.toDataURL('image/jpeg', 0.5));
          }
        } catch (_) {}
      }, 600);

      // Poll for movement completion (every 200 ms, 10 s total = 50 ticks).
      // MediaPipe available: movement must be detected within 10 s — timeout = fail.
      // MediaPipe unavailable: fall back to timer-accept so accessibility is not broken.
      let challengeCompleted = false;
      let _lastGuidanceMs = 0;
      let _lastDebugLogMs = 0;
      // v2.1.12 (Issue 1): elapsed must be reachable from outside the Promise so
      // it can be sent to the backend in the proof payload. Hoisting it (and a
      // start timestamp) here eliminates a ReferenceError that could leave the
      // UI stuck at "Processing liveness proof..." and prevent tx_token issue.
      let challengeElapsedMs = 0;
      const challengeStartTs = Date.now();
      await new Promise((resolve) => {
        const iv = setInterval(() => {
          challengeElapsedMs += 200;
          const elapsed = challengeElapsedMs;

          // Real-time movement progress overlay
          if (mpAvailable && _progressEl && LivenessTracker.getDebugInfo) {
            const dbg = LivenessTracker.getDebugInfo(currentChallenge);
            if (dbg.hasLandmarks) {
              const pct = dbg.progress;
              const delta = Math.abs(dbg.movementDelta).toFixed(1);
              _progressEl.textContent = 'Tracking: ' + delta + '° / ' + dbg.threshold + '° needed (' + pct + '%)';
              _progressEl.style.color = pct >= 100 ? '#15803d' : pct >= 50 ? '#a16207' : '#0c4a6e';
            } else {
              const _missed = LivenessTracker.getMissedFrameCount ? LivenessTracker.getMissedFrameCount() : 0;
              _progressEl.textContent = _missed > 15 ? 'Tracking: looking for face…' : 'Tracking: …';
              _progressEl.style.color = '#9d174d';
            }
          }

          // Debug console log every ~1.5 s
          if (mpAvailable && Date.now() - _lastDebugLogMs > 1500 && LivenessTracker.getDebugInfo) {
            _lastDebugLogMs = Date.now();
            const dbg = LivenessTracker.getDebugInfo(currentChallenge);
            console.log('FANS-C challenge tick ' + elapsed + 'ms dir=' + currentChallenge
              + ' landmarks=' + dbg.hasLandmarks
              + (dbg.hasLandmarks ? ' yawΔ=' + dbg.yawDelta.toFixed(2) + ' pitchΔ=' + dbg.pitchDelta.toFixed(2) + ' progress=' + dbg.progress + '%' : ''));
          }

          if (mpAvailable && LivenessTracker.checkChallenge(currentChallenge)) {
            challengeCompleted = true;
            const _donePose = LivenessTracker.getCurrentPose ? LivenessTracker.getCurrentPose() : null;
            const _doneBase = LivenessTracker.getBaselineYaw ? LivenessTracker.getBaselineYaw() : null;
            const _doneYawDelta = (_donePose && _doneBase !== null) ? _donePose.yaw - _doneBase : null;
            console.log('[FANS-C Challenge] completed', {
              currentYaw: _donePose ? _donePose.yaw : null,
              baselineYaw: _doneBase,
              yawDelta: _doneYawDelta,
              absYawDelta: _doneYawDelta !== null ? Math.abs(_doneYawDelta) : null,
            });
            console.log('FANS-C: Challenge PASSED at ' + elapsed + 'ms direction=' + currentChallenge);
            clearInterval(iv);
            resolve();
            return;
          }
          // Progressive guidance based on consecutive missed frames (frame-level counter from liveness.js).
          // 0–15 missed frames (~0–0.5 s): silent — do not interrupt user who is mid-movement.
          // 15–45 missed frames (~0.5–1.5 s): "Looking for your face…" (soft recovery cue).
          // 45+ missed frames (~1.5 s+): "Face tracking lost." (clear re-center instruction).
          if (mpAvailable && elapsed > 1000) {
            const _missed = LivenessTracker.getMissedFrameCount ? LivenessTracker.getMissedFrameCount() : 0;
            if (_missed === 0) {
              if (Date.now() - _lastGuidanceMs > 2000) {
                _lastGuidanceMs = Date.now();
                showGuidance(CHALLENGE_DISPLAY + ' — keep moving until the bar reaches 100%.', 'warning');
              }
            } else if (_missed > 45) {
              if (Date.now() - _lastGuidanceMs > 500) {
                _lastGuidanceMs = Date.now();
                showGuidance('Face tracking lost. Re-center your face inside the oval.', 'warning');
              }
            } else if (_missed > 15) {
              if (Date.now() - _lastGuidanceMs > 500) {
                _lastGuidanceMs = Date.now();
                showGuidance('Looking for your face…', 'info');
              }
            }
          }
          if (elapsed >= 10000) {
            // Timeout — never auto-accept. Real head movement is required.
            // v2.1.11 (Issue 10): widened from 7 s → 10 s to give real users
            // time to follow the instruction at a normal speed.
            challengeCompleted = false;
            console.log('FANS-C: Challenge timeout. mpAvailable=' + mpAvailable + ' — not auto-accepted.');
            clearInterval(iv);
            resolve();
          }
        }, 200);
      });
      clearInterval(_seqIv);
      livenessData.challenge_completed = challengeCompleted;
      if (challengeBox) challengeBox.style.display = 'none';
      clearGuidance();

      // ── Mode B: send final frame + sequence to server for PAD + TX issuance ─
      // The server computes FaceNet embedding from this frame and stores it in a
      // LivenessTransaction.  verify_submit will use the stored embedding for
      // identity matching, preventing face-switching after liveness.
      //
      // v2.1.12 (Issue 1): the UI MUST exit "Processing liveness proof..." on
      // every path so the operator is never stranded. We use try/catch/finally,
      // capture frontend-thrown exceptions, and wait briefly for the minimum
      // sequence frame count to be collected before sending.
      let _proofFrontendError = null;
      if (challengeCompleted) {
        step2Msg.textContent = 'Processing liveness proof…';

        // If the challenge completed before enough sequence frames were
        // captured (the backend requires 3 minimum), capture an immediate
        // burst so a fast-but-valid real movement still produces a proof.
        const MIN_SEQ_FRAMES = 3;
        if (sequenceFrames.length < MIN_SEQ_FRAMES) {
          console.log('FANS-C: Only', sequenceFrames.length, 'seq frames captured; topping up.');
          const _topupStart = Date.now();
          while (sequenceFrames.length < MIN_SEQ_FRAMES && Date.now() - _topupStart < 1500) {
            try {
              const w = video.videoWidth || 320;
              const h = video.videoHeight || 240;
              if (w && h) {
                const tmp = document.createElement('canvas');
                tmp.width = 320; tmp.height = Math.round(320 * h / w);
                tmp.getContext('2d', { willReadFrequently: true }).drawImage(video, 0, 0, tmp.width, tmp.height);
                sequenceFrames.push(tmp.toDataURL('image/jpeg', 0.5));
              }
            } catch (_) {}
            await sleep(200);
          }
        }

        console.time('liveness-proof-request');
        try {
          const proofFrame = captureFrame(video, captureCanvas);
          // v2.1.11 — bundle client-side movement metrics so the backend can
          // store them in the audit log even when MediaPipe is the only signal.
          const _dbgFinal = (mpAvailable && LivenessTracker.getDebugInfo)
            ? LivenessTracker.getDebugInfo(currentChallenge) : null;
          // v2.1.12 — challengeElapsedMs is reachable from the outer scope.
          // Use a fallback computed from start timestamp if the interval was
          // never ticked (defence-in-depth).
          const _durationMs = challengeElapsedMs > 0
            ? challengeElapsedMs
            : Math.max(0, Date.now() - challengeStartTs);
          const _movement = {
            peak_yaw_delta: _dbgFinal ? Math.abs(_dbgFinal.peakYawDelta || 0) : 0,
            peak_pitch_delta: _dbgFinal ? Math.abs(_dbgFinal.peakPitchDelta || 0) : 0,
            threshold: _dbgFinal ? _dbgFinal.threshold : 0,
            face_lost_count: (LivenessTracker.getFaceLostCount
              ? LivenessTracker.getFaceLostCount() : 0),
            max_lost_burst: (LivenessTracker.getMaxLostBurst
              ? LivenessTracker.getMaxLostBurst() : 0),
            challenge_duration_ms: _durationMs,
            mediapipe_available: !!mpAvailable,
          };
          console.log('FANS-C: Mode B proof POST — seq_frames=' + sequenceFrames.length
            + ' duration_ms=' + _durationMs + ' peak_yaw=' + _movement.peak_yaw_delta.toFixed(2)
            + ' peak_pitch=' + _movement.peak_pitch_delta.toFixed(2));
          // v2.1.16 (Security Hardening Round #3 — Blocker 1): raw MediaPipe
          // landmark snapshots so the server can independently recompute pose
          // and verify the challenge itself, rather than trusting
          // challenge_completed / movement.* alone.
          const _baselineLandmarks = (LivenessTracker.getBaselineLandmarks
            ? LivenessTracker.getBaselineLandmarks() : null);
          const _challengeLandmarks = (LivenessTracker.getPeakLandmarks
            ? LivenessTracker.getPeakLandmarks() : null);
          const proofResult = await postJSON(CHECK_LIVENESS_URL, {
            image: proofFrame,
            neutral_image: neutralFrameData,  // frontal frame for embedding + anti-spoof
            challenge_completed: true,
            frames: sequenceFrames,
            movement: _movement,
            baseline_landmarks: _baselineLandmarks,
            challenge_landmarks: _challengeLandmarks,
            session_id: SESSION_ID,
          }, CSRF_TOKEN);
          console.timeEnd('liveness-proof-request');
          console.log('Liveness response JSON:', JSON.stringify(proofResult, null, 2));

          if (proofResult && proofResult.tx_token) {
            livenessToken = proofResult.tx_token;
            console.log('Saved livenessToken:', livenessToken);
            console.log('FANS-C: Liveness TX issued, token=', livenessToken);
          } else {
            // Challenge completed but server did not issue a proof token.
            // CRITICAL: clear any stale token, zero out score, block submission.
            livenessToken = null;
            livenessData.passed = false;
            livenessData.liveness_score = 0;
            const _noTokenReason = (
              proofResult && (proofResult.error || proofResult.reason || proofResult.debug_stage)
            ) || 'Proof token not issued by server.';
            livenessData._noTokenReason = _noTokenReason;
            console.warn('FANS-C: Mode B returned no tx_token. debug_stage=',
              proofResult && proofResult.debug_stage, 'reason=', _noTokenReason);
            step2Icon.innerHTML = iconX();
            step2Msg.textContent = 'Liveness proof not issued — please retry.';
            // Show score as 0% immediately (before the combined calculation below overwrites it)
            showLivenessScoreBar(0);
            showLivenessCard(false, 'Liveness proof not issued: ' + _noTokenReason + ' Please retry.',
              LIVENESS_REQUIRED ? 'danger' : 'warning');
          }
          if (proofResult && proofResult.presentation_attack_suspected) {
            console.warn('FANS-C: PAD suspicious, score=', proofResult.presentation_attack_score,
              'flags=', proofResult.presentation_attack_flags);
          }
        } catch (proofErr) {
          try { console.timeEnd('liveness-proof-request'); } catch (_) {}
          // v2.1.12 (Issue 1): capture any JS-level failure (ReferenceError,
          // TypeError, network error, etc.) so the UI never gets stuck and the
          // operator sees the real reason.
          _proofFrontendError = String(proofErr && proofErr.message || proofErr);
          console.warn('FANS-C: Mode B liveness proof call failed:', proofErr);
          livenessToken = null;
          livenessData.passed = false;
          livenessData.liveness_score = 0;
          livenessData._noTokenReason =
            'Liveness proof request failed: ' + _proofFrontendError;
          step2Icon.innerHTML = iconX();
          step2Msg.textContent = 'Liveness proof request failed — please retry.';
          showLivenessScoreBar(0);
          showLivenessCard(false,
            'Liveness proof request failed (' + _proofFrontendError + '). Please retry.',
            LIVENESS_REQUIRED ? 'danger' : 'warning');
        } finally {
          // Defence-in-depth: clear the spinner regardless of which branch ran.
          if (processingOverlay) processingOverlay.style.display = 'none';
        }
      }

      // Only overwrite step2 icon/msg when no token failure hasn't already set them.
      if (!challengeCompleted || livenessToken) {
        step2Icon.innerHTML = challengeCompleted ? iconCheck() : iconX();
        // v2.1.11 (Issue 10): provide a specific failure reason so the user
        // knows whether the camera lost the face (move slower) vs. no
        // movement happened at all (move at all).
        let _failMsg = 'Challenge failed — no head movement detected';
        if (mpAvailable && !challengeCompleted) {
          const _dbg = LivenessTracker.getDebugInfo ? LivenessTracker.getDebugInfo(currentChallenge) : null;
          const _maxLost = LivenessTracker.getMaxLostBurst ? LivenessTracker.getMaxLostBurst() : 0;
          const _peakY = _dbg ? _dbg.peakYawDelta : 0;
          const _peakP = _dbg ? _dbg.peakPitchDelta : 0;
          if (_maxLost > 8) {
            _failMsg = 'Movement too fast or face lost during the challenge — please retry and move your head slowly and naturally.';
          } else if (_peakY < 1.5 && _peakP < 1.5) {
            _failMsg = 'No head movement detected — please move your head slightly left/right or up/down.';
          } else {
            _failMsg = 'Head movement was too small — please move a little further in the indicated direction.';
          }
        }
        step2Msg.textContent = challengeCompleted
          ? (mpAvailable ? 'Challenge completed' : 'Challenge accepted (head tracking unavailable)')
          : (!mpAvailable
              ? 'Head tracking unavailable — liveness challenge could not be verified'
              : _failMsg);
      }

      // CRITICAL: passed requires anti-spoof + challenge + a valid server-issued token.
      // Without livenessToken the verify button must show liveness-failed state so the
      // operator knows to retry — not a green "Process Verification" button.
      livenessData.passed = livenessApiResult.anti_spoof_passed && challengeCompleted && !!livenessToken;

      // Final combined score: 0.6 × anti_spoof + 0.4 × challenge_completed.
      // If the server did not issue a token, the score is 0 — not 100% — because
      // liveness is not proven regardless of client-side progress.
      let finalLivenessScore;
      if (!livenessToken) {
        // No valid server-issued proof: score is always 0, never reflects client progress.
        finalLivenessScore = 0;
      } else {
        finalLivenessScore = Math.min(
          0.6 * (livenessData.anti_spoof_score || 0) + 0.4 * (challengeCompleted ? 1.0 : 0.0),
          1.0
        );
      }
      livenessData.liveness_score = finalLivenessScore;

      showLivenessScoreBar(finalLivenessScore);

      const antiSpoof = pct(livenessData.anti_spoof_score);
      if (livenessData.passed) {
        showLivenessCard(true, `Liveness check passed. Anti-spoof: ${antiSpoof}%, Challenge: done.`);
      } else if (!livenessApiResult.anti_spoof_passed) {
        // Anti-spoof texture check failed (low score \u2014 possible spoof or poor quality).
        showLivenessCard(false,
          LIVENESS_REQUIRED
            ? `Anti-spoofing score too low (${antiSpoof}%). Verification will be denied.`
            : `Anti-spoofing score low (${antiSpoof}%) \u2014 continuing in assisted rollout mode. Real deployment requires a higher score.`,
          LIVENESS_REQUIRED ? 'danger' : 'warning'
        );
      } else if (challengeCompleted && !livenessToken) {
        // v2.1.12 (Issue 1): the challenge was completed on the client BUT the
        // server did not issue a tx_token (anti-spoof on neutral frame, PAD,
        // embedding failure, no session, etc.). Show the real backend reason
        // instead of the generic "head movement not detected" message.
        const _reason = livenessData._noTokenReason
          || (_proofFrontendError ? ('Frontend error: ' + _proofFrontendError) : 'Server did not issue a liveness proof.');
        showLivenessCard(false,
          'Liveness proof not issued: ' + _reason + ' Please retry.',
          LIVENESS_REQUIRED ? 'danger' : 'warning'
        );
      } else {
        // Anti-spoof passed but challenge was not completed (movement not detected or
        // head tracking unavailable).
        showLivenessCard(false,
          !mpAvailable
            ? 'Liveness challenge could not be verified. Please retry with your full face visible and camera tracking enabled.'
            : 'Head movement not detected \u2014 move your head clearly in the required direction and try again.',
          LIVENESS_REQUIRED ? 'danger' : 'warning'
        );
      }

    }

    // ── Show verify button ────────────────────────────────────────────────────
    // Full reset: clear every state that could leave the button unclickable after
    // a previous verify attempt or retry (disabled attribute, aria-disabled,
    // pointer-events, and the processing overlay that sits above the button).
    activateDot(3);
    verifyBtn.removeAttribute('aria-disabled');
    verifyBtn.style.pointerEvents = '';
    if (processingOverlay) processingOverlay.style.display = 'none';
    verifyBtn.style.display = '';

    if (!livenessData.passed && LIVENESS_REQUIRED) {
      // Strict mode + liveness failed — turn the verify button into a CLICKABLE
      // Retry button (Issue 7). Previously it was disabled which left users
      // stranded with no way forward except a full page reload.
      verifyBtn.disabled = false;
      verifyBtn.removeAttribute('aria-disabled');
      verifyBtn.style.pointerEvents = '';
      verifyBtn.innerHTML = '<i class="bi bi-arrow-counterclockwise me-1"></i> Liveness Failed — Retry';
      verifyBtn.className = 'btn btn-danger fw-semibold w-100';
      verifyBtn.dataset.action = 'retry';
    } else if (!livenessData.passed && !LIVENESS_REQUIRED) {
      verifyBtn.disabled = false;
      verifyBtn.innerHTML = '<i class="bi bi-shield-exclamation me-1"></i> Process Verification (Liveness Warning)';
      verifyBtn.className = 'btn btn-warning fw-semibold w-100';
    } else {
      verifyBtn.disabled = false;
      verifyBtn.innerHTML = '<i class="bi bi-shield-check me-1"></i> Process Verification';
      verifyBtn.className = 'btn btn-success fw-semibold w-100';
      verifyBtn.dataset.action = 'verify';
    }
    startBtn.style.display = 'none';

    showGuidance('Hold still and look at the camera, then click Process Verification.', 'info');
    startQualityPreview();
  });

  // ── Verify Button ─────────────────────────────────────────────────────────────
  verifyBtn.addEventListener('click', async () => {
    if (verifyBtn.dataset.action === 'retry') {
      verifyBtn.disabled = true;
      verifyBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Resetting\u2026';
      showStatus('Resetting capture flow \u2014 please retry the liveness challenge.', 'info');
      await resetToCaptureFlow();
      return;
    }
    verifyBtn.disabled = true;
    verifyBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Processing\u2026';
    if (processingOverlay) processingOverlay.style.display = '';
    clearGuidance();
    stopQualityPreview();
    activateDot(4);

    // Capture best-quality frame from a 7-frame burst
    const imageData = await captureFrameHighQuality(video, captureCanvas, 7, 70);
    await submitVerification(imageData);
  });

  // ── Submit ───────────────────────────────────────────────────────────────────
  async function submitVerification(imageData) {
    // Guard: block submit if liveness did not fully pass (passed=false or token missing).
    // A failed retry must never reuse a token from a previous successful attempt.
    // In strict mode this path is unreachable because the button is disabled, but
    // the guard remains as a defence-in-depth check for non-strict mode edge cases.
    if (!livenessData.passed || !livenessToken) {
      const _backendReason = livenessData._noTokenReason
        || (!livenessToken ? 'No valid server-issued liveness proof.' : 'Liveness check did not pass.');
      showStatus(
        'Cannot submit: ' + _backendReason + ' Please retry the liveness challenge.',
        'danger'
      );
      await resetToCaptureFlow();
      return;
    }
    // Capture token for THIS submit then immediately clear the in-page copy.
    // v2.1.11 (Issue 3/10 follow-up): even if a network error blows up the request
    // mid-flight, the same token can never be reused by a subsequent click — the
    // server-side single-use enforcement already prevents it, but clearing here
    // also blocks the no-network-error case where an aborted submit might leave
    // the token lying around. The user must complete a fresh liveness challenge.
    const _submitToken = livenessToken;
    livenessToken = null;
    livenessData.passed = false;
    console.log('Submitting tx_token:', _submitToken);
    console.time('verify-submit');
    try {
      const result = await postJSON(VERIFY_SUBMIT_URL, {
        image: imageData,
        challenge_completed: livenessData.challenge_completed,
        liveness_passed: true,
        face_detected: livenessData.face_detected,
        liveness_score: livenessData.liveness_score,
        anti_spoof_score: livenessData.anti_spoof_score,
        tx_token: _submitToken || '',
        session_id: SESSION_ID,
      }, CSRF_TOKEN);

      console.timeEnd('verify-submit');
      stopCamera();
      if (processingOverlay) processingOverlay.style.display = 'none';

      if (!result.success) {
        const errMsg = result.error || 'Verification failed. Please try again.';
        // Session expired → page reload is the only fix; send back to selection.
        if (/session\s*expired/i.test(errMsg)) {
          showStatus(errMsg + ' Returning to beneficiary selection…', 'danger');
          setTimeout(() => { window.location.href = '/verification/'; }, 1500);
          return;
        }
        // No active payout event → show clear message, don't loop
        if (result.no_event) {
          showStatus('No active payout event. Create or activate a payout event before processing claims.', 'danger');
          startBtn.style.display = 'none';
          verifyBtn.style.display = 'none';
          return;
        }
        // HTTP fallback → show HTTPS redirect message
        if (result.http_fallback) {
          showStatus('Camera verification requires HTTPS. Open https://fans-barangay.local to continue.', 'danger');
          startBtn.style.display = 'none';
          verifyBtn.style.display = 'none';
          return;
        }
        showStatus(errMsg, 'danger');
        await resetToCaptureFlow();
        return;
      }

      if (result.decision === 'retry') {
        // Score was below threshold — the next attempt always requires the full challenge
        isRetry = true;
        currentChallenge = result.new_challenge || CHALLENGE;
        clearRetryAlerts();
        showRetryAlert(result.message, result.new_challenge_display, result.score, result.threshold, result.attempt_number, result.max_retries);
        await restartCamera();
        // Full verify button reset before hiding — eliminates any stale disabled/aria state
        // that would block the button from being clickable when it's shown again.
        verifyBtn.disabled = false;
        verifyBtn.removeAttribute('aria-disabled');
        verifyBtn.style.pointerEvents = '';
        verifyBtn.innerHTML = '<i class="bi bi-shield-check me-1"></i> Process Verification';
        verifyBtn.className = 'btn btn-success fw-semibold w-100';
        verifyBtn.style.display = 'none';
        startBtn.style.display = '';
        startBtn.disabled = false;
        startBtn.innerHTML = '<i class="bi bi-arrow-repeat me-1"></i> Retry Verification';
        livenessData = {
          passed: false, anti_spoof_score: 0, liveness_score: 0,
          face_detected: false, challenge_completed: false,
        };
        livenessToken = null;
        neutralFrameData = null;
        sequenceFrames = [];
        // Reset step indicators to waiting state
        step1Icon.innerHTML = '<i class="bi bi-hourglass-split text-muted"></i>';
        step1Msg.textContent = 'Waiting\u2026';
        step2Icon.innerHTML = '<i class="bi bi-hourglass-split text-muted"></i>';
        step2Msg.textContent = 'Waiting\u2026';
        if (livenessScoreBox) livenessScoreBox.style.display = 'none';
        if (livenessResultEl) livenessResultEl.style.display = 'none';
        if (faceBorder) faceBorder.style.borderColor = 'rgba(255,255,255,0.65)';
        activateDot(1);
        startQualityPreview();
        return;
      }

      window.location.href = result.redirect;

    } catch (err) {
      if (processingOverlay) processingOverlay.style.display = 'none';
      const m = String(err && err.message || err);
      let friendly = `Network error: ${m}.`;
      if (/Failed to fetch|NetworkError|TypeError/i.test(m)) {
        friendly = 'Network error: cannot reach the server. Check that the system is running, then try again.';
      } else if (/HTTP 5\d\d/i.test(m)) {
        friendly = `Server error (${m}). Try again in a few seconds; if it keeps failing, contact IT/Admin.`;
      } else if (/HTTP 403/i.test(m)) {
        friendly = 'Session/CSRF check failed. Please refresh the page and sign in again.';
      } else if (/HTTP 413/i.test(m)) {
        friendly = 'Captured image is too large. Move closer and try again.';
      }
      showStatus(friendly, 'danger');
      await resetToCaptureFlow();
    }
  }

  async function restartCamera() {
    stopCamera();
    return startCamera(video);
  }

  // Brings the UI back to the "Capture & Verify" starting state after a failed
  // submit. Clears stale liveness data, restarts the camera if needed, and
  // re-enables the start button so the user can recapture cleanly.
  async function resetToCaptureFlow() {
    // Always clear token on any reset so a failed retry cannot reuse a previous token.
    livenessToken = null;
    neutralFrameData = null;
    sequenceFrames = [];
    livenessData = {
      passed: false, anti_spoof_score: 0, liveness_score: 0,
      face_detected: false, challenge_completed: false,
    };
    verifyBtn.style.display = 'none';
    verifyBtn.disabled = false;
    verifyBtn.removeAttribute('aria-disabled');
    verifyBtn.style.pointerEvents = '';
    verifyBtn.innerHTML = '<i class="bi bi-shield-check me-1"></i> Process Verification';
    verifyBtn.className = 'btn btn-success fw-semibold w-100';
    if (challengeBox) challengeBox.style.display = 'none';
    if (livenessScoreBox) livenessScoreBox.style.display = 'none';
    if (livenessResultEl) livenessResultEl.style.display = 'none';
    if (faceBorder) faceBorder.style.borderColor = 'rgba(255,255,255,0.65)';
    step1Icon.innerHTML = '<i class="bi bi-hourglass-split text-muted"></i>';
    step1Msg.textContent = 'Waiting…';
    step2Icon.innerHTML = '<i class="bi bi-hourglass-split text-muted"></i>';
    step2Msg.textContent = 'Waiting…';
    activateDot(1);

    const cam = await restartCamera();
    if (!cam || cam.success === false) {
      showCameraError(cam && cam.error || 'Camera unavailable.');
      startBtn.disabled = true;
      startBtn.style.display = '';
      return;
    }
    startBtn.disabled = false;
    startBtn.style.display = '';
    startBtn.innerHTML = '<i class="bi bi-arrow-repeat me-1"></i> Try Capture Again';
    startQualityPreview();
  }

  // ── Guidance helpers ──────────────────────────────────────────────────────────
  function buildFaceGuidance(reason) {
    const r = (reason || '').toLowerCase();
    if (r.includes('small') || r.includes('closer')) {
      return 'Face too small \u2014 move closer to the camera (30-50 cm away).';
    }
    if (r.includes('dark') || r.includes('lighting')) {
      return 'Too dark \u2014 face a light source (window or lamp) and turn on room lights.';
    }
    if (r.includes('blur') || r.includes('still') || r.includes('focus')) {
      return 'Image blurry \u2014 hold the device/laptop steady. Do not shake.';
    }
    if (r.includes('glare') || r.includes('overexpos')) {
      return 'Glare detected \u2014 move away from bright light or glass behind you.';
    }
    if (r.includes('confidence') || r.includes('low')) {
      return 'Face not clearly detected \u2014 look directly at the camera, remove hat or mask.';
    }
    return 'No face detected \u2014 center your face in the oval and look directly at the camera.';
  }

  function showGuidance(msg, type) {
    if (!guidancePanel) return;
    const icon = type === 'warning' ? 'exclamation-triangle-fill'
               : type === 'success' ? 'check-circle-fill'
               : 'info-circle-fill';
    const colors = {
      info:    'background:#f0f9ff; border:1px solid #7dd3fc; color:#0c4a6e;',
      warning: 'background:#fff7ed; border:1px solid #fdba74; color:#7c2d12;',
      success: 'background:#f0fdf4; border:1px solid #86efac; color:#14532d;',
      danger:  'background:#fef2f2; border:1px solid #fca5a5; color:#7f1d1d;',
    };
    guidancePanel.style.display = '';
    guidancePanel.innerHTML = `
      <div class="d-flex gap-2 align-items-start p-3 rounded-3 small" style="${colors[type] || colors.info}">
        <i class="bi bi-${icon} flex-shrink-0 mt-1"></i>
        <span>${msg}</span>
      </div>`;
  }

  function clearGuidance() {
    if (guidancePanel) { guidancePanel.style.display = 'none'; guidancePanel.innerHTML = ''; }
  }

  // ── Retry Alert ───────────────────────────────────────────────────────────────
  function showRetryAlert(msg, newChallenge, score, threshold, attemptNum, maxRetries) {
    const container = document.getElementById('retryAlertContainer');
    if (!container) return;
    const scoreText = (score !== null && score !== undefined)
      ? `Score: <strong>${score.toFixed(3)}</strong> (need &ge; <strong>${threshold.toFixed(2)}</strong>). `
      : '';
    const attemptText = (attemptNum && maxRetries)
      ? `Attempt ${attemptNum} of ${maxRetries + 1}. ` : '';
    container.innerHTML = `
      <div class="alert alert-warning d-flex gap-2 align-items-start">
        <i class="bi bi-arrow-repeat flex-shrink-0 mt-1"></i>
        <div>
          <strong>Retry Required</strong><br>
          ${scoreText}${attemptText}${msg || ''}
          ${newChallenge ? `<br>New challenge: <strong>${newChallenge}</strong>` : ''}
        </div>
      </div>`;
  }

  function clearRetryAlerts() {
    const container = document.getElementById('retryAlertContainer');
    if (container) container.innerHTML = '';
  }

  // ── UI Helpers ───────────────────────────────────────────────────────────────
  function showLivenessScoreBar(score) {
    if (!livenessScoreBox) return;
    livenessScoreBox.style.display = '';
    const sp = Math.min(pct(score), 100);
    if (livenessScoreVal) livenessScoreVal.textContent = `${sp}%`;
    if (livenessScoreBar) {
      livenessScoreBar.style.width = `${sp}%`;
      livenessScoreBar.className = `progress-bar ${sp >= 60 ? 'bg-success' : sp >= 30 ? 'bg-warning' : 'bg-danger'}`;
    }
  }

  // severity: 'success' | 'danger' | 'warning'. Defaults to 'success' when
  // passed, otherwise 'danger' — a failed/blocking liveness result must read
  // visibly differently from a merely soft, non-blocking warning. Callers
  // pass severity='warning' explicitly only for the one genuinely non-blocking
  // case (assisted rollout mode continuing despite a low anti-spoof score).
  function showLivenessCard(passed, msg, severity) {
    if (!livenessResultEl) return;
    if (!severity) severity = passed ? 'success' : 'danger';
    livenessResultEl.style.display = '';
    const _iconClass = severity === 'success' ? 'bi-check-circle-fill'
      : severity === 'warning' ? 'bi-exclamation-triangle-fill'
      : 'bi-x-circle-fill';
    livenessResultBody.innerHTML =
      `<div class="d-flex align-items-center gap-2 text-${severity}"><i class="bi ${_iconClass} fs-5"></i><span>${msg}</span></div>`;
  }

  function showStatus(msg, type) {
    if (!statusPanel) return;
    statusPanel.innerHTML = `<div class="alert alert-${type} d-flex gap-2 align-items-center mt-2">
      <i class="bi bi-exclamation-triangle-fill flex-shrink-0"></i>${msg}</div>`;
  }

  function clearStatus() {
    if (statusPanel) statusPanel.innerHTML = '';
  }

  function iconCheck()   { return '<i class="bi bi-check-circle-fill text-success fs-5"></i>'; }
  function iconX()       { return '<i class="bi bi-x-circle-fill text-danger fs-5"></i>'; }
  function iconWarn()    { return '<i class="bi bi-exclamation-triangle-fill text-warning fs-5"></i>'; }
  function iconSpinner() { return '<span class="spinner-border spinner-border-sm text-warning"></span>'; }
  function pct(val)      { return Math.round((val || 0) * 100); }
  function sleep(ms)     { return new Promise(r => setTimeout(r, ms)); }

  // ── bfcache restore guard ─────────────────────────────────────────────────────
  // When the browser restores this page from the back/forward cache after a
  // navigate-away (e.g., denied → result page → browser Back), the JS state is
  // frozen with verifyBtn.disabled=true. Reset to a clean capture state so the
  // staff can retry without a full page refresh.
  window.addEventListener('pageshow', (ev) => {
    if (ev.persisted) {
      resetToCaptureFlow();
    }
  });
});
