// liveness build: 2026-05-25 baseline-yaw-fix
/**
 * Liveness detection helpers using MediaPipe Face Mesh.
 * Tracks head pose to validate challenge completion (client-side).
 *
 * CHALLENGE_THRESHOLD_DEG = 5 degrees.
 *   Chosen so a natural ~15° physical head turn produces a passing reading.
 *   The normalized formula ((nose.x - eyeCenterX) / eyeWidth) * 45 maps
 *   a 15° physical turn to roughly 5° in formula space, making 5° the correct
 *   accessible threshold.
 *
 * Challenge direction 'side':
 *   Accepts movement to EITHER left or right (abs yaw delta >= threshold).
 *   This avoids issues with mirrored webcam preview where left/right may be
 *   perceived differently by different users. A static photo or phone screen
 *   cannot produce this movement.
 *
 * Assets loaded from local static path (window.MEDIAPIPE_BASE_URL), not CDN.
 */

const LivenessTracker = (() => {
  let faceMesh       = null;
  let initialPose    = null;
  let _baselineYaw   = null;   // explicit baseline yaw; mirrors initialPose.yaw after setBaseline()
  let currentLandmarks = null;
  let _initialized   = false;

  // Accessible threshold for senior citizens (v2.1.11 — Issue 10).
  // Tuned to accept normal-speed head turns while still rejecting micro-jitter
  // from a static photo. A natural ~12° physical head turn produces ~4° in
  // the normalized formula. Lower than this would let phone-screen jitter in;
  // higher would force exaggerated/slow movement that fails real users.
  const CHALLENGE_THRESHOLD_DEG = 4;

  // Rolling history of recent pose estimates for a stable averaged baseline.
  const POSE_HISTORY_SIZE = 5;
  let poseHistory = [];

  let stableFrameCount = 0; // consecutive frames with landmarks (resets on miss)
  let missedFrameCount  = 0; // consecutive frames without landmarks (resets on detection)
  let _frameCount       = 0; // total frames sent to FaceMesh (for periodic debug log)
  // v2.1.11 (Issue 10): track best (peak) movement observed during the
  // challenge so a brief natural turn counts even if the user returned
  // to centre before the next frame was sampled.
  let _peakYawDelta    = 0;
  let _peakPitchDelta  = 0;
  let _faceLostCount   = 0; // total frames with no face during the challenge
  let _lostBurst       = 0; // current run length of face-lost frames (for fast-motion detection)
  let _maxLostBurst    = 0; // longest face-lost burst observed during the challenge
  // v2.1.16 (Security Hardening Round #3 — Blocker 1): raw landmark snapshots
  // sent to the server so it can independently recompute pose and verify the
  // challenge itself (server no longer trusts challenge_completed / peak
  // yaw-pitch numbers alone — see verify_server_side_challenge() in
  // verification/liveness.py). Captured at setBaseline() and whenever a new
  // peak movement is observed.
  let _baselineLandmarks = null;
  let _peakLandmarks     = null;

  function _snapshotLandmarks(lm) {
    if (!lm) return null;
    return lm.map((p) => ({ x: p.x, y: p.y, z: p.z }));
  }

  /**
   * Estimate head pose from normalized FaceMesh landmarks.
   * Uses nose TIP (landmark 4) which is far from the rotation axis and shows
   * clear angular displacement for even slight turns/tilts.
   * Normalizes by face dimensions for scale-invariance across camera distances.
   */
  function estimatePose(landmarks) {
    if (!landmarks || landmarks.length < 468) return { yaw: 0, pitch: 0 };

    const nose     = landmarks[4];   // nose TIP — more angular displacement than bridge (1)
    const leftEye  = landmarks[33];
    const rightEye = landmarks[263];
    const chin     = landmarks[152];
    const forehead = landmarks[10];

    const eyeCenterX = (leftEye.x + rightEye.x) / 2;
    const eyeCenterY = (leftEye.y + rightEye.y) / 2;
    const eyeWidth   = Math.max(Math.abs(rightEye.x - leftEye.x), 0.01);
    const faceHeight = Math.max(chin.y - forehead.y, 0.05);

    // Yaw: horizontal nose offset from eye center, normalized by eye spread.
    // Raw video (unmirrored): user turns THEIR left → nose.x increases → positive yaw.
    const yaw   = ((nose.x - eyeCenterX) / eyeWidth) * 45;

    // Pitch: vertical nose position below eye center, normalized by face height.
    // Look down → nose.y increases relative to eye center → positive pitch delta.
    // Look up   → nose.y decreases relative to eye center → negative pitch delta.
    const pitch = ((nose.y - eyeCenterY) / faceHeight) * 90;

    return { yaw, pitch };
  }

  /**
   * Check whether the landmark delta satisfies the challenge direction.
   *
   * 'side' = accept either left OR right yaw movement (absolute delta).
   *   This is the preferred challenge — mirrors cannot flip the required direction,
   *   and a static photo/screen cannot produce the movement.
   * left/right/up/down = kept for backward compat but 'side' is the default.
   */
  function checkChallenge(direction) {
    // v2.1.11 (Issue 10): consider the peak delta seen so far. Real users
    // turn briefly and return — the instantaneous sample at the next tick
    // might already be back near baseline.
    if (!initialPose) return false;
    let liveYawDelta = 0;
    let livePitchDelta = 0;
    if (currentLandmarks) {
      const current = estimatePose(currentLandmarks);
      liveYawDelta = current.yaw - initialPose.yaw;
      livePitchDelta = current.pitch - initialPose.pitch;
    }
    const absYaw = Math.max(Math.abs(liveYawDelta), _peakYawDelta);
    const absPitch = Math.max(Math.abs(livePitchDelta), _peakPitchDelta);

    if (direction === 'side')  return absYaw >= CHALLENGE_THRESHOLD_DEG;
    if (direction === 'left')  return liveYawDelta  >  CHALLENGE_THRESHOLD_DEG || _peakYawDelta >= CHALLENGE_THRESHOLD_DEG;
    if (direction === 'right') return liveYawDelta  < -CHALLENGE_THRESHOLD_DEG || _peakYawDelta >= CHALLENGE_THRESHOLD_DEG;
    if (direction === 'up')    return livePitchDelta < -CHALLENGE_THRESHOLD_DEG || _peakPitchDelta >= CHALLENGE_THRESHOLD_DEG;
    if (direction === 'down')  return livePitchDelta >  CHALLENGE_THRESHOLD_DEG || _peakPitchDelta >= CHALLENGE_THRESHOLD_DEG;
    return absYaw >= CHALLENGE_THRESHOLD_DEG;
  }

  async function init(videoEl, onResults) {
    if (typeof FaceMesh === 'undefined') {
      console.error('[FANS-C FaceMesh] MediaPipe asset failed to load. Check static/vendor/mediapipe/face_mesh files.');
      return false;
    }
    try {
      const baseUrl = (window.MEDIAPIPE_BASE_URL || '/static/vendor/mediapipe/face_mesh/');
      faceMesh = new FaceMesh({
        locateFile: (file) => {
          const url = baseUrl + file;
          console.log('[FANS-C FaceMesh] locateFile:', file, '→', url);
          return url;
        },
      });
      faceMesh.setOptions({
        maxNumFaces: 1,
        refineLandmarks: true,
        minDetectionConfidence: 0.4,
        minTrackingConfidence: 0.4,
      });
      faceMesh.onResults((results) => {
        if (results.multiFaceLandmarks && results.multiFaceLandmarks.length > 0) {
          currentLandmarks = results.multiFaceLandmarks[0];
          const pose = estimatePose(currentLandmarks);
          poseHistory.push(pose);
          if (poseHistory.length > POSE_HISTORY_SIZE) poseHistory.shift();
          stableFrameCount++;
          missedFrameCount = 0;
          // v2.1.11 — track peak movement so brief turns count.
          if (initialPose) {
            const yawD = Math.abs(pose.yaw - initialPose.yaw);
            const pitchD = Math.abs(pose.pitch - initialPose.pitch);
            if (yawD > _peakYawDelta || pitchD > _peakPitchDelta) {
              if (yawD > _peakYawDelta) _peakYawDelta = yawD;
              if (pitchD > _peakPitchDelta) _peakPitchDelta = pitchD;
              // Snapshot the landmarks behind this new peak so the server can
              // independently recompute the same pose from raw evidence.
              _peakLandmarks = _snapshotLandmarks(currentLandmarks);
            }
          }
          _lostBurst = 0;
          if (onResults) onResults(currentLandmarks);
        } else {
          currentLandmarks = null;
          missedFrameCount++;
          stableFrameCount = 0;
          _faceLostCount++;
          _lostBurst++;
          if (_lostBurst > _maxLostBurst) _maxLostBurst = _lostBurst;
        }
      });

      const camera = new Camera(videoEl, {
        onFrame: async () => {
          _frameCount++;
          if (!videoEl || videoEl.readyState < 2 || videoEl.videoWidth === 0 || videoEl.videoHeight === 0) {
            return;
          }
          if (faceMesh) await faceMesh.send({ image: videoEl });
          if (_frameCount % 30 === 0) {
            const _dbgPose = currentLandmarks ? estimatePose(currentLandmarks) : null;
            console.log('[FANS-C FaceMesh]',
              'frame=' + _frameCount,
              'landmarks=' + (currentLandmarks ? currentLandmarks.length : 0),
              'readyState=' + videoEl.readyState,
              'video=' + videoEl.videoWidth + 'x' + videoEl.videoHeight,
              'stable=' + stableFrameCount,
              'missed=' + missedFrameCount,
              'yaw=' + (_dbgPose ? _dbgPose.yaw.toFixed(2) : 'n/a'),
              'baseYaw=' + (_baselineYaw !== null ? _baselineYaw.toFixed(2) : 'n/a')
            );
          }
        },
        width: 480,
        height: 360,
      });
      await camera.start();
      _initialized = true;
      console.log('FANS-C: MediaPipe FaceMesh initialized. Threshold =', CHALLENGE_THRESHOLD_DEG, 'deg');
      return true;
    } catch (err) {
      console.error('[FANS-C FaceMesh] MediaPipe asset failed to load. Check static/vendor/mediapipe/face_mesh files.', err);
      return false;
    }
  }

  /** True once FaceMesh has produced at least one landmark result. */
  function hasLandmarks() {
    return currentLandmarks !== null;
  }

  /**
   * Record the current pose as the neutral baseline.
   * Averages recent pose history for stability — avoids outlier frames.
   * Falls back to single frame or {0,0} if called before first result.
   */
  function setBaseline() {
    // v2.1.11 — reset peak-delta trackers so the challenge only counts
    // movement that happens AFTER the baseline is captured.
    _peakYawDelta = 0;
    _peakPitchDelta = 0;
    _faceLostCount = 0;
    _lostBurst = 0;
    _maxLostBurst = 0;
    // Baseline landmark snapshot is always the current raw FaceMesh reading —
    // averaging is only applied to the derived yaw/pitch numbers below, not
    // to the point cloud sent to the server for independent recomputation.
    _baselineLandmarks = _snapshotLandmarks(currentLandmarks);
    _peakLandmarks = null;
    if (poseHistory.length > 0) {
      const avgYaw   = poseHistory.reduce((s, p) => s + p.yaw,   0) / poseHistory.length;
      const avgPitch = poseHistory.reduce((s, p) => s + p.pitch, 0) / poseHistory.length;
      initialPose = { yaw: avgYaw, pitch: avgPitch };
      _baselineYaw = avgYaw;
      console.log('FANS-C: Baseline set (avg', poseHistory.length, 'frames) yaw=', avgYaw.toFixed(2), 'pitch=', avgPitch.toFixed(2));
      return true;
    }
    if (currentLandmarks) {
      initialPose = estimatePose(currentLandmarks);
      _baselineYaw = initialPose.yaw;
      console.log('FANS-C: Baseline set (single frame) yaw=', initialPose.yaw.toFixed(2), 'pitch=', initialPose.pitch.toFixed(2));
      return true;
    }
    initialPose = { yaw: 0, pitch: 0 };
    _baselineYaw = 0;
    console.warn('FANS-C: Baseline fallback {0,0} — no landmarks yet');
    return false;
  }

  function setBaselineYaw(value) {
    _baselineYaw = value;
    if (initialPose) {
      initialPose.yaw = value;
    } else {
      initialPose = { yaw: value, pitch: 0 };
    }
  }

  function getBaselineYaw() {
    return _baselineYaw;
  }

  function reset() {
    initialPose      = null;
    _baselineYaw     = null;
    currentLandmarks = null;
    poseHistory      = [];
    stableFrameCount = 0;
    missedFrameCount = 0;
    _frameCount      = 0;
    _peakYawDelta    = 0;
    _peakPitchDelta  = 0;
    _faceLostCount   = 0;
    _lostBurst       = 0;
    _maxLostBurst    = 0;
    _baselineLandmarks = null;
    _peakLandmarks     = null;
  }

  function getBaselineLandmarks() { return _baselineLandmarks; }
  function getPeakLandmarks()     { return _peakLandmarks;     }

  function getCurrentPose() {
    return currentLandmarks ? estimatePose(currentLandmarks) : null;
  }

  /**
   * Return real-time debug info for the challenge progress overlay.
   * yawDelta/pitchDelta are relative to the baseline (positive = toward threshold for left/down).
   */
  function getDebugInfo(direction) {
    if (!currentLandmarks) {
      // Even when current landmarks are missing, expose the best progress
      // observed so far so the UI bar doesn't flash back to 0 every blink.
      const peakAbs = Math.max(_peakYawDelta, _peakPitchDelta);
      return {
        hasLandmarks: false,
        yawDelta: 0,
        pitchDelta: 0,
        peakYawDelta: _peakYawDelta,
        peakPitchDelta: _peakPitchDelta,
        threshold: CHALLENGE_THRESHOLD_DEG,
        progress: Math.min(100, Math.max(0, Math.round(peakAbs / CHALLENGE_THRESHOLD_DEG * 100))),
        faceLostCount: _faceLostCount,
        maxLostBurst: _maxLostBurst,
      };
    }
    const current    = estimatePose(currentLandmarks);
    const yawDelta   = initialPose ? (current.yaw   - initialPose.yaw)   : 0;
    const pitchDelta = initialPose ? (current.pitch - initialPose.pitch) : 0;

    let movementDelta = 0;
    if (direction === 'side')  movementDelta = Math.max(Math.abs(yawDelta), _peakYawDelta);
    if (direction === 'left')  movementDelta = Math.max(yawDelta, _peakYawDelta);
    if (direction === 'right') movementDelta = Math.max(-yawDelta, _peakYawDelta);
    if (direction === 'up')    movementDelta = Math.max(-pitchDelta, _peakPitchDelta);
    if (direction === 'down')  movementDelta = Math.max(pitchDelta, _peakPitchDelta);

    const progress = Math.min(100, Math.max(0, Math.round(movementDelta / CHALLENGE_THRESHOLD_DEG * 100)));

    return {
      hasLandmarks: true,
      yawDelta,
      pitchDelta,
      peakYawDelta: _peakYawDelta,
      peakPitchDelta: _peakPitchDelta,
      movementDelta,
      threshold: CHALLENGE_THRESHOLD_DEG,
      progress,
      faceLostCount: _faceLostCount,
      maxLostBurst: _maxLostBurst,
    };
  }

  function getStableFrameCount() { return stableFrameCount; }
  function getMissedFrameCount()  { return missedFrameCount;  }
  function getFaceLostCount()     { return _faceLostCount;    }
  function getMaxLostBurst()      { return _maxLostBurst;     }
  function getPeakYawDelta()      { return _peakYawDelta;     }
  function getPeakPitchDelta()    { return _peakPitchDelta;   }

  return {
    init, hasLandmarks, setBaseline, setBaselineYaw, getBaselineYaw,
    checkChallenge, reset, getCurrentPose, getDebugInfo,
    getStableFrameCount, getMissedFrameCount,
    getFaceLostCount, getMaxLostBurst, getPeakYawDelta, getPeakPitchDelta,
    getBaselineLandmarks, getPeakLandmarks,
  };
})();
