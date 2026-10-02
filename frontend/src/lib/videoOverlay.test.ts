import { describe, expect, it } from 'vitest'
import { containedVideoPoint, shelfBoxGuide, shelfDirectionGuide } from './videoOverlay'

describe('containedVideoPoint', () => {
  it('includes letterboxing when the camera and screen have different aspect ratios', () => {
    expect(containedVideoPoint([640, 360], [1280, 720], [400, 800])).toEqual({ x: 200, y: 400 })
    expect(containedVideoPoint([0, 0], [1280, 720], [400, 800])).toEqual({ x: 0, y: 287.5 })
  })

  it('ignores dimensions before the camera is ready', () => {
    expect(containedVideoPoint([20, 20], [0, 0], [400, 800])).toBeNull()
  })
})

describe('shelfDirectionGuide', () => {
  const corners: [[number, number], [number, number], [number, number], [number, number]] = [
    [620, 340], [660, 340], [660, 380], [620, 380],
  ]

  it('points from a detected tag into its mapped shelf quadrant', () => {
    const guide = shelfDirectionGuide([640, 360], corners, 'top_left', [1280, 720], [400, 800])
    expect(guide).not.toBeNull()
    expect(guide!.origin).toEqual({ x: 200, y: 400 })
    expect(guide!.tip.x).toBeLessThan(guide!.origin.x)
    expect(guide!.tip.y).toBeLessThan(guide!.origin.y)
    expect(guide!.tip.y).toBeGreaterThan(287.5)
    expect(guide!.plateSize).toBeGreaterThanOrEqual(68)
  })

  it('does not draw a direction for a tiny or unmapped tag', () => {
    const tiny: typeof corners = [[639, 359], [641, 359], [641, 361], [639, 361]]
    expect(shelfDirectionGuide([640, 360], tiny, 'top_left', [1280, 720], [400, 800])).toBeNull()
    expect(shelfDirectionGuide([640, 360], corners, 'unknown', [1280, 720], [400, 800])).toBeNull()
  })
})

describe('shelfBoxGuide', () => {
  const tag8 = {
    center: [206, 335] as [number, number],
    corners: [[190, 319], [222, 318], [223, 351], [190, 352]] as [[number, number], [number, number], [number, number], [number, number]],
    quadrant: 'bottom_right',
  }
  const tag4 = {
    center: [920, 991] as [number, number],
    corners: [[904, 975], [937, 975], [937, 1007], [904, 1007]] as [[number, number], [number, number], [number, number], [number, number]],
    quadrant: 'top_left',
  }

  it('uses the opposite tags in the supplied shelf photo to cover the compartment', () => {
    const box = shelfBoxGuide([tag8, tag4], [1128, 2000], [402, 874])
    expect(box).not.toBeNull()
    expect(box!.left).toBeGreaterThan(75)
    expect(box!.left + box!.width).toBeLessThan(330)
    expect(box!.top).toBeGreaterThan(200)
    expect(box!.top + box!.height).toBeLessThan(440)
  })

  it('keeps a single tag in directional mode', () => {
    expect(shelfBoxGuide([tag8], [1128, 2000], [402, 874])).toBeNull()
  })
})
