/**
 * Shared webcam utility for FANS-C.
 * Provides: startCamera(), captureFrame(), captureFrameHighQuality(), stopCamera(), postJSON().
 *
 * Camera: requested at 640x480 for better face quality with typical laptop webcams.
 * captureFrameHighQuality(): takes a burst of frames, picks the sharpest using
 *   a Sobel-based edge variance estimator (more accurate than pixel variance alone).
 */

let _stream = null;

async function startCamera(videoEl) {
  try {
    // Prefer 640x480; accept anything >= 320x240
    const constraints = {
      video: {
        width: { ideal: 640, min: 320 },
        height: { ideal: 480, min: 240 },
        facingMode: 'user',
        frameRate: { ideal: 30, min: 15 },
      },
      audio: false,
    };
    _stream = await navigator.mediaDevices.getUserMedia(constraints);
    videoEl.srcObject = _stream;
    // Wait for video metadata to load
    await new Promise((resolve) => {
      if (videoEl.readyState >= 2) { resolve(); return; }
      videoEl.onloadedmetadata = () => resolve();
      setTimeout(resolve, 2500); // fallback timeout
    });
    // Wait one additional frame for stable output
    await new Promise(r => setTimeout(r, 150));
    return { success: true };
  } catch (err) {
    return { success: false, error: err.message };
  }
}

/**
 * Capture a single frame from the video element.
 * Draws without mirroring — CSS mirror (scaleX(-1)) is cosmetic only.
 */
function captureFrame(videoEl, canvasEl, width, height) {
  const w = width  || videoEl.videoWidth  || 480;
  const h = height || videoEl.videoHeight || 360;
  canvasEl.width  = w;
  canvasEl.height = h;
  const ctx = canvasEl.getContext('2d');
  ctx.drawImage(videoEl, 0, 0, w, h);
  return canvasEl.toDataURL('image/jpeg', 0.92);
}

/**
 * Capture the best-quality frame from a short burst.
 * Uses a Sobel-based edge density estimate for sharpness — more reliable than
 * pixel variance alone (which measures contrast, not focus).
 *
 * @param {HTMLVideoElement} videoEl
 * @param {HTMLCanvasElement} canvasEl
 * @param {number} frames   Number of frames to sample (default 7)
 * @param {number} delayMs  Delay between frames in ms (default 70)
 * @returns {Promise<string>} Data URL of the sharpest captured frame
 */
async function captureFrameHighQuality(videoEl, canvasEl, frames = 7, delayMs = 70) {
  const w = videoEl.videoWidth  || 480;
  const h = videoEl.videoHeight || 360;

  const tmpCanvas = document.createElement('canvas');
  const tmpCtx = tmpCanvas.getContext('2d', { willReadFrequently: true });
  tmpCanvas.width  = w;
  tmpCanvas.height = h;

  let bestFrame     = null;
  let bestSharpness = -1;

  for (let i = 0; i < frames; i++) {
    tmpCtx.drawImage(videoEl, 0, 0, w, h);
    const dataUrl   = tmpCanvas.toDataURL('image/jpeg', 0.92);
    const sharpness = estimateSharpnessSobel(tmpCtx, w, h);
    if (sharpness > bestSharpness) {
      bestSharpness = sharpness;
      bestFrame     = dataUrl;
    }
    if (i < frames - 1) {
      await new Promise(r => setTimeout(r, delayMs));
    }
  }

  // Write best frame into shared canvas for consistent dimensions
  canvasEl.width  = w;
  canvasEl.height = h;
  return bestFrame || captureFrame(videoEl, canvasEl, w, h);
}

/**
 * Estimate sharpness using approximate Sobel edge detection.
 * Samples a central region for speed; computes variance of edge magnitudes.
 * Higher value = sharper (more in-focus) image.
 */
function estimateSharpnessSobel(ctx, w, h) {
  // Sample central 50% of the frame
  const sampleW = Math.floor(w * 0.5);
  const sampleH = Math.floor(h * 0.5);
  const sx = Math.floor((w - sampleW) / 2);
  const sy = Math.floor((h - sampleH) / 2);

  try {
    const imageData = ctx.getImageData(sx, sy, sampleW, sampleH);
    const d  = imageData.data;
    const sw = sampleW;

    // Convert to grayscale luma array
    const gray = new Float32Array(sampleW * sampleH);
    for (let i = 0; i < gray.length; i++) {
      const p = i * 4;
      gray[i] = 0.299 * d[p] + 0.587 * d[p + 1] + 0.114 * d[p + 2];
    }

    // Approximate Sobel: sum of squared horizontal differences
    let sumSq = 0;
    let n = 0;
    for (let y = 0; y < sampleH - 1; y++) {
      for (let x = 0; x < sampleW - 1; x++) {
        const gx = gray[y * sw + x + 1] - gray[y * sw + x];
        const gy = gray[(y + 1) * sw + x] - gray[y * sw + x];
        sumSq += gx * gx + gy * gy;
        n++;
      }
    }
    return n > 0 ? sumSq / n : 0;
  } catch (e) {
    return 0;
  }
}

function stopCamera() {
  if (_stream) {
    _stream.getTracks().forEach(t => t.stop());
    _stream = null;
  }
}

async function postJSON(url, data, csrfToken) {
  const res = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken': csrfToken,
    },
    credentials: 'same-origin',
    body: JSON.stringify(data),
  });
  // For structured JSON error responses (4xx), parse the body so callers can
  // display a meaningful message rather than a generic "Network error".
  if (!res.ok) {
    let body = null;
    try { body = await res.json(); } catch (_) { /* ignore parse failure */ }
    if (body && typeof body === 'object') {
      // Return the parsed error body so the caller can branch on http_fallback / no_event
      return { ...body, success: false, _httpStatus: res.status };
    }
    throw new Error(`HTTP ${res.status}: ${res.statusText}`);
  }
  return res.json();
}
