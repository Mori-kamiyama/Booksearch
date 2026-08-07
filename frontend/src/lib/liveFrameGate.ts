export type FrameSkipReason = 'warming_up' | 'blurred' | 'glare' | 'unchanged' | 'rate_limited' | 'no_tag'

export interface FrameGateState {
  previous?: Uint8Array
  lastSentAt: number
  // Timestamp of the most recent frame in which a mapped tag was tracked.
  // Undefined means no tag has been seen at all yet.
  lastTagSeenAt?: number
}

export interface FrameMetrics { blur: number; difference: number; glareRatio: number }

export const LIVE_SCAN_DEFAULTS = {
  evaluationFps: 15, maxSendFps: 1, minBlur: 80, minDifference: 12, maxGlareRatio: 0.18,
  // Tag detection runs on its own interval, so a frame captured between two
  // detections must not be discarded. This must stay above the slowest
  // detection loop (the 1600ms server fallback) plus its round trip; beyond it
  // the camera has genuinely left the tagged shelf.
  tagGraceMs: 2500,
  // When no tag is seen, still send frames at this lower rate so a session
  // never completes with zero accepted frames just because the camera never
  // saw an AprilTag (e.g. OpenCV.js failed to load, or tags are absent).
  noTagSendIntervalMs: 5000,
}

// Kept independent of React/canvas so the policy is unit-testable.
export function shouldSendFrame(metrics: FrameMetrics, now: number, state: FrameGateState, options = LIVE_SCAN_DEFAULTS): FrameSkipReason | null {
  const tagActive = state.lastTagSeenAt != null && now - state.lastTagSeenAt <= options.tagGraceMs
  if (metrics.blur < options.minBlur) return 'blurred'
  if (metrics.glareRatio > options.maxGlareRatio) return 'glare'
  if (tagActive) {
    // The first sharp frame is useful immediately. Skipping it as a warm-up
    // frame can leave a stationary, well-framed shelf with zero uploads.
    if (!state.previous) return null
    if (metrics.difference < options.minDifference) return 'unchanged'
    if (now - state.lastSentAt < 1000 / options.maxSendFps) return 'rate_limited'
    return null
  }
  // No tag tracked: allow a slow trickle of frames so the session can still
  // produce crops from OCR even when AprilTags are not detected.
  if (now - state.lastSentAt < options.noTagSendIntervalMs) return 'rate_limited'
  if (!state.previous) return null
  return null
}

export function frameMetrics(image: ImageData, previous?: Uint8Array): { metrics: FrameMetrics; sample: Uint8Array } {
  const { data, width, height } = image
  const sampleWidth = Math.ceil(width / 4)
  const sampleHeight = Math.ceil(height / 4)
  const sample = new Uint8Array(sampleWidth * sampleHeight)
  let n = 0; let diff = 0; let glarePixels = 0
  for (let y = 0; y < height; y += 4) for (let x = 0; x < width; x += 4) {
    const i = (y * width + x) * 4
    const gray = (data[i] * 77 + data[i + 1] * 150 + data[i + 2] * 29) >> 8
    const maxChannel = Math.max(data[i], data[i + 1], data[i + 2])
    const minChannel = Math.min(data[i], data[i + 1], data[i + 2])
    if (gray >= 242 && maxChannel - minChannel <= 18) glarePixels++
    sample[n] = gray
    if (previous) diff += Math.abs(gray - previous[n])
    n++
  }
  let laplacianSum = 0; let laplacianSquared = 0; let laplacianCount = 0
  for (let y = 1; y < sampleHeight - 1; y++) for (let x = 1; x < sampleWidth - 1; x++) {
    const i = y * sampleWidth + x
    const value = sample[i] * 4 - sample[i - 1] - sample[i + 1] - sample[i - sampleWidth] - sample[i + sampleWidth]
    laplacianSum += value; laplacianSquared += value * value; laplacianCount++
  }
  const mean = laplacianSum / Math.max(1, laplacianCount)
  const blur = laplacianSquared / Math.max(1, laplacianCount) - mean * mean
  return { metrics: { blur, difference: previous ? diff / n : 0, glareRatio: glarePixels / Math.max(1, n) }, sample }
}
