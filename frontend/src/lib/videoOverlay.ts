/** Maps detector pixels into a video element displayed with object-fit: contain. */
export function containedVideoPoint(
  point: [number, number],
  source: [number, number],
  viewport: [number, number],
): { x: number; y: number } | null {
  const [sourceWidth, sourceHeight] = source
  const [viewWidth, viewHeight] = viewport
  if (sourceWidth <= 0 || sourceHeight <= 0 || viewWidth <= 0 || viewHeight <= 0) return null
  const scale = Math.min(viewWidth / sourceWidth, viewHeight / sourceHeight)
  return {
    x: (viewWidth - sourceWidth * scale) / 2 + point[0] * scale,
    y: (viewHeight - sourceHeight * scale) / 2 + point[1] * scale,
  }
}

export interface ShelfDirectionGuide {
  origin: { x: number; y: number }
  tip: { x: number; y: number }
  plateSize: number
  viewport: { width: number; height: number }
}

export interface ShelfBoxGuide {
  left: number
  top: number
  width: number
  height: number
}

export interface ShelfCornerTag {
  center: [number, number]
  corners: [[number, number], [number, number], [number, number], [number, number]]
  quadrant: string
}

/** Two tags at opposite shelf corners give a camera-aligned compartment box. */
export function shelfBoxGuide(
  tags: ShelfCornerTag[], source: [number, number], viewport: [number, number],
): ShelfBoxGuide | null {
  const oppositePairs: Array<[string, string]> = [
    ['bottom_right', 'top_left'],
    ['bottom_left', 'top_right'],
  ]
  let best: ShelfBoxGuide | null = null
  for (const [firstQuadrant, secondQuadrant] of oppositePairs) {
    const firstTags = tags.filter(tag => tag.quadrant === firstQuadrant)
    const secondTags = tags.filter(tag => tag.quadrant === secondQuadrant)
    for (const first of firstTags) for (const second of secondTags) {
      const a = containedVideoPoint(first.center, source, viewport)
      const b = containedVideoPoint(second.center, source, viewport)
      const aCorner = containedVideoPoint(first.corners[0], source, viewport)
      const aNext = containedVideoPoint(first.corners[1], source, viewport)
      const bCorner = containedVideoPoint(second.corners[0], source, viewport)
      const bNext = containedVideoPoint(second.corners[1], source, viewport)
      if (!a || !b || !aCorner || !aNext || !bCorner || !bNext) continue
      if (a.y >= b.y || (firstQuadrant === 'bottom_right' ? a.x >= b.x : a.x <= b.x)) continue
      const tagSize = (Math.hypot(aCorner.x - aNext.x, aCorner.y - aNext.y)
        + Math.hypot(bCorner.x - bNext.x, bCorner.y - bNext.y)) / 2
      const rawWidth = Math.abs(a.x - b.x)
      const rawHeight = Math.abs(a.y - b.y)
      if (rawWidth < tagSize * 4 || rawHeight < tagSize * 4) continue
      // Tags sit on wooden dividers; move the highlight just inside them.
      const inset = tagSize * 0.75
      const candidate = {
        left: Math.min(a.x, b.x) + inset,
        top: Math.min(a.y, b.y) + inset,
        width: rawWidth - inset * 2,
        height: rawHeight - inset * 2,
      }
      if (!best || candidate.width * candidate.height > best.width * best.height) best = candidate
    }
  }
  return best
}

/** A directional cue only: shelf dimensions are not encoded in the tag map. */
export function shelfDirectionGuide(
  center: [number, number],
  corners: [[number, number], [number, number], [number, number], [number, number]],
  quadrant: string,
  source: [number, number],
  viewport: [number, number],
): ShelfDirectionGuide | null {
  const origin = containedVideoPoint(center, source, viewport)
  const first = containedVideoPoint(corners[0], source, viewport)
  const second = containedVideoPoint(corners[1], source, viewport)
  if (!origin || !first || !second || !['top_left', 'top_right', 'bottom_left', 'bottom_right'].includes(quadrant)) return null
  const tagWidth = Math.hypot(second.x - first.x, second.y - first.y)
  if (tagWidth < 12) return null
  const plateSize = Math.min(220, Math.max(68, tagWidth * 1.6))
  const [viewWidth, viewHeight] = viewport
  const scale = Math.min(viewWidth / source[0], viewHeight / source[1])
  const imageWidth = source[0] * scale
  const imageHeight = source[1] * scale
  const left = (viewWidth - imageWidth) / 2
  const top = (viewHeight - imageHeight) / 2
  const signX = quadrant.endsWith('left') ? -1 : 1
  const signY = quadrant.startsWith('top') ? -1 : 1
  const distance = Math.min(300, Math.max(72, tagWidth * 3)) / Math.SQRT2
  const tip = {
    x: Math.max(left + 24, Math.min(left + imageWidth - 24, origin.x + signX * distance)),
    y: Math.max(top + 24, Math.min(top + imageHeight - 24, origin.y + signY * distance)),
  }
  if (Math.hypot(tip.x - origin.x, tip.y - origin.y) < 25) return null
  return { origin, tip, plateSize, viewport: { width: viewWidth, height: viewHeight } }
}
