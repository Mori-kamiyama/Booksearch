export type FrameSkipReason = 'warming_up' | 'blurred' | 'unchanged' | 'rate_limited'

export interface FrameGateState {
  previous?: Uint8Array
  lastSentAt: number
}

export interface FrameMetrics { blur: number; difference: number }

export const LIVE_SCAN_DEFAULTS = { evaluationFps: 15, maxSendFps: 3, minBlur: 80, minDifference: 12 }

// Kept independent of React/canvas so the policy is unit-testable.
export function shouldSendFrame(metrics: FrameMetrics, now: number, state: FrameGateState, options = LIVE_SCAN_DEFAULTS): FrameSkipReason | null {
  if (!state.previous) return 'warming_up'
  if (metrics.blur < options.minBlur) return 'blurred'
  if (metrics.difference < options.minDifference) return 'unchanged'
  if (now - state.lastSentAt < 1000 / options.maxSendFps) return 'rate_limited'
  return null
}

export function frameMetrics(image: ImageData, previous?: Uint8Array): { metrics: FrameMetrics; sample: Uint8Array } {
  const { data, width, height } = image
  const sampleWidth = Math.ceil(width / 4)
  const sampleHeight = Math.ceil(height / 4)
  const sample = new Uint8Array(sampleWidth * sampleHeight)
  let n = 0; let diff = 0
  for (let y = 0; y < height; y += 4) for (let x = 0; x < width; x += 4) {
    const i = (y * width + x) * 4
    const gray = (data[i] * 77 + data[i + 1] * 150 + data[i + 2] * 29) >> 8
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
  return { metrics: { blur, difference: previous ? diff / n : 0 }, sample }
}
