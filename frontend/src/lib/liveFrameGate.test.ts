import { describe, expect, it } from 'vitest'
import { frameMetrics, shouldSendFrame } from './liveFrameGate'

describe('live frame gate', () => {
  const state = { previous: new Uint8Array([1]), lastSentAt: 0 }
  it('skips blurred, static and too-frequent frames', () => {
    expect(shouldSendFrame({ blur: 1, difference: 99, glareRatio: 0 }, 1000, state)).toBe('blurred')
    expect(shouldSendFrame({ blur: 99, difference: 99, glareRatio: 0.5 }, 1000, state)).toBe('glare')
    expect(shouldSendFrame({ blur: 99, difference: 1, glareRatio: 0 }, 1000, state)).toBe('unchanged')
    expect(shouldSendFrame({ blur: 99, difference: 99, glareRatio: 0 }, 100, state)).toBe('rate_limited')
  })
  it('accepts a sharp changed frame after the interval', () => {
    expect(shouldSendFrame({ blur: 99, difference: 99, glareRatio: 0 }, 1000, state)).toBeNull()
  })
  it('accepts the first sharp frame without requiring camera movement', () => {
    expect(shouldSendFrame({ blur: 99, difference: 0, glareRatio: 0 }, 1000, { lastSentAt: 0 })).toBeNull()
  })
  it('reports more Laplacian variance for a checkerboard than a flat frame', () => {
    const makeImage = (checker: boolean) => {
      const data = new Uint8ClampedArray(32 * 32 * 4)
      for (let y = 0; y < 32; y++) for (let x = 0; x < 32; x++) {
        const value = checker && ((x >> 2) + (y >> 2)) % 2 ? 255 : checker ? 0 : 128
        const i = (y * 32 + x) * 4; data[i] = data[i + 1] = data[i + 2] = value; data[i + 3] = 255
      }
      return { data, width: 32, height: 32, colorSpace: 'srgb' } as ImageData
    }
    expect(frameMetrics(makeImage(true)).metrics.blur).toBeGreaterThan(frameMetrics(makeImage(false)).metrics.blur)
  })
  it('detects a largely white low-saturation glare frame', () => {
    const data = new Uint8ClampedArray(16 * 16 * 4).fill(250)
    for (let i = 3; i < data.length; i += 4) data[i] = 255
    const image = { data, width: 16, height: 16, colorSpace: 'srgb' } as ImageData
    expect(frameMetrics(image).metrics.glareRatio).toBeGreaterThan(0.9)
  })
})
